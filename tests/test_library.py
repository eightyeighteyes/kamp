"""Tests for kamp_core.library (LibraryIndex and LibraryScanner)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import mutagen.id3 as id3
import pytest

from kamp_core.library import (
    AlbumInfo,
    LibraryIndex,
    LibraryScanner,
    ScanResult,
    Track,
    extract_art,
    _read_mp3_tags,
    _read_m4a_tags,
    _read_vorbis_tags,
    _read_tags,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mp3(path: Path, **tags: str) -> None:
    """Write a minimal ID3-tagged MP3 stub."""
    t = id3.ID3()
    if "artist" in tags:
        t["TPE1"] = id3.TPE1(encoding=3, text=tags["artist"])
    if "album_artist" in tags:
        t["TPE2"] = id3.TPE2(encoding=3, text=tags["album_artist"])
    if "album" in tags:
        t["TALB"] = id3.TALB(encoding=3, text=tags["album"])
    if "year" in tags:
        t["TDRC"] = id3.TDRC(encoding=3, text=tags["year"])
    if "title" in tags:
        t["TIT2"] = id3.TIT2(encoding=3, text=tags["title"])
    if "track" in tags:
        t["TRCK"] = id3.TRCK(encoding=3, text=tags["track"])
    if "disc" in tags:
        t["TPOS"] = id3.TPOS(encoding=3, text=tags["disc"])
    path.write_bytes(b"\xff\xfb" * 64)
    t.save(str(path))


def _sample_track(file_path: Path) -> Track:
    return Track(
        file_path=file_path,
        title="A Song",
        artist="The Artist",
        album_artist="The Artist",
        album="The Album",
        year="2024",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="rel-123",
        mb_recording_id="rec-456",
    )


# ---------------------------------------------------------------------------
# LibraryIndex
# ---------------------------------------------------------------------------


class TestLibraryIndex:
    def test_wal_journal_mode_enabled(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        # _conn is the current thread's connection; WAL is set on every new conn.
        mode = index._conn.execute("PRAGMA journal_mode").fetchone()[0]
        index.close()

        assert mode == "wal"

    def test_each_thread_gets_its_own_connection(self, tmp_path: Path) -> None:
        """Concurrent threads must not share connection objects."""
        import threading as _threading

        index = LibraryIndex(tmp_path / "library.db")
        conns: list[sqlite3.Connection] = []
        lock = _threading.Lock()

        def _capture() -> None:
            c = index._conn
            with lock:
                conns.append(c)

        threads = [_threading.Thread(target=_capture) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        index.close()
        # All four worker threads plus the main thread each have a unique conn.
        assert len(set(id(c) for c in conns)) == 4

    def test_creates_tables_on_init(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        LibraryIndex(db_path).close()

        conn = sqlite3.connect(str(db_path))
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()

        assert "tracks" in tables
        assert "schema_version" in tables

    def test_migration_version_is_current(self, tmp_path: Path) -> None:
        LibraryIndex(tmp_path / "library.db").close()

        conn = sqlite3.connect(str(tmp_path / "library.db"))
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        conn.close()

        assert version == 6

    def test_upsert_adds_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "01.mp3"))
        tracks = index.all_tracks()
        index.close()

        assert len(tracks) == 1
        assert tracks[0].title == "A Song"
        assert tracks[0].mb_release_id == "rel-123"

    def test_upsert_updates_existing_track(self, tmp_path: Path) -> None:
        path = tmp_path / "01.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(path))
        updated = _sample_track(path)
        updated.title = "Renamed Song"
        index.upsert_track(updated)
        tracks = index.all_tracks()
        index.close()

        assert len(tracks) == 1
        assert tracks[0].title == "Renamed Song"

    def test_remove_track(self, tmp_path: Path) -> None:
        path = tmp_path / "01.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(path))
        index.remove_track(path)
        tracks = index.all_tracks()
        index.close()

        assert tracks == []

    def test_all_tracks_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.all_tracks() == []
        index.close()

    def test_albums_sorted_by_album_artist_then_album(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i, (aa, album) in enumerate(
            [
                ("Zeppelin", "Physical Graffiti"),
                ("Aesop Rock", "Labor Days"),
                ("Aesop Rock", "Bazooka Tooth"),
            ]
        ):
            t = _sample_track(tmp_path / f"{i}.mp3")
            t.artist = aa
            t.album_artist = aa
            t.album = album
            index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert [(a["album_artist"], a["album"]) for a in albums] == [
            ("Aesop Rock", "Bazooka Tooth"),
            ("Aesop Rock", "Labor Days"),
            ("Zeppelin", "Physical Graffiti"),
        ]

    def test_albums_includes_track_count(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i in range(3):
            t = _sample_track(tmp_path / f"{i}.mp3")
            t.track_number = i + 1
            index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert albums[0]["track_count"] == 3

    def test_artists_returns_unique_sorted(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i, aa in enumerate(["Zeppelin", "Aesop Rock", "Aesop Rock"]):
            t = _sample_track(tmp_path / f"{i}.mp3")
            t.album_artist = aa
            index.upsert_track(t)
        artists = index.artists()
        index.close()

        assert artists == ["Aesop Rock", "Zeppelin"]

    def test_tracks_for_album_sorted_by_disc_then_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for disc, track_num, title in [(1, 2, "B"), (2, 1, "C"), (1, 1, "A")]:
            t = _sample_track(tmp_path / f"{disc}-{track_num}.mp3")
            t.title = title
            t.disc_number = disc
            t.track_number = track_num
            index.upsert_track(t)
        tracks = index.tracks_for_album("The Artist", "The Album")
        index.close()

        assert [t.title for t in tracks] == ["A", "B", "C"]

    def test_upsert_many_inserts_all_tracks(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        tracks = [_sample_track(tmp_path / f"{i}.mp3") for i in range(5)]
        for t in tracks:
            t.track_number = int(t.file_path.stem) + 1
        index.upsert_many(tracks)
        result = index.all_tracks()
        index.close()

        assert len(result) == 5

    def test_upsert_many_empty_list_is_noop(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([])
        assert index.all_tracks() == []
        index.close()

    def test_indexed_paths_returns_set_of_paths(self, tmp_path: Path) -> None:
        p1, p2 = tmp_path / "a.mp3", tmp_path / "b.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(p1))
        index.upsert_track(_sample_track(p2))
        paths = index.indexed_paths()
        index.close()

        assert paths == {p1, p2}

    def test_migrate_on_existing_db_is_idempotent(self, tmp_path: Path) -> None:
        """Opening an already-migrated DB should not insert a second version row."""
        db = tmp_path / "library.db"
        LibraryIndex(db).close()
        # Second open hits the `row is not None` branch in _migrate
        index = LibraryIndex(db)
        index.upsert_track(_sample_track(tmp_path / "01.mp3"))
        assert len(index.all_tracks()) == 1
        index.close()

    def test_albums_has_art_true_when_track_has_embedded_art(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "1.mp3")
        t.embedded_art = True
        index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert albums[0].has_art is True

    def test_albums_has_art_false_when_no_embedded_art(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "1.mp3"))
        albums = index.albums()
        index.close()

        assert albums[0].has_art is False

    def test_albums_has_art_true_when_any_track_has_art(self, tmp_path: Path) -> None:
        """has_art is True if at least one track in the album has embedded art."""
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "1.mp3")
        t1.track_number = 1
        t1.embedded_art = False
        t2 = _sample_track(tmp_path / "2.mp3")
        t2.track_number = 2
        t2.embedded_art = True
        index.upsert_many([t1, t2])
        albums = index.albums()
        index.close()

        assert albums[0].has_art is True

    def test_get_track_by_path_returns_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(tmp_path / "01.mp3")
        index.upsert_track(track)
        result = index.get_track_by_path(tmp_path / "01.mp3")
        index.close()

        assert result is not None
        assert result.title == "A Song"

    def test_get_track_by_path_returns_none_for_missing_path(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.get_track_by_path(tmp_path / "missing.mp3")
        index.close()

        assert result is None

    def test_save_and_load_player_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_player_state(tmp_path / "track.mp3", 42.5)
        result = index.load_player_state()
        index.close()

        assert result is not None
        path, position = result
        assert path == tmp_path / "track.mp3"
        assert position == 42.5

    def test_load_player_state_returns_none_when_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.load_player_state()
        index.close()

        assert result is None

    def test_clear_player_state_removes_saved_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_player_state(tmp_path / "track.mp3", 42.5)
        index.clear_player_state()
        result = index.load_player_state()
        index.close()

        assert result is None

    def test_save_player_state_overwrites_previous(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_player_state(tmp_path / "first.mp3", 10.0)
        index.save_player_state(tmp_path / "second.mp3", 99.0)
        result = index.load_player_state()
        index.close()

        assert result is not None
        path, position = result
        assert path == tmp_path / "second.mp3"
        assert position == 99.0

    def test_save_and_load_queue_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        tracks = [tmp_path / "a.mp3", tmp_path / "b.mp3", tmp_path / "c.mp3"]
        index.save_queue_state(tracks, pos=1, shuffle=True, repeat=False)
        result = index.load_queue_state()
        index.close()

        assert result is not None
        paths, pos, shuffle, repeat = result
        assert paths == tracks
        assert pos == 1
        assert shuffle is True
        assert repeat is False

    def test_load_queue_state_returns_none_when_absent(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.load_queue_state()
        index.close()

        assert result is None

    def test_save_queue_state_overwrites_previous(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_queue_state([tmp_path / "a.mp3"], pos=0, shuffle=False, repeat=False)
        index.save_queue_state(
            [tmp_path / "b.mp3", tmp_path / "c.mp3"], pos=1, shuffle=True, repeat=True
        )
        result = index.load_queue_state()
        index.close()

        assert result is not None
        paths, pos, shuffle, repeat = result
        assert paths == [tmp_path / "b.mp3", tmp_path / "c.mp3"]
        assert pos == 1
        assert shuffle is True
        assert repeat is True

    def test_clear_queue_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_queue_state([tmp_path / "a.mp3"], pos=0, shuffle=False, repeat=False)
        index.clear_queue_state()
        result = index.load_queue_state()
        index.close()

        assert result is None


# ---------------------------------------------------------------------------
# extract_art
# ---------------------------------------------------------------------------


class TestExtractArt:
    def test_mp3_with_apic_returns_data_and_mime(self, tmp_path: Path) -> None:
        path = tmp_path / "track.mp3"
        path.write_bytes(b"\xff\xfb" * 64)
        img_data = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        tags = id3.ID3()
        tags.add(
            id3.APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img_data)
        )
        tags.save(str(path))

        result = extract_art(path)

        assert result is not None
        data, mime = result
        assert data == img_data
        assert mime == "image/jpeg"

    def test_mp3_without_art_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "track.mp3"
        _make_mp3(path, title="No Art")

        assert extract_art(path) is None

    def test_nonexistent_path_returns_none(self, tmp_path: Path) -> None:
        assert extract_art(tmp_path / "ghost.mp3") is None


# ---------------------------------------------------------------------------
# LibraryScanner
# ---------------------------------------------------------------------------


class TestLibraryScanner:
    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        index.close()

        assert result == ScanResult(added=0, removed=0, unchanged=0)

    def test_scan_finds_and_indexes_mp3_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="Track One")
        _make_mp3(lib / "02.mp3", title="Track Two")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.added == 2
        assert len(tracks) == 2

    def test_scan_reads_mp3_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(
            lib / "01.mp3",
            artist="The Artist",
            album_artist="The Artist",
            album="Great Album",
            year="2010",
            title="Best Song",
            track="5",
            disc="2",
        )

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        t = tracks[0]
        assert t.artist == "The Artist"
        assert t.album_artist == "The Artist"
        assert t.album == "Great Album"
        assert t.year == "2010"
        assert t.title == "Best Song"
        assert t.track_number == 5
        assert t.disc_number == 2
        assert t.ext == "mp3"

    def test_scan_parses_track_number_with_total(self, tmp_path: Path) -> None:
        """TRCK can be "5/12"; only the number part should be stored."""
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", track="5/12", disc="2/2")

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert tracks[0].track_number == 5
        assert tracks[0].disc_number == 2

    def test_scan_handles_missing_tags_gracefully(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "untagged.mp3").write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(lib / "untagged.mp3"))

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.added == 1
        assert tracks[0].title == ""
        assert tracks[0].artist == ""
        assert tracks[0].track_number == 0

    def test_scan_ignores_non_audio_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "cover.jpg").write_bytes(b"\xff\xd8\xff")
        (lib / "info.txt").write_text("notes")
        _make_mp3(lib / "01.mp3", title="Track")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        index.close()

        assert result.added == 1

    def test_scan_walks_subdirectories(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        subdir = lib / "Artist" / "2024 - Album"
        subdir.mkdir(parents=True)
        _make_mp3(subdir / "01.mp3", title="Nested Track")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        index.close()

        assert result.added == 1

    def test_scan_incremental_adds_only_new_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="Existing")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        _make_mp3(lib / "02.mp3", title="New")
        result = scanner.scan(lib)
        index.close()

        assert result.added == 1
        assert result.unchanged == 1

    def test_scan_removes_deleted_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        mp3 = lib / "01.mp3"
        _make_mp3(mp3, title="Gone")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)
        mp3.unlink()
        result = scanner.scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.removed == 1
        assert tracks == []

    def test_scan_reads_m4a_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.m4a").write_bytes(b"\x00" * 32)

        mock_audio = MagicMock()
        mock_audio.tags = {
            "\xa9ART": ["M4A Artist"],
            "aART": ["M4A Album Artist"],
            "\xa9alb": ["M4A Album"],
            "\xa9day": ["2023"],
            "\xa9nam": ["M4A Track"],
            "trkn": [(3, 10)],
            "disk": [(1, 1)],
        }

        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        t = tracks[0]
        assert t.artist == "M4A Artist"
        assert t.album_artist == "M4A Album Artist"
        assert t.album == "M4A Album"
        assert t.year == "2023"
        assert t.title == "M4A Track"
        assert t.track_number == 3
        assert t.ext == "m4a"

    def test_scan_reads_flac_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.flac").write_bytes(b"fLaC")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "ARTIST": ["FLAC Artist"],
            "ALBUMARTIST": ["FLAC Album Artist"],
            "ALBUM": ["FLAC Album"],
            "DATE": ["2022"],
            "TITLE": ["FLAC Track"],
            "TRACKNUMBER": ["7"],
            "DISCNUMBER": ["2"],
            "MUSICBRAINZ_ALBUMID": ["mbid-flac"],
        }
        mock_audio.pictures = []

        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        t = tracks[0]
        assert t.artist == "FLAC Artist"
        assert t.album_artist == "FLAC Album Artist"
        assert t.album == "FLAC Album"
        assert t.year == "2022"
        assert t.title == "FLAC Track"
        assert t.track_number == 7
        assert t.disc_number == 2
        assert t.mb_release_id == "mbid-flac"
        assert t.ext == "flac"

    def test_scan_reads_ogg_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.ogg").write_bytes(b"OggS")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "ARTIST": ["OGG Artist"],
            "ALBUMARTIST": ["OGG Album Artist"],
            "ALBUM": ["OGG Album"],
            "DATE": ["2021"],
            "TITLE": ["OGG Track"],
            "TRACKNUMBER": ["4"],
            "DISCNUMBER": ["1"],
        }
        mock_audio.pictures = []

        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis", return_value=mock_audio
        ):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        assert len(tracks) == 1
        assert tracks[0].artist == "OGG Artist"
        assert tracks[0].ext == "ogg"

    def test_scan_nonexistent_directory(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(tmp_path / "does_not_exist")
        index.close()

        assert result == ScanResult(added=0, removed=0, unchanged=0)

    def test_scan_skips_unreadable_file(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "bad.mp3")

        with patch("kamp_core.library._read_tags", return_value=None):
            index = LibraryIndex(tmp_path / "library.db")
            result = LibraryScanner(index).scan(lib)
            index.close()

        assert result.added == 0

    def test_scan_calls_on_progress_for_each_new_file(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")
        _make_mp3(lib / "02.mp3")
        _make_mp3(lib / "03.mp3")

        calls: list[tuple[int, int]] = []
        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib, on_progress=lambda c, t: calls.append((c, t)))
        index.close()

        # One call per new file; total is always 3.
        assert len(calls) == 3
        assert all(total == 3 for _, total in calls)
        assert sorted(current for current, _ in calls) == [1, 2, 3]

    def test_scan_on_progress_not_called_when_no_new_files(
        self, tmp_path: Path
    ) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")

        index = LibraryIndex(tmp_path / "library.db")
        # First scan indexes the file.
        LibraryScanner(index).scan(lib)
        # Second scan: nothing new — callback must not be called.
        calls: list[tuple[int, int]] = []
        LibraryScanner(index).scan(lib, on_progress=lambda c, t: calls.append((c, t)))
        index.close()

        assert calls == []

    def test_scan_without_on_progress_still_works(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)  # no on_progress arg
        index.close()

        assert result.added == 1


# ---------------------------------------------------------------------------
# Tag reader helpers
# ---------------------------------------------------------------------------


class TestTagReaders:
    def test_read_mp3_tags_falls_back_on_no_id3_header(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "no_id3.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)  # MPEG bytes, no ID3 header
        track = _read_mp3_tags(mp3)
        assert track.title == ""
        assert track.ext == "mp3"

    def test_read_m4a_tags_falls_back_on_parse_error(self, tmp_path: Path) -> None:
        m4a = tmp_path / "bad.m4a"
        m4a.write_bytes(b"\x00" * 8)
        with patch("kamp_core.library.mutagen.mp4.MP4", side_effect=Exception("bad")):
            track = _read_m4a_tags(m4a)
        assert track.title == ""
        assert track.ext == "m4a"

    def test_read_vorbis_tags_falls_back_on_parse_error(self, tmp_path: Path) -> None:
        ogg = tmp_path / "bad.ogg"
        ogg.write_bytes(b"\x00" * 8)
        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis",
            side_effect=Exception("bad"),
        ):
            track = _read_vorbis_tags(ogg, is_flac=False)
        assert track.title == ""
        assert track.ext == "ogg"

    def test_read_tags_logs_and_returns_none_on_unexpected_error(
        self, tmp_path: Path
    ) -> None:
        mp3 = tmp_path / "boom.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        with patch(
            "kamp_core.library._read_mp3_tags", side_effect=RuntimeError("boom")
        ):
            result = _read_tags(mp3)
        assert result is None

    def test_read_tags_returns_none_for_unknown_extension(self, tmp_path: Path) -> None:
        wav = tmp_path / "file.wav"
        wav.write_bytes(b"")
        assert _read_tags(wav) is None


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------


class TestSearch:
    def _index_with_tracks(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        tracks = [
            Track(
                file_path=tmp_path / "01.mp3",
                title="Morning Bell",
                artist="Radiohead",
                album_artist="Radiohead",
                album="Kid A",
                year="2000",
                track_number=1,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
            Track(
                file_path=tmp_path / "02.mp3",
                title="Everything in Its Right Place",
                artist="Radiohead",
                album_artist="Radiohead",
                album="Kid A",
                year="2000",
                track_number=2,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
            Track(
                file_path=tmp_path / "03.mp3",
                title="Ocean",
                artist="Björk",
                album_artist="Björk",
                album="Homogenic",
                year="1997",
                track_number=1,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
        ]
        index.upsert_many(tracks)
        return index

    def test_empty_query_returns_no_results(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("")
        index.close()
        assert results == []

    def test_whitespace_only_query_returns_no_results(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("   ")
        index.close()
        assert results == []

    def test_match_by_artist(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("radiohead")
        index.close()
        assert all(t.album_artist == "Radiohead" for t in results)
        assert len(results) == 2

    def test_match_by_album(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("kid a")
        index.close()
        assert len(results) == 2

    def test_match_by_title(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("morning bell")
        index.close()
        assert len(results) == 1
        assert results[0].title == "Morning Bell"

    def test_prefix_match(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("radio")
        index.close()
        assert len(results) == 2

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("zzznomatch")
        index.close()
        assert results == []

    def test_removed_track_excluded_from_search(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        index.remove_track(tmp_path / "01.mp3")
        results = index.search("morning bell")
        index.close()
        assert results == []

    def test_v1_database_migrated_to_current(self, tmp_path: Path) -> None:
        """Existing v1 databases are fully migrated (FTS + date columns) on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a v1-style database without the FTS table.
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (1)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT ''
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/a.mp3', 'Title', 'ArtistA', 'ArtistA', "
            "'RecordA', '2000', 1, 1, 'mp3', 0, '', '')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        # Opening with LibraryIndex should migrate v1 → current.
        index = LibraryIndex(db_path)
        results = index.search("ArtistA")
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert version == 6
        assert len(results) == 1
        assert results[0].title == "Title"

    def test_v2_database_migrated_to_v3(self, tmp_path: Path) -> None:
        """Existing v2 databases gain date_added and last_played columns on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a v2-style database (has FTS but no date columns).
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT ''
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/b.mp3', 'Song', 'BandB', 'BandB', "
            "'AlbumB', '2010', 1, 1, 'mp3', 0, '', '')"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, content=tracks, content_rowid=id)"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        # Opening with LibraryIndex should migrate v2 → v4.
        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        # date_added and last_played columns must exist (no exception on select).
        row = index._conn.execute(
            "SELECT date_added, last_played FROM tracks WHERE id = 1"
        ).fetchone()
        index.close()

        assert version == 6
        assert row is not None
        # date_added will be NULL since the file path is fake; that is expected.
        assert row[0] is None
        assert row[1] is None


# ---------------------------------------------------------------------------
# Sort and record_played
# ---------------------------------------------------------------------------


class TestAlbumsSort:
    """Tests for LibraryIndex.albums(sort=...) ordering."""

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        """Return an index pre-populated with three albums by two artists."""
        index = LibraryIndex(tmp_path / "library.db")
        # Use real files so date_added is populated from ctime.
        p1 = tmp_path / "a.mp3"
        p2 = tmp_path / "b.mp3"
        p3 = tmp_path / "c.mp3"
        _make_mp3(
            p1,
            artist="Zappa",
            album_artist="Zappa",
            album="Hot Rats",
            year="1969",
            title="T1",
        )
        _make_mp3(
            p2,
            artist="Amon Tobin",
            album_artist="Amon Tobin",
            album="Foley Room",
            year="2007",
            title="T2",
        )
        _make_mp3(
            p3,
            artist="Zappa",
            album_artist="Zappa",
            album="Apostrophe",
            year="1974",
            title="T3",
        )
        index.upsert_many(
            [
                _sample_track(p1).__class__(
                    file_path=p1,
                    title="T1",
                    artist="Zappa",
                    album_artist="Zappa",
                    album="Hot Rats",
                    year="1969",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    date_added=1000.0,
                ),
                _sample_track(p2).__class__(
                    file_path=p2,
                    title="T2",
                    artist="Amon Tobin",
                    album_artist="Amon Tobin",
                    album="Foley Room",
                    year="2007",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    date_added=3000.0,
                ),
                _sample_track(p3).__class__(
                    file_path=p3,
                    title="T3",
                    artist="Zappa",
                    album_artist="Zappa",
                    album="Apostrophe",
                    year="1974",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    date_added=2000.0,
                ),
            ]
        )
        return index

    def test_default_sort_is_album_artist(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums()
        index.close()
        assert albums[0].album_artist == "Amon Tobin"
        assert albums[1].album_artist == "Zappa"

    def test_sort_by_album(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums(sort="album")
        index.close()
        names = [a.album for a in albums]
        assert names == ["Apostrophe", "Foley Room", "Hot Rats"]

    def test_sort_by_date_added_newest_first(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums(sort="date_added")
        index.close()
        # Foley Room: date_added=3000 → first
        assert albums[0].album == "Foley Room"
        # Apostrophe: date_added=2000 → second
        assert albums[1].album == "Apostrophe"
        # Hot Rats: date_added=1000 → last
        assert albums[2].album == "Hot Rats"

    def test_sort_by_last_played_newest_first(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        # Only record play for one album; it should sort to the top.
        p3 = tmp_path / "c.mp3"
        index.record_played(p3)  # Apostrophe played most recently
        albums = index.albums(sort="last_played")
        index.close()
        assert albums[0].album == "Apostrophe"

    def test_unknown_sort_key_falls_back_to_album_artist(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums(sort="bogus")
        index.close()
        assert albums[0].album_artist == "Amon Tobin"


class TestRecordPlayed:
    """Tests for LibraryIndex.record_played()."""

    def test_sets_last_played_timestamp(self, tmp_path: Path) -> None:
        import time

        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    year="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )

        before = time.time()
        index.record_played(p)
        after = time.time()

        row = index._conn.execute(
            "SELECT last_played FROM tracks WHERE file_path = ?", (str(p),)
        ).fetchone()
        index.close()

        assert row is not None
        assert before <= row[0] <= after

    def test_record_played_unknown_path_is_noop(self, tmp_path: Path) -> None:
        """Calling record_played for a path not in the index must not raise."""
        index = LibraryIndex(tmp_path / "library.db")
        index.record_played(tmp_path / "ghost.mp3")  # should not raise
        index.close()

    def test_play_count_defaults_to_zero(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    year="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.play_count == 0

    def test_record_played_increments_play_count(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    year="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        index.record_played(p)
        index.record_played(p)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.play_count == 2

    def test_migration_v4_to_v5_adds_play_count_column(self, tmp_path: Path) -> None:
        """Existing v4 databases gain the play_count column on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (4)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/d.mp3', 'Song', 'Band', 'Band', "
            "'Album', '2000', 1, 1, 'mp3', 0, '', '', NULL, NULL, 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, tokenize='unicode61')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        row = index._conn.execute(
            "SELECT play_count FROM tracks WHERE id = 1"
        ).fetchone()
        index.close()

        assert version == 6
        assert row is not None
        assert row[0] == 0


