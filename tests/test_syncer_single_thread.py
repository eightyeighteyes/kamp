"""Exactly one thread owns Bandcamp sync management (KAMP-726).

`Syncer` shared one `threading.Event` across thread generations, and `reload()`
CLEARED it after a bounded 5s join before starting a replacement. `_run` only
checks that event between iterations and cannot be interrupted mid-sync, so a
sync outlasting the join left the old thread looping against a cleared event —
two threads both walking the Bandcamp collection, the endpoint CLAUDE.md
documents as the hardest-rate-limited one. Nothing downstream throttles it: the
governor cannot see the stream sync because it runs in a spawn subprocess.

Three paths produced it: `reload()`, `pause()` -> `resume()`, and `start()`
called twice (reachable from `kamp daemon resume`, which has no state guard).

The fix removes the cause rather than racing it: the poll interval is read live
so `reload()` never needs to restart, and `start()` is a singleton guard that
reports whether it actually started anything.

These tests are deterministic by construction — no sleeps, and no bounded joins
racing a loaded runner (the KAMP-721 flake shape). Thread identity is tracked
through a ledger rather than `threading.enumerate()`, which cannot tell the
generations apart since both are named "syncer".
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kamp_daemon.config import (
    ArtworkConfig,
    BandcampConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from kamp_daemon.syncer import NeedsLoginError, Syncer


def _config(tmp_path: Path, poll_interval: int) -> Config:
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


class _Harness:
    """A Syncer whose sync_once is gated, recording which thread ran it."""

    def __init__(self, tmp_path: Path, poll_interval: int = 60) -> None:
        self.syncer = Syncer(_config(tmp_path, poll_interval))
        self.ledger: list[threading.Thread] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.syncer.sync_once = self._sync_once  # type: ignore[method-assign]

    def _sync_once(self, **_: Any) -> None:
        self.ledger.append(threading.current_thread())
        self.entered.set()
        self.release.wait()

    def finish(self) -> None:
        """Let any parked sync return, then retire every thread we started."""
        self.release.set()
        self.syncer.stop()
        for t in {*self.ledger}:
            t.join()


def test_reload_does_not_start_a_second_thread(tmp_path: Path) -> None:
    """A poll-interval change must not spawn a replacement generation.

    This is the whole invariant: one thread owns sync, and reload() adjusts it
    rather than racing a 5s join to replace it.
    """
    h = _Harness(tmp_path)
    h.syncer.start()
    h.entered.wait()  # generation 1 is parked inside sync_once
    first = h.syncer._thread

    h.syncer.reload(_config(tmp_path, 5))

    assert h.syncer._thread is first
    h.finish()
    # The parked sync ran once and the thread never began a second walk.
    assert h.ledger.count(first) == 1  # type: ignore[arg-type]


def test_reload_wakes_a_sleeping_thread(tmp_path: Path) -> None:
    """A shortened interval takes effect without waiting out the old sleep."""
    h = _Harness(tmp_path, poll_interval=60)
    # Let the first sync through so the thread reaches its 60-minute wait.
    h.release.set()
    h.syncer.start()
    h.entered.wait()

    h.syncer.reload(_config(tmp_path, 5))

    # The wake event is what breaks the sleep; without it the thread would sit
    # on the old 3600s timeout and this would hang rather than fail.
    assert h.syncer._wake.is_set() or len(h.ledger) > 1
    h.syncer.stop()
    for t in {*h.ledger}:
        t.join()


def test_start_twice_spawns_one_thread(tmp_path: Path) -> None:
    """The second start() reports that one was already running.

    Reachable today via `kamp daemon resume`, which DaemonCore does not guard.
    """
    h = _Harness(tmp_path)
    assert h.syncer.start() is True
    h.entered.wait()
    first = h.syncer._thread

    assert h.syncer.start() is False
    assert h.syncer._thread is first

    h.finish()
    assert len(set(h.ledger)) == 1


def test_resume_does_not_spawn_alongside_a_live_thread(tmp_path: Path) -> None:
    """pause() -> resume() while the old thread is winding down keeps one thread.

    pause() bounded-joins too, so a sync outlasting it leaves the thread alive;
    resume() must adopt it rather than start a second.
    """
    h = _Harness(tmp_path)
    h.syncer.start()
    h.entered.wait()
    first = h.syncer._thread

    # The join inside pause() would block on the parked sync; a no-op join makes
    # "the join timed out" instant and leaves the production 5s bound alone.
    with patch.object(first, "join"):
        h.syncer.pause()
    h.syncer.resume()

    assert h.syncer._thread is first
    h.finish()
    assert len(set(h.ledger)) == 1


def test_start_after_stop_does_not_spawn(tmp_path: Path) -> None:
    """stop() is terminal — shutdown cannot be outrun by a concurrent start().

    reload()/resume() are reachable from HTTP handlers on the anyio pool while
    stop() arrives on the main thread via SIGTERM, so without a latch a start()
    can win the race and leave a thread running after shutdown completed.
    """
    syncer = Syncer(_config(tmp_path, 60))
    syncer.stop()
    assert syncer.start() is False
    assert syncer._thread is None


def test_a_dead_thread_can_be_restarted(tmp_path: Path) -> None:
    """The guard must not over-block: a thread that exited can be replaced.

    _run breaks out of its loop on NeedsLoginError, leaving a dead thread
    object behind. Logging back in has to be able to start polling again.
    """
    syncer = Syncer(_config(tmp_path, 60))
    calls: list[int] = []

    def _needs_login(**_: Any) -> None:
        calls.append(1)
        raise NeedsLoginError("no session")

    syncer.sync_once = _needs_login  # type: ignore[method-assign]
    assert syncer.start() is True
    first = syncer._thread
    assert first is not None
    first.join()  # unbounded: the loop breaks immediately

    assert syncer.start() is True
    assert syncer._thread is not first
    syncer.stop()
    second = syncer._thread
    assert second is not None
    second.join()
    assert len(calls) == 2
