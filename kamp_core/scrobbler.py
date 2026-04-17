"""Last.fm scrobbling integration.

Tracks cumulative listening time per play instance and scrobbles when either
30 seconds have been listened to, or the track reaches natural end-of-file,
whichever comes first.

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
import time
from typing import TYPE_CHECKING

import pylast

if TYPE_CHECKING:
    from kamp_core.library import Track

logger = logging.getLogger(__name__)

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
        # Per-play-instance state
        self._play_listening_secs: float = 0.0
        self._play_start_timestamp: int = 0  # Unix time; sent with scrobble
        self._scrobbled: bool = False
        self._last_tick_at: float | None = None
        self._last_tick_playing: bool = False

    def on_track_changed(self, track: Track | None) -> None:
        """Call when a new file is loaded. Resets play instance state.

        Sends a now-playing notification to Last.fm when *track* is not None.
        """
        # Reset play instance
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

    def tick(self, track: Track | None, playing: bool) -> None:
        """Call at ~1 Hz from the state-saver thread.

        Accumulates listening time while *playing* is True and fires the
        30-second scrobble when the threshold is crossed.
        """
        now = time.monotonic()

        if self._last_tick_at is not None and playing and self._last_tick_playing:
            self._play_listening_secs += now - self._last_tick_at

        self._last_tick_at = now
        self._last_tick_playing = playing

        if (
            track is not None
            and not self._scrobbled
            and self._play_listening_secs >= _SCROBBLE_THRESHOLD_SECS
        ):
            self._scrobble(track)

    def on_track_ended(self, track: Track | None) -> None:
        """Call at natural EOF. Scrobbles if not already done this instance."""
        if track is not None and not self._scrobbled:
            self._scrobble(track)

    def _scrobble(self, track: Track) -> None:
        self._scrobbled = True
        # Last.fm requires artist and title; skip rather than send a 400.
        if not track.artist or not track.title:
            return
        try:
            self._network.scrobble(
                artist=track.artist,
                title=track.title,
                timestamp=self._play_start_timestamp,
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
                self._play_listening_secs,
            )
        except Exception:
            logger.warning(
                "Last.fm scrobble failed for %s – %s",
                track.artist,
                track.title,
                exc_info=True,
            )
