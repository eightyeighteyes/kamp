"""Tests for kamp_daemon.syncer."""

import logging.handlers
import queue as _queue_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kamp_daemon.config import (
    ArtworkConfig,
    BandcampConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from kamp_daemon.syncer import NeedsLoginError, Syncer, logout


def _make_config(tmp_path: Path, poll_interval: int = 0) -> Config:
    return Config(
        paths=PathsConfig(
            watch_folder=tmp_path / "watch", library=tmp_path / "library"
        ),
        musicbrainz=MusicBrainzConfig(),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
        bandcamp=BandcampConfig(
            username="user",
            cookie_file=None,
            format="mp3-v0",
            poll_interval_minutes=poll_interval,
        ),
    )


def _make_config_no_bandcamp(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            watch_folder=tmp_path / "watch", library=tmp_path / "library"
        ),
        musicbrainz=MusicBrainzConfig(),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
    )


class _FakeProc:
    """Fake subprocess returned by _inline_worker / _noop_worker."""

    exitcode = 0

    def join(self, timeout: object = None) -> None:
        pass

    def is_alive(self) -> bool:
        return False


def _inline_worker(target: Any, args: tuple[Any, ...]) -> tuple[Any, Any, Any, Any]:
    """Test helper: run the worker synchronously in-process.

    Patches on kamp_daemon.bandcamp.* work because the target code runs in
    the same process and the same sys.modules as the test.
    """
    status_q: _queue_module.Queue[str] = _queue_module.Queue()
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    target(*args, status_q, log_q, result_q)
    return _FakeProc(), status_q, log_q, result_q


def _noop_worker(target: Any, args: tuple[Any, ...]) -> tuple[Any, Any, Any, Any]:
    """Test helper: skip the worker entirely, return an empty-ok result.

    Use when you only need sync_once() / mark_synced() to complete without
    importing or running any bandcamp code (e.g. isolation assertions).
    """
    status_q: _queue_module.Queue[str] = _queue_module.Queue()
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q.put(("ok", []))
    return _FakeProc(), status_q, log_q, result_q


class TestStart:
    def test_noop_when_no_bandcamp(self, tmp_path: Path) -> None:
        """start() does nothing when there is no [bandcamp] config."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        syncer.start()
        assert syncer._thread is None

    def test_noop_when_interval_zero(self, tmp_path: Path) -> None:
        """start() does nothing when poll_interval_minutes is 0."""
        syncer = Syncer(_make_config(tmp_path, poll_interval=0))
        syncer.start()
        assert syncer._thread is None

    def test_launches_thread_when_interval_set(self, tmp_path: Path) -> None:
        """start() spawns a daemon thread when poll_interval_minutes > 0."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                assert syncer._thread is not None
                assert syncer._thread.is_alive()
                syncer.stop()


class TestStop:
    def test_stop_sets_event(self, tmp_path: Path) -> None:
        """stop() sets the stop event even when no thread was started."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        syncer.stop()
        assert syncer._stop_event.is_set()

    def test_stop_joins_thread(self, tmp_path: Path) -> None:
        """stop() waits for the polling thread to finish."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                syncer.stop()
                assert syncer._thread is not None
                assert not syncer._thread.is_alive()


