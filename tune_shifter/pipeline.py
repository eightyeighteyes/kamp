"""Subprocess-isolated entry point for the ingest pipeline.

Imports of mutagen, musicbrainzngs, requests, and Pillow are deferred to
_pipeline_worker so that watcher.py can import this module without loading
any heavy dependencies into the parent process.  The actual pipeline logic
lives in pipeline_impl.py; this module is only the IPC wrapper.
"""

from __future__ import annotations

import logging
import logging.handlers
import multiprocessing
import queue
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)

# Prefix written to stage_q when the pipeline calls its _on_directory callback.
# Encodes the directory path so the parent can invoke _claim_directory without
# sharing mutable state across the process boundary.
_DIR_SENTINEL = "__dir__:"


def _pipeline_worker(
    path: Path,
    config: Config,
    stage_q: Any,
    log_q: Any,
    result_q: Any,
) -> None:
    """Entry point for the pipeline subprocess.

    All heavy imports (mutagen, musicbrainzngs, requests, Pillow) are deferred
    here — they are never loaded into the parent process.
    """
    _root = logging.getLogger()
    _root.setLevel(logging.DEBUG)
    _queue_handler = logging.handlers.QueueHandler(log_q)
    _root.addHandler(_queue_handler)
    try:
        import importlib.metadata

        import musicbrainzngs

        from .pipeline_impl import run

        # The subprocess starts with a clean interpreter — set_useragent must be
        # called here because the parent's call does not carry over across the
        # process boundary.
        musicbrainzngs.set_useragent(
            "tune-shifter",
            importlib.metadata.version("tune-shifter"),
            config.musicbrainz.contact,
        )

        run(
            path,
            config,
            _on_directory=lambda d: stage_q.put(f"{_DIR_SENTINEL}{d}"),
            stage_callback=lambda s: stage_q.put(s),
        )
        result_q.put(("ok", None))
    except Exception as exc:  # noqa: BLE001
        result_q.put(("error", str(exc)))
    finally:
        # Remove the handler so that _replay_log_queue re-emission does not
        # loop back through this queue (matters when running inline in tests).
        _root.removeHandler(_queue_handler)


def _spawn_worker(  # pragma: no cover
    target: Any,
    args: tuple[Any, ...],
) -> tuple[Any, Any, Any, Any]:
    """Spawn an isolated subprocess running target(*args, stage_q, log_q, result_q).

    Uses 'spawn' so the child starts with a clean interpreter — heavy pipeline
    dependencies are never inherited from the parent.

    Returns (proc, stage_q, log_q, result_q).
    """
    ctx = multiprocessing.get_context("spawn")
    stage_q: Any = ctx.Queue()
    log_q: Any = ctx.Queue()
    result_q: Any = ctx.Queue()
    proc = ctx.Process(target=target, args=(*args, stage_q, log_q, result_q))
    proc.start()
    return proc, stage_q, log_q, result_q


def _handle_stage_msg(
    msg: str,
    stage_callback: Callable[[str], None] | None,
    on_directory: Callable[[Path], None] | None,
) -> None:
    """Dispatch a single message from stage_q to the appropriate callback."""
    if msg.startswith(_DIR_SENTINEL):
        if on_directory is not None:
            on_directory(Path(msg[len(_DIR_SENTINEL) :]))
    elif stage_callback is not None:
        stage_callback(msg)


def _replay_log_queue(log_q: Any) -> None:
    """Re-emit log records from the subprocess into the parent's log handlers."""
    while True:
        try:
            record = log_q.get_nowait()
            logging.getLogger(record.name).handle(record)
        except queue.Empty:
            break


def run_in_subprocess(
    path: Path,
    config: Config,
    _on_directory: Callable[[Path], None] | None = None,
    stage_callback: Callable[[str], None] | None = None,
) -> None:
    """Process a single staging item in an isolated subprocess.

    Drop-in replacement for pipeline_impl.run() — same signature, same
    semantics, but mutagen / musicbrainzngs / requests / Pillow are loaded
    only inside the child process and freed when it exits.
    """
    proc, stage_q, log_q, result_q = _spawn_worker(
        _pipeline_worker,
        (path, config),
    )

    # Drain both queues while the subprocess runs.  _DIR_SENTINEL messages in
    # stage_q are routed to _on_directory; everything else goes to
    # stage_callback.  log_q MUST also be drained here — multiprocessing.Queue
    # uses an OS pipe with a finite buffer; if the subprocess fills log_q
    # without the parent consuming it, the subprocess blocks on put() and
    # proc.is_alive() never becomes False (deadlock).  Draining here also
    # surfaces log records in real-time rather than only after the subprocess
    # exits.
    while proc.is_alive():  # pragma: no cover
        try:
            msg = stage_q.get(timeout=0.1)
            _handle_stage_msg(msg, stage_callback, _on_directory)
        except queue.Empty:
            pass
        _replay_log_queue(log_q)

    # Drain any messages that arrived just before the process exited.
    while True:
        try:
            msg = stage_q.get_nowait()
            _handle_stage_msg(msg, stage_callback, _on_directory)
        except queue.Empty:
            break

    proc.join()
    _replay_log_queue(log_q)

    try:
        status, value = result_q.get_nowait()
    except queue.Empty:  # pragma: no cover
        raise RuntimeError("Pipeline subprocess exited without returning a result")

    if status == "error":
        raise RuntimeError(f"Pipeline failed: {value}")
