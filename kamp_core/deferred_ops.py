"""Deferred tag/rename operation queue (KAMP-309).

When a PATCH arrives for a track that is currently playing (or in mpv's
gapless-lookahead slot), the endpoint inserts a row into ``deferred_ops``
instead of performing the tag write and file move immediately.  The ops are
executed (drained) at three points:

  * track-end — when the finished track's lock naturally releases
  * daemon startup — catches ops that survived a crash or clean quit
  * app quit — synchronous flush with a 5-second cap
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kamp_core.library import DeferredOp, LibraryIndex
    from kamp_daemon.watcher import LibraryWatcher

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


def execute_op(
    op: "DeferredOp",
    index: "LibraryIndex",
    lib_watcher: "LibraryWatcher | None",
    on_completed: Callable[[int, int], None],
    notify_library_changed: Callable[[], None],
) -> None:
    """Execute one deferred op: tag write + optional file move + DB update.

    Raises on failure — callers are responsible for calling fail_deferred_op
    and deciding whether to retry.
    """
    from kamp_core.library import (
        write_album_tags_to_file,
        write_artist_to_file,
        write_title_to_file,
    )

    payload = json.loads(op.payload_json)

    if op.op_type == "track_retag":
        old_path = Path(payload["old_path"])
        new_path = Path(payload["new_path"])
        title: str = payload["title"]
        is_case_only: bool = payload.get("is_case_only", False)

        write_title_to_file(old_path, title)
        # KAMP-582: an artist edit on a locked track merges into a pending
        # rename op (deferred_ops is UNIQUE(track_id)) — honor it here.
        if payload.get("artist"):
            write_artist_to_file(old_path, payload["artist"])

        if str(old_path) != str(new_path):
            new_path.parent.mkdir(parents=True, exist_ok=True)
            if lib_watcher is not None:
                lib_watcher.suppress_paths({old_path, new_path})
            if is_case_only:
                tmp = old_path.with_suffix(f".kamp_rename{old_path.suffix}")
                shutil.move(str(old_path), tmp)
                shutil.move(str(tmp), new_path)
            else:
                shutil.move(str(old_path), new_path)

        index.move_track(old_path, new_path, title, time.time())

        if lib_watcher is not None and str(old_path) != str(new_path):
            lib_watcher.scan_now()

    elif op.op_type == "album_retag":
        old_path = Path(payload["old_path"])
        new_path = Path(payload["new_path"])
        new_album: str = payload["new_album"]
        new_album_artist: str = payload["new_album_artist"]
        new_artist: str | None = payload.get("new_artist")
        is_case_only = payload.get("is_case_only", False)

        write_album_tags_to_file(
            old_path, new_album, new_album_artist, artist=new_artist
        )
        # KAMP-582: a per-track artist edit merged into this pending album op.
        if payload.get("artist"):
            write_artist_to_file(old_path, payload["artist"])

        if str(old_path) != str(new_path):
            new_path.parent.mkdir(parents=True, exist_ok=True)
            if lib_watcher is not None:
                lib_watcher.suppress_paths({old_path, new_path})
            if is_case_only:
                tmp = old_path.with_suffix(f".kamp_rename{old_path.suffix}")
                shutil.move(str(old_path), tmp)
                shutil.move(str(tmp), new_path)
            else:
                shutil.move(str(old_path), new_path)

        index.update_track_after_album_drain(
            op.track_id, new_path, new_album, new_album_artist, new_artist, time.time()
        )

        if lib_watcher is not None and str(old_path) != str(new_path):
            lib_watcher.scan_now()

    elif op.op_type == "track_artist_retag":
        # KAMP-582: tag-only artist write deferred while the track was playing.
        # The DB was updated when the op was queued; only the file write is
        # outstanding. The payload carries no path — resolve the track's
        # CURRENT location so a rename that landed while this op was pending
        # doesn't strand the write on a stale path.
        track = index.get_track_by_id(op.track_id)
        if track is None:
            logger.warning(
                "deferred op %d: track %d no longer exists, skipping artist write",
                op.id,
                op.track_id,
            )
        else:
            write_artist_to_file(track.file_path, payload["artist"])

    else:
        raise ValueError(f"unknown deferred op type: {op.op_type!r}")

    index.complete_deferred_op(op.id)
    # Broadcast deferred_op.completed BEFORE library.changed so the frontend
    # clears the pip before the library reload re-renders the track list.
    on_completed(op.track_id, op.id)
    notify_library_changed()


def _handle_failure(
    op: "DeferredOp",
    index: "LibraryIndex",
    exc: Exception,
) -> None:
    """Increment attempts; delete the row if max attempts reached."""
    next_attempts = op.attempts + 1
    logger.error(
        "deferred op %d (track_id=%d, type=%s) failed (attempt %d/%d): %s",
        op.id,
        op.track_id,
        op.op_type,
        next_attempts,
        MAX_ATTEMPTS,
        exc,
    )
    if next_attempts >= MAX_ATTEMPTS:
        logger.error(
            "deferred op %d: max attempts reached, dropping to prevent infinite retry",
            op.id,
        )
        index.complete_deferred_op(op.id)
    else:
        index.fail_deferred_op(op.id, str(exc))


def drain_for_track(
    track_id: int,
    index: "LibraryIndex",
    lib_watcher: "LibraryWatcher | None",
    on_completed: Callable[[int, int], None],
    notify_library_changed: Callable[[], None],
) -> None:
    """Execute all pending ops for *track_id* (called after track-end event)."""
    for op in index.pending_deferred_ops_for_track(track_id):
        try:
            execute_op(op, index, lib_watcher, on_completed, notify_library_changed)
        except Exception as exc:
            _handle_failure(op, index, exc)


def drain_all(
    index: "LibraryIndex",
    lib_watcher: "LibraryWatcher | None",
    on_completed: Callable[[int, int], None],
    notify_library_changed: Callable[[], None],
    *,
    timeout_secs: float | None = None,
    is_locked: Callable[[int], bool] | None = None,
) -> None:
    """Execute all pending ops (startup drain or shutdown flush).

    *timeout_secs* caps total execution time; ops skipped by the deadline
    remain in the table for the next drain cycle.

    *is_locked* is called per-op when provided; ops for locked tracks (e.g. a
    track resumed after a crash restart) are skipped and retried at track-end.
    This is critical on Windows where open files cannot be renamed.
    """
    deadline = time.monotonic() + timeout_secs if timeout_secs is not None else None
    for op in index.all_pending_deferred_ops():
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "deferred drain timed out after %.1f s; remaining ops will retry",
                timeout_secs,
            )
            break
        if is_locked is not None and is_locked(op.track_id):
            logger.debug(
                "deferred op %d: track %d is locked, skipping until track ends",
                op.id,
                op.track_id,
            )
            continue
        try:
            execute_op(op, index, lib_watcher, on_completed, notify_library_changed)
        except Exception as exc:
            _handle_failure(op, index, exc)
