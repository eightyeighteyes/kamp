"""Tests for the tracemalloc layer that attributes the daemon's growth (KAMP-680).

KAMP-716's RSS sampler answered *which process* (the Python daemon: 65 -> 1112 MiB
over 11.6h, never releasing a byte). This layer answers the next question, and it
is a fork in the road rather than a detail:

    RSS high + traced_current high  -> live objects are being retained; the top
                                       allocation sites name the culprit.
    RSS high + traced_current low   -> Python freed it and the allocator kept the
                                       pages; no amount of reference-hunting will
                                       help, and the fix is subprocess isolation.

The CLAUDE.md memory lesson is the second case: the ``sys.modules`` eviction work
passed every mechanism test while leaving pymalloc pages resident. Telling the two
apart before writing a fix is the entire point of this code.
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path
from typing import Any, Iterator

import pytest

from kamp_core import diagnostics


@pytest.fixture(autouse=True)
def _no_leaked_tracing() -> Iterator[None]:
    """Never leave tracemalloc running — it slows every later test in the session."""
    was = tracemalloc.is_tracing()
    yield
    if tracemalloc.is_tracing() and not was:
        tracemalloc.stop()


# --------------------------------------------------------------------------
# env gate
# --------------------------------------------------------------------------


def test_tracemalloc_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAMP_DIAGNOSTICS_TRACEMALLOC", raising=False)
    assert diagnostics.tracemalloc_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_tracemalloc_enabled_by_env(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("KAMP_DIAGNOSTICS_TRACEMALLOC", value)
    assert diagnostics.tracemalloc_enabled() is True


def test_tracemalloc_gate_is_independent_of_the_rss_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tracing costs real overhead, so it must not ride along on KAMP_DIAGNOSTICS.
    monkeypatch.setenv("KAMP_DIAGNOSTICS", "1")
    monkeypatch.delenv("KAMP_DIAGNOSTICS_TRACEMALLOC", raising=False)

    assert diagnostics.enabled() is True
    assert diagnostics.tracemalloc_enabled() is False


# --------------------------------------------------------------------------
# start / read
# --------------------------------------------------------------------------


def test_start_tracemalloc_begins_tracing() -> None:
    diagnostics.start_tracemalloc()
    assert tracemalloc.is_tracing()


def test_start_tracemalloc_is_idempotent() -> None:
    diagnostics.start_tracemalloc()
    diagnostics.start_tracemalloc()  # must not raise
    assert tracemalloc.is_tracing()


def test_traced_memory_is_none_when_not_tracing() -> None:
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    assert diagnostics.traced_memory() is None


def test_traced_memory_reports_current_and_peak() -> None:
    diagnostics.start_tracemalloc()
    hog = [bytearray(200_000) for _ in range(20)]  # ~4 MB, kept alive

    traced = diagnostics.traced_memory()
    assert traced is not None
    current, peak = traced
    assert (
        current > 1_000_000
    ), f"expected to see the 4 MB we are holding, got {current}"
    assert peak >= current
    del hog


def test_traced_memory_falls_when_the_reference_is_dropped() -> None:
    # The property that makes this layer able to tell retention from churn: a
    # freed allocation must stop counting toward `current`.
    diagnostics.start_tracemalloc()
    hog = [bytearray(200_000) for _ in range(20)]
    before = diagnostics.traced_memory()
    del hog
    after = diagnostics.traced_memory()

    assert before is not None and after is not None
    assert after[0] < before[0]


# --------------------------------------------------------------------------
# top_allocations
# --------------------------------------------------------------------------


def test_top_allocations_is_empty_when_not_tracing() -> None:
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    assert diagnostics.top_allocations() == []


def test_top_allocations_names_the_allocating_site() -> None:
    diagnostics.start_tracemalloc()
    hog = [bytearray(300_000) for _ in range(20)]  # ~6 MB on this line

    rows = diagnostics.top_allocations(limit=5)
    del hog

    assert rows, "expected at least one allocation site"
    assert len(rows) <= 5
    assert {"file", "line", "size_bytes", "count"} <= set(rows[0])
    # Sorted biggest-first, which is the only ordering that makes the log useful.
    assert rows == sorted(rows, key=lambda r: r["size_bytes"], reverse=True)
    # This test file should be the largest allocator in its own process.
    assert any(Path(r["file"]).name == Path(__file__).name for r in rows)


def test_top_allocations_respects_the_limit() -> None:
    diagnostics.start_tracemalloc()
    assert len(diagnostics.top_allocations(limit=3)) <= 3


# --------------------------------------------------------------------------
# sampler integration
# --------------------------------------------------------------------------


def _sampler(tmp_path: Path, **kw: Any) -> diagnostics.DiagnosticsSampler:
    kw.setdefault("clock", lambda: 1_760_000_000.0)
    return diagnostics.DiagnosticsSampler(tmp_path, **kw)


def test_record_omits_traced_fields_when_not_tracing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    monkeypatch.setattr(diagnostics, "process_rss", lambda pid: 1024)
    sampler = _sampler(tmp_path)

    sampler.write_sample()

    record = json.loads(sampler.current_path().read_text().strip())
    assert "traced_current_bytes" not in record


def test_record_carries_traced_totals_when_tracing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diagnostics, "process_rss", lambda pid: 1024)
    diagnostics.start_tracemalloc()
    sampler = _sampler(tmp_path)

    sampler.write_sample()

    record = json.loads(sampler.current_path().read_text().strip())
    # Both are needed: the RSS-vs-traced comparison is the whole diagnosis.
    assert record["traced_current_bytes"] > 0
    assert record["traced_peak_bytes"] >= record["traced_current_bytes"]


def test_allocation_snapshot_writes_its_own_log(tmp_path: Path) -> None:
    diagnostics.start_tracemalloc()
    sampler = _sampler(tmp_path)

    sampler.write_allocation_snapshot(limit=5)

    path = sampler.alloc_path()
    record = json.loads(path.read_text().strip())
    assert record["t"] == 1_760_000_000.0
    assert len(record["top"]) <= 5
    assert record["top"][0]["size_bytes"] > 0


def test_allocation_snapshot_is_a_no_op_when_not_tracing(tmp_path: Path) -> None:
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    sampler = _sampler(tmp_path)

    sampler.write_allocation_snapshot()

    # No file at all, rather than a file full of empty records.
    assert not sampler.alloc_path().exists()


def test_allocation_log_is_a_separate_file_from_the_rss_log(tmp_path: Path) -> None:
    sampler = _sampler(tmp_path)
    assert sampler.alloc_path() != sampler.current_path()


def test_burst_mode_samples_fast_then_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The startup ramp is the informative part: at a flat 60s cadence KAMP-680's
    # growth was a single 64 -> 804 MiB jump between two ticks.
    monkeypatch.setattr(diagnostics, "process_rss", lambda pid: 1)
    waits: list[float] = []
    sampler = _sampler(
        tmp_path, interval_seconds=60.0, burst_ticks=3, burst_interval_seconds=0.01
    )

    def record_wait(timeout: float | None = None) -> bool:
        waits.append(float(timeout or 0))
        # Stop once the burst has elapsed and one steady wait is observed.
        return len(waits) >= 3

    monkeypatch.setattr(sampler._stop, "wait", record_wait)
    sampler._run()

    # burst_ticks=3 means three burst *samples*, so two waits at the burst
    # interval; the wait after the last burst sample is already the steady one.
    assert waits == [0.01, 0.01, 60.0], waits


def test_burst_mode_snapshots_every_tick_not_just_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The original cadence snapshotted tick 0 (before any startup work) and then
    # tick 10, so a short run captured one empty snapshot. A churner is only
    # visible near traced-peak, so the burst has to snapshot every tick.
    monkeypatch.setattr(diagnostics, "process_rss", lambda pid: 1)
    diagnostics.start_tracemalloc()
    taken: list[int] = []
    sampler = _sampler(
        tmp_path, interval_seconds=60.0, burst_ticks=3, burst_interval_seconds=0.01
    )
    monkeypatch.setattr(
        sampler, "write_allocation_snapshot", lambda *a, **k: taken.append(1)
    )
    monkeypatch.setattr(sampler._stop, "wait", lambda t=None: len(taken) >= 3)

    sampler._run()

    assert len(taken) == 3, f"expected a snapshot on each burst tick, got {len(taken)}"


def test_allocation_snapshot_errors_are_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostics.start_tracemalloc()
    sampler = _sampler(tmp_path)

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)
    sampler.write_allocation_snapshot()  # must not raise
