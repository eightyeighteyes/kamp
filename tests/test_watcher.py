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
        musicbrainz=MusicBrainzConfig(contact="test@example.com"),
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

    def test_audio_file_moved_into_staging_is_scheduled(self, config: Config) -> None:
        """Dragging a single audio file into staging fires a moved event; it must be scheduled."""
        handler = _make_handler(config)
        dest = str(config.paths.staging / "track.mp3")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_moved(_file_moved_event("/tmp/track.mp3", dest))

        mock_schedule.assert_called_once_with(Path(dest))

    def test_non_audio_non_zip_file_moved_in_is_ignored(self, config: Config) -> None:
        handler = _make_handler(config)
        dest = str(config.paths.staging / "cover.jpg")

        with patch.object(handler, "_schedule") as mock_schedule:
            handler.on_moved(_file_moved_event("/tmp/cover.jpg", dest))

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

    def test_existing_audio_file_is_scheduled_on_start(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lone audio file already in staging when the daemon starts gets scheduled."""
        config.paths.staging.mkdir(parents=True, exist_ok=True)
        (config.paths.staging / "track.mp3").write_bytes(b"fake mp3")

        scheduled: list[Path] = []
        monkeypatch.setattr(
            _StagingHandler, "_schedule", lambda self, p: scheduled.append(p)
        )

        watcher = Watcher(config)
        monkeypatch.setattr(watcher._observer, "start", lambda: None)
        monkeypatch.setattr(watcher._observer, "schedule", lambda *a, **kw: None)
        watcher.start()

        assert any(p.name == "track.mp3" for p in scheduled)

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
        monkeypatch.setattr(
            "tune_shifter.watcher.run_in_subprocess", lambda path, cfg, **kw: None
        )
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

        monkeypatch.setattr("tune_shifter.watcher.run_in_subprocess", _boom)
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

    def test_audio_file_created_in_staging_is_scheduled(self, config: Config) -> None:
        """A single audio file created in staging is scheduled for processing."""
        handler = _make_handler(config)
        path = str(config.paths.staging / "track.mp3")

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


class TestDuplicateRunPrevention:
    """The pipeline notifies the watcher of the extracted directory so that any
    pending debounce timer for it is cancelled before Run 2 can start."""

    def test_process_passes_claim_callback_to_run(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_process passes a _on_directory callback to run(); verifies callback arg."""
        handler = _make_handler(config)
        album = config.paths.staging / "my-album"
        album.mkdir()

        received_callbacks: list[object] = []

        def _fake_run(
            path: Path, cfg: object, _on_directory: object = None, **kw: object
        ) -> None:
            received_callbacks.append(_on_directory)

        monkeypatch.setattr("tune_shifter.watcher.run_in_subprocess", _fake_run)
        handler._process(album)

        assert len(received_callbacks) == 1
        assert callable(received_callbacks[0])

    def test_claim_callback_cancels_pending_dir_timer(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the pipeline calls the callback, any pending timer for the
        extracted directory is cancelled and the directory is added to _in_flight."""
        handler = _make_handler(config)
        zip_path = config.paths.staging / "album.zip"
        zip_path.write_bytes(b"PK")

        extracted_dir = config.paths.staging / "album"
        extracted_dir.mkdir()

        # Simulate a pending timer for the extracted directory (as the watcher
        # would create when on_created fires for the new directory).
        pending_timer = MagicMock()
        handler._pending[extracted_dir] = pending_timer

        def _fake_run(
            path: Path, cfg: object, _on_directory: object = None, **kw: object
        ) -> None:
            # The pipeline calls the callback right after extraction
            if callable(_on_directory):
                _on_directory(extracted_dir)

        monkeypatch.setattr("tune_shifter.watcher.run_in_subprocess", _fake_run)
        handler._process(zip_path)

        # The pending timer for the extracted directory must have been cancelled
        pending_timer.cancel.assert_called_once()
        # The extracted directory must NOT be in _in_flight after _process finishes
        assert extracted_dir not in handler._in_flight

    def test_claim_callback_blocks_in_flight_scheduling(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A directory added to _in_flight by the callback cannot be rescheduled."""
        handler = _make_handler(config)
        zip_path = config.paths.staging / "album.zip"
        zip_path.write_bytes(b"PK")

        extracted_dir = config.paths.staging / "album"
        extracted_dir.mkdir()

        def _fake_run(path: Path, cfg: object, _on_directory: object = None) -> None:
            if callable(_on_directory):
                _on_directory(extracted_dir)
            # Simulate the pipeline still running — try to schedule the directory
            handler._schedule(extracted_dir)

        monkeypatch.setattr("tune_shifter.watcher.run_in_subprocess", _fake_run)
        handler._process(zip_path)

        # _schedule while in-flight must have been a no-op
        assert extracted_dir not in handler._pending


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


class TestWatcherPauseResume:
    def _patched_watcher(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> Watcher:
        watcher = Watcher(config)
        monkeypatch.setattr(watcher._observer, "start", lambda: None)
        monkeypatch.setattr(watcher._observer, "schedule", lambda *a, **kw: None)
        monkeypatch.setattr(watcher._observer, "stop", lambda: None)
        monkeypatch.setattr(watcher._observer, "join", lambda **kw: None)
        watcher.start()
        return watcher

    def test_pause_cancels_pending_timers(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pause() cancels any in-flight debounce timers."""
        watcher = self._patched_watcher(config, monkeypatch)
        timer = MagicMock()
        watcher._handler._pending[config.paths.staging / "album"] = timer

        watcher.pause()

        timer.cancel.assert_called_once()

    def test_pause_stops_observer(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pause() stops the watchdog observer."""
        watcher = self._patched_watcher(config, monkeypatch)
        stop_calls: list[int] = []
        monkeypatch.setattr(watcher._observer, "stop", lambda: stop_calls.append(1))

        watcher.pause()

        assert stop_calls == [1]

    def test_pause_is_idempotent(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling pause() twice does not double-stop the observer."""
        watcher = self._patched_watcher(config, monkeypatch)
        stop_calls: list[int] = []
        monkeypatch.setattr(watcher._observer, "stop", lambda: stop_calls.append(1))

        watcher.pause()
        watcher.pause()

        assert stop_calls == [1]

    def test_resume_creates_new_observer_and_starts(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """resume() creates a fresh observer and starts watching again."""
        watcher = self._patched_watcher(config, monkeypatch)
        watcher.pause()

        start_calls: list[int] = []
        # Patch Observer class so the new instance is controllable
        mock_observer = MagicMock()
        mock_observer.start = lambda: start_calls.append(1)
        mock_observer.schedule = lambda *a, **kw: None
        monkeypatch.setattr("tune_shifter.watcher.Observer", lambda: mock_observer)

        watcher.resume()

        assert start_calls == [1]

    def test_resume_scans_staging_for_existing_items(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """resume() picks up items already in staging."""
        config.paths.staging.mkdir(parents=True, exist_ok=True)
        (config.paths.staging / "Artist - Album").mkdir()
        watcher = self._patched_watcher(config, monkeypatch)
        watcher.pause()

        scheduled: list[Path] = []
        monkeypatch.setattr(
            "tune_shifter.watcher.Observer",
            lambda: MagicMock(start=lambda: None, schedule=lambda *a, **kw: None),
        )
        monkeypatch.setattr(
            _StagingHandler, "_schedule", lambda self, p: scheduled.append(p)
        )

        watcher.resume()

        assert any(p.name == "Artist - Album" for p in scheduled)

    def test_resume_is_idempotent(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling resume() when not paused does nothing."""
        watcher = self._patched_watcher(config, monkeypatch)
        start_calls: list[int] = []
        monkeypatch.setattr(
            "tune_shifter.watcher.Observer",
            lambda: MagicMock(
                start=lambda: start_calls.append(1), schedule=lambda *a, **kw: None
            ),
        )

        watcher.resume()  # not paused — should be a no-op

        assert start_calls == []

    def test_stop_when_paused_is_safe(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stop() after pause() does not attempt to stop an already-stopped observer."""
        watcher = self._patched_watcher(config, monkeypatch)
        stop_calls: list[int] = []
        monkeypatch.setattr(watcher._observer, "stop", lambda: stop_calls.append(1))
        watcher.pause()
        stop_calls.clear()

        watcher.stop()  # must not raise or double-stop

        assert stop_calls == []


class TestWatcherReload:
    def _patched_watcher(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> Watcher:
        watcher = Watcher(config)
        monkeypatch.setattr(watcher._observer, "start", lambda: None)
        monkeypatch.setattr(watcher._observer, "schedule", lambda *a, **kw: None)
        monkeypatch.setattr(watcher._observer, "stop", lambda: None)
        monkeypatch.setattr(watcher._observer, "join", lambda **kw: None)
        watcher.start()
        return watcher

    def test_reload_updates_config_on_handler(
        self, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reload() propagates new config to the handler."""
        watcher = self._patched_watcher(config, monkeypatch)
        new_config = Config(
            paths=config.paths,
            musicbrainz=config.musicbrainz,
            artwork=ArtworkConfig(min_dimension=500, max_bytes=5_000_000),
            library=config.library,
        )
        watcher.reload(new_config)
        assert watcher._handler._config.artwork.min_dimension == 500

    def test_reload_same_path_does_not_reschedule(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reload() with the same staging path does not touch the observer."""
        watcher = self._patched_watcher(config, monkeypatch)
        reschedule_calls: list[int] = []
        monkeypatch.setattr(
            watcher._observer,
            "unschedule_all",
            lambda: reschedule_calls.append(1),
        )
        watcher.reload(config)
        assert reschedule_calls == []

    def test_reload_changed_path_reschedules_observer(
        self, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """reload() with a new staging path unschedules and reschedules the observer."""
        watcher = self._patched_watcher(config, monkeypatch)
        reschedule_calls: list[int] = []
        monkeypatch.setattr(
            watcher._observer,
            "unschedule_all",
            lambda: reschedule_calls.append(1),
        )
        new_staging = tmp_path / "new_staging"
        new_config = Config(
            paths=PathsConfig(staging=new_staging, library=config.paths.library),
            musicbrainz=config.musicbrainz,
            artwork=config.artwork,
            library=config.library,
        )
        watcher.reload(new_config)
        assert reschedule_calls == [1]
        assert watcher._config.paths.staging == new_staging


class TestStageCallback:
    def test_stage_callback_forwarded_to_run(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_process passes handler.stage_callback as stage_callback kwarg to run()."""
        handler = _make_handler(config)
        album = config.paths.staging / "my-album"
        album.mkdir()

        received: dict[str, object] = {}

        def _fake_run(
            path: object,
            cfg: object,
            _on_directory: object = None,
            stage_callback: object = None,
            **kw: object,
        ) -> None:
            received["stage_callback"] = stage_callback

        cb = lambda stage: None  # noqa: E731
        handler.stage_callback = cb
        monkeypatch.setattr("tune_shifter.watcher.run_in_subprocess", _fake_run)
        handler._process(album)

        assert received["stage_callback"] is cb

    def test_watcher_stage_callback_setter_propagates_to_handler(
        self, config: Config
    ) -> None:
        """Setting Watcher.stage_callback updates the live handler."""
        watcher = Watcher(config)
        cb = lambda stage: None  # noqa: E731
        watcher.stage_callback = cb
        assert watcher._handler.stage_callback is cb
