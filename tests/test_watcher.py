"""Tests for the filesystem watcher event handling."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tune_shifter.config import (
    ArtworkConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from tune_shifter.watcher import _StagingHandler


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            staging=tmp_path / "staging",
            library=tmp_path / "library",
        ),
        musicbrainz=MusicBrainzConfig(
            app_name="tune-shifter-test",
            app_version="0.0.1",
            contact="test@example.com",
        ),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=5_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
    )


def _make_handler(config: Config) -> _StagingHandler:
    config.paths.staging.mkdir(parents=True, exist_ok=True)
    return _StagingHandler(config)


def _dir_moved_event(src: str, dest: str) -> MagicMock:
    event = MagicMock()
    event.is_directory = True
    event.src_path = src
    event.dest_path = dest
    return event


def _file_moved_event(src: str, dest: str) -> MagicMock:
    event = MagicMock()
    event.is_directory = False
    event.src_path = src
    event.dest_path = dest
    return event


def _dir_created_event(path: str) -> MagicMock:
    event = MagicMock()
    event.is_directory = True
    event.src_path = path
    return event


class TestOnMoved:
    def test_directory_moved_into_staging_is_scheduled(self, config: Config) -> None:
        """Dragging a folder from Finder fires a moved event; it must be scheduled."""
        handler = _make_handler(config)
        dest = str(config.paths.staging / "my-album")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_moved(_dir_moved_event("/tmp/my-album", dest))

        mock_schedule.assert_called_once_with(Path(dest))

    def test_zip_moved_into_staging_is_scheduled(self, config: Config) -> None:
        handler = _make_handler(config)
        dest = str(config.paths.staging / "album.zip")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_moved(_file_moved_event("/tmp/album.zip", dest))

        mock_schedule.assert_called_once_with(Path(dest))

    def test_directory_moved_into_subdir_is_ignored(self, config: Config) -> None:
        """Items moved into a subdirectory of staging (not directly into root) are skipped."""
        handler = _make_handler(config)
        dest = str(config.paths.staging / "subdir" / "my-album")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_moved(_dir_moved_event("/tmp/my-album", dest))

        mock_schedule.assert_not_called()

    def test_errors_directory_moved_in_is_ignored(self, config: Config) -> None:
        handler = _make_handler(config)
        dest = str(config.paths.staging / "errors")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_moved(_dir_moved_event("/tmp/errors", dest))

        mock_schedule.assert_not_called()

    def test_non_zip_file_moved_in_is_ignored(self, config: Config) -> None:
        handler = _make_handler(config)
        dest = str(config.paths.staging / "track.mp3")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_moved(_file_moved_event("/tmp/track.mp3", dest))

        mock_schedule.assert_not_called()


class TestOnModified:
    def _dir_modified_event(self, path: str) -> MagicMock:
        event = MagicMock()
        event.is_directory = True
        event.src_path = path
        return event

    def test_staging_root_modified_schedules_new_directory(
        self, config: Config, tmp_path: Path
    ) -> None:
        """DirModifiedEvent on staging root (FSEvents rename coalescing) triggers schedule."""
        handler = _make_handler(config)
        album_dir = config.paths.staging / "my-album"
        album_dir.mkdir()

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_modified(self._dir_modified_event(str(config.paths.staging)))

        mock_schedule.assert_called_once_with(album_dir)

    def test_staging_root_modified_ignores_errors_dir(self, config: Config) -> None:
        handler = _make_handler(config)
        (config.paths.staging / "errors").mkdir()

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_modified(self._dir_modified_event(str(config.paths.staging)))

        mock_schedule.assert_not_called()

    def test_modified_event_on_subdirectory_is_ignored(self, config: Config) -> None:
        """Modification events inside staging subdirectories are not acted on."""
        handler = _make_handler(config)
        subdir = config.paths.staging / "subdir"
        subdir.mkdir()

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_modified(self._dir_modified_event(str(subdir)))

        mock_schedule.assert_not_called()

    def test_already_pending_items_not_rescheduled(self, config: Config) -> None:
        handler = _make_handler(config)
        album_dir = config.paths.staging / "my-album"
        album_dir.mkdir()

        # Simulate an already-pending path
        fake_timer = MagicMock()
        handler._pending[album_dir] = fake_timer

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_modified(self._dir_modified_event(str(config.paths.staging)))

        mock_schedule.assert_not_called()


class TestOnCreated:
    def test_directory_created_in_staging_is_scheduled(self, config: Config) -> None:
        handler = _make_handler(config)
        path = str(config.paths.staging / "my-album")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_created(_dir_created_event(path))

        mock_schedule.assert_called_once_with(Path(path))

    def test_errors_directory_created_is_ignored(self, config: Config) -> None:
        handler = _make_handler(config)
        path = str(config.paths.staging / "errors")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_created(_dir_created_event(path))

        mock_schedule.assert_not_called()
