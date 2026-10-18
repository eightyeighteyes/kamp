"""Stream sync respects the measured Bandcamp rate ceiling (KAMP-729).

Catching up a stale library produced 191 relayed 429s and 151 "skipping
tracks" warnings in about four minutes — roughly every other album dropped,
and the skipped ones hit the same wall on every later poll.

Three causes, in order of how much they mattered:

1. Every album page was fetched TWICE — once by fetch_album_tracks and again
   by fetch_album_art_bytes, purely to re-read art_id the first call already
   had.
2. The walk spaced requests at 0.5s. KAMP-644 measured album pages returning
   429 after 57 requests in 39s (~87/min); bandcamp_ratelimit pins the safe
   rate at <=40/min. Two requests per album at 0.5s is ~120/min — above the
   measured ceiling.
3. A 429 fell into a blanket `except Exception` that logged a skip and moved
   on, so the walk never slowed and never retried.

No real sleeps here: the walk takes an injected `sleep`, mirroring
backfill_download_sizes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.bandcamp import (
    RateLimited,
    _Pacer,
    fetch_album_art_bytes,
    fetch_album_tracks,
)
from kamp_daemon.bandcamp_ratelimit import cooldowns, min_spacing
from kamp_daemon.discovery import ALBUM_PAGE

# The loop-level tests live in test_bandcamp.py, beside the collection-response
# harness they need; this module covers the pieces that stand alone.


def _noop(_seconds: float) -> None:
    return None


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.content = b"\xff\xd8\xff"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("raise_for_status should not be reached for 429")


def test_a_429_raises_rate_limited_not_a_generic_error() -> None:
    """429 must be distinguishable from a dead album.

    Read off .status_code rather than raise_for_status, because a relayed
    response is a _ProxyResponse whose HTTPError carries no .response — the
    same call discovery_sources already made.
    """
    session = MagicMock()
    session.get.return_value = _Resp(429)

    with pytest.raises(RateLimited):
        fetch_album_tracks("https://b.bandcamp.com/album/x", 1, "B", "A", session)


def test_art_fetch_with_a_known_art_id_does_not_fetch_the_page() -> None:
    """The headline fix: one album-page request per album, not two.

    fetch_album_art_bytes used to GET the album page again just to re-read
    art_id. Given the id, the only request is to the art CDN.
    """
    with patch("kamp_daemon.bandcamp._make_requests_session") as make_session:
        with patch("kamp_daemon.bandcamp._requests.get") as cdn_get:
            cdn_get.return_value = _Resp(200)
            data = fetch_album_art_bytes(
                "https://b.bandcamp.com/album/x", {}, art_id=42
            )

    assert data == b"\xff\xd8\xff"
    # No authenticated session at all — nothing touched bandcamp.com.
    make_session.assert_not_called()
    assert "a42_0.jpg" in cdn_get.call_args[0][0]


def test_art_fetch_surfaces_a_429_instead_of_returning_none() -> None:
    """ "Rate limited" and "this album has no art" are different answers.

    Conflating them hid 429s from the log entirely and left no cache file, so
    every later poll retried the same album forever.
    """
    session = MagicMock()
    session.get.return_value = _Resp(429)

    with patch("kamp_daemon.bandcamp._make_requests_session", return_value=session):
        with pytest.raises(RateLimited):
            fetch_album_art_bytes("https://b.bandcamp.com/album/x", {})


class TestPacer:
    def test_spacing_comes_from_the_measured_ceiling(self) -> None:
        """Not a hand-picked number — the one the governor already uses."""
        waits: list[float] = []
        pacer = _Pacer(sleep=waits.append)
        pacer.wait_turn()  # first request is free
        pacer.wait_turn()
        assert waits == [min_spacing(ALBUM_PAGE)]
        assert min_spacing(ALBUM_PAGE) > 0.5  # the old hardcoded value

    def test_the_ladder_escalates_then_gives_up(self) -> None:
        waits: list[float] = []
        pacer = _Pacer(sleep=waits.append)
        assert [pacer.report_rate_limited() for _ in cooldowns()] == [True] * len(
            cooldowns()
        )
        assert waits == list(cooldowns())
        # Exhausted: the caller is told to stop rather than loop forever.
        assert pacer.report_rate_limited() is False

    def test_a_clean_fetch_resets_the_ladder(self) -> None:
        """Scattered 429s over a long walk must not each cost the full pause."""
        waits: list[float] = []
        pacer = _Pacer(sleep=waits.append)
        pacer.report_rate_limited()
        pacer.report_rate_limited()
        pacer.report_ok()
        pacer.report_rate_limited()
        assert waits[-1] == cooldowns()[0]

    def test_total_pause_is_capped(self) -> None:
        """The ladder alone is unbounded, and the sync subprocess is not a
        daemon — an unbounded pause is an unbounded quit hang."""
        pacer = _Pacer(sleep=_noop)
        # Never reset, so each strike takes the next rung and the running
        # total climbs until the cap refuses.
        results = []
        for _ in range(12):
            results.append(pacer.report_rate_limited())
            pacer.report_ok()
        assert False in results

    def test_the_stop_event_ends_a_pause(self) -> None:
        """A quitting daemon must not sit out a five-minute cooldown."""
        import threading

        stop = threading.Event()
        stop.set()
        pacer = _Pacer(stop_event=stop, sleep=_noop)
        # Waiting on an already-set event returns at once, and the pacer
        # reports that the walk should stop.
        assert pacer.report_rate_limited() is False
        assert pacer.stopping is True
