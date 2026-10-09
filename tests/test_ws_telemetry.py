"""The WebSocket sink must not grow with the producer's rate (KAMP-717).

`_notify_audio_level` fires at ~20 Hz from the engine's stdout reader thread and
every event was `put_nowait` onto an UNBOUNDED `asyncio.Queue` per client, while
the drain loop sends one event per iteration behind `await ws.send_json`. It
cannot sustain 20 Hz, so the backlog grew without bound.

Measured before the fix, broadcasting through the real app with a client that
does not read: 59,491 of 60,000 events retained, `gc.collect()` freeing none —
live objects held by the queue, not allocator churn. That scales to ~460 MiB over
a 16-hour session, against ~560 MiB of observed daemon drift.

So these tests are about a *property*, not a mechanism: the sink's size must stay
bounded no matter how fast events arrive. A test that only checked "levels are
delivered" would have passed before the fix.
"""

from __future__ import annotations

import asyncio

import pytest

from kamp_core.server import _ClientSink


def _level(db: float = -18.0) -> dict[str, object]:
    return {
        "type": "audio.level",
        "left_db": db,
        "right_db": db,
        "crest_db": 12.0,
        "peak_db": -6.0,
    }


# --------------------------------------------------------------------------
# Coalescing: the whole point
# --------------------------------------------------------------------------


def test_audio_levels_do_not_accumulate() -> None:
    """20,000 levels must not occupy 20,000 slots.

    This is the regression that mattered: the old queue held one entry per event.
    """
    sink = _ClientSink()

    for i in range(20_000):
        sink.push(_level(-float(i % 60)))

    assert sink.pending() <= 1, f"expected coalescing, got {sink.pending()} pending"


def test_coalescing_keeps_the_newest_level() -> None:
    # A stale VU reading is worthless — the meter should show the latest value,
    # not work through a backlog.
    sink = _ClientSink()

    sink.push(_level(-30.0))
    sink.push(_level(-20.0))
    sink.push(_level(-10.0))

    event = (
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(sink.get())
    )
    assert event["left_db"] == pytest.approx(-10.0)


def test_level_slot_refills_after_delivery() -> None:
    """Delivering a level must re-arm the slot, or the meter freezes after one
    frame — the failure mode of a naive 'only queue it once' fix."""
    sink = _ClientSink()
    loop = asyncio.new_event_loop()
    try:
        sink.push(_level(-30.0))
        first = loop.run_until_complete(sink.get())
        assert first["left_db"] == pytest.approx(-30.0)

        sink.push(_level(-25.0))
        second = loop.run_until_complete(sink.get())
        assert second["left_db"] == pytest.approx(-25.0)
    finally:
        loop.close()


def test_no_level_pending_once_delivered() -> None:
    sink = _ClientSink()
    loop = asyncio.new_event_loop()
    try:
        sink.push(_level())
        loop.run_until_complete(sink.get())
        assert sink.pending() == 0
    finally:
        loop.close()


# --------------------------------------------------------------------------
# The general queue stays bounded too
# --------------------------------------------------------------------------


def test_regular_events_are_bounded() -> None:
    """Defence in depth: the coalescing above removes today's 20 Hz flood, but a
    future high-frequency event must not be able to do this again."""
    sink = _ClientSink(maxsize=8)

    for i in range(500):
        sink.push({"type": "library.changed", "n": i})

    assert sink.pending() <= 8


def test_overflow_drops_the_oldest_not_the_newest() -> None:
    # These are idempotent refresh signals, so recency is what matters: the
    # newest "library.changed" is the one that reflects reality.
    sink = _ClientSink(maxsize=4)
    loop = asyncio.new_event_loop()
    try:
        for i in range(10):
            sink.push({"type": "library.changed", "n": i})

        seen = [loop.run_until_complete(sink.get())["n"] for _ in range(4)]
        assert seen == [6, 7, 8, 9], seen
    finally:
        loop.close()


def test_levels_do_not_evict_regular_events() -> None:
    """The level slot is separate storage, so a burst of telemetry must not push
    a library.changed out of the queue."""
    sink = _ClientSink(maxsize=4)
    loop = asyncio.new_event_loop()
    try:
        sink.push({"type": "library.changed", "n": 1})
        for _ in range(10_000):
            sink.push(_level())

        kinds = {loop.run_until_complete(sink.get())["type"] for _ in range(2)}
        assert "library.changed" in kinds
    finally:
        loop.close()


# --------------------------------------------------------------------------
# Delivery order and blocking
# --------------------------------------------------------------------------


def test_get_blocks_until_something_arrives() -> None:
    sink = _ClientSink()
    loop = asyncio.new_event_loop()
    try:

        async def scenario() -> dict[str, object]:
            task = asyncio.ensure_future(sink.get())
            await asyncio.sleep(0)
            assert not task.done(), "get() must wait when the sink is empty"
            sink.push({"type": "library.changed"})
            return await asyncio.wait_for(task, timeout=1.0)

        event = loop.run_until_complete(scenario())
        assert event["type"] == "library.changed"
    finally:
        loop.close()


def test_regular_events_are_delivered_in_order() -> None:
    sink = _ClientSink()
    loop = asyncio.new_event_loop()
    try:
        for i in range(5):
            sink.push({"type": "pipeline.stage", "n": i})

        seen = [loop.run_until_complete(sink.get())["n"] for _ in range(5)]
        assert seen == [0, 1, 2, 3, 4]
    finally:
        loop.close()
