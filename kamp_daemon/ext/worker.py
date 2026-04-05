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

from .context import KampGround

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker entry point (runs inside the subprocess)
# ---------------------------------------------------------------------------


def _extension_worker(
    cls: type,
    method_name: str,
    args: tuple[Any, ...],
    ctx: KampGround,
    log_q: Any,
    result_q: Any,
) -> None:
    """Subprocess entry point: instantiate cls(ctx) and call method_name(*args).

    The KampGround context is passed to the extension constructor so extensions
    can query library state and read playback state during their invocation.

    Logging is forwarded to the parent via log_q.  The QueueHandler is removed
    in the finally block so that _drain_log_queue re-emission in tests does not
    loop back through the root logger's handler (same invariant as pipeline.py).
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    queue_handler = logging.handlers.QueueHandler(log_q)
    root.addHandler(queue_handler)
    try:
        instance = cls(ctx)
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
    ctx: KampGround,
) -> tuple[Any, Any, Any]:
    """Spawn an isolated subprocess running _extension_worker.

    Uses 'spawn' so the child starts with a clean interpreter.
    Returns (proc, log_q, result_q).
    """  # pragma: no cover
    mp_ctx = multiprocessing.get_context("spawn")
    log_q: Any = mp_ctx.Queue()
    result_q: Any = mp_ctx.Queue()
    proc = mp_ctx.Process(
        target=_extension_worker,
        args=(cls, method_name, args, ctx, log_q, result_q),
    )
    proc.start()
    return proc, log_q, result_q


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def invoke_extension(
    cls: type,
    method_name: str,
    *args: Any,
    ctx: KampGround | None = None,
) -> bool:
    """Invoke cls(ctx).method_name(*args) in an isolated subprocess.

    Args:
        cls: Extension class to instantiate. Must accept a KampGround as its
            sole constructor argument.
        method_name: Name of the method to call on the instance.
        *args: Positional arguments forwarded to the method.
        ctx: KampGround context to pass to the extension constructor. A default
            empty context is used if omitted.

    Returns:
        True on success, False on any failure (exception or crash). Never
        raises — callers can unconditionally continue after a False return.
    """
    if ctx is None:
        ctx = KampGround()

    proc, log_q, result_q = _spawn_extension_worker(cls, method_name, args, ctx)

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
