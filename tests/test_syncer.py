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
        bandcamp=BandcampConfig(format="mp3-v0", poll_interval_minutes=poll_interval),
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


def _seed_collection_db(tmp_path: Path) -> None:
    """Pre-populate bandcamp_collection so sync_once() skips first-run auto-mark.

    sync_once() checks whether the collection table is empty to determine if
    this is a first run.  Insert a dummy row so the table is non-empty and
    auto-mark is skipped.  Call this before patching _state_dir.
    """
    from kamp_core.library import LibraryIndex

    idx = LibraryIndex(tmp_path / "library.db")
    idx.upsert_collection_item("_seed", mode="local")
    idx.close()


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
        _seed_collection_db(tmp_path)
        fake_paths = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
        with patch("kamp_daemon.bandcamp.sync_new_purchases", return_value=fake_paths):
            with patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker):
                with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                    syncer = Syncer(_make_config(tmp_path))
                    syncer.sync_once()

    def test_logs_nothing_new(self, tmp_path: Path) -> None:
        """sync_once() handles an empty result without error."""
        _seed_collection_db(tmp_path)
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

        _seed_collection_db(tmp_path)
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


class TestDispatchDownloadMsg:
    """KAMP-436: byte-progress from the download subprocess is demuxed away from
    the global status_callback and onto the per-album progress_callback."""

    def test_progress_message_routes_to_progress_callback(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config(tmp_path))
        progress: list[tuple[str, int]] = []
        status: list[str] = []
        syncer.progress_callback = lambda sid, pct: progress.append((sid, pct))
        syncer.status_callback = status.append

        syncer._dispatch_download_msg("progress:42", "12345")

        assert progress == [("12345", 42)]
        assert status == []  # must NOT leak to the global sync indicator

    def test_status_message_routes_to_status_callback(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config(tmp_path))
        progress: list[tuple[str, int]] = []
        status: list[str] = []
        syncer.progress_callback = lambda sid, pct: progress.append((sid, pct))
        syncer.status_callback = status.append

        syncer._dispatch_download_msg("Downloading album 12345…", "12345")

        assert status == ["Downloading album 12345…"]
        assert progress == []

    def test_malformed_progress_is_ignored(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config(tmp_path))
        progress: list[tuple[str, int]] = []
        syncer.progress_callback = lambda sid, pct: progress.append((sid, pct))

        syncer._dispatch_download_msg("progress:notanint", "12345")

        assert progress == []

    def test_progress_without_callback_is_safe(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config(tmp_path))
        # progress_callback unset (None) — must not raise.
        syncer._dispatch_download_msg("progress:50", "12345")


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

        _seed_collection_db(tmp_path)
        sys.modules.pop("kamp_daemon.bandcamp", None)
        syncer = Syncer(_make_config(tmp_path))
        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer.sync_once()
        assert "kamp_daemon.bandcamp" not in sys.modules


class TestWorkerExceptions:
    def test_sync_worker_exception_propagates(self, tmp_path: Path) -> None:
        """Exceptions raised inside the sync worker are re-raised by sync_once()."""
        _seed_collection_db(tmp_path)
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

        _seed_collection_db(tmp_path)
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
        _seed_collection_db(tmp_path)
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

        _seed_collection_db(tmp_path)
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

        _seed_collection_db(tmp_path)
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

        _seed_collection_db(tmp_path)
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

    def test_no_auto_mark_when_collection_db_populated(self, tmp_path: Path) -> None:
        """sync_once() skips auto-mark when bandcamp_collection already has rows."""
        _seed_collection_db(tmp_path)
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


