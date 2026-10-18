"""Only one Bandcamp sync runs at a time (KAMP-728).

`POST /api/v1/bandcamp/sync` spawned a `manual-sync` thread per call with no
guard, so pressing sync twice while the poller was already syncing produced
three concurrent collection walks against the endpoint CLAUDE.md documents as
the hardest-rate-limited one. Nothing downstream throttles it — the governor
cannot see a sync running in a spawn subprocess.

The guard lives in `Syncer`, not in the endpoint, because the poller calls
`sync_once` directly and never passes through any HTTP wrapper.

Tests gate INSIDE sync_once by patching `_spawn_worker`. Replacing `sync_once`
wholesale (as the KAMP-726 harness does) would patch the lock out entirely and
assert nothing. No sleeps and no bounded joins, per KAMP-721.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.config import (
    ArtworkConfig,
    BandcampConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from kamp_daemon.syncer import Syncer


def _config(tmp_path: Path, mode: str = "stream") -> Config:
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
            format="mp3-v0", poll_interval_minutes=0, collection_mode=mode
        ),
    )


class _GatedWorker:
    """A _spawn_worker stand-in that parks until released.

    Holding the worker open holds the sync lock open, which is what lets a
    second caller be tested without any timing assumptions.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.raises = False

    def __call__(self, target: Any, args: tuple[Any, ...]) -> Any:
        self.calls += 1
        self.entered.set()
        self.release.wait()
        if self.raises:
            raise RuntimeError("worker blew up")
        proc = MagicMock()
        proc.is_alive.return_value = False
        proc.exitcode = 0
        # queue.Empty specifically: the drain loops catch that, not Exception.
        status_q: Any = MagicMock()
        status_q.get_nowait.side_effect = queue.Empty
        log_q: Any = MagicMock()
        log_q.get_nowait.side_effect = queue.Empty
        result_q: Any = MagicMock()
        result_q.get_nowait.return_value = ("ok", 0)
        return proc, status_q, log_q, result_q


def test_a_second_sync_declines_while_one_is_running(tmp_path: Path) -> None:
    """The exact repro: a manual sync on top of a running one does not walk."""
    syncer = Syncer(_config(tmp_path))
    worker = _GatedWorker()

    with patch("kamp_daemon.syncer._spawn_worker", side_effect=worker):
        holder = threading.Thread(target=syncer.sync_once)
        holder.start()
        worker.entered.wait()  # the first sync owns the lock
        assert syncer.is_syncing is True

        # The second caller runs on this thread and must return immediately.
        syncer.sync_once()
        assert worker.calls == 1

        worker.release.set()
        holder.join()

    assert syncer.is_syncing is False


def test_the_lock_is_released_when_a_sync_raises(tmp_path: Path) -> None:
    """A failed sync must not wedge every later one.

    Without try/finally the first exception would leave the lock held and every
    subsequent sync would decline silently — worse than the bug being fixed.
    """
    syncer = Syncer(_config(tmp_path))
    worker = _GatedWorker()
    worker.raises = True
    worker.release.set()

    with patch("kamp_daemon.syncer._spawn_worker", side_effect=worker):
        # Inline rather than in a thread so the propagation is asserted here
        # instead of surfacing as an unraisable thread exception.
        with pytest.raises(RuntimeError, match="worker blew up"):
            syncer.sync_once()

        assert syncer.is_syncing is False

        # A later sync still gets through.
        worker.raises = False
        syncer.sync_once()

    assert worker.calls == 2


def test_sync_all_still_resets_and_syncs(tmp_path: Path) -> None:
    """sync_all_purchases must do BOTH halves under one lock.

    It calls into the same guarded path, so a naive non-reentrant guard would
    let the state reset commit and then silently decline the re-download. With
    poll_interval_minutes at 0 there is no next poll to recover it, so the
    reset would be permanent and the re-download would never happen.
    """
    syncer = Syncer(_config(tmp_path, mode="download"))
    worker = _GatedWorker()
    worker.release.set()
    index = MagicMock()

    with patch("kamp_daemon.syncer._spawn_worker", side_effect=worker):
        with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
            with patch("kamp_core.library.LibraryIndex", return_value=index):
                syncer.sync_all_purchases()

    assert index.reset_collection_sync_state.called
    assert worker.calls == 1


def test_is_syncing_is_false_at_rest(tmp_path: Path) -> None:
    """The flag tracks the lock, not the sync-status string.

    Deliberately not derived from status_callback state: download_album drives
    that too, so a queued album download would otherwise block a manual sync.
    """
    syncer = Syncer(_config(tmp_path))
    assert syncer.is_syncing is False
