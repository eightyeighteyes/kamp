"""Which player the ambiguous controls address (KAMP-736).

kamp has two mpv engines — the user's queue and the Crate preview — and three
inputs that name neither: the spacebar, the arrow keys and the media keys. This
records which engine was playing most recently so those inputs can act on it.

The rule, from the ticket:

* explicit controls address their own transport (the main transport's play
  button always recovers the main queue);
* ambiguous controls follow the most recent player;
* the preview's claim expires, after which the main queue wins again.

Expiry is the half that is easy to get wrong. Nothing reaps a paused preview —
``_arm_idle_timer`` is called from ``stop()`` and ``release_for_main()`` but
never from ``pause()`` — so without a bound, a preview paused at 09:00 still
owns the media key at 17:00, and resuming it re-resolves an expired URL and
replays the album from the top against Bandcamp.
"""

from __future__ import annotations

from kamp_core.transport_floor import MAIN, PREVIEW, FloorClaim


class TestDefault:
    def test_nothing_has_played_yet_so_the_queue_wins(self) -> None:
        """A fresh daemon has no history. The user's own queue is the safer
        surprise than a record they have not chosen."""
        assert FloorClaim().holder() == MAIN

    def test_main_is_the_fallback_after_the_preview_expires(self) -> None:
        clock = [1000.0]
        floor = FloorClaim(ttl=300.0, now=lambda: clock[0])
        floor.claim(PREVIEW)
        clock[0] += 301.0
        assert floor.holder() == MAIN


class TestClaiming:
    def test_the_most_recent_claim_wins(self) -> None:
        floor = FloorClaim(now=lambda: 1000.0)
        floor.claim(PREVIEW)
        assert floor.holder() == PREVIEW
        floor.claim(MAIN)
        assert floor.holder() == MAIN
        floor.claim(PREVIEW)
        assert floor.holder() == PREVIEW

    def test_a_fresh_preview_claim_holds(self) -> None:
        clock = [1000.0]
        floor = FloorClaim(ttl=300.0, now=lambda: clock[0])
        floor.claim(PREVIEW)
        clock[0] += 299.0
        assert floor.holder() == PREVIEW

    def test_re_claiming_restarts_the_clock(self) -> None:
        """A preview that plays for ten minutes must not expire mid-record.

        The publish path claims on every playing and paused snapshot, so the
        stamp tracks the last moment it was audible rather than when it started.
        """
        clock = [1000.0]
        floor = FloorClaim(ttl=300.0, now=lambda: clock[0])
        floor.claim(PREVIEW)
        clock[0] += 250.0
        floor.claim(PREVIEW)
        clock[0] += 250.0
        assert floor.holder() == PREVIEW


class TestMainNeverExpires:
    def test_an_old_main_claim_still_wins(self) -> None:
        """Only the preview's claim is bounded. Expiry exists to stop a
        forgotten record ambushing the user; falling back TO the queue needs no
        such protection, and expiring main's claim would change nothing anyway
        because main is already what expiry falls back to."""
        clock = [1000.0]
        floor = FloorClaim(ttl=300.0, now=lambda: clock[0])
        floor.claim(MAIN)
        clock[0] += 86_400.0
        assert floor.holder() == MAIN


class TestUnknownClaimant:
    def test_an_unrecognised_name_is_refused(self) -> None:
        """A typo'd claimant would otherwise sit in the state unreadable by
        holder(), silently handing every key to the main queue forever."""
        floor = FloorClaim()
        try:
            floor.claim("turntable")
        except ValueError:
            return
        raise AssertionError("expected ValueError for an unknown claimant")
