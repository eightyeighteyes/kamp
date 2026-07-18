"""Tests for kamp_daemon.genre_backfill (KAMP-591 library-wide genre backfill)."""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from kamp_daemon import genre_backfill as gb
from kamp_daemon.config import (
    ArtworkConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
    TaggingConfig,
)


def _config() -> Config:
    return Config(
        paths=PathsConfig(watch_folder=None, library=None),
        musicbrainz=MusicBrainzConfig(),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
        library=LibraryConfig(path_template=""),
        tagging=TaggingConfig(lastfm_genres=True),
    )


class _Track:
    def __init__(self, tid: int) -> None:
        self.id = tid


def _album(aid: int, **extra: Any) -> dict[str, Any]:
    row = {
        "id": aid,
        "album_artist": f"Artist{aid}",
        "album": f"Album{aid}",
        "sale_item_id": None,
        "album_url": None,
        "keywords": None,
    }
    row.update(extra)
    return row


def _index(albums: list[dict[str, Any]]) -> MagicMock:
    index = MagicMock()
    index.albums_pending_genre_enrichment.return_value = albums
    index.tracks_for_album.return_value = [_Track(1)]
    return index


class _Progress:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str]] = []

    def __call__(self, done: int, total: int, state: str) -> None:
        self.calls.append((done, total, state))


