"""Behavioural tests for the opt-in process-memory sampler (KAMP-716).

The sampler exists to answer KAMP-680's blocking question — *which* process
holds the 1.6 GB — so the properties under test are the ones that make a
multi-day memory curve trustworthy: current (not peak) usage, one durable line per
tick, and a sampler that never takes the daemon down with it when a process
disappears mid-read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from kamp_core import diagnostics

# --------------------------------------------------------------------------
# enabled()
# --------------------------------------------------------------------------


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAMP_DIAGNOSTICS", raising=False)
    assert diagnostics.enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_enabled_accepts_common_truthy_spellings(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("KAMP_DIAGNOSTICS", value)
    assert diagnostics.enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_enabled_rejects_falsey_spellings(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("KAMP_DIAGNOSTICS", value)
    assert diagnostics.enabled() is False


# --------------------------------------------------------------------------
# process_footprint() — platform readers
# --------------------------------------------------------------------------


def test_footprint_linux_converts_resident_pages_to_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # statm fields are "size resident shared text lib data dt", in pages.
    statm = tmp_path / "statm"
    statm.write_text("5000 1234 900 10 0 400 0\n")
    monkeypatch.setattr(diagnostics, "_statm_path", lambda pid: statm)
    monkeypatch.setattr(diagnostics, "_page_size", lambda: 4096)

    assert diagnostics._footprint_linux(999) == 1234 * 4096


def test_footprint_linux_returns_none_when_process_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "nope" / "statm"
    monkeypatch.setattr(diagnostics, "_statm_path", lambda pid: missing)

    assert diagnostics._footprint_linux(999) is None


def test_footprint_linux_returns_none_on_malformed_statm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    statm = tmp_path / "statm"
    statm.write_text("garbage\n")
    monkeypatch.setattr(diagnostics, "_statm_path", lambda pid: statm)

    assert diagnostics._footprint_linux(999) is None


_darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="proc_pid_rusage is a macOS interface"
)


@_darwin_only
def test_footprint_darwin_reads_a_plausible_value() -> None:
    value = diagnostics._footprint_darwin(os.getpid())

    assert value is not None
    assert value > 1_000_000, f"implausibly small footprint: {value}"


@_darwin_only
def test_footprint_darwin_agrees_with_vmmap() -> None:
    """Cross-check against an independent reading of the same quantity.

    The whole reason this reader was rewritten (KAMP-719) is that the previous
    one was never validated against anything. `vmmap --summary` reports the same
    `phys_footprint` Activity Monitor shows, so if these two disagree the reader
    is wrong — which is precisely the failure that went unnoticed before.
    """
    out = subprocess.run(
        ["vmmap", "--summary", str(os.getpid())],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    reported = None
    for line in out.splitlines():
        if "Physical footprint:" in line and "Peak" not in line:
            raw = line.split(":", 1)[1].strip()
            scale = {"G": 1024**3, "M": 1024**2, "K": 1024}.get(raw[-1])
            if scale:
                reported = float(raw[:-1]) * scale
            break
    if reported is None:
        pytest.skip("vmmap did not report a physical footprint")

    value = diagnostics._footprint_darwin(os.getpid())
    assert value is not None
    # vmmap rounds to one decimal in its own unit, and the process keeps running
    # between the two reads, so agreement means "same ballpark", not identical.
    assert abs(value - reported) < max(
        4 * 1024 * 1024, reported * 0.25
    ), f"reader says {value / 1048576:.1f} MiB, vmmap says {reported / 1048576:.1f} MiB"


@_darwin_only
def test_footprint_darwin_falls_back_after_a_large_transient() -> None:
    """The property that `ps rss` failed and this exists to provide.

    `ps rss` kept counting released pages: after churning 200 MiB blobs it read
    310 MiB while the OS charged the process 10. That is what made the daemon
    appear to reach 900+ MiB when Activity Monitor showed ~199 MB, and it sent
    several hypotheses chasing a number that was not real.
    """
    pid = os.getpid()
    before = diagnostics._footprint_darwin(pid)
    assert before is not None

    blob = [bytearray(2_000_000) for _ in range(50)]  # ~100 MiB held
    during = diagnostics._footprint_darwin(pid)
    assert during is not None
    assert during > before + 50_000_000, "should track memory actually held"

    del blob
    after = diagnostics._footprint_darwin(pid)
    assert after is not None
    # The point: it comes back down. ps rss did not.
    assert after < during - 50_000_000, (
        f"footprint stayed at {after / 1048576:.0f} MiB after releasing "
        f"~100 MiB (peak {during / 1048576:.0f})"
    )


@_darwin_only
def test_footprint_darwin_returns_none_for_an_unknown_pid() -> None:
    # Unreadable or gone is absent, not an error — the contract every reader in
    # this module shares, because children exit mid-tick.
    assert diagnostics._footprint_darwin(999_999) is None


def test_footprint_darwin_returns_none_when_the_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Broken:
        def __getattr__(self, name: str) -> Any:
            raise OSError("libSystem unavailable")

    monkeypatch.setattr(diagnostics.ctypes, "CDLL", lambda *a, **k: _Broken())

    assert diagnostics._footprint_darwin(999) is None


def test_statm_path_points_at_proc() -> None:
    assert diagnostics._statm_path(1234) == Path("/proc/1234/statm")


@pytest.mark.skipif(
    not hasattr(os, "sysconf"),
    reason="os.sysconf is POSIX-only; the Linux reader is unreachable on Windows",
)
def test_page_size_is_a_positive_power_of_two() -> None:
    # Guarded on the attribute rather than the platform name: _page_size is only
    # ever reached from _footprint_linux, which process_footprint only dispatches to on
    # linux, so there is nothing to assert where sysconf does not exist.
    size = diagnostics._page_size()
    assert size > 0 and size & (size - 1) == 0


def test_process_footprint_dispatches_on_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diagnostics.sys, "platform", "linux")
    monkeypatch.setattr(diagnostics, "_footprint_linux", lambda pid: 111)
    assert diagnostics.process_footprint(1) == 111

    monkeypatch.setattr(diagnostics.sys, "platform", "darwin")
    monkeypatch.setattr(diagnostics, "_footprint_darwin", lambda pid: 222)
    assert diagnostics.process_footprint(1) == 222


@pytest.mark.skipif(
    not (
        sys.platform == "win32"
        or sys.platform == "darwin"
        or sys.platform.startswith("linux")
    ),
    reason="no reader for this platform",
)
def test_process_footprint_reads_a_plausible_value_for_a_live_process() -> None:
    # The only unmocked test of the platform readers, and the one that matters:
    # every other test fakes the syscall, so they prove the parsing but not that
    # the reader works here. This is what keeps the Windows ctypes path honest --
    # CI runs it on Windows, where nothing else asserts a real number.
    rss = diagnostics.process_footprint(os.getpid())

    assert rss is not None
    assert rss > 1_000_000, f"implausibly small RSS for a live interpreter: {rss}"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "POSIX-only guarantee: Popen holds the Win32 process handle open after "
        "wait(), so the process object survives, OpenProcess succeeds, and "
        "Windows reports a residual working set (~32 KB) rather than nothing"
    ),
)
def test_process_footprint_returns_none_for_a_reaped_process() -> None:
    victim = subprocess.Popen([sys.executable, "-c", "pass"])
    victim.wait()

    assert diagnostics.process_footprint(victim.pid) is None


def test_process_footprint_returns_none_on_unknown_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diagnostics.sys, "platform", "sunos5")
    assert diagnostics.process_footprint(1) is None


# --------------------------------------------------------------------------
# DiagnosticsSampler
# --------------------------------------------------------------------------


def _sampler(tmp_path: Path, **kwargs: Any) -> diagnostics.DiagnosticsSampler:
    kwargs.setdefault("clock", lambda: 1_760_000_000.0)
    return diagnostics.DiagnosticsSampler(tmp_path, **kwargs)


def test_registers_own_process_as_daemon(tmp_path: Path) -> None:
    sampler = _sampler(tmp_path)

    roles = {s.role for s in sampler.sample()}
    assert roles == {"daemon"}


def test_sample_reports_registered_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: pid * 10)
    sampler = _sampler(tmp_path)
    sampler.register(4321, "mpv")

    by_role = {s.role: s for s in sampler.sample()}
    assert by_role["mpv"].pid == 4321
    assert by_role["mpv"].footprint_bytes == 43210


def test_unregister_drops_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 1)
    sampler = _sampler(tmp_path)
    sampler.register(4321, "mpv")
    sampler.unregister(4321)

    assert "mpv" not in {s.role for s in sampler.sample()}


def test_unregister_is_forgiving_of_unknown_pids(tmp_path: Path) -> None:
    sampler = _sampler(tmp_path)
    sampler.unregister(999_999)  # must not raise


def test_dead_process_is_reported_as_none_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A sampler that raises when a child exits mid-read would take the daemon
    # with it. Absent RSS is data ("process gone"), not a failure.
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: None)
    sampler = _sampler(tmp_path)
    sampler.register(4321, "mpv")

    assert all(s.footprint_bytes is None for s in sampler.sample())


def test_resolver_supplies_a_pid_that_does_not_exist_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # mpv is spawned lazily on first playback, so its pid is unknown when the
    # sampler starts. A resolver is re-read every tick instead.
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 7)
    sampler = _sampler(tmp_path)
    pid_box: list[int | None] = [None]
    sampler.register_resolver("mpv", lambda: pid_box[0])

    assert "mpv" not in {s.role for s in sampler.sample()}

    pid_box[0] = 5555
    by_role = {s.role: s for s in sampler.sample()}
    assert by_role["mpv"].pid == 5555


def test_metrics_are_recorded_alongside_rss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KAMP-718 needs mpv's demuxer cache size next to mpv's RSS in the same tick.

    Attributing how much of a process's memory one subsystem accounts for means
    reading both at the same instant; two logs sampled independently cannot be
    lined up afterwards.
    """
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 1024)
    sampler = _sampler(tmp_path)
    sampler.register_metric("mpv_demuxer_cache_bytes", lambda: 12_345)

    sampler.write_sample()

    record = json.loads(sampler.current_path().read_text().strip())
    assert record["metrics"] == {"mpv_demuxer_cache_bytes": 12_345}


