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

    def test_migration_version_is_1(self, tmp_path: Path) -> None:
        LibraryIndex(tmp_path / "library.db").close()

        conn = sqlite3.connect(str(tmp_path / "library.db"))
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        conn.close()

        assert version == 1

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
