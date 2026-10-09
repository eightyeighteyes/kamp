"""Opt-in per-process memory sampling (KAMP-716).

Prerequisite instrumentation for KAMP-680 (1.6 GB of resident memory after a
few days of uptime) and KAMP-704 (kamp degrading other applications' draw
performance).  Both bugs are currently unworkable for the same reason: nobody
knows which of kamp's processes is responsible, and the CLAUDE.md
diagnosis-discipline lesson is explicit that a fix without evidence is how
sessions get burned on components that were never broken.

Two decisions here are load-bearing and easy to get wrong:

**Current RSS, not peak.**  ``resource.getrusage(...).ru_maxrss`` is the
obvious stdlib answer and it is useless for this job — it is a high-water mark,
so it only ever rises and a leak that has been *fixed* looks identical to one
that has not.  Each platform reader below returns the process's RSS right now.

**A missing reading is data, not an error.**  Children (mpv, spawn-mode sync
workers) come and go, and a sampler that raised when one exited mid-read would
take the daemon down with it.  Every reader returns ``None`` instead, and the
whole write path is wrapped — diagnostics must never be the reason kamp falls
over.

Deliberately dependency-free: psutil would do all of this, but it is a new
runtime dependency plus a compiled wheel in the PyInstaller bundle, which is a
poor trade for three short platform readers.  The ctypes approach on Windows
mirrors ``win_credential.py``.

Off unless ``KAMP_DIAGNOSTICS`` is set, so there is no cost in normal use.

Companion samplers live in the Electron main process (``app.getAppMetrics()``,
which attributes memory across main/renderer/GPU) and the renderer (running
animation counts against window focus, for KAMP-704).  This module covers the
Python daemon and the processes it owns.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final

_ENV_VAR: Final[str] = "KAMP_DIAGNOSTICS"
_TRACEMALLOC_ENV_VAR: Final[str] = "KAMP_DIAGNOSTICS_TRACEMALLOC"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

# A leak that takes days to show is not sampled usefully at second resolution,
# and a tighter interval only inflates the log.
DEFAULT_INTERVAL_SECONDS: Final[float] = 60.0

# Allocation snapshots walk every tracked allocation, so they run once every N
# RSS ticks rather than every tick. At the default interval that is ~10 minutes,
# which is fine resolution for a leak measured in hours — and the first snapshot
# is taken on the very first tick, so a startup burst is still captured.
DEFAULT_ALLOC_EVERY: Final[int] = 10

# Startup is sampled finely, then the cadence settles. 24 x 5s covers the first
# two minutes, which is where KAMP-680's +740 MiB step happens — at the steady
# 60s cadence it appeared as one unhelpful jump between consecutive ticks.
DEFAULT_BURST_TICKS: Final[int] = 24
DEFAULT_BURST_INTERVAL_SECONDS: Final[float] = 5.0


def enabled() -> bool:
    """Return True when diagnostics sampling has been switched on."""
    return os.environ.get(_ENV_VAR, "").strip().lower() in _TRUTHY


def tracemalloc_enabled() -> bool:
    """Return True when Python allocation tracking has been switched on.

    A **separate** gate from :func:`enabled` on purpose. RSS sampling is three
    cheap reads a minute and can be left on for days; tracemalloc intercepts
    every allocation and costs real CPU and memory of its own, so it must be
    opted into deliberately rather than riding along on ``KAMP_DIAGNOSTICS``.
    """
    return os.environ.get(_TRACEMALLOC_ENV_VAR, "").strip().lower() in _TRUTHY


def start_tracemalloc() -> None:
    """Begin tracking Python allocations. Idempotent.

    One frame per traceback: the question this answers is "which line is holding
    the memory", and deeper stacks multiply tracemalloc's own overhead for
    detail that the allocation site already provides.
    """
    if not tracemalloc.is_tracing():
        tracemalloc.start(1)


def traced_memory() -> tuple[int, int] | None:
    """``(current, peak)`` bytes of live traced allocations, or None if not tracing.

    ``current`` against RSS is the diagnosis for KAMP-680: close together means
    live objects are being retained and the top allocation sites name them; far
    apart means Python released the memory and the allocator kept the pages, for
    which the only fix in this codebase's experience is subprocess isolation.
    """
    if not tracemalloc.is_tracing():
        return None
    current, peak = tracemalloc.get_traced_memory()
    return int(current), int(peak)


def top_allocations(limit: int = 15) -> list[dict[str, object]]:
    """The *limit* largest live allocation sites, biggest first.

    Empty when not tracing, so callers need no separate guard.
    """
    if not tracemalloc.is_tracing():
        return []
    stats = tracemalloc.take_snapshot().statistics("lineno")[:limit]
    return [
        {
            "file": stat.traceback[0].filename,
            "line": stat.traceback[0].lineno,
            "size_bytes": int(stat.size),
            "count": int(stat.count),
        }
        for stat in stats
    ]


@dataclass(frozen=True)
class ProcessSample:
    """One process's resident size at one instant.

    ``rss_bytes`` is None when the process could not be read — usually because
    it exited between registration and this tick.
    """

    pid: int
    role: str
    rss_bytes: int | None


# --------------------------------------------------------------------------
# Platform readers
# --------------------------------------------------------------------------


def _statm_path(pid: int) -> Path:
    return Path(f"/proc/{pid}/statm")


def _page_size() -> int:
    return os.sysconf("SC_PAGE_SIZE")


def _rss_linux(pid: int) -> int | None:
    """Resident size from /proc/<pid>/statm (field 2, in pages)."""
    try:
        fields = _statm_path(pid).read_text().split()
        return int(fields[1]) * _page_size()
    except (OSError, IndexError, ValueError):
        return None


def _rss_darwin(pid: int) -> int | None:
    """Resident size via ``ps``, which reports kilobytes.

    macOS has no /proc.  Shelling out is acceptable at a 60-second cadence and
    avoids binding libproc through ctypes for one number.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip()) * 1024
    except ValueError:
        return None


