"""Tests for DaemonCore lifecycle management."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from kamp_daemon.config import Config
from kamp_daemon.daemon_core import DaemonCore, _PID_PATH


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = MagicMock(spec=Config)
    cfg.paths = MagicMock()
    return cfg


@pytest.fixture
def mock_watcher():
    with patch("kamp_daemon.daemon_core.Watcher") as cls:
        yield cls


@pytest.fixture
def mock_syncer():
    with patch("kamp_daemon.daemon_core.KampBandcampSyncer") as cls:
        yield cls


@pytest.fixture
def mock_pid(tmp_path: Path):
    pid_path = tmp_path / "daemon.pid"
    with patch("kamp_daemon.daemon_core._PID_PATH", pid_path):
        yield pid_path


@pytest.fixture
def core(
    config: Config,
    mock_watcher: MagicMock,
    mock_syncer: MagicMock,
    mock_pid: Path,
) -> DaemonCore:
    return DaemonCore(config)


class TestInitialState:
    def test_state_is_stopped_before_start(self, core: DaemonCore) -> None:
        assert core.state == "stopped"

    def test_watcher_exists_before_start(
        self, core: DaemonCore, mock_watcher: MagicMock
    ) -> None:
        # Watcher is created at __init__ time so callers can wire callbacks
        # before start() launches threads.
        assert core.watcher is mock_watcher.return_value

    def test_syncer_exists_before_start(
        self, core: DaemonCore, mock_syncer: MagicMock
    ) -> None:
        # Syncer is created at __init__ time so callers can wire callbacks
        # before start() launches threads.
        assert core.syncer is mock_syncer.return_value


class TestStart:
    def test_starts_watcher_and_syncer(
        self,
        core: DaemonCore,
        mock_watcher: MagicMock,
        mock_syncer: MagicMock,
        mock_pid: Path,
    ) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()

        mock_watcher.return_value.start.assert_called_once()
        mock_syncer.return_value.start.assert_called_once()

    def test_state_becomes_running(self, core: DaemonCore, mock_pid: Path) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        assert core.state == "running"

    def test_exposes_watcher_after_start(
        self, core: DaemonCore, mock_watcher: MagicMock, mock_pid: Path
    ) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        assert core.watcher is mock_watcher.return_value

    def test_exposes_syncer_after_start(
        self, core: DaemonCore, mock_syncer: MagicMock, mock_pid: Path
    ) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        assert core.syncer is mock_syncer.return_value

    def test_writes_pidfile(self, core: DaemonCore, mock_pid: Path) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        assert mock_pid.exists()
        assert mock_pid.read_text().strip().isdigit()


class TestStop:
    def test_pauses_watcher_and_syncer(
        self,
        core: DaemonCore,
        mock_watcher: MagicMock,
        mock_syncer: MagicMock,
        mock_pid: Path,
    ) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        core.stop()
        mock_watcher.return_value.pause.assert_called_once()
        mock_syncer.return_value.pause.assert_called_once()

    def test_state_becomes_paused(self, core: DaemonCore, mock_pid: Path) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        core.stop()
        assert core.state == "paused"


class TestResume:
    def test_resumes_watcher_and_syncer(
        self,
        core: DaemonCore,
        mock_watcher: MagicMock,
        mock_syncer: MagicMock,
        mock_pid: Path,
    ) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        core.stop()
        core.resume()
        mock_watcher.return_value.resume.assert_called_once()
        mock_syncer.return_value.resume.assert_called_once()

    def test_state_becomes_running_again(
        self, core: DaemonCore, mock_pid: Path
    ) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        core.stop()
        core.resume()
        assert core.state == "running"


class TestShutdown:
    def test_stops_watcher_and_syncer(
        self,
        core: DaemonCore,
        mock_watcher: MagicMock,
        mock_syncer: MagicMock,
        mock_pid: Path,
    ) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        core.shutdown()
        mock_syncer.return_value.stop.assert_called_once()
        mock_watcher.return_value.stop.assert_called_once()

    def test_state_becomes_stopped(self, core: DaemonCore, mock_pid: Path) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        core.shutdown()
        assert core.state == "stopped"

    def test_sets_done_event(self, core: DaemonCore, mock_pid: Path) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()
        core.shutdown()
        assert core._done.is_set()


class TestBeforeStart:
    """stop/resume/shutdown are safe to call before start()."""

    def test_stop_before_start_is_safe(self, core: DaemonCore) -> None:
        core.stop()  # must not raise
        assert core.state == "paused"

    def test_resume_before_start_is_safe(self, core: DaemonCore) -> None:
        core.resume()  # must not raise
        assert core.state == "running"

    def test_shutdown_before_start_is_safe(self, core: DaemonCore) -> None:
        core.shutdown()  # must not raise
        assert core.state == "stopped"


class TestSignalHandlers:
    def test_sigint_triggers_shutdown(self, core: DaemonCore, mock_pid: Path) -> None:
        import signal as _signal

        installed: dict[int, object] = {}

        def capture(sig: int, handler: object) -> None:
            installed[sig] = handler

        with patch("kamp_daemon.daemon_core.signal.signal", side_effect=capture):
            with patch.object(core, "start", wraps=core.start):
                # Call _install_signal_handlers directly to avoid real signal changes
                core._install_signal_handlers()

        sigint_handler = installed[_signal.SIGINT]
        assert callable(sigint_handler)
        with patch.object(core, "shutdown") as mock_shutdown:
            sigint_handler(int(_signal.SIGINT), None)  # type: ignore[call-arg]
            mock_shutdown.assert_called_once()

    def test_sigusr1_triggers_stop(self, core: DaemonCore) -> None:
        import signal as _signal

        installed: dict[int, object] = {}

        with patch(
            "kamp_daemon.daemon_core.signal.signal",
            side_effect=lambda s, h: installed.update({s: h}),
        ):
            core._install_signal_handlers()

        handler = installed[_signal.SIGUSR1]
        with patch.object(core, "stop") as mock_stop:
            handler(int(_signal.SIGUSR1), None)  # type: ignore[call-arg]
            mock_stop.assert_called_once()

    def test_sigusr2_triggers_resume(self, core: DaemonCore) -> None:
        import signal as _signal

        installed: dict[int, object] = {}

        with patch(
            "kamp_daemon.daemon_core.signal.signal",
            side_effect=lambda s, h: installed.update({s: h}),
        ):
            core._install_signal_handlers()

        handler = installed[_signal.SIGUSR2]
        with patch.object(core, "resume") as mock_resume:
            handler(int(_signal.SIGUSR2), None)  # type: ignore[call-arg]
            mock_resume.assert_called_once()


class TestReload:
    def test_reload_delegates_to_watcher_and_syncer(
        self,
        core: DaemonCore,
        mock_watcher: MagicMock,
        mock_syncer: MagicMock,
        config: Config,
    ) -> None:
        new_config = MagicMock(spec=Config)
        core.reload(new_config)
        mock_watcher.return_value.reload.assert_called_once_with(new_config)
        mock_syncer.return_value.reload.assert_called_once_with(new_config)

    def test_reload_updates_stored_config(
        self, core: DaemonCore, config: Config
    ) -> None:
        new_config = MagicMock(spec=Config)
        core.reload(new_config)
        assert core._config is new_config


class TestWait:
    def test_returns_after_shutdown(self, core: DaemonCore, mock_pid: Path) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()

        # Trigger shutdown from a background thread after a brief delay
        def _delayed_shutdown() -> None:
            import time

            time.sleep(0.05)
            core.shutdown()

        t = threading.Thread(target=_delayed_shutdown, daemon=True)
        t.start()
        core.wait()  # should unblock
        t.join(timeout=1)
        assert not t.is_alive()

    def test_removes_pidfile_on_exit(self, core: DaemonCore, mock_pid: Path) -> None:
        with patch.object(core, "_install_signal_handlers"):
            core.start()

        assert mock_pid.exists()

        def _shutdown() -> None:
            import time

            time.sleep(0.02)
            core.shutdown()

        t = threading.Thread(target=_shutdown, daemon=True)
        t.start()
        core.wait()
        t.join(timeout=1)

        assert not mock_pid.exists()
