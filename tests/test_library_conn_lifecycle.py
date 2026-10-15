"""Per-thread SQLite connections must not outlive their thread (KAMP-720).

`LibraryIndex._conn` opens one connection per thread and registered it in
`_all_conns`, which only `close()` ever emptied. Every thread that touched the
index therefore left a live connection — and its SQLite page cache — behind for
the life of the process. FastAPI runs `def` endpoints on anyio's worker pool,
which retires idle workers, and the daemon spawns a fresh thread per track for
the playback lookahead, so ordinary use churned threads steadily.

The cost is invisible to tracemalloc because SQLite's page cache is allocated by
C, which is why the leak read as "footprint climbs, traced stays flat".

These tests need no sleep, timeout or poll loop: CPython releases a thread's
local storage as the thread exits, so the finalizer has already run by the time
join() returns.
"""

from __future__ import annotations

import gc
import sqlite3
import threading
import weakref
from pathlib import Path
from typing import Any

import pytest

from kamp_core.library import LibraryIndex, _retire_conn


@pytest.fixture
def index(tmp_path: Path) -> Any:
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


def test_thread_conn_retired_when_thread_exits(index: LibraryIndex) -> None:
    """A dead thread's connection is deregistered and closed."""
    baseline = len(index._all_conns)
    captured: list[sqlite3.Connection] = []

    def work() -> None:
        captured.append(index._conn)
        index.get_stats()

    t = threading.Thread(target=work)
    t.start()
    t.join()
    gc.collect()

    assert len(index._all_conns) == baseline
    # Deregistering is not enough — the connection must actually be closed, or
    # the page cache it holds is still charged to the process.
    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")


def test_thread_conn_retired_when_thread_raises(index: LibraryIndex) -> None:
    """An exception propagating out of a thread does not strand its connection."""
    baseline = len(index._all_conns)

    def work() -> None:
        index.get_stats()
        raise RuntimeError("boom")

    original = threading.excepthook
    threading.excepthook = lambda args: None  # keep pytest's output clean
    try:
        t = threading.Thread(target=work)
        t.start()
        t.join()
    finally:
        threading.excepthook = original
    gc.collect()

    assert len(index._all_conns) == baseline


def test_live_thread_keeps_its_conn(index: LibraryIndex) -> None:
    """Retirement is driven by thread death, not by garbage collection alone.

    Guards against an over-eager finalizer closing a connection out from under
    the thread still using it.
    """
    baseline = len(index._all_conns)
    ready = threading.Event()
    release = threading.Event()

    def work() -> None:
        index.get_stats()
        ready.set()
        release.wait()
        # Still usable after a full collection while the thread is alive.
        index.get_stats()

    t = threading.Thread(target=work)
    t.start()
    try:
        ready.wait()
        gc.collect()
        assert len(index._all_conns) == baseline + 1
    finally:
        release.set()
        t.join()


def test_conn_lock_is_reentrant(index: LibraryIndex) -> None:
    """The registry lock must be reentrant, or retirement can self-deadlock.

    A finalizer runs at an arbitrary allocation point, which can be on a thread
    already inside the locked region — close() is allocation-heavy. With a plain
    Lock, _retire_conn would block on a lock its own thread already holds.
    Acquiring non-blockingly distinguishes the two without risking a hang.
    """
    with index._all_conns_lock:
        acquired = index._all_conns_lock.acquire(blocking=False)
        if acquired:
            index._all_conns_lock.release()
    assert acquired, "a plain Lock cannot be re-acquired by its holder"


def test_retire_conn_survives_a_failing_close() -> None:
    """_retire_conn must never raise: it runs as a finalizer.

    An exception escaping a finalizer is not handled by anything — it surfaces
    as an unraisable exception on stderr and the connection stays leaked.
    """

    class Stubborn:
        def close(self) -> None:
            raise sqlite3.OperationalError("cannot close")

    conn: Any = Stubborn()
    registry: list[Any] = [conn]
    _retire_conn(conn, registry, threading.RLock())

    # Deregistered despite the close failing, so it is not retried forever.
    assert registry == []


def test_index_is_collectable_after_close(tmp_path: Path) -> None:
    """Retirement must not pin the index itself (the B1 regression).

    Registering a *bound method* as the finalize callback would keep the whole
    LibraryIndex alive for as long as the registering thread — and the main
    thread never exits. The daemon builds and drops short-lived indexes
    (kamp_daemon/__main__.py builds one to initialise the DB, closes it, and
    lets it go), so pinning them would trade this leak for another one.
    """
    idx = LibraryIndex(tmp_path / "library.db")
    idx.get_stats()
    ref = weakref.ref(idx)
    idx.close()
    del idx
    gc.collect()

    assert ref() is None
