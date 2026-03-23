"""Orchestrate the ingest pipeline: extract → tag → artwork → move."""

from __future__ import annotations

import json
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
    payload = json.dumps(
        {"title": "Tune-Shifter", "subtitle": subtitle, "message": message}
    )
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
                release = tag_directory(directory, audio_files)
            except TaggingError as exc:
                logger.error("Tagging failed: %s", exc)
                _notify(notify_callback, "Tagging failed", path.name)
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
            if _TEST_INJECT["artwork"] in directory.name:
                raise ArtworkError("Injected by test-notify --type artwork")
            elif _TEST_INJECT["move"] not in directory.name:
                # Skip network artwork fetch when testing the move stage so it
                # doesn't raise its own ArtworkError before we reach the move
                # injection point.
                fetch_and_embed(
                    mbid=mbid,
                    audio_files=audio_files,
                    min_dimension=config.artwork.min_dimension,
                    max_bytes=config.artwork.max_bytes,
                    release_group_mbid=rg_mbid,
                    directory=directory,
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