def _rss_windows(pid: int) -> int | None:  # pragma: no cover - needs Windows
    """Resident size via GetProcessMemoryInfo's WorkingSetSize.

    The body is inside a ``sys.platform`` guard rather than carrying
    ``type: ignore`` comments: ``ctypes.WinDLL`` and ``ctypes.wintypes`` do not
    exist off Windows, and narrowing on the platform is how mypy is told that
    without blanket-suppressing attribute errors for the whole module.

    A Windows caveat that shows up when reading the logs: a process that has
    *exited* may still read back a small working set (~32 KB observed) rather
    than nothing, because a Win32 process object survives as long as any handle
    to it is open — and ``subprocess.Popen`` holds its handle past ``wait()``.
    So on Windows, "a row stopped appearing" is the signal that a child is gone;
    "a row went tiny and flat" means exited-but-not-yet-closed. On POSIX the pid
    simply becomes unreadable and the row drops out. Not normalised here: the
    reader reports what the OS reports, and the alternative is another
    Windows-only branch that cannot be verified outside CI.
    """
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    class _ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not handle:
        return None
    try:
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), ctypes.sizeof(counters)
        )
        if not ok:
            return None
        return int(counters.WorkingSetSize)
    finally:
        kernel32.CloseHandle(handle)


def process_rss(pid: int) -> int | None:
    """Return *pid*'s current resident set size in bytes, or None."""
    if sys.platform == "win32":  # pragma: no cover - needs Windows
        return _rss_windows(pid)
    if sys.platform == "darwin":
        return _rss_darwin(pid)
    if sys.platform.startswith("linux"):
        return _rss_linux(pid)
    return None


# --------------------------------------------------------------------------
# Sampler
# --------------------------------------------------------------------------


