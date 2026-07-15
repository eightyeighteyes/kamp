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

from kamp_core.library import AlbumInfo, ArtistInfo, LibraryStats, Track
from kamp_core.playback import PlaybackState
from kamp_core.server import TrackOut, create_app, resolve_playback_uri

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
        release_date="2024",
        track_number=n,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
    )


def _album(
    artist: str,
    album: str,
    release_date: str = "2024",
    count: int = 10,
    has_art: bool = False,
) -> AlbumInfo:
    return AlbumInfo(
        album_artist=artist,
        album=album,
        release_date=release_date,
        track_count=count,
        has_art=has_art,
    )


@pytest.fixture()
def mock_index() -> MagicMock:
    index = MagicMock()
    index.albums.return_value = []
    index.artists.return_value = []
    index.tracks_for_album.return_value = []
    index.search_playlists.return_value = []
    index.playlists_for_tracks.return_value = []
    index.get_playlist_cover.return_value = None
    # Default: no magic criteria (static playlist). Individual tests override.
    index.get_magic_playlist_criteria.return_value = None
    # Default: no magic playlists for field_index building.
    index.list_all_magic_criteria.return_value = []
    # Default: empty download queue so download.queue snapshots serialize (KAMP-566).
    index.download_queue_items.return_value = []
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
    queue.repeat = "off"
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
        mock_index.albums.return_value = [
            _album("Artist", "Record", release_date="2020")
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        album = c.get("/api/v1/albums").json()[0]
        assert set(album.keys()) >= {
            "album_artist",
            "album",
            "release_date",
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

    def test_direction_param_forwarded_to_index(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        c.get("/api/v1/albums?sort=date_added&direction=asc")
        mock_index.albums.assert_called_with(sort="date_added", sort_dir="asc")

    def test_empty_direction_passes_none_to_index(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        c.get("/api/v1/albums?sort=album_artist")
        mock_index.albums.assert_called_with(sort="album_artist", sort_dir=None)

    def test_invalid_direction_ignored(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        c.get("/api/v1/albums?direction=sideways")
        mock_index.albums.assert_called_with(sort="album_artist", sort_dir=None)


class TestTrackSources:
    """TrackOut/PlaylistTrackOut carry a sources[] list wired from the index (KAMP-537)."""

    _SRCS = {
        1: [
            {
                "kind": "file",
                "provider": "local",
                "uri": "/m/a.mp3",
                "is_available": 1,
                "duration": 101.0,
            },
            {
                "kind": "stream",
                "provider": "bandcamp",
                "uri": "bandcamp://9/1",
                "is_available": 1,
                "duration": 100.0,
            },
        ]
    }

    def test_get_tracks_includes_sources(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.id = 1
        mock_index.tracks_for_album.return_value = [track]
        mock_index.sources_for_track_ids.return_value = self._SRCS
        c = TestClient(
            create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        )
        res = c.get("/api/v1/tracks?album_artist=Artist&album=Album")
        assert res.status_code == 200
        srcs = res.json()[0]["sources"]
        assert [s["kind"] for s in srcs] == ["file", "stream"]
        assert srcs[1]["uri"] == "bandcamp://9/1"

    def test_queue_includes_sources(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        # The queue path bypasses TrackOut.from_track's usual list builders — assert
        # it still gets sources (Reality-Checker risk #1).
        track = _track(1)
        track.id = 1
        mock_queue.queue_tracks.return_value = ([track], 0)
        mock_index.sources_for_track_ids.return_value = self._SRCS
        c = TestClient(
            create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        )
        res = c.get("/api/v1/player/queue")
        assert res.status_code == 200
        assert [s["kind"] for s in res.json()["tracks"][0]["sources"]] == [
            "file",
            "stream",
        ]

    def test_sourceless_track_has_empty_sources(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        mock_index.sources_for_track_ids.return_value = {}  # legacy row, no sources
        c = TestClient(
            create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        )
        res = c.get("/api/v1/tracks?album_artist=Artist&album=Album")
        assert res.json()[0]["sources"] == []


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


class TestTopArtistsEndpoint:
    def test_returns_empty_list_when_no_artists(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.top_artists.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        response = c.get("/api/v1/artists/top")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_top_artists_ranked_by_play_time(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.top_artists.return_value = [
            ArtistInfo(name="Earth", play_time=3600.0, top_album="Pentastar"),
            ArtistInfo(name="Sunn O)))", play_time=1800.0, top_album="Black One"),
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        response = c.get("/api/v1/artists/top?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "Earth"
        assert data[0]["play_time"] == pytest.approx(3600.0)
        assert data[0]["top_album"] == "Pentastar"
        mock_index.top_artists.assert_called_once_with(2)


class TestMissingAlbumEndpoints:
    """Endpoints that address a missing-album track (album tag empty) by its
    canonical id — the id replaces the album-granularity file_path (KAMP-554)."""

    def test_albums_includes_missing_album_fields(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [
            AlbumInfo(
                album_artist="Mndsgn.",
                album="Lone Track",
                release_date="2020",
                track_count=1,
                has_art=False,
                missing_album=True,
                missing_track_id=77,
            )
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        album = c.get("/api/v1/albums").json()[0]
        assert album["missing_album"] is True
        assert album["track_id"] == 77
        assert "file_path" not in album

    def test_tracks_endpoint_uses_track_id_when_provided(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1, album="")
        mock_index.get_track_by_id.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/tracks?album_artist=&album=&track_id=42").json()
        assert len(data) == 1
        mock_index.get_track_by_id.assert_called_once_with(42)
        mock_index.tracks_for_album.assert_not_called()

    def test_tracks_endpoint_unknown_track_id_returns_empty(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_track_by_id.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        resp = c.get("/api/v1/tracks?album_artist=&album=&track_id=999")
        assert resp.status_code == 200
        assert resp.json() == []
        mock_index.tracks_for_album.assert_not_called()

    def test_album_art_endpoint_uses_track_id_when_provided(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1, album="")
        track.embedded_art = True
        mock_index.get_track_by_id.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with patch(
            "kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")
        ):
            res = c.get("/api/v1/album-art?album_artist=&album=&track_id=42")
        assert res.status_code == 200
        mock_index.get_track_by_id.assert_called_once_with(42)
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

    def test_extrapolates_position_when_time_pos_events_stale(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """When no time-pos event has arrived for >300 ms while playing, the
        snapshot must extrapolate position from wall-clock time so the progress
        bar advances even after mpv stops emitting events (e.g. seek near EOF
        of an HTTP stream — KAMP-392)."""
        import time

        state = PlaybackState(playing=True, position=42.0, duration=180.0)
        state.position_updated_at = time.time() - 2.0  # simulate 2 s stale
        mock_engine.state = state
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/player/state").json()
        # Position must be extrapolated beyond the raw 42.0 (by ~2 s).
        assert data["position"] > 42.0
        assert data["position"] < 47.0  # allow 5 s slack for test execution time
        assert data["position"] <= 180.0

    def test_does_not_extrapolate_when_paused(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Extrapolation must be suppressed when paused — freezing the bar
        is correct behaviour in that state."""
        import time

        state = PlaybackState(playing=False, position=42.0, duration=180.0)
        state.position_updated_at = time.time() - 5.0
        mock_engine.state = state
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/player/state").json()
        assert data["position"] == pytest.approx(42.0)

    def test_buffering_false_by_default(self, client: TestClient) -> None:
        data = client.get("/api/v1/player/state").json()
        assert data["buffering"] is False


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
        mock_queue.set_shuffle.assert_called_once_with(True, album_mode=False)

    def test_set_shuffle_album_mode(
        self, client: TestClient, mock_queue: MagicMock
    ) -> None:
        response = client.post(
            "/api/v1/player/shuffle", json={"shuffle": True, "album_shuffle": True}
        )
        assert response.status_code == 200
        mock_queue.set_shuffle.assert_called_once_with(True, album_mode=True)

    def test_set_repeat(self, client: TestClient, mock_queue: MagicMock) -> None:
        response = client.post("/api/v1/player/repeat", json={"mode": "queue"})
        assert response.status_code == 200
        mock_queue.set_repeat_mode.assert_called_once_with("queue")


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
        assert data["repeat"] == "off"

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
            "ext",
        }

    def test_queue_response_includes_shuffle_and_repeat_flags(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_queue.shuffle = True
        mock_queue.repeat = "off"
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/player/queue").json()
        assert data["shuffle"] is True
        assert data["repeat"] == "off"


class TestPlayFilesEndpoint:
    """play-files replaces the queue with an explicit ordered list of track ids
    (KAMP-552: id-native)."""

    def test_play_files_loads_ids_in_order(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        t1, t2 = _track(1), _track(2)
        mock_index.get_track_by_id.side_effect = lambda i: {1: t1, 2: t2}.get(i)
        mock_queue.current.return_value = None
        resp = client.post(
            "/api/v1/player/play-files", json={"ids": [2, 1], "start_index": 0}
        )
        assert resp.status_code == 200
        mock_queue.load.assert_called_once_with([t2, t1], start_index=0)

    def test_play_files_empty_ids_is_noop(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        resp = client.post("/api/v1/player/play-files", json={"ids": []})
        assert resp.status_code == 200
        mock_queue.load.assert_not_called()

    def test_play_files_skips_unknown_ids(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        t1 = _track(1)
        mock_index.get_track_by_id.side_effect = lambda i: t1 if i == 1 else None
        mock_queue.current.return_value = None
        resp = client.post("/api/v1/player/play-files", json={"ids": [1, 999]})
        assert resp.status_code == 200
        mock_queue.load.assert_called_once_with([t1], start_index=0)


class TestQueueMutationEndpoints:
    def test_add_to_queue_calls_queue_method(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1)
        mock_index.get_track_by_id.return_value = t
        resp = client.post("/api/v1/player/queue/add", json={"id": 1})
        assert resp.status_code == 200
        mock_queue.add_to_queue.assert_called_once_with(t)

    def test_add_to_queue_404_for_unknown_id(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_track_by_id.return_value = None
        resp = client.post("/api/v1/player/queue/add", json={"id": 999})
        assert resp.status_code == 404

    def test_play_next_calls_queue_method(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(2)
        mock_index.get_track_by_id.return_value = t
        resp = client.post("/api/v1/player/queue/play-next", json={"id": 2})
        assert resp.status_code == 200
        mock_queue.play_next.assert_called_once_with(t)

    def test_play_next_404_for_unknown_id(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_track_by_id.return_value = None
        resp = client.post("/api/v1/player/queue/play-next", json={"id": 999})
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
        mock_index.get_track_by_id.return_value = t
        # Three calls: was_stopped check, current after mutation, _state_snapshot in notify
        mock_queue.current.side_effect = [None, t, t]
        resp = client.post("/api/v1/player/queue/add", json={"id": 1})
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
        mock_index.get_track_by_id.return_value = t
        # Three calls: was_stopped check, current after mutation, _state_snapshot in notify
        mock_queue.current.side_effect = [None, t, t]
        resp = client.post("/api/v1/player/queue/play-next", json={"id": 2})
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

    def test_add_album_resolves_missing_album_by_id(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        # KAMP-554: a missing-album card queues its single track by canonical id.
        t = _track(1, album="")
        mock_index.get_track_by_id.return_value = t
        resp = client.post(
            "/api/v1/player/queue/add-album",
            json={"album_artist": "Artist", "album": "", "id": 8},
        )
        assert resp.status_code == 200
        mock_index.get_track_by_id.assert_called_once_with(8)
        mock_index.tracks_for_album.assert_not_called()
        mock_queue.add_album_to_queue.assert_called_once_with([t])

    def test_play_album_next_resolves_missing_album_by_id(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        # KAMP-554: adds the id branch play-album-next previously lacked (KAMP-537).
        t = _track(1, album="")
        mock_index.get_track_by_id.return_value = t
        resp = client.post(
            "/api/v1/player/queue/play-album-next",
            json={"album_artist": "Artist", "album": "", "id": 9},
        )
        assert resp.status_code == 200
        mock_index.get_track_by_id.assert_called_once_with(9)
        mock_index.tracks_for_album.assert_not_called()
        mock_queue.play_album_next.assert_called_once_with([t])

    def test_play_album_next_404_for_unknown_id(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_track_by_id.return_value = None
        resp = client.post(
            "/api/v1/player/queue/play-album-next",
            json={"album_artist": "Artist", "album": "", "id": 999},
        )
        assert resp.status_code == 404

    def test_insert_album_resolves_missing_album_by_id(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1, album="")
        mock_index.get_track_by_id.return_value = t
        resp = client.post(
            "/api/v1/player/queue/insert-album",
            json={"album_artist": "Artist", "album": "", "index": 1, "id": 10},
        )
        assert resp.status_code == 200
        mock_index.get_track_by_id.assert_called_once_with(10)
        mock_index.tracks_for_album.assert_not_called()
        mock_queue.insert_album_at.assert_called_once_with([t], 1)

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

    def test_buffering_cleared_by_on_file_loaded(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """on_file_loaded clears buffering when mpv opens the new file."""
        import time

        remote = Track(
            file_path=Path("bandcamp://999/1"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.example.com/track.mp3",
            stream_url_expires_at=time.time() + 7200,
        )
        mock_queue.next.return_value = remote
        mock_index.get_collection_item.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        # Trigger buffering=True via a remote-track play.
        c.post("/api/v1/player/next")
        assert c.get("/api/v1/player/state").json()["buffering"] is True

        # Simulate mpv firing file-loaded (new file opened and decoding begun).
        mock_engine.on_file_loaded()

        assert c.get("/api/v1/player/state").json()["buffering"] is False


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
        assert data == {"albums": [], "tracks": [], "playlists": []}

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

    def test_album_card_matches_track_case_insensitively(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """KAMP-545: an album row whose album_artist casing diverges from its
        tracks' casing ("SUNN O)))" vs "Sunn O)))") still surfaces as an album
        card — the album↔track match honours the NOCASE collation."""
        t = _track(1, album="sunn O)))", artist="Sunn O)))")
        mock_index.search.return_value = [t]
        mock_index.albums.return_value = [_album("SUNN O)))", "sunn O)))")]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=sunn")
        data = res.json()
        assert len(data["albums"]) == 1
        assert data["albums"][0]["album_artist"] == "SUNN O)))"

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

    def test_search_response_includes_playlists_key(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.search.return_value = []
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=anything")
        assert "playlists" in res.json()

    def test_search_returns_playlist_name_match(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.search.return_value = []
        mock_index.search_playlists.return_value = [
            {
                "id": 1,
                "title": "Road Trip",
                "favorite": False,
                "track_count": 5,
                "created_at": 1000.0,
                "updated_at": 1001.0,
                "last_played_at": None,
                "source": "local",
            }
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=road")
        assert res.status_code == 200
        data = res.json()
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["title"] == "Road Trip"
        assert data["playlists"][0]["source"] == "local"

    def test_search_returns_playlists_containing_matched_tracks(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1, album="Kid A", artist="Radiohead")
        mock_index.search.return_value = [t]
        mock_index.albums.return_value = []
        mock_index.playlists_for_tracks.return_value = [
            {
                "id": 7,
                "title": "Chill Mix",
                "favorite": True,
                "track_count": 10,
                "created_at": 2000.0,
                "updated_at": 2001.0,
                "last_played_at": None,
                "source": "bandcamp",
            }
        ]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=radiohead")
        assert res.status_code == 200
        data = res.json()
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["id"] == 7
        assert data["playlists"][0]["source"] == "bandcamp"
        mock_index.playlists_for_tracks.assert_called_once_with([t.id])

    def test_search_playlists_deduplication(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """A playlist matching both by name and by track-membership appears once."""
        t = _track(1, album="Kid A", artist="Radiohead")
        mock_index.search.return_value = [t]
        mock_index.albums.return_value = []
        shared = {
            "id": 3,
            "title": "Radiohead Playlist",
            "favorite": False,
            "track_count": 2,
            "created_at": 3000.0,
            "updated_at": 3001.0,
            "last_played_at": None,
            "source": "local",
        }
        mock_index.search_playlists.return_value = [shared]
        mock_index.playlists_for_tracks.return_value = [shared]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        res = TestClient(app).get("/api/v1/search?q=radiohead")
        data = res.json()
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["id"] == 3


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

    def test_set_sort_order_release_date_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/v1/ui/sort-order", json={"sort_order": "release_date"})
        assert resp.status_code == 200

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
        callback.assert_called_with("ui.sort_order", "date_added")

    def test_get_ui_state_includes_sort_dir(self, client: TestClient) -> None:
        data = client.get("/api/v1/ui").json()
        assert "sort_dir" in data
        assert data["sort_dir"] == "asc"

    def test_ui_sort_dir_init_param_reflected_in_ui_state(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            ui_sort_dir="desc",
        )
        data = TestClient(app).get("/api/v1/ui").json()
        assert data["sort_dir"] == "desc"

    def test_set_sort_order_persists_sort_dir(self, client: TestClient) -> None:
        client.post(
            "/api/v1/ui/sort-order",
            json={"sort_order": "date_added", "sort_dir": "asc"},
        )
        assert client.get("/api/v1/ui").json()["sort_dir"] == "asc"

    def test_set_sort_order_without_sort_dir_leaves_dir_unchanged(
        self, client: TestClient
    ) -> None:
        client.post(
            "/api/v1/ui/sort-order",
            json={"sort_order": "date_added", "sort_dir": "desc"},
        )
        client.post("/api/v1/ui/sort-order", json={"sort_order": "album_artist"})
        assert client.get("/api/v1/ui").json()["sort_dir"] == "desc"

    def test_set_sort_order_invalid_sort_dir_ignored(self, client: TestClient) -> None:
        client.post(
            "/api/v1/ui/sort-order",
            json={"sort_order": "album_artist", "sort_dir": "sideways"},
        )
        assert client.get("/api/v1/ui").json()["sort_dir"] == "asc"


# ---------------------------------------------------------------------------
# Favorite endpoint
# ---------------------------------------------------------------------------


class TestFavoriteEndpoint:
    def test_set_favorite_endpoint(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.id = 42
        mock_index.get_track_by_id.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/tracks/favorite",
            json={"id": 42, "favorite": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # KAMP-537: favorite resolves the track first, then keys the DB write on its
        # canonical uri (the stored preferred-source path), not the raw request path.
        mock_index.set_favorite.assert_called_once_with(
            str(Path("/music/01.mp3")), True
        )
        # KAMP-538/532: the in-memory queue is patched by canonical id so the next
        # player-state snapshot is correct even if the queued uri has diverged.
        mock_queue.update_favorite.assert_called_once_with(42, True)

    def test_set_favorite_returns_404_for_unknown_track(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_track_by_id.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/tracks/favorite",
            json={"id": 999, "favorite": True},
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

    def test_set_favorite_remote_track(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.id = 42
        track.file_path = Path("bandcamp://999/3")
        mock_index.get_track_by_id.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/tracks/favorite",
            json={"id": 42, "favorite": True},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # KAMP-552: DB write keys on the resolved track's canonical uri. KAMP-538/532:
        # the queue is patched by canonical id.
        mock_index.set_favorite.assert_called_once_with("bandcamp://999/3", True)
        mock_queue.update_favorite.assert_called_once_with(42, True)


class TestApiIdContract:
    """Freezes the KAMP-537 track-shape contract handed to KAMP-538, and the exact
    set of response shapes still carrying file_path — the KAMP-539 removal checklist.
    A drift (a shape gains/loses id, sources, or file_path) fails here."""

    def test_source_out_fields(self) -> None:
        from kamp_core.server import SourceOut

        assert set(SourceOut.model_fields) == {
            "kind",
            "provider",
            "uri",
            "is_available",
            "duration",
        }

    def test_track_shapes_carry_id_and_sources(self) -> None:
        from kamp_core.server import PlaylistTrackOut, TrackOut

        # KAMP-552: track shapes are id-native; file_path is gone (sources[].uri
        # carries the delivery paths).
        assert {"id", "sources"} <= set(TrackOut.model_fields)
        assert "file_path" not in TrackOut.model_fields
        assert {"id", "sources"} <= set(PlaylistTrackOut.model_fields)
        assert "file_path" not in PlaylistTrackOut.model_fields

    def test_album_out_addresses_missing_album_by_track_id(self) -> None:
        from kamp_core.server import AlbumOut

        # KAMP-554: a missing-album card is addressed by track_id; file_path is gone.
        assert {"track_id", "missing_album"} <= set(AlbumOut.model_fields)
        assert "file_path" not in AlbumOut.model_fields

    def test_no_out_shape_carries_file_path(self) -> None:
        import inspect

        from pydantic import BaseModel

        from kamp_core import server

        carriers = {
            name
            for name, obj in inspect.getmembers(server, inspect.isclass)
            if issubclass(obj, BaseModel)
            and obj.__module__ == server.__name__
            and name.endswith("Out")
            and "file_path" in obj.model_fields
        }
        # KAMP-552 deleted file_path from the track shapes; KAMP-554 removed the
        # last carrier (AlbumOut's missing-album key). No Out shape carries it now.
        assert carriers == set()


class TestDualAcceptId:
    """Track-keyed endpoints resolve the canonical id, preferred over file_path (KAMP-537)."""

    def test_favorite_by_id(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.id = 5
        mock_index.get_track_by_id.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/tracks/favorite", json={"id": 5, "favorite": True}
        )
        assert resp.status_code == 200
        mock_index.get_track_by_id.assert_called_once_with(5)
        mock_index.get_track_by_path.assert_not_called()
        mock_index.set_favorite.assert_called_once_with(
            str(Path("/music/01.mp3")), True
        )

    def test_favorite_id_wins_when_both_sent(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.id = 5
        mock_index.get_track_by_id.return_value = track
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post(
            "/api/v1/tracks/favorite",
            json={"id": 5, "file_path": "/music/other.mp3", "favorite": True},
        )
        assert resp.status_code == 200
        # id wins — no 400, file_path ignored.
        mock_index.get_track_by_id.assert_called_once_with(5)
        mock_index.get_track_by_path.assert_not_called()

    def test_favorite_without_id_is_422(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        # KAMP-552: id is required now (no file_path fallback), so omitting it is a
        # request-validation error, not a 404.
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post("/api/v1/tracks/favorite", json={"favorite": True})
        assert resp.status_code == 422

    def test_queue_add_by_id(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        track = _track(1)
        track.id = 7
        mock_index.get_track_by_id.return_value = track
        mock_queue.current.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post("/api/v1/player/queue/add", json={"id": 7})
        assert resp.status_code == 200
        mock_index.get_track_by_id.assert_called_once_with(7)
        mock_queue.add_to_queue.assert_called_once_with(track)


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
            config_values={
                "bandcamp.connected": True,
                "bandcamp.username": "johndoe",
                "bandcamp.ever_connected": True,
            },
            on_bandcamp_disconnect=lambda: None,
        )
        c = TestClient(app)
        c.delete("/api/v1/bandcamp/connect")
        data = c.get("/api/v1/config").json()
        assert data["bandcamp.connected"] is False
        assert data["bandcamp.username"] is None
        assert data["bandcamp.ever_connected"] is True

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
            config_values={
                "bandcamp.connected": False,
                "bandcamp.username": None,
                "bandcamp.ever_connected": False,
            },
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
        assert data["bandcamp.ever_connected"] is True

    def test_login_complete_sets_username_when_available(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        cookies = [{"name": "js_logged_in", "value": "1", "domain": ".bandcamp.com"}]
        session = {"cookies": cookies, "username": "johndoe"}
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            config_values={
                "bandcamp.connected": False,
                "bandcamp.username": None,
                "bandcamp.ever_connected": False,
            },
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
        assert data["bandcamp.ever_connected"] is True

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

    def test_notify_pipeline_stage_broadcasts_sale_item_id_and_committed(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """KAMP-562: the pipeline.stage event carries sale_item_id + committed so a
        per-album card can show a tagging badge; the global indicator ignores them.
        KAMP-558: it also carries the album label for the indicator tooltip."""
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            app.state.notify_pipeline_stage("Tagging", "392692056", False, "My Album")
            during = ws.receive_json()
            app.state.notify_pipeline_stage("", "392692056", True, "My Album")
            terminal = ws.receive_json()
        assert during == {
            "type": "pipeline.stage",
            "stage": "Tagging",
            "sale_item_id": "392692056",
            "committed": False,
            "album": "My Album",
        }
        assert terminal == {
            "type": "pipeline.stage",
            "stage": "",
            "sale_item_id": "392692056",
            "committed": True,
            "album": "My Album",
        }

    def test_notify_pipeline_stage_defaults_are_backward_compatible(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """Called with just a stage (the global indicator's usage), sale_item_id is
        None, committed False, and album "" — the payload the preload consumer
        already tolerates.
        """
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()
            app.state.notify_pipeline_stage("Extracting")
            msg = ws.receive_json()
        assert msg == {
            "type": "pipeline.stage",
            "stage": "Extracting",
            "sale_item_id": None,
            "committed": False,
            "album": "",
        }


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
        import queue as _queue

        mock_index.get_collection_item.return_value = None
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            dl_queue=_queue.Queue(),
        )
        resp = TestClient(app).post("/api/v1/bandcamp/collection/99999/download")
        assert resp.status_code == 404

    def test_returns_503_when_dl_queue_not_configured(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_collection_item.return_value = {"sale_item_id": "42"}
        # dl_queue defaults to None → 503
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).post("/api/v1/bandcamp/collection/42/download")
        assert resp.status_code == 503

    def test_enqueues_item_and_broadcasts_queued(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        import queue as _queue

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "remote",
            "item_title": "Album 42",
            "band_name": "Artist 42",
        }
        dl_q: _queue.Queue[str] = _queue.Queue()
        ws_messages: list[dict] = []

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            dl_queue=dl_q,
        )
        with TestClient(app) as c:
            with c.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # consume initial state push
                resp = c.post("/api/v1/bandcamp/collection/42/download")
                ws_messages.append(ws.receive_json())
                ws_messages.append(ws.receive_json())

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # DB enqueue and mode update must be called
        mock_index.set_collection_item_mode.assert_called_once_with("42", "local")
        mock_index.set_track_source_for_item.assert_not_called()
        # Enqueued with the album snapshot for the Downloads-view card.
        mock_index.enqueue_download.assert_called_once_with(
            "42", album_name="Album 42", album_artist="Artist 42"
        )
        # Item placed on the in-memory queue
        assert dl_q.get_nowait() == "42"
        # WS broadcast is 'queued', not 'downloading'
        assert ws_messages[0] == {
            "type": "bandcamp.album-download",
            "sale_item_id": "42",
            "state": "queued",
        }
        # Followed by a structured download.queue snapshot (KAMP-566)
        assert ws_messages[1] == {"type": "download.queue", "items": []}

    def test_second_download_also_broadcasts_queued(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """Both items broadcast 'queued'; the worker serializes execution."""
        import queue as _queue

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "x",
            "mode": "remote",
        }
        dl_q: _queue.Queue[str] = _queue.Queue()
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            dl_queue=dl_q,
        )
        with TestClient(app) as c:
            with c.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # consume initial state push
                c.post("/api/v1/bandcamp/collection/11/download")
                c.post("/api/v1/bandcamp/collection/22/download")
                # Each POST emits an album-download 'queued' + a download.queue
                # snapshot; collect the per-item events (ignore snapshots).
                events = [ws.receive_json() for _ in range(4)]

        album_events = [e for e in events if e["type"] == "bandcamp.album-download"]
        assert [(e["sale_item_id"], e["state"]) for e in album_events] == [
            ("11", "queued"),
            ("22", "queued"),
        ]
        # Both items are on the queue in FIFO order
        assert dl_q.get_nowait() == "11"
        assert dl_q.get_nowait() == "22"

    def test_notify_album_download_progress_broadcasts_bytes_and_percent(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """KAMP-436/566: per-album progress rides bandcamp.album-download with the
        derived percent (for the art reveal) plus raw downloaded/total bytes."""
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            app.state.notify_album_download_progress("12345", 420, 1000)
            msg = ws.receive_json()
        assert msg == {
            "type": "bandcamp.album-download",
            "sale_item_id": "12345",
            "state": "downloading",
            "progress": 42,
            "downloaded_bytes": 420,
            "total_bytes": 1000,
        }

    def test_notify_album_download_progress_zero_total_is_safe(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """A zero total (unknowable size) yields progress 0 without dividing by zero."""
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()
            app.state.notify_album_download_progress("12345", 0, 0)
            msg = ws.receive_json()
        assert msg["progress"] == 0
        assert msg["total_bytes"] == 0

    def test_notify_download_queue_broadcasts_snapshot(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """KAMP-566: notify_download_queue broadcasts a download.queue snapshot of
        the full queue (status/size/error/album metadata) for the Downloads view."""
        items = [
            {
                "provider": "bandcamp",
                "provider_item_id": "1",
                "status": "downloading",
                "position": 1,
                "size_bytes": 1000,
                "size_is_estimate": False,
                "error_text": None,
                "album_name": "Album One",
                "album_artist": "Artist One",
                "artwork_ref": None,
                "queued_at": 1.0,
            },
            {
                "provider": "bandcamp",
                "provider_item_id": "2",
                "status": "failed",
                "position": 2,
                "size_bytes": None,
                "size_is_estimate": True,
                "error_text": "HTTP 500",
                "album_name": "Album Two",
                "album_artist": "Artist Two",
                "artwork_ref": None,
                "queued_at": 2.0,
            },
        ]
        mock_index.download_queue_items.return_value = items
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        with c.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()  # consume initial player.state
            app.state.notify_download_queue()
            msg = ws.receive_json()
        assert msg == {"type": "download.queue", "items": items}

    def test_notify_album_download_progress_exposed_on_app_state(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        assert callable(getattr(app.state, "notify_album_download_progress", None))
        assert callable(getattr(app.state, "notify_download_queue", None))

    def test_notify_album_download_status_exposed_on_app_state(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        assert callable(getattr(app.state, "notify_album_download_status", None))


# ---------------------------------------------------------------------------
# /api/v1/downloads — queue management (KAMP-567)
# ---------------------------------------------------------------------------


class TestDownloadQueueEndpoints:
    """GET/POST/DELETE /api/v1/downloads — list / reorder / retry / cancel."""

    def _app(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        dl_q: Any = None,
    ) -> Any:
        return create_app(
            index=mock_index, engine=mock_engine, queue=mock_queue, dl_queue=dl_q
        )

    def test_list_returns_queue_items(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        items = [
            {
                "provider": "bandcamp",
                "provider_item_id": "1",
                "status": "downloading",
                "position": 1,
                "size_bytes": 1000,
                "size_is_estimate": False,
                "error_text": None,
                "album_name": "A1",
                "album_artist": "Artist",
                "artwork_ref": None,
                "queued_at": 1.0,
            }
        ]
        mock_index.download_queue_items.return_value = items
        app = self._app(mock_index, mock_engine, mock_queue)
        resp = TestClient(app).get("/api/v1/downloads")
        assert resp.status_code == 200
        assert resp.json() == {"items": items}

    def test_reorder_reorders_and_broadcasts_snapshot(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = self._app(mock_index, mock_engine, mock_queue)
        with TestClient(app) as c:
            with c.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # initial state push
                resp = c.post(
                    "/api/v1/downloads/reorder",
                    json={"provider_item_ids": ["c", "a", "b"]},
                )
                snapshot = ws.receive_json()
        assert resp.status_code == 200
        mock_index.reorder_download_queue.assert_called_once_with(["c", "a", "b"])
        assert snapshot == {"type": "download.queue", "items": []}

    def test_reorder_invalid_permutation_returns_400(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.reorder_download_queue.side_effect = ValueError("stale reorder")
        app = self._app(mock_index, mock_engine, mock_queue)
        resp = TestClient(app).post(
            "/api/v1/downloads/reorder", json={"provider_item_ids": ["a"]}
        )
        assert resp.status_code == 400

    def test_retry_requeues_wakes_and_broadcasts(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        import queue as _queue

        dl_q: _queue.Queue[str] = _queue.Queue()
        app = self._app(mock_index, mock_engine, mock_queue, dl_q)
        with TestClient(app) as c:
            with c.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # initial state push
                resp = c.post("/api/v1/downloads/42/retry")
                msg = ws.receive_json()
                snapshot = ws.receive_json()
        assert resp.status_code == 200
        mock_index.retry_download.assert_called_once_with("42")
        assert dl_q.get_nowait() == "42"  # worker woken
        assert msg == {
            "type": "bandcamp.album-download",
            "sale_item_id": "42",
            "state": "queued",
        }
        assert snapshot == {"type": "download.queue", "items": []}

    def test_retry_returns_503_when_dl_queue_not_configured(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = self._app(mock_index, mock_engine, mock_queue)  # dl_queue=None
        resp = TestClient(app).post("/api/v1/downloads/42/retry")
        assert resp.status_code == 503
        mock_index.retry_download.assert_not_called()

    def test_cancel_removes_and_broadcasts_removed(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = self._app(mock_index, mock_engine, mock_queue)
        with TestClient(app) as c:
            with c.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # initial state push
                resp = c.delete("/api/v1/downloads/42")
                msg = ws.receive_json()
                snapshot = ws.receive_json()
        assert resp.status_code == 200
        mock_index.cancel_download.assert_called_once_with("42")
        assert msg == {
            "type": "bandcamp.album-download",
            "sale_item_id": "42",
            "state": "removed",
        }
        assert snapshot == {"type": "download.queue", "items": []}


# DELETE /api/v1/bandcamp/collection/{sale_item_id}/download
# ---------------------------------------------------------------------------


class TestBandcampRemoveDownload:
    """Tests for DELETE /api/v1/bandcamp/collection/{sale_item_id}/download."""

    def test_returns_404_when_item_not_in_collection(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_collection_item.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).delete("/api/v1/bandcamp/collection/99/download")
        assert resp.status_code == 404

    def test_returns_409_when_track_is_actively_playing(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        from kamp_core.library import Track
        from pathlib import Path as _Path

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "local",
        }
        playing_track = Track(
            file_path=_Path("/music/Artist/Album/01.flac"),
            title="Playing Track",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="flac",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
        )
        playing_track.id = 77
        mock_index.local_tracks_for_sale_item_id.return_value = [playing_track]
        mock_queue.current.return_value = playing_track
        mock_engine.state.playing = True

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).delete("/api/v1/bandcamp/collection/42/download")
        assert resp.status_code == 409

    def test_returns_200_and_broadcasts_removed(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        from pathlib import Path as _Path

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "local",
        }
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_index.remove_download.return_value = []

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with TestClient(app) as c:
            with c.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # consume initial state push
                resp = c.delete("/api/v1/bandcamp/collection/42/download")
                msg = ws.receive_json()

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert msg["type"] == "bandcamp.album-download"
        assert msg["sale_item_id"] == "42"
        assert msg["state"] == "removed"

    def test_deletes_local_files(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        from pathlib import Path as _Path

        track_file = tmp_path / "track.flac"  # type: ignore[operator]
        track_file.write_bytes(b"dummy")

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "local",
        }
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_index.remove_download.return_value = [track_file]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert not track_file.exists()

    def test_no_error_when_file_already_missing(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        from pathlib import Path as _Path

        missing_file = tmp_path / "gone.flac"  # type: ignore[operator]
        # Do not create the file — simulates already-deleted scenario.

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "local",
        }
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_index.remove_download.return_value = [missing_file]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert resp.status_code == 200

    def test_removes_cover_art_before_rmdir(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Cover art left in the album dir after track deletion is cleaned up."""
        album_dir = tmp_path / "Artist" / "Album"  # type: ignore[operator]
        album_dir.mkdir(parents=True)
        track_file = album_dir / "01.flac"
        cover_file = album_dir / "cover.jpg"
        track_file.write_bytes(b"audio")
        cover_file.write_bytes(b"img")

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "local",
        }
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_index.remove_download.return_value = [track_file]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert not cover_file.exists()
        assert not album_dir.exists()

    def test_preserves_artist_dir_when_other_album_remains(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Artist dir is kept when another album's folder still lives in it."""
        artist_dir = tmp_path / "Artist"  # type: ignore[operator]
        album_dir = artist_dir / "Album A"
        other_album_dir = artist_dir / "Album B"
        album_dir.mkdir(parents=True)
        other_album_dir.mkdir(parents=True)
        (other_album_dir / "01.flac").write_bytes(b"audio")

        track_file = album_dir / "01.flac"
        track_file.write_bytes(b"audio")

        mock_index.get_collection_item.return_value = {
            "sale_item_id": "42",
            "mode": "local",
        }
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_index.remove_download.return_value = [track_file]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert not album_dir.exists()
        assert artist_dir.exists()  # kept — other album still present

    # --- KAMP-527: on-demand stream materialization for download-mode albums ---

    def _download_only_item(self) -> dict[str, Any]:
        return {
            "sale_item_id": "42",
            "mode": "local",
            "band_name": "Artist",
            "item_title": "Album",
            "album_url": "https://artist.bandcamp.com/album/album",
            "num_streamable_tracks": 2,
        }

    def test_materializes_stream_tracks_when_missing(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """A download-mode album with no stream rows fetches + materializes them
        before remove_download runs."""
        mock_index.get_collection_item.return_value = self._download_only_item()
        mock_index.all_downloads_streamable.return_value = False
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_index.remove_download.return_value = []
        mock_engine.state.playing = False

        fetched = [MagicMock(name="track1"), MagicMock(name="track2")]
        with (
            patch(
                "kamp_daemon.bandcamp.fetch_album_tracks", return_value=fetched
            ) as fat,
            patch("kamp_daemon.bandcamp._make_requests_session"),
        ):
            app = create_app(
                index=mock_index,
                engine=mock_engine,
                queue=mock_queue,
                get_bandcamp_session=lambda: {"cookies": []},
            )
            resp = TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert resp.status_code == 200
        fat.assert_called_once()
        mock_index.materialize_stream_tracks.assert_called_once_with("42", fetched)
        mock_index.remove_download.assert_called_once_with("42")

    def test_returns_422_when_no_session_for_materialization(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_collection_item.return_value = self._download_only_item()
        mock_index.all_downloads_streamable.return_value = False
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_engine.state.playing = False

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            get_bandcamp_session=lambda: None,
        )
        resp = TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert resp.status_code == 422
        mock_index.materialize_stream_tracks.assert_not_called()
        mock_index.remove_download.assert_not_called()

    def test_returns_422_when_no_streamable_version_available(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """fetch_album_tracks returning nothing => no streamable version; abort."""
        mock_index.get_collection_item.return_value = self._download_only_item()
        mock_index.all_downloads_streamable.return_value = False
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_engine.state.playing = False

        with (
            patch("kamp_daemon.bandcamp.fetch_album_tracks", return_value=[]),
            patch("kamp_daemon.bandcamp._make_requests_session"),
        ):
            app = create_app(
                index=mock_index,
                engine=mock_engine,
                queue=mock_queue,
                get_bandcamp_session=lambda: {"cookies": []},
            )
            resp = TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert resp.status_code == 422
        mock_index.materialize_stream_tracks.assert_not_called()
        mock_index.remove_download.assert_not_called()

    def test_returns_422_when_fetch_raises(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_collection_item.return_value = self._download_only_item()
        mock_index.all_downloads_streamable.return_value = False
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_engine.state.playing = False

        with (
            patch(
                "kamp_daemon.bandcamp.fetch_album_tracks",
                side_effect=RuntimeError("boom"),
            ),
            patch("kamp_daemon.bandcamp._make_requests_session"),
        ):
            app = create_app(
                index=mock_index,
                engine=mock_engine,
                queue=mock_queue,
                get_bandcamp_session=lambda: {"cookies": []},
            )
            resp = TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert resp.status_code == 422
        mock_index.remove_download.assert_not_called()

    def test_returns_422_when_remove_download_reports_no_streamable(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """Stream rows appear present (skip materialize) but remove_download's own
        per-track guard rejects the removal => surface a clean 422."""
        from kamp_core.library import NoStreamableVersionError

        mock_index.get_collection_item.return_value = self._download_only_item()
        mock_index.all_downloads_streamable.return_value = True
        mock_index.local_tracks_for_sale_item_id.return_value = []
        mock_index.remove_download.side_effect = NoStreamableVersionError("nope")
        mock_engine.state.playing = False

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).delete("/api/v1/bandcamp/collection/42/download")

        assert resp.status_code == 422


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
            release_date="2020",
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
        updated = Track(**{**track.__dict__, "label": "ECM", "release_date": "1975"})
        mock_index.tracks_for_album.return_value = [track]
        mock_index.update_album_meta.return_value = [updated]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch("kamp_core.library.write_meta_tags_to_file"):
            resp = TestClient(app).patch(
                "/api/v1/albums/meta",
                params={"album_artist": "Artist", "album": "Record"},
                json={"label": "ECM", "release_date": "1975"},
            )

        assert resp.status_code == 200
        mock_index.update_album_meta.assert_called_once_with(
            "Artist",
            "Record",
            genre=None,
            label="ECM",
            release_date="1975",
            mb_release_id=None,
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
            release_date="2020",
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
            release_date="",
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

    def _make_remote_track(self, n: int = 1) -> Track:
        return Track(
            file_path=Path(f"bandcamp://123/{n}"),
            title=f"Stream {n}",
            artist="Artist",
            album_artist="Artist",
            album="Record",
            release_date="",
            track_number=n,
            disc_number=1,
            ext="",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="",
            label="",
            source="bandcamp",
        )

    def test_remote_tracks_skip_file_write_but_update_db(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """Bandcamp tracks must not trigger write_meta_tags_to_file."""
        remote = self._make_remote_track()
        updated = Track(**{**remote.__dict__, "genre": "Jazz"})
        mock_index.tracks_for_album.return_value = [remote]
        mock_index.update_album_meta.return_value = [updated]

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch("kamp_core.library.write_meta_tags_to_file") as mock_write:
            resp = TestClient(app).patch(
                "/api/v1/albums/meta",
                params={"album_artist": "Artist", "album": "Record"},
                json={"genre": "Jazz"},
            )

        assert resp.status_code == 200
        mock_write.assert_not_called()
        mock_index.update_album_meta.assert_called_once_with(
            "Artist",
            "Record",
            genre="Jazz",
            label=None,
            release_date=None,
            mb_release_id=None,
        )

    def test_mixed_album_writes_files_only_for_local_tracks(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """Only local tracks in a mixed album receive file-tag writes."""
        local = self._make_track(1)
        remote = self._make_remote_track(2)
        updated = [
            Track(**{**local.__dict__, "genre": "Jazz"}),
            Track(**{**remote.__dict__, "genre": "Jazz"}),
        ]
        mock_index.tracks_for_album.return_value = [local, remote]
        mock_index.update_album_meta.return_value = updated

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        with patch("kamp_core.library.write_meta_tags_to_file") as mock_write:
            resp = TestClient(app).patch(
                "/api/v1/albums/meta",
                params={"album_artist": "Artist", "album": "Record"},
                json={"genre": "Jazz"},
            )

        assert resp.status_code == 200
        assert mock_write.call_count == 1
        assert mock_write.call_args[0][0] == local.file_path


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
            release_date="1988",
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
                release_date="1988",
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
                release_date="1988",
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
                release_date="1988",
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


class TestRemoteMissingAlbumById:
    """A remote (bandcamp) missing-album track is played by its canonical id — no
    path/uri is sent by the client any more (KAMP-554)."""

    def _remote_track(self) -> Track:
        return Track(
            file_path=Path("bandcamp://123456/1"),
            title="Remote Track 1",
            artist="Artist",
            album_artist="Artist",
            album="",
            release_date="2025",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.bcbits.com/stream/t.mp3",
            stream_url_expires_at=9999999999.0,
            id=55,
        )

    def test_play_remote_missing_album_via_id(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        remote = self._remote_track()
        mock_index.get_track_by_id.return_value = remote
        mock_index.preferred_source.return_value = None  # use the track's stream fields
        mock_queue.current.return_value = remote
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        res = c.post(
            "/api/v1/player/play",
            json={"album_artist": "Artist", "album": "", "id": 55, "track_index": 0},
        )
        assert res.status_code == 200
        mock_index.get_track_by_id.assert_called_with(55)


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
            release_date="2024",
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
            release_date="2024",
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
            release_date="2024",
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

    def test_buffering_cleared_on_resolve_exception(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """If resolve_playback_uri raises, buffering is cleared immediately."""
        import time

        remote = Track(
            file_path=Path("bandcamp://999/1"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.example.com/track.mp3",
            stream_url_expires_at=time.time() - 1,  # expired — triggers refresh
        )
        mock_queue.next.return_value = remote
        mock_index.get_collection_item.return_value = {
            "sale_item_id": "999",
            "album_url": "https://artist.bandcamp.com/album/x",
        }
        refresh_fn = MagicMock(side_effect=RuntimeError("network failure"))
        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            refresh_stream_url=refresh_fn,
        )
        c = TestClient(app, raise_server_exceptions=False)

        c.post("/api/v1/player/next")

        # Buffering must be False after the exception — not stuck on.
        assert c.get("/api/v1/player/state").json()["buffering"] is False

    def test_buffering_true_after_remote_play_cleared_by_play_state_change(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """Buffering is cleared by on_play_state_changed only when playing=True.
        A pause transition (playing=False) must NOT clear it — mpv briefly sets
        pause=True during a playing→playing file switch and we must not lose the
        indicator before the new file has actually loaded."""
        import time

        remote = Track(
            file_path=Path("bandcamp://999/1"),
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Album",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            stream_url="https://cdn.example.com/track.mp3",
            stream_url_expires_at=time.time() + 7200,
        )
        mock_queue.next.return_value = remote
        mock_index.get_collection_item.return_value = None
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)

        c.post("/api/v1/player/next")

        # Buffering stays True after the endpoint returns — mpv hasn't started yet.
        assert c.get("/api/v1/player/state").json()["buffering"] is True

        # Simulate mpv's internal pause=True transition (old file ending during
        # a playing→playing switch) — must NOT clear the indicator.
        mock_engine.state.playing = False
        app.state.notify_play_state_changed()
        assert c.get("/api/v1/player/state").json()["buffering"] is True

        # Simulate mpv reporting playing=True (new file is playing).
        mock_engine.state.playing = True
        app.state.notify_play_state_changed()
        assert c.get("/api/v1/player/state").json()["buffering"] is False


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
            release_date="2024",
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

    def test_resolves_via_stream_source_and_refreshes_onto_source(
        self, tmp_path: Path
    ) -> None:
        """A track with a stream source resolves+refreshes via track_sources (KAMP-541)."""
        from kamp_core.library import LibraryIndex

        idx = LibraryIndex(tmp_path / "l.db")
        c = idx._conn
        c.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, album_url)"
            " VALUES ('sid', 'https://a.bandcamp.com/album/x')"
        )
        c.execute("INSERT INTO tracks DEFAULT VALUES")  # KAMP-552: no file_path
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, provider, provider_item_id, uri,"
            " stream_url, stream_url_expires_at)"
            " VALUES (?, 'stream', 'bandcamp', 'sid', 'bandcamp://sid/2',"
            " 'https://cdn/old.mp3', 0.0)",
            (tid,),
        )
        c.commit()
        track = idx.get_track_by_id(tid)
        refresh_fn = MagicMock(return_value=("https://cdn/new.mp3", 9999.0))

        result = resolve_playback_uri(track, idx, refresh_fn)

        refresh_fn.assert_called_once_with("https://a.bandcamp.com/album/x", 2)
        persisted = c.execute(
            "SELECT stream_url FROM track_sources WHERE track_id = ?", (tid,)
        ).fetchone()[0]
        idx.close()
        assert result == "https://cdn/new.mp3"
        assert persisted == "https://cdn/new.mp3"  # written onto the source row

    def test_head_check_passes_returns_cached_url(self) -> None:
        """If HEAD returns 2xx, no refresh is triggered and the cached URL is used."""
        import time

        index = MagicMock()
        check_fn = MagicMock(return_value=200)
        track = self._remote_track(
            stream_url="https://cdn.example.com/valid.mp3",
            stream_url_expires_at=time.time() + 7200,
        )
        result = resolve_playback_uri(track, index, None, check_fn)

        assert result == "https://cdn.example.com/valid.mp3"
        check_fn.assert_called_once_with("https://cdn.example.com/valid.mp3")
        index.update_stream_url.assert_not_called()

    def test_head_check_410_forces_refresh(self) -> None:
        """If HEAD returns 410 Gone, a forced refresh is attempted even if expires_at is future."""
        import time

        index = MagicMock()
        index.get_collection_item.return_value = {
            "album_url": "https://artist.bandcamp.com/album/x"
        }
        check_fn = MagicMock(return_value=410)
        refresh_fn = MagicMock(
            return_value=("https://cdn.example.com/fresh.mp3", 9999.0)
        )
        track = self._remote_track(
            stream_url="https://cdn.example.com/stale.mp3",
            stream_url_expires_at=time.time() + 7200,  # not expired per our estimate
        )
        result = resolve_playback_uri(track, index, refresh_fn, check_fn)

        assert result == "https://cdn.example.com/fresh.mp3"
        refresh_fn.assert_called_once_with("https://artist.bandcamp.com/album/x", 2)
        index.update_stream_url.assert_called_once_with(
            "bandcamp://777/2", "https://cdn.example.com/fresh.mp3", 9999.0
        )

    def test_head_check_403_forces_refresh(self) -> None:
        """HEAD 403 (forbidden) also triggers a forced refresh."""
        import time

        index = MagicMock()
        index.get_collection_item.return_value = {
            "album_url": "https://artist.bandcamp.com/album/x"
        }
        check_fn = MagicMock(return_value=403)
        refresh_fn = MagicMock(
            return_value=("https://cdn.example.com/fresh.mp3", 9999.0)
        )
        track = self._remote_track(
            stream_url="https://cdn.example.com/stale.mp3",
            stream_url_expires_at=time.time() + 7200,
        )
        result = resolve_playback_uri(track, index, refresh_fn, check_fn)

        assert result == "https://cdn.example.com/fresh.mp3"

    def test_head_check_network_error_falls_through(self) -> None:
        """HEAD returning 0 (network error) does not trigger refresh — let mpv try."""
        import time

        index = MagicMock()
        check_fn = MagicMock(return_value=0)
        track = self._remote_track(
            stream_url="https://cdn.example.com/maybe.mp3",
            stream_url_expires_at=time.time() + 7200,
        )
        result = resolve_playback_uri(track, index, None, check_fn)

        assert result == "https://cdn.example.com/maybe.mp3"
        index.update_stream_url.assert_not_called()

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
            release_date="2024",
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

    KAMP-554: the client addresses a missing-album card by track_id; the endpoint
    resolves the track and, for a remote track, falls through to the remote-proxy
    tail which parses the sale_item_id from the resolved track's derived uri.
    """

    _SALE_ID = "123456"
    _TRALBUM_ID = "987654321"
    _JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20

    def _remote_track(self, uri: str = f"bandcamp://{_SALE_ID}/1") -> Track:
        # embedded_art=False so local-art lookup yields nothing and the endpoint
        # falls through to the remote-proxy tail. album="" -> a missing-album card.
        return Track(
            file_path=Path(uri),
            title="Track One",
            artist="Artist",
            album_artist="Artist",
            album="",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            id=1,
        )

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
        mock_index.get_track_by_id.return_value = self._remote_track()
        mock_index.get_collection_item.return_value = self._collection_item()

        c = self._make_app(mock_index, mock_engine, mock_queue, art_cache_dir=cache_dir)
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "A", "album": "B", "track_id": 1},
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
        mock_index.get_track_by_id.return_value = self._remote_track()
        mock_index.get_collection_item.return_value = self._collection_item()

        c = self._make_app(mock_index, mock_engine, mock_queue, art_cache_dir=cache_dir)

        with patch(
            "kamp_daemon.bandcamp.fetch_album_art_bytes", return_value=self._JPEG
        ):
            res = c.get(
                "/api/v1/album-art",
                params={"album_artist": "A", "album": "B", "track_id": 1},
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
        mock_index.get_track_by_id.return_value = self._remote_track()
        mock_index.get_collection_item.return_value = None
        c = self._make_app(
            mock_index, mock_engine, mock_queue, art_cache_dir=tmp_path / "art_cache"
        )
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "A", "album": "B", "track_id": 1},
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
        mock_index.get_track_by_id.return_value = self._remote_track()
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
            params={"album_artist": "A", "album": "B", "track_id": 1},
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
        mock_index.get_track_by_id.return_value = self._remote_track()
        mock_index.get_collection_item.return_value = self._collection_item()
        c = self._make_app(
            mock_index, mock_engine, mock_queue, art_cache_dir=tmp_path / "art_cache"
        )
        with patch("kamp_daemon.bandcamp.fetch_album_art_bytes", return_value=None):
            res = c.get(
                "/api/v1/album-art",
                params={"album_artist": "A", "album": "B", "track_id": 1},
            )
        assert res.status_code == 404

    def test_no_art_cache_dir_returns_404(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """If art_cache_dir is None in make_app, remote art returns 404."""
        mock_index.get_track_by_id.return_value = self._remote_track()
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
            params={"album_artist": "A", "album": "B", "track_id": 1},
        )
        assert res.status_code == 404

    def test_album_artist_album_path_serves_art(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Art request via album_artist+album (no track_id) works for remote albums.

        The UI sends album_artist and album without track_id for normal albums
        (track_id is only populated for missing-album cards). The endpoint must
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
            release_date="2024",
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
        # No track_id — this is the real request the UI sends for normal albums.
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "Artist", "album": "Album"},
        )

        assert res.status_code == 200
        assert res.content == self._JPEG

    def test_windows_backslash_uri_serves_art(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Windows: a resolved track's derived uri is bandcamp:\\sale_id\\1.

        _remote_art_response must strip backslashes as well as forward slashes
        so the sale_item_id is parsed correctly and art is served.
        """
        cache_dir = tmp_path / "art_cache"
        cache_dir.mkdir()
        (cache_dir / f"{self._TRALBUM_ID}.jpg").write_bytes(self._JPEG)
        # Windows-normalised uri: double backslash becomes the separator.
        mock_index.get_track_by_id.return_value = self._remote_track(
            uri=f"bandcamp:\\\\{self._SALE_ID}\\1"
        )
        mock_index.get_collection_item.return_value = self._collection_item()

        c = self._make_app(mock_index, mock_engine, mock_queue, art_cache_dir=cache_dir)
        res = c.get(
            "/api/v1/album-art",
            params={"album_artist": "A", "album": "B", "track_id": 1},
        )

        assert res.status_code == 200
        assert res.content == self._JPEG


class TestIsAvailableField:
    """TrackOut exposes is_available from Track (KAMP-423)."""

    def test_is_available_true_by_default(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1)
        mock_index.tracks_for_album.return_value = [t]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/tracks?album_artist=Artist&album=Album").json()
        assert data[0]["is_available"] is True

    def test_is_available_false_for_unreleased_preorder_track(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        t = _track(1)
        t.is_available = False
        mock_index.tracks_for_album.return_value = [t]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/tracks?album_artist=Artist&album=Album").json()
        assert data[0]["is_available"] is False


class TestIsPreorderField:
    """AlbumOut exposes is_preorder from AlbumInfo (KAMP-423)."""

    def test_is_preorder_false_by_default(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [_album("Artist", "Record")]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/albums").json()
        assert data[0]["is_preorder"] is False

    def test_is_preorder_true_when_album_info_flagged(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        info = _album("Artist", "Record")
        info.is_preorder = True
        mock_index.albums.return_value = [info]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/albums").json()
        assert data[0]["is_preorder"] is True


class TestPreorderUnavailableTracksExcludedFromQueue:
    """Unavailable pre-order tracks must not enter the queue (KAMP-423)."""

    def _unavailable_track(self, n: int) -> "Track":
        t = _track(n)
        t.is_available = False
        return t

    def test_play_album_excludes_unavailable_tracks(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        available = _track(1)
        unavailable = self._unavailable_track(2)
        mock_index.tracks_for_album.return_value = [available, unavailable]
        mock_queue.current.return_value = available
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        c.post(
            "/api/v1/player/play",
            json={"album_artist": "A", "album": "B", "track_index": 0},
        )
        mock_queue.load.assert_called_once_with([available], start_index=0)

    def test_play_album_404_when_all_tracks_unavailable(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [self._unavailable_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        resp = c.post(
            "/api/v1/player/play",
            json={"album_artist": "A", "album": "B", "track_index": 0},
        )
        assert resp.status_code == 404

    def test_play_album_adjusts_start_index_for_filtered_tracks(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        """When track_index points past a filtered-out track, start_index shifts down."""
        t1 = self._unavailable_track(1)
        t2 = _track(2)  # available, originally at index 1
        mock_index.tracks_for_album.return_value = [t1, t2]
        mock_queue.current.return_value = t2
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        # User requests track at index 1 (t2) in the unfiltered list
        c.post(
            "/api/v1/player/play",
            json={"album_artist": "A", "album": "B", "track_index": 1},
        )
        # After filtering t1 out, t2 is at index 0 in the available list
        mock_queue.load.assert_called_once_with([t2], start_index=0)

    def test_add_album_to_queue_excludes_unavailable_tracks(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        available = _track(1)
        unavailable = self._unavailable_track(2)
        mock_index.tracks_for_album.return_value = [available, unavailable]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        c.post(
            "/api/v1/player/queue/add-album", json={"album_artist": "A", "album": "B"}
        )
        mock_queue.add_album_to_queue.assert_called_once_with([available])

    def test_play_album_next_excludes_unavailable_tracks(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        available = _track(1)
        unavailable = self._unavailable_track(2)
        mock_index.tracks_for_album.return_value = [available, unavailable]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        c.post(
            "/api/v1/player/queue/play-album-next",
            json={"album_artist": "A", "album": "B"},
        )
        mock_queue.play_album_next.assert_called_once_with([available])

    def test_insert_album_excludes_unavailable_tracks(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        available = _track(1)
        unavailable = self._unavailable_track(2)
        mock_index.tracks_for_album.return_value = [available, unavailable]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        c.post(
            "/api/v1/player/queue/insert-album",
            json={"album_artist": "A", "album": "B", "index": 0},
        )
        mock_queue.insert_album_at.assert_called_once_with([available], 0)

    def test_add_album_to_queue_404_when_all_tracks_unavailable(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.tracks_for_album.return_value = [self._unavailable_track(1)]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        resp = c.post(
            "/api/v1/player/queue/add-album", json={"album_artist": "A", "album": "B"}
        )
        assert resp.status_code == 404


class TestAlbumUrlField:
    """AlbumOut exposes album_url from AlbumInfo (KAMP-367)."""

    def test_album_url_in_response(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        info = _album("Artist", "Record")
        info.album_url = "https://artist.bandcamp.com/album/record"
        mock_index.albums.return_value = [info]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/albums").json()
        assert data[0]["album_url"] == "https://artist.bandcamp.com/album/record"

    def test_album_url_empty_string_by_default(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.albums.return_value = [_album("Artist", "Record")]
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        c = TestClient(app)
        data = c.get("/api/v1/albums").json()
        assert data[0]["album_url"] == ""


# ---------------------------------------------------------------------------
# Playlist endpoints (KAMP-441)
# ---------------------------------------------------------------------------


def _playlist(
    id: int = 1,
    title: str = "Test Mix",
    favorite: bool = False,
    track_count: int = 0,
    last_played_at: float | None = None,
) -> dict:
    import time

    now = time.time()
    return {
        "id": id,
        "title": title,
        "favorite": favorite,
        "track_count": track_count,
        "created_at": now,
        "updated_at": now,
        "last_played_at": last_played_at,
    }


def _playlist_track(
    playlist_track_id: int = 1,
    position: int = 0,
    file_path: str = "/lib/a.mp3",
    title: str = "A Track",
) -> dict:
    return {
        "playlist_track_id": playlist_track_id,
        "position": position,
        "id": 10,
        "file_path": file_path,
        "title": title,
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "release_date": "2024",
        "track_number": 1,
        "disc_number": 1,
        "ext": "mp3",
        "embedded_art": False,
        "mb_release_id": "",
        "mb_recording_id": "",
        "genre": "",
        "label": "",
        "favorite": False,
        "play_count": 0,
        "source": "local",
        "is_available": True,
        "duration": 180.0,
    }


class TestPlaylistEndpoints:
    def test_create_playlist(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.create_playlist.return_value = _playlist(title="My Mix")
        resp = client.post("/api/v1/playlists", json={"title": "My Mix"})
        assert resp.status_code == 201
        assert resp.json()["title"] == "My Mix"
        mock_index.create_playlist.assert_called_once_with("My Mix")

    def test_list_playlists(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlists.return_value = [_playlist(id=1), _playlist(id=2)]
        resp = client.get("/api/v1/playlists")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_playlist(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = _playlist(id=7, title="Road Trip")
        resp = client.get("/api/v1/playlists/7")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Road Trip"

    def test_get_playlist_404(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = None
        assert client.get("/api/v1/playlists/999").status_code == 404

    def test_patch_playlist_title(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        pl = _playlist(id=1, title="New Name")
        mock_index.get_playlist.return_value = pl
        resp = client.patch("/api/v1/playlists/1", json={"title": "New Name"})
        assert resp.status_code == 200
        mock_index.rename_playlist.assert_called_once_with(1, "New Name")

    def test_patch_playlist_favorite(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        pl = _playlist(id=1, favorite=True)
        mock_index.get_playlist.return_value = pl
        resp = client.patch("/api/v1/playlists/1", json={"favorite": True})
        assert resp.status_code == 200
        mock_index.set_playlist_favorite.assert_called_once_with(1, True)

    def test_patch_playlist_404(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        assert (
            client.patch("/api/v1/playlists/999", json={"title": "x"}).status_code
            == 404
        )

    def test_delete_playlist(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        resp = client.delete("/api/v1/playlists/1")
        assert resp.status_code == 204
        mock_index.delete_playlist.assert_called_once_with(1)

    def test_delete_playlist_404(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        assert client.delete("/api/v1/playlists/999").status_code == 404

    def test_get_playlist_tracks(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.get_playlist_tracks.return_value = [_playlist_track()]
        resp = client.get("/api/v1/playlists/1/tracks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_playlist_tracks_404(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        assert client.get("/api/v1/playlists/999/tracks").status_code == 404

    def test_add_track_by_id(self, client: TestClient, mock_index: MagicMock) -> None:
        # KAMP-552: single tracks are added by canonical id; the endpoint resolves
        # the track and keys the playlist write on its canonical uri.
        track = _track(1)
        track.file_path = Path("/lib/a.mp3")
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.get_track_by_id.return_value = track
        resp = client.post("/api/v1/playlists/1/tracks", json={"id": 1})
        assert resp.status_code == 200
        # str(Path) so the expected separator matches the platform (Windows: \\).
        mock_index.add_track_to_playlist.assert_called_once_with(
            1, str(track.file_path)
        )

    def test_add_album_tracks(self, client: TestClient, mock_index: MagicMock) -> None:
        from kamp_core.library import Track

        t = Track(
            file_path="/lib/a.mp3",
            title="A",
            artist="Ar",
            album_artist="Ar",
            album="Al",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
        )
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.tracks_for_album.return_value = [t]
        resp = client.post(
            "/api/v1/playlists/1/tracks",
            json={"album_artist": "Ar", "album": "Al"},
        )
        assert resp.status_code == 200
        mock_index.add_track_to_playlist.assert_called_once_with(1, "/lib/a.mp3")

    def test_add_track_missing_body_fields(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        resp = client.post("/api/v1/playlists/1/tracks", json={})
        assert resp.status_code == 400

    def test_add_track_playlist_404(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        resp = client.post("/api/v1/playlists/999/tracks", json={"file_path": "/x.mp3"})
        assert resp.status_code == 404

    def test_remove_track(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        resp = client.delete("/api/v1/playlists/1/tracks/5")
        assert resp.status_code == 204
        mock_index.remove_track_from_playlist.assert_called_once_with(1, 5)

    def test_remove_track_playlist_404(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        assert client.delete("/api/v1/playlists/999/tracks/1").status_code == 404

    def test_reorder_playlist(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        resp = client.put("/api/v1/playlists/1/order", json={"track_ids": [3, 1, 2]})
        assert resp.status_code == 200
        mock_index.reorder_playlist_tracks.assert_called_once_with(1, [3, 1, 2])

    def test_reorder_playlist_invalid_ids(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.reorder_playlist_tracks.side_effect = ValueError("bad ids")
        resp = client.put("/api/v1/playlists/1/order", json={"track_ids": [99]})
        assert resp.status_code == 400

    def test_reorder_playlist_404(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        assert (
            client.put(
                "/api/v1/playlists/999/order", json={"track_ids": [1]}
            ).status_code
            == 404
        )

    def test_playlist_art_returns_svg(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1, title="My Mix")
        resp = client.get("/api/v1/playlists/1/art")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        assert "My Mix" in resp.text

    def test_playlist_art_truncates_long_title(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(
            id=1, title="A Very Long Playlist Title That Should Be Truncated"
        )
        resp = client.get("/api/v1/playlists/1/art")
        assert resp.status_code == 200
        assert "A Very Long…" in resp.text

    def test_playlist_art_404(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = None
        assert client.get("/api/v1/playlists/999/art").status_code == 404

    def test_play_playlist_records_last_played(
        self, client: TestClient, mock_index: MagicMock, mock_queue: MagicMock
    ) -> None:
        from kamp_core.library import Track

        t = Track(
            file_path="/lib/a.mp3",
            title="A",
            artist="Ar",
            album_artist="Ar",
            album="Al",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
        )
        mock_index.get_playlist.return_value = _playlist(id=3)
        mock_index.tracks_for_playlist.return_value = [t]
        mock_queue.current.return_value = None

        resp = client.post(
            "/api/v1/player/play-playlist", json={"playlist_id": 3, "start_index": 0}
        )

        assert resp.status_code == 200
        mock_index.record_playlist_played.assert_called_once_with(3)

    def test_play_playlist_empty_does_not_record_last_played(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=5)
        mock_index.tracks_for_playlist.return_value = []

        resp = client.post(
            "/api/v1/player/play-playlist", json={"playlist_id": 5, "start_index": 0}
        )

        assert resp.status_code == 200
        mock_index.record_playlist_played.assert_not_called()

    def test_record_playlist_played_endpoint(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=7)
        resp = client.post("/api/v1/playlists/7/played")
        assert resp.status_code == 204
        mock_index.record_playlist_played.assert_called_once_with(7)

    def test_record_playlist_played_endpoint_404(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        assert client.post("/api/v1/playlists/999/played").status_code == 404


_SAMPLE_CRITERIA = {
    "groups": [
        {
            "conditions": [{"field": "track.artist", "op": "is", "value": "Alvvays"}],
            "match": "all",
            "negate": False,
        }
    ],
    "match": "all",
}


class TestPlaylistModuleContentEndpoint:
    def test_returns_content_list(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.get_playlist_module_content.return_value = [
            {"album_artist": "Alvvays", "album": "Antisocialites"}
        ]
        resp = client.get(
            "/api/v1/playlists/1/module-content?contents=albums&sort=random&limit=5"
        )
        assert resp.status_code == 200
        assert resp.json()[0]["album"] == "Antisocialites"
        mock_index.get_playlist_module_content.assert_called_once_with(
            1, "albums", "random", 5
        )

    def test_returns_404_for_missing_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        assert client.get("/api/v1/playlists/999/module-content").status_code == 404


class TestMagicPlaylistEndpoints:
    """Tests for magic playlist routes added in KAMP-461."""

    # ------------------------------------------------------------------
    # POST /api/v1/playlists — with criteria
    # ------------------------------------------------------------------

    def test_create_magic_playlist_calls_create_magic_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        from kamp_core.library import MagicCriteria

        mock_index.create_magic_playlist.return_value = 5
        mock_index.get_playlist.return_value = _playlist(id=5, title="Smart Mix")
        resp = client.post(
            "/api/v1/playlists",
            json={"title": "Smart Mix", "criteria": _SAMPLE_CRITERIA},
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == 5
        mock_index.create_magic_playlist.assert_called_once()
        # criteria key is present in the response (enriched from get_magic_playlist_criteria
        # which returns None by default in the fixture → criteria=None)
        assert "criteria" in resp.json()

    def test_create_magic_playlist_invalid_criteria_returns_400(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        resp = client.post(
            "/api/v1/playlists", json={"title": "Bad", "criteria": {"bad": "data"}}
        )
        assert resp.status_code == 400

    def test_create_static_playlist_unaffected(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.create_playlist.return_value = _playlist(title="Static")
        resp = client.post("/api/v1/playlists", json={"title": "Static"})
        assert resp.status_code == 201
        mock_index.create_playlist.assert_called_once_with("Static")
        mock_index.create_magic_playlist.assert_not_called()

    # ------------------------------------------------------------------
    # GET /api/v1/playlists — criteria field + ?type=simple filter
    # ------------------------------------------------------------------

    def test_list_playlists_includes_criteria_field(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlists.return_value = [_playlist(id=1)]
        resp = client.get("/api/v1/playlists")
        assert resp.status_code == 200
        assert "criteria" in resp.json()[0]

    def test_list_playlists_type_simple_excludes_magic(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        from kamp_core.library import Condition, Group, MagicCriteria

        mock_index.get_playlists.return_value = [_playlist(id=1), _playlist(id=2)]
        mc = MagicCriteria(
            groups=[
                Group(conditions=[Condition("track.artist", "is", "X")], match="all")
            ],
            match="all",
        )
        # playlist 1 is magic, playlist 2 is static
        mock_index.get_magic_playlist_criteria.side_effect = lambda pid: (
            mc if pid == 1 else None
        )
        resp = client.get("/api/v1/playlists?type=simple")
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()]
        assert 1 not in ids
        assert 2 in ids

    def test_list_playlists_no_type_includes_all(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        from kamp_core.library import Condition, Group, MagicCriteria

        mock_index.get_playlists.return_value = [_playlist(id=1), _playlist(id=2)]
        mc = MagicCriteria(
            groups=[
                Group(conditions=[Condition("track.artist", "is", "X")], match="all")
            ],
            match="all",
        )
        mock_index.get_magic_playlist_criteria.side_effect = lambda pid: (
            mc if pid == 1 else None
        )
        resp = client.get("/api/v1/playlists")
        ids = [p["id"] for p in resp.json()]
        assert ids == [1, 2]

    # ------------------------------------------------------------------
    # GET /api/v1/playlists/{id}/tracks — magic branch
    # ------------------------------------------------------------------

    def test_get_magic_playlist_tracks_calls_get_magic_tracks(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        from kamp_core.library import Condition, Group, MagicCriteria

        mc = MagicCriteria(
            groups=[
                Group(conditions=[Condition("track.artist", "is", "X")], match="all")
            ],
            match="all",
        )
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.get_magic_playlist_criteria.return_value = mc
        magic_track = dict(_playlist_track(), playlist_track_id=None, position=0)
        mock_index.get_magic_playlist_tracks.return_value = [magic_track]
        resp = client.get("/api/v1/playlists/1/tracks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["playlist_track_id"] is None
        mock_index.get_magic_playlist_tracks.assert_called_once_with(1)
        mock_index.get_playlist_tracks.assert_not_called()

    def test_get_static_playlist_tracks_unaffected(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        # get_magic_playlist_criteria returns None by default (static playlist)
        mock_index.get_playlist_tracks.return_value = [_playlist_track()]
        resp = client.get("/api/v1/playlists/1/tracks")
        assert resp.status_code == 200
        mock_index.get_playlist_tracks.assert_called_once_with(1)
        mock_index.get_magic_playlist_tracks.assert_not_called()

    # ------------------------------------------------------------------
    # PUT /api/v1/playlists/{id}/criteria
    # ------------------------------------------------------------------

    def test_put_criteria_updates_and_returns_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        resp = client.put(
            "/api/v1/playlists/1/criteria", json={"criteria": _SAMPLE_CRITERIA}
        )
        assert resp.status_code == 200
        mock_index.update_magic_playlist_criteria.assert_called_once()

    def test_put_criteria_404(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = None
        resp = client.put(
            "/api/v1/playlists/999/criteria", json={"criteria": _SAMPLE_CRITERIA}
        )
        assert resp.status_code == 404

    def test_put_criteria_not_magic_returns_400(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.update_magic_playlist_criteria.side_effect = ValueError(
            "not a magic playlist"
        )
        resp = client.put(
            "/api/v1/playlists/1/criteria", json={"criteria": _SAMPLE_CRITERIA}
        )
        assert resp.status_code == 400

    def test_put_criteria_invalid_body_returns_400(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        resp = client.put(
            "/api/v1/playlists/1/criteria", json={"criteria": {"bad": True}}
        )
        assert resp.status_code == 400

    # ------------------------------------------------------------------
    # GET /api/v1/playlists/{id}/criteria
    # ------------------------------------------------------------------

    def test_get_criteria_returns_dict_for_magic_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        from kamp_core.library import Condition, Group, MagicCriteria

        mc = MagicCriteria(
            groups=[
                Group(conditions=[Condition("track.artist", "is", "X")], match="all")
            ],
            match="all",
        )
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.get_magic_playlist_criteria.return_value = mc
        resp = client.get("/api/v1/playlists/1/criteria")
        assert resp.status_code == 200
        body = resp.json()
        assert "criteria" in body
        assert body["criteria"]["match"] == "all"

    def test_get_criteria_returns_null_for_static_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        resp = client.get("/api/v1/playlists/1/criteria")
        assert resp.status_code == 200
        assert resp.json()["criteria"] is None

    def test_get_criteria_404(self, client: TestClient, mock_index: MagicMock) -> None:
        mock_index.get_playlist.return_value = None
        assert client.get("/api/v1/playlists/999/criteria").status_code == 404

    # ------------------------------------------------------------------
    # POST /api/v1/criteria/preview
    # ------------------------------------------------------------------

    def test_criteria_preview_returns_count(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.count_magic_criteria.return_value = 42
        resp = client.post(
            "/api/v1/criteria/preview", json={"criteria": _SAMPLE_CRITERIA}
        )
        assert resp.status_code == 200
        assert resp.json() == {"count": 42}
        mock_index.count_magic_criteria.assert_called_once()

    def test_criteria_preview_invalid_criteria_returns_400(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        resp = client.post("/api/v1/criteria/preview", json={"criteria": {"bad": True}})
        assert resp.status_code == 400


class TestPlaylistArt:
    def _make_jpeg_bytes(self) -> bytes:
        import io

        from PIL import Image

        img = Image.new("RGB", (100, 100), color=(128, 64, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    def test_get_art_returns_jpeg_when_cover_present(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.get_playlist_cover.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 60

        resp = client.get("/api/v1/playlists/1/art")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    def test_get_art_falls_back_to_svg_when_no_cover(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1, title="My Mix")
        mock_index.get_playlist_cover.return_value = None

        resp = client.get("/api/v1/playlists/1/art")

        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers["content-type"]
        assert "My Mix" in resp.text

    def test_post_art_happy_path_returns_playlist_out(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        updated = _playlist(id=1, title="Art Test")
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.set_playlist_cover.return_value = updated
        image_bytes = self._make_jpeg_bytes()

        with patch("kamp_daemon.artwork.validate_image_bytes"):
            resp = client.post(
                "/api/v1/playlists/1/art",
                files={"file": ("cover.jpg", image_bytes, "image/jpeg")},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Art Test"
        mock_index.set_playlist_cover.assert_called_once_with(1, image_bytes)

    def test_post_art_404_when_playlist_not_found(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = None
        image_bytes = self._make_jpeg_bytes()

        with patch("kamp_daemon.artwork.validate_image_bytes"):
            resp = client.post(
                "/api/v1/playlists/999/art",
                files={"file": ("cover.jpg", image_bytes, "image/jpeg")},
            )

        assert resp.status_code == 404

    def test_post_art_422_for_non_image_content_type(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        resp = client.post(
            "/api/v1/playlists/1/art",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 422

    def test_post_art_422_for_corrupt_image(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        from kamp_daemon.artwork import ArtworkError

        mock_index.get_playlist.return_value = _playlist(id=1)

        with patch(
            "kamp_daemon.artwork.validate_image_bytes",
            side_effect=ArtworkError("not a valid image"),
        ):
            resp = client.post(
                "/api/v1/playlists/1/art",
                files={"file": ("cover.jpg", b"\xff\xd8\xff not real", "image/jpeg")},
            )

        assert resp.status_code == 422

    def test_post_art_preserves_criteria_for_magic_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        """Regression: POST /art used to return criteria: null for magic playlists."""
        from kamp_core.library import Condition, Group, MagicCriteria

        mc = MagicCriteria(
            groups=[
                Group(
                    match="all",
                    conditions=[
                        Condition(field="track.favorite", op="is", value="true")
                    ],
                )
            ],
            match="all",
        )
        updated = _playlist(id=5, title="Faves")
        mock_index.get_playlist.return_value = _playlist(id=5)
        mock_index.set_playlist_cover.return_value = updated
        mock_index.get_magic_playlist_criteria.return_value = mc
        image_bytes = self._make_jpeg_bytes()

        with patch("kamp_daemon.artwork.validate_image_bytes"):
            resp = client.post(
                "/api/v1/playlists/5/art",
                files={"file": ("cover.jpg", image_bytes, "image/jpeg")},
            )

        assert resp.status_code == 200
        assert resp.json()["criteria"] is not None


class TestMagicPlaylistReactivity:
    """Tests for the field_index rebuild and on_fields_changed callback (KAMP-462)."""

    def test_field_index_rebuilt_after_create_magic_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.create_magic_playlist.return_value = 5
        mock_index.get_playlist.return_value = _playlist(id=5, title="Favs")
        mock_index.list_all_magic_criteria.reset_mock()
        client.post(
            "/api/v1/playlists",
            json={"title": "Favs", "criteria": _SAMPLE_CRITERIA},
        )
        mock_index.list_all_magic_criteria.assert_called()

    def test_field_index_not_rebuilt_after_create_static_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.create_playlist.return_value = _playlist(title="Static")
        mock_index.list_all_magic_criteria.reset_mock()
        client.post("/api/v1/playlists", json={"title": "Static"})
        mock_index.list_all_magic_criteria.assert_not_called()

    def test_field_index_rebuilt_after_delete_playlist(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.list_all_magic_criteria.reset_mock()
        client.delete("/api/v1/playlists/1")
        mock_index.list_all_magic_criteria.assert_called()

    def test_field_index_rebuilt_after_update_criteria(
        self, client: TestClient, mock_index: MagicMock
    ) -> None:
        mock_index.get_playlist.return_value = _playlist(id=1)
        mock_index.list_all_magic_criteria.reset_mock()
        client.put("/api/v1/playlists/1/criteria", json={"criteria": _SAMPLE_CRITERIA})
        mock_index.list_all_magic_criteria.assert_called()

    def test_on_fields_changed_is_callable_and_no_crash(
        self, client: TestClient
    ) -> None:
        # Verify the callback is exposed on app.state and can be called without
        # error even with no WebSocket clients connected (_event_loop is None).
        on_fields_changed = client.app.state.on_fields_changed
        assert callable(on_fields_changed)
        on_fields_changed({"track.favorite"})  # must not raise


# ---------------------------------------------------------------------------
# Display override endpoints (KAMP-467)
# ---------------------------------------------------------------------------


def _bandcamp_track(n: int = 1) -> Track:
    return Track(
        file_path=Path(f"bandcamp://42/{n}"),
        title=f"Track {n}",
        artist="Band",
        album_artist="Band",
        album="Long Name",
        release_date="2020",
        track_number=n,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
        source="bandcamp",
    )


class TestPatchTrackDisplayEndpoint:
    """PATCH /api/v1/tracks/{track_id}/display — display-only title override."""

    def test_returns_updated_track(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = _bandcamp_track()
        track.id = 7
        updated = Track(**{**track.__dict__, "title": "Short Name"})
        updated.id = 7
        mock_index.get_track_by_id.return_value = track
        mock_index.update_track_display_title.return_value = updated

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/tracks/7/display", json={"display_title": "Short Name"}
        )

        assert resp.status_code == 200
        assert resp.json()["title"] == "Short Name"
        mock_index.update_track_display_title.assert_called_once_with(7, "Short Name")

    def test_returns_404_for_missing_track(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.get_track_by_id.return_value = None

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/tracks/9999/display", json={"display_title": "X"}
        )

        assert resp.status_code == 404


class TestPatchAlbumDisplayEndpoint:
    """PATCH /api/v1/albums/display — display-only album title and artist override."""

    def _album_info(self) -> AlbumInfo:
        return AlbumInfo(
            album_artist="Band",
            album="Long Name",
            release_date="2020",
            track_count=1,
            has_art=False,
            source="bandcamp",
            display_album="Short",
            display_album_artist="B",
        )

    def test_returns_updated_album(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.update_album_display.return_value = self._album_info()

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/albums/display",
            json={
                "album_artist": "Band",
                "album": "Long Name",
                "display_album": "Short",
                "display_album_artist": "B",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["display_album"] == "Short"
        assert data["display_album_artist"] == "B"
        mock_index.update_album_display.assert_called_once_with(
            "Band", "Long Name", "Short", "B"
        )

    def test_returns_404_for_missing_album(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.update_album_display.return_value = None

        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).patch(
            "/api/v1/albums/display",
            json={
                "album_artist": "Ghost",
                "album": "Phantom",
                "display_album": None,
                "display_album_artist": None,
            },
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/stats (KAMP-481)
# ---------------------------------------------------------------------------


class TestStatsEndpoint:
    def test_returns_stats_with_defaults(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_stats.return_value = LibraryStats(
            track_count=100,
            album_count=12,
            artist_count=8,
            total_play_seconds=3600.0,
            total_track_plays=50,
            albums_played=5,
            top_artist_name="Slowdive",
            top_artist_seconds=1800.0,
            top_tracks=[],
        )
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["track_count"] == 100
        assert data["album_count"] == 12
        assert data["artist_count"] == 8
        assert data["total_play_seconds"] == pytest.approx(3600.0)
        assert data["total_track_plays"] == 50
        assert data["albums_played"] == 5
        assert data["top_artist_name"] == "Slowdive"
        assert data["top_artist_seconds"] == pytest.approx(1800.0)
        assert data["top_tracks"] == []
        mock_index.get_stats.assert_called_once_with(top_tracks_limit=3)

    def test_top_tracks_query_param_forwarded(
        self, mock_index: MagicMock, mock_engine: MagicMock, mock_queue: MagicMock
    ) -> None:
        mock_index.get_stats.return_value = LibraryStats(
            track_count=0,
            album_count=0,
            artist_count=0,
            total_play_seconds=0.0,
            total_track_plays=0,
            albums_played=0,
            top_artist_name=None,
            top_artist_seconds=None,
            top_tracks=[],
        )
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        TestClient(app).get("/api/v1/stats?top_tracks=5")
        mock_index.get_stats.assert_called_once_with(top_tracks_limit=5)
