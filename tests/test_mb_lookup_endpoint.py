"""Tests for the MusicBrainz lookup, track-meta, and extended album-meta endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kamp_core.library import Track
from kamp_core.playback import PlaybackState
from kamp_core.server import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _track(n: int, title: str = "") -> Track:
    return Track(
        file_path=Path(f"/music/{n:02d}.mp3"),
        title=title or f"Track {n}",
        artist="Band",
        album_artist="Band",
        album="Record",
        release_date="2020",
        track_number=n,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
    )


def _fake_release(mbid: str = "release-1") -> MagicMock:
    """Return a minimal ReleaseInfo-shaped mock."""
    track = MagicMock()
    track.number = 1
    track.disc = 1
    track.title = "MB Track 1"
    track.recording_mbid = "rec-1"

    r = MagicMock()
    r.mbid = mbid
    r.release_group_mbid = "rg-1"
    r.title = "MB Record"
    r.album_artist = "MB Band"
    r.release_date = "2021"
    r.label = "MB Label"
    r.release_type = "Album"
    r.tracks = {"1-1": track}
    return r


@pytest.fixture()
def mock_index() -> MagicMock:
    index = MagicMock()
    index.albums.return_value = []
    index.tracks_for_album.return_value = []
    return index


@pytest.fixture()
def mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.state = PlaybackState()
    return engine


@pytest.fixture()
def mock_queue() -> MagicMock:
    queue = MagicMock()
    queue.current.return_value = None
    queue.peek_next.return_value = None
    return queue


# ---------------------------------------------------------------------------
# GET /api/v1/albums/musicbrainz
# ---------------------------------------------------------------------------


class TestGetAlbumMusicBrainz:
    """GET /api/v1/albums/musicbrainz returns ranked MB candidates."""

    def test_happy_path_returns_candidates(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        tracks = [_track(1), _track(2)]
        mock_index.tracks_for_album.return_value = tracks
        release = _fake_release()
        lookup_fn = MagicMock(return_value=[release])

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_lookup_fn=lookup_fn,
        )
        resp = TestClient(app).get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["candidates"]) == 1
        c = data["candidates"][0]
        assert c["mbid"] == "release-1"
        assert c["title"] == "MB Record"
        assert c["album_artist"] == "MB Band"
        assert c["release_date"] == "2021"
        assert c["label"] == "MB Label"
        assert c["release_type"] == "Album"
        assert len(c["tracks"]) == 1
        assert c["tracks"][0]["title"] == "MB Track 1"
        assert c["tracks"][0]["recording_mbid"] == "rec-1"

        # Verify the lookup received the correct (artist, title, album) tuples
        lookup_fn.assert_called_once_with(
            [("Band", "Track 1", "Record"), ("Band", "Track 2", "Record")]
        )

    def test_returns_multiple_candidates(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        releases = [_fake_release("r1"), _fake_release("r2")]
        releases[1].title = "MB Record (Deluxe)"

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_lookup_fn=MagicMock(return_value=releases),
        )
        resp = TestClient(app).get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )

        assert resp.status_code == 200
        assert len(resp.json()["candidates"]) == 2
        assert resp.json()["candidates"][1]["mbid"] == "r2"

    def test_returns_404_when_album_not_found(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = []

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_lookup_fn=MagicMock(),
        )
        resp = TestClient(app).get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Ghost", "album": "Void"},
        )
        assert resp.status_code == 404

    def test_returns_404_when_mb_lookup_raises(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        lookup_fn = MagicMock(side_effect=Exception("No MusicBrainz results"))

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_lookup_fn=lookup_fn,
        )
        resp = TestClient(app).get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Unknown", "album": "Untitled"},
        )
        assert resp.status_code == 404
        assert "No MusicBrainz results" in resp.json()["detail"]

    def test_returns_503_when_lookup_fn_not_wired(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]

        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue
        )  # no mb_lookup_fn
        resp = TestClient(app).get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )
        assert resp.status_code == 503

    def test_tracks_sorted_by_disc_then_number(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        t1, t2, t3 = MagicMock(), MagicMock(), MagicMock()
        t1.number, t1.disc, t1.title, t1.recording_mbid = 2, 1, "Second", "rec-2"
        t2.number, t2.disc, t2.title, t2.recording_mbid = 1, 2, "Disc2T1", "rec-3"
        t3.number, t3.disc, t3.title, t3.recording_mbid = 1, 1, "First", "rec-1"
        release = _fake_release()
        release.tracks = {"1-2": t1, "2-1": t2, "1-1": t3}

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_lookup_fn=MagicMock(return_value=[release]),
        )
        resp = TestClient(app).get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )
        titles = [t["title"] for t in resp.json()["candidates"][0]["tracks"]]
        assert titles == ["First", "Second", "Disc2T1"]


# ---------------------------------------------------------------------------
# PATCH /api/v1/albums/meta — mb_release_id extension (KAMP-230)
# ---------------------------------------------------------------------------


class TestPatchAlbumMetaMbReleaseId:
    def _make_track(self, n: int = 1) -> Track:
        return Track(
            file_path=Path(f"/music/{n:02d}.mp3"),
            title=f"Track {n}",
            artist="Band",
            album_artist="Band",
            album="Record",
            release_date="2020",
            track_number=n,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
        )

    def test_mb_release_id_written_to_file_and_db(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = self._make_track()
        updated = Track(**{**track.__dict__, "mb_release_id": "new-mbid"})
        mock_index.tracks_for_album.return_value = [track]
        mock_index.update_album_meta.return_value = [updated]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch("kamp_core.library.write_meta_tags_to_file") as mock_write:
            resp = TestClient(app).patch(
                "/api/v1/albums/meta",
                params={"album_artist": "Band", "album": "Record"},
                json={"mb_release_id": "new-mbid"},
            )

        assert resp.status_code == 200
        mock_write.assert_called_once_with(
            track.file_path,
            genre=None,
            label=None,
            release_date=None,
            mb_release_id="new-mbid",
        )
        mock_index.update_album_meta.assert_called_once_with(
            "Band",
            "Record",
            genre=None,
            label=None,
            release_date=None,
            mb_release_id="new-mbid",
        )

    def test_returns_400_when_only_mb_release_id_absent_alongside_other_nones(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [self._make_track()]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/albums/meta",
            params={"album_artist": "Band", "album": "Record"},
            json={},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH /api/v1/tracks/{track_id}/meta (KAMP-230)
# ---------------------------------------------------------------------------


class TestPatchTrackMeta:
    def _make_track(self) -> Track:
        return Track(
            file_path=Path("/music/01.mp3"),
            title="Track 1",
            artist="Band",
            album_artist="Band",
            album="Record",
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            id=42,
        )

    def test_writes_mbid_to_file_and_db(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = self._make_track()
        updated = Track(**{**track.__dict__, "mb_recording_id": "rec-new"})
        mock_index.get_track_by_id.return_value = track
        mock_index.update_track_mb_recording_id.return_value = updated

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch("kamp_core.library.write_track_mbid_to_file") as mock_write:
            resp = TestClient(app).patch(
                "/api/v1/tracks/42/meta",
                json={"mb_recording_id": "rec-new"},
            )

        assert resp.status_code == 200
        mock_write.assert_called_once_with(track.file_path, mb_recording_id="rec-new")
        mock_index.update_track_mb_recording_id.assert_called_once_with(42, "rec-new")
        assert resp.json()["mb_recording_id"] == "rec-new"

    def test_returns_404_for_unknown_track(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_track_by_id.return_value = None

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/tracks/999/meta",
            json={"mb_recording_id": "rec-1"},
        )
        assert resp.status_code == 404

    def test_returns_500_when_file_write_fails(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_track_by_id.return_value = self._make_track()

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch(
            "kamp_core.library.write_track_mbid_to_file",
            side_effect=OSError("permission denied"),
        ):
            resp = TestClient(app).patch(
                "/api/v1/tracks/42/meta",
                json={"mb_recording_id": "rec-1"},
            )
        assert resp.status_code == 500
