"""Tests for KampGround context (context.py)."""

from __future__ import annotations

import pickle
from typing import Any
from unittest.mock import patch

from kamp_daemon.ext.context import KampGround, PlaybackSnapshot
from kamp_daemon.ext.types import ArtworkQuery, TrackMetadata
from kamp_daemon.ext.worker import invoke_extension

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track(
    title: str, artist: str = "Artist", album: str = "Album"
) -> TrackMetadata:
    return TrackMetadata(
        title=title,
        artist=artist,
        album=album,
        album_artist=artist,
        year="2000",
        track_number=1,
        mbid="mbid-" + title,
    )


# ---------------------------------------------------------------------------
# AC #1 — library query methods
# ---------------------------------------------------------------------------


def test_search_returns_all_when_query_empty() -> None:
    tracks = [_make_track("A"), _make_track("B")]
    ctx = KampGround(library_tracks=tracks)
    assert ctx.search("") == tracks


def test_search_filters_by_title() -> None:
    t1 = _make_track("Alright")
    t2 = _make_track("Money Trees")
    ctx = KampGround(library_tracks=[t1, t2])
    assert ctx.search("alright") == [t1]


def test_search_filters_by_artist() -> None:
    t1 = _make_track("Song", artist="Kendrick Lamar")
    t2 = _make_track("Song", artist="J. Cole")
    ctx = KampGround(library_tracks=[t1, t2])
    assert ctx.search("kendrick") == [t1]


def test_search_filters_by_album() -> None:
    t1 = _make_track("Track", album="Madvillainy")
    t2 = _make_track("Track", album="MM..FOOD")
    ctx = KampGround(library_tracks=[t1, t2])
    assert ctx.search("madvillainy") == [t1]


def test_search_is_case_insensitive() -> None:
    t = _make_track("ALRIGHT")
    ctx = KampGround(library_tracks=[t])
    assert ctx.search("alright") == [t]


def test_search_returns_empty_list_when_no_match() -> None:
    ctx = KampGround(library_tracks=[_make_track("Something")])
    assert ctx.search("zzznomatch") == []


# ---------------------------------------------------------------------------
# AC #2 — playback state
# ---------------------------------------------------------------------------


def test_default_playback_snapshot() -> None:
    ctx = KampGround()
    assert ctx.playback.playing is False
    assert ctx.playback.position == 0.0
    assert ctx.playback.duration == 0.0
    assert ctx.playback.volume == 100


def test_playback_snapshot_fields() -> None:
    snap = PlaybackSnapshot(playing=True, position=42.5, duration=180.0, volume=75)
    ctx = KampGround(playback=snap)
    assert ctx.playback.playing is True
    assert ctx.playback.position == 42.5


# ---------------------------------------------------------------------------
# AC #3 — event subscription
# ---------------------------------------------------------------------------


def test_subscribe_and_fire() -> None:
    fired: list[str] = []
    ctx = KampGround()
    ctx.subscribe("track_start", lambda: fired.append("start"))
    ctx.subscribe("track_start", lambda: fired.append("start2"))
    ctx.fire("track_start")
    assert fired == ["start", "start2"]


def test_fire_unknown_event_is_noop() -> None:
    ctx = KampGround()
    ctx.fire("nonexistent")  # must not raise


def test_subscribe_multiple_events() -> None:
    fired: list[str] = []
    ctx = KampGround()
    ctx.subscribe("track_start", lambda: fired.append("start"))
    ctx.subscribe("track_end", lambda: fired.append("end"))
    ctx.fire("track_end")
    assert fired == ["end"]


# ---------------------------------------------------------------------------
# AC #4 / AC #5 — picklable; no internal daemon objects
# ---------------------------------------------------------------------------


def test_kampground_pickles_cleanly() -> None:
    ctx = KampGround(
        playback=PlaybackSnapshot(
            playing=True, position=10.0, duration=200.0, volume=80
        ),
        library_tracks=[_make_track("Alright")],
    )
    restored = pickle.loads(pickle.dumps(ctx))
    assert restored.playback.playing is True
    assert restored.library_tracks[0].title == "Alright"


def test_playback_snapshot_pickles() -> None:
    snap = PlaybackSnapshot(playing=False, position=0.0, duration=0.0, volume=100)
    assert pickle.loads(pickle.dumps(snap)) == snap


# ---------------------------------------------------------------------------
# Context passed through to extension constructor via invoke_extension
# ---------------------------------------------------------------------------


def test_context_reaches_extension_constructor() -> None:
    """KampGround passed to invoke_extension is forwarded to cls(ctx)."""
    received: list[KampGround] = []

    import queue as _queue_module
    from kamp_daemon.ext.worker import _extension_worker

    class _FakeProc:
        exitcode = 0

        def join(self, timeout: object = None) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    class _CtxCapture:
        def __init__(self, ctx: KampGround) -> None:
            received.append(ctx)

        def run(self) -> None:
            pass

    def _inline(
        cls: type, method_name: str, args: tuple[Any, ...], ctx: KampGround
    ) -> tuple[Any, Any, Any]:
        log_q: _queue_module.Queue[Any] = _queue_module.Queue()
        result_q: _queue_module.Queue[Any] = _queue_module.Queue()
        _extension_worker(cls, method_name, args, ctx, log_q, result_q)
        return _FakeProc(), log_q, result_q

    ctx = KampGround(library_tracks=[_make_track("Test Track")])
    with patch("kamp_daemon.ext.worker._spawn_extension_worker", side_effect=_inline):
        invoke_extension(_CtxCapture, "run", ctx=ctx)

    assert len(received) == 1
    assert received[0].library_tracks[0].title == "Test Track"


def test_default_context_used_when_omitted() -> None:
    """invoke_extension works without an explicit ctx argument."""

    import queue as _queue_module
    from kamp_daemon.ext.worker import _extension_worker

    class _FakeProc:
        exitcode = 0

        def join(self, timeout: object = None) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    class _NullExt:
        def __init__(self, ctx: KampGround) -> None:
            pass

        def run(self) -> None:
            pass

    def _inline(
        cls: type, method_name: str, args: tuple[Any, ...], ctx: KampGround
    ) -> tuple[Any, Any, Any]:
        log_q: _queue_module.Queue[Any] = _queue_module.Queue()
        result_q: _queue_module.Queue[Any] = _queue_module.Queue()
        _extension_worker(cls, method_name, args, ctx, log_q, result_q)
        return _FakeProc(), log_q, result_q

    with patch("kamp_daemon.ext.worker._spawn_extension_worker", side_effect=_inline):
        result = invoke_extension(_NullExt, "run")  # no ctx kwarg
    assert result is True
