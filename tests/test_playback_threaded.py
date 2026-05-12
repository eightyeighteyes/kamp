"""Real-thread regression tests for MpvPlaybackEngine locking (KAMP-284).

Single-threaded tests in tests/test_playback.py assert command order under
manual ``_handle_event`` injection, with ``_send_command`` patched and the
reader thread suppressed. These tests fill the gap by spawning the actual
reader thread against a controllable fake IPC transport, then verifying:

* a slow ``on_track_end`` callback (simulating a hung Last.fm scrobble) does
  not block a concurrent ``engine.seek()`` on the main thread (KAMP-284);
* the "double playlist-remove" race between ``seek()`` and the reader-thread
  EOF handler (KAMP-261, "Race A") stays prevented under real concurrency;
* ``on_file_loaded`` runs strictly after ``on_track_end`` returns even when
  the latter sleeps (preserved by the single reader thread);
* the transport-level write lock serializes concurrent ``_send_command``
  callers so JSON frames never interleave on the wire.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kamp_core.library import Track
from kamp_core.playback import (
    MpvPlaybackEngine,
    _IPCTransport,
    _UnixSocketTransport,
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


class FakeIPCTransport(_IPCTransport):
    """Controllable in-memory transport for engine-threading tests.

    Mirrors the production transports' contract: ``sendall`` is serialized
    by an internal write lock, ``recv`` is blocking and reads one event at
    a time from a test-controlled queue. Setting ``pause_send`` parks the
    next ``sendall`` caller on the event so tests can pin write-side timing.
    """

    def __init__(self) -> None:
        # Mirrors the production transports: serialize concurrent sendall via
        # a transport-local lock. This is exactly the contract the engine
        # depends on now that ``_send_command`` no longer takes ``_lock``.
        self._write_lock = threading.Lock()
        self._send_log: list[bytes] = []
        self._recv_queue: queue.Queue[bytes] = queue.Queue()
        self._closed = False

    @property
    def server_arg(self) -> str:
        return "fake://test"

    def open(self, timeout: float, proc: Any) -> None:  # pragma: no cover
        return None

    def sendall(self, data: bytes) -> None:
        with self._write_lock:
            if self._closed:
                raise OSError("transport closed")
            self._send_log.append(data)

    def recv(self, n: int) -> bytes:
        if self._closed:
            return b""
        try:
            chunk = self._recv_queue.get(timeout=10.0)
        except queue.Empty:
            return b""
        return chunk

    def close(self) -> None:
        self._closed = True
        # Push a sentinel so the reader thread breaks out of its blocking recv.
        self._recv_queue.put(b"")

    # -- test helpers --

    def push_event(self, event: dict[str, object]) -> None:
        """Inject an mpv event (newline-delimited JSON) into recv()."""
        self._recv_queue.put((json.dumps(event) + "\n").encode())

    def send_log_snapshot(self) -> list[bytes]:
        with self._write_lock:
            return list(self._send_log)

    def commands_sent(self) -> list[list[object]]:
        """Return the list of decoded mpv command arrays in send order."""
        out: list[list[object]] = []
        for raw in self.send_log_snapshot():
            try:
                msg = json.loads(raw.decode().strip())
            except Exception:
                continue
            cmd = msg.get("command")
            if isinstance(cmd, list):
                out.append(cmd)
        return out


def _make_threaded_engine() -> tuple[MpvPlaybackEngine, FakeIPCTransport]:
    """Construct an engine with a real reader thread reading from a fake transport.

    Bypasses ``_start_mpv`` (no subprocess) and ``_make_ipc_transport`` (no real
    socket / pipe), then manually wires the fake into ``engine._ipc`` and
    spawns the reader thread.
    """
    fake = FakeIPCTransport()
    with (
        patch("kamp_core.playback._make_ipc_transport", return_value=fake),
        patch.object(MpvPlaybackEngine, "_start_mpv"),
    ):
        engine = MpvPlaybackEngine()
    engine._ipc = fake
    reader = threading.Thread(
        target=engine._read_loop, daemon=True, name="mpv-reader-test"
    )
    engine._reader_thread = reader
    reader.start()
    return engine, fake


def _stop_engine(engine: MpvPlaybackEngine, fake: FakeIPCTransport) -> None:
    """Unblock the reader thread and join it. Best-effort cleanup."""
    fake.close()
    if engine._reader_thread is not None:
        engine._reader_thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# KAMP-284 — slow callback must not block main-thread seek
# ---------------------------------------------------------------------------


class TestSlowCallbackDoesNotBlockSeek:
    def test_seek_completes_while_on_track_end_is_parked(self) -> None:
        """The original KAMP-284 symptom: a hung Last.fm scrobble inside
        ``on_track_end`` used to hold ``_lock`` indefinitely, freezing every
        FastAPI player route. After the refactor the callback runs OUTSIDE
        ``_lock``, so a concurrent seek must complete with low latency.
        """
        engine, fake = _make_threaded_engine()
        try:
            engine.state.duration = 180.0
            engine.state.position = 60.0
            callback_started = threading.Event()
            callback_release = threading.Event()

            def _slow_on_track_end(had_lookahead: bool) -> None:
                callback_started.set()
                callback_release.wait(timeout=5.0)

            engine.on_track_end = _slow_on_track_end
            fake.push_event({"event": "end-file", "reason": "eof"})
            assert callback_started.wait(
                timeout=2.0
            ), "reader thread never entered on_track_end"
            start = time.monotonic()
            engine.seek(30.0)
            elapsed = time.monotonic() - start
            assert elapsed < 0.2, (
                f"seek() blocked for {elapsed:.3f}s while on_track_end was "
                f"parked; expected <0.2s. This is the KAMP-284 regression."
            )
            cmds = fake.commands_sent()
            assert any(
                c[0] == "seek" and c[1] == 30.0 for c in cmds
            ), f"seek command never reached the transport. commands={cmds}"
            callback_release.set()
        finally:
            _stop_engine(engine, fake)

    def test_pause_completes_while_on_track_end_is_parked(self) -> None:
        """Same shape as the seek test, but for ``pause()`` — that route is
        also hit by media keys and the play/pause button."""
        engine, fake = _make_threaded_engine()
        try:
            callback_release = threading.Event()
            entered = threading.Event()

            def _slow(had_lookahead: bool) -> None:
                entered.set()
                callback_release.wait(timeout=5.0)

            engine.on_track_end = _slow
            fake.push_event({"event": "end-file", "reason": "eof"})
            assert entered.wait(timeout=2.0)
            start = time.monotonic()
            engine.pause()
            elapsed = time.monotonic() - start
            assert elapsed < 0.2
            callback_release.set()
        finally:
            _stop_engine(engine, fake)


# ---------------------------------------------------------------------------
# KAMP-261 — Race A: seek vs end-file/eof under real concurrency
# ---------------------------------------------------------------------------


class TestRaceAUnderRealConcurrency:
    def test_no_double_playlist_remove_when_seek_races_eof(self) -> None:
        """Run seek() and reader-thread eof in lockstep across many iterations.
        Each iteration arms a fresh lookahead, then triggers the two paths
        simultaneously. The invariant: across the cycle, mpv sees AT MOST one
        ``playlist-remove`` per slot — Race A would emit BOTH
        ``playlist-remove 0`` and ``playlist-remove 1`` for the same lookahead,
        emptying mpv's playlist and stopping time-pos events.
        """
        iterations = 50
        for i in range(iterations):
            engine, fake = _make_threaded_engine()
            try:
                # Arm a lookahead in slot 1. preload_next runs on the main
                # thread; the reader is idle until we push the eof event.
                engine.state.duration = 180.0
                engine.state.position = 50.0  # outside guard so the append happens
                engine.preload_next(_track(i + 1))
                # Now move into the guard window so seek() will try to remove.
                engine.state.position = 175.0
                assert engine.has_lookahead is True

                barrier = threading.Barrier(2)

                def _do_seek() -> None:
                    barrier.wait(timeout=5.0)
                    engine.seek(178.0)

                seek_thread = threading.Thread(target=_do_seek)
                seek_thread.start()
                barrier.wait(timeout=5.0)
                fake.push_event({"event": "end-file", "reason": "eof"})
                seek_thread.join(timeout=5.0)
                # Give the reader a moment to drain the eof event.
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and engine.has_lookahead:
                    time.sleep(0.005)

                cmds = fake.commands_sent()
                # The lookahead's life-cycle commands:
                # - "loadfile" "append" (during preload_next)
                # - exactly one of: "playlist-remove" 0 (eof won) or
                #   "playlist-remove" 1 (seek won), but never both.
                rm0 = sum(1 for c in cmds if c[:2] == ["playlist-remove", 0])
                rm1 = sum(1 for c in cmds if c[:2] == ["playlist-remove", 1])
                # rm0 + rm1 counts how many removes fired against the original
                # lookahead. The Race-A bug shows up as rm0 >= 1 AND rm1 >= 1
                # in the same cycle (double-remove).
                assert not (rm0 >= 1 and rm1 >= 1), (
                    f"Race A: both playlist-remove 0 and playlist-remove 1 "
                    f"fired in iteration {i}. commands={cmds}"
                )
                assert engine._lookahead_path is None, (
                    f"iteration {i}: _lookahead_path not cleared after eof. "
                    f"commands={cmds}"
                )
            finally:
                _stop_engine(engine, fake)


# ---------------------------------------------------------------------------
# Callback ordering — single reader thread guarantees on_track_end completes
# before on_file_loaded fires for the next track.
# ---------------------------------------------------------------------------


class TestCallbackOrdering:
    def test_on_file_loaded_starts_after_on_track_end_returns(self) -> None:
        engine, fake = _make_threaded_engine()
        try:
            order: list[str] = []
            order_lock = threading.Lock()

            def _on_te(had_lookahead: bool) -> None:
                with order_lock:
                    order.append("te_enter")
                time.sleep(0.05)
                with order_lock:
                    order.append("te_exit")

            def _on_fl() -> None:
                with order_lock:
                    order.append("fl_enter")

            engine.on_track_end = _on_te
            engine.on_file_loaded = _on_fl
            fake.push_event({"event": "end-file", "reason": "eof"})
            fake.push_event({"event": "file-loaded"})
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                with order_lock:
                    if "fl_enter" in order:
                        break
                time.sleep(0.01)
            with order_lock:
                snapshot = list(order)
            te_exit_idx = snapshot.index("te_exit")
            fl_enter_idx = snapshot.index("fl_enter")
            assert (
                te_exit_idx < fl_enter_idx
            ), f"on_file_loaded started before on_track_end returned: {snapshot}"
        finally:
            _stop_engine(engine, fake)


# ---------------------------------------------------------------------------
# Transport write-lock — concurrent _send_command callers must not interleave
# JSON frames on the wire. With the engine lock narrowed (KAMP-284), this
# responsibility moved entirely to the transport.
# ---------------------------------------------------------------------------


class TestTransportWriteLock:
    def test_fake_transport_serializes_concurrent_sends(self) -> None:
        """Validates the FakeIPCTransport mirrors the production contract:
        no two threads' frames ever interleave inside one log entry."""
        engine, fake = _make_threaded_engine()
        try:
            n_threads = 8
            sends_per_thread = 250
            start_barrier = threading.Barrier(n_threads)

            def _worker(label: int) -> None:
                start_barrier.wait(timeout=5.0)
                for i in range(sends_per_thread):
                    engine._send_command("set_property", f"label-{label}", i)

            threads = [
                threading.Thread(target=_worker, args=(j,)) for j in range(n_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10.0)
            entries = fake.send_log_snapshot()
            # Each entry must be exactly one well-formed JSON line ending in \n.
            for raw in entries:
                assert raw.endswith(
                    b"\n"
                ), f"frame did not end on newline: {raw!r} — frames interleaved"
                # Must round-trip as a single JSON object with a "command" list.
                msg = json.loads(raw.decode().strip())
                assert isinstance(msg.get("command"), list)
            assert len(entries) == n_threads * sends_per_thread
        finally:
            _stop_engine(engine, fake)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="_UnixSocketTransport is POSIX-only",
    )
    def test_posix_transport_write_lock_exists(self) -> None:
        """The POSIX transport gained a write lock in KAMP-284 so it
        symmetrically protects sendall from concurrent callers (the Windows
        named-pipe transport already had one)."""
        t = _UnixSocketTransport()
        try:
            assert hasattr(t, "_write_lock"), "_UnixSocketTransport missing _write_lock"
            # Confirm it is actually a Lock (not just any attribute).
            assert isinstance(t._write_lock, type(threading.Lock()))
        finally:
            t.close()


