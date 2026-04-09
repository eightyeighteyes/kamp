"""Tests for kamp_core.scrobbler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from kamp_core.library import Track
from kamp_core import scrobbler as _mod
from kamp_core.scrobbler import Scrobbler, authenticate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _track(
    artist: str = "Artist",
    title: str = "Song",
    album: str = "Album",
    album_artist: str = "Artist",
    track_number: int = 1,
    mb_recording_id: str = "",
) -> Track:
    return Track(
        file_path=Path("/music/01.mp3"),
        title=title,
        artist=artist,
        album_artist=album_artist,
        album=album,
        year="2024",
        track_number=track_number,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id=mb_recording_id,
    )


def _make_scrobbler() -> tuple[Scrobbler, MagicMock]:
    """Return a Scrobbler and its mocked pylast network."""
    mock_network = MagicMock()
    with patch("kamp_core.scrobbler.pylast.LastFMNetwork", return_value=mock_network):
        s = Scrobbler(session_key="test-session-key")
    return s, mock_network


# ---------------------------------------------------------------------------
# authenticate()
# ---------------------------------------------------------------------------


class TestAuthenticate:
    def test_returns_session_key_from_network(self) -> None:
        mock_network = MagicMock()
        mock_network.session_key = "returned-session-key"
        with patch(
            "kamp_core.scrobbler.pylast.LastFMNetwork", return_value=mock_network
        ) as mock_cls:
            result = authenticate("alice", "secret")
        assert result == "returned-session-key"

    def test_passes_md5_password_hash(self) -> None:
        """Password is hashed with MD5 before being sent to pylast."""
        mock_network = MagicMock()
        mock_network.session_key = "sk"
        with patch(
            "kamp_core.scrobbler.pylast.LastFMNetwork", return_value=mock_network
        ) as mock_cls:
            with patch(
                "kamp_core.scrobbler.pylast.md5", return_value="md5hash"
            ) as mock_md5:
                authenticate("alice", "secret")
        mock_md5.assert_called_once_with("secret")
        _, kwargs = mock_cls.call_args
        assert kwargs.get("password_hash") == "md5hash"
        assert "password" not in kwargs


# ---------------------------------------------------------------------------
# Scrobbler.on_track_changed
# ---------------------------------------------------------------------------


class TestOnTrackChanged:
    def test_sends_now_playing_when_track_is_not_none(self) -> None:
        s, net = _make_scrobbler()
        t = _track()
        s.on_track_changed(t)
        net.update_now_playing.assert_called_once()
        call_kwargs = net.update_now_playing.call_args.kwargs
        assert call_kwargs["artist"] == "Artist"
        assert call_kwargs["title"] == "Song"

    def test_does_not_send_now_playing_when_track_is_none(self) -> None:
        s, net = _make_scrobbler()
        s.on_track_changed(None)
        net.update_now_playing.assert_not_called()

    def test_resets_listening_time(self) -> None:
        """Starting a new track resets cumulative listening seconds."""
        s, net = _make_scrobbler()
        t = _track()
        s.on_track_changed(t)
        # Simulate some ticks
        import time

        with patch("kamp_core.scrobbler.time.monotonic", return_value=1000.0):
            s.on_track_changed(t)
        with patch("kamp_core.scrobbler.time.monotonic", return_value=1001.0):
            s.tick(t, playing=True)
        with patch("kamp_core.scrobbler.time.monotonic", return_value=1002.0):
            s.tick(t, playing=True)
        assert s._play_listening_secs < 5.0  # only ~2 seconds since reset

    def test_resets_scrobbled_flag(self) -> None:
        """Loading a new track after scrobble resets the scrobbled flag."""
        s, net = _make_scrobbler()
        t = _track()
        s._scrobbled = True
        s.on_track_changed(t)
        assert s._scrobbled is False

    def test_album_artist_omitted_when_same_as_artist(self) -> None:
        """album_artist is None in the API call when it equals artist."""
        s, net = _make_scrobbler()
        t = _track(artist="Solo", album_artist="Solo")
        s.on_track_changed(t)
        call_kwargs = net.update_now_playing.call_args.kwargs
        assert call_kwargs.get("album_artist") is None

    def test_album_artist_sent_when_differs_from_artist(self) -> None:
        s, net = _make_scrobbler()
        t = _track(artist="Solo", album_artist="Various Artists")
        s.on_track_changed(t)
        call_kwargs = net.update_now_playing.call_args.kwargs
        assert call_kwargs.get("album_artist") == "Various Artists"

    def test_now_playing_exception_does_not_propagate(self) -> None:
        """pylast exceptions must never crash the player."""
        s, net = _make_scrobbler()
        net.update_now_playing.side_effect = Exception("network error")
        # Should not raise
        s.on_track_changed(_track())

    def test_mb_recording_id_passed_as_mbid(self) -> None:
        s, net = _make_scrobbler()
        t = _track(mb_recording_id="mbid-123")
        s.on_track_changed(t)
        call_kwargs = net.update_now_playing.call_args.kwargs
        assert call_kwargs.get("mbid") == "mbid-123"

    def test_empty_mb_recording_id_passed_as_none(self) -> None:
        s, net = _make_scrobbler()
        t = _track(mb_recording_id="")
        s.on_track_changed(t)
        call_kwargs = net.update_now_playing.call_args.kwargs
        assert call_kwargs.get("mbid") is None


# ---------------------------------------------------------------------------
# Scrobbler.tick — 30-second threshold
# ---------------------------------------------------------------------------


class TestTick:
    def _advance(self, s: Scrobbler, track: Track, seconds: float) -> None:
        """Simulate *seconds* of continuous playback via tick calls."""
        step = 1.0
        t = 1000.0
        with patch("kamp_core.scrobbler.time.monotonic") as mock_mono:
            mock_mono.return_value = t
            s.on_track_changed(track)
            elapsed = 0.0
            while elapsed < seconds:
                t += step
                elapsed += step
                mock_mono.return_value = t
                s.tick(track, playing=True)

    def test_scrobble_fires_at_30s(self) -> None:
        s, net = _make_scrobbler()
        t = _track()
        self._advance(s, t, 31.0)
        net.scrobble.assert_called_once()

    def test_no_scrobble_before_30s(self) -> None:
        s, net = _make_scrobbler()
        t = _track()
        self._advance(s, t, 29.0)
        net.scrobble.assert_not_called()

    def test_pause_does_not_accumulate_listening_time(self) -> None:
        """Ticks with playing=False do not advance listening time."""
        s, net = _make_scrobbler()
        t = _track()
        step = 1.0
        start = 1000.0
        with patch("kamp_core.scrobbler.time.monotonic") as mock_mono:
            mock_mono.return_value = start
            s.on_track_changed(t)
            # 15 seconds of playing
            for i in range(1, 16):
                mock_mono.return_value = start + i
                s.tick(t, playing=True)
            # 60 seconds of pause — should NOT count
            for i in range(16, 76):
                mock_mono.return_value = start + i
                s.tick(t, playing=False)
        net.scrobble.assert_not_called()

    def test_resumed_play_continues_accumulating(self) -> None:
        """Pause then resume keeps cumulative listening time — same play instance."""
        s, net = _make_scrobbler()
        t = _track()
        step = 1.0
        start = 1000.0
        with patch("kamp_core.scrobbler.time.monotonic") as mock_mono:
            mock_mono.return_value = start
            s.on_track_changed(t)
            # 20 seconds playing
            for i in range(1, 21):
                mock_mono.return_value = start + i
                s.tick(t, playing=True)
            # pause
            mock_mono.return_value = start + 21
            s.tick(t, playing=False)
            # 15 more seconds playing
            for i in range(22, 37):
                mock_mono.return_value = start + i
                s.tick(t, playing=True)
        # 20 + 15 = 35 seconds of listening → should scrobble
        net.scrobble.assert_called_once()

    def test_scrobble_fires_only_once_per_play_instance(self) -> None:
        """30-second threshold must not trigger a second scrobble."""
        s, net = _make_scrobbler()
        t = _track()
        self._advance(s, t, 60.0)
        net.scrobble.assert_called_once()

    def test_new_track_load_allows_fresh_scrobble(self) -> None:
        """After on_track_changed, the 30s counter resets and can scrobble again."""
        s, net = _make_scrobbler()
        t = _track()
        self._advance(s, t, 35.0)
        assert net.scrobble.call_count == 1
        self._advance(s, t, 35.0)
        assert net.scrobble.call_count == 2

    def test_tick_with_none_track_does_not_scrobble(self) -> None:
        s, net = _make_scrobbler()
        with patch("kamp_core.scrobbler.time.monotonic", return_value=1000.0):
            s.on_track_changed(None)
        with patch("kamp_core.scrobbler.time.monotonic", return_value=1031.0):
            s.tick(None, playing=True)
        net.scrobble.assert_not_called()

    def test_scrobble_exception_does_not_propagate(self) -> None:
        s, net = _make_scrobbler()
        net.scrobble.side_effect = Exception("network error")
        t = _track()
        # Should not raise
        self._advance(s, t, 35.0)


# ---------------------------------------------------------------------------
# Scrobbler.on_track_ended — EOF scrobble
# ---------------------------------------------------------------------------


class TestOnTrackEnded:
    def test_scrobble_fires_on_eof_when_not_yet_scrobbled(self) -> None:
        s, net = _make_scrobbler()
        t = _track()
        s.on_track_changed(t)
        s.on_track_ended(t)
        net.scrobble.assert_called_once()

    def test_no_double_scrobble_if_already_scrobbled_at_30s(self) -> None:
        """If 30s threshold already fired, EOF must not scrobble again."""
        s, net = _make_scrobbler()
        t = _track()
        step = 1.0
        start = 1000.0
        with patch("kamp_core.scrobbler.time.monotonic") as mock_mono:
            mock_mono.return_value = start
            s.on_track_changed(t)
            for i in range(1, 35):
                mock_mono.return_value = start + i
                s.tick(t, playing=True)
        s.on_track_ended(t)
        net.scrobble.assert_called_once()

    def test_on_track_ended_with_none_does_not_scrobble(self) -> None:
        s, net = _make_scrobbler()
        s.on_track_ended(None)
        net.scrobble.assert_not_called()

    def test_eof_scrobble_exception_does_not_propagate(self) -> None:
        s, net = _make_scrobbler()
        net.scrobble.side_effect = Exception("network error")
        t = _track()
        s.on_track_changed(t)
        # Should not raise
        s.on_track_ended(t)

    def test_scrobble_includes_artist_and_title(self) -> None:
        s, net = _make_scrobbler()
        t = _track(artist="The Band", title="My Song")
        s.on_track_changed(t)
        s.on_track_ended(t)
        call_kwargs = net.scrobble.call_args.kwargs
        assert call_kwargs["artist"] == "The Band"
        assert call_kwargs["title"] == "My Song"

    def test_scrobble_includes_album(self) -> None:
        s, net = _make_scrobbler()
        t = _track(album="Great Album")
        s.on_track_changed(t)
        s.on_track_ended(t)
        call_kwargs = net.scrobble.call_args.kwargs
        assert call_kwargs["album"] == "Great Album"

    def test_scrobble_includes_timestamp(self) -> None:
        """Scrobble timestamp is the Unix time when the track started."""
        s, net = _make_scrobbler()
        t = _track()
        fixed_time = 1_700_000_000
        with patch("kamp_core.scrobbler.time.time", return_value=float(fixed_time)):
            s.on_track_changed(t)
        s.on_track_ended(t)
        call_kwargs = net.scrobble.call_args.kwargs
        assert call_kwargs["timestamp"] == fixed_time

    def test_repeat_play_scrobbles_twice(self) -> None:
        """Same track played back-to-back (two on_track_changed calls) → two scrobbles."""
        s, net = _make_scrobbler()
        t = _track()
        # First play instance
        s.on_track_changed(t)
        s.on_track_ended(t)
        # Second play instance (same track, new file-loaded event)
        s.on_track_changed(t)
        s.on_track_ended(t)
        assert net.scrobble.call_count == 2
