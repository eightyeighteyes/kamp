"""Subprocess worker for extension method invocations.

Each call to invoke_extension() runs the extension method inside a fresh
spawn-context subprocess. A crash (unhandled exception or segfault) is caught
here: the failure is logged and False is returned — the daemon never raises and
continues processing other items.

The pattern mirrors pipeline.py; see that module for the rationale behind the
spawn context and the QueueHandler cleanup in the worker's finally block.
"""

from __future__ import annotations

import logging
import logging.handlers
import multiprocessing
import queue
from typing import Any

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker entry point (runs inside the subprocess)
# ---------------------------------------------------------------------------


def _extension_worker(
    cls: type,
    method_name: str,
    args: tuple[Any, ...],
    log_q: Any,
    result_q: Any,
) -> None:
    """Subprocess entry point: instantiate *cls* and call *method_name*(*args).

    Logging is forwarded to the parent via log_q.  The QueueHandler is removed
    in the finally block so that _replay_log_queue re-emission in tests does not
    loop back through the root logger's handler (same invariant as pipeline.py).
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    queue_handler = logging.handlers.QueueHandler(log_q)
    root.addHandler(queue_handler)
    try:
        instance = cls()
        getattr(instance, method_name)(*args)
        result_q.put(("ok", None))
    except Exception as exc:  # noqa: BLE001
        result_q.put(("error", str(exc)))
    finally:
        root.removeHandler(queue_handler)


# ---------------------------------------------------------------------------
# Spawn helper (pragma: no cover — real subprocess, not run in unit tests)
# ---------------------------------------------------------------------------


def _spawn_extension_worker(
    cls: type,
    method_name: str,
    args: tuple[Any, ...],
) -> tuple[Any, Any, Any]:
    """Spawn an isolated subprocess running _extension_worker.

    Uses 'spawn' so the child starts with a clean interpreter.
    Returns (proc, log_q, result_q).
    """  # pragma: no cover
    ctx = multiprocessing.get_context("spawn")
    log_q: Any = ctx.Queue()
    result_q: Any = ctx.Queue()
    proc = ctx.Process(
        target=_extension_worker,
        args=(cls, method_name, args, log_q, result_q),
    )
    proc.start()
    return proc, log_q, result_q


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def invoke_extension(cls: type, method_name: str, *args: Any) -> bool:
    """Invoke cls().method_name(*args) in an isolated subprocess.

    Returns True on success, False on any failure (exception or crash).  Never
    raises — callers can unconditionally continue after a False return.
    """
    proc, log_q, result_q = _spawn_extension_worker(cls, method_name, args)

    # Drain log_q while the subprocess runs to prevent the OS pipe from
    # filling and blocking the child (same pattern as pipeline.py).
    while proc.is_alive():  # pragma: no cover
        _drain_log_queue(log_q)

    proc.join()
    _drain_log_queue(log_q)

    # Non-zero exit code indicates a hard crash (e.g. segfault) — the worker
    # never got to put a result on result_q.
    if proc.exitcode != 0:
        _logger.error(
            "Extension worker for %s.%s exited with code %d (crash)",
            cls.__qualname__,
            method_name,
            proc.exitcode,
        )
        return False

    try:
        status, value = result_q.get_nowait()
    except queue.Empty:  # pragma: no cover
        _logger.error(
            "Extension worker for %s.%s exited without a result",
            cls.__qualname__,
            method_name,
        )
        return False

    if status == "error":
        _logger.error(
            "Extension %s.%s failed: %s",
            cls.__qualname__,
            method_name,
            value,
        )
        return False

    return True


def _drain_log_queue(log_q: Any) -> None:
    """Re-emit log records from the subprocess into the parent's log handlers."""
    while True:
        try:
            record = log_q.get_nowait()
            logging.getLogger(record.name).handle(record)
        except queue.Empty:
            break