def test_metrics_key_is_absent_when_none_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Absent rather than empty, so a reader can tell "not instrumented" from
    # "instrumented and measured zero".
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 1024)
    sampler = _sampler(tmp_path)

    sampler.write_sample()

    assert "metrics" not in json.loads(sampler.current_path().read_text().strip())


def test_metric_that_raises_does_not_break_the_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 1024)
    sampler = _sampler(tmp_path)

    def boom() -> float:
        raise RuntimeError("engine torn down mid-read")

    sampler.register_metric("broken", boom)
    sampler.register_metric("fine", lambda: 7)

    sampler.write_sample()

    record = json.loads(sampler.current_path().read_text().strip())
    # The healthy metric still lands; the broken one is simply absent.
    assert record["metrics"] == {"fine": 7}
    assert record["procs"], "RSS sampling must be unaffected by a bad metric"


def test_metric_returning_none_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # mpv is not always running; "no reading" is absent, not zero.
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 1024)
    sampler = _sampler(tmp_path)
    sampler.register_metric("mpv_demuxer_cache_bytes", lambda: None)

    sampler.write_sample()

    assert "metrics" not in json.loads(sampler.current_path().read_text().strip())


def test_resolver_that_raises_does_not_break_the_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 7)
    sampler = _sampler(tmp_path)

    def boom() -> int | None:
        raise RuntimeError("engine torn down")

    sampler.register_resolver("mpv", boom)

    assert {s.role for s in sampler.sample()} == {"daemon"}


