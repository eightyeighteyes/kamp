"""Tests for tune_shifter.__main__ helpers."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tune_shifter.__main__ import (
    _SERVICE_LABEL,
    _cmd_play,
    _cmd_status,
    _cmd_stop,
    _launchd_domain,
    _parse_launchctl_info,
    _service_pid,
    _service_registered,
    _yn_prompt,
)


class TestYnPrompt:
    def test_empty_input_returns_default_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert _yn_prompt("Question?", default=True) is True

    def test_empty_input_returns_default_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert _yn_prompt("Question?", default=False) is False

    def test_y_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "y")
        assert _yn_prompt("Question?") is True

    def test_yes_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "yes")
        assert _yn_prompt("Question?") is True

    def test_n_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert _yn_prompt("Question?") is False

    def test_eof_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_eof(_: str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        assert _yn_prompt("Question?", default=True) is True


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
        plist = tmp_path / "com.tune-shifter.plist"
        with patch("tune_shifter.__main__._PLIST_PATH", plist):
            _cmd_stop()
        assert "not installed" in capsys.readouterr().out

    def test_already_stopped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=None),
        ):
            _cmd_stop()
        assert "already stopped" in capsys.readouterr().out

    def test_stops_running_service(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=42),
            patch("subprocess.run") as mock_run,
        ):
            _cmd_stop()
        mock_run.assert_called_once_with(
            ["launchctl", "bootout", _launchd_domain(), str(plist)], check=False
        )
        assert "stopped" in capsys.readouterr().out


class TestCmdPlay:
    def test_not_installed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        with patch("tune_shifter.__main__._PLIST_PATH", plist):
            _cmd_play()
        out = capsys.readouterr().out
        assert "not installed" in out

    def test_already_running(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=42),
        ):
            _cmd_play()
        assert "already running" in capsys.readouterr().out

    def test_starts_unregistered_service(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=None),
            patch("tune_shifter.__main__._service_registered", return_value=False),
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
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        domain = _launchd_domain()
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=None),
            patch("tune_shifter.__main__._service_registered", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            _cmd_play()
        mock_run.assert_called_once_with(
            ["launchctl", "kickstart", f"{domain}/{_SERVICE_LABEL}"], check=True
        )
        assert "started" in capsys.readouterr().out


class TestCmdStatus:
    def test_not_installed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        with patch("tune_shifter.__main__._PLIST_PATH", plist):
            _cmd_status()
        assert "not installed" in capsys.readouterr().out

    def test_stopped_cleanly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        # returncode=1 → not registered; no crash info, no subprocess needed
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=None),
            patch(
                "tune_shifter.__main__._launchctl_list",
                return_value=_launchctl_result("", returncode=1),
            ),
        ):
            _cmd_status()
        assert "not running" in capsys.readouterr().out

    def test_crashed_shows_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=None),
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
        plist = tmp_path / "com.tune-shifter.plist"
        plist.touch()
        ps_result = MagicMock()
        ps_result.returncode = 0
        ps_result.stdout = "  1:23:45\n"
        with (
            patch("tune_shifter.__main__._PLIST_PATH", plist),
            patch("tune_shifter.__main__._service_pid", return_value=99),
            patch("subprocess.run", return_value=ps_result),
        ):
            _cmd_status()
        out = capsys.readouterr().out
        assert "running" in out
        assert "1:23:45" in out