class TestSyncOnce:
    def test_warns_when_no_bandcamp(self, tmp_path: Path) -> None:
        """sync_once() logs a warning and returns when [bandcamp] is absent."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        with patch("kamp_daemon.syncer._spawn_worker") as mock_spawn:
            syncer.sync_once()
        mock_spawn.assert_not_called()

    def test_logs_downloaded_count(self, tmp_path: Path) -> None:
        """sync_once() reports the number of downloaded files."""
        (tmp_path / "bandcamp_state.json").write_text("{}")
        fake_paths = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
        with patch("kamp_daemon.bandcamp.sync_new_purchases", return_value=fake_paths):
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    syncer.sync_once()

    def test_logs_nothing_new(self, tmp_path: Path) -> None:
        """sync_once() handles an empty result without error."""
        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch("kamp_daemon.bandcamp.sync_new_purchases", return_value=[]):
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    syncer.sync_once()


class TestReload:
    def test_reload_updates_config(self, tmp_path: Path) -> None:
        """reload() replaces the stored config."""
        syncer = Syncer(_make_config(tmp_path, poll_interval=0))
        new_config = _make_config(tmp_path, poll_interval=0)
        new_config.musicbrainz.trust_musicbrainz_when_tags_conflict = False
        syncer.reload(new_config)
        assert syncer._config.musicbrainz.trust_musicbrainz_when_tags_conflict is False

    def test_reload_changed_interval_no_existing_thread(self, tmp_path: Path) -> None:
        """reload() with interval change when no thread was running starts one."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                # interval=0 means no thread is started initially
                syncer = Syncer(_make_config(tmp_path, poll_interval=0))
                assert syncer._thread is None
                syncer.reload(_make_config(tmp_path, poll_interval=60))
                assert syncer._thread is not None
                assert syncer._thread.is_alive()
                syncer.stop()

    def test_reload_same_interval_does_not_restart_thread(self, tmp_path: Path) -> None:
        """reload() with unchanged poll_interval leaves the thread running."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                original_thread = syncer._thread
                syncer.reload(_make_config(tmp_path, poll_interval=60))
                assert syncer._thread is original_thread
                syncer.stop()

    def test_reload_changed_interval_restarts_thread(self, tmp_path: Path) -> None:
        """reload() with a new poll_interval stops and restarts the thread."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                original_thread = syncer._thread
                syncer.reload(_make_config(tmp_path, poll_interval=30))
                assert syncer._thread is not original_thread
                assert syncer._thread is not None
                assert syncer._thread.is_alive()
                syncer.stop()

    def test_reload_interval_to_zero_stops_thread(self, tmp_path: Path) -> None:
        """reload() with interval=0 stops the polling thread."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                assert syncer._thread is not None
                syncer.reload(_make_config(tmp_path, poll_interval=0))
                assert syncer._thread is None


class TestPauseResume:
    def test_pause_stops_polling_thread(self, tmp_path: Path) -> None:
        """pause() stops the polling thread."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                assert syncer._thread is not None and syncer._thread.is_alive()
                syncer.pause()
                assert syncer._thread is None or not syncer._thread.is_alive()

    def test_pause_when_no_thread_is_safe(self, tmp_path: Path) -> None:
        """pause() is safe when no polling thread was ever started."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        syncer.pause()  # must not raise

    def test_resume_restarts_polling_thread(self, tmp_path: Path) -> None:
        """resume() starts a new polling thread after a pause."""
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                syncer.pause()
                syncer.resume()
                assert syncer._thread is not None
                assert syncer._thread.is_alive()
                syncer.stop()

    def test_resume_noop_when_no_bandcamp(self, tmp_path: Path) -> None:
        """resume() is a no-op when there is no [bandcamp] config."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        syncer.pause()
        syncer.resume()
        assert syncer._thread is None


