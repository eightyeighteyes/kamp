"""Tests for kamp_daemon.__main__ helpers."""

import logging
from pathlib import Path
from unittest.mock import patch

from kamp_core.library import LibraryIndex
from kamp_daemon.__main__ import _build_config_values
from kamp_daemon.config import _CONFIG_KEY_TYPES, Config


class TestBuildConfigValues:
    """The startup config snapshot GET /api/v1/config serves (KAMP-576).

    Built by a testable function precisely because the previous inline dict
    silently drifted from the settable-key allowlist and dropped
    artwork.save_format, so the toggle never survived a restart.
    """

    def _config(self, tmp_path: Path, **settings: str) -> Config:
        db = LibraryIndex(tmp_path / "library.db")
        Config.write_defaults(db)
        for key, value in settings.items():
            db.set_setting(key, value)
        cfg = Config.load(db)
        db.close()
        return cfg

    def test_includes_save_format_reflecting_stored_value(self, tmp_path: Path) -> None:
        cfg = self._config(tmp_path, **{"artwork.save_format": "cover-file"})
        snapshot = _build_config_values(cfg, bc_session=None, bc_ever_connected=False)
        assert snapshot["artwork.save_format"] == "cover-file"

    def test_includes_bandcamp_genres_reflecting_stored_value(
        self, tmp_path: Path
    ) -> None:
        cfg = self._config(tmp_path, **{"tagging.bandcamp_genres": "false"})
        snapshot = _build_config_values(cfg, bc_session=None, bc_ever_connected=False)
        assert snapshot["tagging.bandcamp_genres"] is False

    def test_every_settable_non_ui_key_is_present(self, tmp_path: Path) -> None:
        """Drift guard: every settable key that isn't served by the separate
        /ui-state endpoint must appear in the snapshot, or its preference
        cannot survive a restart (this is the whole KAMP-576 bug class)."""
        cfg = self._config(tmp_path)
        snapshot = _build_config_values(cfg, bc_session=None, bc_ever_connected=False)
        for key in _CONFIG_KEY_TYPES:
            if key.startswith("ui."):
                continue
            assert key in snapshot, f"{key} missing from config snapshot"

    def test_reflects_bandcamp_session_presence(self, tmp_path: Path) -> None:
        cfg = self._config(tmp_path)
        snapshot = _build_config_values(
            cfg, bc_session={"username": "alice"}, bc_ever_connected=True
        )
        assert snapshot["bandcamp.connected"] is True
        assert snapshot["bandcamp.username"] == "alice"
        assert snapshot["bandcamp.ever_connected"] is True


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
