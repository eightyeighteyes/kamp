"""Watches the config file for changes and triggers a live reload."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config

logger = logging.getLogger(__name__)


class _ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, config_path: Path, on_change: Callable[[Config], None]) -> None:
        super().__init__()
        self._config_path = config_path.resolve()
        self._on_change = on_change

    def _handle(self, path: Path) -> None:
        if path.resolve() != self._config_path:
            return
        logger.info("Config file changed — reloading.")
        try:
            config = Config.load(self._config_path)
        except Exception:
            logger.exception("Failed to reload config; keeping current settings.")
            return
        try:
            self._on_change(config)
        except Exception:
            logger.exception("Error applying reloaded config.")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle(Path(str(event.src_path)))

    def on_created(self, event: FileSystemEvent) -> None:
        # Some editors save atomically (write temp → rename), which fires
        # on_created for the destination instead of on_modified.
        if not event.is_directory:
            self._handle(Path(str(event.src_path)))


class ConfigMonitor:
    """Watch *config_path* for modifications and call *on_change* with the new Config.

    Uses a watchdog Observer on the config file's parent directory (watchdog
    cannot reliably watch a single file directly on all platforms).
    """

    def __init__(self, config_path: Path, on_change: Callable[[Config], None]) -> None:
        self._config_path = config_path
        self._on_change = on_change
        self._observer = Observer()

    def start(self) -> None:
        handler = _ConfigFileHandler(self._config_path, self._on_change)
        self._observer.schedule(handler, str(self._config_path.parent), recursive=False)
        self._observer.start()
        logger.info("Watching config file for changes: %s", self._config_path)

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
        logger.info("Config monitor stopped.")
