"""Tests for tune_shifter.artwork."""

import io
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

import requests as req_lib

from tune_shifter.artwork import (
    ArtworkError,
    _detect_mime,
    _embed_m4a,
    _embed_mp3,
    _embed_ogg,
    _load_local_artwork,
    fetch_and_embed,
    find_local_artwork,
    has_embedded_art,
)

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


class TestFindLocalArtworkFallback:
    def test_returns_first_alphabetically_when_no_preferred_name(
        self, tmp_path: Path
    ) -> None:
        """find_local_artwork falls back to sorted-first when no keyword matches."""
        (tmp_path / "zzz.jpg").write_bytes(_make_jpeg(100, 100))
        (tmp_path / "aaa.jpg").write_bytes(_make_jpeg(100, 100))

        result = find_local_artwork(tmp_path)
        assert result is not None
        assert result.name == "aaa.jpg"


class TestLoadLocalArtwork:
    def test_returns_none_when_pil_cannot_open(self, tmp_path: Path) -> None:
        """_load_local_artwork returns None when PIL fails to open the file."""
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"not an image")

        result = _load_local_artwork(bad, min_dimension=100, max_bytes=5_000_000)
        assert result is None

    def test_quality_reduction_loop_succeeds(self, tmp_path: Path) -> None:
        """_load_local_artwork re-encodes at lower quality when raw exceeds max_bytes."""
        # Create a large image at high quality so the raw file is big
        img = Image.new("RGB", (1500, 1500), color=(80, 120, 160))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        large_jpeg = buf.getvalue()

        cover = tmp_path / "cover.jpg"
        cover.write_bytes(large_jpeg)

        # max_bytes slightly below the raw size forces the quality loop, but a
        # lower-quality re-encode of a solid-color image will fit easily
        max_bytes = len(large_jpeg) - 1

        result = _load_local_artwork(cover, min_dimension=100, max_bytes=max_bytes)
        assert result is not None
        assert len(result) <= max_bytes


