"""Tests for KAMP-307: tag-edit file rename, path rendering, and server endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import mutagen.id3 as id3
import pytest
from fastapi.testclient import TestClient

from kamp_core.library import LibraryIndex, Track, write_title_to_file
from kamp_core.path_utils import (
    make_path_vars,
    render_destination,
    sanitize_path_component,
)
from kamp_core.playback import PlaybackState
from kamp_core.server import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMPLATE = "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"


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
        title="Old Title",
        artist="Artist",
        album_artist="Artist",
        album="Album",
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
# path_utils
# ---------------------------------------------------------------------------


class TestSanitizePathComponent:
    def test_replaces_slash(self) -> None:
        assert sanitize_path_component("a/b") == "a_b"

    def test_replaces_colon(self) -> None:
        assert sanitize_path_component("a:b") == "a_b"

    def test_strips_trailing_dots(self) -> None:
        assert sanitize_path_component("foo.") == "foo"

    def test_strips_trailing_spaces(self) -> None:
        assert sanitize_path_component("foo  ") == "foo"

    def test_passthrough_normal_name(self) -> None:
        assert sanitize_path_component("My Song (Remix)") == "My Song (Remix)"


class TestRenderDestination:
    def test_renders_standard_template(self, tmp_path: Path) -> None:
        tags = make_path_vars("Art", "Art", "Alb", "2024", 3, 1, "Title", "mp3")
        result = render_destination(tags, tmp_path, _TEMPLATE)
        assert result == tmp_path / "Art" / "2024 - Alb" / "03 - Title.mp3"

    def test_sanitizes_unsafe_chars_in_title(self, tmp_path: Path) -> None:
        tags = make_path_vars("Art", "Art", "Alb", "2024", 1, 1, "A/B:C", "mp3")
        result = render_destination(tags, tmp_path, _TEMPLATE)
        assert "/" not in result.name
        assert ":" not in result.name

    def test_raises_on_bad_template(self, tmp_path: Path) -> None:
        tags = make_path_vars("Art", "Art", "Alb", "2024", 1, 1, "Title", "mp3")
        with pytest.raises(ValueError):
            render_destination(tags, tmp_path, "{nonexistent_key}")

    def test_case_difference_produces_different_path(self, tmp_path: Path) -> None:
        tags_lower = make_path_vars("Art", "Art", "Alb", "2024", 1, 1, "norway", "mp3")
        tags_upper = make_path_vars("Art", "Art", "Alb", "2024", 1, 1, "Norway", "mp3")
        p_lower = render_destination(tags_lower, tmp_path, _TEMPLATE)
        p_upper = render_destination(tags_upper, tmp_path, _TEMPLATE)
        assert p_lower != p_upper


# ---------------------------------------------------------------------------
# write_title_to_file
# ---------------------------------------------------------------------------


class TestWriteTitleToFile:
    def test_updates_mp3_title_tag(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "track.mp3"
        _make_mp3(mp3, title="Old Title")

        write_title_to_file(mp3, "New Title")

        tags = id3.ID3(str(mp3))
        assert str(tags["TIT2"]) == "New Title"

    def test_raises_on_unsupported_format(self, tmp_path: Path) -> None:
        f = tmp_path / "track.wav"
        f.write_bytes(b"\x00" * 16)
        with pytest.raises(ValueError):
            write_title_to_file(f, "Title")


# ---------------------------------------------------------------------------
# LibraryIndex.get_track_by_id / move_track
# ---------------------------------------------------------------------------


class TestLibraryIndexTagEdit:
    def test_get_track_by_id_returns_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(tmp_path / "01.mp3")
        index.upsert_track(track)
        row = index.get_track_by_path(tmp_path / "01.mp3")
        assert row is not None

        fetched = index.get_track_by_id(row.id)
        assert fetched is not None
        assert fetched.title == "Old Title"
        index.close()

    def test_get_track_by_id_returns_none_for_missing(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "db.sqlite")
        assert index.get_track_by_id(99999) is None
        index.close()

    def test_move_track_updates_path_and_title(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "db.sqlite")
        old_path = tmp_path / "01.mp3"
        new_path = tmp_path / "02 - New Title.mp3"
        track = _sample_track(old_path)
        index.upsert_track(track)
        original = index.get_track_by_path(old_path)
        assert original is not None

        index.move_track(old_path, new_path, "New Title", 1234567890.0)

        # Old path gone, new path present with updated title.
        assert index.get_track_by_path(old_path) is None
        updated = index.get_track_by_path(new_path)
        assert updated is not None
        assert updated.title == "New Title"
        # Stats preserved.
        assert updated.id == original.id
        assert updated.mb_recording_id == original.mb_recording_id
        index.close()

    def test_move_track_preserves_stats(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "db.sqlite")
        old_path = tmp_path / "01.mp3"
        track = _sample_track(old_path)
        index.upsert_track(track)
        # Set favorite via the dedicated method (upsert_track doesn't persist it).
        index.set_favorite(old_path, True)
        index.record_played(old_path)

        new_path = tmp_path / "01 - New.mp3"
        index.move_track(old_path, new_path, "New Title", 2000.0)

        updated = index.get_track_by_path(new_path)
        assert updated is not None
        assert updated.favorite is True
        assert updated.last_played is not None
        assert updated.play_count == 1
        index.close()


# ---------------------------------------------------------------------------
# Server endpoint: PATCH /api/v1/tracks/{track_id}/tags
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


class TestPatchTrackTagsEndpoint:
    def _client_with_track(
        self,
        tmp_path: Path,
        track: Track,
        engine: MagicMock,
        queue: MagicMock,
    ) -> tuple[TestClient, LibraryIndex]:
        db = LibraryIndex(tmp_path / "db.sqlite")
        db.upsert_track(track)
        app = create_app(
            index=db,
            engine=engine,
            queue=queue,
            library_path=tmp_path,
        )
        return TestClient(app, raise_server_exceptions=False), db

    def test_rename_updates_file_and_db(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        mp3 = tmp_path / "Artist" / "2024 - Album" / "01 - Old Title.mp3"
        mp3.parent.mkdir(parents=True)
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2024",
            track="1",
            title="Old Title",
        )
        track = _sample_track(
            mp3, title="Old Title", album_artist="Artist", album="Album", year="2024"
        )
        client, index = self._client_with_track(
            tmp_path, track, _mock_engine, _mock_queue
        )
        row = index.get_track_by_path(mp3)
        assert row is not None

        resp = client.patch(
            f"/api/v1/tracks/{row.id}/tags", json={"title": "New Title"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Title"
        # Old path gone, new path on disk.
        assert not mp3.exists()
        new_mp3 = tmp_path / "Artist" / "2024 - Album" / "01 - New Title.mp3"
        assert new_mp3.exists()
        # DB updated.
        updated = index.get_track_by_path(new_mp3)
        assert updated is not None
        assert updated.title == "New Title"
        assert updated.id == row.id  # id preserved
        index.close()

    def test_returns_404_for_unknown_track(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        app = create_app(
            index=db, engine=_mock_engine, queue=_mock_queue, library_path=tmp_path
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch("/api/v1/tracks/99999/tags", json={"title": "X"})
        assert resp.status_code == 404
        db.close()

    def test_returns_403_for_currently_playing_track(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        mp3 = tmp_path / "Artist" / "2024 - Album" / "01 - Old.mp3"
        mp3.parent.mkdir(parents=True)
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2024",
            track="1",
            title="Old",
        )
        track = _sample_track(
            mp3, title="Old", album_artist="Artist", album="Album", year="2024"
        )
        client, index = self._client_with_track(
            tmp_path, track, _mock_engine, _mock_queue
        )
        row = index.get_track_by_path(mp3)
        assert row is not None
        # Simulate currently-playing.
        _mock_queue.current.return_value = row

        resp = client.patch(f"/api/v1/tracks/{row.id}/tags", json={"title": "New"})
        assert resp.status_code == 403
        index.close()

    def test_returns_403_for_lookahead_track(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        mp3 = tmp_path / "Artist" / "2024 - Album" / "01 - Old.mp3"
        mp3.parent.mkdir(parents=True)
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2024",
            track="1",
            title="Old",
        )
        track = _sample_track(
            mp3, title="Old", album_artist="Artist", album="Album", year="2024"
        )
        client, index = self._client_with_track(
            tmp_path, track, _mock_engine, _mock_queue
        )
        row = index.get_track_by_path(mp3)
        assert row is not None
        # Simulate lookahead (N+1).
        _mock_queue.current.return_value = None
        _mock_queue.peek_next.return_value = row

        resp = client.patch(f"/api/v1/tracks/{row.id}/tags", json={"title": "New"})
        assert resp.status_code == 403
        index.close()

    def test_returns_409_on_collision_without_overwrite(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        mp3 = tmp_path / "Artist" / "2024 - Album" / "01 - Old Title.mp3"
        mp3.parent.mkdir(parents=True)
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2024",
            track="1",
            title="Old Title",
        )
        # Create a file at the destination path to force collision.
        collision_path = tmp_path / "Artist" / "2024 - Album" / "01 - New Title.mp3"
        collision_path.write_bytes(b"\xff\xfb" * 64)

        track = _sample_track(
            mp3, title="Old Title", album_artist="Artist", album="Album", year="2024"
        )
        client, index = self._client_with_track(
            tmp_path, track, _mock_engine, _mock_queue
        )
        row = index.get_track_by_path(mp3)
        assert row is not None

        resp = client.patch(
            f"/api/v1/tracks/{row.id}/tags", json={"title": "New Title"}
        )
        assert resp.status_code == 409
        body = resp.json()
        assert "target_path" in body["detail"]
        # Original file untouched.
        assert mp3.exists()
        index.close()

    def test_overwrite_replaces_existing_file(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        mp3 = tmp_path / "Artist" / "2024 - Album" / "01 - Old Title.mp3"
        mp3.parent.mkdir(parents=True)
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2024",
            track="1",
            title="Old Title",
        )
        collision_path = tmp_path / "Artist" / "2024 - Album" / "01 - New Title.mp3"
        collision_path.write_bytes(b"\xff\xfb" * 64)

        track = _sample_track(
            mp3, title="Old Title", album_artist="Artist", album="Album", year="2024"
        )
        client, index = self._client_with_track(
            tmp_path, track, _mock_engine, _mock_queue
        )
        row = index.get_track_by_path(mp3)
        assert row is not None

        resp = client.patch(
            f"/api/v1/tracks/{row.id}/tags",
            json={"title": "New Title", "overwrite": True},
        )
        assert resp.status_code == 200
        assert collision_path.exists()
        assert not mp3.exists()
        index.close()

    def test_no_rename_when_path_unchanged(
        self, tmp_path: Path, _mock_engine: MagicMock, _mock_queue: MagicMock
    ) -> None:
        """Title edit that doesn't change the rendered path should not move the file."""
        # Use a template that doesn't include title so the path is always the same.
        mp3 = tmp_path / "Artist" / "2024 - Album" / "track.mp3"
        mp3.parent.mkdir(parents=True)
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2024",
            track="1",
            title="Old",
        )

        track = _sample_track(
            mp3, title="Old", album_artist="Artist", album="Album", year="2024"
        )
        db = LibraryIndex(tmp_path / "db.sqlite")
        db.upsert_track(track)
        app = create_app(
            index=db,
            engine=_mock_engine,
            queue=_mock_queue,
            library_path=tmp_path,
            # Template that omits {title} so any title edit leaves path unchanged.
            config_values={
                "library.path_template": "{album_artist}/{year} - {album}/track.{ext}"
            },
        )
        client = TestClient(app, raise_server_exceptions=False)
        row = db.get_track_by_path(mp3)
        assert row is not None

        resp = client.patch(f"/api/v1/tracks/{row.id}/tags", json={"title": "New"})
        assert resp.status_code == 200
        assert mp3.exists()  # file not moved
        updated = db.get_track_by_path(mp3)
        assert updated is not None
        assert updated.title == "New"
        db.close()
