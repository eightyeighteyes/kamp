"""Tests for kamp_core.playback (PlaybackQueue and MpvPlaybackEngine)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from kamp_core.library import Track
from kamp_core.playback import (
    MpvPlaybackEngine,
    PlaybackQueue,
    PlaybackState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _track(n: int) -> Track:
    return Track(
        file_path=Path(f"/music/{n:02d}.mp3"),
        title=f"Track {n}",
        artist="Artist",
        album_artist="Artist",
        album="Album",
        year="2024",
        track_number=n,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
    )


# ---------------------------------------------------------------------------
# PlaybackQueue
# ---------------------------------------------------------------------------


class TestPlaybackQueue:
    def test_empty_queue_has_no_current(self) -> None:
        assert PlaybackQueue().current() is None

    def test_load_sets_current_to_first_track(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        assert q.current() == tracks[0]

    def test_load_with_start_index(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks, start_index=2)
        assert q.current() == tracks[2]

    def test_next_advances(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        assert q.next() == tracks[1]
        assert q.current() == tracks[1]

    def test_next_at_end_returns_none(self) -> None:
        q = PlaybackQueue()
        q.load([_track(1)])
        assert q.next() is None
        assert q.current() is None

    def test_next_at_end_wraps_when_repeat(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        q.set_repeat(True)
        q.next()
        q.next()
        assert q.next() == tracks[0]

    def test_prev_goes_back(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        q.next()
        assert q.prev() == tracks[0]

    def test_prev_at_start_returns_none(self) -> None:
        q = PlaybackQueue()
        q.load([_track(1)])
        assert q.prev() is None

    def test_prev_at_start_wraps_when_repeat(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        q.set_repeat(True)
        assert q.prev() == tracks[2]

    def test_shuffle_randomises_order(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(20)]
        q.load(tracks)
        q.set_shuffle(True)
        # Advance through the full queue and confirm all tracks appear exactly once
        current = q.current()
        assert current is not None
        seen = {current.title}
        for _ in range(19):
            nxt = q.next()
            assert nxt is not None
            seen.add(nxt.title)
        assert len(seen) == 20

    def test_shuffle_off_restores_original_order(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(5)]
        q.load(tracks)
        q.set_shuffle(True)
        q.set_shuffle(False)
        # After turning shuffle off, next() should follow original order from current
        current = q.current()
        assert current is not None
        original_idx = tracks.index(current)
        if original_idx < len(tracks) - 1:
            assert q.next() == tracks[original_idx + 1]

    def test_load_empty_list_clears_queue(self) -> None:
        q = PlaybackQueue()
        q.load([_track(1)])
        q.load([])
        assert q.current() is None

    def test_load_with_shuffle_active_places_track_first(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(5)]
        q.load(tracks)
        q.set_shuffle(True)
        # Load a new set while shuffle is already on; current should be tracks[0]
        q.load(tracks, start_index=0)
        assert q.current() == tracks[0]

    def test_next_returns_none_on_empty_queue(self) -> None:
        assert PlaybackQueue().next() is None

    def test_prev_returns_none_on_empty_queue(self) -> None:
        assert PlaybackQueue().prev() is None

    def test_set_shuffle_noop_when_same_value(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        before = q.current()
        q.set_shuffle(False)  # already False — should be a no-op
        assert q.current() == before

    def test_set_shuffle_noop_when_no_tracks(self) -> None:
        q = PlaybackQueue()
        q.set_shuffle(True)  # no tracks loaded — should not raise
        assert q.current() is None


# ---------------------------------------------------------------------------
# MpvPlaybackEngine
# ---------------------------------------------------------------------------


def _make_engine() -> tuple[MpvPlaybackEngine, MagicMock]:
    """Return an MpvPlaybackEngine with a patched _send_command."""
    with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
        engine = MpvPlaybackEngine()
    send = MagicMock(return_value=None)
    engine._send_command = send  # type: ignore[method-assign]
    return engine, send


class TestMpvPlaybackEngine:
    def test_play_sends_loadfile_command(self) -> None:
        engine, send = _make_engine()
        engine.play(Path("/music/01.mp3"))
        send.assert_called_once_with("loadfile", "/music/01.mp3", "replace")

    def test_pause_sets_pause_true(self) -> None:
        engine, send = _make_engine()
        engine.pause()
        send.assert_called_once_with("set_property", "pause", True)

    def test_resume_sets_pause_false(self) -> None:
        engine, send = _make_engine()
        engine.resume()
        send.assert_called_once_with("set_property", "pause", False)

    def test_seek_sends_seek_command(self) -> None:
        engine, send = _make_engine()
        engine.seek(42.5)
        send.assert_called_once_with("seek", 42.5, "absolute")

    def test_set_volume_sends_set_property(self) -> None:
        engine, send = _make_engine()
        engine.volume = 75
        send.assert_called_once_with("set_property", "volume", 75)

    def test_stop_sends_stop_command(self) -> None:
        engine, send = _make_engine()
        engine.stop()
        send.assert_called_once_with("stop")

    def test_on_track_end_callback_is_called(self) -> None:
        engine, _ = _make_engine()
        callback = MagicMock()
        engine.on_track_end = callback

        # Simulate mpv sending an end-file event
        engine._handle_event({"event": "end-file", "reason": "eof"})

        callback.assert_called_once()

    def test_on_track_end_not_called_for_stop_reason(self) -> None:
        """User-initiated stops should not trigger the end-of-track callback."""
        engine, _ = _make_engine()
        callback = MagicMock()
        engine.on_track_end = callback

        engine._handle_event({"event": "end-file", "reason": "stop"})

        callback.assert_not_called()

    def test_position_updated_from_property_change_event(self) -> None:
        engine, _ = _make_engine()
        engine._handle_event(
            {"event": "property-change", "name": "time-pos", "data": 12.3}
        )
        assert engine.state.position == pytest.approx(12.3)

    def test_duration_updated_from_property_change_event(self) -> None:
        engine, _ = _make_engine()
        engine._handle_event(
            {"event": "property-change", "name": "duration", "data": 240.0}
        )
        assert engine.state.duration == pytest.approx(240.0)

    def test_pause_state_updated_from_property_change_event(self) -> None:
        engine, _ = _make_engine()
        engine._handle_event(
            {"event": "property-change", "name": "pause", "data": True}
        )
        assert engine.state.playing is False

    def test_initial_state(self) -> None:
        engine, _ = _make_engine()
        assert engine.state == PlaybackState(
            playing=False, position=0.0, duration=0.0, volume=100
        )

    def test_shutdown_terminates_process(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_proc = MagicMock()
        engine._proc = mock_proc

        engine.shutdown()

        mock_proc.terminate.assert_called_once()

    def test_shutdown_closes_socket(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_sock = MagicMock()
        engine._sock = mock_sock
        engine.shutdown()
        mock_sock.close.assert_called_once()

    def test_shutdown_ignores_socket_close_error(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_sock = MagicMock()
        mock_sock.close.side_effect = OSError("already closed")
        engine._sock = mock_sock
        engine.shutdown()  # should not raise

    def test_shutdown_handles_none_proc(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        engine._proc = None
        engine.shutdown()  # should not raise

    def test_send_command_is_noop_when_no_socket(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        engine._send_command("stop")  # _sock is None — should not raise

    def test_volume_getter_returns_state_volume(self) -> None:
        engine, _ = _make_engine()
        assert engine.volume == 100

    def test_send_command_sends_json_over_socket(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_sock = MagicMock()
        engine._sock = mock_sock
        engine._send_command("loadfile", "/music/01.mp3", "replace")
        expected = (
            json.dumps({"command": ["loadfile", "/music/01.mp3", "replace"]}) + "\n"
        )
        mock_sock.sendall.assert_called_once_with(expected.encode())

    def test_send_command_logs_warning_on_oserror(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_sock = MagicMock()
        mock_sock.sendall.side_effect = OSError("broken pipe")
        engine._sock = mock_sock
        engine._send_command("stop")  # should not raise

    def test_handle_event_unknown_event_is_ignored(self) -> None:
        engine, _ = _make_engine()
        engine._handle_event({"event": "seek"})
        assert engine.state == PlaybackState()

    def test_handle_event_pause_with_non_bool_data_is_ignored(self) -> None:
        engine, _ = _make_engine()
        engine._handle_event({"event": "property-change", "name": "pause", "data": 1})
        assert engine.state.playing is False