# ---------------------------------------------------------------------------
# Favorite
# ---------------------------------------------------------------------------


class TestFavorite:
    """Tests for LibraryIndex.set_favorite() and Track.favorite persistence."""

    def _make_index_with_track(self, tmp_path: Path) -> tuple[LibraryIndex, Path]:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    year="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        return index, p

    def test_favorite_defaults_to_false(self, tmp_path: Path) -> None:
        index, p = self._make_index_with_track(tmp_path)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.favorite is False

    def test_set_favorite_marks_track(self, tmp_path: Path) -> None:
        index, p = self._make_index_with_track(tmp_path)
        index.set_favorite(p, True)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.favorite is True

    def test_set_favorite_clears_flag(self, tmp_path: Path) -> None:
        index, p = self._make_index_with_track(tmp_path)
        index.set_favorite(p, True)
        index.set_favorite(p, False)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.favorite is False

    def test_migration_v3_to_v4_adds_favorite_column(self, tmp_path: Path) -> None:
        """Existing v3 databases gain the favorite column on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a v3-style database (has date columns but no favorite).
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (3)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/c.mp3', 'Song', 'Band', 'Band', "
            "'Album', '2000', 1, 1, 'mp3', 0, '', '', NULL, NULL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, tokenize='unicode61')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        # Opening with LibraryIndex should migrate v3 → v4.
        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        row = index._conn.execute("SELECT favorite FROM tracks WHERE id = 1").fetchone()
        index.close()

        assert version == 6
        assert row is not None
        assert row[0] == 0  # existing tracks default to not-favorited


# ---------------------------------------------------------------------------
# Mtime-based re-indexing (TASK-66)
# ---------------------------------------------------------------------------


class TestMtimeReindex:
    """Tests for LibraryScanner mtime change detection."""

    def test_scan_stores_file_mtime_on_first_index(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="T")

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert tracks[0].file_mtime == pytest.approx(p.stat().st_mtime, abs=1.0)

    def test_scan_reindexes_file_when_mtime_changes(self, tmp_path: Path) -> None:
        """Updating a file's mtime causes re-read on next scan."""
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="Original")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        # Rewrite the file with a new title and bump mtime.
        _make_mp3(p, title="Updated")
        import os

        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 1))

        result = scanner.scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.updated == 1
        assert result.added == 0
        assert result.unchanged == 0
        assert tracks[0].title == "Updated"

    def test_scan_unchanged_count_excludes_updated_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        p1 = lib / "01.mp3"
        p2 = lib / "02.mp3"
        _make_mp3(p1, title="T1")
        _make_mp3(p2, title="T2")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        # Bump mtime on one file.
        import os

        os.utime(p1, (p1.stat().st_atime, p1.stat().st_mtime + 1))

        result = scanner.scan(lib)
        index.close()

        assert result.updated == 1
        assert result.unchanged == 1

    def test_scan_updates_stored_mtime_after_reindex(self, tmp_path: Path) -> None:
        """After re-indexing a changed file, its stored mtime matches the new value."""
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="T")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        import os

        new_mtime = p.stat().st_mtime + 1
        os.utime(p, (p.stat().st_atime, new_mtime))
        scanner.scan(lib)

        tracks = index.all_tracks()
        index.close()

        assert tracks[0].file_mtime == pytest.approx(new_mtime, abs=0.001)

    def test_null_mtime_in_db_causes_reindex_on_scan(self, tmp_path: Path) -> None:
        """Tracks with NULL file_mtime (e.g. after v5→v6 migration) are always
        re-read on the next scan so tag changes made before the upgrade are
        picked up automatically."""
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="Original")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        # Simulate the state right after a v5→v6 migration: file_mtime is NULL.
        index._conn.execute(
            "UPDATE tracks SET file_mtime = NULL WHERE file_path = ?", (str(p),)
        )
        index._conn.commit()

        # Also update the file tags to simulate an edit made before the migration.
        _make_mp3(p, title="Updated")

        result = scanner.scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.updated == 1
        assert tracks[0].title == "Updated"
        assert tracks[0].file_mtime is not None  # mtime stored after re-read

    def test_migration_v5_to_v6_adds_file_mtime_column(self, tmp_path: Path) -> None:
        """Existing v5 databases gain the file_mtime column on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (5)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/e.mp3', 'Song', 'Band', 'Band', "
            "'Album', '2000', 1, 1, 'mp3', 0, '', '', NULL, NULL, 0, 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, tokenize='unicode61')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        row = index._conn.execute(
            "SELECT file_mtime FROM tracks WHERE id = 1"
        ).fetchone()
        index.close()

        assert version == 6
        assert row is not None
        # file_mtime is intentionally left NULL on migration so the next scan
        # treats all existing tracks as changed and re-reads their tags.
        assert row[0] is None
