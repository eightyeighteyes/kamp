"""Orchestrate the ingest pipeline: extract → tag → artwork → move.

Extension wrappers (KampMusicBrainzTagger, KampCoverArtArchive) are invoked
in-process here rather than via invoke_extension() because the outer
pipeline.py subprocess is already the isolation boundary and return values
need to flow directly back to the host.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from .artwork import ArtworkError, _detect_mime, _embed, find_local_artwork
from .config import Config
from .ext.builtin.coverart import KampCoverArtArchive
from .ext.builtin.musicbrainz import KampMusicBrainzTagger
from .ext.context import KampGround, PlaybackSnapshot
from .ext.types import ArtworkQuery, ArtworkResult, TrackMetadata
from .extractor import ExtractionError, extract, find_audio_files
from .mover import MoveError, move_to_library
from .tagger import (
    TaggingError,
    is_tagged,
    read_release_mbids,
    read_track_metadata_from_file,
    write_tags_from_track_metadata,
)

logger = logging.getLogger(__name__)

# Marker embedded in a staging item's name to inject a failure at a specific
# pipeline stage.  Used exclusively by `kamp test-notify` so the full
# IPC notification path (pipeline_impl → stage_q → notification_callback) can
# be exercised without a real audio file or network access.
_TEST_INJECT = {
    "extraction": "__test_extraction_error",
    "tagging": "__test_tagging_error",
    "artwork": "__test_artwork_error",
    "move": "__test_move_error",
}

# Sentinel prefix must match pipeline.py — imported lazily to avoid a circular
# dependency; duplicated as a string literal here so pipeline_impl stays usable
# standalone (e.g. in tests that call run() directly).
_NOTIFY_SENTINEL = "__notify__:"


def _notify(
    notify_callback: Callable[[str], None] | None,
    subtitle: str,
    message: str,
) -> None:
    """Emit a notification payload via notify_callback using the __notify__: sentinel.

    notify_callback receives an already-serialised sentinel string so
    pipeline_impl stays decoupled from how the parent delivers the notification.
    The caller (pipeline_worker) wires it to stage_q.put, same as stage_callback.
    """
    if notify_callback is None:
        return
    payload = json.dumps({"title": "Kamp", "subtitle": subtitle, "message": message})
    notify_callback(f"{_NOTIFY_SENTINEL}{payload}")


def run(
    path: Path,
    config: Config,
    _on_directory: Callable[[Path], None] | None = None,
    stage_callback: Callable[[str], None] | None = None,
    notify_callback: Callable[[str], None] | None = None,
) -> None:
    """Process a single staging item (ZIP or directory) end-to-end.

    On per-step failure the item is moved to staging/errors/ so the watcher
    does not trigger on it again.  *stage_callback* (if provided) is called
    with the current stage name ("Extracting", "Tagging", etc.) and with an
    empty string in a finally block so the caller can always reset its display.
    *notify_callback* (if provided) receives __notify__: sentinel strings that
    the parent process routes to rumps.notification().
    """
    logger.info("Pipeline started for %s", path)

    try:
        # --- 1. Extract -------------------------------------------------------
        if stage_callback:
            stage_callback("Extracting")
        try:
            if _TEST_INJECT["extraction"] in path.name:
                raise ExtractionError("Injected by test-notify --type extraction")
            directory = extract(path)
        except ExtractionError as exc:
            logger.error("Extraction failed: %s", exc)
            _notify(notify_callback, "Extraction failed", path.name)
            _quarantine(path, config.paths.staging)
            return

        # Notify the watcher of the staging directory as early as possible so it
        # can cancel any pending debounce timer for this directory.  Without this,
        # extracting a ZIP creates the directory, the watcher schedules it for a
        # second pipeline run, and that run races the first.
        if _on_directory is not None:
            _on_directory(directory)

        audio_files = find_audio_files(directory)
        if not audio_files:
            logger.error("No audio files found in %s", directory)
            _notify(
                notify_callback, "Extraction failed", f"No audio files in {path.name}"
            )
            _quarantine(directory, config.paths.staging)
            return

        # Build a shared KampGround context for this pipeline invocation.
        # library_tracks is empty because the pipeline acts on staging files
        # (not yet in the library); playback snapshot is a default.
        ctx = KampGround(playback=PlaybackSnapshot(), library_tracks=[])

        # --- 2. Tag -----------------------------------------------------------
        # Skip the MusicBrainz lookup (and tag writes) when every file already has
        # an MBID — the most expensive operation in the pipeline.  If even one file
        # is untagged, run the full pass for the whole directory to stay consistent.
        if stage_callback:
            stage_callback("Tagging")
        if all(is_tagged(f) for f in audio_files):
            logger.info("All files already tagged — skipping MusicBrainz lookup")
            mbid, rg_mbid = read_release_mbids(audio_files[0])
            title = "(already tagged)"
        else:
            try:
                if _TEST_INJECT["tagging"] in directory.name:
                    raise TaggingError("Injected by test-notify --type tagging")
                # Build TrackMetadata from existing file tags, invoke the tagger
                # extension in-process, then write enriched metadata back to files.
                tracks = [read_track_metadata_from_file(f) for f in audio_files]
                tagger = KampMusicBrainzTagger(ctx)
                enriched = tagger.tag_release(tracks)
                if (
                    not config.musicbrainz.trust_musicbrainz_when_tags_conflict
                    and _mb_tags_conflict(tracks, enriched)
                ):
                    # MB returned different artist/album than the existing file
                    # tags — likely a mis-match for a release not yet in the DB.
                    # Keep the existing tags; proceed to artwork with no MBID.
                    first = tracks[0] if tracks else None
                    logger.warning(
                        "MusicBrainz tags conflict with existing file tags "
                        "(existing: %r / %r, MB: %r / %r) — skipping ID3 write",
                        first.artist if first else "",
                        first.album if first else "",
                        enriched[0].artist if enriched else "",
                        enriched[0].album if enriched else "",
                    )
                    mbid = ""
                    rg_mbid = ""
                    title = first.album if first else directory.name
                else:
                    total = len(audio_files)
                    for audio_file, track in zip(audio_files, enriched):
                        write_tags_from_track_metadata(
                            audio_file, track, total_tracks=total
                        )
                    # Use the first enriched track to carry release-level IDs forward.
                    mbid = enriched[0].release_mbid if enriched else ""
                    rg_mbid = enriched[0].release_group_mbid if enriched else ""
                    title = enriched[0].album if enriched else directory.name
            except TaggingError as exc:
                logger.error("Tagging failed: %s", exc)
                _notify(notify_callback, "Tagging failed", path.name)
                _quarantine(directory, config.paths.staging)
                return

        # --- 3. Artwork -------------------------------------------------------
        # Always run: even if art is already embedded, a higher-quality image may
        # be available (e.g. a bundled cover.jpg in the ZIP that beats the art the
        # original files shipped with).
        if stage_callback:
            stage_callback("Updating artwork")
        try:
            if _TEST_INJECT["artwork"] in directory.name:
                raise ArtworkError("Injected by test-notify --type artwork")
            elif _TEST_INJECT["move"] not in directory.name:
                # Skip network artwork fetch when testing the move stage so it
                # doesn't raise its own ArtworkError before we reach the move
                # injection point.
                _fetch_and_embed_via_extension(
                    ctx=ctx,
                    audio_files=audio_files,
                    release_mbid=mbid,
                    release_group_mbid=rg_mbid,
                    directory=directory,
                    min_dimension=config.artwork.min_dimension,
                    max_bytes=config.artwork.max_bytes,
                )
        except ArtworkError as exc:
            # Artwork failure is non-fatal: log and continue.
            logger.warning("Artwork step failed: %s", exc)
            _notify(notify_callback, "Artwork warning", str(exc)[:120])

        # --- 4. Move ----------------------------------------------------------
        if stage_callback:
            stage_callback("Moving")
        try:
            if _TEST_INJECT["move"] in directory.name:
                raise MoveError("Injected by test-notify --type move")
            destinations = move_to_library(
                audio_files=audio_files,
                staging_dir=directory,
                library_root=config.paths.library,
                path_template=config.library.path_template,
            )
        except MoveError as exc:
            logger.error("Move failed: %s", exc)
            _notify(notify_callback, "Move failed", path.name)
            _quarantine(directory, config.paths.staging)
            return

        logger.info(
            "Pipeline complete: %d file(s) moved to library for release %r",
            len(destinations),
            title,
        )

    finally:
        # Always clear the stage so the caller's display resets on success,
        # quarantine, or unexpected error.
        if stage_callback:
            stage_callback("")


def _fetch_and_embed_via_extension(
    ctx: KampGround,
    audio_files: list[Path],
    release_mbid: str,
    release_group_mbid: str,
    directory: Path,
    min_dimension: int,
    max_bytes: int,
) -> None:
    """Fetch cover art via KampCoverArtArchive extension and embed in audio files.

    Checks *directory* for a bundled image first (local-first; host responsibility
    because it requires file path access).  If no qualifying local image is found,
    delegates to KampCoverArtArchive to fetch from the MusicBrainz Cover Art Archive.
    Embedding is performed by the host — the extension only returns image bytes.
    """
    from .artwork import _load_local_artwork, has_embedded_art

    image_bytes: bytes | None = None
    mime_type = "image/jpeg"

    # Local-first: check for a bundled cover image in the staging directory.
    local = find_local_artwork(directory)
    if local is not None:
        image_bytes = _load_local_artwork(local, min_dimension, max_bytes)
        if image_bytes is not None:
            logger.info("Using bundled artwork from %s", local)
            mime_type = _detect_mime(image_bytes)

    if image_bytes is None:
        # Skip the Cover Art Archive network call when all files already have
        # qualifying embedded art — cheaper to keep what we have.
        if audio_files and all(
            has_embedded_art(f, min_dimension, max_bytes) for f in audio_files
        ):
            logger.info(
                "All %d file(s) have qualifying embedded art — skipping Cover Art Archive fetch",
                len(audio_files),
            )
            return

        query = ArtworkQuery(
            mbid=release_mbid,
            release_group_mbid=release_group_mbid,
            album="",
            artist="",
            min_dimension=min_dimension,
            max_bytes=max_bytes,
        )
        result: ArtworkResult | None = KampCoverArtArchive(ctx).fetch(query)
        if result is not None:
            image_bytes = result.image_bytes
            mime_type = result.mime_type

    if image_bytes is None:
        logger.warning(
            "No qualifying cover art found for release %s "
            "(min %dpx, max %d bytes) — skipping artwork",
            release_mbid,
            min_dimension,
            max_bytes,
        )
        return

    logger.info(
        "Embedding cover art (%d bytes) into %d file(s)",
        len(image_bytes),
        len(audio_files),
    )
    for audio_file in audio_files:
        _embed(audio_file, image_bytes)


def _mb_tags_conflict(
    original: list[TrackMetadata],
    enriched: list[TrackMetadata],
) -> bool:
    """Return True if MB-enriched artist or album differs from existing file tags.

    Only flags a conflict when the file already has non-empty artist/album tags
    — files with no tags at all can't conflict, only be filled in.
    Comparison is case-insensitive and whitespace-normalised.
    """
    if not original or not enriched:
        return False
    orig = original[0]
    enr = enriched[0]

    def _norm(s: str) -> str:
        return s.strip().lower()

    if orig.artist and enr.artist and _norm(orig.artist) != _norm(enr.artist):
        return True
    if orig.album and enr.album and _norm(orig.album) != _norm(enr.album):
        return True
    return False


def _quarantine(item: Path, staging_root: Path) -> None:
    """Move *item* to staging/errors/ to prevent reprocessing."""
    errors_dir = staging_root / "errors"
    errors_dir.mkdir(exist_ok=True)
    dest = errors_dir / item.name
    try:
        shutil.move(str(item), dest)
        logger.info("Quarantined %s → %s", item, dest)
    except Exception as exc:
        logger.error("Failed to quarantine %s: %s", item, exc)
