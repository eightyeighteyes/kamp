"""Fetch and embed front cover art from the MusicBrainz Cover Art Archive."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

import mutagen.flac
import mutagen.id3 as id3
import mutagen.mp4
import mutagen.oggvorbis
import requests
from PIL import Image

logger = logging.getLogger(__name__)

COVER_ART_ARCHIVE_URL = "https://coverartarchive.org/release/{mbid}"
RELEASE_GROUP_ART_URL = "https://coverartarchive.org/release-group/{mbid}"

_PREFERRED_ART_NAMES = ("cover", "folder", "artwork", "front")


class ArtworkError(Exception):
    pass


def has_embedded_art(path: Path, min_dimension: int, max_bytes: int) -> bool:
    """Return True if *path* has embedded cover art that meets quality requirements.

    Checks that art exists, its dimensions are ≥ min_dimension on both axes,
    and its byte size is ≤ max_bytes.  Presence alone is not enough — art
    embedded by another tool may be too small or oversized for our threshold.
    Returns False on any read or decode error.
    """
    try:
        suffix = path.suffix.lower()
        image_bytes: bytes | None = None

        if suffix == ".mp3":
            tags = id3.ID3(str(path))
            apic_keys = [k for k in tags if k.startswith("APIC")]
            if not apic_keys:
                return False
            image_bytes = tags[apic_keys[0]].data  # type: ignore[attr-defined]
        elif suffix == ".m4a":
            audio = mutagen.mp4.MP4(str(path))
            if audio.tags is None:
                return False
            covr = audio.tags.get("covr")
            if not covr:
                return False
            image_bytes = bytes(covr[0])  # type: ignore[index]
        elif suffix == ".flac":
            flac = mutagen.flac.FLAC(str(path))
            if not flac.pictures:
                return False
            image_bytes = flac.pictures[0].data
        elif suffix == ".ogg":
            ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if ogg.tags is None:
                return False
            pic_data = ogg.tags.get("METADATA_BLOCK_PICTURE")
            if not pic_data:
                return False
            pic = mutagen.flac.Picture(base64.b64decode(pic_data[0]))
            image_bytes = pic.data
        else:
            return False

        if len(image_bytes) > max_bytes:
            logger.debug(
                "Embedded art in %s exceeds max_bytes (%d > %d)",
                path,
                len(image_bytes),
                max_bytes,
            )
            return False

        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w < min_dimension or h < min_dimension:
            logger.debug(
                "Embedded art in %s too small (%dx%d < %dpx)", path, w, h, min_dimension
            )
            return False

        return True
    except Exception:
        pass
    return False


def find_local_artwork(directory: Path) -> Path | None:
    """Return the best image file found directly in *directory*, or None.

    Prefers filenames whose stem contains a recognised art keyword
    (cover, folder, artwork, front) in that priority order.  Falls back to
    the alphabetically first image if no keyword matches.
    """
    candidates: list[Path] = []
    for pattern in ("*.jpg", "*.jpeg", "*.png"):
        candidates.extend(directory.glob(pattern))
    if not candidates:
        return None
    for hint in _PREFERRED_ART_NAMES:
        for p in sorted(candidates):
            if hint in p.stem.lower():
                return p
    return sorted(candidates)[0]


def _load_local_artwork(path: Path, min_dimension: int, max_bytes: int) -> bytes | None:
    """Load *path* and return qualifying image bytes, resizing if necessary.

    Returns None if the image cannot be opened or its dimensions are below
    *min_dimension* (caller should fall back to online sources).  If the raw
    file exceeds *max_bytes*, re-encodes as JPEG at progressively lower quality;
    if still too large, scales dimensions down to *min_dimension* on the short
    edge before a final encode.
    """
    raw = path.read_bytes()
    try:
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
    except Exception as exc:
        logger.debug("Could not open local artwork %s: %s", path, exc)
        return None

    if w < min_dimension or h < min_dimension:
        logger.debug(
            "Local artwork %s too small (%dx%d < %dpx) — will try online",
            path,
            w,
            h,
            min_dimension,
        )
        return None

    if len(raw) <= max_bytes:
        logger.debug("Using local artwork %s (%dx%d, %d bytes)", path, w, h, len(raw))
        return raw

    compressed = _compress_to_max_bytes(img, min_dimension, max_bytes)
    logger.debug("Re-encoded local artwork %s to %d bytes", path, len(compressed))
    return compressed


def _compress_to_max_bytes(
    img: Image.Image, min_dimension: int, max_bytes: int
) -> bytes:
    """Re-encode *img* as JPEG to fit within *max_bytes*.

    Tries quality levels 85 → 70 → 55 → 40.  If still too large, scales the
    image down so its short edge equals *min_dimension* and encodes once more
    at quality=75.  Always returns bytes.
    """
    rgb = img.convert("RGB")
    for quality in (85, 70, 55, 40):
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    # Quality reduction insufficient: scale to min_dimension on the short edge
    w, h = img.size
    scale = min_dimension / min(w, h)
    resized = rgb.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def fetch_and_embed(
    mbid: str,
    audio_files: list[Path],
    min_dimension: int,
    max_bytes: int,
    release_group_mbid: str = "",
    directory: Path | None = None,
) -> None:
    """Embed front cover art into all audio files.

    Checks *directory* for a bundled image first (e.g. cover.jpg from a
    Bandcamp ZIP).  If no qualifying local image is found, falls back to the
    MusicBrainz Cover Art Archive (release MBID, then release-group MBID).
    A qualifying image must be at least *min_dimension* × *min_dimension* px;
    oversized images are resized to fit within *max_bytes*.  If no art is
    found at all, logs a warning but does not raise — the pipeline continues.
    """
    image_bytes: bytes | None = None

    if directory is not None:
        local = find_local_artwork(directory)
        if local is not None:
            image_bytes = _load_local_artwork(local, min_dimension, max_bytes)
            if image_bytes is not None:
                logger.info("Using bundled artwork from %s", local)

    if image_bytes is None:
        # Skip the Cover Art Archive network call when every file already has
        # qualifying embedded art — no local image was found, so the online
        # fetch is the only remaining source, and the embedded art already
        # satisfies the quality threshold.
        if audio_files and all(
            has_embedded_art(f, min_dimension, max_bytes) for f in audio_files
        ):
            logger.info(
                "All %d file(s) have qualifying embedded art — skipping Cover Art Archive fetch",
                len(audio_files),
            )
            return
        image_bytes = _fetch_cover(
            COVER_ART_ARCHIVE_URL.format(mbid=mbid), min_dimension, max_bytes
        )
    if image_bytes is None and release_group_mbid:
        logger.debug(
            "No art for release %s; trying release-group %s", mbid, release_group_mbid
        )
        image_bytes = _fetch_cover(
            RELEASE_GROUP_ART_URL.format(mbid=release_group_mbid),
            min_dimension,
            max_bytes,
        )
    if image_bytes is None:
        logger.warning(
            "No qualifying cover art found for release %s "
            "(min %dpx, max %d bytes) — skipping artwork",
            mbid,
            min_dimension,
            max_bytes,
        )
        return

    logger.info(
        "Embedding cover art (%d bytes) into %d file(s)",
        len(image_bytes),
        len(audio_files),
    )
    for path in audio_files:
        _embed(path, image_bytes)


def _fetch_cover(url: str, min_dimension: int, max_bytes: int) -> bytes | None:
    """Return image bytes for the first front cover at *url* that meets size criteria.

    Returns None if the listing returns 404 (no art available at this URL).
    Raises ArtworkError for other network failures.
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.debug("No cover art listing at %s (404)", url)
            return None
        raise ArtworkError(
            f"Could not fetch cover art listing from {url}: {exc}"
        ) from exc
    except requests.RequestException as exc:
        raise ArtworkError(
            f"Could not fetch cover art listing from {url}: {exc}"
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

        if len(raw) > max_bytes:
            compressed = _compress_to_max_bytes(img, min_dimension, max_bytes)
            if len(compressed) > max_bytes:
                raise ArtworkError(
                    f"CAA image {image_url} ({len(raw)} bytes) could not be compressed"
                    f" to fit within {max_bytes} bytes"
                )
            raw = compressed
            logger.debug("Compressed CAA image %s to %d bytes", image_url, len(raw))

        logger.info(
            "Selected cover art: %s (%dx%d, %d bytes)", image_url, w, h, len(raw)
        )
        return raw

    logger.debug(
        "No qualifying cover art after checking %d candidate(s) at %s",
        len(candidates),
        url,
    )
    return None


def _embed(path: Path, image_bytes: bytes) -> None:
    """Embed *image_bytes* as front cover art in the given audio file."""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _embed_mp3(path, image_bytes)
    elif suffix == ".m4a":
        _embed_m4a(path, image_bytes)
    elif suffix == ".flac":
        _embed_flac(path, image_bytes)
    elif suffix == ".ogg":
        _embed_ogg(path, image_bytes)
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


def _embed_flac(path: Path, image_bytes: bytes) -> None:
    audio = mutagen.flac.FLAC(str(path))
    audio.clear_pictures()

    pic = mutagen.flac.Picture()
    pic.type = 3  # front cover
    pic.mime = _detect_mime(image_bytes)
    pic.data = image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        pic.width, pic.height = img.size
        pic.depth = 24
    except Exception:
        pass

    audio.add_picture(pic)
    audio.save()
    logger.debug("Embedded cover art in FLAC %s", path)


def _embed_ogg(path: Path, image_bytes: bytes) -> None:
    # OGG Vorbis stores art as a base64-encoded FLAC Picture block in the
    # METADATA_BLOCK_PICTURE Vorbis comment — reuse mutagen.flac.Picture.
    audio = mutagen.oggvorbis.OggVorbis(str(path))

    pic = mutagen.flac.Picture()
    pic.type = 3  # front cover
    pic.mime = _detect_mime(image_bytes)
    pic.data = image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        pic.width, pic.height = img.size
        pic.depth = 24
    except Exception:
        pass

    encoded = base64.b64encode(pic.write()).decode("ascii")
    audio["METADATA_BLOCK_PICTURE"] = [encoded]
    audio.save()
    logger.debug("Embedded cover art in OGG %s", path)


def _detect_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"