# ---------------------------------------------------------------------------
# End-to-end: non-gapless EOF flow (regression coverage for the "stuck
# position bar after seek-near-end" report). The original KAMP-284 refactor
# shipped without a test that runs the full daemon-style callback chain
# through a non-gapless EOF, so a regression here had no failing test.
# ---------------------------------------------------------------------------


class TestNonGaplessEofFlow:
    def test_seek_into_guard_window_then_eof_advances_to_next_track(self) -> None:
        """User scenario: a queued lookahead is in slot 1, user seeks within
        the gapless guard window (so seek() removes the lookahead), the
        track ends naturally a few seconds later. The daemon callback must
        call engine.play(next_track) and that loadfile must land on the
        transport — otherwise mpv goes idle, time-pos stops, position bar
        freezes (the exact user-reported symptom).
        """
        engine, fake = _make_threaded_engine()
        try:
            engine.state.duration = 180.0
            engine.state.position = 60.0
            engine.preload_next(_track(2))
            assert engine.has_lookahead is True

            # Daemon-shaped callback: advance a fake queue and call play(next)
            # iff !had_lookahead, mirroring kamp_daemon/__main__.py:_on_track_end.
            queue_tracks = [_track(1), _track(2), _track(3)]
            qpos = [0]
            captured: list[tuple[bool, Path]] = []

            def _on_te(had_lookahead: bool) -> None:
                qpos[0] += 1
                if qpos[0] < len(queue_tracks):
                    next_path = queue_tracks[qpos[0]].file_path
                    if not had_lookahead:
                        engine.play(next_path)
                    captured.append((had_lookahead, next_path))

            engine.on_track_end = _on_te

            # Move into the guard window and seek — clears _lookahead_path.
            engine.state.position = 175.0
            engine.seek(175.0)
            assert engine.has_lookahead is False

            # Track ends naturally. Reader processes eof on its own thread.
            fake.push_event({"event": "end-file", "reason": "eof"})

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not captured:
                time.sleep(0.01)

            assert captured == [(False, queue_tracks[1].file_path)], (
                f"on_track_end did not fire with had_lookahead=False, or queue "
                f"never advanced. captured={captured}"
            )
            cmds = fake.commands_sent()
            assert any(
                c[:3] == ["loadfile", str(queue_tracks[1].file_path), "replace"]
                for c in cmds
            ), (
                f"loadfile replace for next track never reached transport — "
                f"mpv would go idle. cmds={cmds}"
            )
        finally:
            _stop_engine(engine, fake)

    def test_state_is_reset_at_notify_time_after_non_gapless_eof(self) -> None:
        """At the moment the daemon's notify_track_changed fires (end of
        the on_track_end chain), state.position and state.duration must
        reflect the NEW track's starting values, not the finishing track's
        stale ones. Otherwise the track.changed WS event ships position=175
        / duration=180 for the new track and the UI renders a stuck slider —
        the user-reported KAMP-284 regression after seek-near-EOF.
        """
        engine, fake = _make_threaded_engine()
        try:
            engine.state.duration = 180.0
            engine.state.position = 175.0
            engine.preload_next(_track(2))
            engine.seek(175.0)  # clears lookahead via guard window
            assert engine.has_lookahead is False

            snap: dict[str, float] = {}
            queue_tracks = [_track(1), _track(2)]
            qpos = [0]

            def _on_te(had_lookahead: bool) -> None:
                qpos[0] += 1
                if qpos[0] < len(queue_tracks) and not had_lookahead:
                    engine.play(queue_tracks[qpos[0]].file_path)
                # _on_track_end_notify fires app.state.notify_track_changed()
                # right after this callback returns — capture the state it
                # would see.
                snap["pos"] = engine.state.position
                snap["dur"] = engine.state.duration

            engine.on_track_end = _on_te
            fake.push_event({"event": "end-file", "reason": "eof"})

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and "pos" not in snap:
                time.sleep(0.01)
            assert snap.get("pos") == 0.0, (
                f"state.position={snap.get('pos')!r} at notify time; "
                f"expected 0.0. UI would render the new track at the "
                f"finishing track's stale position bar."
            )
            assert (
                snap.get("dur") == 0.0
            ), f"state.duration={snap.get('dur')!r} at notify time; expected 0.0."
        finally:
            _stop_engine(engine, fake)

    def test_state_is_reset_at_notify_time_after_gapless_eof(self) -> None:
        """Gapless counterpart: when had_lookahead=True at EOF, mpv has
        already transitioned but state.position/duration still reflect the
        finished track until file-loaded for the new track is processed
        (which the reader can't do until on_track_end returns). The engine
        must reset state synchronously inside the eof handler so the
        callback's WS notify sees the new track at 0:00.
        """
        engine, fake = _make_threaded_engine()
        try:
            engine.state.duration = 180.0
            engine.state.position = 100.0  # outside guard so lookahead survives
            engine.preload_next(_track(2))
            assert engine.has_lookahead is True

            snap: dict[str, float] = {}

            def _on_te(had_lookahead: bool) -> None:
                snap["pos"] = engine.state.position
                snap["dur"] = engine.state.duration
                snap["had_lookahead"] = float(had_lookahead)

            engine.on_track_end = _on_te
            fake.push_event({"event": "end-file", "reason": "eof"})

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and "pos" not in snap:
                time.sleep(0.01)
            assert snap.get("had_lookahead") == 1.0, "gapless path not exercised"
            assert (
                snap.get("pos") == 0.0
            ), f"state.position={snap.get('pos')!r} at notify time; expected 0.0"
            assert snap.get("dur") == 0.0
        finally:
            _stop_engine(engine, fake)

    def test_natural_eof_with_lookahead_does_not_call_play(self) -> None:
        """Counterpart to the above: when there IS a lookahead at eof (mpv
        already transitioned gaplessly), the callback must NOT call
        engine.play, because that would clobber the gapless transition with
        a loadfile replace.
        """
        engine, fake = _make_threaded_engine()
        try:
            engine.state.duration = 180.0
            engine.state.position = 60.0
            engine.preload_next(_track(2))
            assert engine.has_lookahead is True

            queue_tracks = [_track(1), _track(2), _track(3)]
            qpos = [0]
            captured_had: list[bool] = []
            replace_calls: list[str] = []

            def _on_te(had_lookahead: bool) -> None:
                qpos[0] += 1
                captured_had.append(had_lookahead)
                if qpos[0] < len(queue_tracks) and not had_lookahead:
                    engine.play(queue_tracks[qpos[0]].file_path)

            engine.on_track_end = _on_te

            # Stay OUTSIDE the guard window so the lookahead survives to eof.
            engine.state.position = 100.0
            fake.push_event({"event": "end-file", "reason": "eof"})

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not captured_had:
                time.sleep(0.01)

            assert captured_had == [True]
            cmds = fake.commands_sent()
            for c in cmds:
                if c[:1] == ["loadfile"] and len(c) >= 3 and c[2] == "replace":
                    replace_calls.append(c[1])
            assert (
                replace_calls == []
            ), f"engine.play was wrongly called after a gapless EOF: {replace_calls}"
        finally:
            _stop_engine(engine, fake)


