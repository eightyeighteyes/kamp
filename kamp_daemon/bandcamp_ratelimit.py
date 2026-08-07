"""Rate-limit safety net for kamp's own bandcamp.com requests (KAMP-646).

Discovery (KAMP-643) is a new consumer of an origin that several workers already
share. This module keeps *discovery's* requests underneath the ceiling KAMP-644
measured, so that a crate build — or a bug in one — cannot earn an account-wide
429 that would cascade into the download queue.

**Scope, deliberately narrow.** This governs only the requests whose callers ask it
to. It does not, and cannot, gate:

* the stream sync and album download, which run in ``spawn`` subprocesses
  (:func:`kamp_daemon.syncer._spawn_worker`) and so cannot see this object at all;
* playback stream-URL resolution, which is the *same* endpoint class but runs
  inside an HTTP handler (``kamp_core/server.py``) and on the gapless-lookahead
  path. A backoff there would stall the next track or hang a request. **Do not
  wire this into playback.** Same reasoning as ``resolve_preview`` being
  budget-exempt in :mod:`kamp_daemon.discovery`.

**Why per endpoint class.** KAMP-644 measured materially different limits: album
pages returned 429 after 57 requests in 39s (~87/min), while ``discover_web``
absorbed 120 at the same rate without complaint. One global number would be
simultaneously too strict for one and too loose for the other.

Bandcamp offers no ``Retry-After`` even on the direct transport, and the Electron
relay cannot surface response headers at all, so the cooldown ladder here is the
only backoff signal available.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from typing import Protocol

from .discovery import ALBUM_PAGE, ARTIST_PAGE, DISCOVER_API, FANCOLLECTION

logger = logging.getLogger(__name__)

# Minimum gap between two of *our* requests in the same class. Chosen against the
# measured ceilings with roughly 2x headroom: 1.5s pins album pages at <=40/min
# against a limit that appeared at ~87/min. Spacing is the only mechanism that
# bounds a *pathological loop* — a budget bounds a single crate, spacing bounds
# everything, including a bug.
_MIN_SPACING = {
    ALBUM_PAGE: 1.5,
    # An artist /music page is the same kind of HTML page fetch as an album page,
    # so it gets the same floor rather than the silent _DEFAULT_SPACING.
    ARTIST_PAGE: 1.5,
    DISCOVER_API: 1.0,
    # The collection endpoint is the one that rate-limits hardest (three walks in
    # a minute earns a 429), and crate building never touches it. Only the
    # wishlist walk does.
    FANCOLLECTION: 5.0,
}
_DEFAULT_SPACING = 2.0

# Escalating cooldown after an observed 429, per class. Mirrors the ladder the
# download drain already uses, so a user sees consistent pause lengths.
_COOLDOWNS = (60.0, 120.0, 300.0)


class Clock(Protocol):
    """Time source and interruptible wait, injected so tests never really sleep.

    One object rather than two callables on purpose: a fake ``sleep`` paired with
    a separately-faked ``monotonic`` is the standard way to write a wait loop that
    never advances its own clock, which then spins forever and hangs the suite.
    Binding the two together makes that mistake unrepresentable.
    """

    def now(self) -> float:
        """Monotonic seconds."""

    def wait(self, timeout: float) -> bool:
        """Wait up to *timeout*. Return True if interrupted (i.e. shutting down)."""


class RealClock:
    """Production clock. ``wait`` is bound to a shutdown event, not ``time.sleep``.

    That is what makes a long cooldown safe: a stopping daemon interrupts the wait
    immediately rather than sitting out the remaining five minutes.
    """

    def __init__(self, stop: threading.Event | None = None) -> None:
        self._stop = stop or threading.Event()

    def now(self) -> float:
        return time.monotonic()

    def wait(self, timeout: float) -> bool:
        return self._stop.wait(timeout)


class BandcampGovernor:
    """Spacing and cooldown for kamp-issued bandcamp.com requests.

    Thread-safe. Callers declare the endpoint class themselves — the governor
    never inspects a URL to guess it. Sniffing the host would misclassify the
    Bandcamp Pro custom domains that ~1% of a real collection uses, and would
    invite a relay-level 422 (a URL that never reached Bandcamp at all) to be
    treated as an origin rate limit.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        # A spawn subprocess importing this module would get a brand-new instance
        # with no history — a governor that silently governs nothing. Fail loudly
        # instead; the children have their own hand-rolled backoff and are not
        # meant to use this.
        if multiprocessing.parent_process() is not None:
            raise RuntimeError(
                "BandcampGovernor is parent-process only: a subprocess instance "
                "would carry no backoff state and silently govern nothing."
            )
        self._clock = clock or RealClock()
        self._lock = threading.Lock()
        self._last_request_at: dict[str, float] = {}
        self._blocked_until: dict[str, float] = {}
        self._strikes: dict[str, int] = {}

    def wait_turn(self, endpoint_class: str) -> bool:
        """Block until it is polite to issue a request in *endpoint_class*.

        Returns False if the wait was interrupted by shutdown, in which case the
        caller must NOT issue the request.

        The lock is never held across a wait: the deadline is computed under it,
        released, waited on, then re-checked. Holding it would let a slow consumer
        block :meth:`report_429` from an unrelated thread, which is how a safety
        net turns into the outage it was meant to prevent.
        """
        while True:
            with self._lock:
                now = self._clock.now()
                spacing = _MIN_SPACING.get(endpoint_class, _DEFAULT_SPACING)
                ready_at = max(
                    self._blocked_until.get(endpoint_class, 0.0),
                    self._last_request_at.get(endpoint_class, 0.0) + spacing,
                )
                if now >= ready_at:
                    # Reserve the slot before releasing, so two threads in the
                    # same class cannot both decide it is their turn.
                    self._last_request_at[endpoint_class] = now
                    return True
                delay = ready_at - now

            if self._clock.wait(delay):
                logger.debug("wait_turn(%s) interrupted by shutdown", endpoint_class)
                return False

    def report_429(self, endpoint_class: str) -> float:
        """Record an observed rate limit and return the cooldown applied.

        Per class rather than account-wide: the measured limits differ by roughly
        2x, so pausing album pages because the collection endpoint complained
        would cost real time for no protection. What this does buy is the class
        the download queue and the wishlist walk genuinely share.
        """
        with self._lock:
            strikes = self._strikes.get(endpoint_class, 0)
            cooldown = _COOLDOWNS[min(strikes, len(_COOLDOWNS) - 1)]
            self._strikes[endpoint_class] = strikes + 1
            self._blocked_until[endpoint_class] = self._clock.now() + cooldown
        logger.warning(
            "Bandcamp rate-limited on %s — holding that class for %.0fs",
            endpoint_class,
            cooldown,
        )
        return cooldown

    def report_ok(self, endpoint_class: str) -> None:
        """Record a clean result, resetting that class's escalation."""
        with self._lock:
            self._strikes.pop(endpoint_class, None)

    def blocked_for(self, endpoint_class: str) -> float:
        """Seconds remaining on *endpoint_class*'s cooldown, 0.0 when free."""
        with self._lock:
            return max(
                0.0, self._blocked_until.get(endpoint_class, 0.0) - self._clock.now()
            )

    def reset(self) -> None:
        """Forget all backoff state. **Test isolation only.**

        KAMP-646 wrote this expecting logout to call it, reasoning that a 429 is
        scoped to the account. KAMP-648 deliberately left it unwired: the limit
        is scoped to the IP at least as much as the account, so clearing a live
        cooldown because someone signed out and back in asks Bandcamp for
        another 429 and earns a longer one. Waiting costs 300s at worst.

        Also not called on config reload, which fires on unrelated preference
        changes — otherwise a user could clear a live backoff by toggling a
        checkbox.
        """
        with self._lock:
            self._last_request_at.clear()
            self._blocked_until.clear()
            self._strikes.clear()


_governor: BandcampGovernor | None = None
_governor_lock = threading.Lock()


def get_governor() -> BandcampGovernor:
    """Return the process-wide governor, creating it on first use."""
    global _governor
    with _governor_lock:
        if _governor is None:
            _governor = BandcampGovernor()
        return _governor


def reset_governor() -> None:
    """Drop the singleton. Test isolation only — see :meth:`BandcampGovernor.reset`."""
    global _governor
    with _governor_lock:
        _governor = None
