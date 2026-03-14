"""Polling thread that periodically syncs new Bandcamp purchases to staging."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .bandcamp import mark_collection_synced, sync_new_purchases
from .config import Config, _state_dir

logger = logging.getLogger(__name__)


class Syncer:
    """Run Bandcamp collection sync on a configurable interval.

    If ``poll_interval_minutes`` is 0 the syncer is a no-op daemon — use
    ``sync_once()`` directly (e.g. from the ``tune-shifter sync`` subcommand).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread (no-op if interval is 0)."""
        bc = self._config.bandcamp
        if bc is None or bc.poll_interval_minutes <= 0:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="syncer")
        self._thread.start()
        logger.info(
            "Bandcamp syncer started — polling every %d minute(s).",
            bc.poll_interval_minutes,
        )

    def stop(self) -> None:
        """Signal the polling thread to exit and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def sync_once(self) -> None:
        """Download any new purchases immediately (one-shot, blocking)."""
        bc = self._config.bandcamp
        if bc is None:
            logger.warning("No [bandcamp] section in config — nothing to sync.")
            return

        state_file = _state_dir() / "bandcamp_state.json"
        logger.info("Starting Bandcamp sync…")
        paths = sync_new_purchases(
            bc_config=bc,
            staging_dir=self._config.paths.staging,
            state_file=state_file,
        )
        if paths:
            logger.info("Sync complete: %d file(s) downloaded to staging.", len(paths))
        else:
            logger.info("Sync complete: nothing new.")

    def mark_synced(self) -> None:
        """Mark the entire collection as already downloaded without fetching anything."""
        bc = self._config.bandcamp
        if bc is None:
            logger.warning("No [bandcamp] section in config — nothing to mark.")
            return
        state_file = _state_dir() / "bandcamp_state.json"
        mark_collection_synced(bc_config=bc, state_file=state_file)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        bc = self._config.bandcamp
        assert bc is not None
        interval_seconds = bc.poll_interval_minutes * 60

        while not self._stop_event.is_set():
            try:
                self.sync_once()
            except Exception:
                logger.exception("Unhandled error during Bandcamp sync")
            self._stop_event.wait(timeout=interval_seconds)
