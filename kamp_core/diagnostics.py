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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final

_ENV_VAR: Final[str] = "KAMP_DIAGNOSTICS"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

# A leak that takes days to show is not sampled usefully at second resolution,
# and a tighter interval only inflates the log.
DEFAULT_INTERVAL_SECONDS: Final[float] = 60.0


def enabled() -> bool:
    """Return True when diagnostics sampling has been switched on."""
    return os.environ.get(_ENV_VAR, "").strip().lower() in _TRUTHY


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
    ) -> None:
        self._out_dir = Path(out_dir)
        self._interval = interval_seconds
        self._clock = clock
        # pid -> role.  Guarded because children are registered from the
        # playback and sync threads while the sampler thread reads.
        self._tracked: dict[int, str] = {os.getpid(): self_role}
        # Roles whose pid is not known up front (mpv is spawned lazily on first
        # playback) are resolved fresh each tick instead of registered once.
        self._resolvers: dict[str, Callable[[], int | None]] = {}
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

    def write_sample(self) -> None:
        """Append one tick to the log.  Never raises."""
        samples = self.sample()
        record = {
            "t": self._clock(),
            "procs": [
                {"pid": s.pid, "role": s.role, "rss_bytes": s.rss_bytes}
                for s in samples
            ],
        }
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            with self.current_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            # Diagnostics are never worth failing the daemon over.
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
        while True:
            self.write_sample()
            if self._stop.wait(self._interval):
                return
