"""Tests for tune_shifter.artwork."""

import io
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from tune_shifter.artwork import ArtworkError, fetch_and_embed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jpeg(width: int, height: int) -> bytes:
    """Return bytes of a real JPEG with the given dimensions."""
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=10)
    return buf.getvalue()


def _listing_response(image_url: str) -> dict:
    return {"images": [{"image": image_url, "front": True, "back": False}]}


def _mock_requests_get(listing: dict, image_bytes: bytes) -> MagicMock:
    """Return a mock for requests.get that serves listing then image."""
    call_count = 0

    def side_effect(url: str, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if call_count == 1:
            resp.json.return_value = listing
        else:
            resp.content = image_bytes
        return resp

    m = MagicMock(side_effect=side_effect)
    return m


def _make_mp3(path: Path) -> None:
    import mutagen.id3 as id3

    tags = id3.ID3()
    path.write_bytes(b"\xff\xfb" * 64)
    tags.save(str(path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFetchAndEmbed:
    def test_embeds_qualifying_jpeg_in_mp3(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        image_bytes = _make_jpeg(1200, 1200)
        listing = _listing_response("http://example.com/cover.jpg")
        mock_get = _mock_requests_get(listing, image_bytes)

        with patch("tune_shifter.artwork.requests.get", mock_get):
            fetch_and_embed("abc-123", [mp3], min_dimension=1000, max_bytes=5_000_000)

        import mutagen.id3 as id3

        tags = id3.ID3(str(mp3))
        assert any(k.startswith("APIC") for k in tags)

    def test_skips_image_too_small(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        image_bytes = _make_jpeg(500, 500)
        listing = _listing_response("http://example.com/small.jpg")
        mock_get = _mock_requests_get(listing, image_bytes)

        with patch("tune_shifter.artwork.requests.get", mock_get):
            # Should not raise — just log a warning
            fetch_and_embed("abc-123", [mp3], min_dimension=1000, max_bytes=5_000_000)

        import mutagen.id3 as id3

        tags = id3.ID3(str(mp3))
        assert not any(k.startswith("APIC") for k in tags)

    def test_skips_image_too_large_in_bytes(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        image_bytes = _make_jpeg(1200, 1200)
        listing = _listing_response("http://example.com/big.jpg")
        mock_get = _mock_requests_get(listing, image_bytes)

        with patch("tune_shifter.artwork.requests.get", mock_get):
            # max_bytes set below the generated image size
            fetch_and_embed("abc-123", [mp3], min_dimension=1000, max_bytes=1)

        import mutagen.id3 as id3

        tags = id3.ID3(str(mp3))
        assert not any(k.startswith("APIC") for k in tags)

    def test_raises_when_listing_request_fails(self, tmp_path: Path) -> None:
        import requests as req_lib

        with patch(
            "tune_shifter.artwork.requests.get",
            side_effect=req_lib.RequestException("timeout"),
        ):
            with pytest.raises(ArtworkError, match="Could not fetch cover art listing"):
                fetch_and_embed("abc-123", [], min_dimension=1000, max_bytes=1_000_000)

    def test_no_images_in_listing_is_graceful(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        def mock_get(url: str, **kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"images": []}
            return resp

        with patch("tune_shifter.artwork.requests.get", mock_get):
            # Should complete without raising
            fetch_and_embed("abc-123", [mp3], min_dimension=1000, max_bytes=1_000_000)