class DiagnosticsSampler:
    """Periodically append per-process RSS readings to a JSONL log.

    One line per tick (rather than per process) so a tick's processes stay
    correlated — the question being asked is "where did the total go", which
    needs the processes read together.
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.time,
        self_role: str = "daemon",
        alloc_every: int = DEFAULT_ALLOC_EVERY,
        burst_ticks: int = DEFAULT_BURST_TICKS,
        burst_interval_seconds: float = DEFAULT_BURST_INTERVAL_SECONDS,
    ) -> None:
        self._out_dir = Path(out_dir)
        self._interval = interval_seconds
        self._clock = clock
        self._alloc_every = alloc_every
        self._burst_ticks = burst_ticks
        self._burst_interval = burst_interval_seconds
        # pid -> role.  Guarded because children are registered from the
        # playback and sync threads while the sampler thread reads.
        self._tracked: dict[int, str] = {os.getpid(): self_role}
        # Roles whose pid is not known up front (mpv is spawned lazily on first
        # playback) are resolved fresh each tick instead of registered once.
        self._resolvers: dict[str, Callable[[], int | None]] = {}
        # Scalar readings recorded alongside RSS — see register_metric.
        self._metrics: dict[str, Callable[[], float | None]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- registration ----------------------------------------------------

    def register(self, pid: int, role: str) -> None:
        """Track *pid* under *role* (e.g. "mpv", "sync-worker")."""
        with self._lock:
            self._tracked[pid] = role

    def unregister(self, pid: int) -> None:
        """Stop tracking *pid*.  Unknown pids are ignored."""
        with self._lock:
            self._tracked.pop(pid, None)

    def register_metric(self, name: str, read: Callable[[], float | None]) -> None:
        """Track a scalar read on every tick, recorded next to the RSS readings.

        For attributing how much of a process's memory one subsystem accounts for
        — KAMP-718 reads mpv's demuxer cache size against mpv's RSS. Both have to
        land in the same record: two independently sampled logs cannot be lined
        up after the fact.

        A reader that returns None or raises is omitted rather than recorded as
        zero, so "not running" stays distinguishable from "measured zero".
        """
        with self._lock:
            self._metrics[name] = read

    def register_resolver(self, role: str, resolve: Callable[[], int | None]) -> None:
        """Track *role* via a callable re-read on every tick.

        For processes whose pid is not known when sampling starts — mpv is
        spawned on first playback and respawned after a crash, so a one-time
        pid would go stale.
        """
        with self._lock:
            self._resolvers[role] = resolve

    # -- sampling --------------------------------------------------------

    def sample(self) -> list[ProcessSample]:
        """Read every tracked process's RSS right now."""
        with self._lock:
            tracked = dict(self._tracked)
            resolvers = dict(self._resolvers)

        samples = [
            ProcessSample(pid=pid, role=role, rss_bytes=process_rss(pid))
            for pid, role in tracked.items()
        ]
        for role, resolve in resolvers.items():
            try:
                pid = resolve()
            except Exception:
                # A resolver reaching into a half-torn-down engine must not
                # cost the whole tick.
                continue
            if pid is None:
                continue
            samples.append(
                ProcessSample(pid=pid, role=role, rss_bytes=process_rss(pid))
            )
        return samples

    def current_path(self) -> Path:
        """Path of the log file for the current date (UTC)."""
        stamp = datetime.fromtimestamp(self._clock(), tz=timezone.utc)
        return self._out_dir / f"memory-{stamp:%Y-%m-%d}.jsonl"

    def alloc_path(self) -> Path:
        """Path of the allocation-snapshot log for the current date (UTC).

        Separate from the RSS log because the records have a different shape and
        a much lower cadence; mixing them would make both awkward to read.
        """
        stamp = datetime.fromtimestamp(self._clock(), tz=timezone.utc)
        return self._out_dir / f"alloc-{stamp:%Y-%m-%d}.jsonl"

    def _read_metrics(self) -> dict[str, float]:
        """Read every registered metric, skipping any that fails or has no value."""
        with self._lock:
            readers = dict(self._metrics)
        out: dict[str, float] = {}
        for name, read in readers.items():
            try:
                value = read()
            except Exception:
                # Same contract as the pid resolvers: one bad reader must not
                # cost the whole tick.
                continue
            if value is not None:
                out[name] = value
        return out

    def write_sample(self) -> None:
        """Append one tick to the log.  Never raises."""
        samples = self.sample()
        record: dict[str, object] = {
            "t": self._clock(),
            "procs": [
                {"pid": s.pid, "role": s.role, "rss_bytes": s.rss_bytes}
                for s in samples
            ],
        }
        metrics = self._read_metrics()
        if metrics:
            record["metrics"] = metrics
        # Only present while tracing, so a reader can tell "not measured" from
        # "measured as zero" without consulting the env var the run used.
        traced = traced_memory()
        if traced is not None:
            record["traced_current_bytes"] = traced[0]
            record["traced_peak_bytes"] = traced[1]
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            with self.current_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            # Diagnostics are never worth failing the daemon over.
            pass

    def write_allocation_snapshot(self, limit: int = 15) -> None:
        """Append the largest live allocation sites.  No-op unless tracing.

        Taking a snapshot walks every tracked allocation, so this runs on a much
        slower cadence than :meth:`write_sample` — see ``alloc_every``.
        """
        top = top_allocations(limit)
        if not top:
            return
        record = {"t": self._clock(), "top": top}
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            with self.alloc_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Begin sampling on a background thread.  Idempotent."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="kamp-diagnostics", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and join the thread.  Safe without start()."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None

    def _run(self) -> None:
        # Sample immediately so a short-lived session still records something,
        # then wait on the Event rather than sleeping — stop() must not block
        # for a whole interval during shutdown.
        #
        # The first burst_ticks run at burst_interval rather than interval. Two
        # reasons, both learned the hard way on KAMP-680:
        #
        #  - At a flat 60s cadence the startup growth showed up as a single
        #    64 -> 804 MiB jump between two ticks. The ramp carries the
        #    information about *what* is allocating; one jump carries none.
        #  - Allocation snapshots fired on tick 0 and then tick 10. Tick 0 is
        #    *before* any startup work, so a short run captured one empty
        #    snapshot and nothing else.
        #
        # Note what a snapshot can and cannot show: it lists allocations that are
        # still LIVE, so it finds retained references but never a churner that
        # frees what it allocates. KAMP-680 turned out to be the latter, which is
        # why sampling frequently enough to land near traced-peak matters — that
        # is the only moment a churner's working set is visible at all.
        tick = 0
        while True:
            self.write_sample()
            in_burst = tick < self._burst_ticks
            if in_burst or (self._alloc_every > 0 and tick % self._alloc_every == 0):
                self.write_allocation_snapshot()
            tick += 1
            delay = self._burst_interval if tick < self._burst_ticks else self._interval
            if self._stop.wait(delay):
                return
