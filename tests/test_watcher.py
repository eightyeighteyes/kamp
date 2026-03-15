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
from tune_shifter.watcher import _StagingHandler, Watcher


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


def _file_created_event(path: str) -> MagicMock:
    event = MagicMock(spec=["is_directory", "src_path"])
    event.is_directory = False
    event.src_path = path
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


class TestStartupScan:
    def test_existing_directory_is_scheduled_on_start(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A directory already in staging when the daemon starts gets scheduled."""
        config.paths.staging.mkdir(parents=True, exist_ok=True)
        (config.paths.staging / "Artist - Album").mkdir()

        scheduled: list[Path] = []
        monkeypatch.setattr(
            _StagingHandler, "_schedule", lambda self, p: scheduled.append(p)
        )

        watcher = Watcher(config)
        monkeypatch.setattr(watcher._observer, "start", lambda: None)
        monkeypatch.setattr(watcher._observer, "schedule", lambda *a, **kw: None)
        watcher.start()

        assert any(p.name == "Artist - Album" for p in scheduled)

    def test_existing_zip_is_scheduled_on_start(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ZIP already in staging when the daemon starts gets scheduled."""
        config.paths.staging.mkdir(parents=True, exist_ok=True)
        (config.paths.staging / "album.zip").write_bytes(b"PK")

        scheduled: list[Path] = []
        monkeypatch.setattr(
            _StagingHandler, "_schedule", lambda self, p: scheduled.append(p)
        )

        watcher = Watcher(config)
        monkeypatch.setattr(watcher._observer, "start", lambda: None)
        monkeypatch.setattr(watcher._observer, "schedule", lambda *a, **kw: None)
        watcher.start()

        assert any(p.name == "album.zip" for p in scheduled)

    def test_errors_directory_is_not_scheduled_on_start(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The errors/ subdirectory is not scheduled on startup."""
        config.paths.staging.mkdir(parents=True, exist_ok=True)
        (config.paths.staging / "errors").mkdir()

        scheduled: list[Path] = []
        monkeypatch.setattr(
            _StagingHandler, "_schedule", lambda self, p: scheduled.append(p)
        )

        watcher = Watcher(config)
        monkeypatch.setattr(watcher._observer, "start", lambda: None)
        monkeypatch.setattr(watcher._observer, "schedule", lambda *a, **kw: None)
        watcher.start()

        assert not any(p.name == "errors" for p in scheduled)


class TestInFlight:
    def test_in_flight_path_is_not_rescheduled(self, config: Config) -> None:
        """_schedule is a no-op for a path currently being processed."""
        handler = _make_handler(config)
        path = config.paths.staging / "my-album"
        handler._in_flight.add(path)

        handler._schedule(path)

        assert path not in handler._pending

    def test_scan_staging_root_skips_in_flight(self, config: Config) -> None:
        """_scan_staging_root does not schedule a path that is currently in-flight."""
        handler = _make_handler(config)
        album = config.paths.staging / "my-album"
        album.mkdir()
        handler._in_flight.add(album)

        with patch.object(handler, "_schedule") as mock_schedule:
            handler._scan_staging_root()

        mock_schedule.assert_not_called()

    def test_in_flight_cleared_after_process(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After _process completes, the path is removed from _in_flight."""
        handler = _make_handler(config)
        album = config.paths.staging / "my-album"
        album.mkdir()
        monkeypatch.setattr("tune_shifter.watcher.run", lambda path, cfg: None)
        handler._process(album)

        assert album not in handler._in_flight

    def test_in_flight_cleared_after_process_error(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_in_flight is cleaned up even when run() raises."""
        handler = _make_handler(config)
        album = config.paths.staging / "my-album"
        album.mkdir()

        def _boom(path: Path, cfg: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr("tune_shifter.watcher.run", _boom)
        handler._process(album)  # exception is caught internally

        assert album not in handler._in_flight


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

    def test_zip_file_created_in_staging_is_scheduled(self, config: Config) -> None:
        """A ZIP file created in staging is scheduled for processing."""
        handler = _make_handler(config)
        path = str(config.paths.staging / "album.zip")

        with patch.object(handler, "_schedule") as mock_schedule:
            from watchdog.events import FileCreatedEvent

            handler.on_created(FileCreatedEvent(path))

        mock_schedule.assert_called_once_with(Path(path))

    def test_non_zip_file_created_is_ignored(self, config: Config) -> None:
        """A non-ZIP file created in staging is not scheduled."""
        handler = _make_handler(config)
        path = str(config.paths.staging / "cover.jpg")

        with patch.object(handler, "_schedule") as mock_schedule:
            from watchdog.events import FileCreatedEvent

            handler.on_created(FileCreatedEvent(path))

        mock_schedule.assert_not_called()


class TestOnModifiedFileEvent:
    def test_file_modified_event_is_ignored(self, config: Config) -> None:
        """on_modified ignores events for files (only directory events matter)."""
        handler = _make_handler(config)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(config.paths.staging / "track.mp3")

        with patch.object(handler, "_scan_staging_root") as mock_scan:
            handler.on_modified(event)

        mock_scan.assert_not_called()


class TestScanStagingRootOSError:
    def test_oserror_during_scan_is_silently_ignored(self, config: Config) -> None:
        """_scan_staging_root handles OSError from iterdir gracefully."""
        handler = _make_handler(config)

        # Patch at the class level — PosixPath C-methods can't be patched on instances
        with patch("pathlib.Path.iterdir", side_effect=OSError("no access")):
            handler._scan_staging_root()  # must not raise


class TestScheduleTimer:
    def test_schedule_adds_to_pending(self, config: Config) -> None:
        """_schedule creates a timer and adds it to _pending."""
        handler = _make_handler(config)
        path = config.paths.staging / "my-album"
        (config.paths.staging / "my-album").mkdir()

        handler._schedule(path)
        assert path in handler._pending
        handler._pending[path].cancel()

    def test_schedule_cancels_existing_timer(self, config: Config) -> None:
        """_schedule cancels any existing timer for the same path."""
        handler = _make_handler(config)
        path = config.paths.staging / "my-album"
        path.mkdir()

        old_timer = MagicMock()
        handler._pending[path] = old_timer

        handler._schedule(path)
        old_timer.cancel.assert_called_once()
        handler._pending[path].cancel()


class TestWaitForStableSize:
    def test_returns_when_size_stable(self, tmp_path: Path) -> None:
        """_wait_for_stable_size returns once the file size stops changing."""
        from tune_shifter.watcher import _wait_for_stable_size

        f = tmp_path / "track.mp3"
        f.write_bytes(b"x" * 100)

        with patch("tune_shifter.watcher.time.sleep"):
            _wait_for_stable_size(f)  # must not hang

    def test_returns_when_file_disappears(self, tmp_path: Path) -> None:
        """_wait_for_stable_size returns if the file is deleted mid-wait."""
        from tune_shifter.watcher import _wait_for_stable_size

        f = tmp_path / "track.mp3"
        f.write_bytes(b"x" * 100)

        call_count = 0

        def fake_stat(self_path: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise OSError("gone")
            m = MagicMock()
            m.st_size = 100
            return m

        # Patch at the class level — PosixPath C-methods can't be patched on instances
        with patch("pathlib.Path.stat", fake_stat):
            with patch("tune_shifter.watcher.time.sleep"):
                _wait_for_stable_size(f)


class TestWatcherStopJoin:
    def test_stop_and_join(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Watcher.stop() and join() delegate to the observer."""
        watcher = Watcher(config)
        monkeypatch.setattr(watcher._observer, "start", lambda: None)
        monkeypatch.setattr(watcher._observer, "schedule", lambda *a, **kw: None)
        monkeypatch.setattr(watcher._observer, "stop", lambda: None)
        monkeypatch.setattr(watcher._observer, "join", lambda **kw: None)

        watcher.start()
        watcher.stop()
        watcher.join()
