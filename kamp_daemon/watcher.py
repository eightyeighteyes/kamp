"""Filesystem watcher that triggers the ingest pipeline on new items in the watch folder."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import (
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
    FileSystemMovedEvent,
)
from watchdog.observers import Observer

from .config import Config
from .extractor import AUDIO_EXTENSIONS
from .pipeline import run_in_subprocess

logger = logging.getLogger(__name__)

_SETTLE_SECONDS = 2.0  # wait for file to stop growing before processing
_POLL_INTERVAL = 0.5  # how often to check file size during settle


class _WatchHandler(FileSystemEventHandler):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._watch_root = config.paths.watch_folder
        # Track paths being debounced: path → timer
        self._pending: dict[Path, threading.Timer] = {}
        # Track paths whose pipeline is currently running to prevent double-execution
        self._in_flight: set[Path] = set()
        self._lock = threading.Lock()
        # Set by Watcher to surface current pipeline stage in the menu bar.
        self.stage_callback: Callable[[str], None] | None = None
        # Set by Watcher to deliver error notifications to the menu bar.
        self.notification_callback: Callable[[str, str, str], None] | None = None

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            path = Path(str(event.src_path))
            # Ignore the errors/ subdirectory
            if path.name == "errors":
                return
            self._schedule(path)
        elif isinstance(event, FileCreatedEvent):
            path = Path(str(event.src_path))
            if path.suffix.lower() == ".zip" or path.suffix.lower() in AUDIO_EXTENSIONS:
                self._schedule(path)

    def on_modified(self, event: FileSystemEvent) -> None:
        # FSEvents on macOS coalesces renames into DirModifiedEvent on the parent
        # directory rather than emitting DirCreatedEvent/DirMovedEvent for the new
        # item. Scan watch root on every modification and schedule any item that
        # has appeared and is not already pending.
        if not event.is_directory:
            return
        if Path(str(event.src_path)) != self._watch_root:
            return
        self._scan_watch_root()

    def _scan_watch_root(self) -> None:
        """Schedule any directories or ZIPs in the watch folder that are not already pending."""
        try:
            children = list(self._watch_root.iterdir())
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
            elif child.is_file() and (
                child.suffix.lower() == ".zip"
                or child.suffix.lower() in AUDIO_EXTENSIONS
            ):
                self._schedule(child)

    def on_moved(self, event: FileSystemMovedEvent) -> None:
        # On macOS, dragging a folder/file into the watch folder fires a moved event
        # rather than a created event. Handle it the same way, but only for items whose
        # destination is directly inside the watch folder (not nested subdirectories).
        dest = Path(str(event.dest_path))
        if dest.parent != self._watch_root:
            return
        if event.is_directory and dest.name != "errors":
            self._schedule(dest)
        elif not event.is_directory and (
            dest.suffix.lower() == ".zip" or dest.suffix.lower() in AUDIO_EXTENSIONS
        ):
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
                run_in_subprocess(
                    path,
                    self._config,
                    _on_directory=_claim_directory,
                    stage_callback=self.stage_callback,
                    notification_callback=self.notification_callback,
                )
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
    """Manage a watchdog observer watching the watch folder."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._observer = Observer()
        self._handler = _WatchHandler(config)
        self._paused = False
        self._stage_callback: Callable[[str], None] | None = None
        self._notification_callback: Callable[[str, str, str], None] | None = None

    @property
    def stage_callback(self) -> Callable[[str], None] | None:
        return self._stage_callback

    @stage_callback.setter
    def stage_callback(self, cb: Callable[[str], None] | None) -> None:
        self._stage_callback = cb
        self._handler.stage_callback = cb

    @property
    def notification_callback(self) -> Callable[[str, str, str], None] | None:
        return self._notification_callback

    @notification_callback.setter
    def notification_callback(self, cb: Callable[[str, str, str], None] | None) -> None:
        self._notification_callback = cb
        self._handler.notification_callback = cb

    def start(self) -> None:
        watch_folder = self._config.paths.watch_folder
        watch_folder.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(self._handler, str(watch_folder), recursive=False)
        self._observer.start()
        logger.info("Watching watch folder: %s", watch_folder)
        # Process any items already present when the daemon starts.
        self._handler._scan_watch_root()

    def pause(self) -> None:
        """Stop pipeline processing without tearing down the daemon.

        Cancels pending debounce timers and stops the observer thread.
        Items dropped into the watch folder while paused are picked up on resume()
        via _scan_watch_root().
        """
        if self._paused:
            return
        self._paused = True
        with self._handler._lock:
            for timer in self._handler._pending.values():
                timer.cancel()
            self._handler._pending.clear()
        self._observer.stop()
        self._observer.join()
        logger.info("Watcher paused")

    def resume(self) -> None:
        """Restart watching after a pause."""
        if not self._paused:
            return
        self._paused = False
        watch_folder = self._config.paths.watch_folder
        self._observer = Observer()
        self._handler = _WatchHandler(self._config)
        self._handler.stage_callback = self._stage_callback
        self._handler.notification_callback = self._notification_callback
        self._observer.schedule(self._handler, str(watch_folder), recursive=False)
        self._observer.start()
        self._handler._scan_watch_root()
        logger.info("Watcher resumed")

    def stop(self) -> None:
        # Observer is already stopped when paused; avoid a redundant stop/join.
        if not self._paused:
            self._observer.stop()
            self._observer.join()
        logger.info("Watcher stopped")

    def reload(self, config: Config) -> None:
        """Apply a new config live.

        Updates the handler's config so future pipeline runs see the new
        settings.  If the watch folder path changed the observer is rescheduled
        to the new directory immediately.
        """
        old_watch_folder = self._config.paths.watch_folder
        self._config = config
        self._handler._config = config

        if config.paths.watch_folder != old_watch_folder:
            logger.info(
                "Watch folder changed (%s → %s); rescheduling observer.",
                old_watch_folder,
                config.paths.watch_folder,
            )
            self._observer.unschedule_all()
            new_watch_folder = config.paths.watch_folder
            new_watch_folder.mkdir(parents=True, exist_ok=True)
            self._handler = _WatchHandler(config)
            self._handler.stage_callback = self._stage_callback
            self._handler.notification_callback = self._notification_callback
            self._observer.schedule(
                self._handler, str(new_watch_folder), recursive=False
            )
            self._handler._scan_watch_root()
        else:
            logger.info("Watcher config reloaded.")

    def join(self) -> None:
        """Block until the observer thread exits (e.g. after stop())."""
        self._observer.join()


