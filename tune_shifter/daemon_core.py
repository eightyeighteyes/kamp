"""DaemonCore: lifecycle management for the tune-shifter daemon.

Encapsulates Watcher, Syncer, and ConfigMonitor start/stop/pause/resume,
signal handler installation, and PID-file management so that __main__.py
remains thin CLI glue and the menu bar path can hand the main thread to
the rumps AppKit run loop instead of blocking here.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path
from typing import Literal

from .config import Config, _state_dir
from .config_monitor import ConfigMonitor
from .syncer import Syncer
from .watcher import Watcher

_PID_PATH: Path = _state_dir() / "daemon.pid"

DaemonState = Literal["running", "paused", "stopped"]

_logger = logging.getLogger(__name__)


class DaemonCore:
    """Manage the daemon pipeline lifecycle.

    Owns Watcher, Syncer, and ConfigMonitor instances and coordinates their
    start/stop/pause/resume. Installs OS signal handlers so that SIGINT/SIGTERM
    trigger a clean shutdown and SIGUSR1/SIGUSR2 pause and resume the pipeline.
    """

    def __init__(self, config: Config, config_path: Path) -> None:
        self._config = config
        self._config_path = config_path
        self._state: DaemonState = "stopped"
        self._done = threading.Event()
        self._watcher: Watcher | None = None
        self._syncer: Syncer | None = None
        self._monitor: ConfigMonitor | None = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> DaemonState:
        return self._state

    @property
    def watcher(self) -> Watcher | None:
        """The active Watcher instance, or None before start()."""
        return self._watcher

    @property
    def syncer(self) -> Syncer | None:
        """The active Syncer instance, or None before start()."""
        return self._syncer

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Create and start all pipeline components, write pidfile, install signals."""
        self._watcher = Watcher(self._config)
        self._syncer = Syncer(self._config)

        def _on_config_reload(new_config: Config) -> None:
            self._config = new_config
            if self._watcher:
                self._watcher.reload(new_config)
            if self._syncer:
                self._syncer.reload(new_config)

        self._monitor = ConfigMonitor(self._config_path, _on_config_reload)

        self._watcher.start()
        self._syncer.start()
        self._monitor.start()

        # Write pidfile so `daemon pause` / `daemon resume` can signal this process.
        _PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PID_PATH.write_text(str(os.getpid()))

        self._state = "running"
        self._install_signal_handlers()

    def stop(self) -> None:
        """Pause the pipeline (watcher + syncer). Used by the Play/Stop menu toggle."""
        _logger.info("Pipeline pausing…")
        if self._watcher:
            self._watcher.pause()
        if self._syncer:
            self._syncer.pause()
        self._state = "paused"

    def resume(self) -> None:
        """Resume the pipeline after stop()."""
        _logger.info("Pipeline resuming…")
        if self._watcher:
            self._watcher.resume()
        if self._syncer:
            self._syncer.resume()
        self._state = "running"

    def shutdown(self) -> None:
        """Stop all components and unblock wait()."""
        _logger.info("Shutting down…")
        if self._monitor:
            self._monitor.stop()
        if self._syncer:
            self._syncer.stop()
        if self._watcher:
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
