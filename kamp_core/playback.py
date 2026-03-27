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

    def pause(self) -> None:
        self._send_command("set_property", "pause", True)

    def resume(self) -> None:
        self._send_command("set_property", "pause", False)

    def seek(self, position: float) -> None:
        self._send_command("seek", position, "absolute")

    def stop(self) -> None:
        self._send_command("stop")

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

        elif name == "end-file":
            # Only fire on natural end-of-file, not user-initiated stops.
            if event.get("reason") == "eof" and self.on_track_end is not None:
                self.on_track_end()
