"""Tests for kamp_daemon.__main__ helpers."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.__main__ import (
    _SERVICE_LABEL,
    _cmd_install_service,
    _cmd_play,
    _cmd_status,
    _cmd_stop,
    _launchd_domain,
    _parse_launchctl_info,
    _resolve_kamp_binary,
    _service_pid,
    _service_registered,
)


def _launchctl_result(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


def _launchctl_output(pid: int | None = None, last_exit: int = 0) -> str:
    """Build realistic launchctl list <label> dict-style output for tests."""
    lines = [
        "{",
        f'\t"Label" = "{_SERVICE_LABEL}";',
        f'\t"LastExitStatus" = {last_exit};',
    ]
    if pid is not None:
        lines.append(f'\t"PID" = {pid};')
    lines.append("};")
    return "\n".join(lines) + "\n"


class TestParseLaunchctlInfo:
    def test_parses_pid(self) -> None:
        assert _parse_launchctl_info(_launchctl_output(pid=12345))["PID"] == "12345"

    def test_parses_last_exit_status(self) -> None:
        assert (
            _parse_launchctl_info(_launchctl_output(last_exit=256))["LastExitStatus"]
            == "256"
        )

    def test_no_pid_key_when_not_running(self) -> None:
        assert "PID" not in _parse_launchctl_info(_launchctl_output(pid=None))

    def test_parses_quoted_string_value(self) -> None:
        assert _parse_launchctl_info(_launchctl_output())["Label"] == _SERVICE_LABEL


class TestServiceRegistered:
    def test_returns_true_when_registered(self) -> None:
        with patch(
            "subprocess.run",
            return_value=_launchctl_result(_launchctl_output()),
        ):
            assert _service_registered() is True

    def test_returns_false_when_not_registered(self) -> None:
        with patch("subprocess.run", return_value=_launchctl_result("", returncode=1)):
            assert _service_registered() is False


class TestServicePid:
    def test_returns_pid_when_running(self) -> None:
        with patch(
            "subprocess.run",
            return_value=_launchctl_result(_launchctl_output(pid=12345)),
        ):
            assert _service_pid() == 12345

    def test_returns_none_when_not_loaded(self) -> None:
        with patch("subprocess.run", return_value=_launchctl_result("", returncode=1)):
            assert _service_pid() is None

    def test_returns_none_when_no_pid_key(self) -> None:
        # Registered but not running — PID key absent from output
        with patch(
            "subprocess.run",
            return_value=_launchctl_result(_launchctl_output(pid=None)),
        ):
            assert _service_pid() is None

    def test_returns_none_when_pid_is_zero(self) -> None:
        with patch(
            "subprocess.run",
            return_value=_launchctl_result(_launchctl_output(pid=0)),
        ):
            assert _service_pid() is None


class TestCmdStop:
    def test_not_installed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plist = tmp_path / "com.kamp.plist"
        with patch("kamp_daemon.__main__._PLIST_PATH", plist):
            _cmd_stop()
        assert "not installed" in capsys.readouterr().out

    def test_already_stopped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=None),
        ):
            _cmd_stop()
        assert "already stopped" in capsys.readouterr().out

    def test_stops_running_service(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=42),
            patch("subprocess.run") as mock_run,
        ):
            _cmd_stop()
        mock_run.assert_called_once_with(
            ["launchctl", "bootout", _launchd_domain(), str(plist)], check=False
        )
        assert "stopped" in capsys.readouterr().out


class TestCmdPlay:
    def test_not_installed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plist = tmp_path / "com.kamp.plist"
        with patch("kamp_daemon.__main__._PLIST_PATH", plist):
            _cmd_play()
        out = capsys.readouterr().out
        assert "not installed" in out

    def test_already_running(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=42),
        ):
            _cmd_play()
        assert "already running" in capsys.readouterr().out

    def test_starts_unregistered_service(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=None),
            patch("kamp_daemon.__main__._service_registered", return_value=False),
            patch("subprocess.run") as mock_run,
        ):
            _cmd_play()
        mock_run.assert_called_once_with(
            ["launchctl", "bootstrap", _launchd_domain(), str(plist)], check=True
        )
        assert "started" in capsys.readouterr().out

    def test_kickstarts_registered_but_stopped_service(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        domain = _launchd_domain()
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=None),
            patch("kamp_daemon.__main__._service_registered", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            _cmd_play()
        mock_run.assert_called_once_with(
            ["launchctl", "kickstart", f"{domain}/{_SERVICE_LABEL}"], check=True
        )
        assert "started" in capsys.readouterr().out


class TestCmdStatus:
    def test_not_installed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plist = tmp_path / "com.kamp.plist"
        with patch("kamp_daemon.__main__._PLIST_PATH", plist):
            _cmd_status()
        assert "not installed" in capsys.readouterr().out

    def test_stopped_cleanly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        # returncode=1 → not registered; no crash info, no subprocess needed
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=None),
            patch(
                "kamp_daemon.__main__._launchctl_list",
                return_value=_launchctl_result("", returncode=1),
            ),
        ):
            _cmd_status()
        assert "not running" in capsys.readouterr().out

    def test_crashed_shows_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=None),
            patch(
                "subprocess.run",
                return_value=_launchctl_result(_launchctl_output(last_exit=256)),
            ),
        ):
            _cmd_status()
        out = capsys.readouterr().out
        assert "crashed" in out
        assert "256" in out

    def test_running_shows_uptime(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.kamp.plist"
        plist.touch()
        ps_result = MagicMock()
        ps_result.returncode = 0
        ps_result.stdout = "  1:23:45\n"
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._service_pid", return_value=99),
            patch("subprocess.run", return_value=ps_result),
        ):
            _cmd_status()
        out = capsys.readouterr().out
        assert "running" in out
        assert "1:23:45" in out


class TestLogNoiseSuppression:
    """Third-party loggers are silenced at INFO level to reduce noise."""

    _NOISY_LOGGERS = ["asyncio", "PIL.TiffImagePlugin"]

    def setup_method(self) -> None:
        # Reset logger levels before each test so they don't bleed between runs.
        for name in self._NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)

    def _run_main_with_log_level(self, level: str) -> None:
        from kamp_daemon.__main__ import main

        with patch("sys.argv", ["kamp", "--log-level", level, "config", "show"]):
            with patch("kamp_daemon.__main__._cmd_config"):
                main()

    def test_asyncio_suppressed_at_info(self) -> None:
        self._run_main_with_log_level("INFO")
        assert logging.getLogger("asyncio").level == logging.WARNING

    def test_pil_tiff_suppressed_at_info(self) -> None:
        self._run_main_with_log_level("INFO")
        assert logging.getLogger("PIL.TiffImagePlugin").level == logging.WARNING

    def test_asyncio_not_suppressed_at_debug(self) -> None:
        self._run_main_with_log_level("DEBUG")
        assert logging.getLogger("asyncio").level != logging.WARNING

    def test_pil_tiff_not_suppressed_at_debug(self) -> None:
        self._run_main_with_log_level("DEBUG")
        assert logging.getLogger("PIL.TiffImagePlugin").level != logging.WARNING


class TestResolveKampBinary:
    def test_prefers_brew_prefix_path(self, tmp_path: Path) -> None:
        brew_bin = tmp_path / "bin" / "kamp"
        brew_bin.parent.mkdir()
        brew_bin.touch()
        brew_result = MagicMock()
        brew_result.stdout = str(tmp_path)
        with patch("subprocess.run", return_value=brew_result) as mock_run:
            result = _resolve_kamp_binary()
        mock_run.assert_called_once()
        assert result == str(brew_bin)

    def test_falls_back_to_known_homebrew_paths(self, tmp_path: Path) -> None:
        fake_brew_bin = tmp_path / "kamp"
        fake_brew_bin.touch()
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch(
                "kamp_daemon.__main__._HOMEBREW_KAMP_PATHS",
                [str(fake_brew_bin)],
            ),
        ):
            result = _resolve_kamp_binary()
        assert result == str(fake_brew_bin)

    def test_warns_when_resolved_to_pyenv_shim(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        shim_path = "/Users/user/.pyenv/shims/kamp"
        with (
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("kamp_daemon.__main__._HOMEBREW_KAMP_PATHS", []),
            patch("shutil.which", return_value=shim_path),
        ):
            result = _resolve_kamp_binary()
        assert result == shim_path
        assert "pyenv shim" in capsys.readouterr().out

    def test_plist_uses_resolved_binary(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.touch()
        plist = tmp_path / "com.kamp.plist"
        expected = "/opt/homebrew/bin/kamp"
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("kamp_daemon.__main__._resolve_kamp_binary", return_value=expected),
            patch("subprocess.run"),
        ):
            _cmd_install_service(config_path)
        assert expected in plist.read_text()


class TestCmdInstallService:
    def test_runs_first_run_setup_when_no_config(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        config_path = tmp_path / "config.toml"
        plist = tmp_path / "com.kamp.plist"
        with (
            patch("kamp_daemon.__main__._PLIST_PATH", plist),
            patch("subprocess.run"),
            patch(
                "kamp_daemon.__main__._resolve_kamp_binary",
                return_value="/opt/homebrew/bin/kamp",
            ),
            patch(
                "kamp_daemon.__main__.Config.first_run_setup",
                return_value=None,
            ) as mock_setup,
        ):
            _cmd_install_service(config_path)
        mock_setup.assert_called_once_with(config_path)