class TestStatusCallback:
    def test_status_callback_default_is_none(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config(tmp_path))
        assert syncer.status_callback is None

    def test_status_callback_receives_messages(self, tmp_path: Path) -> None:
        """Status messages put by the worker are forwarded to status_callback."""
        received: list[str] = []

        def _worker_with_two_messages(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            # Two messages to exercise the loop-continues branch in the drain loop.
            status_q.put("Downloading: Album A")
            status_q.put("Downloading: Album B")
            result_q.put(("ok", []))
            return _FakeProc(), status_q, log_q, result_q

        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch(
            "kamp_daemon.syncer._spawn_worker", side_effect=_worker_with_two_messages
        ):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.status_callback = received.append
                syncer.sync_once()

        # sync_once() prepends "Syncing…" to signal start, then forwards
        # per-item messages from the worker, then appends "" to signal completion.
        assert received == [
            "Syncing\u2026",
            "Downloading: Album A",
            "Downloading: Album B",
            "",
        ]


class TestLazyImport:
    def test_bandcamp_not_imported_at_construction(self, tmp_path: Path) -> None:
        """Constructing a Syncer must not load bandcamp (and by extension playwright)."""
        import sys

        sys.modules.pop("kamp_daemon.bandcamp", None)
        _ = Syncer(_make_config(tmp_path))
        assert "kamp_daemon.bandcamp" not in sys.modules

    def test_bandcamp_not_imported_in_parent_after_sync(self, tmp_path: Path) -> None:
        """sync_once() never imports bandcamp into the parent process.

        With subprocess isolation the bandcamp module lives only inside the
        child process; the parent's sys.modules stays clean throughout.
        """
        import sys

        (tmp_path / "bandcamp_state.json").write_text("{}")
        sys.modules.pop("kamp_daemon.bandcamp", None)
        syncer = Syncer(_make_config(tmp_path))
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer.sync_once()
        assert "kamp_daemon.bandcamp" not in sys.modules


class TestWorkerExceptions:
    def test_sync_worker_exception_propagates(self, tmp_path: Path) -> None:
        """Exceptions raised inside the sync worker are re-raised by sync_once()."""
        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch(
            "kamp_daemon.bandcamp.sync_new_purchases",
            side_effect=RuntimeError("network failure"),
        ):
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    with pytest.raises(RuntimeError, match="network failure"):
                        syncer.sync_once()

    def test_mark_synced_worker_exception_propagates(self, tmp_path: Path) -> None:
        """Exceptions raised inside the mark-synced worker are re-raised."""
        with patch(
            "kamp_daemon.bandcamp.mark_collection_synced",
            side_effect=RuntimeError("auth error"),
        ):
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    with pytest.raises(RuntimeError, match="auth error"):
                        syncer.mark_synced()

    def test_needs_login_error_propagates(self, tmp_path: Path) -> None:
        """NeedsLoginError raised in the worker is re-raised by sync_once()."""

        class _FakeNeedsLogin(Exception):
            pass

        _FakeNeedsLogin.__name__ = "NeedsLoginError"

        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch(
            "kamp_daemon.bandcamp.sync_new_purchases",
            side_effect=_FakeNeedsLogin("no session"),
        ):
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    with pytest.raises(NeedsLoginError, match="no session"):
                        syncer.sync_once()

    def test_needs_login_clears_status_callback(self, tmp_path: Path) -> None:
        """sync_once() clears the status display when NeedsLoginError is raised."""

        class _FakeNeedsLogin(Exception):
            pass

        _FakeNeedsLogin.__name__ = "NeedsLoginError"

        statuses: list[str] = []
        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch(
            "kamp_daemon.bandcamp.sync_new_purchases",
            side_effect=_FakeNeedsLogin("no session"),
        ):
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    syncer.status_callback = statuses.append
                    with pytest.raises(NeedsLoginError):
                        syncer.sync_once()
        # The final status update must clear the display (empty string).
        assert statuses[-1] == ""

    def test_run_stops_polling_on_needs_login(self, tmp_path: Path) -> None:
        """_run() stops the polling loop when NeedsLoginError is raised."""
        call_count = 0

        def _needs_login_worker(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            nonlocal call_count
            call_count += 1
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q.put(("needs_login", "no session"))
            return _FakeProc(), status_q, log_q, result_q

        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch(
            "kamp_daemon.syncer._spawn_worker",
            side_effect=_needs_login_worker,
        ):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                import time

                time.sleep(0.1)
                syncer.stop()
        # The loop must have broken after the first NeedsLoginError — not retried.
        assert call_count == 1

    def test_run_logs_exception_and_continues(self, tmp_path: Path) -> None:
        """_run() catches sync_once() failures and keeps polling."""
        call_count = 0

        def _failing_then_stopping_worker(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            nonlocal call_count
            call_count += 1
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q.put(("error", "boom"))
            return _FakeProc(), status_q, log_q, result_q

        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch(
            "kamp_daemon.syncer._spawn_worker",
            side_effect=_failing_then_stopping_worker,
        ):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                import time

                time.sleep(0.1)
                syncer.stop()
        # _run caught the exception and logged it rather than crashing the thread.
        assert call_count >= 1


class TestLogReplay:
    def test_log_records_forwarded_to_parent(self, tmp_path: Path) -> None:
        """Log records emitted in the subprocess are re-emitted in the parent."""
        import logging

        received: list[logging.LogRecord] = []

        def _worker_with_log(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            # Simulate a log record put by QueueHandler in the subprocess.
            record = logging.LogRecord(
                name="kamp_daemon.bandcamp",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="Fetched fan_id=12345",
                args=(),
                exc_info=None,
            )
            log_q.put(record)
            result_q.put(("ok", []))
            return _FakeProc(), status_q, log_q, result_q

        (tmp_path / "bandcamp_state.json").write_text("{}")
        handler = logging.handlers.MemoryHandler(
            capacity=100, flushLevel=logging.CRITICAL
        )
        logging.getLogger("kamp_daemon.bandcamp").addHandler(handler)
        logging.getLogger("kamp_daemon.bandcamp").setLevel(logging.DEBUG)
        try:
            with patch(
                "kamp_daemon.syncer._spawn_worker", side_effect=_worker_with_log
            ):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    syncer.sync_once()
        finally:
            logging.getLogger("kamp_daemon.bandcamp").removeHandler(handler)

        assert any(r.getMessage() == "Fetched fan_id=12345" for r in handler.buffer)


class TestAutoMarkOnFirstSync:
    def test_auto_marks_when_no_state_file(self, tmp_path: Path) -> None:
        """sync_once() calls mark_synced() first when the state file is absent."""
        call_order: list[str] = []

        def _recording_noop_worker(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            call_order.append(target.__name__)
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q.put(("ok", []))
            return _FakeProc(), status_q, log_q, result_q

        with patch(
            "kamp_daemon.syncer._spawn_worker", side_effect=_recording_noop_worker
        ):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.sync_once()

        # mark-synced worker runs first, then sync worker
        assert call_order == ["_mark_synced_worker", "_sync_worker"]

    def test_no_auto_mark_when_state_file_exists(self, tmp_path: Path) -> None:
        """sync_once() skips auto-mark when the state file already exists."""
        (tmp_path / "bandcamp_state.json").write_text("{}")
        call_order: list[str] = []

        def _recording_noop_worker(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            call_order.append(target.__name__)
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q.put(("ok", []))
            return _FakeProc(), status_q, log_q, result_q

        with patch(
            "kamp_daemon.syncer._spawn_worker", side_effect=_recording_noop_worker
        ):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.sync_once()

        assert call_order == ["_sync_worker"]

    def test_skip_auto_mark_bypasses_mark_synced(self, tmp_path: Path) -> None:
        """sync_once(skip_auto_mark=True) skips auto-mark even without a state file."""
        call_order: list[str] = []

        def _recording_noop_worker(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            call_order.append(target.__name__)
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q.put(("ok", []))
            return _FakeProc(), status_q, log_q, result_q

        with patch(
            "kamp_daemon.syncer._spawn_worker", side_effect=_recording_noop_worker
        ):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.sync_once(skip_auto_mark=True)

        assert call_order == ["_sync_worker"]


class TestMarkSynced:
    def test_warns_when_no_bandcamp(self, tmp_path: Path) -> None:
        """mark_synced() warns and returns when [bandcamp] is absent."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        with patch("kamp_daemon.syncer._spawn_worker") as mock_spawn:
            syncer.mark_synced()
        mock_spawn.assert_not_called()

    def test_calls_mark_collection_synced(self, tmp_path: Path) -> None:
        """mark_synced() delegates to mark_collection_synced with correct args."""
        with patch("kamp_daemon.bandcamp.mark_collection_synced") as mock_mark:
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    syncer.mark_synced()
        mock_mark.assert_called_once()


class TestLogout:
    def test_deletes_session_and_state_files(self, tmp_path: Path) -> None:
        """logout() removes both files when both exist."""
        (tmp_path / "bandcamp_session.json").write_text("{}")
        (tmp_path / "bandcamp_state.json").write_text("{}")
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()
        assert not (tmp_path / "bandcamp_session.json").exists()
        assert not (tmp_path / "bandcamp_state.json").exists()

    def test_noop_when_no_files(self, tmp_path: Path) -> None:
        """logout() does not raise when neither file exists."""
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()  # must not raise

    def test_deletes_only_existing_file(self, tmp_path: Path) -> None:
        """logout() removes whichever file(s) exist without erroring on absent ones."""
        (tmp_path / "bandcamp_session.json").write_text("{}")
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()
        assert not (tmp_path / "bandcamp_session.json").exists()
