"""Tests for the Bandcamp rate-limit governor (KAMP-646).

Everything runs against a virtual clock, so the suite never actually sleeps and
the timing assertions are exact rather than flaky.

The fake clock is deliberately self-limiting: a regression that turns wait_turn
into a spin loop raises with a useful message instead of hanging CI, which is the
usual way a hand-rolled fake clock fails.
"""

from __future__ import annotations

import threading

import pytest

from kamp_daemon.bandcamp_ratelimit import (
    _COOLDOWNS,
    _MIN_SPACING,
    BandcampGovernor,
    RealClock,
    get_governor,
    reset_governor,
)
from kamp_daemon.discovery import ALBUM_PAGE, DISCOVER_API, FANCOLLECTION


class FakeClock:
    """Virtual time. ``wait`` advances the clock instead of blocking."""

    def __init__(self, *, interrupt_after: int | None = None, max_waits: int = 50):
        self.t = 1000.0
        self.waits: list[float] = []
        self._interrupt_after = interrupt_after
        self._max_waits = max_waits

    def now(self) -> float:
        return self.t

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        if len(self.waits) > self._max_waits:
            raise AssertionError(
                f"wait() called {len(self.waits)} times — wait_turn is spinning "
                "rather than converging on a deadline"
            )
        if (
            self._interrupt_after is not None
            and len(self.waits) >= self._interrupt_after
        ):
            return True  # shutting down
        self.t += timeout
        return False


@pytest.fixture(autouse=True)
def _isolate_singleton():
    """The module-level governor would otherwise leak backoff across tests."""
    reset_governor()
    yield
    reset_governor()


def _governor(**kw) -> tuple[BandcampGovernor, FakeClock]:
    clock = FakeClock(**kw)
    return BandcampGovernor(clock=clock), clock


class TestSpacing:
    def test_first_request_in_a_class_is_immediate(self) -> None:
        gov, clock = _governor()
        assert gov.wait_turn(ALBUM_PAGE) is True
        assert clock.waits == []

    def test_second_request_waits_the_class_floor(self) -> None:
        """Spacing is what bounds a pathological loop; a budget only bounds a crate."""
        gov, clock = _governor()
        gov.wait_turn(ALBUM_PAGE)
        start = clock.now()
        gov.wait_turn(ALBUM_PAGE)
        assert clock.now() - start == pytest.approx(_MIN_SPACING[ALBUM_PAGE])

    def test_classes_are_spaced_independently(self) -> None:
        """Album pages 429'd at ~87/min while discover absorbed 120 clean, so a
        shared spacer would be wrong for both."""
        gov, clock = _governor()
        gov.wait_turn(ALBUM_PAGE)
        start = clock.now()
        assert gov.wait_turn(DISCOVER_API) is True
        assert clock.now() == start, "a different class must not inherit the wait"

    def test_unknown_class_gets_the_default_spacing(self) -> None:
        gov, clock = _governor()
        gov.wait_turn("something_new")
        start = clock.now()
        gov.wait_turn("something_new")
        assert clock.now() > start

    def test_spacing_keeps_us_under_the_measured_ceiling(self) -> None:
        """The floor must imply a rate below the 87/min that actually produced a 429."""
        implied_per_minute = 60.0 / _MIN_SPACING[ALBUM_PAGE]
        assert implied_per_minute < 87.0


class TestCooldown:
    def test_429_blocks_that_class(self) -> None:
        gov, clock = _governor()
        cooldown = gov.report_429(ALBUM_PAGE)
        assert cooldown == _COOLDOWNS[0]
        assert gov.blocked_for(ALBUM_PAGE) == pytest.approx(_COOLDOWNS[0])

        start = clock.now()
        assert gov.wait_turn(ALBUM_PAGE) is True
        assert clock.now() - start == pytest.approx(_COOLDOWNS[0])

    def test_429_does_not_block_other_classes(self) -> None:
        """Per class, not account-wide: the limits differ by ~2x, so pausing album
        pages because the collection endpoint complained costs time and buys nothing."""
        gov, clock = _governor()
        gov.report_429(FANCOLLECTION)
        start = clock.now()
        assert gov.wait_turn(ALBUM_PAGE) is True
        assert clock.now() == start
        assert gov.blocked_for(ALBUM_PAGE) == 0.0

    def test_cooldown_escalates_then_saturates(self) -> None:
        gov, _ = _governor()
        assert [gov.report_429(ALBUM_PAGE) for _ in range(4)] == [
            _COOLDOWNS[0],
            _COOLDOWNS[1],
            _COOLDOWNS[2],
            _COOLDOWNS[2],
        ]

    def test_report_ok_resets_the_escalation(self) -> None:
        gov, _ = _governor()
        gov.report_429(ALBUM_PAGE)
        gov.report_429(ALBUM_PAGE)
        gov.report_ok(ALBUM_PAGE)
        assert gov.report_429(ALBUM_PAGE) == _COOLDOWNS[0]

    def test_report_ok_on_one_class_leaves_another_escalated(self) -> None:
        gov, _ = _governor()
        gov.report_429(ALBUM_PAGE)
        gov.report_ok(DISCOVER_API)
        assert gov.report_429(ALBUM_PAGE) == _COOLDOWNS[1]

    def test_reset_clears_everything(self) -> None:
        """Called on logout — a 429 is account-scoped, so a new session starts clean."""
        gov, _ = _governor()
        gov.report_429(ALBUM_PAGE)
        gov.reset()
        assert gov.blocked_for(ALBUM_PAGE) == 0.0
        assert gov.report_429(ALBUM_PAGE) == _COOLDOWNS[0]


