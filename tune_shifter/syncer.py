"""Polling thread that periodically syncs new Bandcamp purchases to staging."""

from __future__ import annotations

import logging
import logging.handlers
import multiprocessing
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import BandcampConfig, Config, _state_dir

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Subprocess worker functions
#
# These run inside an isolated child process spawned by _spawn_worker.
# Playwright and the bandcamp module are imported only inside the
# subprocess — when the process exits the OS reclaims all of their
# memory, which the parent process's pymalloc allocator would not
# release even after sys.modules eviction.
# ------------------------------------------------------------------


def _sync_worker(
    bc_config: BandcampConfig,
    staging_dir: Path,
    state_file: Path,
    status_q: Any,
    log_q: Any,
    result_q: Any,
) -> None:
    """Entry point for the sync subprocess."""
    # Route all log records to the parent via log_q for a consolidated log stream.
    _root = logging.getLogger()
    _root.setLevel(logging.DEBUG)
    _root.addHandler(logging.handlers.QueueHandler(log_q))
    try:
        from .bandcamp import sync_new_purchases

        paths = sync_new_purchases(
            bc_config=bc_config,
            staging_dir=staging_dir,
            state_file=state_file,
            status_callback=lambda msg: status_q.put(msg),
        )
        result_q.put(("ok", paths))
    except Exception as exc:  # noqa: BLE001
        result_q.put(("error", str(exc)))


def _mark_synced_worker(
    bc_config: BandcampConfig,
    state_file: Path,
    status_q: Any,
    log_q: Any,
    result_q: Any,
) -> None:
    """Entry point for the mark-synced subprocess."""
    _root = logging.getLogger()
    _root.setLevel(logging.DEBUG)
    _root.addHandler(logging.handlers.QueueHandler(log_q))
    try:
        from .bandcamp import mark_collection_synced

        mark_collection_synced(bc_config=bc_config, state_file=state_file)
        result_q.put(("ok", None))
    except Exception as exc:  # noqa: BLE001
        result_q.put(("error", str(exc)))


def _spawn_worker(  # pragma: no cover
    target: Any,
    args: tuple[Any, ...],
) -> tuple[Any, Any, Any, Any]:
    """Spawn an isolated subprocess running target(*args, status_q, log_q, result_q).

    Uses 'spawn' (not 'fork') so the child starts with a clean interpreter —
    no inherited file descriptors, threads, or loaded modules.

    Returns (proc, status_q, log_q, result_q).
    """
    ctx = multiprocessing.get_context("spawn")
    status_q: Any = ctx.Queue()
    log_q: Any = ctx.Queue()
    result_q: Any = ctx.Queue()
    # Not daemon=True: daemon subprocesses on macOS can have localhost networking
    # issues that break Playwright's Node.js ↔ Chromium DevTools Protocol channel.
    # The parent cleans up via proc.join() and the process terminates naturally.
    proc = ctx.Process(target=target, args=(*args, status_q, log_q, result_q))
    proc.start()
    return proc, status_q, log_q, result_q


def _replay_log_queue(log_q: Any) -> None:
    """Re-emit log records from the subprocess into the parent's log handlers.

    Called after the subprocess exits.  QueueHandler.prepare() serialises
    exc_info into exc_text before pickling, so every record is safe to handle.
    """
    while True:
        try:
            record = log_q.get_nowait()
            logging.getLogger(record.name).handle(record)
        except queue.Empty:
            break


class Syncer:
    """Run Bandcamp collection sync on a configurable interval.

    If ``poll_interval_minutes`` is 0 the syncer is a no-op daemon — use
    ``sync_once()`` directly (e.g. from the ``tune-shifter sync`` subcommand).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.status_callback: Callable[[str], None] | None = None

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

    def reload(self, config: Config) -> None:
        """Apply a new config live.

        If ``poll_interval_minutes`` changed the polling thread is restarted so
        the new interval takes effect immediately rather than at the end of the
        current sleep.
        """
        old_interval = (
            self._config.bandcamp.poll_interval_minutes if self._config.bandcamp else 0
        )
        new_interval = config.bandcamp.poll_interval_minutes if config.bandcamp else 0
        self._config = config

        if old_interval != new_interval:
            logger.info(
                "Poll interval changed (%d → %d min); restarting syncer thread.",
                old_interval,
                new_interval,
            )
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._stop_event.clear()
            self._thread = None
            self.start()
        else:
            logger.info("Syncer config reloaded.")

    def pause(self) -> None:
        """Stop the polling thread temporarily.

        The stop event is set and the thread is joined, but the event is *not*
        cleared — call resume() to restart polling.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        logger.info("Syncer paused")

    def resume(self) -> None:
        """Restart the polling thread after a pause."""
        self._stop_event.clear()
        self.start()
        logger.info("Syncer resumed")

    def sync_once(self) -> None:
        """Download any new purchases in an isolated subprocess.

        Playwright and bandcamp modules are loaded only inside the child
        process; when it exits the OS reclaims all of their memory.
        Log records emitted inside the subprocess are replayed into the
        parent's log stream after the process exits.
        """
        bc = self._config.bandcamp
        if bc is None:
            logger.warning("No [bandcamp] section in config — nothing to sync.")
            return

        state_file = _state_dir() / "bandcamp_state.json"
        logger.info("Starting Bandcamp sync…")

        proc, status_q, log_q, result_q = _spawn_worker(
            _sync_worker,
            (bc, self._config.paths.staging, state_file),
        )

        # Drain status messages while the subprocess runs, forwarding each
        # to the caller's callback so the menu bar can update in real time.
        while proc.is_alive():  # pragma: no cover
            try:
                msg = status_q.get(timeout=0.5)
                if self.status_callback is not None:
                    self.status_callback(msg)
            except queue.Empty:
                pass

        # Drain any messages that arrived just before the process exited.
        while True:
            try:
                msg = status_q.get_nowait()
                if self.status_callback is not None:
                    self.status_callback(msg)
            except queue.Empty:
                break

        proc.join(timeout=10)
        _replay_log_queue(log_q)

        try:
            status, value = result_q.get_nowait()
        except queue.Empty:  # pragma: no cover
            raise RuntimeError("Sync subprocess exited without returning a result")

        if status == "error":
            raise RuntimeError(f"Bandcamp sync failed: {value}")

        paths = value or []
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

        proc, _status_q, log_q, result_q = _spawn_worker(
            _mark_synced_worker,
            (bc, state_file),
        )
        proc.join(timeout=30)
        _replay_log_queue(log_q)

        try:
            status, value = result_q.get_nowait()
        except queue.Empty:  # pragma: no cover
            raise RuntimeError(
                "Mark-synced subprocess exited without returning a result"
            )

        if status == "error":
            raise RuntimeError(f"Bandcamp mark-synced failed: {value}")

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
