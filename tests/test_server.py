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
    queue.queue_tracks.return_value = ([], -1)
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

    def test_album_includes_art_version(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        album_info = _album("Artist", "Record")
        album_info.art_version = 1234567.0
        mock_index.albums.return_value = [album_info]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        album = c.get("/api/v1/albums").json()[0]
        assert album["art_version"] == pytest.approx(1234567.0)


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

    def test_versioned_request_returns_immutable_cache_header(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """?v= stamp → Cache-Control: public, max-age=31536000, immutable."""
        track = _track(1)
        track.embedded_art = True
        mock_index.tracks_for_album.return_value = [track]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with patch(
            "kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")
        ):
            res = c.get("/api/v1/album-art?album_artist=Artist&album=Album&v=1234567.0")
        assert res.status_code == 200
        cc = res.headers.get("cache-control", "")
        assert "public" in cc
        assert "immutable" in cc
        assert "max-age=31536000" in cc

    def test_unversioned_request_returns_no_store_cache_header(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """No ?v= stamp → Cache-Control: no-store so stale art is never served."""
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
        cc = res.headers.get("cache-control", "")
        assert "no-store" in cc


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


class TestMissingAlbumEndpoints:
    """Endpoints that support file_path-based lookup for tracks without an album tag."""

    def test_albums_includes_missing_album_fields(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [
            AlbumInfo(
                album_artist="Mndsgn.",
                album="Lone Track",
                year="2020",
                track_count=1,
                has_art=False,
                missing_album=True,
                file_path="/music/lone.mp3",
            )
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        album = c.get("/api/v1/albums").json()[0]
        assert album["missing_album"] is True
        assert album["file_path"] == "/music/lone.mp3"

    def test_tracks_endpoint_uses_file_path_when_provided(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1, album="")
        mock_index.get_track_by_path.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get(
            "/api/v1/tracks?album_artist=&album=&file_path=%2Fmusic%2F01.mp3"
        ).json()
        assert len(data) == 1
        mock_index.get_track_by_path.assert_called_once_with(Path("/music/01.mp3"))
        mock_index.tracks_for_album.assert_not_called()

    def test_album_art_endpoint_uses_file_path_when_provided(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1, album="")
        track.embedded_art = True
        mock_index.get_track_by_path.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with patch(
            "kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")
        ):
            res = c.get(
                "/api/v1/album-art?album_artist=&album=&file_path=%2Fmusic%2F01.mp3"
            )
        assert res.status_code == 200
        mock_index.get_track_by_path.assert_called_once_with(Path("/music/01.mp3"))
        mock_index.tracks_for_album.assert_not_called()


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


class TestScanProgressEndpoint:
    def test_progress_idle_by_default(self, client: TestClient) -> None:
        res = client.get("/api/v1/library/scan/progress")
        assert res.status_code == 200
        data = res.json()
        assert data["active"] is False
        assert data["current"] == 0
        assert data["total"] == 0

    def test_progress_callback_updates_state(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        # Capture the on_progress callback that scan_library passes to LibraryScanner.
        captured: list[MagicMock] = []

        def _fake_scan(path: object, on_progress: object = None) -> MagicMock:
            captured.append(on_progress)  # type: ignore[arg-type]
            return MagicMock(added=2, removed=0, unchanged=0)

        with patch("kamp_core.server.LibraryScanner") as MockScanner:
            MockScanner.return_value.scan.side_effect = _fake_scan
            app = create_app(
                index=mock_index,
                engine=mock_engine,
                queue=mock_queue,
                library_path=Path("/music"),
            )
            c = TestClient(app)
            c.post("/api/v1/library/scan")

        # The callback was passed into scan().
        assert len(captured) == 1
        assert callable(captured[0])

    def test_progress_resets_to_idle_after_scan(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        with patch("kamp_core.server.LibraryScanner") as MockScanner:
            MockScanner.return_value.scan.return_value = MagicMock(
                added=1, removed=0, unchanged=0
            )
            app = create_app(
                index=mock_index,
                engine=mock_engine,
                queue=mock_queue,
                library_path=Path("/music"),
            )
            c = TestClient(app)
            c.post("/api/v1/library/scan")
            data = c.get("/api/v1/library/scan/progress").json()

        assert data["active"] is False


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


class TestQueueEndpoint:
    def test_empty_queue(self, client: TestClient) -> None:
        response = client.get("/api/v1/player/queue")
        assert response.status_code == 200
        data = response.json()
        assert data["tracks"] == []
        assert data["position"] == -1

    def test_returns_tracks_with_position(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        ts = [_track(1), _track(2), _track(3)]
        mock_queue.queue_tracks.return_value = (ts, 1)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/player/queue").json()
        assert len(data["tracks"]) == 3
        assert data["position"] == 1
        assert data["tracks"][1]["title"] == "Track 2"

    def test_track_has_required_fields(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_queue.queue_tracks.return_value = ([_track(1)], 0)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        track = c.get("/api/v1/player/queue").json()["tracks"][0]
        assert set(track.keys()) >= {
            "title",
            "artist",
            "album_artist",
            "album",
            "file_path",
            "ext",
        }


class TestQueueMutationEndpoints:
    def test_add_to_queue_calls_queue_method(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1)
        mock_index.get_track_by_path.return_value = t
        resp = client.post(
            "/api/v1/player/queue/add", json={"file_path": str(t.file_path)}
        )
        assert resp.status_code == 200
        mock_queue.add_to_queue.assert_called_once_with(t)

    def test_add_to_queue_404_for_unknown_path(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_track_by_path.return_value = None
        resp = client.post(
            "/api/v1/player/queue/add", json={"file_path": "/no/such/file.mp3"}
        )
        assert resp.status_code == 404

    def test_play_next_calls_queue_method(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(2)
        mock_index.get_track_by_path.return_value = t
        resp = client.post(
            "/api/v1/player/queue/play-next", json={"file_path": str(t.file_path)}
        )
        assert resp.status_code == 200
        mock_queue.play_next.assert_called_once_with(t)

    def test_play_next_404_for_unknown_path(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_track_by_path.return_value = None
        resp = client.post(
            "/api/v1/player/queue/play-next", json={"file_path": "/no/such/file.mp3"}
        )
        assert resp.status_code == 404

    def test_move_queue_calls_queue_method(
        self, client: TestClient, mock_queue: MagicMock
    ) -> None:
        resp = client.post(
            "/api/v1/player/queue/move", json={"from_index": 0, "to_index": 2}
        )
        assert resp.status_code == 200
        mock_queue.move.assert_called_once_with(0, 2)

    def test_move_queue_400_on_index_error(
        self, client: TestClient, mock_queue: MagicMock
    ) -> None:
        mock_queue.move.side_effect = IndexError("Queue index out of range: 0, 99")
        resp = client.post(
            "/api/v1/player/queue/move", json={"from_index": 0, "to_index": 99}
        )
        assert resp.status_code == 400

    def test_clear_queue_calls_queue_method(
        self, client: TestClient, mock_queue: MagicMock
    ) -> None:
        resp = client.post("/api/v1/player/queue/clear")
        assert resp.status_code == 200
        mock_queue.clear.assert_called_once()

    def test_clear_remaining_calls_queue_method_with_position(
        self, client: TestClient, mock_queue: MagicMock
    ) -> None:
        resp = client.post("/api/v1/player/queue/clear-remaining", json={"position": 4})
        assert resp.status_code == 200
        mock_queue.clear_remaining.assert_called_once_with(4)

    def test_skip_to_calls_engine_play(
        self, client: TestClient, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(3)
        mock_queue.skip_to.return_value = t
        resp = client.post("/api/v1/player/queue/skip-to", json={"position": 3})
        assert resp.status_code == 200
        mock_queue.skip_to.assert_called_once_with(3)
        mock_engine.play.assert_called_once_with(t.file_path)

    def test_skip_to_invalid_position_does_not_play(
        self, client: TestClient, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_queue.skip_to.return_value = None
        resp = client.post("/api/v1/player/queue/skip-to", json={"position": 99})
        assert resp.status_code == 200
        mock_engine.play.assert_not_called()


class TestAlbumQueueEndpoints:
    def test_add_album_to_queue_calls_queue_method(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        ts = [_track(i) for i in range(3)]
        mock_index.tracks_for_album.return_value = ts
        resp = client.post(
            "/api/v1/player/queue/add-album",
            json={"album_artist": "Artist", "album": "Album"},
        )
        assert resp.status_code == 200
        mock_queue.add_album_to_queue.assert_called_once_with(ts)

    def test_add_album_to_queue_404_for_unknown_album(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = []
        resp = client.post(
            "/api/v1/player/queue/add-album",
            json={"album_artist": "X", "album": "Y"},
        )
        assert resp.status_code == 404

    def test_play_album_next_calls_queue_method(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        ts = [_track(i) for i in range(3)]
        mock_index.tracks_for_album.return_value = ts
        resp = client.post(
            "/api/v1/player/queue/play-album-next",
            json={"album_artist": "Artist", "album": "Album"},
        )
        assert resp.status_code == 200
        mock_queue.play_album_next.assert_called_once_with(ts)

    def test_play_album_next_404_for_unknown_album(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = []
        resp = client.post(
            "/api/v1/player/queue/play-album-next",
            json={"album_artist": "X", "album": "Y"},
        )
        assert resp.status_code == 404

    def test_insert_album_calls_queue_method(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        ts = [_track(i) for i in range(3)]
        mock_index.tracks_for_album.return_value = ts
        resp = client.post(
            "/api/v1/player/queue/insert-album",
            json={"album_artist": "Artist", "album": "Album", "index": 2},
        )
        assert resp.status_code == 200
        mock_queue.insert_album_at.assert_called_once_with(ts, 2)

    def test_insert_album_404_for_unknown_album(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = []
        resp = client.post(
            "/api/v1/player/queue/insert-album",
            json={"album_artist": "X", "album": "Y", "index": 0},
        )
        assert resp.status_code == 404


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

    def test_websocket_push_track_changed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """notify_track_changed() proactively pushes a track.changed message."""
        mock_engine.state = PlaybackState()
        mock_queue.current.return_value = _track(1)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            mock_queue.current.return_value = _track(2)
            app.state.notify_track_changed()
            msg = ws.receive_json()
        assert msg["type"] == "track.changed"
        assert msg["current_track"]["title"] == "Track 2"

    def test_websocket_push_play_state_changed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """notify_play_state_changed() proactively pushes a play_state.changed message."""
        mock_engine.state = PlaybackState(playing=False)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            mock_engine.state = PlaybackState(playing=True)
            app.state.notify_play_state_changed()
            msg = ws.receive_json()
        assert msg["type"] == "play_state.changed"
        assert msg["playing"] is True

    def test_websocket_engine_on_play_state_changed_wired(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """create_app wires engine.on_play_state_changed to the broadcast notifier."""
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        assert mock_engine.on_play_state_changed is not None
        assert callable(mock_engine.on_play_state_changed)

    def test_play_endpoint_fires_track_changed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """POST /api/v1/player/play broadcasts a track.changed event."""
        mock_index.tracks_for_album.return_value = [_track(1)]
        mock_queue.current.return_value = _track(1)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            c.post("/api/v1/player/play", json={"album_artist": "A", "album": "B"})
            msg = ws.receive_json()
        assert msg["type"] == "track.changed"

    def test_next_endpoint_fires_track_changed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """POST /api/v1/player/next broadcasts a track.changed event."""
        mock_queue.next.return_value = _track(2)
        mock_queue.current.return_value = _track(2)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            c.post("/api/v1/player/next")
            msg = ws.receive_json()
        assert msg["type"] == "track.changed"


# ---------------------------------------------------------------------------
# Config: set library path
# ---------------------------------------------------------------------------


class TestSetLibraryPathEndpoint:
    def _make_client(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        *,
        on_library_path_set: object = None,
    ) -> TestClient:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_library_path_set=on_library_path_set,
        )
        return TestClient(app)

    def test_valid_directory_returns_ok(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        c = self._make_client(mock_index, mock_engine, mock_queue)
        res = c.post("/api/v1/config/library-path", json={"path": str(tmp_path)})
        assert res.status_code == 200
        assert res.json() == {"ok": True}

    def test_valid_path_unblocks_scan(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        # Start with no library_path — scan should return 503.
        c = self._make_client(mock_index, mock_engine, mock_queue)
        assert c.post("/api/v1/library/scan").status_code == 503

        # Set a valid path — scan should now succeed.
        c.post("/api/v1/config/library-path", json={"path": str(tmp_path)})
        with patch("kamp_core.server.LibraryScanner") as MockScanner:
            MockScanner.return_value.scan.return_value = MagicMock(
                added=0, removed=0, unchanged=0
            )
            res = c.post("/api/v1/library/scan")
        assert res.status_code == 200

    def test_nonexistent_path_returns_422(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        c = self._make_client(mock_index, mock_engine, mock_queue)
        res = c.post(
            "/api/v1/config/library-path",
            json={"path": "/this/does/not/exist/at/all"},
        )
        assert res.status_code == 422

    def test_file_path_returns_422(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "notadir.txt"
        f.touch()
        c = self._make_client(mock_index, mock_engine, mock_queue)
        res = c.post("/api/v1/config/library-path", json={"path": str(f)})
        assert res.status_code == 422

    def test_callback_invoked_on_success(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        callback = MagicMock()
        c = self._make_client(
            mock_index, mock_engine, mock_queue, on_library_path_set=callback
        )
        c.post("/api/v1/config/library-path", json={"path": str(tmp_path)})
        callback.assert_called_once_with(tmp_path.resolve())

    def test_callback_not_invoked_on_invalid_path(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        callback = MagicMock()
        c = self._make_client(
            mock_index, mock_engine, mock_queue, on_library_path_set=callback
        )
        c.post(
            "/api/v1/config/library-path",
            json={"path": "/this/does/not/exist/at/all"},
        )
        callback.assert_not_called()

    def test_no_callback_is_fine(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        # on_library_path_set=None (the default) — should not raise
        c = self._make_client(mock_index, mock_engine, mock_queue)
        res = c.post("/api/v1/config/library-path", json={"path": str(tmp_path)})
        assert res.status_code == 200


class TestSearchEndpoint:
    def test_empty_query_returns_empty_results(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.search.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=")
        assert res.status_code == 200
        data = res.json()
        assert data == {"albums": [], "tracks": []}

    def test_returns_matching_tracks_and_albums(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1, album="Kid A", artist="Radiohead")
        mock_index.search.return_value = [t]
        mock_index.albums.return_value = [_album("Radiohead", "Kid A")]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=radiohead")
        assert res.status_code == 200
        data = res.json()
        assert len(data["tracks"]) == 1
        assert data["tracks"][0]["album"] == "Kid A"
        assert len(data["albums"]) == 1
        assert data["albums"][0]["album"] == "Kid A"

    def test_albums_deduplicated_when_multiple_tracks_match(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t1 = _track(1, album="Kid A", artist="Radiohead")
        t2 = _track(2, album="Kid A", artist="Radiohead")
        mock_index.search.return_value = [t1, t2]
        mock_index.albums.return_value = [_album("Radiohead", "Kid A")]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=radiohead")
        data = res.json()
        # Two matching tracks → only one album entry (deduplication via index.albums)
        assert len(data["albums"]) == 1
        assert len(data["tracks"]) == 2

    def test_search_called_with_query_param(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.search.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        TestClient(app).get("/api/v1/search?q=kid+a")
        mock_index.search.assert_called_once_with("kid a")

    def test_search_albums_respect_sort_param(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t1 = _track(1, album="Amnesiac", artist="Radiohead")
        t2 = _track(2, album="Kid A", artist="Radiohead")
        mock_index.search.return_value = [t2, t1]  # FTS rank order (Kid A first)
        # index.albums returns albums in requested sort order (alphabetical by album)
        mock_index.albums.return_value = [
            _album("Radiohead", "Amnesiac"),
            _album("Radiohead", "Kid A"),
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=radiohead&sort=album")
        data = res.json()
        assert [a["album"] for a in data["albums"]] == ["Amnesiac", "Kid A"]
        mock_index.albums.assert_called_once_with(sort="album")


# ---------------------------------------------------------------------------
# UI state endpoints
# ---------------------------------------------------------------------------


class TestUiStateEndpoints:
    def test_get_ui_state_defaults(self, client: TestClient) -> None:
        resp = client.get("/api/v1/ui")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_view"] == "library"
        assert data["sort_order"] == "album_artist"
        assert data["queue_panel_open"] is False

    def test_get_ui_state_reflects_init_values(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            ui_active_view="now-playing",
            ui_sort_order="last_played",
            ui_queue_panel_open=1,
        )
        resp = TestClient(app).get("/api/v1/ui")
        data = resp.json()
        assert data["active_view"] == "now-playing"
        assert data["sort_order"] == "last_played"
        assert data["queue_panel_open"] is True

    def test_set_queue_panel_open_persists(self, client: TestClient) -> None:
        resp = client.post("/api/v1/ui/queue-panel", json={"open": True})
        assert resp.status_code == 200
        assert client.get("/api/v1/ui").json()["queue_panel_open"] is True

    def test_set_queue_panel_calls_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        callback = MagicMock()
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_ui_state_set=callback,
        )
        TestClient(app).post("/api/v1/ui/queue-panel", json={"open": True})
        callback.assert_called_once_with("ui.queue_panel_open", "1")

    def test_set_sort_order_persists(self, client: TestClient) -> None:
        resp = client.post("/api/v1/ui/sort-order", json={"sort_order": "last_played"})
        assert resp.status_code == 200
        assert client.get("/api/v1/ui").json()["sort_order"] == "last_played"

    def test_set_sort_order_invalid_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/ui/sort-order", json={"sort_order": "bogus"})
        assert resp.status_code == 422

    def test_set_sort_order_calls_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        callback = MagicMock()
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_ui_state_set=callback,
        )
        TestClient(app).post("/api/v1/ui/sort-order", json={"sort_order": "date_added"})
        callback.assert_called_once_with("ui.sort_order", "date_added")


# ---------------------------------------------------------------------------
# Favorite endpoint
# ---------------------------------------------------------------------------


class TestFavoriteEndpoint:
    def test_set_favorite_endpoint(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_track_by_path.return_value = _track(1)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/tracks/favorite",
            json={"file_path": "/music/01.mp3", "favorite": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_index.set_favorite.assert_called_once_with(Path("/music/01.mp3"), True)
        # Queue must also be updated so the next player-state snapshot is correct.
        mock_queue.update_favorite.assert_called_once_with(Path("/music/01.mp3"), True)

    def test_set_favorite_returns_404_for_unknown_track(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_track_by_path.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/tracks/favorite",
            json={"file_path": "/music/ghost.mp3", "favorite": True},
        )
        assert resp.status_code == 404

    def test_track_out_includes_favorite_field(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        track = (
            TestClient(app)
            .get("/api/v1/tracks?album_artist=Artist&album=Album")
            .json()[0]
        )
        assert "favorite" in track
        assert track["favorite"] is False

    def test_track_out_includes_play_count_field(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        track = (
            TestClient(app)
            .get("/api/v1/tracks?album_artist=Artist&album=Album")
            .json()[0]
        )
        assert "play_count" in track
        assert track["play_count"] == 0


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------

_SAMPLE_CONFIG_VALUES = {
    "paths.staging": "~/Music/staging",
    "paths.library": "~/Music",
    "artwork.min_dimension": 1000,
    "artwork.max_bytes": 1000000,
    "library.path_template": "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}",
    "bandcamp.connected": False,
    "bandcamp.username": None,
    "bandcamp.format": None,
    "bandcamp.poll_interval_minutes": None,
}


class TestConfigEndpoints:
    def test_get_config_returns_values(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values=_SAMPLE_CONFIG_VALUES,
        )
        response = TestClient(app).get("/api/v1/config")
        assert response.status_code == 200
        data = response.json()
        assert data["paths.staging"] == "~/Music/staging"
        assert data["paths.library"] == "~/Music"
        assert data["artwork.min_dimension"] == 1000
        assert data["artwork.max_bytes"] == 1000000
        assert data["library.path_template"].startswith("{album_artist}")
        assert data["bandcamp.username"] is None

    def test_get_config_returns_empty_dict_when_not_configured(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        response = TestClient(app).get("/api/v1/config")
        assert response.status_code == 200
        assert response.json() == {}

    def test_patch_config_calls_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        received: list[tuple[str, str]] = []

        def _on_config_set(key: str, value: str) -> None:
            received.append((key, value))

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values=_SAMPLE_CONFIG_VALUES.copy(),
            on_config_set=_on_config_set,
        )
        response = TestClient(app).patch(
            "/api/v1/config", json={"key": "artwork.min_dimension", "value": "500"}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert received == [("artwork.min_dimension", "500")]

    def test_patch_config_updates_in_memory_state(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values=_SAMPLE_CONFIG_VALUES.copy(),
            on_config_set=lambda k, v: None,
        )
        c = TestClient(app)
        c.patch("/api/v1/config", json={"key": "artwork.min_dimension", "value": "500"})
        data = c.get("/api/v1/config").json()
        assert data["artwork.min_dimension"] == 500

    def test_patch_config_returns_422_on_invalid_key(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        def _on_config_set(key: str, value: str) -> None:
            raise KeyError(f"Unknown config key {key!r}")

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values=_SAMPLE_CONFIG_VALUES.copy(),
            on_config_set=_on_config_set,
        )
        response = TestClient(app).patch(
            "/api/v1/config", json={"key": "nonexistent.key", "value": "foo"}
        )
        assert response.status_code == 422

    def test_patch_config_returns_422_on_invalid_value(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        def _on_config_set(key: str, value: str) -> None:
            raise ValueError(f"Invalid value {value!r}")

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values=_SAMPLE_CONFIG_VALUES.copy(),
            on_config_set=_on_config_set,
        )
        response = TestClient(app).patch(
            "/api/v1/config", json={"key": "bandcamp.format", "value": "invalid-fmt"}
        )
        assert response.status_code == 422

    def test_patch_config_coerces_int_values_in_state(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Integer config values should be stored as ints after a PATCH."""
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values=_SAMPLE_CONFIG_VALUES.copy(),
            on_config_set=lambda k, v: None,
        )
        c = TestClient(app)
        c.patch("/api/v1/config", json={"key": "artwork.max_bytes", "value": "500000"})
        data = c.get("/api/v1/config").json()
        assert data["artwork.max_bytes"] == 500000
        assert isinstance(data["artwork.max_bytes"], int)


class TestLastfmEndpoints:
    def test_connect_calls_callback_and_returns_ok(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        received: list[tuple[str, str]] = []

        def _on_connect(username: str, password: str) -> None:
            received.append((username, password))

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_lastfm_connect=_on_connect,
        )
        response = TestClient(app).post(
            "/api/v1/lastfm/connect",
            json={"username": "alice", "password": "secret"},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["username"] == "alice"
        assert received == [("alice", "secret")]

    def test_connect_updates_config_state(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_lastfm_connect=lambda u, p: None,
        )
        c = TestClient(app)
        c.post("/api/v1/lastfm/connect", json={"username": "alice", "password": "x"})
        data = c.get("/api/v1/config").json()
        assert data["lastfm.username"] == "alice"

    def test_connect_returns_422_when_callback_raises(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        def _on_connect(username: str, password: str) -> None:
            raise Exception("Invalid credentials")

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_lastfm_connect=_on_connect,
        )
        response = TestClient(app).post(
            "/api/v1/lastfm/connect",
            json={"username": "alice", "password": "wrong"},
        )
        assert response.status_code == 422

    def test_connect_returns_503_when_no_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        response = TestClient(app).post(
            "/api/v1/lastfm/connect",
            json={"username": "alice", "password": "x"},
        )
        assert response.status_code == 503

    def test_disconnect_calls_callback_and_returns_ok(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        called: list[bool] = []

        def _on_disconnect() -> None:
            called.append(True)

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_lastfm_disconnect=_on_disconnect,
        )
        response = TestClient(app).delete("/api/v1/lastfm/connect")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert called == [True]

    def test_disconnect_clears_lastfm_username_in_config(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"lastfm.username": "alice"},
            on_lastfm_connect=lambda u, p: None,
            on_lastfm_disconnect=lambda: None,
        )
        c = TestClient(app)
        c.delete("/api/v1/lastfm/connect")
        data = c.get("/api/v1/config").json()
        assert data["lastfm.username"] is None

    def test_disconnect_returns_503_when_no_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        response = TestClient(app).delete("/api/v1/lastfm/connect")
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Bandcamp session status / disconnect
# ---------------------------------------------------------------------------


class TestBandcampStatus:
    def test_status_returns_disconnected_when_no_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        response = TestClient(app).get("/api/v1/bandcamp/status")
        assert response.status_code == 200
        assert response.json() == {"connected": False, "username": None}

    def test_status_returns_connected_with_username(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        session = {"cookies": [], "username": "johndoe"}
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: session,
        )
        response = TestClient(app).get("/api/v1/bandcamp/status")
        assert response.status_code == 200
        assert response.json() == {"connected": True, "username": "johndoe"}

    def test_status_returns_connected_without_username(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        session: dict = {"cookies": []}
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: session,
        )
        response = TestClient(app).get("/api/v1/bandcamp/status")
        assert response.status_code == 200
        assert response.json() == {"connected": True, "username": None}

    def test_status_returns_disconnected_when_session_is_none(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: None,
        )
        response = TestClient(app).get("/api/v1/bandcamp/status")
        assert response.status_code == 200
        assert response.json() == {"connected": False, "username": None}

    def test_disconnect_calls_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        called: list[bool] = []

        def _on_disconnect() -> None:
            called.append(True)

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_bandcamp_disconnect=_on_disconnect,
        )
        response = TestClient(app).delete("/api/v1/bandcamp/connect")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert called == [True]

    def test_disconnect_returns_503_when_no_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        response = TestClient(app).delete("/api/v1/bandcamp/connect")
        assert response.status_code == 503

    def test_disconnect_clears_bandcamp_username_in_config(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"bandcamp.connected": True, "bandcamp.username": "johndoe"},
            on_bandcamp_disconnect=lambda: None,
        )
        c = TestClient(app)
        c.delete("/api/v1/bandcamp/connect")
        data = c.get("/api/v1/config").json()
        assert data["bandcamp.connected"] is False
        assert data["bandcamp.username"] is None

    def test_login_complete_sets_bandcamp_connected(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """bandcamp.connected is set to True even when username fetch fails."""
        cookies = [{"name": "js_logged_in", "value": "1", "domain": ".bandcamp.com"}]
        session = {"cookies": cookies, "username": None}
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"bandcamp.connected": False, "bandcamp.username": None},
            on_bandcamp_login_complete=lambda payload: None,
            get_bandcamp_session=lambda: session,
        )
        c = TestClient(app)
        c.post(
            "/api/v1/bandcamp/login-complete",
            json={"cookies": cookies, "origins": []},
        )
        data = c.get("/api/v1/config").json()
        assert data["bandcamp.connected"] is True

    def test_login_complete_sets_username_when_available(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        cookies = [{"name": "js_logged_in", "value": "1", "domain": ".bandcamp.com"}]
        session = {"cookies": cookies, "username": "johndoe"}
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"bandcamp.connected": False, "bandcamp.username": None},
            on_bandcamp_login_complete=lambda payload: None,
            get_bandcamp_session=lambda: session,
        )
        c = TestClient(app)
        c.post(
            "/api/v1/bandcamp/login-complete",
            json={"cookies": cookies, "origins": []},
        )
        data = c.get("/api/v1/config").json()
        assert data["bandcamp.connected"] is True
        assert data["bandcamp.username"] == "johndoe"


# ---------------------------------------------------------------------------
# Bandcamp session-cookies endpoint
# ---------------------------------------------------------------------------


class TestBandcampSessionCookies:
    """Tests for GET /api/v1/bandcamp/session-cookies."""

    def test_returns_empty_list_when_no_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        response = TestClient(app).get("/api/v1/bandcamp/session-cookies")
        assert response.status_code == 200
        assert response.json() == {"cookies": []}

    def test_returns_empty_list_when_session_is_none(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: None,
        )
        response = TestClient(app).get("/api/v1/bandcamp/session-cookies")
        assert response.status_code == 200
        assert response.json() == {"cookies": []}

    def test_returns_cookies_from_session(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        cookies = [
            {
                "name": "js_logged_in",
                "value": "1",
                "domain": ".bandcamp.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "lax",
            }
        ]
        session = {"cookies": cookies, "username": "johndoe"}
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: session,
        )
        response = TestClient(app).get("/api/v1/bandcamp/session-cookies")
        assert response.status_code == 200
        assert response.json() == {"cookies": cookies}


# ---------------------------------------------------------------------------
# Bandcamp proxy endpoints
# ---------------------------------------------------------------------------


class TestBandcampProxyEndpoints:
    """Tests for the proxy-fetch / fetch-result relay."""

    def test_fetch_result_returns_404_for_unknown_id(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/bandcamp/fetch-result",
            json={
                "id": "nonexistent",
                "status": 200,
                "body": "x",
                "content_type": "text/plain",
            },
        )
        assert response.status_code == 404

    def test_proxy_roundtrip_broadcasts_and_delivers_result(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """proxy-fetch broadcasts the request over the WS push channel and blocks.

        The WS broadcast carries the req_id that Electron uses to call fetch-result,
        which unblocks proxy-fetch and returns the net.fetch result to the daemon.

        All requests use the same TestClient so they share one event loop portal:
        proxy-fetch's asyncio.Event.wait() yields, the portal handles fetch-result,
        the event is set, and proxy-fetch completes.
        """
        import threading

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        proxy_response: dict = {}

        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # discard initial player.state

            # Post proxy-fetch from a background thread — it will block until
            # fetch-result is called.  Using the same TestClient so both requests
            # run in the same anyio event loop portal.
            t = threading.Thread(
                target=lambda: proxy_response.update(
                    c.post(
                        "/api/v1/bandcamp/proxy-fetch",
                        json={
                            "url": "https://bandcamp.com/api/fan/2/collection_summary",
                            "method": "GET",
                            "headers": {"User-Agent": "test"},
                            "body": None,
                        },
                    ).json()
                )
            )
            t.start()

            # The WS broadcast carries the req_id — Electron uses this to post back.
            msg = ws.receive_json()
            assert msg["type"] == "bandcamp.proxy-fetch"
            assert msg["url"] == "https://bandcamp.com/api/fan/2/collection_summary"
            assert msg["method"] == "GET"
            req_id = msg["id"]
            assert req_id
            # Cookies must not appear in the broadcast payload.
            assert "cookies" not in msg

            # Simulate Electron posting the net.fetch result.
            result_r = c.post(
                "/api/v1/bandcamp/fetch-result",
                json={
                    "id": req_id,
                    "status": 200,
                    "body": '{"fan_id": 42}',
                    "content_type": "application/json",
                },
            )
            assert result_r.status_code == 200

            t.join(timeout=5)

        assert proxy_response["status"] == 200
        assert proxy_response["body"] == '{"fan_id": 42}'
        assert proxy_response["content_type"] == "application/json"

    def test_proxy_broadcast_excludes_cookies(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Cookies must never appear in the proxy-fetch WS broadcast payload."""
        import threading

        cookies = [{"name": "js_logged_in", "value": "1", "domain": ".bandcamp.com"}]
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: {"cookies": cookies, "username": "johndoe"},
        )
        c = TestClient(app)
        broadcast: dict = {}

        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # discard initial player.state

            t = threading.Thread(
                target=lambda: c.post(
                    "/api/v1/bandcamp/proxy-fetch",
                    json={
                        "url": "https://bandcamp.com/api/test",
                        "method": "GET",
                        "headers": {},
                        "body": None,
                    },
                )
            )
            t.start()

            broadcast.update(ws.receive_json())
            req_id = broadcast["id"]

            c.post(
                "/api/v1/bandcamp/fetch-result",
                json={
                    "id": req_id,
                    "status": 200,
                    "body": "ok",
                    "content_type": "text/plain",
                },
            )
            t.join(timeout=5)

        # Cookies must not be broadcast — Electron fetches /session-cookies directly.
        assert "cookies" not in broadcast

    def test_late_joining_client_receives_pending_proxy_fetch(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """A WS client that connects after proxy-fetch is posted still gets the event.

        This is the startup-race fix: the daemon may fire proxy requests before
        the Electron preload has established its WS connection.
        """
        import threading

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        proxy_response: dict = {}

        # POST proxy-fetch with NO WS client connected yet.
        t = threading.Thread(
            target=lambda: proxy_response.update(
                c.post(
                    "/api/v1/bandcamp/proxy-fetch",
                    json={
                        "url": "https://bandcamp.com/api/fan/2/collection_summary",
                        "method": "GET",
                        "headers": {},
                        "body": None,
                    },
                ).json()
            )
        )
        t.start()

        # Give the thread a moment to register the request server-side.
        import time

        time.sleep(0.05)

        # Now the WS client connects — it should receive the pending event on connect.
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # discard player.state
            msg = ws.receive_json()  # should be the replayed proxy-fetch
            assert msg["type"] == "bandcamp.proxy-fetch"
            req_id = msg["id"]

            c.post(
                "/api/v1/bandcamp/fetch-result",
                json={
                    "id": req_id,
                    "status": 200,
                    "body": "ok",
                    "content_type": "text/plain",
                },
            )
            t.join(timeout=5)

        assert proxy_response["status"] == 200

    def test_fetch_result_removes_from_pending(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Answering a proxy request removes it from the pending replay queue."""
        import threading

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # discard player.state

            t = threading.Thread(
                target=lambda: c.post(
                    "/api/v1/bandcamp/proxy-fetch",
                    json={
                        "url": "https://bandcamp.com/api/test",
                        "method": "GET",
                        "headers": {},
                        "body": None,
                    },
                )
            )
            t.start()

            msg = ws.receive_json()
            req_id = msg["id"]

            # Answer the request.
            c.post(
                "/api/v1/bandcamp/fetch-result",
                json={
                    "id": req_id,
                    "status": 200,
                    "body": "done",
                    "content_type": "text/plain",
                },
            )
            t.join(timeout=5)

        # A second client connecting after the request is answered should NOT
        # receive the already-answered proxy-fetch event.
        with c.websocket_connect("/api/v1/ws") as ws2:
            ws2.receive_json()  # discard player.state
            # No further message should arrive — queue should be empty.
            import queue as _queue

            with TestClient(app).websocket_connect("/api/v1/ws") as ws3:
                ws3.receive_json()
                # Verify the pending dict is empty by confirming no replay events
                # arrive for a fresh client (the answered request must be gone).
                # We confirm indirectly: send a ping and get a player.state back,
                # not a proxy-fetch replay.
                ws3.send_text("ping")
                pong = ws3.receive_json()
                assert pong["type"] == "player.state"

    # -- URL allowlist tests -------------------------------------------------

    @pytest.mark.parametrize(
        "url",
        [
            "https://bandcamp.com/api/fan/2/collection_summary",
            "https://api.bandcamp.com/api/tralbum/2/info",
            "https://f4.bcbits.com/img/a1234567890_10.jpg",
            "https://t4.bcbits.com/stream/some-track",
        ],
    )
    def test_proxy_fetch_allows_bandcamp_urls(
        self,
        client: TestClient,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        url: str,
    ) -> None:
        """Legitimate Bandcamp hostnames must not be rejected by the allowlist."""
        import threading

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        responses: list = []

        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # discard player.state

            t = threading.Thread(
                target=lambda: responses.append(
                    c.post(
                        "/api/v1/bandcamp/proxy-fetch",
                        json={"url": url, "method": "GET", "headers": {}, "body": None},
                    )
                )
            )
            t.start()

            msg = ws.receive_json()
            req_id = msg["id"]

            c.post(
                "/api/v1/bandcamp/fetch-result",
                json={
                    "id": req_id,
                    "status": 200,
                    "body": "{}",
                    "content_type": "application/json",
                },
            )
            t.join(timeout=5)

        assert responses and responses[0].status_code == 200

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com/steal-cookies",
            "https://notbandcamp.com/api",
            "https://bandcamp.com.evil.com/api",
            "http://127.0.0.1:9000/internal",
            "https://bcbits.com/img/fake.jpg",
        ],
    )
    def test_proxy_fetch_rejects_non_bandcamp_urls(
        self, client: TestClient, url: str
    ) -> None:
        """Non-Bandcamp URLs must be rejected with 422 before any broadcast."""
        response = client.post(
            "/api/v1/bandcamp/proxy-fetch",
            json={"url": url, "method": "GET", "headers": {}, "body": None},
        )
        assert response.status_code == 422
        assert "not allowed" in response.json()["detail"]


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCORSMiddleware:
    def _make_client(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        dev_mode: bool = False,
    ) -> TestClient:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            dev_mode=dev_mode,
        )
        return TestClient(app, raise_server_exceptions=True)

    def _preflight(self, client: TestClient, origin: str) -> "requests.Response":
        return client.options(
            "/api/v1/albums",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

    def test_wildcard_not_used(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        client = self._make_client(mock_index, mock_engine, mock_queue)
        resp = self._preflight(client, "http://localhost")
        acao = resp.headers.get("access-control-allow-origin", "")
        assert acao != "*"

    def test_localhost_origin_allowed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        client = self._make_client(mock_index, mock_engine, mock_queue)
        resp = self._preflight(client, "http://localhost")
        assert resp.headers.get("access-control-allow-origin") == "http://localhost"

    def test_127_origin_allowed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        client = self._make_client(mock_index, mock_engine, mock_queue)
        resp = self._preflight(client, "http://127.0.0.1")
        assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1"

    def test_electron_null_origin_allowed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        # Electron's file:// renderer sends Origin: null (opaque origin serialization).
        client = self._make_client(mock_index, mock_engine, mock_queue)
        resp = self._preflight(client, "null")
        assert resp.headers.get("access-control-allow-origin") == "null"

    def test_arbitrary_origin_rejected(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        client = self._make_client(mock_index, mock_engine, mock_queue)
        resp = self._preflight(client, "https://evil.example.com")
        assert "access-control-allow-origin" not in resp.headers

    def test_vite_origin_blocked_without_dev_mode(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        client = self._make_client(mock_index, mock_engine, mock_queue, dev_mode=False)
        resp = self._preflight(client, "http://localhost:5173")
        assert "access-control-allow-origin" not in resp.headers

    def test_vite_origin_allowed_in_dev_mode(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        client = self._make_client(mock_index, mock_engine, mock_queue, dev_mode=True)
        resp = self._preflight(client, "http://localhost:5173")
        assert (
            resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
        )