def test_write_sample_appends_one_json_line_per_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 2048)
    sampler = _sampler(tmp_path)
    sampler.register(4321, "mpv")

    sampler.write_sample()
    sampler.write_sample()

    lines = sampler.current_path().read_text().strip().splitlines()
    assert len(lines) == 2

    record = json.loads(lines[0])
    assert record["t"] == 1_760_000_000.0
    assert {p["role"] for p in record["procs"]} == {"daemon", "mpv"}
    assert all(p["footprint_bytes"] == 2048 for p in record["procs"])


def test_log_file_is_named_by_date_so_long_runs_rotate(tmp_path: Path) -> None:
    # 1760000000 is 2025-10-09 UTC; a multi-day capture must not land in one
    # unbounded file.
    day_one = _sampler(tmp_path, clock=lambda: 1_760_000_000.0)
    day_two = _sampler(tmp_path, clock=lambda: 1_760_000_000.0 + 86_400)

    assert day_one.current_path() != day_two.current_path()
    assert day_one.current_path().name == "memory-2025-10-09.jsonl"


def test_write_sample_creates_the_output_directory(tmp_path: Path) -> None:
    nested = tmp_path / "diagnostics"
    sampler = diagnostics.DiagnosticsSampler(nested, clock=lambda: 1_760_000_000.0)

    sampler.write_sample()

    assert sampler.current_path().exists()


def test_write_errors_are_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Diagnostics must never be the reason the daemon falls over.
    sampler = _sampler(tmp_path)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)
    sampler.write_sample()  # must not raise


def test_start_samples_then_stop_joins_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 512)
    wrote = threading.Event()
    sampler = _sampler(tmp_path, interval_seconds=0.01)
    real_write = sampler.write_sample

    def write_and_signal() -> None:
        real_write()
        wrote.set()

    monkeypatch.setattr(sampler, "write_sample", write_and_signal)

    sampler.start()
    try:
        assert wrote.wait(timeout=5.0), "sampler thread never wrote a sample"
    finally:
        sampler.stop()

    assert sampler.current_path().exists()


def test_stop_is_safe_without_start(tmp_path: Path) -> None:
    _sampler(tmp_path).stop()  # must not raise


def test_start_twice_runs_a_single_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 1)
    sampler = _sampler(tmp_path, interval_seconds=60.0)

    sampler.start()
    first = sampler._thread
    sampler.start()
    try:
        assert sampler._thread is first
    finally:
        sampler.stop()


def test_stop_waits_out_a_long_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # stop() must interrupt the wait rather than block for the full interval,
    # or daemon shutdown stalls for minutes.
    monkeypatch.setattr(diagnostics, "process_footprint", lambda pid: 1)
    sampler = _sampler(tmp_path, interval_seconds=3600.0)
    sampler.start()
    sampler.stop()

    assert sampler._thread is None
