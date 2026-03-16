"""Orchestrate the ingest pipeline: extract → tag → artwork → move."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .artwork import ArtworkError, fetch_and_embed, has_embedded_art
from .config import Config
from .extractor import ExtractionError, extract, find_audio_files
from .mover import MoveError, move_to_library
from .tagger import TaggingError, is_tagged, read_release_mbids, tag_directory

logger = logging.getLogger(__name__)


def run(path: Path, config: Config) -> None:
    """Process a single staging item (ZIP or directory) end-to-end.

    On per-step failure the item is moved to staging/errors/ so the watcher
    does not trigger on it again.
    """
    logger.info("Pipeline started for %s", path)

    # --- 1. Extract -----------------------------------------------------------
    try:
        directory = extract(path)
    except ExtractionError as exc:
        logger.error("Extraction failed: %s", exc)
        _quarantine(path, config.paths.staging)
        return

    audio_files = find_audio_files(directory)
    if not audio_files:
        logger.error("No audio files found in %s", directory)
        _quarantine(directory, config.paths.staging)
        return

    # --- 2. Tag ---------------------------------------------------------------
    # Skip the MusicBrainz lookup (and tag writes) when every file already has
    # an MBID — the most expensive operation in the pipeline.  If even one file
    # is untagged, run the full pass for the whole directory to stay consistent.
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

    # --- 3. Artwork -----------------------------------------------------------
    # Checked independently of tagging: a file can be tagged but lack art if a
    # prior run's artwork step failed non-fatally.
    if all(has_embedded_art(f) for f in audio_files):
        logger.info("All files already have embedded art — skipping artwork step")
    else:
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

    # --- 4. Move --------------------------------------------------------------
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