# ---------------------------------------------------------------------------
# Defense in depth: the reader thread must survive any callback exception so
# a buggy on_track_end can't silently wedge the daemon until restart.
# ---------------------------------------------------------------------------


class TestReaderSurvivesCallbackException:
    def test_zero_division_in_on_track_end_does_not_kill_reader(self) -> None:
        engine, fake = _make_threaded_engine()
        try:
            engine.on_track_end = lambda _: 1 / 0
            fake.push_event({"event": "end-file", "reason": "eof"})
            # Give the bad callback a moment to fire (and be swallowed).
            time.sleep(0.1)
            # If the reader is alive, this time-pos must reach state.position.
            fake.push_event(
                {"event": "property-change", "name": "time-pos", "data": 42.5}
            )
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and engine.state.position != 42.5:
                time.sleep(0.01)
            assert engine.state.position == 42.5, (
                "reader thread died from the on_track_end exception; "
                "time-pos updates stopped flowing"
            )
        finally:
            _stop_engine(engine, fake)

    def test_attribute_error_in_on_file_loaded_does_not_kill_reader(self) -> None:
        engine, fake = _make_threaded_engine()
        try:
            engine.on_file_loaded = lambda: None.foo  # type: ignore[attr-defined]
            fake.push_event({"event": "file-loaded"})
            time.sleep(0.1)
            fake.push_event(
                {"event": "property-change", "name": "duration", "data": 123.0}
            )
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and engine.state.duration != 123.0:
                time.sleep(0.01)
            assert engine.state.duration == 123.0
        finally:
            _stop_engine(engine, fake)
