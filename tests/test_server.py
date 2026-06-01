"""Tests for kamp_core.server (REST API and WebSocket)."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kamp_core.library import AlbumInfo, Track
from kamp_core.playback import PlaybackState
from kamp_core.server import create_app, resolve_playback_uri

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
    queue.peek_next.return_value = None
    queue.queue_tracks.return_value = ([], -1)
    queue.shuffle = False
    queue.repeat = False
    return queue


@pytest.fixture()
def client(
    mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
) -> TestClient:
    app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth token middleware
# ---------------------------------------------------------------------------


class TestAuthToken:
    def test_no_auth_token_allows_all_requests(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """When auth_token is not set, all requests pass through."""
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        assert c.get("/api/v1/albums").status_code == 200

    def test_request_without_token_returns_401(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        assert c.get("/api/v1/albums").status_code == 401

    def test_request_with_correct_token_succeeds(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        assert (
            c.get("/api/v1/albums", headers={"X-Kamp-Token": "secret"}).status_code
            == 200
        )

    def test_request_with_token_query_param_succeeds(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Token in query param accepted (needed for <img src> album-art URLs)."""
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        assert c.get("/api/v1/albums?token=secret").status_code == 200

    def test_request_with_wrong_token_returns_401(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        assert (
            c.get("/api/v1/albums", headers={"X-Kamp-Token": "wrong"}).status_code
            == 401
        )

    def test_options_bypasses_auth(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """CORS preflight OPTIONS requests are never rejected by auth."""
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        # TestClient follows CORS — a plain OPTIONS to a real endpoint should not 401.
        res = c.options("/api/v1/albums", headers={"Origin": "http://localhost"})
        assert res.status_code != 401

    def test_websocket_with_correct_token_accepted(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_engine.state = mock_engine.state.__class__()
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws?token=secret") as ws:
            msg = ws.receive_json()
        assert msg["type"] == "player.state"

    def test_websocket_with_token_header_accepted(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Electron's webRequest interceptor injects the token as a header."""
        mock_engine.state = mock_engine.state.__class__()
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        with c.websocket_connect(
            "/api/v1/ws", headers={"X-Kamp-Token": "secret"}
        ) as ws:
            msg = ws.receive_json()
        assert msg["type"] == "player.state"

    def test_websocket_without_token_rejected(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        with pytest.raises(Exception):
            with c.websocket_connect("/api/v1/ws"):
                pass  # pragma: no cover

    def test_websocket_with_wrong_token_rejected(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, auth_token="secret"
        )
        c = TestClient(app)
        with pytest.raises(Exception):
            with c.websocket_connect("/api/v1/ws?token=wrong"):
                pass  # pragma: no cover


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

    def test_cover_file_preference_serves_cover_file(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """save_format=cover-file: cover file is served when present."""
        track = _track(1)
        track.embedded_art = False
        mock_index.tracks_for_album.return_value = [track]
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"artwork.save_format": "cover-file"},
        )
        c = TestClient(app)
        with patch(
            "kamp_daemon.artwork.read_cover_file",
            return_value=(b"COVERDATA", "image/jpeg"),
        ):
            res = c.get("/api/v1/album-art?album_artist=Artist&album=Album")
        assert res.status_code == 200
        assert res.content == b"COVERDATA"

    def test_cover_file_preference_falls_back_to_embedded(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """save_format=cover-file: embedded art is used when no cover file exists."""
        track = _track(1)
        track.embedded_art = True
        mock_index.tracks_for_album.return_value = [track]
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"artwork.save_format": "cover-file"},
        )
        c = TestClient(app)
        with (
            patch("kamp_daemon.artwork.read_cover_file", return_value=None),
            patch(
                "kamp_core.server.extract_art", return_value=(b"EMBEDDED", "image/jpeg")
            ),
        ):
            res = c.get("/api/v1/album-art?album_artist=Artist&album=Album")
        assert res.status_code == 200
        assert res.content == b"EMBEDDED"

    def test_embedded_preference_falls_back_to_cover_file(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """save_format=embedded: cover file is used when no embedded art exists."""
        track = _track(1)
        track.embedded_art = False
        mock_index.tracks_for_album.return_value = [track]
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"artwork.save_format": "embedded"},
        )
        c = TestClient(app)
        with patch(
            "kamp_daemon.artwork.read_cover_file",
            return_value=(b"COVERDATA", "image/jpeg"),
        ):
            res = c.get("/api/v1/album-art?album_artist=Artist&album=Album")
        assert res.status_code == 200
        assert res.content == b"COVERDATA"


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
        # Server resolves the path before lookup; on Windows that prepends the
        # current drive letter, so assert against the same resolved form.
        mock_index.get_track_by_path.assert_called_once_with(
            Path("/music/01.mp3").resolve()
        )
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
        mock_index.get_track_by_path.assert_called_once_with(
            Path("/music/01.mp3").resolve()
        )
        mock_index.tracks_for_album.assert_not_called()


# ---------------------------------------------------------------------------
# Path containment validation
# ---------------------------------------------------------------------------


class TestPathContainmentValidation:
    """file_path parameters must resolve within the configured library directory."""

    def _client(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        library_path: Path = Path("/music"),
    ) -> TestClient:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            library_path=library_path,
        )
        return TestClient(app)

    def test_tracks_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.get("/api/v1/tracks?album_artist=&album=&file_path=/etc/passwd")
        assert resp.status_code == 400
        mock_index.get_track_by_path.assert_not_called()

    def test_tracks_rejects_traversal_path(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.get(
            "/api/v1/tracks?album_artist=&album=&file_path=/music/../etc/passwd"
        )
        assert resp.status_code == 400

    def test_tracks_accepts_valid_library_path(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_track_by_path.return_value = _track(1)
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.get(
            "/api/v1/tracks?album_artist=&album=&file_path=/music/artist/01.mp3"
        )
        assert resp.status_code == 200

    def test_favorite_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.post(
            "/api/v1/tracks/favorite",
            json={"file_path": "/etc/passwd", "favorite": True},
        )
        assert resp.status_code == 400
        mock_index.get_track_by_path.assert_not_called()

    def test_album_art_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.get("/api/v1/album-art?album_artist=&album=&file_path=/etc/passwd")
        assert resp.status_code == 400
        mock_index.get_track_by_path.assert_not_called()

    def test_play_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.post(
            "/api/v1/player/play",
            json={"file_path": "/etc/passwd", "album_artist": "", "album": ""},
        )
        assert resp.status_code == 400
        mock_index.get_track_by_path.assert_not_called()

    def test_queue_add_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.post("/api/v1/player/queue/add", json={"file_path": "/etc/passwd"})
        assert resp.status_code == 400
        mock_index.get_track_by_path.assert_not_called()

    def test_queue_play_next_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.post(
            "/api/v1/player/queue/play-next", json={"file_path": "/etc/passwd"}
        )
        assert resp.status_code == 400

    def test_queue_insert_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.post(
            "/api/v1/player/queue/insert",
            json={"file_path": "/etc/passwd", "index": 0},
        )
        assert resp.status_code == 400

    def test_queue_add_album_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.post(
            "/api/v1/player/queue/add-album",
            json={"file_path": "/etc/passwd", "album_artist": "", "album": ""},
        )
        assert resp.status_code == 400

    def test_queue_play_album_next_rejects_path_outside_library(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._client(mock_index, mock_engine, mock_queue)
        resp = c.post(
            "/api/v1/player/queue/play-album-next",
            json={"file_path": "/etc/passwd", "album_artist": "", "album": ""},
        )
        assert resp.status_code == 400

    def test_no_validation_when_library_path_not_configured(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_track_by_path.return_value = _track(1)
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        resp = c.get("/api/v1/tracks?album_artist=&album=&file_path=/music/01.mp3")
        assert resp.status_code == 200


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
        assert data["num_albums"] == 0
        assert data["num_artists"] == 0

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

    def test_progress_exposes_track_data(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        captured: list[Any] = []

        def _fake_scan(path: object, on_progress: Any = None) -> MagicMock:
            captured.append(on_progress)
            return MagicMock(added=1, removed=0, unchanged=0)

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

        # Invoke the captured callback with two tracks to simulate scan progress.
        track_a = _track(1, artist="Aphex Twin", album="Selected Ambient Works")
        track_a.title = "Xtal"
        track_b = _track(2, artist="Aphex Twin", album="Selected Ambient Works")
        track_b.title = "Tha"
        captured[0](1, 2, track_a)
        captured[0](2, 2, track_b)

        data = c.get("/api/v1/library/scan/progress").json()
        assert data["current_file"] == "Tha"
        assert data["current_artist"] == "Aphex Twin"
        assert data["top_artist"] == "Aphex Twin"
        assert data["num_artists"] == 1
        assert data["num_albums"] == 1


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
        mock_engine.play.assert_called_once_with(str(tracks[0].file_path))

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
        mock_engine.play.assert_called_once_with(str(next_track.file_path))

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
        mock_engine.play.assert_called_once_with(str(prev_track.file_path))

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
        assert data["shuffle"] is False
        assert data["repeat"] is False

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

    def test_queue_response_includes_shuffle_and_repeat_flags(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_queue.shuffle = True
        mock_queue.repeat = False
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/player/queue").json()
        assert data["shuffle"] is True
        assert data["repeat"] is False


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

    def test_remove_from_queue_calls_remove_at_with_indices(
        self, client: TestClient, mock_queue: MagicMock
    ) -> None:
        resp = client.post("/api/v1/player/queue/remove", json={"indices": [2, 4]})
        assert resp.status_code == 200
        mock_queue.remove_at.assert_called_once_with([2, 4])

    def test_skip_to_calls_engine_play(
        self, client: TestClient, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(3)
        mock_queue.skip_to.return_value = t
        resp = client.post("/api/v1/player/queue/skip-to", json={"position": 3})
        assert resp.status_code == 200
        mock_queue.skip_to.assert_called_once_with(3)
        mock_engine.play.assert_called_once_with(str(t.file_path))

    def test_skip_to_invalid_position_does_not_play(
        self, client: TestClient, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_queue.skip_to.return_value = None
        resp = client.post("/api/v1/player/queue/skip-to", json={"position": 99})
        assert resp.status_code == 200
        mock_engine.play.assert_not_called()

    def test_add_to_queue_starts_playback_when_stopped(
        self,
        client: TestClient,
        mock_index: MagicMock,
        mock_queue: MagicMock,
        mock_engine: MagicMock,
    ) -> None:
        t = _track(1)
        mock_index.get_track_by_path.return_value = t
        # Three calls: was_stopped check, current after mutation, _state_snapshot in notify
        mock_queue.current.side_effect = [None, t, t]
        resp = client.post(
            "/api/v1/player/queue/add", json={"file_path": str(t.file_path)}
        )
        assert resp.status_code == 200
        mock_engine.play.assert_called_once_with(str(t.file_path))
        mock_engine.preload_next.assert_not_called()

    def test_play_next_starts_playback_when_stopped(
        self,
        client: TestClient,
        mock_index: MagicMock,
        mock_queue: MagicMock,
        mock_engine: MagicMock,
    ) -> None:
        t = _track(2)
        mock_index.get_track_by_path.return_value = t
        # Three calls: was_stopped check, current after mutation, _state_snapshot in notify
        mock_queue.current.side_effect = [None, t, t]
        resp = client.post(
            "/api/v1/player/queue/play-next", json={"file_path": str(t.file_path)}
        )
        assert resp.status_code == 200
        mock_engine.play.assert_called_once_with(str(t.file_path))
        mock_engine.preload_next.assert_not_called()


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

    def test_add_album_starts_playback_when_stopped(
        self,
        client: TestClient,
        mock_index: MagicMock,
        mock_queue: MagicMock,
        mock_engine: MagicMock,
    ) -> None:
        ts = [_track(i) for i in range(3)]
        mock_index.tracks_for_album.return_value = ts
        # Three calls: was_stopped check, current after mutation, _state_snapshot in notify
        mock_queue.current.side_effect = [None, ts[0], ts[0]]
        resp = client.post(
            "/api/v1/player/queue/add-album",
            json={"album_artist": "Artist", "album": "Album"},
        )
        assert resp.status_code == 200
        mock_engine.play.assert_called_once_with(str(ts[0].file_path))
        mock_engine.preload_next.assert_not_called()

    def test_play_album_next_starts_playback_when_stopped(
        self,
        client: TestClient,
        mock_index: MagicMock,
        mock_queue: MagicMock,
        mock_engine: MagicMock,
    ) -> None:
        ts = [_track(i) for i in range(3)]
        mock_index.tracks_for_album.return_value = ts
        # Three calls: was_stopped check, current after mutation, _state_snapshot in notify
        mock_queue.current.side_effect = [None, ts[0], ts[0]]
        resp = client.post(
            "/api/v1/player/queue/play-album-next",
            json={"album_artist": "Artist", "album": "Album"},
        )
        assert resp.status_code == 200
        mock_engine.play.assert_called_once_with(str(ts[0].file_path))
        mock_engine.preload_next.assert_not_called()


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

    def test_websocket_engine_on_audio_level_wired(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """create_app wires engine.on_audio_level to the broadcast notifier."""
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        assert mock_engine.on_audio_level is not None
        assert callable(mock_engine.on_audio_level)

    def test_audio_level_broadcast(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """engine.on_audio_level fires a WebSocket audio.level message."""
        mock_engine.state = PlaybackState()
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            mock_engine.on_audio_level(-18.5, -19.1, 12.4, -6.1)
            msg = ws.receive_json()
        assert msg["type"] == "audio.level"
        assert msg["left_db"] == pytest.approx(-18.5)
        assert msg["right_db"] == pytest.approx(-19.1)
        assert msg["crest_db"] == pytest.approx(12.4)
        assert msg["peak_db"] == pytest.approx(-6.1)

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

    def test_relative_path_returns_422(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        c = self._make_client(mock_index, mock_engine, mock_queue)
        for bad in ("music", "../music", "relative/path"):
            res = c.post("/api/v1/config/library-path", json={"path": bad})
            assert res.status_code == 422, f"expected 422 for {bad!r}"

    @pytest.mark.parametrize(
        "forbidden",
        (
            [
                r"C:\Windows",
                r"C:\Windows\System32",
                r"C:\Program Files",
                r"C:\Program Files (x86)",
                r"C:\ProgramData",
                r"C:\Users",
                "C:\\",
            ]
            if sys.platform == "win32"
            else ["/", "/etc", "/System", "/usr", "/bin", "/Library", "/Applications"]
        ),
    )
    def test_forbidden_system_roots_return_422(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        forbidden: str,
    ) -> None:
        c = self._make_client(mock_index, mock_engine, mock_queue)
        res = c.post("/api/v1/config/library-path", json={"path": forbidden})
        assert res.status_code == 422


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

    def test_remote_track_appears_in_results(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1, album="The Moon Rang Like a Bell", artist="Hundred Waters")
        t.source = "bandcamp"
        t.file_path = Path("bandcamp://12345/01.mp3")
        remote_album = _album("Hundred Waters", "The Moon Rang Like a Bell")
        remote_album.source = "bandcamp"
        mock_index.search.return_value = [t]
        mock_index.albums.return_value = [remote_album]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=hundred+waters")
        assert res.status_code == 200
        data = res.json()
        assert len(data["tracks"]) == 1
        assert data["tracks"][0]["source"] == "bandcamp"
        assert len(data["albums"]) == 1
        assert data["albums"][0]["source"] == "bandcamp"


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

    def test_set_active_view_home_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/v1/ui/active-view", json={"view": "home"})
        assert resp.status_code == 200
        assert client.get("/api/v1/ui").json()["active_view"] == "home"

    def test_set_active_view_invalid_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/ui/active-view", json={"view": "bogus"})
        assert resp.status_code == 422

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
        mock_index.set_favorite.assert_called_once_with(
            Path("/music/01.mp3").resolve(), True
        )
        # Queue must also be updated so the next player-state snapshot is correct.
        mock_queue.update_favorite.assert_called_once_with(
            Path("/music/01.mp3").resolve(), True
        )

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
# Album favorite endpoint (KAMP-293)
# ---------------------------------------------------------------------------


class TestAlbumFavoriteEndpoint:
    def test_set_album_favorite_endpoint(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/albums/favorite",
            json={"album_artist": "Artist", "album": "Album", "favorite": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_index.toggle_album_favorite.assert_called_once_with(
            "Artist", "Album", True
        )

    def test_album_out_includes_favorite_field(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [_album("Artist", "Album")]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        album = TestClient(app).get("/api/v1/albums").json()[0]
        assert "favorite" in album
        assert album["favorite"] is False

    def test_album_out_reflects_favorited_album(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        a = _album("Artist", "Album")
        a.favorite = True
        mock_index.albums.return_value = [a]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        album = TestClient(app).get("/api/v1/albums").json()[0]
        assert album["favorite"] is True


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------

_SAMPLE_CONFIG_VALUES = {
    "paths.watch_folder": "~/Music/staging",
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
        assert data["paths.watch_folder"] == "~/Music/staging"
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

    def test_login_complete_accepts_full_electron_cookie_shape(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Regression for KAMP-282: the full Electron payload shape must validate.

        The renderer (`kamp_ui/src/main/index.ts`) sends every field Chromium's
        cookie store returns — including a float `expires`, capitalised
        `sameSite`, and `httpOnly`/`secure` booleans.  The Pydantic model is
        permissive (`list[dict[str, Any]]`) and the handler must accept it.
        """
        cookies = [
            {
                "name": "session",
                "value": "abc123",
                "domain": ".bandcamp.com",
                "path": "/",
                "expires": 1893456000.123,  # float, not int
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "js_logged_in",
                "value": "1",
                "domain": ".bandcamp.com",
                "path": "/",
                "expires": -1,  # session cookie
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            },
        ]
        captured: dict[str, Any] = {}

        def _capture(payload: dict[str, Any]) -> None:
            captured["payload"] = payload

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"bandcamp.connected": False, "bandcamp.username": None},
            on_bandcamp_login_complete=_capture,
        )
        c = TestClient(app)
        resp = c.post(
            "/api/v1/bandcamp/login-complete",
            json={"cookies": cookies, "origins": []},
        )
        assert resp.status_code == 200, resp.text
        assert captured["payload"]["cookies"] == cookies

    def test_login_complete_returns_422_when_callback_raises(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Callback exceptions surface as 422 with the message in `detail`.

        Documents the contract instrumented for KAMP-282 — the handler must
        log the traceback (verified by capturing logs) but still return 422
        with the exception message so the renderer can show it.
        """

        def _boom(payload: dict[str, Any]) -> None:
            raise RuntimeError("simulated keyring backend failure")

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_bandcamp_login_complete=_boom,
        )
        c = TestClient(app)
        resp = c.post(
            "/api/v1/bandcamp/login-complete",
            json={"cookies": [{"name": "x", "value": "y"}], "origins": []},
        )
        assert resp.status_code == 422
        assert "simulated keyring backend failure" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Bandcamp manual sync endpoint
# ---------------------------------------------------------------------------


class TestBandcampSync:
    """Tests for POST /api/v1/bandcamp/sync."""

    def test_sync_returns_503_when_no_trigger_configured(
        self, client: TestClient
    ) -> None:
        resp = client.post("/api/v1/bandcamp/sync")
        assert resp.status_code == 503

    def test_sync_fires_trigger_and_returns_ok(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        called: list[bool] = []
        trigger_done = threading.Event()

        def _trigger() -> None:
            called.append(True)
            trigger_done.set()

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_bandcamp_sync_trigger=_trigger,
        )
        with TestClient(app) as c:
            resp = c.post("/api/v1/bandcamp/sync")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        trigger_done.wait(timeout=2)
        assert called == [True]

    def test_notify_bandcamp_sync_status_exposed_on_app_state(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        assert callable(getattr(app.state, "notify_bandcamp_sync_status", None))

    def test_notify_pipeline_stage_exposed_on_app_state(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        assert callable(getattr(app.state, "notify_pipeline_stage", None))


# ---------------------------------------------------------------------------
# Bandcamp sync-all endpoint
# ---------------------------------------------------------------------------


class TestBandcampSyncAll:
    """Tests for POST /api/v1/bandcamp/sync-all."""

    def test_sync_all_returns_503_when_no_trigger_configured(
        self, client: TestClient
    ) -> None:
        resp = client.post("/api/v1/bandcamp/sync-all")
        assert resp.status_code == 503

    def test_sync_all_fires_trigger_and_returns_ok(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        called: list[bool] = []
        trigger_done = threading.Event()

        def _trigger() -> None:
            called.append(True)
            trigger_done.set()

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_bandcamp_sync_all_trigger=_trigger,
        )
        with TestClient(app) as c:
            resp = c.post("/api/v1/bandcamp/sync-all")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        trigger_done.wait(timeout=2)
        assert called == [True]


# ---------------------------------------------------------------------------
# Bandcamp collection item download endpoint
# ---------------------------------------------------------------------------


class TestBandcampCollectionDownload:
    """Tests for POST /api/v1/bandcamp/collection/{sale_item_id}/download."""

    def test_returns_404_when_item_not_in_collection(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_collection_item.return_value = None
        trigger_calls: list[str] = []
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_album_download_trigger=lambda sid: trigger_calls.append(sid),
        )
        resp = TestClient(app).post("/api/v1/bandcamp/collection/99999/download")
        assert resp.status_code == 404
        assert trigger_calls == []

    def test_returns_503_when_download_trigger_not_configured(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_collection_item.return_value = {"sale_item_id": "42"}
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post("/api/v1/bandcamp/collection/42/download")
        assert resp.status_code == 503

    def test_sets_db_state_and_fires_download_trigger(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "remote",
        }
        trigger_done = threading.Event()
        trigger_calls: list[str] = []

        def _trigger(sid: str) -> None:
            trigger_calls.append(sid)
            trigger_done.set()

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            on_album_download_trigger=_trigger,
        )
        with TestClient(app) as c:
            resp = c.post("/api/v1/bandcamp/collection/42/download")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_index.set_collection_item_mode.assert_called_once_with("42", "local")
        mock_index.set_track_source_for_item.assert_called_once_with("42", "local")
        trigger_done.wait(timeout=2)
        assert trigger_calls == ["42"]

    def test_notify_album_download_status_exposed_on_app_state(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        assert callable(getattr(app.state, "notify_album_download_status", None))


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

    def test_proxy_fetch_timeout_removes_from_pending(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Timed-out proxy-fetch is removed from pending so it is not replayed.

        This is the TASK-181 crash-loop fix: without the pop(), a timed-out
        request stays in _pending_proxy_fetches and is re-delivered to every
        new WS client, causing an infinite crash loop.
        """
        import threading
        from threading import Event as _RealEvent
        from unittest.mock import patch

        # Patch threading.Event as seen from kamp_core.server so the per-request
        # event times out immediately.  wait(timeout=None) is used by
        # Thread._started so we only return False for bounded waits (the
        # proxy-fetch handler always passes a 60.0 timeout).
        class _ImmediateTimeoutEvent(_RealEvent):
            def wait(self, timeout=None):  # type: ignore[override]
                if timeout is None:
                    return super().wait()
                return False

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        proxy_response: dict = {}

        with patch("kamp_core.server._threading.Event", _ImmediateTimeoutEvent):
            with c.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # discard player.state

                t = threading.Thread(
                    target=lambda: proxy_response.update(
                        {
                            "resp": c.post(
                                "/api/v1/bandcamp/proxy-fetch",
                                json={
                                    "url": "https://bandcamp.com/api/fan/2/collection_summary",
                                    "method": "GET",
                                    "headers": {},
                                    "body": None,
                                },
                            )
                        }
                    )
                )
                t.start()
                t.join(timeout=5)

        assert proxy_response["resp"].status_code == 504

        # A new WS client connecting after the timeout must NOT receive the
        # timed-out request as a replay event.
        with c.websocket_connect("/api/v1/ws") as ws2:
            ws2.receive_json()  # discard player.state
            ws2.send_text("ping")
            pong = ws2.receive_json()
            assert pong["type"] == "player.state"  # no replay event


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

    def test_vite_alternate_port_allowed_in_dev_mode(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Vite rolls forward to 5174/5175/... when 5173 is occupied (e.g. a
        stale dev session). dev_mode CORS must accept any localhost port so
        the renderer keeps working across restarts."""
        client = self._make_client(mock_index, mock_engine, mock_queue, dev_mode=True)
        resp = self._preflight(client, "http://localhost:5174")
        assert (
            resp.headers.get("access-control-allow-origin") == "http://localhost:5174"
        )

    def test_non_localhost_origin_rejected_even_in_dev_mode(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """The dev regex must only match localhost/127.0.0.1, not arbitrary
        origins. Otherwise an attacker on the LAN could hit the dev daemon."""
        client = self._make_client(mock_index, mock_engine, mock_queue, dev_mode=True)
        resp = self._preflight(client, "http://192.168.1.10:5173")
        assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# PATCH /api/v1/albums/meta (KAMP-303)
# ---------------------------------------------------------------------------


class TestPatchAlbumMetaEndpoint:
    """PATCH /api/v1/albums/meta writes genre/label/year tags to album tracks."""

    def _make_track(self, n: int = 1) -> Track:
        return Track(
            file_path=Path(f"/music/{n:02d}.mp3"),
            title=f"Track {n}",
            artist="Artist",
            album_artist="Artist",
            album="Record",
            year="2020",
            track_number=n,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="",
            label="",
        )

    def test_patch_genre_writes_tag_and_returns_updated_tracks(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = self._make_track()
        updated = Track(**{**track.__dict__, "genre": "Jazz"})
        mock_index.tracks_for_album.return_value = [track]
        mock_index.update_album_meta.return_value = [updated]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch("kamp_core.library.write_meta_tags_to_file"):
            resp = TestClient(app).patch(
                "/api/v1/albums/meta",
                params={"album_artist": "Artist", "album": "Record"},
                json={"genre": "Jazz"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tracks"]) == 1
        assert data["tracks"][0]["genre"] == "Jazz"

    def test_patch_label_and_year_persisted(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = self._make_track()
        updated = Track(**{**track.__dict__, "label": "ECM", "year": "1975"})
        mock_index.tracks_for_album.return_value = [track]
        mock_index.update_album_meta.return_value = [updated]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch("kamp_core.library.write_meta_tags_to_file"):
            resp = TestClient(app).patch(
                "/api/v1/albums/meta",
                params={"album_artist": "Artist", "album": "Record"},
                json={"label": "ECM", "year": "1975"},
            )

        assert resp.status_code == 200
        mock_index.update_album_meta.assert_called_once_with(
            "Artist", "Record", genre=None, label="ECM", year="1975", mb_release_id=None
        )

    def test_returns_404_for_unknown_album(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = []

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/albums/meta",
            params={"album_artist": "Ghost", "album": "Void"},
            json={"genre": "Noise"},
        )
        assert resp.status_code == 404

    def test_returns_400_when_no_fields_provided(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = self._make_track()
        mock_index.tracks_for_album.return_value = [track]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/albums/meta",
            params={"album_artist": "Artist", "album": "Record"},
            json={},
        )
        assert resp.status_code == 400

    def test_returns_500_when_tag_write_fails(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = self._make_track()
        mock_index.tracks_for_album.return_value = [track]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch(
            "kamp_core.library.write_meta_tags_to_file",
            side_effect=OSError("permission denied"),
        ):
            resp = TestClient(app).patch(
                "/api/v1/albums/meta",
                params={"album_artist": "Artist", "album": "Record"},
                json={"genre": "Rock"},
            )
        assert resp.status_code == 500

    def test_track_out_includes_genre_and_label(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """TrackOut model must expose genre and label fields."""
        track = Track(
            file_path=Path("/music/01.mp3"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Record",
            year="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="Reggae",
            label="Trojan",
        )
        mock_index.tracks_for_album.return_value = [track]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        tracks_resp = TestClient(app).get(
            "/api/v1/tracks",
            params={"album_artist": "Artist", "album": "Record"},
        )
        assert tracks_resp.status_code == 200
        t = tracks_resp.json()[0]
        assert t["genre"] == "Reggae"
        assert t["label"] == "Trojan"

    def test_track_out_includes_source(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = _track(1)
        track.source = "bandcamp"
        mock_index.tracks_for_album.return_value = [track]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).get(
            "/api/v1/tracks",
            params={"album_artist": track.album_artist, "album": track.album},
        )
        assert resp.status_code == 200
        assert resp.json()[0]["source"] == "bandcamp"

    def test_track_out_includes_reachable_field(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = _track(1)
        mock_index.tracks_for_album.return_value = [track]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).get(
            "/api/v1/tracks",
            params={"album_artist": track.album_artist, "album": track.album},
        )
        assert resp.status_code == 200
        assert resp.json()[0]["reachable"] is True

    def test_track_out_stub_track_reachable_false(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """Stub tracks created during queue restore expose reachable=False."""
        from pathlib import Path as _Path

        from kamp_core.library import Track as _Track
        from kamp_core.server import TrackOut

        stub = _Track(
            file_path=_Path("bandcamp://777/1"),
            title="777/1",
            artist="",
            album_artist="",
            album="",
            year="",
            track_number=0,
            disc_number=0,
            ext="",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            reachable=False,
        )
        out = TrackOut.from_track(stub)
        assert out.reachable is False

    def test_album_out_includes_source_and_has_remote_tracks(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        album = _album("Tycho", "Dive")
        album.source = "bandcamp"
        album.has_remote_tracks = True
        mock_index.albums.return_value = [album]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        data = TestClient(app).get("/api/v1/albums").json()
        assert data[0]["source"] == "bandcamp"
        assert data[0]["has_remote_tracks"] is True


# ---------------------------------------------------------------------------
# iTunes art search / apply (KAMP-341)
# ---------------------------------------------------------------------------

_ITUNES_CANDIDATE = {
    "title": "Up Your Alley",
    "artist": "Joan Jett & The Blackhearts",
    "artwork_url_template": (
        "https://is1-ssl.mzstatic.com/image/thumb/Music115/v4/49/f7/8b/"
        "49f78bb0/cover.jpg/{size}.jpg"
    ),
    "preview_url": (
        "https://is1-ssl.mzstatic.com/image/thumb/Music115/v4/49/f7/8b/"
        "49f78bb0/cover.jpg/200x200bb.jpg"
    ),
}

_MZSTATIC_TEMPLATE = (
    "https://is1-ssl.mzstatic.com/image/thumb/Music115/v4/49/f7/8b/"
    "49f78bb0/cover.jpg/{size}.jpg"
)


class TestItunesArtSearchEndpoint:
    def test_returns_candidates_from_itunes(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        from kamp_daemon.artwork import ItunesCandidate

        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        candidate = ItunesCandidate(**_ITUNES_CANDIDATE)
        with patch("kamp_daemon.artwork.search_itunes", return_value=[candidate]):
            res = c.get(
                "/api/v1/albums/art/search",
                params={"album_artist": "Joan Jett", "album": "Up Your Alley"},
            )

        assert res.status_code == 200
        body = res.json()
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["title"] == "Up Your Alley"

    def test_returns_empty_candidates_on_no_itunes_results(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        with patch("kamp_daemon.artwork.search_itunes", return_value=[]):
            res = c.get(
                "/api/v1/albums/art/search",
                params={"album_artist": "Unknown", "album": "Obscure"},
            )

        assert res.status_code == 200
        assert res.json()["candidates"] == []

    def test_returns_404_when_album_not_in_library(self, client: TestClient) -> None:
        res = client.get(
            "/api/v1/albums/art/search",
            params={"album_artist": "Ghost", "album": "Nobody"},
        )
        assert res.status_code == 404

    def test_returns_502_on_artwork_error(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        from kamp_daemon.artwork import ArtworkError

        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        with patch(
            "kamp_daemon.artwork.search_itunes",
            side_effect=ArtworkError("timeout"),
        ):
            res = c.get(
                "/api/v1/albums/art/search",
                params={"album_artist": "Joan Jett", "album": "Up Your Alley"},
            )

        assert res.status_code == 502


class TestItunesArtApplyEndpoint:
    def _make_app(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        has_art: bool = True,
    ) -> TestClient:
        import io

        from PIL import Image

        mock_index.tracks_for_album.return_value = [_track(1)]
        album_info = _album("Joan Jett", "Up Your Alley", has_art=has_art)
        album_info = AlbumInfo(
            album_artist="Joan Jett",
            album="Up Your Alley",
            year="1988",
            track_count=1,
            has_art=has_art,
            art_version=12345.0,
        )
        mock_index.albums.return_value = [album_info]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        return TestClient(app)

    def _valid_payload(self) -> dict[str, str]:
        return {
            "album_artist": "Joan Jett",
            "album": "Up Your Alley",
            "artwork_url_template": _MZSTATIC_TEMPLATE,
        }

    def _make_jpeg_bytes(self, w: int = 600, h: int = 600) -> bytes:
        import io

        from PIL import Image

        img = Image.new("RGB", (w, h), color=(128, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_happy_path_returns_album_out(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._make_app(mock_index, mock_engine, mock_queue, has_art=True)
        image_bytes = self._make_jpeg_bytes()

        with (
            patch("kamp_daemon.artwork.fetch_itunes_image", return_value=image_bytes),
            patch("kamp_daemon.artwork._embed"),
        ):
            res = c.post("/api/v1/albums/art/apply", json=self._valid_payload())

        assert res.status_code == 200
        body = res.json()
        assert body["album"] == "Up Your Alley"
        assert body["has_art"] is True
        mock_index.mark_album_art_embedded.assert_called_once()

    def test_notify_library_changed_is_called(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._make_app(mock_index, mock_engine, mock_queue)
        image_bytes = self._make_jpeg_bytes()

        with (
            patch("kamp_daemon.artwork.fetch_itunes_image", return_value=image_bytes),
            patch("kamp_daemon.artwork._embed"),
        ):
            res = c.post("/api/v1/albums/art/apply", json=self._valid_payload())

        assert res.status_code == 200
        # _notify_library_changed broadcasts via WebSocket connections; we verify
        # the index broadcast was attempted (no active WS here, so call is a no-op).

    def test_returns_400_for_non_mzstatic_url(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        payload = {
            **self._valid_payload(),
            "artwork_url_template": "https://evil.com/art.jpg",
        }
        res = c.post("/api/v1/albums/art/apply", json=payload)
        assert res.status_code == 400

    def test_returns_400_for_non_https_url(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        payload = {
            **self._valid_payload(),
            "artwork_url_template": "file:///etc/passwd",
        }
        res = c.post("/api/v1/albums/art/apply", json=payload)
        assert res.status_code == 400

    def test_returns_404_when_album_not_found(self, client: TestClient) -> None:
        res = client.post(
            "/api/v1/albums/art/apply",
            json={
                "album_artist": "Ghost",
                "album": "Nobody",
                "artwork_url_template": _MZSTATIC_TEMPLATE,
            },
        )
        assert res.status_code == 404

    def test_returns_409_when_track_is_locked(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        locked_track = _track(1)
        mock_index.tracks_for_album.return_value = [locked_track]
        mock_queue.current.return_value = locked_track  # track 1 is playing
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        res = c.post("/api/v1/albums/art/apply", json=self._valid_payload())
        assert res.status_code == 409

    def test_returns_422_when_image_below_min_dimension(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        from kamp_daemon.artwork import ArtworkError

        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        with patch(
            "kamp_daemon.artwork.fetch_itunes_image",
            side_effect=ArtworkError("below minimum 500px"),
        ):
            res = c.post("/api/v1/albums/art/apply", json=self._valid_payload())

        assert res.status_code == 422

    def test_returns_502_when_download_fails(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        from kamp_daemon.artwork import ArtworkError

        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        with patch(
            "kamp_daemon.artwork.fetch_itunes_image",
            side_effect=ArtworkError("Could not download"),
        ):
            res = c.post("/api/v1/albums/art/apply", json=self._valid_payload())

        assert res.status_code == 502

    def test_cover_file_mode_writes_cover_file_not_embed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """In cover-file mode, art is written to a cover file instead of embedded."""
        image_bytes = self._make_jpeg_bytes()
        c = self._make_app(mock_index, mock_engine, mock_queue, has_art=True)
        # Re-create with cover-file preference.
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"artwork.save_format": "cover-file"},
        )
        mock_index.albums.return_value = [
            AlbumInfo(
                album_artist="Joan Jett",
                album="Up Your Alley",
                year="1988",
                track_count=1,
                has_art=True,
                art_version=12345.0,
            )
        ]
        c = TestClient(app)

        with (
            patch("kamp_daemon.artwork.fetch_itunes_image", return_value=image_bytes),
            patch("kamp_daemon.artwork.write_cover_file") as mock_write,
        ):
            res = c.post("/api/v1/albums/art/apply", json=self._valid_payload())

        assert res.status_code == 200
        mock_write.assert_called_once()
        mock_index.mark_album_art_embedded.assert_called_once()


class TestApplyLocalAlbumArt:
    def _make_jpeg_bytes(self, w: int = 600, h: int = 600) -> bytes:
        import io

        from PIL import Image

        img = Image.new("RGB", (w, h), color=(0, 64, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def _make_app(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        has_art: bool = False,
    ) -> TestClient:
        mock_index.tracks_for_album.return_value = [_track(1)]
        mock_index.albums.return_value = [
            AlbumInfo(
                album_artist="Joan Jett",
                album="Up Your Alley",
                year="1988",
                track_count=1,
                has_art=has_art,
                art_version=99.0,
            )
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        return TestClient(app)

    def test_happy_path_returns_album_out(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        c = self._make_app(mock_index, mock_engine, mock_queue, has_art=True)
        image_bytes = self._make_jpeg_bytes()

        with patch("kamp_daemon.artwork._embed"):
            res = c.post(
                "/api/v1/albums/art/apply-local",
                data={"album_artist": "Joan Jett", "album": "Up Your Alley"},
                files={"file": ("cover.jpg", image_bytes, "image/jpeg")},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["album"] == "Up Your Alley"
        assert body["has_art"] is True
        mock_index.mark_album_art_embedded.assert_called_once()

    def test_returns_404_when_album_not_found(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        res = c.post(
            "/api/v1/albums/art/apply-local",
            data={"album_artist": "Unknown", "album": "Ghost"},
            files={"file": ("cover.jpg", self._make_jpeg_bytes(), "image/jpeg")},
        )

        assert res.status_code == 404

    def test_returns_409_when_track_is_locked(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        locked = _track(1)
        mock_index.tracks_for_album.return_value = [locked]
        mock_queue.current.return_value = locked
        mock_queue.peek_next.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        res = c.post(
            "/api/v1/albums/art/apply-local",
            data={"album_artist": "Joan Jett", "album": "Up Your Alley"},
            files={"file": ("cover.jpg", self._make_jpeg_bytes(), "image/jpeg")},
        )

        assert res.status_code == 409

    def test_returns_422_for_non_image_content_type(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        res = c.post(
            "/api/v1/albums/art/apply-local",
            data={"album_artist": "Joan Jett", "album": "Up Your Alley"},
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )

        assert res.status_code == 422

    def test_returns_422_for_corrupt_image_bytes(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        res = c.post(
            "/api/v1/albums/art/apply-local",
            data={"album_artist": "Joan Jett", "album": "Up Your Alley"},
            files={
                "file": ("cover.jpg", b"\xff\xd8\xff not a real jpeg", "image/jpeg")
            },
        )

        assert res.status_code == 422

    def test_cover_file_mode_writes_cover_file_not_embed(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """In cover-file mode, art is written to a cover file instead of embedded."""
        image_bytes = self._make_jpeg_bytes()
        mock_index.tracks_for_album.return_value = [_track(1)]
        mock_index.albums.return_value = [
            AlbumInfo(
                album_artist="Joan Jett",
                album="Up Your Alley",
                year="1988",
                track_count=1,
                has_art=True,
                art_version=99.0,
            )
        ]
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"artwork.save_format": "cover-file"},
        )
        c = TestClient(app)

        with patch("kamp_daemon.artwork.write_cover_file") as mock_write:
            res = c.post(
                "/api/v1/albums/art/apply-local",
                data={"album_artist": "Joan Jett", "album": "Up Your Alley"},
                files={"file": ("cover.jpg", image_bytes, "image/jpeg")},
            )

        assert res.status_code == 200
        mock_write.assert_called_once()
        mock_index.mark_album_art_embedded.assert_called_once()


class TestValidateLibraryPathRemoteURI:
    """Remote URIs bypass _validate_library_path in single-track queue endpoints."""

    def test_bandcamp_double_slash_uri_bypasses_path_validation(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """bandcamp:// URIs no longer get HTTP 400 from path validation — they
        bypass it and return 404 when not found in the index."""
        mock_index.get_track_by_path.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        response = c.post(
            "/api/v1/player/queue/add",
            json={"file_path": "bandcamp://380008227/3"},
        )
        assert response.status_code == 404

    def test_bandcamp_single_slash_uri_bypasses_path_validation(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """POSIX-normalised bandcamp:/ URIs also bypass validation."""
        mock_index.get_track_by_path.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        response = c.post(
            "/api/v1/player/queue/add",
            json={"file_path": "bandcamp:/380008227/3"},
        )
        assert response.status_code == 404


class TestRemoteUriEndpointBypass:
    """Remote track URIs bypass _validate_library_path in single-track endpoints."""

    _REMOTE_URI = "bandcamp://123456/1"

    def _remote_track(self) -> Track:
        return Track(
            file_path=Path("bandcamp://123456/1"),
            title="Remote Track 1",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            year="2025",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.bcbits.com/stream/t.mp3",
            stream_url_expires_at=9999999999.0,
        )

    def test_play_with_remote_uri_does_not_raise_400(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = self._remote_track()
        mock_index.get_track_by_path.return_value = remote
        mock_queue.current.return_value = remote
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        res = c.post(
            "/api/v1/player/play",
            json={
                "album_artist": "Artist",
                "album": "Album",
                "file_path": self._REMOTE_URI,
                "track_index": 0,
            },
        )
        assert res.status_code == 200
        mock_index.get_track_by_path.assert_called_with(self._REMOTE_URI)

    def test_queue_add_remote_uri_adds_track(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = self._remote_track()
        mock_index.get_track_by_path.return_value = remote
        mock_queue.current.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        res = c.post("/api/v1/player/queue/add", json={"file_path": self._REMOTE_URI})
        assert res.status_code == 200
        mock_index.get_track_by_path.assert_called_with(self._REMOTE_URI)

    def test_queue_play_next_remote_uri(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = self._remote_track()
        mock_index.get_track_by_path.return_value = remote
        mock_queue.current.return_value = remote
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        res = c.post(
            "/api/v1/player/queue/play-next", json={"file_path": self._REMOTE_URI}
        )
        assert res.status_code == 200
        mock_index.get_track_by_path.assert_called_with(self._REMOTE_URI)

    def test_queue_insert_remote_uri(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = self._remote_track()
        mock_index.get_track_by_path.return_value = remote
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        res = c.post(
            "/api/v1/player/queue/insert",
            json={"file_path": self._REMOTE_URI, "index": 0},
        )
        assert res.status_code == 200
        mock_index.get_track_by_path.assert_called_with(self._REMOTE_URI)

    def test_favorite_remote_uri_does_not_raise_400(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = self._remote_track()
        mock_index.get_track_by_path.return_value = remote
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        res = c.post(
            "/api/v1/tracks/favorite",
            json={"file_path": self._REMOTE_URI, "favorite": True},
        )
        assert res.status_code == 200
        mock_index.set_favorite.assert_called_once_with(self._REMOTE_URI, True)


class TestResolvePlaybackRemote:
    """_resolve_playback invokes the refresh callback for expired remote track URLs."""

    def test_local_track_plays_via_file_path(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        mock_queue.next.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        c.post("/api/v1/player/next")
        mock_engine.play.assert_called_once_with(str(track.file_path))

    def test_remote_track_uses_stream_url_when_fresh(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        import time

        remote = _track(1)
        remote.source = "bandcamp"
        remote.stream_url = "https://cdn.example.com/stream.mp3"
        remote.stream_url_expires_at = time.time() + 7200  # 2 hours from now

        mock_queue.next.return_value = remote
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        c.post("/api/v1/player/next")
        mock_engine.play.assert_called_once_with("https://cdn.example.com/stream.mp3")

    def test_remote_track_refreshes_when_url_expired(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = Track(
            file_path=Path("bandcamp://999/3"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            year="2024",
            track_number=3,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.example.com/old.mp3",
            stream_url_expires_at=0.0,  # expired
        )
        mock_queue.next.return_value = remote
        mock_index.get_collection_item.return_value = {
            "sale_item_id": "999",
            "album_url": "https://artist.bandcamp.com/album/the-album",
        }

        refreshed_url = "https://cdn.example.com/new.mp3"
        refresh_fn = MagicMock(return_value=(refreshed_url, 9999.0))

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            refresh_stream_url=refresh_fn,
        )
        c = TestClient(app)

        c.post("/api/v1/player/next")

        refresh_fn.assert_called_once_with(
            "https://artist.bandcamp.com/album/the-album", 3
        )
        # update_stream_url receives the canonical bandcamp:// URI.
        # Path() normalises bandcamp:// → bandcamp:/ on POSIX; _resolve_playback
        # restores the canonical form so the DB lookup matches the stored row.
        mock_index.update_stream_url.assert_called_once_with(
            "bandcamp://999/3", refreshed_url, 9999.0
        )
        mock_engine.play.assert_called_once_with(refreshed_url)

    def test_remote_track_refreshes_with_windows_corrupted_path(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Windows Path normalises bandcamp:// to bandcamp:\\ — still parsed correctly."""
        remote = Track(
            # Simulate what str(Path("bandcamp://999/3")) yields on Windows.
            file_path=Path("bandcamp:\\\\999\\3"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            year="2024",
            track_number=3,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.example.com/old.mp3",
            stream_url_expires_at=0.0,
        )
        mock_queue.next.return_value = remote
        mock_index.get_collection_item.return_value = {
            "sale_item_id": "999",
            "album_url": "https://artist.bandcamp.com/album/the-album",
        }

        refresh_fn = MagicMock(return_value=("https://cdn.example.com/new.mp3", 9999.0))
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            refresh_stream_url=refresh_fn,
        )
        c = TestClient(app)

        c.post("/api/v1/player/next")

        refresh_fn.assert_called_once_with(
            "https://artist.bandcamp.com/album/the-album", 3
        )
        mock_index.update_stream_url.assert_called_once_with(
            "bandcamp://999/3", "https://cdn.example.com/new.mp3", 9999.0
        )

    def test_remote_track_skips_refresh_when_no_callback(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = Track(
            file_path=Path("bandcamp://888/1"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            year="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.example.com/existing.mp3",
            stream_url_expires_at=0.0,  # expired
        )
        mock_queue.next.return_value = remote
        # No refresh_stream_url callback provided — falls back to existing URL.
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        c.post("/api/v1/player/next")
        mock_engine.play.assert_called_once_with("https://cdn.example.com/existing.mp3")


# ---------------------------------------------------------------------------
# resolve_playback_uri — module-level function (used by on_track_end auto-advance)
# ---------------------------------------------------------------------------


class TestResolvePlaybackUri:
    """resolve_playback_uri is a module-level function so on_track_end can
    call it without going through a REST endpoint.  The underlying resolution
    logic is the same as _resolve_playback inside create_app — these tests
    verify the function directly to guard the KAMP-396 regression (EOF
    auto-advance passed a raw bandcamp: URI to mpv instead of a CDN URL)."""

    def _remote_track(
        self,
        *,
        stream_url: str | None = None,
        stream_url_expires_at: float | None = None,
    ) -> Track:
        return Track(
            file_path=Path("bandcamp://777/2"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            year="2024",
            track_number=2,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url=stream_url,
            stream_url_expires_at=stream_url_expires_at,
        )

    def test_local_track_returns_file_path(self) -> None:
        index = MagicMock()
        track = _track(1)
        assert resolve_playback_uri(track, index, None) == str(track.file_path)

    def test_remote_track_with_fresh_url_returns_stream_url(self) -> None:
        import time

        index = MagicMock()
        track = self._remote_track(
            stream_url="https://cdn.example.com/fresh.mp3",
            stream_url_expires_at=time.time() + 7200,
        )
        assert (
            resolve_playback_uri(track, index, None)
            == "https://cdn.example.com/fresh.mp3"
        )

    def test_remote_track_with_expired_url_refreshes(self) -> None:
        index = MagicMock()
        index.get_collection_item.return_value = {
            "album_url": "https://artist.bandcamp.com/album/x"
        }
        refresh_fn = MagicMock(return_value=("https://cdn.example.com/new.mp3", 9999.0))

        track = self._remote_track(
            stream_url="https://cdn.example.com/old.mp3",
            stream_url_expires_at=0.0,
        )
        result = resolve_playback_uri(track, index, refresh_fn)

        assert result == "https://cdn.example.com/new.mp3"
        refresh_fn.assert_called_once_with("https://artist.bandcamp.com/album/x", 2)
        index.update_stream_url.assert_called_once_with(
            "bandcamp://777/2", "https://cdn.example.com/new.mp3", 9999.0
        )

    def test_remote_track_with_no_stream_url_falls_back_to_playback_uri(self) -> None:
        """No stream_url and no refresh callback → playback_uri (raw bandcamp: URI).

        This is the best we can do when no refresh is available; mpv will error
        and the error-advance path will skip the track.  The key requirement is
        that we do NOT pass the Path str form (e.g. bandcamp:/777/2) — we pass
        playback_uri which returns the stream_url if set, else str(file_path).
        """
        index = MagicMock()
        track = self._remote_track(stream_url=None, stream_url_expires_at=None)
        # Without a refresh callback we fall through to playback_uri.
        result = resolve_playback_uri(track, index, None)
        assert result == track.playback_uri


# ---------------------------------------------------------------------------
# Art endpoint guards for remote tracks
# ---------------------------------------------------------------------------


class TestArtEndpointRemoteGuards:
    """Art read and write endpoints skip or reject remote-only tracks."""

    def _make_remote_track(self) -> Track:
        return Track(
            file_path=Path("bandcamp://999/1"),
            title="Remote Song",
            artist="The Artist",
            album_artist="The Artist",
            album="The Album",
            year="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=True,  # True but is_remote, so extract_art must not be called
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )

    def test_art_endpoint_skips_extract_art_for_remote_tracks(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """embedded_art=True on a remote track must not trigger extract_art."""
        remote = self._make_remote_track()
        mock_index.tracks_for_album.return_value = [remote]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        with patch("kamp_core.server.extract_art") as mock_extract:
            res = c.get(
                "/api/v1/albums/art",
                params={"album_artist": "The Artist", "album": "The Album"},
            )

        mock_extract.assert_not_called()
        assert res.status_code == 404  # no local art found → 404

    def test_art_endpoint_cover_file_returns_404_for_remote_only_album(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """_cover_file_response skips .parent when all tracks are remote.

        read_cover_file is a lazy import inside the art handler — it is never
        reached when local_tracks is empty, so no patch is needed.
        """
        remote = self._make_remote_track()
        mock_index.tracks_for_album.return_value = [remote]
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={"artwork.save_format": "cover-file"},
        )
        c = TestClient(app)

        res = c.get(
            "/api/v1/albums/art",
            params={"album_artist": "The Artist", "album": "The Album"},
        )

        # No local tracks → _cover_file_response returns None without touching
        # .file_path.parent; _embedded_response also returns None (no local art).
        assert res.status_code == 404

    def test_itunes_art_apply_returns_400_for_remote_only_album(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """POST /api/v1/albums/art/apply returns 400 when all tracks are remote."""
        remote = self._make_remote_track()
        mock_index.tracks_for_album.return_value = [remote]
        mock_index.albums.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        image_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG header

        with patch("kamp_daemon.artwork.fetch_itunes_image", return_value=image_bytes):
            res = c.post(
                "/api/v1/albums/art/apply",
                json={
                    "album_artist": "The Artist",
                    "album": "The Album",
                    "artwork_url_template": "https://example.mzstatic.com/image/{size}.jpg",
                },
            )

        assert res.status_code == 400
        assert "remote-only" in res.json()["detail"]

    def test_upload_art_returns_400_for_remote_only_album(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """POST /api/v1/albums/art/apply-local returns 400 when all tracks are remote."""
        remote = self._make_remote_track()
        mock_index.tracks_for_album.return_value = [remote]
        mock_index.albums.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        import io
        from PIL import Image as _Image

        buf = io.BytesIO()
        _Image.new("RGB", (600, 600)).save(buf, format="JPEG")
        image_bytes = buf.getvalue()

        res = c.post(
            "/api/v1/albums/art/apply-local",
            data={"album_artist": "The Artist", "album": "The Album"},
            files={"file": ("cover.jpg", image_bytes, "image/jpeg")},
        )

        assert res.status_code == 400
        assert "remote-only" in res.json()["detail"]


class TestArtEndpointRemoteAlbums:
    """GET /api/v1/album-art proxies and caches art for remote (bandcamp:) albums.

    Tests use the single-slash URI form ('bandcamp:/sale_id/track') because
    that is what the UI sends: str(track.file_path) where file_path is a Path,
    and Path('bandcamp://...') on POSIX normalises the double-slash to single.
    """

    _SALE_ID = "123456"
    _TRALBUM_ID = "987654321"
    # Single-slash: what the UI actually sends after Path normalisation on POSIX.
    _FILE_PATH = f"bandcamp:/{_SALE_ID}/1"
    _JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20

    def _collection_item(self) -> dict[str, Any]:
        return {
            "sale_item_id": self._SALE_ID,
            "tralbum_id": self._TRALBUM_ID,
            "album_url": "https://artist.bandcamp.com/album/the-album",
            "mode": "remote",
        }

    def _make_app(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        art_cache_dir: Path | None = None,
        session_data: dict[str, Any] | None = None,
    ) -> TestClient:
        session_data_val = session_data if session_data is not None else {"cookies": []}
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: session_data_val,
            art_cache_dir=art_cache_dir,
        )
        return TestClient(app)

    def test_cache_hit_serves_jpeg(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Cached JPEG is served directly without fetching from Bandcamp."""
        cache_dir = tmp_path / "art_cache"
        cache_dir.mkdir()
        (cache_dir / f"{self._TRALBUM_ID}.jpg").write_bytes(self._JPEG)
        mock_index.get_collection_item.return_value = self._collection_item()

        c = self._make_app(mock_index, mock_engine, mock_queue, art_cache_dir=cache_dir)
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "A", "album": "B", "file_path": self._FILE_PATH},
        )

        assert res.status_code == 200
        assert res.content == self._JPEG
        assert res.headers["content-type"] == "image/jpeg"

    def test_cache_miss_fetches_and_caches(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """On cache miss, art is fetched via fetch_album_art_bytes and cached to disk."""
        cache_dir = tmp_path / "art_cache"
        mock_index.get_collection_item.return_value = self._collection_item()

        c = self._make_app(mock_index, mock_engine, mock_queue, art_cache_dir=cache_dir)

        with patch(
            (
                "kamp_core.server.get_album_art.__wrapped__"
                if hasattr(create_app, "__wrapped__")
                else "kamp_daemon.bandcamp.fetch_album_art_bytes"
            ),
            return_value=self._JPEG,
        ):
            with patch(
                "kamp_daemon.bandcamp.fetch_album_art_bytes", return_value=self._JPEG
            ):
                res = c.get(
                    "/api/v1/album-art",
                    params={
                        "album_artist": "A",
                        "album": "B",
                        "file_path": self._FILE_PATH,
                    },
                )

        assert res.status_code == 200
        assert res.content == self._JPEG
        cache_file = cache_dir / f"{self._TRALBUM_ID}.jpg"
        assert cache_file.exists()
        assert cache_file.read_bytes() == self._JPEG

    def test_no_collection_item_returns_404(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If the sale_item_id is not in bandcamp_collection, return 404."""
        mock_index.get_collection_item.return_value = None
        c = self._make_app(
            mock_index, mock_engine, mock_queue, art_cache_dir=tmp_path / "art_cache"
        )
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "A", "album": "B", "file_path": self._FILE_PATH},
        )
        assert res.status_code == 404

    def test_no_session_returns_404(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If get_bandcamp_session returns None, return 404."""
        mock_index.get_collection_item.return_value = self._collection_item()
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: None,
            art_cache_dir=tmp_path / "art_cache",
        )
        c = TestClient(app)
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "A", "album": "B", "file_path": self._FILE_PATH},
        )
        assert res.status_code == 404

    def test_fetch_failure_returns_404(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If fetch_album_art_bytes returns None, return 404."""
        mock_index.get_collection_item.return_value = self._collection_item()
        c = self._make_app(
            mock_index, mock_engine, mock_queue, art_cache_dir=tmp_path / "art_cache"
        )
        with patch("kamp_daemon.bandcamp.fetch_album_art_bytes", return_value=None):
            res = c.get(
                "/api/v1/album-art",
                params={
                    "album_artist": "A",
                    "album": "B",
                    "file_path": self._FILE_PATH,
                },
            )
        assert res.status_code == 404

    def test_no_art_cache_dir_returns_404(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """If art_cache_dir is None in make_app, remote art returns 404."""
        mock_index.get_collection_item.return_value = self._collection_item()
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: {"cookies": []},
            art_cache_dir=None,
        )
        c = TestClient(app)
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "A", "album": "B", "file_path": self._FILE_PATH},
        )
        assert res.status_code == 404

    def test_album_artist_album_path_serves_art(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Art request via album_artist+album (no file_path) works for remote albums.

        The UI sends album_artist and album without file_path for normal albums
        (file_path is only populated for missing-album tracks). The endpoint must
        fall through to the remote-art branch after local art lookup returns nothing.
        """
        cache_dir = tmp_path / "art_cache"
        cache_dir.mkdir()
        (cache_dir / f"{self._TRALBUM_ID}.jpg").write_bytes(self._JPEG)

        remote_track = Track(
            file_path=Path(f"bandcamp://{self._SALE_ID}/1"),
            title="Track One",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            year="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=True,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        mock_index.tracks_for_album.return_value = [remote_track]
        mock_index.get_collection_item.return_value = self._collection_item()

        c = self._make_app(mock_index, mock_engine, mock_queue, art_cache_dir=cache_dir)
        # No file_path — this is the real request the UI sends for normal albums.
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "Artist", "album": "Album"},
        )

        assert res.status_code == 200
        assert res.content == self._JPEG
