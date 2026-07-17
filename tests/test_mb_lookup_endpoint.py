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
    track.artist = "MB Track Artist"

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
# GET /api/v1/albums/musicbrainz — shallow candidate list (KAMP-584)
# ---------------------------------------------------------------------------


class TestGetAlbumMusicBrainz:
    """GET /api/v1/albums/musicbrainz returns shallow ranked MB candidates."""

    def _app(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
        **kwargs: object,
    ) -> TestClient:
        return TestClient(
            create_app(index=mock_index, engine=mock_engine, queue=mock_queue, **kwargs)
        )

    def test_happy_path_returns_shallow_candidates(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1), _track(2)]
        search_fn = MagicMock(return_value=[_fake_release()])

        client = self._app(mock_index, mock_engine, mock_queue, mb_search_fn=search_fn)
        resp = client.get(
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
        assert c["is_current"] is False
        # Shallow contract: candidates never carry a track list.
        assert "tracks" not in c

        # One album-level search — no per-track tuples anymore.
        search_fn.assert_called_once_with("Band", "Record")

    def test_stored_release_id_pinned_first_and_current(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = _track(1)
        track.mb_release_id = "stored-1"
        mock_index.tracks_for_album.return_value = [track]
        search_fn = MagicMock(
            return_value=[
                _fake_release("s1"),
                _fake_release("stored-1"),
                _fake_release("s2"),
            ]
        )
        release_fn = MagicMock(return_value=_fake_release("stored-1"))

        client = self._app(
            mock_index,
            mock_engine,
            mock_queue,
            mb_search_fn=search_fn,
            mb_release_fn=release_fn,
        )
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )

        assert resp.status_code == 200
        candidates = resp.json()["candidates"]
        assert [c["mbid"] for c in candidates] == ["stored-1", "s1", "s2"]
        assert candidates[0]["is_current"] is True
        assert candidates[1]["is_current"] is False
        release_fn.assert_called_once_with("stored-1")

    def test_pin_dedupes_on_canonical_id(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """A merged stored mbid resolves to a new id; dedupe uses the returned id."""
        track = _track(1)
        track.mb_release_id = "merged-away"
        mock_index.tracks_for_album.return_value = [track]
        search_fn = MagicMock(
            return_value=[_fake_release("canonical-1"), _fake_release("s2")]
        )
        release_fn = MagicMock(return_value=_fake_release("canonical-1"))

        client = self._app(
            mock_index,
            mock_engine,
            mock_queue,
            mb_search_fn=search_fn,
            mb_release_fn=release_fn,
        )
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )

        candidates = resp.json()["candidates"]
        assert [c["mbid"] for c in candidates] == ["canonical-1", "s2"]
        assert candidates[0]["is_current"] is True

    def test_pin_degrades_to_search_on_failure(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        """A dead stored mbid must not fail the whole lookup."""
        track = _track(1)
        track.mb_release_id = "gone-mbid"
        mock_index.tracks_for_album.return_value = [track]
        search_fn = MagicMock(return_value=[_fake_release("s1")])
        release_fn = MagicMock(side_effect=Exception("not found"))

        client = self._app(
            mock_index,
            mock_engine,
            mock_queue,
            mb_search_fn=search_fn,
            mb_release_fn=release_fn,
        )
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )

        assert resp.status_code == 200
        assert [c["mbid"] for c in resp.json()["candidates"]] == ["s1"]

    def test_candidates_capped_at_ten(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        track = _track(1)
        track.mb_release_id = "stored-1"
        mock_index.tracks_for_album.return_value = [track]
        search_fn = MagicMock(return_value=[_fake_release(f"s{i}") for i in range(12)])
        release_fn = MagicMock(return_value=_fake_release("stored-1"))

        client = self._app(
            mock_index,
            mock_engine,
            mock_queue,
            mb_search_fn=search_fn,
            mb_release_fn=release_fn,
        )
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )

        assert len(resp.json()["candidates"]) == 10

    def test_returns_404_when_album_not_found(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = []

        client = self._app(
            mock_index, mock_engine, mock_queue, mb_search_fn=MagicMock()
        )
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Ghost", "album": "Void"},
        )
        assert resp.status_code == 404

    def test_returns_404_when_search_raises(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]
        search_fn = MagicMock(side_effect=Exception("MB is down"))

        client = self._app(mock_index, mock_engine, mock_queue, mb_search_fn=search_fn)
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Unknown", "album": "Untitled"},
        )
        assert resp.status_code == 404
        assert "MB is down" in resp.json()["detail"]

    def test_returns_404_when_no_results(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]

        client = self._app(
            mock_index, mock_engine, mock_queue, mb_search_fn=MagicMock(return_value=[])
        )
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Unknown", "album": "Untitled"},
        )
        assert resp.status_code == 404
        assert "No MusicBrainz results" in resp.json()["detail"]

    def test_returns_503_when_search_fn_not_wired(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        mock_index.tracks_for_album.return_value = [_track(1)]

        client = self._app(mock_index, mock_engine, mock_queue)  # no mb fns
        resp = client.get(
            "/api/v1/albums/musicbrainz",
            params={"album_artist": "Band", "album": "Record"},
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/v1/albums/musicbrainz/release/{mbid} — on-demand hydration (KAMP-584)
# ---------------------------------------------------------------------------


class TestGetMusicBrainzRelease:
    def test_returns_full_release_with_tracks(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        release_fn = MagicMock(return_value=_fake_release("release-1"))

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_release_fn=release_fn,
        )
        resp = TestClient(app).get("/api/v1/albums/musicbrainz/release/release-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["mbid"] == "release-1"
        assert len(data["tracks"]) == 1
        assert data["tracks"][0]["title"] == "MB Track 1"
        assert data["tracks"][0]["recording_mbid"] == "rec-1"
        # KAMP-583: per-track credited artist reaches the modal.
        assert data["tracks"][0]["artist"] == "MB Track Artist"
        release_fn.assert_called_once_with("release-1")

    def test_tracks_sorted_by_disc_then_number(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        t1, t2, t3 = MagicMock(), MagicMock(), MagicMock()
        t1.number, t1.disc, t1.title, t1.recording_mbid = 2, 1, "Second", "rec-2"
        t2.number, t2.disc, t2.title, t2.recording_mbid = 1, 2, "Disc2T1", "rec-3"
        t3.number, t3.disc, t3.title, t3.recording_mbid = 1, 1, "First", "rec-1"
        for t in (t1, t2, t3):
            t.artist = "A"
        release = _fake_release()
        release.tracks = {"1-2": t1, "2-1": t2, "1-1": t3}

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_release_fn=MagicMock(return_value=release),
        )
        resp = TestClient(app).get("/api/v1/albums/musicbrainz/release/release-1")

        titles = [t["title"] for t in resp.json()["tracks"]]
        assert titles == ["First", "Second", "Disc2T1"]

    def test_returns_404_when_release_not_found(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        release_fn = MagicMock(side_effect=Exception("entity not found"))

        app = create_app(
            index=mock_index,
            engine=mock_engine,
            queue=mock_queue,
            mb_release_fn=release_fn,
        )
        resp = TestClient(app).get("/api/v1/albums/musicbrainz/release/gone-mbid")

        assert resp.status_code == 404
        assert "entity not found" in resp.json()["detail"]

    def test_returns_503_when_release_fn_not_wired(
        self,
        mock_index: MagicMock,
        mock_engine: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        app = create_app(index=mock_index, engine=mock_engine, queue=mock_queue)
        resp = TestClient(app).get("/api/v1/albums/musicbrainz/release/any-mbid")
        assert resp.status_code == 503


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
