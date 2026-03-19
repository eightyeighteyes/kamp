"""Orchestrate the ingest pipeline: extract → tag → artwork → move."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from .artwork import ArtworkError, fetch_and_embed
from .config import Config
from .extractor import ExtractionError, extract, find_audio_files
from .mover import MoveError, move_to_library
from .tagger import TaggingError, is_tagged, read_release_mbids, tag_directory

logger = logging.getLogger(__name__)


def run(
    path: Path,
    config: Config,
    _on_directory: Callable[[Path], None] | None = None,
    stage_callback: Callable[[str], None] | None = None,
) -> None:
    """Process a single staging item (ZIP or directory) end-to-end.

    On per-step failure the item is moved to staging/errors/ so the watcher
    does not trigger on it again.  *stage_callback* (if provided) is called
    with the current stage name ("Extracting", "Tagging", etc.) and with an
    empty string in a finally block so the caller can always reset its display.
    """
    logger.info("Pipeline started for %s", path)

    try:
        # --- 1. Extract -------------------------------------------------------
        if stage_callback:
            stage_callback("Extracting")
        try:
            directory = extract(path)
        except ExtractionError as exc:
            logger.error("Extraction failed: %s", exc)
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
            _quarantine(directory, config.paths.staging)
            return

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
                release = tag_directory(directory, audio_files)
            except TaggingError as exc:
                logger.error("Tagging failed: %s", exc)
                _quarantine(directory, config.paths.staging)
                return
            mbid, rg_mbid = release.mbid, release.release_group_mbid
            title = release.title

        # --- 3. Artwork -------------------------------------------------------
        # Always run: even if art is already embedded, a higher-quality image may
        # be available (e.g. a bundled cover.jpg in the ZIP that beats the art the
        # original files shipped with).  fetch_and_embed handles the local-first
        # fallback and is cheap when no network call is needed.
        if stage_callback:
            stage_callback("Updating artwork")
        try:
            fetch_and_embed(
                mbid=mbid,
                audio_files=audio_files,
                min_dimension=config.artwork.min_dimension,
                max_bytes=config.artwork.max_bytes,
                release_group_mbid=rg_mbid,
                directory=directory,
            )
        except ArtworkError as exc:
            # Artwork failure is non-fatal: log and continue
            logger.warning("Artwork step failed: %s", exc)

        # --- 4. Move ----------------------------------------------------------
        if stage_callback:
            stage_callback("Moving")
        try:
            destinations = move_to_library(
                audio_files=audio_files,
                staging_dir=directory,
                library_root=config.paths.library,
                path_template=config.library.path_template,
            )
        except MoveError as exc:
            logger.error("Move failed: %s", exc)
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