class TestFetchCoverEdgeCases:
    def test_non_404_http_error_raises_artwork_error(self) -> None:
        """A 500-class HTTP error raises ArtworkError (not silently ignored)."""
        http_err = req_lib.HTTPError(response=MagicMock(status_code=500))

        def mock_get(url: str, **kw: object) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status.side_effect = http_err
            return resp

        with patch("tune_shifter.artwork.requests.get", mock_get):
            with pytest.raises(ArtworkError, match="Could not fetch cover art listing"):
                fetch_and_embed("mbid-x", [], min_dimension=1000, max_bytes=1_000_000)

    def test_empty_image_url_is_skipped(self) -> None:
        """An image entry with no URL is skipped without error."""
        listing = {"images": [{"image": "", "front": True}]}

        def mock_get(url: str, **kw: object) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = listing
            return resp

        with patch("tune_shifter.artwork.requests.get", mock_get):
            # Should complete without downloading or raising
            fetch_and_embed("mbid-x", [], min_dimension=1000, max_bytes=1_000_000)

    def test_image_download_failure_continues(self) -> None:
        """A failed image download is skipped and the next candidate is tried."""
        listing = {"images": [{"image": "http://x.com/img.jpg", "front": True}]}

        call_count = 0

        def mock_get(url: str, **kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.raise_for_status.return_value = None
                resp.json.return_value = listing
            else:
                resp.raise_for_status.side_effect = req_lib.ConnectionError("gone")
            return resp

        with patch("tune_shifter.artwork.requests.get", mock_get):
            fetch_and_embed("mbid-x", [], min_dimension=1000, max_bytes=1_000_000)

    def test_unreadable_image_dimensions_are_skipped(self) -> None:
        """An image whose dimensions can't be parsed by PIL is skipped."""
        image_bytes = b"not a real image"
        listing = {"images": [{"image": "http://x.com/img.jpg", "front": True}]}

        call_count = 0

        def mock_get(url: str, **kw: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.raise_for_status.return_value = None
                resp.json.return_value = listing
            else:
                resp.raise_for_status.return_value = None
                resp.content = image_bytes
            return resp

        with patch("tune_shifter.artwork.requests.get", mock_get):
            fetch_and_embed("mbid-x", [], min_dimension=1000, max_bytes=1_000_000)


class TestEmbed:
    def test_unsupported_format_logs_warning(self, tmp_path: Path) -> None:
        """_embed logs a warning for unsupported formats."""
        from tune_shifter.artwork import _embed

        wav = tmp_path / "track.wav"
        wav.write_bytes(b"RIFF")
        _embed(wav, b"\xff\xd8\xff")  # must not raise

    def test_embed_flac_writes_picture_block(self, tmp_path: Path) -> None:
        """_embed_flac clears existing pictures and embeds the new image."""
        from tune_shifter.artwork import _embed_flac

        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_pic = MagicMock()

        with (
            patch("tune_shifter.artwork.mutagen.flac.FLAC", return_value=mock_flac),
            patch("tune_shifter.artwork.mutagen.flac.Picture", return_value=mock_pic),
        ):
            _embed_flac(flac, _make_jpeg(600, 600))

        mock_flac.clear_pictures.assert_called_once()
        mock_flac.add_picture.assert_called_once_with(mock_pic)
        mock_flac.save.assert_called_once()
        assert mock_pic.type == 3
        assert mock_pic.data is not None

    def test_embed_ogg_writes_metadata_block_picture(self, tmp_path: Path) -> None:
        """_embed_ogg embeds art as a base64 METADATA_BLOCK_PICTURE Vorbis comment."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_pic = MagicMock()
        mock_pic.write.return_value = b"\x00" * 32

        with (
            patch(
                "tune_shifter.artwork.mutagen.oggvorbis.OggVorbis",
                return_value=mock_ogg,
            ),
            patch("tune_shifter.artwork.mutagen.flac.Picture", return_value=mock_pic),
        ):
            _embed_ogg(ogg, _make_jpeg(600, 600))

        # The METADATA_BLOCK_PICTURE key must be set and be a non-empty list
        assert mock_ogg.__setitem__.called
        call_args = mock_ogg.__setitem__.call_args
        assert call_args[0][0] == "METADATA_BLOCK_PICTURE"
        assert isinstance(call_args[0][1], list)
        assert len(call_args[0][1]) == 1
        mock_ogg.save.assert_called_once()
        assert mock_pic.type == 3

    def test_embed_mp3_creates_id3_when_header_missing(self, tmp_path: Path) -> None:
        """_embed_mp3 creates a fresh ID3 when the file has no existing header."""
        raw = tmp_path / "01.mp3"
        raw.write_bytes(b"\xff\xfb" * 64)  # raw MP3 frames, no ID3 header

        _embed_mp3(raw, _make_jpeg(100, 100))

        import mutagen.id3 as id3_

        tags = id3_.ID3(str(raw))
        assert any(k.startswith("APIC") for k in tags)

    def test_embed_m4a_adds_tags_when_none(self, tmp_path: Path) -> None:
        """_embed_m4a calls add_tags() when the MP4 has no tag object."""
        mock_mp4 = MagicMock()
        mock_mp4.tags = None
        # After add_tags(), tags must be non-None
        mock_tags: dict = {}
        mock_mp4.add_tags.side_effect = lambda: setattr(mock_mp4, "tags", mock_tags)

        with patch("tune_shifter.artwork.mutagen.mp4.MP4", return_value=mock_mp4):
            m4a = tmp_path / "01.m4a"
            m4a.write_bytes(b"\x00" * 32)
            _embed_m4a(m4a, _make_jpeg(100, 100))

        mock_mp4.add_tags.assert_called_once()
        assert "covr" in mock_tags


class TestDetectMime:
    def test_returns_png_for_png_magic_bytes(self) -> None:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert _detect_mime(png_bytes) == "image/png"

    def test_returns_jpeg_for_other_bytes(self) -> None:
        assert _detect_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"


class TestHasEmbeddedArt:
    _MIN = 500
    _MAX = 5_000_000

    def _embed_mp3(self, path: Path, image_bytes: bytes) -> None:
        import mutagen.id3 as id3_

        path.write_bytes(b"\xff\xfb" * 64)
        tags = id3_.ID3()
        tags["APIC:Cover"] = id3_.APIC(
            encoding=3, mime="image/jpeg", type=3, desc="Cover", data=image_bytes
        )
        tags.save(str(path))

    def test_mp3_qualifying_art_returns_true(self, tmp_path: Path) -> None:
        """Returns True for an MP3 whose embedded art meets dimension and byte limits."""
        mp3 = tmp_path / "01.mp3"
        self._embed_mp3(mp3, _make_jpeg(600, 600))

        assert (
            has_embedded_art(mp3, min_dimension=self._MIN, max_bytes=self._MAX) is True
        )

    def test_mp3_art_too_small_returns_false(self, tmp_path: Path) -> None:
        """Returns False when embedded art dimensions are below min_dimension."""
        mp3 = tmp_path / "01.mp3"
        self._embed_mp3(mp3, _make_jpeg(200, 200))

        assert (
            has_embedded_art(mp3, min_dimension=self._MIN, max_bytes=self._MAX) is False
        )

    def test_mp3_art_too_large_in_bytes_returns_false(self, tmp_path: Path) -> None:
        """Returns False when embedded art exceeds max_bytes."""
        mp3 = tmp_path / "01.mp3"
        image_bytes = _make_jpeg(600, 600)
        self._embed_mp3(mp3, image_bytes)

        # max_bytes set to 1 byte below the image size
        assert (
            has_embedded_art(
                mp3, min_dimension=self._MIN, max_bytes=len(image_bytes) - 1
            )
            is False
        )

    def test_mp3_without_apic_returns_false(self, tmp_path: Path) -> None:
        """Returns False for an MP3 with no APIC frame."""
        import mutagen.id3 as id3_

        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3_.ID3().save(str(mp3))

        assert (
            has_embedded_art(mp3, min_dimension=self._MIN, max_bytes=self._MAX) is False
        )

    def test_m4a_qualifying_art_returns_true(self, tmp_path: Path) -> None:
        """Returns True for an M4A whose embedded art meets quality requirements."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = {"covr": [_make_jpeg(600, 600)]}

        with patch("tune_shifter.artwork.mutagen.mp4.MP4", return_value=mock_mp4):
            assert (
                has_embedded_art(m4a, min_dimension=self._MIN, max_bytes=self._MAX)
                is True
            )

    def test_m4a_art_too_small_returns_false(self, tmp_path: Path) -> None:
        """Returns False when M4A embedded art dimensions are below min_dimension."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = {"covr": [_make_jpeg(200, 200)]}

        with patch("tune_shifter.artwork.mutagen.mp4.MP4", return_value=mock_mp4):
            assert (
                has_embedded_art(m4a, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_m4a_without_covr_returns_false(self, tmp_path: Path) -> None:
        """Returns False for an M4A with no covr tag."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = {}

        with patch("tune_shifter.artwork.mutagen.mp4.MP4", return_value=mock_mp4):
            assert (
                has_embedded_art(m4a, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_m4a_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """Returns False when M4A tags object is None."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = None

        with patch("tune_shifter.artwork.mutagen.mp4.MP4", return_value=mock_mp4):
            assert (
                has_embedded_art(m4a, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_unsupported_format_returns_false(self, tmp_path: Path) -> None:
        """Returns False for unsupported file formats."""
        wav = tmp_path / "track.wav"
        wav.write_bytes(b"RIFF")

        assert (
            has_embedded_art(wav, min_dimension=self._MIN, max_bytes=self._MAX) is False
        )

    def test_flac_qualifying_art_returns_true(self, tmp_path: Path) -> None:
        """Returns True for a FLAC whose embedded picture meets quality requirements."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_pic = MagicMock()
        mock_pic.data = _make_jpeg(600, 600)
        mock_flac = MagicMock()
        mock_flac.pictures = [mock_pic]

        with patch("tune_shifter.artwork.mutagen.flac.FLAC", return_value=mock_flac):
            assert (
                has_embedded_art(flac, min_dimension=self._MIN, max_bytes=self._MAX)
                is True
            )

    def test_flac_art_too_small_returns_false(self, tmp_path: Path) -> None:
        """Returns False when FLAC embedded picture dimensions are below min_dimension."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_pic = MagicMock()
        mock_pic.data = _make_jpeg(200, 200)
        mock_flac = MagicMock()
        mock_flac.pictures = [mock_pic]

        with patch("tune_shifter.artwork.mutagen.flac.FLAC", return_value=mock_flac):
            assert (
                has_embedded_art(flac, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_flac_without_pictures_returns_false(self, tmp_path: Path) -> None:
        """Returns False for a FLAC with no PICTURE blocks."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.pictures = []

        with patch("tune_shifter.artwork.mutagen.flac.FLAC", return_value=mock_flac):
            assert (
                has_embedded_art(flac, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_ogg_qualifying_art_returns_true(self, tmp_path: Path) -> None:
        """Returns True for an OGG whose METADATA_BLOCK_PICTURE meets quality requirements."""
        import base64

        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_pic = MagicMock()
        mock_pic.data = _make_jpeg(600, 600)
        mock_ogg = MagicMock()
        mock_ogg.tags = {
            "METADATA_BLOCK_PICTURE": [base64.b64encode(b"\x00" * 8).decode()]
        }

        with (
            patch(
                "tune_shifter.artwork.mutagen.oggvorbis.OggVorbis",
                return_value=mock_ogg,
            ),
            patch("tune_shifter.artwork.mutagen.flac.Picture", return_value=mock_pic),
        ):
            assert (
                has_embedded_art(ogg, min_dimension=self._MIN, max_bytes=self._MAX)
                is True
            )

    def test_ogg_art_too_small_returns_false(self, tmp_path: Path) -> None:
        """Returns False when OGG embedded art is below min_dimension."""
        import base64

        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_pic = MagicMock()
        mock_pic.data = _make_jpeg(200, 200)
        mock_ogg = MagicMock()
        mock_ogg.tags = {
            "METADATA_BLOCK_PICTURE": [base64.b64encode(b"\x00" * 8).decode()]
        }

        with (
            patch(
                "tune_shifter.artwork.mutagen.oggvorbis.OggVorbis",
                return_value=mock_ogg,
            ),
            patch("tune_shifter.artwork.mutagen.flac.Picture", return_value=mock_pic),
        ):
            assert (
                has_embedded_art(ogg, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_ogg_without_picture_returns_false(self, tmp_path: Path) -> None:
        """Returns False for an OGG with no METADATA_BLOCK_PICTURE tag."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = {}

        with patch(
            "tune_shifter.artwork.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            assert (
                has_embedded_art(ogg, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_ogg_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """Returns False when OGG tags object is None."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = None

        with patch(
            "tune_shifter.artwork.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            assert (
                has_embedded_art(ogg, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )

    def test_read_error_returns_false(self, tmp_path: Path) -> None:
        """Returns False when reading the file raises an exception."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)

        with patch("tune_shifter.artwork.id3.ID3", side_effect=Exception("corrupt")):
            assert (
                has_embedded_art(mp3, min_dimension=self._MIN, max_bytes=self._MAX)
                is False
            )
