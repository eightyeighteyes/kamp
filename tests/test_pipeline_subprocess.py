"""Tests for the pipeline subprocess isolation wrapper (pipeline.py)."""

from __future__ import annotations

import queue as _queue_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kamp_daemon.config import (
    ArtworkConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from kamp_daemon.pipeline import _DIR_SENTINEL, _handle_stage_msg, run_in_subprocess


def _make_config(tmp_path: Path) -> Config:
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


# ---------------------------------------------------------------------------
# Test helpers (mirror the pattern from test_syncer.py)
# ---------------------------------------------------------------------------


class _FakeProc:
    exitcode = 0

    def join(self, timeout: object = None) -> None:
        pass

    def is_alive(self) -> bool:
        return False


def _inline_worker(target: Any, args: tuple[Any, ...]) -> tuple[Any, Any, Any, Any]:
    """Run the worker synchronously in-process so pipeline_impl patches apply."""
    stage_q: _queue_module.Queue[str] = _queue_module.Queue()
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    target(*args, stage_q, log_q, result_q)
    return _FakeProc(), stage_q, log_q, result_q


def _noop_worker(target: Any, args: tuple[Any, ...]) -> tuple[Any, Any, Any, Any]:
    """Skip the worker entirely; return an empty-ok result."""
    stage_q: _queue_module.Queue[str] = _queue_module.Queue()
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q.put(("ok", None))
    return _FakeProc(), stage_q, log_q, result_q


# ---------------------------------------------------------------------------
# Property test: heavy deps must never enter the parent process
# ---------------------------------------------------------------------------


class TestLazyImport:
    _HEAVY = ["mutagen", "musicbrainzngs", "PIL", "requests"]

    def test_importing_pipeline_does_not_load_heavy_deps(self) -> None:
        """Importing pipeline.py must not load mutagen, musicbrainzngs, PIL, requests.

        This is the key isolation property: watcher.py imports pipeline.py at
        module level, so any top-level heavy import in pipeline.py would defeat
        the subprocess isolation strategy entirely.
        """
        import importlib
        import sys

        # Save and restore so other tests' module references remain valid.
        saved = {mod: sys.modules[mod] for mod in self._HEAVY if mod in sys.modules}
        for mod in self._HEAVY:
            sys.modules.pop(mod, None)
        try:
            import kamp_daemon.pipeline

            importlib.reload(kamp_daemon.pipeline)

            for mod in self._HEAVY:
                assert (
                    mod not in sys.modules
                ), f"{mod} was imported by pipeline.py — it must be deferred to the subprocess"
        finally:
            sys.modules.update(saved)

    def test_run_in_subprocess_does_not_load_heavy_deps(self, tmp_path: Path) -> None:
        """run_in_subprocess() must not import heavy deps into the parent process.

        This is the runtime property: even when run_in_subprocess() executes,
        the parent's sys.modules must stay free of pipeline heavy dependencies.
        """
        import sys

        # Save and restore so other tests' module references remain valid.
        saved = {mod: sys.modules[mod] for mod in self._HEAVY if mod in sys.modules}
        for mod in self._HEAVY:
            sys.modules.pop(mod, None)
        try:
            with patch("kamp_daemon.pipeline._spawn_worker", side_effect=_noop_worker):
                run_in_subprocess(tmp_path / "album", _make_config(tmp_path))

            for mod in self._HEAVY:
                assert (
                    mod not in sys.modules
                ), f"{mod} was imported into the parent by run_in_subprocess()"
        finally:
            sys.modules.update(saved)


# ---------------------------------------------------------------------------
# Functional tests for the subprocess wrapper
# ---------------------------------------------------------------------------


class TestRunInSubprocess:
    def test_success_completes_without_error(self, tmp_path: Path) -> None:
        """run_in_subprocess() completes normally when the worker succeeds."""
        with patch("kamp_daemon.pipeline_impl.run") as mock_run:
            with patch(
                "kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker
            ):
                run_in_subprocess(tmp_path / "album", _make_config(tmp_path))
        mock_run.assert_called_once()

    def test_worker_calls_set_useragent(self, tmp_path: Path) -> None:
        """_pipeline_worker calls musicbrainzngs.set_useragent with the hardcoded contact."""
        with (
            patch("kamp_daemon.pipeline_impl.run"),
            patch("musicbrainzngs.set_useragent") as mock_ua,
            patch("kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker),
        ):
            run_in_subprocess(tmp_path / "album", _make_config(tmp_path))

        mock_ua.assert_called_once()
        _, _, contact = mock_ua.call_args.args
        assert contact == "tedd.e.terry+kamp@gmail.com"

    def test_worker_exception_propagates(self, tmp_path: Path) -> None:
        """Exceptions raised in the worker are re-raised by run_in_subprocess()."""
        with patch(
            "kamp_daemon.pipeline_impl.run",
            side_effect=RuntimeError("tagging failed"),
        ):
            with patch(
                "kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker
            ):
                with pytest.raises(RuntimeError, match="tagging failed"):
                    run_in_subprocess(tmp_path / "album", _make_config(tmp_path))

    def test_stage_callback_receives_stage_messages(self, tmp_path: Path) -> None:
        """Stage labels emitted by the worker are forwarded to stage_callback."""
        received: list[str] = []

        def _worker_with_stages(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            stage_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            stage_q.put("Extracting")
            stage_q.put("Tagging")
            result_q.put(("ok", None))
            return _FakeProc(), stage_q, log_q, result_q

        with patch(
            "kamp_daemon.pipeline._spawn_worker", side_effect=_worker_with_stages
        ):
            run_in_subprocess(
                tmp_path / "album",
                _make_config(tmp_path),
                stage_callback=received.append,
            )

        assert received == ["Extracting", "Tagging"]

    def test_log_records_replayed_in_parent(self, tmp_path: Path) -> None:
        """Log records emitted in the subprocess are re-emitted in the parent."""
        import logging
        import logging.handlers

        records: list[logging.LogRecord] = []

        def _worker_with_log(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            stage_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            rec = logging.LogRecord(
                name="kamp_daemon.pipeline_impl",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="Pipeline started for album",
                args=(),
                exc_info=None,
            )
            log_q.put(rec)
            result_q.put(("ok", None))
            return _FakeProc(), stage_q, log_q, result_q

        handler = logging.handlers.MemoryHandler(
            capacity=100, flushLevel=logging.CRITICAL
        )
        target_logger = logging.getLogger("kamp_daemon.pipeline_impl")
        target_logger.addHandler(handler)
        target_logger.setLevel(logging.DEBUG)
        try:
            with patch(
                "kamp_daemon.pipeline._spawn_worker", side_effect=_worker_with_log
            ):
                run_in_subprocess(tmp_path / "album", _make_config(tmp_path))
        finally:
            target_logger.removeHandler(handler)

        assert any(
            r.getMessage() == "Pipeline started for album" for r in handler.buffer
        )


# ---------------------------------------------------------------------------
# _on_directory sentinel forwarding
# ---------------------------------------------------------------------------


class TestOnDirectorySentinel:
    def test_directory_sentinel_calls_on_directory(self, tmp_path: Path) -> None:
        """A __dir__: sentinel in stage_q triggers the _on_directory callback."""
        extracted = tmp_path / "album"
        called_with: list[Path] = []

        def _worker_sends_sentinel(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            stage_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            stage_q.put(f"{_DIR_SENTINEL}{extracted}")
            result_q.put(("ok", None))
            return _FakeProc(), stage_q, log_q, result_q

        with patch(
            "kamp_daemon.pipeline._spawn_worker", side_effect=_worker_sends_sentinel
        ):
            run_in_subprocess(
                tmp_path / "album.zip",
                _make_config(tmp_path),
                _on_directory=called_with.append,
            )

        assert called_with == [extracted]

    def test_sentinel_not_forwarded_to_stage_callback(self, tmp_path: Path) -> None:
        """The __dir__: sentinel must not reach the stage_callback."""
        stage_received: list[str] = []
        extracted = tmp_path / "album"

        def _worker_sends_sentinel(
            target: Any, args: tuple[Any, ...]
        ) -> tuple[Any, Any, Any, Any]:
            stage_q: _queue_module.Queue[str] = _queue_module.Queue()
            log_q: _queue_module.Queue[Any] = _queue_module.Queue()
            result_q: _queue_module.Queue[Any] = _queue_module.Queue()
            stage_q.put(f"{_DIR_SENTINEL}{extracted}")
            stage_q.put("Tagging")
            result_q.put(("ok", None))
            return _FakeProc(), stage_q, log_q, result_q

        with patch(
            "kamp_daemon.pipeline._spawn_worker", side_effect=_worker_sends_sentinel
        ):
            run_in_subprocess(
                tmp_path / "album.zip",
                _make_config(tmp_path),
                stage_callback=stage_received.append,
            )

        assert stage_received == ["Tagging"]
        assert not any(s.startswith(_DIR_SENTINEL) for s in stage_received)

    def test_pipeline_worker_sends_on_directory_via_sentinel(
        self, tmp_path: Path
    ) -> None:
        """_pipeline_worker converts _on_directory calls into __dir__: sentinels."""
        extracted = tmp_path / "album"
        sentinels: list[str] = []

        def _capture_stage(msg: str) -> None:
            sentinels.append(msg)

        # Run _pipeline_worker directly (in-process) with a mock pipeline that
        # immediately calls _on_directory.
        stage_q: _queue_module.Queue[str] = _queue_module.Queue()
        log_q: _queue_module.Queue[Any] = _queue_module.Queue()
        result_q: _queue_module.Queue[Any] = _queue_module.Queue()

        def _fake_run(
            path: Any, config: Any, _on_directory: Any = None, **kw: Any
        ) -> None:
            if _on_directory:
                _on_directory(extracted)

        with patch("kamp_daemon.pipeline_impl.run", side_effect=_fake_run):
            from kamp_daemon.pipeline import _pipeline_worker

            _pipeline_worker(
                tmp_path / "album.zip", _make_config(tmp_path), stage_q, log_q, result_q
            )

        sentinel_msgs = [m for m in list(stage_q.queue) if m.startswith(_DIR_SENTINEL)]
        assert any(str(extracted) in m for m in sentinel_msgs)


# ---------------------------------------------------------------------------
# _handle_stage_msg unit tests
# ---------------------------------------------------------------------------


class TestHandleStageMsg:
    def test_stage_label_calls_stage_callback(self) -> None:
        received: list[str] = []
        _handle_stage_msg("Tagging", received.append, None)
        assert received == ["Tagging"]

    def test_dir_sentinel_calls_on_directory(self, tmp_path: Path) -> None:
        called: list[Path] = []
        _handle_stage_msg(f"{_DIR_SENTINEL}{tmp_path}", None, called.append)
        assert called == [tmp_path]

    def test_dir_sentinel_not_forwarded_to_stage_callback(self, tmp_path: Path) -> None:
        received: list[str] = []
        _handle_stage_msg(f"{_DIR_SENTINEL}{tmp_path}", received.append, None)
        assert received == []

    def test_no_callbacks_set_is_safe(self, tmp_path: Path) -> None:
        _handle_stage_msg("Extracting", None, None)
        _handle_stage_msg(f"{_DIR_SENTINEL}{tmp_path}", None, None)
