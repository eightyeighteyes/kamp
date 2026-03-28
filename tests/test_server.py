"""Tests for kamp_core.server (REST API and WebSocket)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kamp_core.library import AlbumInfo, Track
from kamp_core.playback import PlaybackState
from kamp_core.server import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _track(n: int, album: str = "Album", artist: str = "Artist") -> Track:
    return Track(
        file_path=Path(f"/music/{n:02d}.mp3"),
        title=f"Track {n}",
        artist=artist,
        album_artist=artist,
        album=album,
        year="2024",
        track_number=n,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
    )


def _album(
    artist: str, album: str, year: str = "2024", count: int = 10, has_art: bool = False
) -> AlbumInfo:
    return AlbumInfo(
        album_artist=artist, album=album, year=year, track_count=count, has_art=has_art
    )


@pytest.fixture()
def mock_index() -> MagicMock:
    index = MagicMock()
    index.albums.return_value = []
    index.artists.return_value = []
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
    return queue


@pytest.fixture()
def client(
    mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
) -> TestClient:
    app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Library endpoints
# ---------------------------------------------------------------------------


class TestAlbumsEndpoint:
    def test_returns_empty_list_when_no_albums(self, client: TestClient) -> None:
        response = client.get("/api/v1/albums")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_album_list(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [
            _album("Aesop Rock", "Labor Days"),
            _album("Aesop Rock", "Bazooka Tooth"),
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/albums").json()
        assert len(data) == 2
        assert data[0]["album"] == "Labor Days"
        assert data[0]["album_artist"] == "Aesop Rock"
        assert data[0]["track_count"] == 10

    def test_album_has_required_fields(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [_album("Artist", "Record", year="2020")]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        album = c.get("/api/v1/albums").json()[0]
        assert set(album.keys()) >= {
            "album_artist",
            "album",
            "year",
            "track_count",
            "has_art",
        }

    def test_album_has_art_field(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [_album("Artist", "Record", has_art=True)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        album = c.get("/api/v1/albums").json()[0]
        assert album["has_art"] is True


class TestAlbumArtEndpoint:
    def test_returns_art_bytes_when_embedded(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.embedded_art = True
        mock_index.tracks_for_album.return_value = [track]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with patch(
            "kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")
        ):
            res = c.get("/api/v1/album-art?album_artist=Artist&album=Album")
        assert res.status_code == 200
        assert res.content == b"IMGDATA"
        assert "image/jpeg" in res.headers["content-type"]

    def test_returns_404_when_no_tracks(self, client: TestClient) -> None:
        res = client.get("/api/v1/album-art?album_artist=Unknown&album=Ghost")
        assert res.status_code == 404

    def test_returns_404_when_no_tracks_have_art(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]  # embedded_art=False
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        res = c.get("/api/v1/album-art?album_artist=Artist&album=Album")
        assert res.status_code == 404

    def test_returns_404_when_extract_returns_none(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.embedded_art = True
        mock_index.tracks_for_album.return_value = [track]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with patch("kamp_core.server.extract_art", return_value=None):
            res = c.get("/api/v1/album-art?album_artist=Artist&album=Album")
        assert res.status_code == 404


class TestArtistsEndpoint:
    def test_returns_empty_list_when_no_artists(self, client: TestClient) -> None:
        response = client.get("/api/v1/artists")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_artist_list(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.artists.return_value = ["Aesop Rock", "Zeppelin"]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        assert c.get("/api/v1/artists").json() == ["Aesop Rock", "Zeppelin"]


class TestTracksForAlbumEndpoint:
    def test_returns_tracks_for_album(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [
            _track(1, album="Labor Days"),
            _track(2, album="Labor Days"),
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/tracks?album_artist=Aesop+Rock&album=Labor+Days").json()
        assert len(data) == 2
        assert data[0]["title"] == "Track 1"
        mock_index.tracks_for_album.assert_called_once_with("Aesop Rock", "Labor Days")

    def test_track_has_required_fields(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        track = c.get("/api/v1/tracks?album_artist=Artist&album=Album").json()[0]
        assert set(track.keys()) >= {
            "title",
            "artist",
            "album",
            "track_number",
            "disc_number",
            "file_path",
            "ext",
        }

    def test_returns_empty_list_for_unknown_album(self, client: TestClient) -> None:
        response = client.get("/api/v1/tracks?album_artist=Unknown&album=Ghost")
        assert response.status_code == 200
        assert response.json() == []


class TestLibraryScanEndpoint:
    def test_scan_returns_result(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        with patch("kamp_core.server.LibraryScanner") as MockScanner:
            MockScanner.return_value.scan.return_value = MagicMock(
                added=3, removed=1, unchanged=10
            )
            app = create_app(
                index=mock_index,
                engine=mock_engine,
                queue=mock_queue,
                library_path=Path("/music"),
            )
            c = TestClient(app)
            data = c.post("/api/v1/library/scan").json()

        assert data["added"] == 3
        assert data["removed"] == 1
        assert data["unchanged"] == 10

    def test_scan_unavailable_without_library_path(self, client: TestClient) -> None:
        # client fixture has no library_path configured
        response = client.post("/api/v1/library/scan")
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Player endpoints
# ---------------------------------------------------------------------------


class TestPlayerStateEndpoint:
    def test_returns_initial_state(self, client: TestClient) -> None:
        response = client.get("/api/v1/player/state")
        assert response.status_code == 200
        data = response.json()
        assert data["playing"] is False
        assert data["position"] == pytest.approx(0.0)
        assert data["duration"] == pytest.approx(0.0)
        assert data["volume"] == 100
        assert data["current_track"] is None

    def test_includes_current_track_when_playing(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_engine.state = PlaybackState(playing=True, position=42.0, duration=180.0)
        mock_queue.current.return_value = _track(3)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/player/state").json()
        assert data["playing"] is True
        assert data["current_track"]["title"] == "Track 3"


class TestPlayerPlayEndpoint:
    def test_play_loads_album_and_starts_playback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        tracks = [_track(i) for i in range(3)]
        mock_index.tracks_for_album.return_value = tracks
        mock_queue.current.return_value = tracks[0]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        response = c.post(
            "/api/v1/player/play",
            json={"album_artist": "Artist", "album": "Album", "track_index": 0},
        )
        assert response.status_code == 200
        mock_queue.load.assert_called_once_with(tracks, start_index=0)
        mock_engine.play.assert_called_once_with(tracks[0].file_path)

    def test_play_returns_404_for_unknown_album(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        response = c.post(
            "/api/v1/player/play",
            json={"album_artist": "Ghost", "album": "None", "track_index": 0},
        )
        assert response.status_code == 404


class TestPlayerControlEndpoints:
    def test_pause(self, client: TestClient, mock_engine: MagicMock) -> None:
        assert client.post("/api/v1/player/pause").status_code == 200
        mock_engine.pause.assert_called_once()

    def test_resume(self, client: TestClient, mock_engine: MagicMock) -> None:
        assert client.post("/api/v1/player/resume").status_code == 200
        mock_engine.resume.assert_called_once()

    def test_stop(self, client: TestClient, mock_engine: MagicMock) -> None:
        assert client.post("/api/v1/player/stop").status_code == 200
        mock_engine.stop.assert_called_once()

    def test_seek(self, client: TestClient, mock_engine: MagicMock) -> None:
        response = client.post("/api/v1/player/seek", json={"position": 42.5})
        assert response.status_code == 200
        mock_engine.seek.assert_called_once_with(42.5)

    def test_set_volume(self, client: TestClient, mock_engine: MagicMock) -> None:
        response = client.post("/api/v1/player/volume", json={"volume": 80})
        assert response.status_code == 200
        assert mock_engine.volume == 80

    def test_next_track(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        next_track = _track(2)
        mock_queue.next.return_value = next_track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        assert c.post("/api/v1/player/next").status_code == 200
        mock_engine.play.assert_called_once_with(next_track.file_path)

    def test_next_at_end_of_queue_stops(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_queue.next.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        assert c.post("/api/v1/player/next").status_code == 200
        mock_engine.stop.assert_called_once()

    def test_prev_track(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        prev_track = _track(1)
        mock_queue.prev.return_value = prev_track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        assert c.post("/api/v1/player/prev").status_code == 200
        mock_engine.play.assert_called_once_with(prev_track.file_path)

    def test_prev_at_start_of_queue_is_noop(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_queue.prev.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        assert c.post("/api/v1/player/prev").status_code == 200
        mock_engine.play.assert_not_called()

    def test_set_shuffle(self, client: TestClient, mock_queue: MagicMock) -> None:
        response = client.post("/api/v1/player/shuffle", json={"shuffle": True})
        assert response.status_code == 200
        mock_queue.set_shuffle.assert_called_once_with(True)

    def test_set_repeat(self, client: TestClient, mock_queue: MagicMock) -> None:
        response = client.post("/api/v1/player/repeat", json={"repeat": True})
        assert response.status_code == 200
        mock_queue.set_repeat.assert_called_once_with(True)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


class TestPlayerWebSocket:
    def test_websocket_sends_initial_state(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_engine.state = PlaybackState(playing=True, position=10.0, duration=200.0)
        mock_queue.current.return_value = _track(1)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            msg = ws.receive_json()
        assert msg["type"] == "player.state"
        assert msg["playing"] is True
        assert msg["position"] == pytest.approx(10.0)
        assert msg["current_track"]["title"] == "Track 1"

    def test_websocket_state_updates_on_poll(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Sending a ping triggers a fresh state snapshot."""
        mock_engine.state = PlaybackState()
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # initial state
            mock_engine.state = PlaybackState(playing=True, position=5.0)
            ws.send_text("ping")
            msg = ws.receive_json()
        assert msg["playing"] is True
        assert msg["position"] == pytest.approx(5.0)