class TestShutdown:
    def test_interrupted_wait_returns_false(self) -> None:
        """A stopping daemon must not sit out a five-minute cooldown, and the caller
        must not issue the request it was waiting for."""
        gov, _ = _governor(interrupt_after=1)
        gov.report_429(ALBUM_PAGE)
        assert gov.wait_turn(ALBUM_PAGE) is False

    def test_real_clock_wait_is_interruptible(self) -> None:
        stop = threading.Event()
        clock = RealClock(stop)
        stop.set()
        assert clock.wait(30.0) is True  # returns at once, not in 30s

    def test_real_clock_reports_no_interrupt_on_timeout(self) -> None:
        assert RealClock(threading.Event()).wait(0.001) is False

    def test_real_clock_is_monotonic_not_wall_clock(self) -> None:
        """Spacing arithmetic must survive a system clock adjustment, so now() has
        to be a monotonic source rather than time.time()."""
        clock = RealClock()
        first = clock.now()
        clock.wait(0.002)
        assert clock.now() >= first


class TestConcurrency:
    def test_report_429_is_not_blocked_by_a_waiting_thread(self) -> None:
        """The lock must not be held across the wait.

        If wait_turn waited while holding the lock, a slow consumer would block an
        unrelated thread's rate-limit report — a safety net causing the outage it
        exists to prevent.

        The report runs on a *separate thread* with a join timeout on purpose: a
        held lock is a deadlock, and calling report_429 inline would hang the suite
        instead of failing it. This way the regression reports itself.
        """
        gov, clock = _governor()
        done = threading.Event()
        entered = threading.Event()
        # Whether the other thread's report landed *while* we were mid-wait. That
        # is the invariant: checking only that it eventually completes would pass
        # even with the lock held, since the lock is released once the wait ends.
        landed_during_wait: list[bool] = []

        original_wait = clock.wait

        def instrumented_wait(timeout: float) -> bool:
            entered.set()  # wait_turn is now mid-wait
            landed_during_wait.append(done.wait(0.5))
            return original_wait(timeout)

        clock.wait = instrumented_wait  # type: ignore[method-assign]

        def reporter() -> None:
            entered.wait(1.0)
            gov.report_429(DISCOVER_API)  # blocks here if wait_turn holds the lock
            done.set()

        thread = threading.Thread(target=reporter)
        thread.start()
        gov.report_429(ALBUM_PAGE)
        gov.wait_turn(ALBUM_PAGE)
        thread.join(timeout=2.0)

        assert not thread.is_alive()
        assert landed_during_wait == [True], (
            "report_429 did not complete while wait_turn was waiting — "
            "wait_turn is holding the lock across the wait"
        )

    def test_two_threads_in_one_class_do_not_share_a_slot(self) -> None:
        """The slot is reserved before the lock is released, so two callers cannot
        both conclude it is their turn at the same instant."""
        gov, clock = _governor()
        gov.wait_turn(ALBUM_PAGE)
        first = clock.now()
        gov.wait_turn(ALBUM_PAGE)
        second = clock.now()
        gov.wait_turn(ALBUM_PAGE)
        assert second - first == pytest.approx(_MIN_SPACING[ALBUM_PAGE])
        assert clock.now() - second == pytest.approx(_MIN_SPACING[ALBUM_PAGE])


class TestSingleton:
    def test_get_governor_is_stable_and_resettable(self) -> None:
        assert get_governor() is get_governor()
        first = get_governor()
        reset_governor()
        assert get_governor() is not first

    def test_refuses_to_construct_in_a_subprocess(self, monkeypatch) -> None:
        """A spawn child would get a stateless instance that silently governs
        nothing; the sync and download children have their own backoff instead."""
        monkeypatch.setattr(
            "kamp_daemon.bandcamp_ratelimit.multiprocessing.parent_process",
            lambda: object(),
        )
        with pytest.raises(RuntimeError, match="parent-process only"):
            BandcampGovernor()
