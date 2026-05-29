"""Tests for KAMP-308: album-level tag fan-out and rename endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import mutagen.id3 as id3
import pytest
from fastapi.testclient import TestClient

from kamp_core.library import LibraryIndex, Track, write_album_tags_to_file
from kamp_core.playback import PlaybackState
from kamp_core.server import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mp3(path: Path, **tags: str) -> None:
    t = id3.ID3()
    if "album_artist" in tags:
        t["TPE2"] = id3.TPE2(encoding=3, text=tags["album_artist"])
    if "album" in tags:
        t["TALB"] = id3.TALB(encoding=3, text=tags["album"])
    if "year" in tags:
        t["TDRC"] = id3.TDRC(encoding=3, text=tags["year"])
    if "track" in tags:
        t["TRCK"] = id3.TRCK(encoding=3, text=tags["track"])
    if "title" in tags:
        t["TIT2"] = id3.TIT2(encoding=3, text=tags["title"])
    path.write_bytes(b"\xff\xfb" * 64)
    t.save(str(path))


def _sample_track(file_path: Path, **overrides: object) -> Track:
    defaults: dict[str, object] = dict(
        file_path=file_path,
        title="Track Title",
        artist="Artist",
        album_artist="Artist",
        album="Old Album",
        year="2024",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="rel-1",
        mb_recording_id="rec-1",
    )
    defaults.update(overrides)
    return Track(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# write_album_tags_to_file
# ---------------------------------------------------------------------------


class TestWriteAlbumTagsToFile:
    def test_updates_mp3_album_and_artist(self, tmp_path: Path) -> None:
        f = tmp_path / "track.mp3"
        _make_mp3(f, album="Old", album_artist="Old Artist")
        write_album_tags_to_file(f, "New Album", "New Artist")
        tags = id3.ID3(str(f))
        assert str(tags["TALB"]) == "New Album"
        assert str(tags["TPE2"]) == "New Artist"

    def test_updates_per_track_artist_when_provided(self, tmp_path: Path) -> None:
        f = tmp_path / "track.mp3"
        _make_mp3(f, album="Migjorn", album_artist="Docks")
        # Simulate TPE1 already set
        tags = id3.ID3(str(f))
        tags["TPE1"] = id3.TPE1(encoding=3, text="Docks")
        tags.save(str(f))

        write_album_tags_to_file(
            f, "Migjorn", "Activity Monitor", artist="Activity Monitor"
        )

        tags = id3.ID3(str(f))
        assert str(tags["TPE1"]) == "Activity Monitor"
        assert str(tags["TPE2"]) == "Activity Monitor"
        assert str(tags["TALB"]) == "Migjorn"

    def test_updates_flac_album_artist_and_per_track_artist(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "track.flac"
        f.write_bytes(b"fLaC")
        mock_audio = MagicMock()
        # tags=None exercises the add_tags() branch; after the call it becomes a dict.
        mock_audio.tags = None

        def _add_tags() -> None:
            mock_audio.tags = {}

        mock_audio.add_tags.side_effect = _add_tags
        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            # With artist — True branch.
            write_album_tags_to_file(f, "New Album", "New Artist", artist="New Artist")
            assert mock_audio.tags["ARTIST"] == ["New Artist"]
            # Without artist — False branch.
            mock_audio.tags = {}
            write_album_tags_to_file(f, "New Album", "New Artist")
            assert "ARTIST" not in mock_audio.tags

        assert mock_audio.tags["ALBUM"] == ["New Album"]
        assert mock_audio.tags["ALBUMARTIST"] == ["New Artist"]
        assert mock_audio.save.call_count == 2

    def test_updates_m4a_album_artist_and_per_track_artist(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "track.m4a"
        f.write_bytes(b"\x00" * 32)
        mock_audio = MagicMock()
        # tags=None exercises the add_tags() branch; after the call it becomes a dict.
        mock_audio.tags = None

        def _add_tags() -> None:
            mock_audio.tags = {}

        mock_audio.add_tags.side_effect = _add_tags
        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            # With artist — True branch.
            write_album_tags_to_file(f, "New Album", "New Artist", artist="New Artist")
            assert mock_audio.tags["\xa9ART"] == ["New Artist"]
            # Without artist — False branch.
            mock_audio.tags = {}
            write_album_tags_to_file(f, "New Album", "New Artist")
            assert "\xa9ART" not in mock_audio.tags

        assert mock_audio.tags["\xa9alb"] == ["New Album"]
        assert mock_audio.tags["aART"] == ["New Artist"]
        assert mock_audio.save.call_count == 2

    def test_updates_ogg_album_artist_and_per_track_artist(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "track.ogg"
        f.write_bytes(b"OggS")
        mock_audio = MagicMock()
        mock_audio.tags = None

        def _add_tags() -> None:
            mock_audio.tags = {}

        mock_audio.add_tags.side_effect = _add_tags
        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis", return_value=mock_audio
        ):
            # With artist — True branch.
            write_album_tags_to_file(f, "New Album", "New Artist", artist="New Artist")
            assert mock_audio.tags["ARTIST"] == ["New Artist"]
            # Without artist — False branch.
            mock_audio.tags = {}
            write_album_tags_to_file(f, "New Album", "New Artist")
            assert "ARTIST" not in mock_audio.tags

        assert mock_audio.tags["ALBUM"] == ["New Album"]
        assert mock_audio.tags["ALBUMARTIST"] == ["New Artist"]
        assert mock_audio.save.call_count == 2

    def test_raises_on_unsupported_format(self, tmp_path: Path) -> None:
        f = tmp_path / "track.wav"
        f.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        with pytest.raises(ValueError, match="Unsupported"):
            write_album_tags_to_file(f, "Album", "Artist")


# ---------------------------------------------------------------------------
# LibraryIndex.rename_album_track
# ---------------------------------------------------------------------------


class TestRenameAlbumTrack:
    def test_updates_path_album_and_album_artist(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "db.sqlite")
        old_path = tmp_path / "old.mp3"
        new_path = tmp_path / "new.mp3"
        track = _sample_track(old_path)
        index.upsert_track(track)
        original = index.get_track_by_path(old_path)
        assert original is not None

        index.rename_album_track(old_path, new_path, "New Album", "New Artist", 9999.0)

        assert index.get_track_by_path(old_path) is None
        updated = index.get_track_by_path(new_path)
        assert updated is not None
        assert updated.album == "New Album"
        assert updated.album_artist == "New Artist"
        assert updated.file_mtime == 9999.0
        # Stats preserved.
        assert updated.id == original.id
        assert updated.mb_recording_id == original.mb_recording_id
        index.close()

    def test_preserves_play_stats(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "db.sqlite")
        p = tmp_path / "track.mp3"
        track = _sample_track(p)
        index.upsert_track(track)
        index.set_favorite(p, True)
        index.record_played(p)
        index.record_track_started(p)

        new_p = tmp_path / "moved.mp3"
        index.rename_album_track(p, new_p, "New Album", "New Artist", 1.0)

        updated = index.get_track_by_path(new_p)
        assert updated is not None
        assert updated.favorite is True
        assert updated.play_count == 1
        assert updated.last_played is not None
        index.close()


# ---------------------------------------------------------------------------
# Server endpoint: PATCH /api/v1/albums/tags
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.state = PlaybackState()
    return engine


@pytest.fixture()
def _mock_queue() -> MagicMock:
    queue = MagicMock()
    queue.current.return_value = None
    queue.peek_next.return_value = None
    queue.queue_tracks.return_value = ([], -1)
    queue.set_shuffle = MagicMock()
    queue.set_repeat = MagicMock()
    return queue


def _make_album(
    tmp_path: Path,
    album_artist: str,
    album: str,
    year: str,
    track_count: int,
) -> list[tuple[Path, Track]]:
    """Create MP3 files + Track objects for a test album."""
    folder = tmp_path / album_artist / f"{year} - {album}"
    folder.mkdir(parents=True)
    result = []
    for i in range(1, track_count + 1):
        title = f"Track {i:02d}"
        mp3 = folder / f"{i:02d} - {title}.mp3"
        _make_mp3(
            mp3,
            album_artist=album_artist,
            album=album,
            year=year,
            track=str(i),
            title=title,
        )
        track = _sample_track(
            mp3,
            title=title,
            album_artist=album_artist,
            album=album,
            year=year,
            track_number=i,
            mb_recording_id=f"rec-{i}",
        )
        result.append((mp3, track))
    return result


class TestPatchAlbumTagsEndpoint:
    def _client_with_album(
        self,
        tmp_path: Path,
        tracks: list[Track],
        engine: MagicMock,
        queue: MagicMock,
        config_values: dict | None = None,
    ) -> tuple[TestClient, LibraryIndex]:
        db = LibraryIndex(tmp_path / "db.sqlite")
        for track in tracks:
            db.upsert_track(track)
        app = create_app(
            index=db,
            engine=engine,
            queue=queue,
            library_path=tmp_path,
            config_values=config_values,
        )
        return TestClient(app, raise_server_exceptions=False), db

    def test_rename_album_moves_all_files(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Old Album", "2024", 3)
        track_objects = [t for _, t in pairs]
        client, db = self._client_with_album(
            tmp_path, track_objects, _mock_engine, _mock_queue
        )

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Old Album"},
            json={"album": "New Album"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["moved"]) == 3
        assert body["skipped"] == []
        assert body["failed"] == []

        # All DB rows now show the new album name.
        rows = db.tracks_for_album("Artist", "New Album")
        assert len(rows) == 3
        for row in rows:
            assert row.album == "New Album"
            # Old album no longer in DB.
        assert db.tracks_for_album("Artist", "Old Album") == []

        # Files exist at new locations.
        new_folder = tmp_path / "Artist" / "2024 - New Album"
        assert new_folder.is_dir()
        assert len(list(new_folder.glob("*.mp3"))) == 3

    def test_tag_only_path_updates_db_and_fts(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """When the path template has no album/artist dir, files stay put (tag-only path)."""
        # Flat template: all files land directly in tmp_path regardless of album/artist.
        flat_template = "{track:02d} - {title}.{ext}"
        pairs = _make_album(tmp_path, "Artist", "Old Album", "2024", 2)
        # Move files to flat layout so they match the template.
        flat_files = []
        for mp3, track in pairs:
            flat = tmp_path / mp3.name
            mp3.rename(flat)
            flat_files.append((flat, track))
        tracks = [
            _sample_track(
                flat,
                title=t.title,
                album_artist="Artist",
                album="Old Album",
                year="2024",
                track_number=t.track_number,
            )
            for flat, t in flat_files
        ]

        client, db = self._client_with_album(
            tmp_path,
            tracks,
            _mock_engine,
            _mock_queue,
            config_values={"library.path_template": flat_template},
        )
        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Old Album"},
            json={"album": "New Album"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["failed"] == []

        # Files are unchanged on disk (same location).
        for flat, _ in flat_files:
            assert flat.exists()

        # DB and FTS reflect the new album name.
        rows = db.tracks_for_album("Artist", "New Album")
        assert len(rows) == 2
        assert db.tracks_for_album("Artist", "Old Album") == []
        assert len(db.search("New Album")) == 2
        assert db.search("Old Album") == []

    def test_tag_only_path_updates_artist_tag_and_fts(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """Tag-only path also updates per-track artist when artist == old album_artist."""
        flat_template = "{track:02d} - {title}.{ext}"
        pairs = _make_album(tmp_path, "Docks", "Migjorn", "2020", 2)
        flat_files = []
        for mp3, track in pairs:
            # Set TPE1 == album_artist on each file.
            tags = id3.ID3(str(mp3))
            tags["TPE1"] = id3.TPE1(encoding=3, text="Docks")
            tags.save(str(mp3))
            flat = tmp_path / mp3.name
            mp3.rename(flat)
            flat_files.append((flat, track))
        tracks = [
            _sample_track(
                flat,
                artist="Docks",
                album_artist="Docks",
                album="Migjorn",
                year="2020",
                title=t.title,
                track_number=t.track_number,
            )
            for flat, t in flat_files
        ]

        client, db = self._client_with_album(
            tmp_path,
            tracks,
            _mock_engine,
            _mock_queue,
            config_values={"library.path_template": flat_template},
        )
        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Docks", "album": "Migjorn"},
            json={"album_artist": "Activity Monitor"},
        )
        assert resp.status_code == 200

        # DB artist column updated.
        for flat, _ in flat_files:
            row = db.get_track_by_path(flat)
            assert row is not None
            assert row.artist == "Activity Monitor"

        # FTS updated.
        assert db.search("Activity Monitor") != []
        assert db.search("Docks") == []

        # File tag updated.
        for flat, _ in flat_files:
            file_tags = id3.ID3(str(flat))
            assert str(file_tags["TPE1"]) == "Activity Monitor"

    def test_rename_album_artist_restructures_folders(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Old Artist", "Album", "2024", 2)
        track_objects = [t for _, t in pairs]
        client, db = self._client_with_album(
            tmp_path, track_objects, _mock_engine, _mock_queue
        )

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Old Artist", "album": "Album"},
            json={"album_artist": "New Artist"},
        )

        assert resp.status_code == 200, resp.text
        rows = db.tracks_for_album("New Artist", "Album")
        assert len(rows) == 2
        for row in rows:
            assert row.album_artist == "New Artist"
        new_folder = tmp_path / "New Artist" / "2024 - Album"
        assert new_folder.is_dir()

    def test_stats_preserved_across_all_tracks(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 2)
        track_objects = [t for _, t in pairs]
        client, db = self._client_with_album(
            tmp_path, track_objects, _mock_engine, _mock_queue
        )
        # Set stats before rename.
        for _, track in pairs:
            db.set_favorite(track.file_path, True)
            db.record_played(track.file_path)
        original_ids = {db.get_track_by_path(t.file_path).id for _, t in pairs}  # type: ignore[union-attr]

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={"album": "Renamed Album"},
        )
        assert resp.status_code == 200

        rows = db.tracks_for_album("Artist", "Renamed Album")
        assert len(rows) == 2
        for row in rows:
            assert row.favorite is True
            assert row.play_count == 1
            assert row.id in original_ids

    def test_returns_404_for_unknown_album(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        app = create_app(
            index=db, engine=_mock_engine, queue=_mock_queue, library_path=tmp_path
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Nobody", "album": "Nothing"},
            json={"album": "New"},
        )
        assert resp.status_code == 404

    def test_returns_400_when_no_changes(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 1)
        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={},
        )
        assert resp.status_code == 400

    def test_returns_409_on_collision_without_overwrite(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 2)
        track_objects = [t for _, t in pairs]
        client, db = self._client_with_album(
            tmp_path, track_objects, _mock_engine, _mock_queue
        )

        # Create a file at the destination of the first track to force a collision.
        colliding_dir = tmp_path / "Artist" / "2024 - New Album"
        colliding_dir.mkdir(parents=True)
        (colliding_dir / "01 - Track 01.mp3").write_bytes(b"\xff\xfb" * 4)

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={"album": "New Album"},
        )

        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["collision_count"] == 1
        # No files were moved — pre-flight stops before any moves.
        assert db.tracks_for_album("Artist", "Album")  # old rows still there

    def test_overwrite_replaces_colliding_files(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 1)
        client, db = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        # Place a different track at the new destination so there's a collision.
        dest_dir = tmp_path / "Artist" / "2024 - New Album"
        dest_dir.mkdir(parents=True)
        (dest_dir / "01 - Track 01.mp3").write_bytes(b"\xff\xfb" * 4)

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={"album": "New Album", "overwrite": True},
        )

        assert resp.status_code == 200, resp.text
        assert len(resp.json()["moved"]) == 1

    def test_skip_conflicts_leaves_colliding_file_and_renames_rest(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 2)
        client, db = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        # Collision on the first track only.
        dest_dir = tmp_path / "Artist" / "2024 - New Album"
        dest_dir.mkdir(parents=True)
        (dest_dir / "01 - Track 01.mp3").write_bytes(b"\xff\xfb" * 4)

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={"album": "New Album", "skip_conflicts": True},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # One moved, one skipped.
        assert len(body["moved"]) == 1
        assert len(body["skipped"]) == 1

    def test_returns_503_when_library_path_not_configured(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 1)
        db = LibraryIndex(tmp_path / "db.sqlite")
        for _, t in pairs:
            db.upsert_track(t)
        app = create_app(index=db, engine=_mock_engine, queue=_mock_queue)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={"album": "New"},
        )
        assert resp.status_code == 503

    def test_tag_write_failure_in_tag_only_path_reported_in_failed(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """Tag-write exceptions in the same-dir path populate failed[], not moved[]."""
        flat_template = "{track:02d} - {title}.{ext}"
        pairs = _make_album(tmp_path, "Artist", "Old Album", "2024", 1)
        mp3, track = pairs[0]
        flat = tmp_path / mp3.name
        mp3.rename(flat)
        tracks = [
            _sample_track(
                flat,
                title=track.title,
                album_artist="Artist",
                album="Old Album",
                year="2024",
                track_number=track.track_number,
            )
        ]
        client, _ = self._client_with_album(
            tmp_path,
            tracks,
            _mock_engine,
            _mock_queue,
            config_values={"library.path_template": flat_template},
        )
        with patch(
            "kamp_core.library.write_album_tags_to_file",
            side_effect=OSError("disk full"),
        ):
            resp = client.patch(
                "/api/v1/albums/tags",
                params={"album_artist": "Artist", "album": "Old Album"},
                json={"album": "New Album"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["failed"]) == 1
        assert body["moved"] == []
        assert "disk full" in body["failed"][0]["error"]

    def test_tag_write_failure_in_atomic_rename_path_reported_in_failed(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """Tag-write exceptions after atomic dir rename populate failed[], not moved[]."""
        pairs = _make_album(tmp_path, "Artist", "Old Album", "2024", 1)
        tracks = [t for _, t in pairs]
        client, _ = self._client_with_album(tmp_path, tracks, _mock_engine, _mock_queue)
        with patch(
            "kamp_core.library.write_album_tags_to_file",
            side_effect=OSError("disk full"),
        ):
            resp = client.patch(
                "/api/v1/albums/tags",
                params={"album_artist": "Artist", "album": "Old Album"},
                json={"album": "New Album"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["failed"]) == 1
        assert body["moved"] == []
        assert "disk full" in body["failed"][0]["error"]

    def test_on_album_tracks_moved_called_once_with_all_pairs(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 3)
        db = LibraryIndex(tmp_path / "db.sqlite")
        for _, t in pairs:
            db.upsert_track(t)
        app = create_app(
            index=db, engine=_mock_engine, queue=_mock_queue, library_path=tmp_path
        )

        batch_calls: list[list[tuple[Path, Path]]] = []
        app.state.on_album_tracks_moved = lambda p: batch_calls.append(p)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={"album": "Renamed"},
        )
        assert resp.status_code == 200
        # Callback fires exactly once with all 3 pairs — not once-per-track
        assert len(batch_calls) == 1
        assert len(batch_calls[0]) == 3

    def test_empty_album_dir_removed_after_rename(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Artist", "Old Album", "2024", 2)
        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        old_album_dir = tmp_path / "Artist" / "2024 - Old Album"
        assert old_album_dir.is_dir()

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Old Album"},
            json={"album": "New Album"},
        )
        assert resp.status_code == 200
        # Old album directory should be gone.
        assert not old_album_dir.exists()
        # Artist directory stays — it now contains the new album folder.
        assert (tmp_path / "Artist").is_dir()

    def test_empty_artist_dir_removed_after_artist_rename(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        pairs = _make_album(tmp_path, "Old Artist", "Album", "2024", 2)
        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        old_artist_dir = tmp_path / "Old Artist"
        assert old_artist_dir.is_dir()

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Old Artist", "album": "Album"},
            json={"album_artist": "New Artist"},
        )
        assert resp.status_code == 200
        # Both the old album dir and the now-empty artist dir should be gone.
        assert not (old_artist_dir / "2024 - Album").exists()
        assert not old_artist_dir.exists()

    def test_non_empty_dir_not_removed(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """If a skipped file remains in the old dir, the dir is preserved."""
        pairs = _make_album(tmp_path, "Artist", "Album", "2024", 2)
        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        old_album_dir = tmp_path / "Artist" / "2024 - Album"
        # Place a collision at the destination of the first track.
        dest_dir = tmp_path / "Artist" / "2024 - New Album"
        dest_dir.mkdir(parents=True)
        (dest_dir / "01 - Track 01.mp3").write_bytes(b"\xff\xfb" * 4)

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Artist", "album": "Album"},
            json={"album": "New Album", "skip_conflicts": True},
        )
        assert resp.status_code == 200
        # Skipped file still lives in the old dir — dir must not be removed.
        assert old_album_dir.is_dir()

    def test_ds_store_in_old_dir_does_not_block_cleanup(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """macOS Finder drops .DS_Store in every visited directory; cleanup must remove it."""
        pairs = _make_album(tmp_path, "Old Artist", "Album", "2024", 2)
        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        # Simulate Finder leaving metadata in the album and artist directories.
        (tmp_path / "Old Artist" / "2024 - Album" / ".DS_Store").write_bytes(b"")
        (tmp_path / "Old Artist" / ".DS_Store").write_bytes(b"")

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Old Artist", "album": "Album"},
            json={"album_artist": "New Artist"},
        )
        assert resp.status_code == 200
        assert not (tmp_path / "Old Artist").exists()

    def test_queue_album_artist_updated_after_rename(
        self, tmp_path: Path, _mock_engine: MagicMock
    ) -> None:
        """Queued tracks must reflect the new album_artist after an album-level rename."""
        from kamp_core.playback import PlaybackQueue

        pairs = _make_album(tmp_path, "Old Artist", "Album", "2024", 2)
        tracks = [t for _, t in pairs]
        queue: PlaybackQueue = PlaybackQueue()
        queue.load(tracks, start_index=0)

        db = LibraryIndex(tmp_path / "db.sqlite")
        for t in tracks:
            db.upsert_track(t)
        app = create_app(
            index=db, engine=_mock_engine, queue=queue, library_path=tmp_path
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Old Artist", "album": "Album"},
            json={"album_artist": "New Artist"},
        )
        assert resp.status_code == 200
        queue_tracks, _ = queue.queue_tracks()
        assert all(t.album_artist == "New Artist" for t in queue_tracks)

    def test_single_track_album_artist_rename(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """Single-track albums must use directory rename, not per-file move."""
        pairs = _make_album(tmp_path, "Old Artist", "Album", "2024", 1)
        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Old Artist", "album": "Album"},
            json={"album_artist": "New Artist"},
        )
        assert resp.status_code == 200
        assert not (tmp_path / "Old Artist").exists()
        assert (tmp_path / "New Artist" / "2024 - Album" / "01 - Track 01.mp3").exists()

    def test_artist_rename_renames_artist_dir_directly(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """When the artist dir contains only this album, rename it in one step.

        The sequence must be: Artist A/ → Artist B/ atomically.
        No intermediate Artist B/ creation then Artist A/ deletion.
        """
        pairs = _make_album(tmp_path, "Old Artist", "Album", "2024", 2)
        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        old_artist_dir = tmp_path / "Old Artist"
        new_artist_dir = tmp_path / "New Artist"

        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Old Artist", "album": "Album"},
            json={"album_artist": "New Artist"},
        )
        assert resp.status_code == 200
        assert not old_artist_dir.exists()
        assert (new_artist_dir / "2024 - Album").is_dir()

    def test_artist_rename_with_other_albums_uses_album_dir_rename(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """When the artist has other albums, only the target album dir moves."""
        pairs = _make_album(tmp_path, "Old Artist", "Album A", "2024", 2)
        # Create a second album by Old Artist that is NOT being renamed.
        other_dir = tmp_path / "Old Artist" / "2024 - Album B"
        other_dir.mkdir(parents=True)
        (other_dir / "01 - Track.mp3").write_bytes(b"\xff\xfb" * 64)

        client, _ = self._client_with_album(
            tmp_path, [t for _, t in pairs], _mock_engine, _mock_queue
        )
        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Old Artist", "album": "Album A"},
            json={"album_artist": "New Artist"},
        )
        assert resp.status_code == 200
        # Old artist dir stays — it still has Album B.
        assert (tmp_path / "Old Artist" / "2024 - Album B").is_dir()
        # The renamed album is under the new artist.
        assert (tmp_path / "New Artist" / "2024 - Album A").is_dir()

    def test_artist_rename_updates_per_track_artist_and_fts(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """Renaming album_artist also updates TPE1/artist so FTS finds the new name."""
        pairs = _make_album(tmp_path, "Docks", "Migjorn", "2020", 2)
        # Give each track TPE1 == album_artist (single-artist album).
        for mp3, _ in pairs:
            tags = id3.ID3(str(mp3))
            tags["TPE1"] = id3.TPE1(encoding=3, text="Docks")
            tags.save(str(mp3))
        tracks = [
            _sample_track(
                mp3,
                artist="Docks",
                album_artist="Docks",
                album="Migjorn",
                year="2020",
                title=t.title,
                track_number=t.track_number,
            )
            for mp3, t in pairs
        ]

        client, db = self._client_with_album(
            tmp_path, tracks, _mock_engine, _mock_queue
        )
        resp = client.patch(
            "/api/v1/albums/tags",
            params={"album_artist": "Docks", "album": "Migjorn"},
            json={"album_artist": "Activity Monitor"},
        )
        assert resp.status_code == 200

        # DB artist column updated for tracks where artist == old album_artist.
        for mp3, _ in pairs:
            new_mp3 = tmp_path / "Activity Monitor" / "2020 - Migjorn" / mp3.name
            updated = db.get_track_by_path(new_mp3)
            assert updated is not None
            assert updated.artist == "Activity Monitor"

        # FTS: searching old name returns nothing; new name returns the tracks.
        assert db.search("Docks") == []
        results = db.search("Activity Monitor")
        assert len(results) == 2

        # File tag also updated.
        for _, track in pairs:
            new_mp3 = (
                tmp_path / "Activity Monitor" / "2020 - Migjorn" / track.file_path.name
            )
            file_tags = id3.ID3(str(new_mp3))
            assert str(file_tags["TPE1"]) == "Activity Monitor"
