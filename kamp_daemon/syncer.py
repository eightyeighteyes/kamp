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


class NeedsLoginError(Exception):
    """No valid Bandcamp session — user must log in before syncing."""


logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Subprocess worker functions
#
# These run inside an isolated child process spawned by _spawn_worker.
# The bandcamp module is imported only inside the subprocess — process
# isolation provides a clean memory slate and prevents any leaked state
# (open sockets, caches) from affecting the parent daemon.
# ------------------------------------------------------------------


def _sync_worker(
    bc_config: BandcampConfig,
    watch_dir: Path,
    state_file: Path,
    db_path: Path,
    status_q: Any,
    log_q: Any,
    result_q: Any,
) -> None:
    """Entry point for the sync subprocess."""
    # Route all log records to the parent via log_q for a consolidated log stream.
    _root = logging.getLogger()
    _root.setLevel(logging.DEBUG)
    handler = logging.handlers.QueueHandler(log_q)
    _root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    try:
        from kamp_core.library import LibraryIndex

        from .bandcamp import sync_new_purchases

        index = LibraryIndex(db_path)
        try:
            paths = sync_new_purchases(
                bc_config=bc_config,
                watch_dir=watch_dir,
                state_file=state_file,
                index=index,
                status_callback=lambda msg: status_q.put(msg),
            )
            result_q.put(("ok", paths))
        finally:
            index.close()
    except Exception as exc:  # noqa: BLE001
        # NeedsLoginError is identified by class name so the parent process
        # does not need to import bandcamp to handle the login-needed case.
        if type(exc).__name__ == "NeedsLoginError":
            result_q.put(("needs_login", str(exc)))
        else:
            result_q.put(("error", str(exc)))
    finally:
        # Remove the QueueHandler so it doesn't linger when the worker runs
        # in-process during tests — otherwise _replay_log_queue would loop.
        _root.removeHandler(handler)


