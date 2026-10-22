"""Which of the two players the ambiguous controls address (KAMP-736).

kamp runs two mpv engines: the user's queue and the Crate preview. Three inputs
name neither of them — the spacebar, the arrow keys and the media keys — so
something has to decide what they mean. This records which engine was playing
most recently, and the transport routes consult it.

The rule the ticket settled on:

* **Explicit controls address their own transport.** The main transport's play
  button recovers the main queue whatever was last playing.
* **Ambiguous controls follow the most recent player.**
* **The preview's claim expires.** After that the main queue wins again.

Why expiry exists: nothing reaps a paused preview. ``_arm_idle_timer`` is
called from ``stop()`` and ``release_for_main()`` but never from ``pause()``
(see :mod:`kamp_daemon.discovery_preview`), so a preview paused at 09:00 is
still sitting there at 17:00. Without a bound it would still own the media key,
and resuming it re-resolves a by-then-expired URL and replays the album from
the top against Bandcamp — an ambush, from a record the user had forgotten.

Deliberately a tiny module of its own rather than a pair of variables in
``create_app``: the two writers live in different closures (``server.py``'s
``_notify_play_state_changed`` and ``discovery_api.py``'s ``_push_preview``)
and the expiry rule has to read the same way from both. Keeping it here means
it is unit-testable without FastAPI, an engine or a socket.

This deliberately does NOT try to infer the answer from state either side
already holds:

* The preview's own state cannot carry it. Deriving "most recent" from a
  lingering ``PAUSED`` requires that state to survive main playback, and
  ``CrateView``'s ``ownsTransport`` requires the opposite — it reads
  ``preview.state !== 'idle'`` as "the deck owns the arrow keys" precisely
  because every POST under ``/api/v1/player/`` parks the preview (KAMP-678).
  One of the two has to be wrong, and it is not the shipped one.
* ``PlaybackState.position_updated_at`` cannot carry it either. It is written
  on seek and load, defaults to ``time.time()`` at construction, and is zeroed
  by ``release_for_main``. It is a position-event stamp, not a floor-ownership
  stamp. Separately, ``PreviewPlayer`` takes an injectable clock while the main
  engine calls ``time.time()`` directly, so comparing the two is a test hazard.
"""

from __future__ import annotations

import time
from typing import Callable

#: The user's own queue.
MAIN = "main"
#: The Crate preview.
PREVIEW = "preview"

_CLAIMANTS = frozenset({MAIN, PREVIEW})

#: How long a preview's claim outlives the audio. Matches the preview's own
#: IDLE_TIMEOUT_SECS, duplicated rather than imported because kamp_core does not
#: depend on kamp_daemon — the same boundary discovery_api's CRATE_SIZE observes.
DEFAULT_TTL_SECS = 300.0


class FloorClaim:
    """Remembers which player was audible most recently.

    Not locked. Claims are single assignments of a str and a float from the
    engine callback threads, reads happen on request threads, and a read that
    races a write returns either the old or the new holder — both of which were
    true moments either side of the call. A lock here would buy nothing a
    caller could observe.
    """

    def __init__(
        self,
        ttl: float = DEFAULT_TTL_SECS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl
        self._now = now
        self._who: str = MAIN
        self._at: float = 0.0

    def claim(self, who: str) -> None:
        """Record that *who* is playing, or has just stopped playing.

        Called on every playing-or-paused publish rather than on the transition
        into playing, so the stamp tracks the last moment the player was
        audible. Claiming only on the transition would let a ten-minute record
        expire its own claim while it was still playing.
        """
        if who not in _CLAIMANTS:
            # Refused rather than stored: an unreadable claimant would sit in
            # the state looking valid while holder() quietly handed every key to
            # the main queue for the life of the process.
            raise ValueError(f"unknown claimant: {who!r}")
        self._who = who
        self._at = self._now()

    def holder(self) -> str:
        """Who the ambiguous controls should address right now.

        Only the preview's claim is bounded. Expiry exists to stop a forgotten
        record ambushing the user; falling back *to* the queue needs no such
        protection, and bounding main's claim would change nothing anyway since
        main is what expiry falls back to.
        """
        if self._who == PREVIEW and self._now() - self._at < self._ttl:
            return PREVIEW
        return MAIN
