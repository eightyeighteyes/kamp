"""Tests for kamp_daemon.__main__ helpers."""

import logging
from unittest.mock import patch


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