def _mark_synced_worker(
    bc_config: BandcampConfig,
    state_file: Path,
    db_path: Path,
    status_q: Any,
    log_q: Any,
    result_q: Any,
) -> None:
    """Entry point for the mark-synced subprocess."""
    _root = logging.getLogger()
    _root.setLevel(logging.DEBUG)
    handler = logging.handlers.QueueHandler(log_q)
    _root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    try:
        from kamp_core.library import LibraryIndex

        from .bandcamp import mark_collection_synced

        index = LibraryIndex(db_path)
        try:
            mark_collection_synced(
                bc_config=bc_config, state_file=state_file, index=index
            )
            result_q.put(("ok", None))
        finally:
            index.close()
    except Exception as exc:  # noqa: BLE001
        result_q.put(("error", str(exc)))
    finally:
        _root.removeHandler(handler)


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
    # Not daemon=True: the parent cleans up via proc.join() so the subprocess
    # terminates naturally rather than being killed when the parent exits.
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
    ``sync_once()`` directly (e.g. from the ``kamp sync`` subcommand).
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.status_callback: Callable[[str], None] | None = None
        self.error_callback: Callable[[str, str, str], None] | None = None

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

    def sync_once(self, *, skip_auto_mark: bool = False) -> None:
        """Download any new purchases in an isolated subprocess.

        The bandcamp module is loaded only inside the child process.
        Log records emitted inside the subprocess are replayed into the
        parent's log stream after the process exits.

        If no state file exists and ``skip_auto_mark`` is False, the entire
        collection is marked as already synced before downloading.  This
        prevents re-downloading the full collection on a first run where the
        user already has their Bandcamp purchases locally.  Pass
        ``skip_auto_mark=True`` (e.g. via ``kamp sync --download-all``)
        to bypass this behaviour and download everything from scratch.
        """
        bc = self._config.bandcamp
        if bc is None:
            logger.warning("No [bandcamp] section in config — nothing to sync.")
            return

        state_file = _state_dir() / "bandcamp_state.json"

        if not skip_auto_mark and not state_file.exists():
            logger.info(
                "No sync state found — marking existing collection as already synced "
                "before first download.  Use --download-all to re-download everything."
            )
            self.mark_synced()

        logger.info("Starting Bandcamp sync…")
        # Signal "sync in progress" immediately — the subprocess spends most
        # of its time logging in and fetching the collection before any per-item
        # status_callback is invoked, so without this the menu bar would show
        # "Idle" for the entire sync unless there are actual downloads.
        if self.status_callback is not None:
            self.status_callback("Syncing\u2026")

        db_path = _state_dir() / "library.db"
        proc, status_q, log_q, result_q = _spawn_worker(
            _sync_worker,
            (bc, self._config.paths.watch_folder, state_file, db_path),
        )

        # Drain both queues while the subprocess runs.  log_q MUST be drained
        # here — if the subprocess fills it without the parent consuming it,
        # the subprocess blocks on put() and proc.is_alive() never becomes
        # False (deadlock).  Draining here also surfaces logs in real time.
        while proc.is_alive():  # pragma: no cover
            try:
                msg = status_q.get(timeout=0.1)
                if self.status_callback is not None:
                    self.status_callback(msg)
            except queue.Empty:
                pass
            _replay_log_queue(log_q)

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

        if status == "needs_login":
            if self.status_callback is not None:
                self.status_callback("")
            raise NeedsLoginError(value)

        if status == "error":
            raise RuntimeError(f"Bandcamp sync failed: {value}")

        paths = value or []
        if paths:
            logger.info(
                "Sync complete: %d file(s) downloaded to watch folder.", len(paths)
            )
        else:
            logger.info("Sync complete: nothing new.")
        # Clear the status display in the menu bar (applies to both automatic
        # and manual syncs — an empty string signals "no active sync").
        if self.status_callback is not None:
            self.status_callback("")

    def mark_synced(self) -> None:
        """Mark the entire collection as already downloaded without fetching anything."""
        bc = self._config.bandcamp
        if bc is None:
            logger.warning("No [bandcamp] section in config — nothing to mark.")
            return
        state_file = _state_dir() / "bandcamp_state.json"
        db_path = _state_dir() / "library.db"

        proc, _status_q, log_q, result_q = _spawn_worker(
            _mark_synced_worker,
            (bc, state_file, db_path),
        )

        # Drain log_q while the subprocess runs — same pattern as sync_once().
        # Without draining, a verbose subprocess can fill the queue and block
        # on put(), preventing it from ever writing its result.
        while proc.is_alive():  # pragma: no cover
            _replay_log_queue(log_q)

        proc.join(timeout=10)
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
            except NeedsLoginError:
                # No session — surface via status callback and stop polling.
                # The user must log in (via menu bar or Preferences) before
                # automatic syncing can resume.
                logger.warning(
                    "Bandcamp sync paused: no valid session. Log in to resume."
                )
                if self.status_callback is not None:
                    self.status_callback("Login required")
                break
            except Exception as exc:
                logger.exception("Unhandled error during Bandcamp sync")
                if self.error_callback is not None:
                    self.error_callback(
                        "Kamp",
                        "Bandcamp sync failed",
                        str(exc)[:120],
                    )
            self._stop_event.wait(timeout=interval_seconds)


def logout() -> None:
    """Clear the Bandcamp session from DB and delete the sync-state file.

    After logout the next sync will re-authenticate interactively and
    re-examine the full collection.  Both the DB session row and the
    sync-state file are removed together — a session without state (or
    vice versa) would leave the system in an inconsistent half-logged-in
    state.
    """
    from kamp_core.library import LibraryIndex

    state = _state_dir()
    db_path = state / "library.db"
    state_file = state / "bandcamp_state.json"

    if db_path.exists():
        index = LibraryIndex(db_path)
        try:
            index.clear_session("bandcamp")
        finally:
            index.close()
        logger.info("Bandcamp logout: session cleared.")

    # Remove legacy session file if it still exists (e.g. migration failed).
    session_file = state / "bandcamp_session.json"
    removed: list[str] = []
    for f in (session_file, state_file):
        if f.exists():
            f.unlink()
            removed.append(f.name)

    if removed:
        logger.info("Bandcamp logout: removed %s.", ", ".join(removed))
    else:
        logger.info("Bandcamp logout: no state files found.")
