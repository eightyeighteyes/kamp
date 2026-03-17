"""Tests for tune_shifter.config_monitor."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tune_shifter.config import DEFAULT_CONFIG_CONTENT, Config
from tune_shifter.config_monitor import ConfigMonitor, _ConfigFileHandler


def _make_event(path: str, is_directory: bool = False) -> MagicMock:
    event = MagicMock()
    event.src_path = path
    event.is_directory = is_directory
    return event


class TestConfigFileHandler:
    def test_on_modified_matching_path_calls_on_change(self, tmp_path: Path) -> None:
        """on_modified fires the callback when the config file is the target."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(DEFAULT_CONFIG_CONTENT)
        received: list[Config] = []
        handler = _ConfigFileHandler(config_path, received.append)
        handler.on_modified(_make_event(str(config_path)))
        assert len(received) == 1

    def test_on_created_matching_path_calls_on_change(self, tmp_path: Path) -> None:
        """on_created also triggers reload (atomic-save editors write a new file)."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(DEFAULT_CONFIG_CONTENT)
        received: list[Config] = []
        handler = _ConfigFileHandler(config_path, received.append)
        handler.on_created(_make_event(str(config_path)))
        assert len(received) == 1

    def test_on_modified_different_file_does_not_call_on_change(
        self, tmp_path: Path
    ) -> None:
        """on_modified for an unrelated file is ignored."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(DEFAULT_CONFIG_CONTENT)
        received: list[Config] = []
        handler = _ConfigFileHandler(config_path, received.append)
        handler.on_modified(_make_event(str(tmp_path / "other.toml")))
        assert received == []

    def test_on_modified_directory_event_is_ignored(self, tmp_path: Path) -> None:
        """Directory-level modification events are ignored."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(DEFAULT_CONFIG_CONTENT)
        received: list[Config] = []
        handler = _ConfigFileHandler(config_path, received.append)
        handler.on_modified(_make_event(str(config_path), is_directory=True))
        assert received == []

    def test_load_failure_does_not_call_on_change(self, tmp_path: Path) -> None:
        """If Config.load() raises, on_change is not called."""
        config_path = tmp_path / "config.toml"
        config_path.write_text("invalid toml [[[")
        received: list[Config] = []
        handler = _ConfigFileHandler(config_path, received.append)
        handler.on_modified(_make_event(str(config_path)))
        assert received == []

    def test_on_change_exception_is_caught(self, tmp_path: Path) -> None:
        """Exceptions raised by on_change are caught and logged, not propagated."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(DEFAULT_CONFIG_CONTENT)

        def _bad_callback(cfg: Config) -> None:
            raise RuntimeError("boom")

        handler = _ConfigFileHandler(config_path, _bad_callback)
        # Should not raise
        handler.on_modified(_make_event(str(config_path)))


class TestConfigMonitor:
    def test_start_schedules_observer(self, tmp_path: Path) -> None:
        """start() schedules the config file's parent directory with the observer."""
        config_path = tmp_path / "config.toml"
        monitor = ConfigMonitor(config_path, lambda cfg: None)
        with patch.object(monitor._observer, "start") as mock_start:
            with patch.object(monitor._observer, "schedule") as mock_schedule:
                monitor.start()
        mock_start.assert_called_once()
        _, call_kwargs = mock_schedule.call_args
        assert call_kwargs.get("recursive") is False
        assert str(config_path.parent) in mock_schedule.call_args[0]

    def test_stop_stops_and_joins_observer(self, tmp_path: Path) -> None:
        """stop() calls stop() and join() on the underlying observer."""
        config_path = tmp_path / "config.toml"
        monitor = ConfigMonitor(config_path, lambda cfg: None)
        with patch.object(monitor._observer, "stop") as mock_stop:
            with patch.object(monitor._observer, "join") as mock_join:
                monitor.stop()
        mock_stop.assert_called_once()
        mock_join.assert_called_once()
