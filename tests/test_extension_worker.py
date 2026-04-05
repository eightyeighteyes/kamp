"""Tests for the extension worker subprocess (worker.py)."""

from __future__ import annotations

import logging
import logging.handlers
import queue as _queue_module
from typing import Any
from unittest.mock import patch

from kamp_daemon.ext.worker import _extension_worker, _drain_log_queue, invoke_extension

# ---------------------------------------------------------------------------
# Concrete extension class used across tests
# ---------------------------------------------------------------------------


class _Recorder:
    """Records calls so tests can assert the method was invoked."""

    calls: list[tuple[Any, ...]] = []

    def run(self, *args: Any) -> None:
        _Recorder.calls.append(args)

    def explode(self, *args: Any) -> None:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# Inline worker helper (mirrors test_pipeline_subprocess.py pattern)
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, exitcode: int = 0) -> None:
        self.exitcode = exitcode

    def join(self, timeout: object = None) -> None:
        pass

    def is_alive(self) -> bool:
        return False


def _inline_worker(
    cls: type, method_name: str, args: tuple[Any, ...]
) -> tuple[Any, Any, Any]:
    """Run _extension_worker synchronously in-process."""
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    _extension_worker(cls, method_name, args, log_q, result_q)
    return _FakeProc(), log_q, result_q


def _crash_worker(
    cls: type, method_name: str, args: tuple[Any, ...]
) -> tuple[Any, Any, Any]:
    """Simulate a hard crash (non-zero exitcode, empty result_q)."""
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    return _FakeProc(exitcode=139), log_q, result_q  # 139 = SIGSEGV


# ---------------------------------------------------------------------------
# AC #1 — extension methods run inside a subprocess
# ---------------------------------------------------------------------------


def test_success_returns_true() -> None:
    _Recorder.calls = []
    with patch(
        "kamp_daemon.ext.worker._spawn_extension_worker", side_effect=_inline_worker
    ):
        result = invoke_extension(_Recorder, "run", "a", "b")
    assert result is True
    assert _Recorder.calls == [("a", "b")]


# ---------------------------------------------------------------------------
# AC #2 — crash / exception quarantines item, daemon continues
# ---------------------------------------------------------------------------


def test_exception_in_worker_returns_false(caplog: Any) -> None:
    with patch(
        "kamp_daemon.ext.worker._spawn_extension_worker", side_effect=_inline_worker
    ):
        with caplog.at_level("ERROR", logger="kamp_daemon.ext.worker"):
            result = invoke_extension(_Recorder, "explode")
    assert result is False
    assert "boom" in caplog.text


def test_exception_does_not_raise() -> None:
    """invoke_extension must never propagate the worker's exception."""
    with patch(
        "kamp_daemon.ext.worker._spawn_extension_worker", side_effect=_inline_worker
    ):
        result = invoke_extension(_Recorder, "explode")
    assert result is False  # no exception raised


def test_non_zero_exitcode_returns_false(caplog: Any) -> None:
    """A segfault (non-zero exitcode, empty result_q) is handled gracefully."""
    with patch(
        "kamp_daemon.ext.worker._spawn_extension_worker", side_effect=_crash_worker
    ):
        with caplog.at_level("ERROR", logger="kamp_daemon.ext.worker"):
            result = invoke_extension(_Recorder, "run")
    assert result is False
    assert "139" in caplog.text or "crash" in caplog.text.lower()


# ---------------------------------------------------------------------------
# AC #3 — worker cleans up QueueHandler; no re-emission loop
# ---------------------------------------------------------------------------


def test_queue_handler_removed_after_worker() -> None:
    """Root logger must not retain the QueueHandler after the worker exits."""
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    _extension_worker(_Recorder, "run", (), log_q, result_q)

    assert root.handlers == handlers_before


def test_no_reemission_loop_on_second_drain() -> None:
    """Draining log_q twice must not produce duplicate records.

    If the QueueHandler is left on the root logger, _drain_log_queue re-emits
    records via the logger hierarchy which loops back into the queue.  This test
    verifies the queue is empty after the first drain.
    """
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()

    # Worker emits one log record internally during instantiation/call
    _extension_worker(_Recorder, "run", ("x",), log_q, result_q)

    _drain_log_queue(log_q)  # first drain — clears the queue
    assert log_q.empty(), "Queue should be empty after first drain"


# ---------------------------------------------------------------------------
# AC #3 / AC #4 — log records replayed in parent; subprocess exits cleanly
# ---------------------------------------------------------------------------


def test_log_records_replayed_in_parent() -> None:
    """Log records emitted by the worker are forwarded to the parent's handlers."""

    class _LoggingExtension:
        def run(self) -> None:
            logging.getLogger("kamp_daemon.ext.worker").info("hello from worker")

    records: list[logging.LogRecord] = []
    handler = logging.handlers.MemoryHandler(capacity=100, flushLevel=logging.CRITICAL)
    target = logging.getLogger("kamp_daemon.ext.worker")
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        with patch(
            "kamp_daemon.ext.worker._spawn_extension_worker", side_effect=_inline_worker
        ):
            invoke_extension(_LoggingExtension, "run")
        records = list(handler.buffer)
    finally:
        target.removeHandler(handler)

    assert any("hello from worker" in r.getMessage() for r in records)
