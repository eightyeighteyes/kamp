"""Local album art is extracted once and then served from disk (KAMP-680).

Why this exists, measured rather than assumed:

``GET /api/v1/album-art`` called ``extract_art`` on every request, which does a
full ``id3.ID3()`` parse (every frame, artwork included) and then *copies* the
image with ``bytes(frame.data)``. Calling it once per album across the real
library cost **+429 MiB of permanently resident RSS** while Python held 1.1 MiB —
allocator retention, not a reference leak. The UI requests art for every album
card on every launch, so that repeated on each start.

The fix removes the mechanism: extract once, write to the art cache, and serve
the file afterwards with ``FileResponse`` so the bytes never land in the Python
heap at all. Remote art already worked this way; this brings local art in line.

The cache key carries the album's ``art_version``, so re-embedding art changes the
key and stale bytes can never be served.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kamp_core.server import create_app

from .test_server import _track  # shared fixture helper


@pytest.fixture
def art_cache(tmp_path: Path) -> Path:
    d = tmp_path / "art_cache"
    d.mkdir()
    return d


def _index_with_local_art(album_id: int = 7, art_version: float = 1234.5) -> MagicMock:
    index = MagicMock()
    track = _track(1)
    track.embedded_art = True
    track.album_id = album_id
    index.tracks_for_album.return_value = [track]
    index.album_identity_for_ids.return_value = {album_id: {"art_version": art_version}}
    return index


def _app(index: MagicMock, art_cache: Path | None) -> Any:
    return create_app(
        index=index,
        engine=MagicMock(),
        queue=MagicMock(),
        art_cache_dir=art_cache,
    )


def test_first_request_extracts_and_serves(art_cache: Path) -> None:
    index = _index_with_local_art()
    client = TestClient(_app(index, art_cache))

    with patch(
        "kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")
    ) as extract:
        res = client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    assert res.status_code == 200
    assert res.content == b"IMGDATA"
    assert "image/jpeg" in res.headers["content-type"]
    assert extract.call_count == 1


def test_first_request_populates_the_cache(art_cache: Path) -> None:
    index = _index_with_local_art()
    client = TestClient(_app(index, art_cache))

    with patch("kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")):
        client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    written = list(art_cache.rglob("*.jpg"))
    assert len(written) == 1, f"expected one cached image, found {written}"
    assert written[0].read_bytes() == b"IMGDATA"


def test_second_request_does_not_touch_the_audio_file(art_cache: Path) -> None:
    # The whole point: the +429 MiB came from re-parsing tags per request.
    index = _index_with_local_art()
    client = TestClient(_app(index, art_cache))

    with patch("kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")):
        client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    with patch("kamp_core.server.extract_art") as extract:
        res = client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    assert res.status_code == 200
    assert res.content == b"IMGDATA"
    extract.assert_not_called()


def test_changed_art_version_is_a_cache_miss(art_cache: Path) -> None:
    # Re-embedding art bumps art_version; serving the old bytes would be a
    # visible bug, so the version has to be part of the key.
    index = _index_with_local_art(art_version=1.0)
    client = TestClient(_app(index, art_cache))
    with patch("kamp_core.server.extract_art", return_value=(b"OLD", "image/jpeg")):
        client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    index.album_identity_for_ids.return_value = {7: {"art_version": 2.0}}
    with patch("kamp_core.server.extract_art", return_value=(b"NEW", "image/jpeg")):
        res = client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    assert res.content == b"NEW"


def test_png_art_round_trips_with_its_own_mime(art_cache: Path) -> None:
    index = _index_with_local_art()
    client = TestClient(_app(index, art_cache))

    with patch("kamp_core.server.extract_art", return_value=(b"PNGDATA", "image/png")):
        client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    with patch("kamp_core.server.extract_art") as extract:
        res = client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    extract.assert_not_called()
    assert res.content == b"PNGDATA"
    assert "image/png" in res.headers["content-type"]


def test_unwritable_cache_still_serves_the_art(art_cache: Path) -> None:
    # A broken cache must degrade to the old behaviour, not to a 500.
    index = _index_with_local_art()
    client = TestClient(_app(index, art_cache))

    with patch("kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")):
        with patch.object(Path, "write_bytes", side_effect=OSError("read-only")):
            res = client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    assert res.status_code == 200
    assert res.content == b"IMGDATA"


def test_no_cache_dir_configured_keeps_the_old_path(tmp_path: Path) -> None:
    # art_cache_dir is None in the daemon's own tests and in any deployment that
    # has not configured it; behaviour there must be unchanged.
    index = _index_with_local_art()
    client = TestClient(_app(index, None))

    with patch(
        "kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")
    ) as extract:
        client.get("/api/v1/album-art?album_artist=Artist&album=Album")
        client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    # No cache, so every request re-extracts — the pre-fix behaviour.
    assert extract.call_count == 2


def test_missing_album_id_skips_the_cache_without_failing(art_cache: Path) -> None:
    # Missing-album tracks (empty album tag) can have album_id 0; there is no
    # stable key for them, so they take the uncached path rather than erroring.
    index = MagicMock()
    track = _track(1)
    track.embedded_art = True
    track.album_id = 0
    index.tracks_for_album.return_value = [track]
    client = TestClient(_app(index, art_cache))

    with patch("kamp_core.server.extract_art", return_value=(b"IMGDATA", "image/jpeg")):
        res = client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    assert res.status_code == 200
    assert res.content == b"IMGDATA"
    assert list(art_cache.rglob("*")) == []


def test_cover_file_art_is_cached_too(art_cache: Path) -> None:
    index = _index_with_local_art()
    client = TestClient(_app(index, art_cache))

    with patch("kamp_core.server.extract_art", return_value=None):
        with patch(
            "kamp_daemon.artwork.read_cover_file", return_value=(b"COVER", "image/jpeg")
        ):
            client.get("/api/v1/album-art?album_artist=Artist&album=Album")

        with patch("kamp_daemon.artwork.read_cover_file") as read_cover:
            res = client.get("/api/v1/album-art?album_artist=Artist&album=Album")

    read_cover.assert_not_called()
    assert res.content == b"COVER"