class _LibraryHandler(FileSystemEventHandler):
    """Debounced handler that fires a callback when audio files change in the library.

    Unlike _WatchHandler (one timer per path), a single timer is sufficient
    here because LibraryScanner.scan() always does a full recursive walk; there
    is no benefit to tracking individual paths.

    Events caused by an active ingest pipeline (files being moved into the
    library) continuously reset the timer, so the scan cannot fire until 2 s
    after the last change — satisfying AC#3 without cross-process coordination.
    """

    def __init__(self, library_root: Path, on_scan: Callable[[], None]) -> None:
        super().__init__()
        self._library_root = library_root
        self._on_scan = on_scan
        self._pending: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_created(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileCreatedEvent) and self._is_audio(event.src_path):
            self._schedule()

    def on_deleted(self, event: FileSystemEvent) -> None:
        # Directory deletions (e.g. entire album folder removed) are caught here
        # in addition to individual audio file deletions.
        if isinstance(event, (FileDeletedEvent, DirDeletedEvent)):
            self._schedule()

    def on_moved(self, event: FileSystemEvent) -> None:
        # Catch both individual audio file moves and whole directory moves (e.g.
        # album folder dragged out of the library).
        if isinstance(event, DirMovedEvent):
            self._schedule()
        elif isinstance(event, FileMovedEvent) and (
            self._is_audio(event.src_path) or self._is_audio(event.dest_path)
        ):
            self._schedule()

    def _is_audio(self, path: bytes | str) -> bool:
        return Path(os.fsdecode(path)).suffix.lower() in AUDIO_EXTENSIONS

    def _schedule(self) -> None:
        with self._lock:
            if self._pending is not None:
                self._pending.cancel()
            self._pending = threading.Timer(_SETTLE_SECONDS, self._fire)
            self._pending.start()
        logger.debug("Library re-scan scheduled in %.1fs", _SETTLE_SECONDS)

    def _fire(self) -> None:
        with self._lock:
            self._pending = None
        logger.info("Library change detected — triggering re-scan")
        try:
            self._on_scan()
        except Exception:
            logger.exception("Unhandled error in library re-scan")

    def cancel_pending(self) -> None:
        """Cancel any pending debounce timer without firing the scan."""
        with self._lock:
            if self._pending is not None:
                self._pending.cancel()
                self._pending = None


class LibraryWatcher:
    """Watch the library directory and trigger a re-scan on audio file changes."""

    def __init__(self, library_path: Path, on_change: Callable[[], None]) -> None:
        self._library_path = library_path
        self._observer = Observer()
        self._handler = _LibraryHandler(library_path, on_change)

    def start(self) -> None:
        self._library_path.mkdir(parents=True, exist_ok=True)
        self._observer.schedule(self._handler, str(self._library_path), recursive=True)
        self._observer.start()
        logger.info("Watching library directory: %s", self._library_path)

    def stop(self) -> None:
        self._handler.cancel_pending()
        self._observer.stop()
        self._observer.join()
        logger.info("Library watcher stopped")
