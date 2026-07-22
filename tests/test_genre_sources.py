"""Tests for kamp_daemon.genre_sources (KAMP-587 Last.fm genre enrichment)."""

from __future__ import annotations

import time
import types
from typing import Any

import pytest

from kamp_daemon import genre_sources as gs
from kamp_daemon.genre_sources import (
    GenreQuery,
    LastfmGenreSource,
    canonicalize,
    enabled_sources,
    fetch_all_genres,
    _run_with_timeout,
)

# --- pylast mocks -----------------------------------------------------------


class _FakeItem:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


class _FakeTop:
    def __init__(self, name: str, weight: int) -> None:
        self.item = _FakeItem(name)
        self.weight = weight


class _FakeEntity:
    def __init__(self, tags: list[_FakeTop], raise_: bool) -> None:
        self._tags = tags
        self._raise = raise_

    def get_top_tags(self, limit: int | None = None) -> list[_FakeTop]:
        if self._raise:
            raise RuntimeError("boom")
        return self._tags


class _FakeNetwork:
    def __init__(
        self,
        album: list[_FakeTop] | None = None,
        artist: list[_FakeTop] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self._album = album or []
        self._artist = artist or []
        self._raise_on = raise_on

    def get_album(self, artist: str, album: str) -> _FakeEntity:
        return _FakeEntity(self._album, self._raise_on == "album")

    def get_artist(self, artist: str) -> _FakeEntity:
        return _FakeEntity(self._artist, self._raise_on == "artist")


_Q = GenreQuery(album_artist="Slowdive", album="Souvlaki")


# --- canonicalize -----------------------------------------------------------


class TestCanonicalize:
    def test_filters_to_allowlist_with_canonical_casing(self) -> None:
        # junk dropped; allowlist genres kept in the allowlist's canonical casing.
        out = canonicalize(["shoegaze", "seen live", "vinyl", "DREAM POP"])
        assert out == ["Shoegaze", "Dream Pop"]

    def test_dedupes_case_insensitively_order_preserving(self) -> None:
        out = canonicalize(["Techno", "techno", "House"])
        assert out == ["Techno", "House"]

    def test_all_junk_returns_empty(self) -> None:
        assert canonicalize(["seen live", "favourites", "spotify", "00s"]) == []

    def test_missing_allowlist_degrades_to_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(gs, "_allowlist", None)
        monkeypatch.setattr(gs, "_ALLOWLIST_PATH", tmp_path / "nope.txt")
        try:
            assert canonicalize(["Shoegaze"]) == []
        finally:
            gs._allowlist = None  # reset the module cache for other tests


# --- allow-list extras (KAMP-610) -------------------------------------------


class TestAllowlistExtras:
    @pytest.fixture(autouse=True)
    def _reset(self) -> Any:
        yield
        gs._extras = []
        gs._allowlist = None  # restore pristine module state for other tests

    def test_extras_pass_through_canonicalize(self) -> None:
        gs.set_allowlist_extras(["Zzz Fake Genre"])
        assert canonicalize(["zzz fake genre"]) == ["Zzz Fake Genre"]

    def test_set_extras_invalidates_cache(self) -> None:
        assert canonicalize(["zzz fake genre"]) == []  # loads cache without extras
        gs.set_allowlist_extras(["Zzz Fake Genre"])
        assert canonicalize(["zzz fake genre"]) == ["Zzz Fake Genre"]  # rebuilt

    def test_default_casing_wins_over_extra(self) -> None:
        gs.set_allowlist_extras(["shoegaze"])  # a shipped default is "Shoegaze"
        assert canonicalize(["SHOEGAZE"]) == ["Shoegaze"]

    def test_default_allowlist_names_excludes_extras(self) -> None:
        gs.set_allowlist_extras(["Zzz Fake Genre"])
        names = gs.default_allowlist_names()
        assert "Zzz Fake Genre" not in names
        assert "Shoegaze" in names  # a real shipped default


# --- _run_with_timeout ------------------------------------------------------


class TestRunWithTimeout:
    def test_returns_result(self) -> None:
        assert _run_with_timeout(lambda: ["a", "b"], 1.0) == ["a", "b"]

    def test_abandons_slow_fetch(self) -> None:
        def slow() -> list[str]:
            time.sleep(2.0)
            return ["late"]

        assert _run_with_timeout(slow, 0.1) == []

    def test_swallows_error(self) -> None:
        def boom() -> list[str]:
            raise RuntimeError("network down")

        assert _run_with_timeout(boom, 1.0) == []


# --- LastfmGenreSource ------------------------------------------------------


class TestLastfmGenreSource:
    def test_fetch_canonicalizes_album_and_artist_tags(self) -> None:
        net = _FakeNetwork(
            album=[_FakeTop("Shoegaze", 100), _FakeTop("seen live", 90)],
            artist=[_FakeTop("Dream Pop", 80)],
        )
        out = LastfmGenreSource(network=net).fetch(_Q)
        assert out == ["Shoegaze", "Dream Pop"]

    def test_weight_threshold_drops_long_tail(self) -> None:
        net = _FakeNetwork(album=[_FakeTop("Shoegaze", 100), _FakeTop("Noise", 5)])
        out = LastfmGenreSource(network=net).fetch(_Q)
        assert out == ["Shoegaze"]  # Noise is allowlisted but under-weighted

    def test_one_getter_failing_still_returns_other(self) -> None:
        net = _FakeNetwork(artist=[_FakeTop("Dream Pop", 80)], raise_on="album")
        out = LastfmGenreSource(network=net).fetch(_Q)
        assert out == ["Dream Pop"]

    def test_total_failure_returns_empty(self) -> None:
        net = _FakeNetwork(raise_on="album")  # artist empty, album raises
        assert LastfmGenreSource(network=net).fetch(_Q) == []

    def test_get_network_builds_from_shared_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        captured: dict[str, Any] = {}

        def _fake_net(api_key: str) -> str:
            captured["key"] = api_key
            return "NET"

        monkeypatch.setitem(
            sys.modules, "pylast", types.SimpleNamespace(LastFMNetwork=_fake_net)
        )
        monkeypatch.setattr("kamp_core.scrobbler.LASTFM_API_KEY", "K123")
        net = LastfmGenreSource()._get_network()
        assert net == "NET"
        assert captured["key"] == "K123"


# --- wiring -----------------------------------------------------------------


class TestEnabledSources:
    def _config(self, on: bool) -> Any:
        return types.SimpleNamespace(tagging=types.SimpleNamespace(lastfm_genres=on))

    def test_lastfm_enabled(self) -> None:
        sources = enabled_sources(self._config(True))
        assert len(sources) == 1 and isinstance(sources[0], LastfmGenreSource)

    def test_disabled_returns_empty(self) -> None:
        assert enabled_sources(self._config(False)) == []


class TestFetchAllGenres:
    def test_unions_sources_deduped(self) -> None:
        net1 = _FakeNetwork(album=[_FakeTop("Shoegaze", 100)])
        net2 = _FakeNetwork(
            album=[_FakeTop("shoegaze", 100), _FakeTop("Dream Pop", 90)]
        )
        out = fetch_all_genres(
            [LastfmGenreSource(network=net1), LastfmGenreSource(network=net2)], _Q
        )
        assert out == ["Shoegaze", "Dream Pop"]

    def test_empty_source_list(self) -> None:
        assert fetch_all_genres([], _Q) == []

    def test_buggy_source_is_swallowed(self) -> None:
        class _Boom(gs.GenreSource):
            def fetch(self, query: GenreQuery) -> list[str]:
                raise RuntimeError("bug")

        net = _FakeNetwork(album=[_FakeTop("Shoegaze", 100)])
        out = fetch_all_genres([_Boom(), LastfmGenreSource(network=net)], _Q)
        assert out == ["Shoegaze"]


class _StubSource(gs.GenreSource):
    def __init__(self, genres: list[str]) -> None:
        self._genres = genres

    def fetch(self, query: GenreQuery) -> list[str]:
        return self._genres


class TestEnrichAlbumGenres:
    def _config(self, on: bool = True) -> Any:
        return types.SimpleNamespace(tagging=types.SimpleNamespace(lastfm_genres=on))

    def _seed_local(self, tmp_path: Any, genres: list[str]) -> Any:
        import mutagen.id3 as id3

        from kamp_core.library import LibraryIndex, Track

        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                Track(
                    file_path=mp3,
                    title="T",
                    artist="Slowdive",
                    album_artist="Slowdive",
                    album="Souvlaki",
                    release_date="1993",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    genres=genres,
                )
            ]
        )
        return index, mp3

    def test_merges_into_db_and_file(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import mutagen.id3 as id3

        index, mp3 = self._seed_local(tmp_path, ["Jazz"])
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(
            gs, "enabled_sources", lambda cfg: [_StubSource(["Shoegaze"])]
        )

        applied = gs.enrich_album_genres(index, [tid], self._config())
        db_genres = {
            g.strip() for g in index.get_track_by_id(tid).genre.split(";") if g.strip()
        }
        index.close()

        assert applied == ["Shoegaze"]
        assert db_genres == {"Jazz", "Shoegaze"}  # merge, not replace
        assert set(id3.ID3(str(mp3))["TCON"].text) == {"Jazz", "Shoegaze"}

    def test_extra_genres_apply_verbatim_without_lastfm(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # KAMP-591: extra_genres (Bandcamp keywords) apply even when Last.fm is
        # off/empty, and VERBATIM — "Portland" is not in the allowlist but a
        # Bandcamp album's location tag is kept (588 consistency), unlike Last.fm.
        index, _ = self._seed_local(tmp_path, [])
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(gs, "enabled_sources", lambda cfg: [])  # Last.fm off
        applied = gs.enrich_album_genres(
            index, [tid], self._config(), extra_genres=["Portland", "Shoegaze"]
        )
        genre = index.get_track_by_id(tid).genre
        index.close()
        assert set(applied) == {"Portland", "Shoegaze"}
        assert "Portland" in genre and "Shoegaze" in genre

    def test_union_lastfm_and_extra_deduped(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index, _ = self._seed_local(tmp_path, [])
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(
            gs, "enabled_sources", lambda cfg: [_StubSource(["Shoegaze"])]
        )
        applied = gs.enrich_album_genres(
            index, [tid], self._config(), extra_genres=["shoegaze", "Dream Pop"]
        )
        index.close()
        assert applied == ["Shoegaze", "Dream Pop"]  # dedup, order-preserving

    def test_file_write_failure_is_best_effort(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failing file write must not crash enrichment; the DB still updates.
        index, _ = self._seed_local(tmp_path, ["Jazz"])
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(
            gs, "enabled_sources", lambda cfg: [_StubSource(["Shoegaze"])]
        )

        def _boom(*a: Any, **k: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("kamp_core.library.write_meta_tags_to_file", _boom)
        applied = gs.enrich_album_genres(index, [tid], self._config())
        genre = index.get_track_by_id(tid).genre
        index.close()
        assert applied == ["Shoegaze"]
        assert "Shoegaze" in genre  # DB merge happened despite the file failure

    def test_disabled_config_is_noop(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index, _ = self._seed_local(tmp_path, ["Jazz"])
        tid = index.all_tracks()[0].id
        # enabled_sources returns [] for a disabled config — no patch needed.
        applied = gs.enrich_album_genres(index, [tid], self._config(on=False))
        db_genres = {
            g.strip() for g in index.get_track_by_id(tid).genre.split(";") if g.strip()
        }
        index.close()
        assert applied == []
        assert db_genres == {"Jazz"}

    def test_no_source_genres_is_noop(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index, _ = self._seed_local(tmp_path, ["Jazz"])
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(gs, "enabled_sources", lambda cfg: [_StubSource([])])
        applied = gs.enrich_album_genres(index, [tid], self._config())
        db_genres = {
            g.strip() for g in index.get_track_by_id(tid).genre.split(";") if g.strip()
        }
        index.close()
        assert applied == []
        assert db_genres == {"Jazz"}

    def test_enrich_new_tracks_resolves_ids_from_db(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A scan's new_tracks are read from files and carry id=0, so the trigger
        # must resolve real ids from the DB by album — grouping on track.id would
        # resolve nothing (the bug behind "not working after ingest").
        from kamp_core.library import Track

        index, mp3 = self._seed_local(tmp_path, ["Jazz"])
        real_tid = index.all_tracks()[0].id
        scan_track = Track(
            file_path=mp3,
            title="T",
            artist="Slowdive",
            album_artist="Slowdive",
            album="Souvlaki",
            release_date="1993",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
        )
        assert scan_track.id == 0  # no DB id, like a real scan result
        monkeypatch.setattr(
            gs, "enabled_sources", lambda cfg: [_StubSource(["Shoegaze"])]
        )
        gs.enrich_new_tracks(index, [scan_track], self._config())
        genre = index.get_track_by_id(real_tid).genre
        index.close()
        assert "Shoegaze" in genre and "Jazz" in genre

    def test_enrich_new_tracks_disabled_is_noop(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index, _ = self._seed_local(tmp_path, ["Jazz"])
        tracks = index.all_tracks()
        gs.enrich_new_tracks(index, tracks, self._config(on=False))
        genre = index.get_track_by_id(tracks[0].id).genre
        index.close()
        assert genre == "Jazz"

    def test_remote_track_gets_db_genres_no_file_write(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A remote/streaming track has no local file to stamp — apply_genres still
        # updates the DB, and the file-write branch is skipped (no crash on the
        # bandcamp:// path).
        from pathlib import Path as _Path

        from kamp_core.library import LibraryIndex, Track

        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                Track(
                    file_path=_Path("bandcamp://S1/1"),
                    title="T",
                    artist="Grimes",
                    album_artist="Grimes",
                    album="Visions",
                    release_date="2012",
                    track_number=1,
                    disc_number=1,
                    ext="",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                    genres=["Art Pop"],
                )
            ]
        )
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(
            gs, "enabled_sources", lambda cfg: [_StubSource(["Synth-Pop"])]
        )
        applied = gs.enrich_album_genres(index, [tid], self._config())
        genre = index.get_track_by_id(tid).genre
        index.close()
        assert applied == ["Synth-Pop"]
        assert "Art Pop" in genre and "Synth-Pop" in genre

    # KAMP-618: enrich_albums — the album-keyed core shared by local ingest and the
    # streaming-add trigger.
    def test_enrich_albums_enriches_each_key(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index, _ = self._seed_local(tmp_path, ["Jazz"])
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(
            gs, "enabled_sources", lambda cfg: [_StubSource(["Shoegaze"])]
        )
        gs.enrich_albums(index, [("Slowdive", "Souvlaki")], self._config())
        genre = index.get_track_by_id(tid).genre
        index.close()
        assert "Jazz" in genre and "Shoegaze" in genre  # merged, not replaced

    def test_enrich_albums_noop_when_no_source(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index, _ = self._seed_local(tmp_path, ["Jazz"])
        tid = index.all_tracks()[0].id
        monkeypatch.setattr(gs, "enabled_sources", lambda cfg: [])
        gs.enrich_albums(index, [("Slowdive", "Souvlaki")], self._config(on=False))
        genre = index.get_track_by_id(tid).genre
        index.close()
        assert genre == "Jazz"  # untouched

    def test_enrich_albums_skips_unknown_album(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index, _ = self._seed_local(tmp_path, ["Jazz"])
        monkeypatch.setattr(
            gs, "enabled_sources", lambda cfg: [_StubSource(["Shoegaze"])]
        )
        gs.enrich_albums(index, [("Nobody", "Nothing")], self._config())  # no tracks
        tid = index.all_tracks()[0].id
        genre = index.get_track_by_id(tid).genre
        index.close()
        assert genre == "Jazz"  # unrelated album untouched, no crash
