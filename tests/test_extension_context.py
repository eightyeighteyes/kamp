"""Tests for KampGround context (context.py)."""

from __future__ import annotations

import io
import pickle
from http.client import HTTPMessage
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.ext.context import FetchResponse, KampGround, PlaybackSnapshot
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


# ---------------------------------------------------------------------------
# AC #1 / AC #2 / AC #3 — KampGround.fetch()
# ---------------------------------------------------------------------------


def _make_urlopen_mock(
    status: int = 200,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> MagicMock:
    """Build a context-manager mock that urlopen returns."""
    msg = HTTPMessage()
    for k, v in (headers or {}).items():
        msg[k] = v
    resp = MagicMock()
    resp.status = status
    resp.headers = msg
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_allowed_domain_returns_fetch_response() -> None:
    ctx = KampGround(allowed_domains=frozenset(["example.com"]))
    mock_resp = _make_urlopen_mock(status=200, body=b"hello")
    with patch("kamp_daemon.ext.context.urlopen", return_value=mock_resp):
        result = ctx.fetch("https://example.com/api")
    assert isinstance(result, FetchResponse)
    assert result.status_code == 200
    assert result.body == b"hello"


def test_fetch_disallowed_domain_raises_permission_error() -> None:
    ctx = KampGround(allowed_domains=frozenset(["safe.example.com"]))
    with pytest.raises(PermissionError, match="evil.com"):
        ctx.fetch("https://evil.com/steal")


def test_fetch_empty_allowlist_blocks_all_domains() -> None:
    ctx = KampGround()  # allowed_domains defaults to frozenset()
    with pytest.raises(PermissionError):
        ctx.fetch("https://example.com/api")


def test_fetch_forwards_method_and_body() -> None:
    ctx = KampGround(allowed_domains=frozenset(["api.example.com"]))
    mock_resp = _make_urlopen_mock(body=b"{}")
    captured: list[Any] = []

    def _capture(req: Any) -> Any:
        captured.append(req)
        return mock_resp

    with patch("kamp_daemon.ext.context.urlopen", side_effect=_capture):
        ctx.fetch("https://api.example.com/data", method="POST", body=b'{"x":1}')

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert req.data == b'{"x":1}'


def test_fetch_response_headers_populated() -> None:
    ctx = KampGround(allowed_domains=frozenset(["cdn.example.com"]))
    mock_resp = _make_urlopen_mock(
        headers={"Content-Type": "application/json"}, body=b"[]"
    )
    with patch("kamp_daemon.ext.context.urlopen", return_value=mock_resp):
        result = ctx.fetch("https://cdn.example.com/feed")
    assert "Content-Type" in result.headers


def test_fetch_response_pickles() -> None:
    resp = FetchResponse(
        status_code=200,
        headers={"Content-Type": "text/plain"},
        body=b"data",
    )
    restored = pickle.loads(pickle.dumps(resp))
    assert restored.status_code == 200
    assert restored.body == b"data"
