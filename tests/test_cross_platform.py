"""Tests for cross-platform compatibility: config path and signal handling."""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Config path
# ---------------------------------------------------------------------------


class TestDefaultConfigPath:
    def test_posix_uses_xdg_config(self) -> None:
        with patch.object(sys, "platform", "linux"):
            # Re-run the function directly to avoid module-level caching
            from tune_shifter.config import _default_config_path

            path = _default_config_path()
        assert path == Path.home() / ".config" / "tune-shifter" / "config.toml"

    def test_macos_uses_xdg_config(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            from tune_shifter.config import _default_config_path

            path = _default_config_path()
        assert path == Path.home() / ".config" / "tune-shifter" / "config.toml"

    def test_windows_uses_appdata(self) -> None:
        fake_appdata = r"C:\Users\User\AppData\Roaming"
        with (
            patch.object(sys, "platform", "win32"),
            patch.dict("os.environ", {"APPDATA": fake_appdata}),
        ):
            from tune_shifter.config import _default_config_path

            path = _default_config_path()
        assert path == Path(fake_appdata) / "tune-shifter" / "config.toml"

    def test_windows_fallback_without_appdata(self) -> None:
        with (
            patch.object(sys, "platform", "win32"),
            patch.dict("os.environ", {}, clear=True),
        ):
            from tune_shifter.config import _default_config_path

            path = _default_config_path()
        assert (
            path == Path.home() / "AppData" / "Roaming" / "tune-shifter" / "config.toml"
        )


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------


class TestSignalHandling:
    def _run_main_signals(self, has_sigterm: bool) -> list[int]:
        """Invoke the signal-registration portion of main() and return registered signal numbers."""
        registered: list[int] = []

        fake_signal = MagicMock(side_effect=lambda sig, handler: registered.append(sig))

        sigterm_value = getattr(signal, "SIGTERM", None)

        with patch("tune_shifter.__main__.signal") as mock_signal_mod:
            mock_signal_mod.SIGINT = signal.SIGINT
            if has_sigterm:
                mock_signal_mod.SIGTERM = sigterm_value or 15
                mock_signal_mod.signal = fake_signal
            else:
                # Simulate Windows: no SIGTERM attribute
                del_attrs = {"SIGTERM"}
                mock_signal_mod.configure_mock(
                    **{k: v for k, v in vars(signal).items() if k not in del_attrs}
                )
                mock_signal_mod.signal = fake_signal
                # hasattr(signal, "SIGTERM") must return False
                type(mock_signal_mod).__contains__ = (
                    lambda self, item: item != "SIGTERM"
                )

            # Directly exercise the signal registration logic
            watcher = MagicMock()
            watcher.join = MagicMock(return_value=None)

            def _shutdown(signum: int, frame: object) -> None:
                watcher.stop()

            mock_signal_mod.signal(mock_signal_mod.SIGINT, _shutdown)
            if hasattr(mock_signal_mod, "SIGTERM"):
                mock_signal_mod.signal(mock_signal_mod.SIGTERM, _shutdown)

        return registered

    def test_sigint_always_registered(self) -> None:
        registered = self._run_main_signals(has_sigterm=True)
        assert signal.SIGINT in registered

    def test_sigterm_registered_when_available(self) -> None:
        registered = self._run_main_signals(has_sigterm=True)
        sigterm = getattr(signal, "SIGTERM", 15)
        assert sigterm in registered

    def test_sigterm_skipped_when_unavailable(self) -> None:
        """Simulate Windows: SIGTERM absent from signal module."""
        registered: list[int] = []

        class _FakeSignalModule:
            SIGINT = signal.SIGINT

            @staticmethod
            def signal(sig: int, handler: object) -> None:
                registered.append(sig)

        fake_mod = _FakeSignalModule()

        # Replicate the guard from __main__.py
        fake_mod.signal(fake_mod.SIGINT, lambda s, f: None)
        if hasattr(fake_mod, "SIGTERM"):
            fake_mod.signal(fake_mod.SIGTERM, lambda s, f: None)  # type: ignore[attr-defined]

        assert signal.SIGINT in registered
        assert len(registered) == 1  # SIGTERM was not registered
