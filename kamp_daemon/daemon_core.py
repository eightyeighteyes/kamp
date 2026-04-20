"""DaemonCore: lifecycle management for the kamp daemon.

Encapsulates Watcher, Syncer start/stop/pause/resume, signal handler
installation, and PID-file management so that __main__.py remains thin CLI
glue and the menu bar path can hand the main thread to the rumps AppKit run
loop instead of blocking here.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path
from typing import Literal

from .config import Config, _state_dir
from .ext import ExtensionRegistry, discover_extensions
from .ext.context import KampGround, PlaybackSnapshot
from .watcher import Watcher

from .ext.builtin.bandcamp import KampBandcampSyncer

_PID_PATH: Path = _state_dir() / "daemon.pid"

DaemonState = Literal["running", "paused", "stopped"]

_logger = logging.getLogger(__name__)


class DaemonCore:
    """Manage the daemon pipeline lifecycle.

    Owns Watcher and Syncer instances and coordinates their start/stop/pause/resume.
    Installs OS signal handlers so that SIGINT/SIGTERM trigger a clean shutdown
    and SIGUSR1/SIGUSR2 pause and resume the pipeline.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._state: DaemonState = "stopped"
        self._done = threading.Event()
        # Created here so callers can wire callbacks (e.g. status_callback on
        # the syncer) before start() launches threads — avoids a race where the
        # first automatic sync fires before the menu bar has wired its callback.
        self._watcher: Watcher = Watcher(config)
        ctx = KampGround(playback=PlaybackSnapshot(), library_tracks=[])
        self._syncer: KampBandcampSyncer = KampBandcampSyncer(ctx)
        self._syncer._configure(config)
        self._extension_registry: ExtensionRegistry = ExtensionRegistry()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> DaemonState:
        return self._state

    @property
    def extension_registry(self) -> ExtensionRegistry:
        """The extension registry populated at start()."""
        return self._extension_registry

    @property
    def watcher(self) -> Watcher:
        """The Watcher instance (available immediately after construction)."""
        return self._watcher

    @property
    def syncer(self) -> KampBandcampSyncer:
        """The syncer instance (available immediately after construction)."""
        return self._syncer

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start all pipeline components, write pidfile, install signals."""
        discover_extensions(self._extension_registry)
        self._watcher.start()
        self._syncer.start()

        # Write pidfile so `daemon pause` / `daemon resume` can signal this process.
        _PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PID_PATH.write_text(str(os.getpid()))

        self._state = "running"
        self._install_signal_handlers()

    def stop(self) -> None:
        """Pause the pipeline (watcher + syncer). Used by the Play/Stop menu toggle."""
        _logger.info("Pipeline pausing…")
        self._watcher.pause()
        self._syncer.pause()
        self._state = "paused"

    def resume(self) -> None:
        """Resume the pipeline after stop()."""
        _logger.info("Pipeline resuming…")
        self._watcher.resume()
        self._syncer.resume()
        self._state = "running"

    def reload(self, config: Config) -> None:
        """Apply updated config to running components without restarting the daemon."""
        self._config = config
        self._watcher.reload(config)
        self._syncer.reload(config)

    def shutdown(self) -> None:
        """Stop all components and unblock wait()."""
        _logger.info("Shutting down…")
        self._syncer.stop()
        self._watcher.stop()
        self._state = "stopped"
        self._done.set()

    def wait(self) -> None:
        """Block until shutdown() is called. Used by the headless daemon path.

        The pidfile is removed here (in finally) rather than in shutdown() so
        that a caller who manages the main-thread run loop (e.g. rumps) can
        call shutdown() and then clean up the pidfile itself at a natural point.
        """
        try:
            self._done.wait()
        finally:
            _PID_PATH.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _shutdown(signum: int, frame: object) -> None:
            self.shutdown()

        def _pause(signum: int, frame: object) -> None:
            self.stop()

        def _resume(signum: int, frame: object) -> None:
            self.resume()

        signal.signal(signal.SIGINT, _shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _shutdown)  # not available on Windows
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, _pause)
        if hasattr(signal, "SIGUSR2"):
            signal.signal(signal.SIGUSR2, _resume)