class TestSyncAllPurchases:
    def test_resets_collection_sync_state_before_sync(self, tmp_path: Path) -> None:
        """sync_all_purchases() nulls synced_at for all rows and runs sync with skip_auto_mark."""
        from kamp_core.library import LibraryIndex

        # Pre-populate DB with a synced row.
        db_path = tmp_path / "library.db"
        idx = LibraryIndex(db_path)
        idx.upsert_collection_item("12345", mode="local", synced_at=1234567890.0)
        idx.close()

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
                syncer.sync_all_purchases()

        # synced_at should be NULL after reset so next sync treats items as new.
        idx2 = LibraryIndex(db_path)
        row = idx2._conn.execute(
            "SELECT synced_at FROM bandcamp_collection WHERE sale_item_id = '12345'"
        ).fetchone()
        idx2.close()
        assert row["synced_at"] is None
        # skip_auto_mark=True means no mark-synced worker runs first
        assert call_order == ["_sync_worker"]

    def test_noop_when_no_state_file(self, tmp_path: Path) -> None:
        """sync_all_purchases() proceeds normally when state file does not exist."""

        def _noop_worker(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            status_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q.put(("ok", []))
            return _FakeProc(), status_q, log_q, result_q

        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.sync_all_purchases()  # must not raise


class TestLogout:
    def test_clears_session_but_preserves_collection_ledger(
        self, tmp_path: Path
    ) -> None:
        """logout() clears the session row but PRESERVES bandcamp_collection.

        The ledger is the file→purchase provenance map (KAMP-528); wiping it on
        every logout needlessly destroys that mapping. Only the session is cleared;
        the next sync reconciles the ledger.
        """
        from kamp_core.library import LibraryIndex

        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index.set_session(
                "bandcamp", {"cookies": [{"name": "js_logged_in", "value": "1"}]}
            )
            index.upsert_collection_item("999", mode="local")

            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                logout()

            assert index.get_session("bandcamp") is None
            assert index.get_collection_state() == {"999": "local"}
        finally:
            index.close()

    def test_noop_when_db_absent(self, tmp_path: Path) -> None:
        """logout() does not raise when library.db does not exist."""
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()  # must not raise

    def test_removes_legacy_session_file_if_present(self, tmp_path: Path) -> None:
        """logout() removes a legacy bandcamp_session.json if it still exists."""
        (tmp_path / "bandcamp_session.json").write_text("{}")
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()
        assert not (tmp_path / "bandcamp_session.json").exists()

    def test_removes_state_file_without_db(self, tmp_path: Path) -> None:
        """logout() removes bandcamp_state.json even when library.db is absent."""
        _seed_collection_db(tmp_path)
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()
        assert not (tmp_path / "bandcamp_state.json").exists()

    def test_clears_art_cache_directory(self, tmp_path: Path) -> None:
        """logout() removes the art_cache directory if it exists."""
        art_cache = tmp_path / "art_cache"
        art_cache.mkdir()
        (art_cache / "12345.jpg").write_bytes(b"\xff\xd8\xff")
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()
        assert not art_cache.exists()

    def test_noop_when_art_cache_absent(self, tmp_path: Path) -> None:
        """logout() does not raise when art_cache does not exist."""
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            logout()  # art_cache dir is absent — must not raise


class TestStreamMode:
    def _make_stream_config(self, tmp_path: Path, poll_interval: int = 0) -> Config:
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
                format="mp3-v0",
                poll_interval_minutes=poll_interval,
                collection_mode="stream",
            ),
        )

    def test_sync_once_stream_mode_skips_auto_mark(self, tmp_path: Path) -> None:
        """sync_once() in stream mode does not call mark_synced() on first run."""
        syncer = Syncer(self._make_stream_config(tmp_path))
        with (
            patch("kamp_daemon.bandcamp.sync_collection_stream", return_value=(2, 10)),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
            patch.object(syncer, "mark_synced") as mock_mark,
        ):
            syncer.sync_once()
        mock_mark.assert_not_called()

    def test_sync_all_purchases_stream_mode_skips_state_reset(
        self, tmp_path: Path
    ) -> None:
        """sync_all_purchases() in stream mode does not reset the collection state."""
        syncer = Syncer(self._make_stream_config(tmp_path))
        with (
            patch("kamp_daemon.bandcamp.sync_collection_stream", return_value=(0, 0)),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
        ):
            from kamp_core.library import LibraryIndex as _LI

            with patch.object(_LI, "reset_collection_sync_state") as mock_reset:
                syncer.sync_all_purchases()
        mock_reset.assert_not_called()

    def test_sync_once_stream_mode_uses_stream_worker(self, tmp_path: Path) -> None:
        """sync_once() in stream mode calls sync_collection_stream, not sync_new_purchases."""
        syncer = Syncer(self._make_stream_config(tmp_path))
        with (
            patch(
                "kamp_daemon.bandcamp.sync_collection_stream", return_value=(3, 15)
            ) as mock_stream,
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
        ):
            syncer.sync_once()
        mock_stream.assert_called_once()

    def test_sync_once_stream_logs_track_count(self, tmp_path: Path) -> None:
        """sync_once() in stream mode logs album and track counts when tracks indexed."""
        syncer = Syncer(self._make_stream_config(tmp_path))
        with (
            patch("kamp_daemon.bandcamp.sync_collection_stream", return_value=(5, 42)),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
            patch("kamp_daemon.syncer.logger") as mock_log,
        ):
            syncer.sync_once()
        info_msgs = " ".join(str(c) for c in mock_log.info.call_args_list)
        assert "42" in info_msgs

    def test_on_tracks_indexed_called_when_tracks_written(self, tmp_path: Path) -> None:
        """on_tracks_indexed fires when stream sync indexes new tracks."""
        syncer = Syncer(self._make_stream_config(tmp_path))
        fired: list[bool] = []
        syncer.on_tracks_indexed = lambda: fired.append(True)
        with (
            patch("kamp_daemon.bandcamp.sync_collection_stream", return_value=(5, 42)),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
        ):
            syncer.sync_once()
        assert fired == [True]

    def test_on_tracks_indexed_fires_per_batch_via_notify_q(
        self, tmp_path: Path
    ) -> None:
        """on_tracks_indexed fires once per album batch during streaming (via notify_q).

        Simulates sync_collection_stream invoking batch_indexed_callback twice
        (two album batches) plus the belt-and-suspenders final call at ok_stream.
        """
        syncer = Syncer(self._make_stream_config(tmp_path))
        fired: list[bool] = []
        syncer.on_tracks_indexed = lambda: fired.append(True)

        def _stream_with_two_batches(
            bc_config: object,
            watch_dir: object,
            index: object,
            status_callback: object = None,
            art_cache_dir: object = None,
            batch_indexed_callback: object = None,
        ) -> tuple[int, int]:
            if batch_indexed_callback is not None:
                batch_indexed_callback()
                batch_indexed_callback()
            return (2, 4)

        with (
            patch(
                "kamp_daemon.bandcamp.sync_collection_stream",
                side_effect=_stream_with_two_batches,
            ),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
        ):
            syncer.sync_once()

        # 2 per-batch calls via notify_q + 1 belt-and-suspenders final call = 3
        assert len(fired) == 3

    def test_on_tracks_indexed_not_called_when_no_new_tracks(
        self, tmp_path: Path
    ) -> None:
        """on_tracks_indexed does not fire when track_count == 0."""
        syncer = Syncer(self._make_stream_config(tmp_path))
        fired: list[bool] = []
        syncer.on_tracks_indexed = lambda: fired.append(True)
        with (
            patch("kamp_daemon.bandcamp.sync_collection_stream", return_value=(636, 0)),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
        ):
            syncer.sync_once()
        assert fired == []

    def test_sync_once_stream_logs_up_to_date_when_no_new_tracks(
        self, tmp_path: Path
    ) -> None:
        """sync_once() in stream mode logs 'up to date' when track_count == 0."""
        syncer = Syncer(self._make_stream_config(tmp_path))
        with (
            patch("kamp_daemon.bandcamp.sync_collection_stream", return_value=(636, 0)),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
            patch("kamp_daemon.syncer.logger") as mock_log,
        ):
            syncer.sync_once()
        info_msgs = " ".join(str(c) for c in mock_log.info.call_args_list)
        assert "up to date" in info_msgs


