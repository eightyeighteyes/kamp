"""Tests for tune_shifter.artwork."""

import io
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from tune_shifter.artwork import ArtworkError, fetch_and_embed, find_local_artwork

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
            side_effect=req_lib.ConnectionError("timeout"),
        ):
            with pytest.raises(ArtworkError, match="Could not fetch cover art listing"):
                fetch_and_embed("abc-123", [], min_dimension=1000, max_bytes=1_000_000)

    def test_404_falls_back_to_release_group(self, tmp_path: Path) -> None:
        """A 404 on the release MBID silently falls back to the release-group endpoint."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        image_bytes = _make_jpeg(1200, 1200)
        listing = _listing_response("http://example.com/cover.jpg")

        import requests as req_lib

        call_count = 0

        def mock_get(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                # First call: release MBID → 404
                http_err = req_lib.HTTPError(response=MagicMock(status_code=404))
                resp.raise_for_status.side_effect = http_err
            elif call_count == 2:
                # Second call: release-group listing
                resp.raise_for_status.return_value = None
                resp.json.return_value = listing
            else:
                # Third call: download the image
                resp.raise_for_status.return_value = None
                resp.content = image_bytes
            return resp

        with patch("tune_shifter.artwork.requests.get", mock_get):
            fetch_and_embed(
                "release-mbid",
                [mp3],
                min_dimension=1000,
                max_bytes=5_000_000,
                release_group_mbid="group-mbid",
            )

        import mutagen.id3 as id3

        tags = id3.ID3(str(mp3))
        assert any(k.startswith("APIC") for k in tags)

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

    def test_embeds_qualifying_jpeg_in_m4a(self, tmp_path: Path) -> None:
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 64)

        image_bytes = _make_jpeg(1200, 1200)
        listing = _listing_response("http://example.com/cover.jpg")
        mock_get = _mock_requests_get(listing, image_bytes)

        mock_tags: dict = {}
        mock_mp4 = MagicMock()
        mock_mp4.tags = mock_tags

        with patch("tune_shifter.artwork.mutagen.mp4.MP4", return_value=mock_mp4):
            with patch("tune_shifter.artwork.requests.get", mock_get):
                fetch_and_embed(
                    "abc-123", [m4a], min_dimension=1000, max_bytes=5_000_000
                )

        assert "covr" in mock_tags
        mock_mp4.save.assert_called_once()


class TestLocalArtwork:
    def test_uses_local_cover_jpg_when_qualifying(self, tmp_path: Path) -> None:
        """A qualifying cover.jpg in the directory is embedded with no network calls."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(_make_jpeg(1200, 1200))

        with patch("tune_shifter.artwork.requests.get") as mock_get:
            fetch_and_embed(
                "abc-123",
                [mp3],
                min_dimension=1000,
                max_bytes=5_000_000,
                directory=tmp_path,
            )

        mock_get.assert_not_called()
        import mutagen.id3 as id3

        tags = id3.ID3(str(mp3))
        assert any(k.startswith("APIC") for k in tags)

    def test_falls_back_to_online_when_local_too_small(self, tmp_path: Path) -> None:
        """A sub-minimum local image triggers the online fallback."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(_make_jpeg(500, 500))

        with patch("tune_shifter.artwork.requests.get") as mock_get:
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"images": []}
            mock_get.return_value = resp

            fetch_and_embed(
                "abc-123",
                [mp3],
                min_dimension=1000,
                max_bytes=5_000_000,
                directory=tmp_path,
            )

        assert mock_get.called

    def test_resizes_oversized_local_art(self, tmp_path: Path) -> None:
        """Local art exceeding max_bytes is re-encoded to fit within the limit."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)
        cover = tmp_path / "cover.jpg"
        # Write a high-quality large JPEG
        large_jpeg = _make_jpeg(2000, 2000)
        cover.write_bytes(large_jpeg)
        max_bytes = len(large_jpeg) // 2  # force re-encoding

        with patch("tune_shifter.artwork.requests.get") as mock_get:
            fetch_and_embed(
                "abc-123",
                [mp3],
                min_dimension=1000,
                max_bytes=max_bytes,
                directory=tmp_path,
            )

        mock_get.assert_not_called()
        import mutagen.id3 as id3

        tags = id3.ID3(str(mp3))
        apic_keys = [k for k in tags if k.startswith("APIC")]
        assert apic_keys
        embedded_bytes = tags[apic_keys[0]].data
        assert len(embedded_bytes) <= max_bytes

    def test_prefers_cover_jpg_over_other_images(self, tmp_path: Path) -> None:
        """cover.jpg is chosen over a non-preferred filename."""
        (tmp_path / "photo.jpg").write_bytes(_make_jpeg(1200, 1200))
        (tmp_path / "cover.jpg").write_bytes(_make_jpeg(1200, 1200))

        result = find_local_artwork(tmp_path)
        assert result is not None
        assert result.name == "cover.jpg"

    def test_no_local_art_falls_back_to_online(self, tmp_path: Path) -> None:
        """A directory with no images triggers the online artwork fetch."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        with patch("tune_shifter.artwork.requests.get") as mock_get:
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"images": []}
            mock_get.return_value = resp

            fetch_and_embed(
                "abc-123",
                [mp3],
                min_dimension=1000,
                max_bytes=5_000_000,
                directory=tmp_path,
            )

        assert mock_get.called
