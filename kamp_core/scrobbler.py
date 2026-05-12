"""Last.fm scrobbling integration.

Tracks cumulative listening time per play instance and scrobbles when either
30 seconds have been listened to, or the track reaches natural end-of-file,
whichever comes first.

Threading model
---------------
HTTP work runs on a daemon-thread worker (``_tx_loop``) that consumes a
``queue.Queue`` of jobs. Callers (engine reader thread for
``on_track_changed`` / ``on_track_ended``, state-saver thread for ``tick``)
snapshot the current play-instance state under ``_state_lock`` and enqueue a
job, then return immediately. A hung Last.fm endpoint can never block the
caller — it only backs up the queue.

Credential note
---------------
LASTFM_API_KEY and LASTFM_API_SECRET are app-level constants registered with
Last.fm. Like beets, picard, Rhythmbox, and other open-source desktop players,
we accept that client-side secrets cannot be fully protected in a distributed
Python application. Last.fm's developer terms permit desktop clients to embed
keys; the key is used for rate limiting and revocation only, not user data
access.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pylast

if TYPE_CHECKING:
    from kamp_core.library import Track

logger = logging.getLogger(__name__)


@dataclass
class _ScrobbleJob:
    """One unit of work for the HTTP worker thread."""

    kind: Literal["now_playing", "scrobble", "shutdown"]
    track: "Track | None" = None
    # Scrobble-only fields. Captured under _state_lock so the worker doesn't
    # need to read mutable per-play-instance state.
    timestamp: int = 0
    listened_secs: float = 0.0


# App-level credentials registered at https://www.last.fm/api/account/create.
# See module docstring for the security rationale.
LASTFM_API_KEY = "edb4b838db9e37e0433c21761e2f7947"
LASTFM_API_SECRET = "76d2c23b31352fe60ce8c1e6ba428a46"

_SCROBBLE_THRESHOLD_SECS = 30.0


def authenticate(username: str, password: str) -> str:
    """Authenticate with Last.fm and return a persistent session key.

    Uses auth.getMobileSession (pylast passes username + MD5 password hash).
    The returned session key never expires and should be stored in config;
    the password is used only here and never persisted.

    Raises pylast.WSError on auth failure (e.g. wrong credentials).
    """
    network = pylast.LastFMNetwork(
        api_key=LASTFM_API_KEY,
        api_secret=LASTFM_API_SECRET,
        username=username,
        password_hash=pylast.md5(password),
    )
    # Accessing session_key triggers auth.getMobileSession when one is not yet set.
    return str(network.session_key)


class Scrobbler:
    """Tracks listening time and submits scrobbles to Last.fm.

    One play instance spans from when a file loads until the next file loads
    (or the track reaches natural EOF). Within a single instance, at most one
    scrobble is submitted.

    Call on_track_changed() when a new file is loaded (including on app
    startup with a restored track). Call tick() at ~1 Hz while the player
    is running. Call on_track_ended() at natural EOF.
    """

    def __init__(self, session_key: str) -> None:
        self._network = pylast.LastFMNetwork(
            api_key=LASTFM_API_KEY,
            api_secret=LASTFM_API_SECRET,
            session_key=session_key,
        )
        # Per-play-instance state, guarded by _state_lock because tick() runs
        # on the state-saver thread while on_track_changed/on_track_ended run
        # on the engine's reader thread.
        self._play_listening_secs: float = 0.0
        self._play_start_timestamp: int = 0  # Unix time; sent with scrobble
        self._scrobbled: bool = False
        self._last_tick_at: float | None = None
        self._last_tick_playing: bool = False
        self._state_lock = threading.Lock()
        # HTTP worker — keeps Last.fm latency off the engine's reader thread
        # so a slow or hung endpoint can never stall mpv event processing
        # (KAMP-284). The queue is unbounded; under normal operation it never
        # holds more than one or two items at a time.
        self._tx_queue: queue.Queue[_ScrobbleJob] = queue.Queue()
        self._tx_thread = threading.Thread(
            target=self._tx_loop, daemon=True, name="scrobbler-tx"
        )
        self._tx_thread.start()

    def on_track_changed(self, track: Track | None) -> None:
        """Call when a new file is loaded. Resets play instance state.

        Sends a now-playing notification to Last.fm when *track* is not None.
        Returns immediately; the HTTP call runs on the scrobbler's worker
        thread.
        """
        with self._state_lock:
            self._play_listening_secs = 0.0
            self._play_start_timestamp = int(time.time())
            self._scrobbled = False
            self._last_tick_at = time.monotonic()
            self._last_tick_playing = False

        if track is None:
            return
        # Last.fm requires artist and title; skip rather than send a 400.
        if not track.artist or not track.title:
            return
        self._tx_queue.put(_ScrobbleJob(kind="now_playing", track=track))

    def tick(self, track: Track | None, playing: bool) -> None:
        """Call at ~1 Hz from the state-saver thread.

        Accumulates listening time while *playing* is True and fires the
        30-second scrobble when the threshold is crossed. Returns immediately;
        any HTTP call runs on the scrobbler's worker thread.
        """
        now = time.monotonic()
        should_scrobble = False
        ts = 0
        secs = 0.0
        with self._state_lock:
            if self._last_tick_at is not None and playing and self._last_tick_playing:
                self._play_listening_secs += now - self._last_tick_at
            self._last_tick_at = now
            self._last_tick_playing = playing

            if (
                track is not None
                and not self._scrobbled
                and self._play_listening_secs >= _SCROBBLE_THRESHOLD_SECS
            ):
                self._scrobbled = True
                should_scrobble = True
                ts = self._play_start_timestamp
                secs = self._play_listening_secs

        if should_scrobble and track is not None:
            self._tx_queue.put(
                _ScrobbleJob(
                    kind="scrobble", track=track, timestamp=ts, listened_secs=secs
                )
            )

    def on_track_ended(self, track: Track | None) -> None:
        """Call at natural EOF. Scrobbles if not already done this instance.

        Returns immediately; the HTTP call runs on the worker thread.
        """
        if track is None:
            return
        should_scrobble = False
        ts = 0
        secs = 0.0
        with self._state_lock:
            if not self._scrobbled:
                self._scrobbled = True
                should_scrobble = True
                ts = self._play_start_timestamp
                secs = self._play_listening_secs
        if should_scrobble:
            self._tx_queue.put(
                _ScrobbleJob(
                    kind="scrobble", track=track, timestamp=ts, listened_secs=secs
                )
            )

    def flush(self) -> None:
        """Block until all enqueued HTTP jobs have completed.

        Intended for tests and for use during ``shutdown()`` so a final scrobble
        has a chance to land before the worker exits. Production callers
        otherwise never need this — the whole point of the worker is that
        callers do not wait on Last.fm.
        """
        self._tx_queue.join()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the worker thread. Best-effort; never blocks daemon shutdown.

        Pushes a sentinel so any in-flight job completes before the worker
        exits, then joins with the timeout. A hung HTTP call beyond *timeout*
        is abandoned — the worker is a daemon thread and dies with the
        process anyway.
        """
        self._tx_queue.put(_ScrobbleJob(kind="shutdown"))
        self._tx_thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Worker — runs on _tx_thread
    # ------------------------------------------------------------------

    def _tx_loop(self) -> None:
        while True:
            job = self._tx_queue.get()
            try:
                if job.kind == "shutdown":
                    return
                if job.kind == "now_playing" and job.track is not None:
                    self._do_now_playing(job.track)
                elif job.kind == "scrobble" and job.track is not None:
                    self._do_scrobble(job.track, job.timestamp, job.listened_secs)
            except Exception:
                # Never let an exception kill the worker — the next job must
                # still get a chance to run.
                logger.warning("Scrobbler worker dispatch failed", exc_info=True)
            finally:
                self._tx_queue.task_done()

    def _do_now_playing(self, track: Track) -> None:
        try:
            self._network.update_now_playing(
                artist=track.artist,
                title=track.title,
                album=track.album or None,
                album_artist=(
                    track.album_artist if track.album_artist != track.artist else None
                ),
                track_number=track.track_number or None,
                duration=None,  # not known at load time; omit
                mbid=track.mb_recording_id or None,
            )
        except Exception:
            logger.warning("Last.fm now-playing update failed", exc_info=True)

    def _do_scrobble(self, track: Track, timestamp: int, listened_secs: float) -> None:
        # Last.fm requires artist and title; skip rather than send a 400.
        if not track.artist or not track.title:
            return
        try:
            self._network.scrobble(
                artist=track.artist,
                title=track.title,
                timestamp=timestamp,
                album=track.album or None,
                album_artist=(
                    track.album_artist if track.album_artist != track.artist else None
                ),
                track_number=track.track_number or None,
                duration=None,
                mbid=track.mb_recording_id or None,
            )
            logger.info(
                "Scrobbled: %s – %s (%.0f s listened)",
                track.artist,
                track.title,
                listened_secs,
            )
        except Exception:
            logger.warning(
                "Last.fm scrobble failed for %s – %s",
                track.artist,
                track.title,
                exc_info=True,
            )
