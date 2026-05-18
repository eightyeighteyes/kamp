"""Tests for kamp_core.playback (PlaybackQueue and MpvPlaybackEngine)."""

from __future__ import annotations

import io
import json
import logging
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

    # ------------------------------------------------------------------
    # peek_next
    # ------------------------------------------------------------------

    def test_peek_next_returns_next_track_without_advancing(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        assert q.peek_next() == tracks[1]
        assert q.current() == tracks[0]  # position unchanged

    def test_peek_next_at_last_track_returns_none(self) -> None:
        q = PlaybackQueue()
        q.load([_track(1)])
        assert q.peek_next() is None

    def test_peek_next_at_last_track_wraps_when_repeat(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        q.set_repeat(True)
        q.next()
        q.next()  # now at last track
        assert q.peek_next() == tracks[0]

    def test_peek_next_on_empty_queue_returns_none(self) -> None:
        assert PlaybackQueue().peek_next() is None

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

    def test_skip_to_valid_position(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(5)]
        q.load(tracks)
        assert q.skip_to(3) == tracks[3]
        assert q.current() == tracks[3]

    def test_skip_to_out_of_bounds_returns_none(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        assert q.skip_to(5) is None
        assert q.current() == tracks[0]  # _pos unchanged

    def test_skip_to_negative_returns_none(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        assert q.skip_to(-1) is None

    def test_skip_to_empty_queue_returns_none(self) -> None:
        q = PlaybackQueue()
        assert q.skip_to(0) is None

    def test_clear_keeps_current_track(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(5)]
        q.load(tracks, start_index=2)
        q.clear()
        ordered, pos = q.queue_tracks()
        assert pos == 0
        assert len(ordered) == 1
        assert ordered[0] == tracks[2]

    def test_clear_with_no_playing_track_empties_queue(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        q.next()
        q.next()
        q.next()  # exhausts queue, _pos → -1
        q.clear()
        assert q.current() is None
        ordered, pos = q.queue_tracks()
        assert ordered == []
        assert pos == -1

    def test_clear_empty_queue_is_noop(self) -> None:
        q = PlaybackQueue()
        q.clear()
        assert q.current() is None

    def test_clear_remaining_drops_tracks_after_given_position(self) -> None:
        # T0 T1 T2* T3 T4 T5 T6 — playing T2, right-click T4 → T5 T6 removed
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(7)]
        q.load(tracks, start_index=2)
        q.clear_remaining(from_position=4)
        ordered, pos = q.queue_tracks()
        assert len(ordered) == 5  # T0–T4 remain
        assert ordered[pos] == tracks[2]  # current track unchanged
        assert q.next() == tracks[3]
        assert q.next() == tracks[4]
        assert q.next() is None  # T5/T6 gone

    def test_clear_remaining_from_current_position(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(5)]
        q.load(tracks, start_index=1)
        q.clear_remaining(from_position=1)
        ordered, pos = q.queue_tracks()
        assert len(ordered) == 2  # tracks[0] and tracks[1]
        assert ordered[pos] == tracks[1]
        assert q.next() is None

    def test_clear_remaining_at_last_track_is_noop(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks, start_index=2)
        q.clear_remaining(from_position=2)
        ordered, pos = q.queue_tracks()
        assert len(ordered) == 3
        assert pos == 2

    def test_clear_remaining_empty_queue_is_noop(self) -> None:
        q = PlaybackQueue()
        q.clear_remaining(from_position=0)
        assert q.current() is None

    def test_update_favorite_patches_matching_tracks(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        q.update_favorite(tracks[1].file_path, True)
        assert q._tracks[0].favorite is False
        assert q._tracks[1].favorite is True
        assert q._tracks[2].favorite is False

    def test_update_favorite_no_match_is_noop(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(2)]
        q.load(tracks)
        q.update_favorite(Path("/nonexistent.mp3"), True)  # should not raise
        assert all(not t.favorite for t in q._tracks)

    def test_update_track_path_patches_matching_track(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        old_path = tracks[1].file_path
        new_path = Path("/new/path/track.mp3")
        q.update_track_path(old_path, new_path, "New Title")
        assert q._tracks[0].file_path == tracks[0].file_path
        assert q._tracks[1].file_path == new_path
        assert q._tracks[1].title == "New Title"
        assert q._tracks[2].file_path == tracks[2].file_path

    def test_update_track_path_no_match_is_noop(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(2)]
        q.load(tracks)
        original_paths = [t.file_path for t in q._tracks]
        q.update_track_path(Path("/nonexistent.mp3"), Path("/other.mp3"), "X")
        assert [t.file_path for t in q._tracks] == original_paths

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

    def test_queue_tracks_empty(self) -> None:
        tracks, pos = PlaybackQueue().queue_tracks()
        assert tracks == []
        assert pos == -1

    def test_queue_tracks_returns_tracks_in_playback_order(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)
        tracks, pos = q.queue_tracks()
        assert tracks == ts
        assert pos == 0

    def test_queue_tracks_reflects_position_after_next(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)
        q.next()
        tracks, pos = q.queue_tracks()
        assert tracks == ts
        assert pos == 1

    def test_queue_tracks_returns_shuffled_order(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(10)]
        q.load(ts)
        q.set_shuffle(True)
        tracks, pos = q.queue_tracks()
        assert set(t.file_path for t in tracks) == {t.file_path for t in ts}
        assert pos == 0

    def test_queue_tracks_after_empty_load(self) -> None:
        q = PlaybackQueue()
        q.load([_track(1)])
        q.load([])
        tracks, pos = q.queue_tracks()
        assert tracks == []
        assert pos == -1

    def test_get_state_returns_paths_in_playback_order(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.load(tracks)
        paths, pos, shuffle, repeat = q.get_state()
        assert paths == [t.file_path for t in tracks]
        assert pos == 0
        assert shuffle is False
        assert repeat is False

    def test_get_state_bakes_in_shuffle_order(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(5)]
        q.load(tracks)
        q.set_shuffle(True)
        paths, pos, shuffle, repeat = q.get_state()
        # All paths present, shuffle flag set, current track first
        assert set(paths) == {t.file_path for t in tracks}
        assert paths[0] == tracks[0].file_path  # current anchored at pos 0
        assert shuffle is True
        assert pos == 0

    def test_get_state_empty_queue(self) -> None:
        q = PlaybackQueue()
        paths, pos, shuffle, repeat = q.get_state()
        assert paths == []
        assert pos == -1

    def test_restore_sets_all_fields(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.restore(tracks, pos=2, shuffle=True, repeat=True)
        assert q.current() == tracks[2]
        paths, pos, shuffle, repeat = q.get_state()
        assert pos == 2
        assert shuffle is True
        assert repeat is True

    def test_restore_empty_list(self) -> None:
        q = PlaybackQueue()
        q.restore([], pos=0, shuffle=False, repeat=False)
        assert q.current() is None
        paths, pos, shuffle, repeat = q.get_state()
        assert paths == []
        assert pos == -1

    def test_restore_then_next(self) -> None:
        q = PlaybackQueue()
        tracks = [_track(i) for i in range(3)]
        q.restore(tracks, pos=0, shuffle=False, repeat=False)
        nxt = q.next()
        assert nxt == tracks[1]

    # ------------------------------------------------------------------
    # add_to_queue
    # ------------------------------------------------------------------

    def test_add_to_queue_appends_track(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(2)]
        q.load(ts)
        extra = _track(99)
        q.add_to_queue(extra)
        tracks, pos = q.queue_tracks()
        assert tracks[-1] == extra
        assert len(tracks) == 3
        assert pos == 0  # current position unchanged

    def test_add_to_queue_on_empty_queue_sets_pos_to_zero(self) -> None:
        q = PlaybackQueue()
        extra = _track(1)
        q.add_to_queue(extra)
        tracks, pos = q.queue_tracks()
        assert tracks == [extra]
        assert pos == 0

    # ------------------------------------------------------------------
    # play_next
    # ------------------------------------------------------------------

    def test_play_next_inserts_after_current(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)  # pos=0
        extra = _track(99)
        q.play_next(extra)
        tracks, pos = q.queue_tracks()
        assert tracks[1] == extra
        assert pos == 0  # still on first track

    def test_play_next_on_empty_queue_sets_pos_to_zero(self) -> None:
        q = PlaybackQueue()
        extra = _track(1)
        q.play_next(extra)
        tracks, pos = q.queue_tracks()
        assert tracks == [extra]
        assert pos == 0

    def test_play_next_removes_existing_occurrence_to_avoid_ghost(self) -> None:
        # Reproduces the bug: album loaded, play_next on a track already in queue
        # should not leave the original occurrence ("ghost") behind.
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)  # _order=[0,1,2,3], pos=0
        q.play_next(ts[3])
        tracks, _ = q.queue_tracks()
        paths = [t.file_path for t in tracks]
        assert paths.count(ts[3].file_path) == 1, "track should appear exactly once"

    def test_play_next_sequential_calls_produce_correct_order(self) -> None:
        # play_next(last) then play_next(second-to-last): expected order is
        # [current, second-to-last, last] with no ghost duplicates.
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)  # pos=0, playing ts[0]
        q.play_next(ts[3])
        q.play_next(ts[2])
        tracks, pos = q.queue_tracks()
        assert pos == 0
        assert tracks[1].file_path == ts[2].file_path
        assert tracks[2].file_path == ts[3].file_path
        assert len(tracks) == 4  # no ghost — same count as loaded

    def test_play_next_from_middle_inserts_at_correct_position(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)
        q.next()  # pos=1
        extra = _track(99)
        q.play_next(extra)
        tracks, _ = q.queue_tracks()
        assert tracks[2] == extra

    # ------------------------------------------------------------------
    # move
    # ------------------------------------------------------------------

    def test_move_shifts_order(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)
        q.move(3, 1)  # move last to position 1
        tracks, _ = q.queue_tracks()
        assert tracks[1] == ts[3]

    def test_move_noop_when_same_index(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)
        q.move(1, 1)
        tracks, pos = q.queue_tracks()
        assert tracks == ts
        assert pos == 0

    def test_move_adjusts_pos_when_current_moves_forward(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)
        q.next()  # pos=1
        q.move(1, 3)  # move current track to end
        _, pos = q.queue_tracks()
        assert pos == 3

    def test_move_adjusts_pos_when_item_moves_over_current(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)
        q.next()
        q.next()  # pos=2
        q.move(0, 2)  # move item before current to current's position
        _, pos = q.queue_tracks()
        assert pos == 1  # current shifted back by one

    def test_move_raises_on_out_of_range_index(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)
        with pytest.raises(IndexError):
            q.move(0, 10)

    # ------------------------------------------------------------------
    # insert_at
    # ------------------------------------------------------------------

    def test_insert_at_places_track_at_given_position(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)
        extra = _track(99)
        q.insert_at(extra, 1)
        tracks, pos = q.queue_tracks()
        assert tracks[1] == extra
        assert len(tracks) == 4
        assert pos == 0  # current position unchanged

    def test_insert_at_before_current_shifts_pos(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)
        q.next()  # pos=1
        extra = _track(99)
        q.insert_at(extra, 0)  # insert before current
        _, pos = q.queue_tracks()
        assert pos == 2  # current shifted forward by one

    def test_insert_at_clamps_large_index(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)
        extra = _track(99)
        q.insert_at(extra, 100)  # beyond end — should append
        tracks, _ = q.queue_tracks()
        assert tracks[-1] == extra

    # ------------------------------------------------------------------
    # add_album_to_queue
    # ------------------------------------------------------------------

    def test_add_album_to_queue_appends_tracks_in_order(self) -> None:
        q = PlaybackQueue()
        album = [_track(i) for i in range(3)]
        q.add_album_to_queue(album)
        tracks, pos = q.queue_tracks()
        assert tracks == album
        assert pos == 0

    def test_add_album_to_queue_onto_existing_queue(self) -> None:
        q = PlaybackQueue()
        existing = [_track(i) for i in range(2)]
        album = [_track(10 + i) for i in range(3)]
        q.load(existing)
        q.add_album_to_queue(album)
        tracks, _ = q.queue_tracks()
        assert tracks[2:] == album

    # ------------------------------------------------------------------
    # play_album_next
    # ------------------------------------------------------------------

    def test_play_album_next_inserts_after_current_in_order(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(3)]
        q.load(ts)  # pos=0
        album = [_track(10 + i) for i in range(3)]
        q.play_album_next(album)
        tracks, pos = q.queue_tracks()
        assert pos == 0
        assert tracks[1:4] == album  # album occupies positions 1-3
        assert len(tracks) == 6

    def test_play_album_next_on_empty_queue(self) -> None:
        q = PlaybackQueue()
        album = [_track(i) for i in range(3)]
        q.play_album_next(album)
        tracks, pos = q.queue_tracks()
        assert tracks == album
        assert pos == 0

    # ------------------------------------------------------------------
    # insert_album_at
    # ------------------------------------------------------------------

    def test_insert_album_at_places_tracks_at_position(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)
        album = [_track(10 + i) for i in range(3)]
        q.insert_album_at(album, 2)
        tracks, _ = q.queue_tracks()
        assert tracks[2:5] == album
        assert len(tracks) == 7

    def test_insert_album_at_adjusts_pos_when_inserted_before_current(self) -> None:
        q = PlaybackQueue()
        ts = [_track(i) for i in range(4)]
        q.load(ts)
        q.next()
        q.next()  # pos=2
        album = [_track(10 + i) for i in range(2)]
        q.insert_album_at(album, 0)  # insert 2 tracks before current
        _, pos = q.queue_tracks()
        assert pos == 4  # shifted forward by 2


# ---------------------------------------------------------------------------
# IPC transport (Unix socket / Windows named pipe)
# ---------------------------------------------------------------------------


class TestIPCTransport:
    """Cross-platform constructor + server-arg generation.

    The actual open/recv/send paths are real I/O against mpv and are exercised
    by the integration test that boots the daemon; here we only verify the
    factory selects the right class and that each transport produces a
    server_arg in the shape mpv expects on its platform.
    """

    def test_factory_returns_unix_socket_when_platform_is_posix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("kamp_core.playback.sys.platform", "darwin")
        from kamp_core.playback import _make_ipc_transport, _UnixSocketTransport

        transport = _make_ipc_transport()
        try:
            assert isinstance(transport, _UnixSocketTransport)
        finally:
            transport.close()

    def test_factory_returns_named_pipe_when_platform_is_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("kamp_core.playback.sys.platform", "win32")
        from kamp_core.playback import (
            _make_ipc_transport,
            _WindowsNamedPipeTransport,
        )

        transport = _make_ipc_transport()
        try:
            assert isinstance(transport, _WindowsNamedPipeTransport)
        finally:
            transport.close()

    def test_unix_socket_server_arg_is_filesystem_path(self) -> None:
        from kamp_core.playback import _UnixSocketTransport

        transport = _UnixSocketTransport()
        try:
            arg = transport.server_arg
            assert arg.endswith("mpv.sock")
            assert "kamp-mpv-" in arg
            # Filesystem path: parent dir must already exist (tempfile.mkdtemp).
            assert Path(arg).parent.is_dir()
        finally:
            transport.close()

    def test_windows_named_pipe_server_arg_uses_pipe_namespace(self) -> None:
        from kamp_core.playback import _WindowsNamedPipeTransport

        transport = _WindowsNamedPipeTransport()
        try:
            arg = transport.server_arg
            # mpv on Windows binds --input-ipc-server=NAME to a Win32 named
            # pipe; using the fully-qualified \\.\pipe\NAME path keeps client
            # and server unambiguously aligned.
            assert arg.startswith(r"\\.\pipe\kamp-mpv-")
        finally:
            transport.close()

    def test_two_transports_use_distinct_server_args(self) -> None:
        """Each engine instance must get its own IPC endpoint so multiple
        daemons (e.g. dev + tests) can run side-by-side without fighting over
        a single socket/pipe name."""
        from kamp_core.playback import (
            _UnixSocketTransport,
            _WindowsNamedPipeTransport,
        )

        a = _UnixSocketTransport()
        b = _UnixSocketTransport()
        try:
            assert a.server_arg != b.server_arg
        finally:
            a.close()
            b.close()

        c = _WindowsNamedPipeTransport()
        d = _WindowsNamedPipeTransport()
        try:
            assert c.server_arg != d.server_arg
        finally:
            c.close()
            d.close()


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
        # str(Path) yields OS-native separators — assert in that form so the
        # test is platform-neutral (Windows uses backslashes).
        send.assert_any_call("loadfile", str(Path("/music/01.mp3")), "replace")

    def test_play_always_unpauses(self) -> None:
        """play() must unpause mpv so a paused engine resumes on the new track."""
        engine, send = _make_engine()
        engine.play(Path("/music/01.mp3"))
        send.assert_any_call("set_property", "pause", False)

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

    def test_seek_into_guard_window_removes_lookahead(self) -> None:
        """Seeking into the gapless danger window must remove the lookahead first
        to prevent an immediate mpv gapless transition that freezes time-pos."""
        engine, send = _make_engine()
        engine.state.duration = 240.0
        engine.preload_next(_track(2))
        send.reset_mock()
        engine.seek(235.0)  # within last 10 s
        send.assert_any_call("playlist-remove", 1)
        assert engine._lookahead_path is None

    def test_seek_into_guard_window_sends_playlist_remove_before_seek(self) -> None:
        """playlist-remove must arrive at mpv before the seek so the lookahead is
        gone before mpv repositions, preventing a premature gapless EOF."""
        engine, send = _make_engine()
        engine.state.duration = 240.0
        engine.preload_next(_track(2))
        send.reset_mock()
        calls: list[tuple[object, ...]] = []
        send.side_effect = lambda *a: calls.append(a)
        engine.seek(235.0)  # within last 10 s
        remove_idx = next(i for i, c in enumerate(calls) if c[0] == "playlist-remove")
        seek_idx = next(i for i, c in enumerate(calls) if c[0] == "seek")
        assert remove_idx < seek_idx

    def test_seek_outside_guard_window_preserves_lookahead(self) -> None:
        """Seeking to an early/middle position must NOT remove the lookahead.
        The danger window is only the last _GAPLESS_GUARD_SECS seconds; removing
        the lookahead unconditionally breaks gapless at the track's natural EOF."""
        engine, send = _make_engine()
        engine.state.duration = 240.0
        engine.preload_next(_track(2))
        send.reset_mock()
        engine.seek(60.0)  # well outside the danger window
        send.assert_called_once_with("seek", 60.0, "absolute")
        assert engine._lookahead_path is not None

    def test_seek_at_exact_guard_boundary_preserves_lookahead(self) -> None:
        """The guard uses strict '>' (matching preload_next), so a seek to exactly
        duration - _GAPLESS_GUARD_SECS is outside the danger window and must
        preserve the lookahead."""
        engine, send = _make_engine()
        engine.state.duration = 240.0
        engine.preload_next(_track(2))
        send.reset_mock()
        engine.seek(230.0)  # exactly duration - _GAPLESS_GUARD_SECS, not inside
        send.assert_called_once_with("seek", 230.0, "absolute")
        assert engine._lookahead_path is not None

    def test_seek_without_lookahead_sends_only_seek_command(self) -> None:
        """No playlist-remove should be sent when there is no active lookahead."""
        engine, send = _make_engine()
        engine.seek(42.5)
        send.assert_called_once_with("seek", 42.5, "absolute")

    def test_seek_with_unknown_duration_preserves_lookahead(self) -> None:
        """When duration is 0 (not yet received from mpv), the guard cannot
        evaluate — leave the lookahead in place and just send the seek."""
        engine, send = _make_engine()
        engine.state.duration = 0.0
        engine.preload_next(_track(2))
        send.reset_mock()
        engine.seek(235.0)
        send.assert_called_once_with("seek", 235.0, "absolute")
        assert engine._lookahead_path is not None

    def test_set_volume_sends_set_property(self) -> None:
        engine, send = _make_engine()
        engine.volume = 75
        send.assert_called_once_with("set_property", "volume", 75)

    def test_stop_pauses_and_seeks_to_start(self) -> None:
        engine, send = _make_engine()
        engine.stop()
        send.assert_any_call("set_property", "pause", True)
        send.assert_any_call("seek", 0, "absolute")

    def test_load_paused_loads_and_pauses(self) -> None:
        engine, send = _make_engine()
        engine.load_paused(Path("/music/track.mp3"))
        send.assert_any_call("loadfile", str(Path("/music/track.mp3")), "replace")
        send.assert_any_call("set_property", "pause", True)

    def test_load_paused_sets_pending_seek_when_position_nonzero(self) -> None:
        engine, _ = _make_engine()
        engine.load_paused(Path("/music/track.mp3"), 42.5)
        assert engine._pending_seek == 42.5

    def test_load_paused_no_pending_seek_when_position_is_zero(self) -> None:
        engine, _ = _make_engine()
        engine.load_paused(Path("/music/track.mp3"), 0.0)
        assert engine._pending_seek is None

    def test_load_paused_does_not_overwrite_on_file_loaded(self) -> None:
        engine, _ = _make_engine()
        callback = MagicMock()
        engine.on_file_loaded = callback
        engine.load_paused(Path("/music/track.mp3"), 42.5)
        # The external callback must be untouched — this was the regression.
        assert engine.on_file_loaded is callback

    def test_pending_seek_fires_and_clears_on_file_loaded_event(self) -> None:
        engine, send = _make_engine()
        engine.load_paused(Path("/music/track.mp3"), 42.5)
        assert engine._pending_seek == 42.5
        engine._handle_event({"event": "file-loaded"})
        send.assert_any_call("seek", 42.5, "absolute")
        assert engine._pending_seek is None  # one-shot: cleared after firing

    def test_pending_seek_fires_before_on_file_loaded_callback(self) -> None:
        """Seek must happen before the user callback so position is set first."""
        engine, send = _make_engine()
        order: list[str] = []
        send.side_effect = lambda *a: order.append(str(a))
        callback = MagicMock(side_effect=lambda: order.append("callback"))
        engine.on_file_loaded = callback
        engine.load_paused(Path("/music/track.mp3"), 42.5)
        engine._handle_event({"event": "file-loaded"})
        seek_idx = next(i for i, s in enumerate(order) if "seek" in s)
        cb_idx = order.index("callback")
        assert seek_idx < cb_idx

    def test_file_loaded_event_triggers_on_file_loaded_callback(self) -> None:
        engine, _ = _make_engine()
        callback = MagicMock()
        engine.on_file_loaded = callback
        engine._handle_event({"event": "file-loaded"})
        callback.assert_called_once()

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

    def test_on_play_state_changed_fires_when_pause_flips(self) -> None:
        engine, _ = _make_engine()
        callback = MagicMock()
        engine.on_play_state_changed = callback
        # Start paused (default), then unpause.
        engine._handle_event(
            {"event": "property-change", "name": "pause", "data": False}
        )
        callback.assert_called_once()

    def test_on_play_state_changed_not_fired_when_pause_unchanged(self) -> None:
        """Callback must not fire when the pause state does not actually change."""
        engine, _ = _make_engine()
        callback = MagicMock()
        engine.on_play_state_changed = callback
        # Initial state is playing=False (paused). Sending pause=True is a no-op.
        engine._handle_event(
            {"event": "property-change", "name": "pause", "data": True}
        )
        callback.assert_not_called()

    def test_on_play_state_changed_fires_on_each_flip(self) -> None:
        engine, _ = _make_engine()
        callback = MagicMock()
        engine.on_play_state_changed = callback
        engine._handle_event(
            {"event": "property-change", "name": "pause", "data": False}
        )  # paused→playing
        engine._handle_event(
            {"event": "property-change", "name": "pause", "data": True}
        )  # playing→paused
        assert callback.call_count == 2

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

    def test_shutdown_closes_ipc_transport(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_ipc = MagicMock()
        engine._ipc = mock_ipc
        engine.shutdown()
        mock_ipc.close.assert_called_once()

    def test_shutdown_ignores_ipc_close_error(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_ipc = MagicMock()
        mock_ipc.close.side_effect = OSError("already closed")
        engine._ipc = mock_ipc
        engine.shutdown()  # should not raise

    def test_shutdown_handles_none_proc(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        engine._proc = None
        engine.shutdown()  # should not raise

    # ------------------------------------------------------------------
    # KAMP-283: Windows Job Object — bind mpv lifetime to the daemon so
    # the kernel kills mpv when the daemon process exits (clean shutdown,
    # crash, or End-Task). All four tests stub _start_mpv's collaborators
    # so the real subprocess.Popen / kernel32 calls never run under test.
    # ------------------------------------------------------------------

    def test_start_mpv_creates_job_and_assigns_mpv_on_windows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("kamp_core.playback.sys.platform", "win32")

        fake_proc = MagicMock()
        fake_proc._handle = 0xCAFE
        fake_job = MagicMock()
        fake_job_class = MagicMock(return_value=fake_job)

        with (
            patch(
                "kamp_core.playback.subprocess.Popen", return_value=fake_proc
            ) as mock_popen,
            patch("kamp_core.playback._WindowsJobObject", fake_job_class),
            patch(
                "kamp_core.playback._make_ipc_transport",
                return_value=MagicMock(),
            ),
            patch("kamp_core.playback.threading.Thread"),
        ):
            engine = MpvPlaybackEngine()

        fake_job_class.assert_called_once_with()
        fake_job.assign.assert_called_once_with(0xCAFE)
        assert engine._job is fake_job
        # CREATE_NO_WINDOW (0x08000000) suppresses the console window mpv
        # would otherwise pop. We do NOT pass CREATE_BREAKAWAY_FROM_JOB
        # because Electron's outer Job typically forbids it; nested Jobs
        # work on Win8+ and give us the cleanup we need.
        _, popen_kwargs = mock_popen.call_args
        assert popen_kwargs["creationflags"] & 0x08000000
        assert not (popen_kwargs["creationflags"] & 0x01000000)

    def test_start_mpv_skips_job_on_posix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("kamp_core.playback.sys.platform", "darwin")
        fake_job_class = MagicMock()

        with (
            patch("kamp_core.playback.subprocess.Popen") as mock_popen,
            patch("kamp_core.playback._WindowsJobObject", fake_job_class),
            patch(
                "kamp_core.playback._make_ipc_transport",
                return_value=MagicMock(),
            ),
            patch("kamp_core.playback.threading.Thread"),
        ):
            engine = MpvPlaybackEngine()

        fake_job_class.assert_not_called()
        assert engine._job is None
        _, popen_kwargs = mock_popen.call_args
        assert popen_kwargs.get("creationflags", 0) == 0

    def test_start_mpv_continues_when_job_creation_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr("kamp_core.playback.sys.platform", "win32")
        fake_job_class = MagicMock(side_effect=OSError("simulated failure"))

        with (
            patch("kamp_core.playback.subprocess.Popen") as mock_popen,
            patch("kamp_core.playback._WindowsJobObject", fake_job_class),
            patch(
                "kamp_core.playback._make_ipc_transport",
                return_value=MagicMock(),
            ),
            patch("kamp_core.playback.threading.Thread"),
            caplog.at_level(logging.WARNING, logger="kamp_core.playback"),
        ):
            engine = MpvPlaybackEngine()

        assert engine._job is None
        mock_popen.assert_called_once()
        assert any(
            "Job Object" in rec.message for rec in caplog.records
        ), "expected a warning about Job Object failure"

    def test_shutdown_closes_job_object(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_job = MagicMock()
        engine._job = mock_job

        engine.shutdown()

        mock_job.close.assert_called_once()
        assert engine._job is None

    def test_volume_getter_returns_state_volume(self) -> None:
        engine, _ = _make_engine()
        assert engine.volume == 100

    def test_send_command_sends_json_over_ipc(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_ipc = MagicMock()
        engine._ipc = mock_ipc
        engine._send_command("loadfile", "/music/01.mp3", "replace")
        expected = (
            json.dumps({"command": ["loadfile", "/music/01.mp3", "replace"]}) + "\n"
        )
        mock_ipc.sendall.assert_called_once_with(expected.encode())

    def test_send_command_logs_warning_on_oserror(self) -> None:
        with patch("kamp_core.playback.MpvPlaybackEngine._start_mpv"):
            engine = MpvPlaybackEngine()
        mock_ipc = MagicMock()
        mock_ipc.sendall.side_effect = OSError("broken pipe")
        engine._ipc = mock_ipc
        engine._send_command("stop")  # should not raise

    def test_handle_event_unknown_event_is_ignored(self) -> None:
        engine, _ = _make_engine()
        engine._handle_event({"event": "seek"})
        assert engine.state == PlaybackState()

    # ------------------------------------------------------------------
    # preload_next / has_lookahead
    # ------------------------------------------------------------------

    def test_has_lookahead_false_initially(self) -> None:
        engine, _ = _make_engine()
        assert engine.has_lookahead is False

    def test_has_lookahead_true_after_preload_next(self) -> None:
        engine, _ = _make_engine()
        engine.preload_next(_track(2))
        assert engine.has_lookahead is True

    def test_has_lookahead_false_after_preload_next_none(self) -> None:
        engine, _ = _make_engine()
        engine.preload_next(_track(2))
        engine.preload_next(None)
        assert engine.has_lookahead is False

    def test_preload_next_sends_loadfile_append(self) -> None:
        engine, send = _make_engine()
        engine.preload_next(_track(2))
        send.assert_called_once_with("loadfile", str(_track(2).file_path), "append")

    def test_preload_next_is_noop_for_same_path(self) -> None:
        engine, send = _make_engine()
        engine.preload_next(_track(2))
        send.reset_mock()
        engine.preload_next(_track(2))
        send.assert_not_called()

    def test_preload_next_replaces_stale_lookahead(self) -> None:
        engine, send = _make_engine()
        engine.preload_next(_track(2))
        send.reset_mock()
        engine.preload_next(_track(3))
        assert send.call_args_list == [
            call("playlist-remove", 1),
            call("loadfile", str(_track(3).file_path), "append"),
        ]

    def test_preload_next_with_none_removes_stale_lookahead(self) -> None:
        engine, send = _make_engine()
        engine.preload_next(_track(2))
        send.reset_mock()
        engine.preload_next(None)
        send.assert_called_once_with("playlist-remove", 1)
        assert engine._lookahead_path is None

    def test_preload_next_with_none_is_noop_when_no_lookahead(self) -> None:
        engine, send = _make_engine()
        engine.preload_next(None)
        send.assert_not_called()

    def test_preload_next_clears_lookahead_before_sending_remove(self) -> None:
        """_lookahead_path must be None before playlist-remove is sent."""
        engine, send = _make_engine()
        engine.preload_next(_track(2))
        observed_during_remove: list[bool] = []

        def capture(*_args: object) -> None:
            observed_during_remove.append(engine._lookahead_path is None)

        send.side_effect = capture
        engine.preload_next(_track(3))
        # First send call is playlist-remove — lookahead must already be None then.
        assert observed_during_remove[0] is True

    def test_play_clears_lookahead_path(self) -> None:
        engine, _ = _make_engine()
        engine.preload_next(_track(2))
        engine.play(Path("/music/01.mp3"))
        assert engine._lookahead_path is None

    def test_load_paused_clears_lookahead_path(self) -> None:
        engine, _ = _make_engine()
        engine.preload_next(_track(2))
        engine.load_paused(Path("/music/01.mp3"))
        assert engine._lookahead_path is None

    # ------------------------------------------------------------------
    # preload_next near-end guard
    # ------------------------------------------------------------------

    def test_preload_next_skips_append_within_guard_window(self) -> None:
        """Appending a new lookahead within the last 10 s triggers an immediate
        gapless EOF in mpv, freezing time-pos updates.  The append must be
        skipped so the track plays through to its natural end."""
        engine, send = _make_engine()
        engine.state.duration = 180.0
        engine.state.position = 172.0  # 8 s from end
        engine.preload_next(_track(2))
        send.assert_not_called()
        assert engine._lookahead_path is None

    def test_preload_next_appends_when_outside_guard_window(self) -> None:
        """preload_next must work normally when the current position is well
        before the gapless danger window."""
        engine, send = _make_engine()
        engine.state.duration = 180.0
        engine.state.position = 60.0  # 2 minutes from end
        engine.preload_next(_track(2))
        send.assert_called_once_with("loadfile", str(_track(2).file_path), "append")
        assert engine.has_lookahead is True

    def test_preload_next_removes_stale_lookahead_even_within_guard_window(
        self,
    ) -> None:
        """The old lookahead must still be evicted when near the end so the
        wrong track does not play gaplessly."""
        engine, send = _make_engine()
        engine.preload_next(_track(2))  # prime with track 2 while not near end
        engine.state.duration = 180.0
        engine.state.position = 172.0  # now near end
        send.reset_mock()
        engine.preload_next(_track(3))  # swap to track 3 while near end
        send.assert_called_once_with("playlist-remove", 1)
        assert engine._lookahead_path is None  # new track NOT appended

    def test_preload_next_appends_when_duration_unknown(self) -> None:
        """When duration is 0 (not yet received from mpv), the guard must not
        block the append — we have no basis to judge nearness to end."""
        engine, send = _make_engine()
        engine.state.duration = 0.0
        engine.state.position = 0.0
        engine.preload_next(_track(2))
        send.assert_called_once_with("loadfile", str(_track(2).file_path), "append")

    def test_file_loaded_resets_state_so_lookahead_re_arms_after_gapless(
        self,
    ) -> None:
        """Regression for KAMP-276: after a gapless transition the file-loaded
        event must reset position/duration before calling on_file_loaded so the
        preload_next guard (position > duration - 10s) does not fire on stale
        old-track values and block the lookahead for the second transition."""
        engine, send = _make_engine()

        # Prime: track 2 is preloaded at the start (position≈0, guard passes)
        engine.preload_next(_track(2))

        # Simulate the near-end state that exists when end-file fires
        engine.state.position = 238.0
        engine.state.duration = 240.0

        # Wire on_file_loaded to capture state at callback time and then call
        # preload_next for the third track, mirroring what the queue manager does.
        state_at_callback: list[tuple[float, float]] = []

        def _on_file_loaded() -> None:
            state_at_callback.append((engine.state.position, engine.state.duration))
            engine.preload_next(_track(3))

        engine.on_file_loaded = _on_file_loaded

        send.reset_mock()

        # Gapless transition: mpv fires end-file/eof, clearing _lookahead_path
        engine._handle_event({"event": "end-file", "reason": "eof"})
        assert engine._lookahead_path is None

        # file-loaded for track 2: stale position/duration would satisfy the guard
        # (238 > 240-10) and block the append without the fix.
        engine._handle_event({"event": "file-loaded"})

        # The reset must have happened BEFORE on_file_loaded fired.
        assert state_at_callback == [(0.0, 0.0)]
        assert engine._lookahead_path == Path("/music/03.mp3")

    # ------------------------------------------------------------------
    # end-file gapless cleanup
    # ------------------------------------------------------------------

    def test_end_file_sends_playlist_remove_0_when_lookahead_present(self) -> None:
        engine, send = _make_engine()
        engine.preload_next(_track(2))
        send.reset_mock()
        engine._handle_event({"event": "end-file", "reason": "eof"})
        send.assert_any_call("playlist-remove", 0)

    def test_end_file_does_not_send_playlist_remove_when_no_lookahead(self) -> None:
        engine, send = _make_engine()
        engine._handle_event({"event": "end-file", "reason": "eof"})
        send.assert_not_called()

    def test_end_file_clears_lookahead_path_after_callback(self) -> None:
        engine, _ = _make_engine()
        engine.preload_next(_track(2))
        engine._handle_event({"event": "end-file", "reason": "eof"})
        assert engine._lookahead_path is None

    def test_on_track_end_receives_had_lookahead_true_after_gapless(self) -> None:
        """on_track_end's had_lookahead arg must be True when a lookahead was armed."""
        engine, _ = _make_engine()
        engine.preload_next(_track(2))
        observed: list[bool] = []
        engine.on_track_end = lambda had_lookahead: observed.append(had_lookahead)
        engine._handle_event({"event": "end-file", "reason": "eof"})
        assert observed == [True]

    def test_on_track_end_receives_had_lookahead_false_without_preload(self) -> None:
        """Without a preloaded lookahead, had_lookahead must be False."""
        engine, _ = _make_engine()
        observed: list[bool] = []
        engine.on_track_end = lambda had_lookahead: observed.append(had_lookahead)
        engine._handle_event({"event": "end-file", "reason": "eof"})
        assert observed == [False]

    def test_lookahead_cleared_before_on_track_end_fires(self) -> None:
        """has_lookahead must read False inside on_track_end — the engine
        clears _lookahead_path under the lock before firing the callback so
        a callback that queries the property sees mpv's true current state.
        """
        engine, _ = _make_engine()
        engine.preload_next(_track(2))
        observed: list[bool] = []
        engine.on_track_end = lambda _: observed.append(engine.has_lookahead)
        engine._handle_event({"event": "end-file", "reason": "eof"})
        assert observed == [False]

    def test_handle_event_pause_with_non_bool_data_is_ignored(self) -> None:
        engine, _ = _make_engine()
        engine._handle_event({"event": "property-change", "name": "pause", "data": 1})
        assert engine.state.playing is False

    # ------------------------------------------------------------------
    # KAMP-319: ebur128 audio level polling
    # ------------------------------------------------------------------

    def test_start_mpv_astats_ametadata_filter_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mpv must be launched with the astats+ametadata filter graph."""
        from kamp_core.playback import _LEVEL_FILTER_GRAPH

        monkeypatch.setattr("kamp_core.playback.sys.platform", "darwin")
        fake_proc = MagicMock()
        fake_proc.stdout = io.BytesIO(b"")
        with (
            patch(
                "kamp_core.playback.subprocess.Popen", return_value=fake_proc
            ) as mock_popen,
            patch("kamp_core.playback._WindowsJobObject"),
            patch("kamp_core.playback._make_ipc_transport", return_value=MagicMock()),
            patch("kamp_core.playback.threading.Thread"),
        ):
            MpvPlaybackEngine()
        popen_args, _ = mock_popen.call_args
        cmd = popen_args[0]
        expected_af = (
            f"--af=lavfi=graph=%{len(_LEVEL_FILTER_GRAPH)}%{_LEVEL_FILTER_GRAPH}"
        )
        assert expected_af in cmd

    def test_start_mpv_msg_level_ffmpeg_verbose(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mpv must be launched with --msg-level=ffmpeg=v to surface ametadata output."""
        monkeypatch.setattr("kamp_core.playback.sys.platform", "darwin")
        fake_proc = MagicMock()
        fake_proc.stdout = io.BytesIO(b"")
        with (
            patch(
                "kamp_core.playback.subprocess.Popen", return_value=fake_proc
            ) as mock_popen,
            patch("kamp_core.playback._WindowsJobObject"),
            patch("kamp_core.playback._make_ipc_transport", return_value=MagicMock()),
            patch("kamp_core.playback.threading.Thread"),
        ):
            MpvPlaybackEngine()
        popen_args, _ = mock_popen.call_args
        cmd = popen_args[0]
        assert "--msg-level=ffmpeg=v" in cmd

    def test_start_mpv_stdout_is_piped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """mpv stdout must be PIPE so _stdout_reader_loop can read ametadata output."""
        import subprocess as _sp

        monkeypatch.setattr("kamp_core.playback.sys.platform", "darwin")
        fake_proc = MagicMock()
        fake_proc.stdout = io.BytesIO(b"")
        with (
            patch(
                "kamp_core.playback.subprocess.Popen", return_value=fake_proc
            ) as mock_popen,
            patch("kamp_core.playback._WindowsJobObject"),
            patch("kamp_core.playback._make_ipc_transport", return_value=MagicMock()),
            patch("kamp_core.playback.threading.Thread"),
        ):
            MpvPlaybackEngine()
        _, popen_kwargs = mock_popen.call_args
        assert popen_kwargs.get("stdout") == _sp.PIPE

    def test_stdout_reader_fires_stereo_on_audio_level(self) -> None:
        """_stdout_reader_loop emits (left_db, right_db, crest_db, peak_db) from astats output."""
        engine, _ = _make_engine()
        received: list[tuple[float, float, float, float]] = []
        engine.on_audio_level = lambda l, r, c, p: received.append((l, r, c, p))
        lines = (
            b"[ffmpeg] Parsed_ametadata_0: frame:0    pts:0       pts_time:0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.RMS_level=-18.5\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.Crest_factor=12.3\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.Peak_level=-6.1\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.2.RMS_level=-19.1\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.2.Crest_factor=11.7\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.2.Peak_level=-7.3\n"
            b"[ffmpeg] Parsed_ametadata_0: frame:1    pts:2205    pts_time:0.05\n"
        )
        engine._stdout_reader_loop(io.BytesIO(lines))
        assert len(received) == 1
        assert received[0][0] == pytest.approx(-18.5)  # left
        assert received[0][1] == pytest.approx(-19.1)  # right
        assert received[0][2] == pytest.approx(12.0)  # crest avg(12.3, 11.7)
        assert received[0][3] == pytest.approx(-6.1)  # peak max(-6.1, -7.3)

    def test_stdout_reader_crest_defaults_when_missing(self) -> None:
        """crest_db defaults to 14.0 when no Crest_factor lines appear."""
        engine, _ = _make_engine()
        received: list[tuple[float, float, float, float]] = []
        engine.on_audio_level = lambda l, r, c, p: received.append((l, r, c, p))
        lines = (
            b"[ffmpeg] Parsed_ametadata_0: frame:0    pts:0       pts_time:0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.RMS_level=-18.5\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.2.RMS_level=-19.1\n"
            b"[ffmpeg] Parsed_ametadata_0: frame:1    pts:2205    pts_time:0.05\n"
        )
        engine._stdout_reader_loop(io.BytesIO(lines))
        assert len(received) == 1
        assert received[0][2] == pytest.approx(14.0)  # DEFAULT_CREST
        assert received[0][3] == pytest.approx(
            -18.5
        )  # peak falls back to max(left, right)

    def test_stdout_reader_peak_level_parsed(self) -> None:
        """Peak_level is parsed separately from RMS and emitted as peak_db."""
        engine, _ = _make_engine()
        received: list[tuple[float, float, float, float]] = []
        engine.on_audio_level = lambda l, r, c, p: received.append((l, r, c, p))
        lines = (
            b"[ffmpeg] Parsed_ametadata_0: frame:0    pts:0       pts_time:0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.RMS_level=-20.0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.Peak_level=-3.0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.2.RMS_level=-20.0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.2.Peak_level=nan_bad\n"
            b"[ffmpeg] Parsed_ametadata_0: frame:1    pts:2205    pts_time:0.05\n"
        )
        engine._stdout_reader_loop(io.BytesIO(lines))
        assert len(received) == 1
        # Only channel 1 peak parsed (channel 2 malformed); max of one entry = -3.0
        assert received[0][3] == pytest.approx(-3.0)

    def test_stdout_reader_mirrors_channel_1_for_mono(self) -> None:
        """Mono files (channel 1 only) must mirror left to right."""
        engine, _ = _make_engine()
        received: list[tuple[float, float, float, float]] = []
        engine.on_audio_level = lambda l, r, c, p: received.append((l, r, c, p))
        lines = (
            b"[ffmpeg] Parsed_ametadata_0: frame:0    pts:0       pts_time:0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.RMS_level=-22.0\n"
            b"[ffmpeg] Parsed_ametadata_0: frame:1    pts:2205    pts_time:0.05\n"
        )
        engine._stdout_reader_loop(io.BytesIO(lines))
        assert len(received) == 1
        assert received[0][0] == pytest.approx(-22.0)
        assert received[0][1] == pytest.approx(-22.0)

    def test_stdout_reader_ignores_non_rms_keys(self) -> None:
        """Non-RMS/Crest astats keys and unrelated lines must not fire on_audio_level."""
        engine, _ = _make_engine()
        received: list[tuple[float, float, float, float]] = []
        engine.on_audio_level = lambda l, r, c, p: received.append((l, r, c, p))
        lines = (
            b"[ffmpeg] Parsed_ametadata_0: frame:0    pts:0       pts_time:0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.Peak_level=-14.0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.Overall.RMS_level=-20.0\n"
        )
        engine._stdout_reader_loop(io.BytesIO(lines))
        assert received == []

    def test_stdout_reader_handles_malformed_float(self) -> None:
        """A non-numeric RMS_level value clamps to -120.0."""
        engine, _ = _make_engine()
        received: list[tuple[float, float, float, float]] = []
        engine.on_audio_level = lambda l, r, c, p: received.append((l, r, c, p))
        lines = (
            b"[ffmpeg] Parsed_ametadata_0: frame:0    pts:0       pts_time:0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.RMS_level=nan_bad\n"
            b"[ffmpeg] Parsed_ametadata_0: frame:1    pts:2205    pts_time:0.05\n"
        )
        engine._stdout_reader_loop(io.BytesIO(lines))
        assert len(received) == 1
        assert received[0][0] == pytest.approx(-120.0)
        assert received[0][1] == pytest.approx(-120.0)

    def test_stdout_reader_no_error_when_callback_is_none(self) -> None:
        """_stdout_reader_loop must not raise when on_audio_level is None."""
        engine, _ = _make_engine()
        assert engine.on_audio_level is None
        lines = (
            b"[ffmpeg] Parsed_ametadata_0: frame:0    pts:0       pts_time:0\n"
            b"[ffmpeg] Parsed_ametadata_0: lavfi.astats.1.RMS_level=-18.5\n"
            b"[ffmpeg] Parsed_ametadata_0: frame:1    pts:2205    pts_time:0.05\n"
        )
        engine._stdout_reader_loop(io.BytesIO(lines))  # should not raise

    def test_no_level_poll_branch_in_handle_event(self) -> None:
        """Events with request_id=9999 (old poll id) must not trigger on_audio_level."""
        engine, _ = _make_engine()
        received: list[tuple[float, float, float, float]] = []
        engine.on_audio_level = lambda lvl, pk, c, p: received.append((lvl, pk, c, p))
        engine._handle_event(
            {"request_id": 9999, "error": "success", "data": {"lavfi.r128.M": "-18.5"}}
        )
        assert received == []
