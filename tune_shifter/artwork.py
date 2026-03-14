"""Fetch and embed front cover art from the MusicBrainz Cover Art Archive."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

import mutagen.id3 as id3
import mutagen.mp4
import requests
from PIL import Image

logger = logging.getLogger(__name__)

COVER_ART_ARCHIVE_URL = "https://coverartarchive.org/release/{mbid}"


class ArtworkError(Exception):
    pass


def fetch_and_embed(
    mbid: str,
    audio_files: list[Path],
    min_dimension: int,
    max_bytes: int,
) -> None:
    """Download qualifying front cover art and embed it in all audio files.

    A qualifying image must be at least *min_dimension* × *min_dimension* pixels
    and no larger than *max_bytes* bytes.  If no qualifying image is found, logs
    a warning but does not raise — the pipeline continues without artwork.
    """
    image_bytes = _fetch_cover(mbid, min_dimension, max_bytes)
    if image_bytes is None:
        logger.warning(
            "No qualifying cover art found for release %s — skipping artwork", mbid
        )
        return

    logger.info(
        "Embedding cover art (%d bytes) into %d file(s)",
        len(image_bytes),
        len(audio_files),
    )
    for path in audio_files:
        _embed(path, image_bytes)


def _fetch_cover(mbid: str, min_dimension: int, max_bytes: int) -> bytes | None:
    """Return image bytes for the first front cover that meets size criteria."""
    url = COVER_ART_ARCHIVE_URL.format(mbid=mbid)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ArtworkError(
            f"Could not fetch cover art listing for {mbid}: {exc}"
        ) from exc

    listing: dict[str, Any] = resp.json()
    images: list[dict[str, Any]] = listing.get("images", [])

    # Prefer front-flagged images; fall back to all images if none flagged
    front_images = [img for img in images if img.get("front", False)]
    candidates = front_images or images

    for image in candidates:
        image_url: str = image.get("image", "")
        if not image_url:
            continue

        try:
            img_resp = requests.get(image_url, timeout=30)
            img_resp.raise_for_status()
        except requests.RequestException:
            logger.debug("Failed to download candidate image %s", image_url)
            continue

        raw = img_resp.content

        if len(raw) > max_bytes:
            logger.debug(
                "Skipping image %s: %d bytes > %d limit", image_url, len(raw), max_bytes
            )
            continue

        try:
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
        except Exception:
            logger.debug("Skipping image %s: could not read dimensions", image_url)
            continue

        if w < min_dimension or h < min_dimension:
            logger.debug(
                "Skipping image %s: %dx%d < %d minimum", image_url, w, h, min_dimension
            )
            continue

        logger.info(
            "Selected cover art: %s (%dx%d, %d bytes)", image_url, w, h, len(raw)
        )
        return raw

    return None


def _embed(path: Path, image_bytes: bytes) -> None:
    """Embed *image_bytes* as front cover art in the given audio file."""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _embed_mp3(path, image_bytes)
    elif suffix == ".m4a":
        _embed_m4a(path, image_bytes)
    else:
        logger.warning("Cannot embed artwork in unsupported format: %s", path)


def _embed_mp3(path: Path, image_bytes: bytes) -> None:
    try:
        tags = id3.ID3(str(path))
    except Exception:
        tags = id3.ID3()

    # Remove any existing cover art
    tags.delall("APIC")

    mime = _detect_mime(image_bytes)
    tags["APIC"] = id3.APIC(
        encoding=3,
        mime=mime,
        type=3,  # front cover
        desc="Cover",
        data=image_bytes,
    )
    tags.save(str(path))
    logger.debug("Embedded cover art in MP3 %s", path)


def _embed_m4a(path: Path, image_bytes: bytes) -> None:
    audio = mutagen.mp4.MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None

    mime = _detect_mime(image_bytes)
    fmt = (
        mutagen.mp4.MP4Cover.FORMAT_JPEG
        if "jpeg" in mime
        else mutagen.mp4.MP4Cover.FORMAT_PNG
    )
    audio.tags["covr"] = [mutagen.mp4.MP4Cover(image_bytes, imageformat=fmt)]
    audio.save()
    logger.debug("Embedded cover art in M4A %s", path)


def _detect_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"
