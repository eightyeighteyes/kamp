"""Filesystem watcher that triggers the ingest pipeline on new staging items."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import (
    FileCreatedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
    FileSystemMovedEvent,
)
from watchdog.observers import Observer

from .config import Config
from .pipeline import run

logger = logging.getLogger(__name__)

_SETTLE_SECONDS = 2.0  # wait for file to stop growing before processing
_POLL_INTERVAL = 0.5  # how often to check file size during settle


class _StagingHandler(FileSystemEventHandler):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._staging_root = config.paths.staging
        # Track paths being debounced: path → timer
        self._pending: dict[Path, threading.Timer] = {}
        # Track paths whose pipeline is currently running to prevent double-execution
        self._in_flight: set[Path] = set()
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            path = Path(str(event.src_path))
            # Ignore the errors/ subdirectory
            if path.name == "errors":
                return
            self._schedule(path)
        elif isinstance(event, FileCreatedEvent):
            path = Path(str(event.src_path))
            if path.suffix.lower() == ".zip":
                self._schedule(path)

    def on_modified(self, event: FileSystemEvent) -> None:
        # FSEvents on macOS coalesces renames into DirModifiedEvent on the parent
        # directory rather than emitting DirCreatedEvent/DirMovedEvent for the new
        # item. Scan staging root on every modification and schedule any item that
        # has appeared and is not already pending.
        if not event.is_directory:
            return
        if Path(str(event.src_path)) != self._staging_root:
            return
        self._scan_staging_root()

    def _scan_staging_root(self) -> None:
        """Schedule any directories or ZIPs in staging that are not already pending."""
        try:
            children = list(self._staging_root.iterdir())
        except OSError:
            return
        with self._lock:
            pending_paths = set(self._pending)
            in_flight_paths = set(self._in_flight)
        for child in children:
            if child in pending_paths or child in in_flight_paths:
                continue
            if child.is_dir() and child.name != "errors":
                self._schedule(child)
            elif child.is_file() and child.suffix.lower() == ".zip":
                self._schedule(child)

    def on_moved(self, event: FileSystemMovedEvent) -> None:
        # On macOS, dragging a folder/file into staging fires a moved event rather
        # than a created event. Handle it the same way, but only for items whose
        # destination is directly inside staging (not nested subdirectories).
        dest = Path(str(event.dest_path))
        if dest.parent != self._staging_root:
            return
        if event.is_directory and dest.name != "errors":
            self._schedule(dest)
        elif not event.is_directory and dest.suffix.lower() == ".zip":
            self._schedule(dest)

    def _schedule(self, path: Path) -> None:
        with self._lock:
            if path in self._in_flight:
                logger.debug("Skipping schedule: %s is already being processed", path)
                return
            existing = self._pending.pop(path, None)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(_SETTLE_SECONDS, self._process, args=[path])
            self._pending[path] = timer
            timer.start()
        logger.debug("Scheduled processing of %s in %.1fs", path, _SETTLE_SECONDS)

    def _process(self, path: Path) -> None:
        with self._lock:
            self._pending.pop(path, None)
            self._in_flight.add(path)

        # Track any directory the pipeline extracts so we can cancel its
        # pending debounce timer and prevent a duplicate pipeline run.
        claimed_dir: list[Path] = []

        def _claim_directory(directory: Path) -> None:
            """Called by the pipeline right after extraction."""
            with self._lock:
                existing = self._pending.pop(directory, None)
                if existing is not None:
                    existing.cancel()
                    logger.debug(
                        "Cancelled duplicate timer for extracted dir %s", directory
                    )
                self._in_flight.add(directory)
            claimed_dir.append(directory)

        try:
            if not path.exists():
                logger.debug("Path no longer exists, skipping: %s", path)
                return

            # Wait until file size stops changing (fully written)
            if path.is_file():
                _wait_for_stable_size(path)

            logger.info("Triggering pipeline for %s", path)
            try:
                run(path, self._config, _on_directory=_claim_directory)
            except Exception:
                logger.exception("Unhandled error in pipeline for %s", path)
        finally:
            with self._lock:
                self._in_flight.discard(path)
                if claimed_dir:
                    self._in_flight.discard(claimed_dir[0])


def _wait_for_stable_size(path: Path, timeout: float = 60.0) -> None:
    """Block until *path*'s size stops changing or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    last_size = -1
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size == last_size:
            return
        last_size = size
        time.sleep(_POLL_INTERVAL)


class Watcher:
    """Manage a watchdog observer watching the staging directory."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._observer = Observer()
        self._handler = _StagingHandler(config)

    def start(self) -> None:
        staging = self._config.paths.staging
        staging.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(self._handler, str(staging), recursive=False)
        self._observer.start()
        logger.info("Watching staging directory: %s", staging)
        # Process any items already present when the daemon starts.
        self._handler._scan_staging_root()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
        logger.info("Watcher stopped")

    def reload(self, config: Config) -> None:
        """Apply a new config live.

        Updates the handler's config so future pipeline runs see the new
        settings.  If the staging path changed the observer is rescheduled to
        the new directory immediately.
        """
        old_staging = self._config.paths.staging
        self._config = config
        self._handler._config = config

        if config.paths.staging != old_staging:
            logger.info(
                "Staging path changed (%s → %s); rescheduling observer.",
                old_staging,
                config.paths.staging,
            )
            self._observer.unschedule_all()
            new_staging = config.paths.staging
            new_staging.mkdir(parents=True, exist_ok=True)
            self._handler = _StagingHandler(config)
            self._observer.schedule(self._handler, str(new_staging), recursive=False)
            self._handler._scan_staging_root()
        else:
            logger.info("Watcher config reloaded.")

    def join(self) -> None:
        """Block until the observer thread exits (e.g. after stop())."""
        self._observer.join()
