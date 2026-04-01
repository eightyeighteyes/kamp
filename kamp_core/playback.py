"""Playback engine and queue for the Kamp music player.

MpvPlaybackEngine controls mpv via its JSON IPC socket
(--input-ipc-server). All player state (position, pause, duration) is
updated by a background reader thread that consumes mpv's event stream.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kamp_core.library import Track

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PlaybackState
# ---------------------------------------------------------------------------


@dataclass
class PlaybackState:
    playing: bool = False
    position: float = 0.0
    duration: float = 0.0
    volume: int = 100


# ---------------------------------------------------------------------------
# PlaybackQueue
# ---------------------------------------------------------------------------


class PlaybackQueue:
    """Ordered track list with next/prev/shuffle/repeat."""

    def __init__(self) -> None:
        self._tracks: list[Track] = []
        self._order: list[int] = []  # indices into _tracks
        self._pos: int = -1  # current position in _order
        self._shuffle: bool = False
        self._repeat: bool = False

    def load(self, tracks: list[Track], start_index: int = 0) -> None:
        """Replace the queue with *tracks* and jump to *start_index*."""
        self._tracks = list(tracks)
        self._order = list(range(len(tracks)))
        if self._shuffle and tracks:
            self._shuffled_order(start_index)
            self._pos = 0
        else:
            self._pos = start_index if tracks else -1

    def current(self) -> Track | None:
        if not self._tracks or self._pos < 0:
            return None
        return self._tracks[self._order[self._pos]]

    def next(self) -> Track | None:
        if not self._tracks:
            return None
        next_pos = self._pos + 1
        if next_pos >= len(self._order):
            if self._repeat:
                next_pos = 0
            else:
                self._pos = -1
                return None
        self._pos = next_pos
        return self.current()

    def prev(self) -> Track | None:
        if not self._tracks:
            return None
        prev_pos = self._pos - 1
        if prev_pos < 0:
            if self._repeat:
                prev_pos = len(self._order) - 1
            else:
                return None
        self._pos = prev_pos
        return self.current()

    def skip_to(self, position: int) -> Track | None:
        """Jump directly to *position* in the queue. Returns the track, or None if invalid."""
        if not self._order or position < 0 or position >= len(self._order):
            return None
        self._pos = position
        return self.current()

    def clear(self) -> None:
        """Clear the queue, keeping the currently playing track (if any).

        If no track is playing the queue is emptied entirely.
        """
        if self._pos < 0 or not self._tracks:
            self._tracks = []
            self._order = []
            self._pos = -1
            return
        current = self._tracks[self._order[self._pos]]
        self._tracks = [current]
        self._order = [0]
        self._pos = 0

    def clear_remaining(self, from_position: int) -> None:
        """Drop all tracks that come after *from_position* in the queue.

        *from_position* is the 0-based display index of the track the user
        right-clicked — everything after it is removed.  Has no effect if the
        queue is empty or *from_position* is out of range.
        """
        if not self._order or from_position < 0 or from_position >= len(self._order):
            return
        kept = self._order[: from_position + 1]
        kept_set = set(kept)
        old_to_new = {old: new for new, old in enumerate(sorted(kept_set))}
        self._tracks = [self._tracks[i] for i in sorted(kept_set)]
        self._order = [old_to_new[i] for i in kept]
        # Clamp _pos in case it pointed past the new end.
        self._pos = min(self._pos, len(self._order) - 1)

    def set_shuffle(self, shuffle: bool) -> None:
        if shuffle == self._shuffle:
            return
        self._shuffle = shuffle
        if not self._tracks:
            return
        current_track_idx = self._order[self._pos] if self._pos >= 0 else -1
        if shuffle:
            self._shuffled_order(current_track_idx)
            self._pos = 0
        else:
            # Restore original order; keep current track as reference point
            self._order = list(range(len(self._tracks)))
            self._pos = current_track_idx if current_track_idx >= 0 else 0

    def set_repeat(self, repeat: bool) -> None:
        self._repeat = repeat

    def queue_tracks(self) -> tuple[list[Track], int]:
        """Return (tracks_in_playback_order, pos) for API serialisation.

        Mirrors get_state() but returns Track objects rather than Paths so
        the server can call TrackOut.from_track() without an extra lookup.
        Returns ([], -1) when the queue is empty.
        """
        return [self._tracks[i] for i in self._order], self._pos

    def get_state(self) -> tuple[list[Path], int, bool, bool]:
        """Return (tracks_in_playback_order, pos, shuffle, repeat) for persistence.

        Tracks are returned in the current playback order so the shuffle
        sequence can be faithfully restored without re-shuffling.
        """
        ordered_paths = [self._tracks[i].file_path for i in self._order]
        return ordered_paths, self._pos, self._shuffle, self._repeat

    def restore(
        self, tracks: list[Track], pos: int, shuffle: bool, repeat: bool
    ) -> None:
        """Restore queue from persisted state.

        *tracks* must already be in playback order (as returned by get_state).
        The order is taken as-is so shuffle sequences survive restarts.
        """
        self._tracks = list(tracks)
        self._order = list(range(len(tracks)))
        self._pos = pos if tracks else -1
        self._shuffle = shuffle
        self._repeat = repeat

    def add_to_queue(self, track: Track) -> None:
        """Append *track* to the end of the queue."""
        idx = len(self._tracks)
        self._tracks.append(track)
        self._order.append(idx)
        if self._pos < 0:
            self._pos = 0

    def insert_at(self, track: Track, display_idx: int) -> None:
        """Insert *track* at display position *display_idx* in the queue.

        *display_idx* is clamped to [0, len(_order)] so out-of-range values
        are safe.  Adjusts _pos if the insertion falls before it.
        """
        idx = len(self._tracks)
        self._tracks.append(track)
        insert_pos = max(0, min(display_idx, len(self._order)))
        self._order.insert(insert_pos, idx)
        if self._pos < 0:
            self._pos = 0
        elif insert_pos <= self._pos:
            self._pos += 1

    def add_album_to_queue(self, tracks: list[Track]) -> None:
        """Append all *tracks* to the end of the queue in order."""
        for track in tracks:
            self.add_to_queue(track)

    def play_album_next(self, tracks: list[Track]) -> None:
        """Insert all *tracks* immediately after the current position in order.

        Each track is inserted at a successive offset so the album plays in
        the correct sequence.
        """
        for offset, track in enumerate(tracks):
            insert_at = (self._pos + 1 + offset) if self._pos >= 0 else offset
            idx = len(self._tracks)
            self._tracks.append(track)
            self._order.insert(insert_at, idx)
        if self._pos < 0 and tracks:
            self._pos = 0

    def insert_album_at(self, tracks: list[Track], display_idx: int) -> None:
        """Insert all *tracks* starting at display position *display_idx* in order.

        Adjusts *_pos* for each insertion so the currently playing track does
        not change.
        """
        for offset, track in enumerate(tracks):
            self.insert_at(track, display_idx + offset)

    def play_next(self, track: Track) -> None:
        """Insert *track* immediately after the current position.

        If the queue is empty the track becomes the first (and current) item.
        Any existing (non-current) occurrence of the same track is removed
        first so the track never appears twice in the queue.
        """
        # Remove any existing non-current occurrence to avoid duplicates.
        existing = next(
            (
                i
                for i, ti in enumerate(self._order)
                if i != self._pos and self._tracks[ti].file_path == track.file_path
            ),
            None,
        )
        if existing is not None:
            self._order.pop(existing)
            # Keep _pos consistent if we removed an entry before it.
            if self._pos >= 0 and existing < self._pos:
                self._pos -= 1

        idx = len(self._tracks)
        self._tracks.append(track)
        insert_at = self._pos + 1 if self._pos >= 0 else 0
        self._order.insert(insert_at, idx)
        if self._pos < 0:
            self._pos = 0

    def move(self, from_idx: int, to_idx: int) -> None:
        """Move the track at display position *from_idx* to *to_idx*.

        Both indices are positions in the current playback order (i.e. into
        ``_order``).  ``_pos`` is updated so the currently playing track does
        not change.
        """
        if from_idx == to_idx:
            return
        n = len(self._order)
        if not (0 <= from_idx < n and 0 <= to_idx < n):
            raise IndexError(f"Queue index out of range: {from_idx}, {to_idx}")

        item = self._order.pop(from_idx)
        self._order.insert(to_idx, item)

        # Adjust _pos so the same track remains current.
        if self._pos < 0:
            return
        if from_idx == self._pos:
            self._pos = to_idx
        elif from_idx < self._pos <= to_idx:
            self._pos -= 1
        elif to_idx <= self._pos < from_idx:
            self._pos += 1

    def _shuffled_order(self, anchor_idx: int) -> None:
        """Shuffle _order so anchor_idx appears first."""
        rest = [i for i in range(len(self._tracks)) if i != anchor_idx]
        random.shuffle(rest)
        self._order = ([anchor_idx] if anchor_idx >= 0 else []) + rest


# ---------------------------------------------------------------------------
# MpvPlaybackEngine
# ---------------------------------------------------------------------------

# Properties to observe from mpv for state tracking
_OBSERVED: list[tuple[int, str]] = [
    (1, "time-pos"),
    (2, "duration"),
    (3, "pause"),
]


class MpvPlaybackEngine:
    """Controls mpv via its JSON IPC socket.

    mpv is started as a subprocess with --input-ipc-server pointing at a
    temporary Unix socket. A background thread reads the event stream and
    updates self.state. Commands are sent synchronously via _send_command.
    """

    def __init__(self, mpv_bin: str = "mpv") -> None:
        self.state = PlaybackState()
        self.on_track_end: Callable[[], None] | None = None
        self.on_file_loaded: Callable[[], None] | None = None
        self._mpv_bin = mpv_bin
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._sock_path = ""
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._start_mpv()

    def _start_mpv(self) -> None:  # pragma: no cover
        """Launch mpv and connect to its IPC socket."""
        tmp = tempfile.mktemp(suffix=".sock", prefix="kamp-mpv-")
        self._sock_path = tmp
        self._proc = subprocess.Popen(
            [
                self._mpv_bin,
                "--no-video",
                "--idle=yes",
                "--really-quiet",
                f"--input-ipc-server={tmp}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._connect_socket()
        self._observe_properties()
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="mpv-reader"
        )
        self._reader_thread.start()

    def _connect_socket(self, timeout: float = 5.0) -> None:  # pragma: no cover
        """Poll until mpv creates its IPC socket, then connect."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if Path(self._sock_path).exists():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(
                f"mpv IPC socket did not appear at {self._sock_path} "
                f"within {timeout}s"
            )
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._sock_path)
        self._sock = sock

    def _observe_properties(self) -> None:  # pragma: no cover
        """Ask mpv to stream property changes for state tracking."""
        for obs_id, prop in _OBSERVED:
            self._send_command("observe_property", obs_id, prop)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def play(self, path: Path) -> None:
        self._send_command("loadfile", str(path), "replace")
        # Explicitly unpause so calling play() after pause() always starts playback.
        self._send_command("set_property", "pause", False)

    def load_paused(self, path: Path, position: float = 0.0) -> None:
        """Load *path* into mpv, paused at *position*, without starting playback.

        Used on daemon startup to restore the previous session state without
        auto-resuming — the user must press play explicitly.

        The seek is deferred to the ``file-loaded`` event callback so it only
        runs after the demuxer is ready — seek commands sent before that point
        are silently dropped by mpv.
        """
        if position > 0:
            # One-shot: seek to position when mpv confirms the file is ready.
            def _seek_then_clear() -> None:
                self.seek(position)
                self.on_file_loaded = None

            self.on_file_loaded = _seek_then_clear
        self._send_command("loadfile", str(path), "replace")
        self._send_command("set_property", "pause", True)

    def pause(self) -> None:
        self._send_command("set_property", "pause", True)

    def resume(self) -> None:
        self._send_command("set_property", "pause", False)

    def seek(self, position: float) -> None:
        self._send_command("seek", position, "absolute")

    def stop(self) -> None:
        # Pause and seek to the beginning rather than unloading the file.
        # mpv's "stop" command unloads the file, making resume() a no-op.
        # Pausing + seeking keeps the track loaded so play() via resume() works.
        self._send_command("set_property", "pause", True)
        self._send_command("seek", 0, "absolute")

    @property
    def volume(self) -> int:
        return self.state.volume

    @volume.setter
    def volume(self, value: int) -> None:
        self.state.volume = max(0, min(100, value))
        self._send_command("set_property", "volume", self.state.volume)

    def shutdown(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ------------------------------------------------------------------
    # Internal: IPC send/receive
    # ------------------------------------------------------------------

    def _send_command(self, *args: object) -> None:
        """Serialize and send a command to mpv over the IPC socket."""
        if self._sock is None:
            return
        msg = json.dumps({"command": list(args)}) + "\n"
        with self._lock:
            try:
                self._sock.sendall(msg.encode())
            except OSError:
                logger.warning("Failed to send command to mpv: %s", args)

    def _read_loop(self) -> None:  # pragma: no cover
        """Background thread: read JSON events from mpv and dispatch them."""
        if self._sock is None:
            return
        buf = b""
        while True:
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    event = json.loads(line)
                    self._handle_event(event)
                except json.JSONDecodeError:
                    pass

    def _handle_event(self, event: dict[str, object]) -> None:
        """Update state and fire callbacks in response to an mpv event."""
        name = event.get("event")

        if name == "property-change":
            prop = event.get("name")
            data = event.get("data")
            if prop == "time-pos" and isinstance(data, (int, float)):
                self.state.position = float(data)
            elif prop == "duration" and isinstance(data, (int, float)):
                self.state.duration = float(data)
            elif prop == "pause" and isinstance(data, bool):
                self.state.playing = not data

        elif name == "file-loaded":
            if self.on_file_loaded is not None:
                self.on_file_loaded()

        elif name == "end-file":
            # Only fire on natural end-of-file, not user-initiated stops.
            if event.get("reason") == "eof" and self.on_track_end is not None:
                self.on_track_end()