class TestRunGenreBackfill:
    def test_enriches_each_album_and_marks_done(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = _index([_album(1), _album(2)])
        calls: list[Any] = []
        monkeypatch.setattr(
            gb,
            "enrich_album_genres",
            lambda idx, ids, cfg, **kw: calls.append(ids) or [],
        )
        prog = _Progress()
        gb.run_genre_backfill(
            index, _config(), None, prog, threading.Event(), sleep=lambda _s: None
        )
        assert len(calls) == 2  # both albums enriched
        assert index.mark_album_genres_enriched.call_count == 2
        assert prog.calls[-1] == (2, 2, gb.DONE)

    def test_empty_library_reports_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prog = _Progress()
        gb.run_genre_backfill(
            _index([]), _config(), None, prog, threading.Event(), sleep=lambda _s: None
        )
        assert prog.calls[-1] == (0, 0, gb.DONE)

    def test_cancel_stops_mid_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        index = _index([_album(1), _album(2), _album(3)])
        cancel = threading.Event()
        seen: list[int] = []

        def _enrich(idx: Any, ids: Any, cfg: Any, **kw: Any) -> list[str]:
            seen.append(1)
            cancel.set()  # cancel after the first album
            return []

        monkeypatch.setattr(gb, "enrich_album_genres", _enrich)
        prog = _Progress()
        gb.run_genre_backfill(
            index, _config(), None, prog, cancel, sleep=lambda _s: None
        )
        assert len(seen) == 1  # stopped before the 2nd album
        assert prog.calls[-1][2] == gb.CANCELLED

    def test_bandcamp_cache_hit_skips_rescrape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = _index(
            [_album(1, sale_item_id="S1", keywords='["Shoegaze"]', album_url="u")]
        )
        session = MagicMock()  # must NOT be used on a cache hit
        passed: list[Any] = []
        monkeypatch.setattr(
            gb,
            "enrich_album_genres",
            lambda idx, ids, cfg, *, extra_genres=(): passed.append(list(extra_genres))
            or [],
        )
        gb.run_genre_backfill(
            index,
            _config(),
            session,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        session.get.assert_not_called()
        assert passed == [["Shoegaze"]]

    def test_bandcamp_cache_miss_rescrapes_and_caches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = _index(
            [_album(1, sale_item_id="S1", keywords=None, album_url="https://x/album")]
        )
        resp = MagicMock()
        resp.text = "<html></html>"
        session = MagicMock()
        session.get.return_value = resp
        monkeypatch.setattr(
            "kamp_daemon.bandcamp.parse_album_keywords", lambda html: ["Noise Rock"]
        )
        passed: list[Any] = []
        monkeypatch.setattr(
            gb,
            "enrich_album_genres",
            lambda idx, ids, cfg, *, extra_genres=(): passed.append(list(extra_genres))
            or [],
        )
        gb.run_genre_backfill(
            index,
            _config(),
            session,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        session.get.assert_called_once()
        index.set_collection_keywords.assert_called_once_with("S1", ["Noise Rock"])
        assert passed == [["Noise Rock"]]

    def test_empty_rescrape_is_not_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A challenge page parses to [] — must NOT be cached (would permanently
        # skip the album on future runs).
        index = _index(
            [_album(1, sale_item_id="S1", keywords=None, album_url="https://x/a")]
        )
        resp = MagicMock()
        resp.text = "<html>challenge</html>"
        session = MagicMock()
        session.get.return_value = resp
        monkeypatch.setattr(
            "kamp_daemon.bandcamp.parse_album_keywords", lambda html: []
        )
        monkeypatch.setattr(gb, "enrich_album_genres", lambda idx, ids, cfg, **kw: [])
        gb.run_genre_backfill(
            index,
            _config(),
            session,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        index.set_collection_keywords.assert_not_called()

    def test_malformed_keywords_cache_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A corrupt keywords blob must degrade to [] (no genres), never crash or
        # trigger a re-scrape.
        index = _index(
            [_album(1, sale_item_id="S1", keywords="not-json", album_url="u")]
        )
        session = MagicMock()
        passed: list[Any] = []
        monkeypatch.setattr(
            gb,
            "enrich_album_genres",
            lambda idx, ids, cfg, *, extra_genres=(): passed.append(list(extra_genres))
            or [],
        )
        gb.run_genre_backfill(
            index,
            _config(),
            session,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        session.get.assert_not_called()
        assert passed == [[]]

    def test_no_session_skips_bandcamp_rescrape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A cache-miss Bandcamp album with no session (not logged in) yields no
        # extra genres — Last.fm still runs.
        index = _index(
            [_album(1, sale_item_id="S1", keywords=None, album_url="https://x/a")]
        )
        passed: list[Any] = []
        monkeypatch.setattr(
            gb,
            "enrich_album_genres",
            lambda idx, ids, cfg, *, extra_genres=(): passed.append(list(extra_genres))
            or [],
        )
        gb.run_genre_backfill(
            index,
            _config(),
            None,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        assert passed == [[]]

    def test_rescrape_exception_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = _index(
            [_album(1, sale_item_id="S1", keywords=None, album_url="https://x/a")]
        )
        session = MagicMock()
        session.get.side_effect = RuntimeError("boom")
        passed: list[Any] = []
        monkeypatch.setattr(
            gb,
            "enrich_album_genres",
            lambda idx, ids, cfg, *, extra_genres=(): passed.append(list(extra_genres))
            or [],
        )
        gb.run_genre_backfill(
            index,
            _config(),
            session,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        index.set_collection_keywords.assert_not_called()
        assert passed == [[]]

    def test_album_with_no_tracks_is_marked_and_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An album whose tracks resolve to nothing is checkpointed but never
        # enriched (no ids to apply to).
        index = _index([_album(1)])
        index.tracks_for_album.return_value = []
        called: list[Any] = []
        monkeypatch.setattr(
            gb, "enrich_album_genres", lambda *a, **k: called.append(1) or []
        )
        prog = _Progress()
        gb.run_genre_backfill(
            index, _config(), None, prog, threading.Event(), sleep=lambda _s: None
        )
        assert called == []  # never enriched
        index.mark_album_genres_enriched.assert_called_once()  # still checkpointed
        assert prog.calls[-1] == (1, 1, gb.DONE)

    def test_enrich_exception_does_not_break_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = _index([_album(1), _album(2)])

        def _enrich(idx: Any, ids: Any, cfg: Any, **kw: Any) -> list[str]:
            raise RuntimeError("network exploded")

        monkeypatch.setattr(gb, "enrich_album_genres", _enrich)
        prog = _Progress()
        gb.run_genre_backfill(
            index, _config(), None, prog, threading.Event(), sleep=lambda _s: None
        )
        # Both albums attempted and checkpointed despite the raise.
        assert index.mark_album_genres_enriched.call_count == 2
        assert prog.calls[-1] == (2, 2, gb.DONE)

    def test_breaker_resets_on_productive_album(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A slow+empty album increments the breaker; a productive album resets it,
        # so an intermittent Last.fm never trips the breaker.
        monkeypatch.setattr(gb, "_LASTFM_SLOW_S", -1.0)  # every album counts as slow
        monkeypatch.setattr(gb, "_LASTFM_BREAKER_N", 3)
        index = _index([_album(i) for i in range(1, 5)])
        # album 1,2 empty (slow) → count=2; album 3 productive → reset; album 4 empty.
        results = iter([[], [], ["Rock"], []])
        flags: list[bool] = []

        def _enrich(idx: Any, ids: Any, cfg: Any, **kw: Any) -> list[str]:
            flags.append(cfg.tagging.lastfm_genres)
            return next(results)

        monkeypatch.setattr(gb, "enrich_album_genres", _enrich)
        gb.run_genre_backfill(
            index,
            _config(),
            None,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        # Breaker never trips (reset at album 3), so Last.fm stays on throughout.
        assert flags == [True, True, True, True]

    def test_lastfm_circuit_breaker_trips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # After N slow+empty albums, Last.fm is disabled for the rest of the run:
        # subsequent enrich calls receive a config with lastfm_genres=False.
        monkeypatch.setattr(gb, "_LASTFM_SLOW_S", -1.0)  # every empty album counts
        monkeypatch.setattr(gb, "_LASTFM_BREAKER_N", 2)
        index = _index([_album(i) for i in range(1, 6)])
        flags: list[bool] = []
        monkeypatch.setattr(
            gb,
            "enrich_album_genres",
            lambda idx, ids, cfg, **kw: flags.append(cfg.tagging.lastfm_genres) or [],
        )
        gb.run_genre_backfill(
            index,
            _config(),
            None,
            _Progress(),
            threading.Event(),
            sleep=lambda _s: None,
        )
        # First 2 albums with Last.fm on (both empty → trips), then off for 3-5.
        assert flags == [True, True, False, False, False]
