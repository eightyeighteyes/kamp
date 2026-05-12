"""Tests for kamp_core.scrobbler."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from kamp_core.library import Track
from kamp_core import scrobbler as _mod
from kamp_core.scrobbler import Scrobbler, _ScrobbleJob, authenticate

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
    """Return a Scrobbler and its mocked pylast network.

    The scrobbler's HTTP work runs on a worker thread in production (KAMP-284),
    but unit tests assert synchronously. Wrap ``_tx_queue.put`` so each test
    sees a drained queue immediately after the action. The dedicated
    ``test_call_returns_immediately`` case bypasses this wrapper to validate
    the actual async path.
    """
    mock_network = MagicMock()
    with patch("kamp_core.scrobbler.pylast.LastFMNetwork", return_value=mock_network):
        s = Scrobbler(session_key="test-session-key")
    _orig_put = s._tx_queue.put

    def _draining_put(job: _ScrobbleJob) -> None:
        _orig_put(job)
        s.flush()

    s._tx_queue.put = _draining_put  # type: ignore[method-assign,assignment]
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

    def test_skips_now_playing_when_artist_is_blank(self) -> None:
        """Tracks with no artist tag must not be sent to Last.fm (400 error)."""
        s, net = _make_scrobbler()
        s.on_track_changed(_track(artist="", title="Metronomic Underground"))
        net.update_now_playing.assert_not_called()

    def test_skips_now_playing_when_title_is_blank(self) -> None:
        """Tracks with no title tag must not be sent to Last.fm (400 error)."""
        s, net = _make_scrobbler()
        s.on_track_changed(_track(artist="Stereolab", title=""))
        net.update_now_playing.assert_not_called()

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

    def test_skips_scrobble_when_artist_is_blank(self) -> None:
        """Tracks with no artist tag must not be scrobbled (Last.fm rejects them)."""
        s, net = _make_scrobbler()
        self._advance(s, _track(artist="", title="Metronomic Underground"), 35.0)
        net.scrobble.assert_not_called()


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


# ---------------------------------------------------------------------------
# Worker-thread async behaviour — explicit, no draining helper (KAMP-284)
# ---------------------------------------------------------------------------


class TestAsyncWorker:
    """The caller (engine reader thread, state-saver thread) must NEVER block
    on Last.fm latency. These tests stand up the real async worker and prove
    the contract by hanging pylast on the worker side.
    """

    def _make_async_scrobbler(self) -> tuple[Scrobbler, MagicMock]:
        """Like _make_scrobbler but WITHOUT the draining put-wrapper."""
        mock_network = MagicMock()
        with patch(
            "kamp_core.scrobbler.pylast.LastFMNetwork", return_value=mock_network
        ):
            s = Scrobbler(session_key="test-session-key")
        return s, mock_network

    def test_on_track_ended_returns_immediately_when_http_is_slow(self) -> None:
        """The whole point of the worker (KAMP-284): a 5s Last.fm response
        must not delay the engine's reader thread by more than a few ms."""
        s, net = self._make_async_scrobbler()
        net.scrobble.side_effect = lambda **_: time.sleep(5.0)
        t = _track()
        s.on_track_changed(t)
        start = time.monotonic()
        s.on_track_ended(t)
        elapsed = time.monotonic() - start
        # Generous bound: production needs sub-100ms; we just need to prove
        # we're not waiting on the side_effect's 5s sleep.
        assert elapsed < 0.2, f"on_track_ended took {elapsed:.3f}s; expected <0.2s"

    def test_on_track_changed_returns_immediately_when_http_is_slow(self) -> None:
        s, net = self._make_async_scrobbler()
        net.update_now_playing.side_effect = lambda **_: time.sleep(5.0)
        t = _track()
        start = time.monotonic()
        s.on_track_changed(t)
        elapsed = time.monotonic() - start
        assert elapsed < 0.2, f"on_track_changed took {elapsed:.3f}s; expected <0.2s"

    def test_flush_blocks_until_worker_drains(self) -> None:
        """flush() is the test-side synchronization primitive — it must block
        until the worker has processed every queued job."""
        s, net = self._make_async_scrobbler()
        gate = threading.Event()
        observed_order: list[str] = []

        def _slow_scrobble(**_: object) -> None:
            gate.wait(timeout=2.0)
            observed_order.append("worker_done")

        net.scrobble.side_effect = _slow_scrobble
        t = _track()
        s.on_track_changed(t)
        s.on_track_ended(t)
        # The worker is currently parked in _slow_scrobble waiting on the gate.
        # Releasing the gate, then flush() must wait for the worker to finish.
        gate.set()
        s.flush()
        observed_order.append("flush_returned")
        assert observed_order == ["worker_done", "flush_returned"]

    def test_shutdown_drains_pending_work(self) -> None:
        """shutdown() drains the queue best-effort within the timeout."""
        s, net = self._make_async_scrobbler()
        t = _track()
        s.on_track_changed(t)
        s.on_track_ended(t)
        s.shutdown(timeout=2.0)
        # All enqueued HTTP work completed before the worker exited.
        net.update_now_playing.assert_called()
        net.scrobble.assert_called()