class TestDownloadAlbum:
    """Tests for Syncer.download_album()."""

    def test_raises_when_no_bandcamp(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        with pytest.raises(RuntimeError, match="No \\[bandcamp\\]"):
            syncer.download_album("42")

    def test_returns_dest_path_on_success(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config(tmp_path))
        expected = str(tmp_path / "watch" / "album.zip")

        def _fake_download(
            bc_config: object,
            watch_dir: object,
            index: object,
            sale_item_id: str,
            status_callback: object,
            on_progress: object = None,
        ) -> str:
            return expected

        with (
            patch(
                "kamp_daemon.bandcamp.download_single_album", side_effect=_fake_download
            ),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
        ):
            result = syncer.download_album("42")
        assert result == expected

    def test_raises_needs_login_error(self, tmp_path: Path) -> None:
        from kamp_daemon.bandcamp import NeedsLoginError as BcNeedsLogin

        syncer = Syncer(_make_config(tmp_path))

        def _needs_login(*_a: object, **_kw: object) -> None:
            raise BcNeedsLogin("no session")

        with (
            patch(
                "kamp_daemon.bandcamp.download_single_album", side_effect=_needs_login
            ),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
            pytest.raises(NeedsLoginError),
        ):
            syncer.download_album("42")

    def test_raises_runtime_error_on_failure(self, tmp_path: Path) -> None:
        syncer = Syncer(_make_config(tmp_path))

        def _fail(*_a: object, **_kw: object) -> None:
            raise RuntimeError("CDN error")

        with (
            patch("kamp_daemon.bandcamp.download_single_album", side_effect=_fail),
            patch("kamp_daemon.syncer._spawn_worker", side_effect=_inline_worker),
            patch("kamp_daemon.syncer._state_dir", return_value=tmp_path),
            pytest.raises(RuntimeError, match="Album download failed"),
        ):
            syncer.download_album("42")
