"""Tests for kamp_core.media_controller."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track(tmp_path: Path) -> "Track":  # noqa: F821
    """Return a minimal Track fixture."""
    from kamp_core.library import Track

    return Track(
        file_path=tmp_path / "track.mp3",
        title="Pyramid Song",
        artist="Radiohead",
        album_artist="Radiohead",
        album="Amnesiac",
        year="2001",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
    )


# ---------------------------------------------------------------------------
# NullMediaController
# ---------------------------------------------------------------------------


class TestNullMediaController:
    def test_null_start_is_noop(self) -> None:
        from kamp_core.media_controller import NullMediaController

        mc = NullMediaController()
        mc.start()  # must not raise

    def test_null_update_is_noop(self, tmp_path: Path) -> None:
        from kamp_core.media_controller import NullMediaController

        mc = NullMediaController()
        mc.update(_make_track(tmp_path), playing=True, position=30.0, duration=300.0)

    def test_null_stop_is_noop(self) -> None:
        from kamp_core.media_controller import NullMediaController

        mc = NullMediaController()
        mc.stop()

    def test_null_update_with_none_track(self) -> None:
        from kamp_core.media_controller import NullMediaController

        mc = NullMediaController()
        mc.update(None, playing=False, position=0.0, duration=0.0)


# ---------------------------------------------------------------------------
# CoreAudioMediaController — patched objc/AppKit
# ---------------------------------------------------------------------------


class _FakeHandler:
    """Pure-Python stand-in for the lazily-built NSObject command handler.

    Replaces the real _get_handler_class() result in tests so no actual
    NSObject subclass (which requires a live PyObjC runtime) is created.
    """

    _on_next: object = None
    _on_prev: object = None

    @classmethod
    def alloc(cls) -> "_FakeHandler":
        return cls()

    def init(self) -> "_FakeHandler":
        return self

    def handleNext_(self, event: object) -> int:
        if self._on_next is not None:
            self._on_next()  # type: ignore[operator]
        return 0

    def handlePrev_(self, event: object) -> int:
        if self._on_prev is not None:
            self._on_prev()  # type: ignore[operator]
        return 0


@pytest.fixture()
def mock_handler_class(monkeypatch: pytest.MonkeyPatch) -> type:
    """Patch _get_handler_class to return _FakeHandler (no NSObject needed)."""
    import kamp_core.media_controller as _mc_mod

    monkeypatch.setattr(_mc_mod, "_get_handler_class", lambda: _FakeHandler)
    return _FakeHandler


@pytest.fixture()
def mock_objc(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Inject a mock objc module so no real framework is ever loaded."""
    mock = MagicMock(name="objc")

    # lookUpClass returns a fresh named mock per class name so tests can
    # distinguish MPNowPlayingInfoCenter from MPRemoteCommandCenter etc.
    def _look_up(name: str) -> MagicMock:
        return MagicMock(name=name)

    mock.lookUpClass.side_effect = _look_up
    monkeypatch.setitem(sys.modules, "objc", mock)
    return mock


@pytest.fixture()
def mock_appkit(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub out AppKit so artwork path doesn't import real framework."""
    mock = MagicMock(name="AppKit")
    monkeypatch.setitem(sys.modules, "AppKit", mock)
    return mock


@pytest.fixture()
def mock_foundation(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock(name="Foundation")
    monkeypatch.setitem(sys.modules, "Foundation", mock)
    return mock


def _build_rcc_mock(mock_objc: MagicMock) -> MagicMock:
    """Return a properly structured MPRemoteCommandCenter mock.

    Configures sharedCommandCenter() → shared, with .nextTrackCommand() and
    .previousTrackCommand() each returning a stable mock command object.
    """
    shared = MagicMock(name="shared_rcc")
    next_cmd = MagicMock(name="nextTrackCommand")
    prev_cmd = MagicMock(name="previousTrackCommand")
    # Return the same command mock on every call so assertions match.
    shared.nextTrackCommand.return_value = next_cmd
    shared.previousTrackCommand.return_value = prev_cmd

    RCC = MagicMock(name="MPRemoteCommandCenter")
    RCC.sharedCommandCenter.return_value = shared

    NPC = MagicMock(name="MPNowPlayingInfoCenter")

    def _look_up(name: str) -> MagicMock:
        if name == "MPRemoteCommandCenter":
            return RCC
        if name == "MPNowPlayingInfoCenter":
            return NPC
        return MagicMock(name=name)

    mock_objc.lookUpClass.side_effect = _look_up
    return shared


@pytest.mark.usefixtures("mock_handler_class")
class TestCoreAudioMediaController:
    def _make_mc(self) -> "CoreAudioMediaController":  # noqa: F821
        from kamp_core.media_controller import CoreAudioMediaController

        return CoreAudioMediaController(
            on_next=MagicMock(),
            on_prev=MagicMock(),
            on_play_pause=MagicMock(),
        )

    def test_start_loads_media_player_framework(
        self, mock_objc: MagicMock, mock_appkit: MagicMock, mock_foundation: MagicMock
    ) -> None:
        _build_rcc_mock(mock_objc)
        mc = self._make_mc()
        mc.start()

        mock_objc.loadBundle.assert_called_once()
        args = mock_objc.loadBundle.call_args
        # First positional arg is the bundle name
        assert args[0][0] == "MediaPlayer"

    def test_start_enables_and_registers_next_prev_commands(
        self, mock_objc: MagicMock, mock_appkit: MagicMock, mock_foundation: MagicMock
    ) -> None:
        shared = _build_rcc_mock(mock_objc)
        mc = self._make_mc()
        mc.start()

        shared.nextTrackCommand().setEnabled_.assert_called_with(True)
        shared.previousTrackCommand().setEnabled_.assert_called_with(True)
        # Uses target-action (not blocks) to avoid PyObjC block-signature issue.
        shared.nextTrackCommand().addTarget_action_.assert_called_once()
        shared.previousTrackCommand().addTarget_action_.assert_called_once()

    def test_next_handler_calls_on_next(
        self, mock_objc: MagicMock, mock_appkit: MagicMock, mock_foundation: MagicMock
    ) -> None:
        _build_rcc_mock(mock_objc)
        on_next = MagicMock()
        from kamp_core.media_controller import CoreAudioMediaController

        mc = CoreAudioMediaController(
            on_next=on_next, on_prev=MagicMock(), on_play_pause=MagicMock()
        )
        mc.start()

        # Invoke the NSObject handler method directly.
        result = mc._cmd_handler.handleNext_(None)

        on_next.assert_called_once()
        assert result == 0  # MPRemoteCommandHandlerStatusSuccess

    def test_prev_handler_calls_on_prev(
        self, mock_objc: MagicMock, mock_appkit: MagicMock, mock_foundation: MagicMock
    ) -> None:
        _build_rcc_mock(mock_objc)
        on_prev = MagicMock()
        from kamp_core.media_controller import CoreAudioMediaController

        mc = CoreAudioMediaController(
            on_next=MagicMock(), on_prev=on_prev, on_play_pause=MagicMock()
        )
        mc.start()

        result = mc._cmd_handler.handlePrev_(None)

        on_prev.assert_called_once()
        assert result == 0

    def test_update_pushes_nowplaying_info(
        self,
        mock_objc: MagicMock,
        mock_appkit: MagicMock,
        mock_foundation: MagicMock,
        tmp_path: Path,
    ) -> None:
        shared = _build_rcc_mock(mock_objc)
        NPC = mock_objc.lookUpClass("MPNowPlayingInfoCenter")
        mc = self._make_mc()
        mc.start()

        track = _make_track(tmp_path)
        # Suppress extract_art so artwork branch is skipped.
        with patch("kamp_core.media_controller.extract_art", return_value=None):
            mc.update(track, playing=True, position=42.0, duration=300.0)

        npc_instance = NPC.defaultCenter()
        npc_instance.setNowPlayingInfo_.assert_called()
        info: dict = npc_instance.setNowPlayingInfo_.call_args[0][0]

        assert info["MPMediaItemPropertyTitle"] == "Pyramid Song"
        assert info["MPMediaItemPropertyArtist"] == "Radiohead"
        assert info["MPMediaItemPropertyAlbumTitle"] == "Amnesiac"
        assert info["MPNowPlayingInfoPropertyElapsedPlaybackTime"] == 42.0
        assert info["MPMediaItemPropertyPlaybackDuration"] == 300.0
        assert info["MPNowPlayingInfoPropertyPlaybackRate"] == 1.0

    def test_update_paused_sets_rate_zero(
        self,
        mock_objc: MagicMock,
        mock_appkit: MagicMock,
        mock_foundation: MagicMock,
        tmp_path: Path,
    ) -> None:
        _build_rcc_mock(mock_objc)
        NPC = mock_objc.lookUpClass("MPNowPlayingInfoCenter")
        mc = self._make_mc()
        mc.start()

        with patch("kamp_core.media_controller.extract_art", return_value=None):
            mc.update(
                _make_track(tmp_path), playing=False, position=0.0, duration=300.0
            )

        info = NPC.defaultCenter().setNowPlayingInfo_.call_args[0][0]
        assert info["MPNowPlayingInfoPropertyPlaybackRate"] == 0.0

    def test_update_with_none_track_clears_info(
        self, mock_objc: MagicMock, mock_appkit: MagicMock, mock_foundation: MagicMock
    ) -> None:
        _build_rcc_mock(mock_objc)
        NPC = mock_objc.lookUpClass("MPNowPlayingInfoCenter")
        mc = self._make_mc()
        mc.start()

        mc.update(None, playing=False, position=0.0, duration=0.0)

        NPC.defaultCenter().setNowPlayingInfo_.assert_called_with(None)

    def test_stop_removes_next_and_prev_handlers(
        self, mock_objc: MagicMock, mock_appkit: MagicMock, mock_foundation: MagicMock
    ) -> None:
        shared = _build_rcc_mock(mock_objc)
        mc = self._make_mc()
        mc.start()

        handler = mc._cmd_handler
        mc.stop()

        shared.nextTrackCommand().removeTarget_.assert_called_with(handler)
        shared.previousTrackCommand().removeTarget_.assert_called_with(handler)

    def test_stop_clears_nowplaying_info(
        self, mock_objc: MagicMock, mock_appkit: MagicMock, mock_foundation: MagicMock
    ) -> None:
        _build_rcc_mock(mock_objc)
        NPC = mock_objc.lookUpClass("MPNowPlayingInfoCenter")
        mc = self._make_mc()
        mc.start()
        mc.stop()

        NPC.defaultCenter().setNowPlayingInfo_.assert_called_with(None)

    def test_update_before_start_is_noop(self, tmp_path: Path) -> None:
        """update() before start() must not raise (npc is None)."""
        from kamp_core.media_controller import CoreAudioMediaController

        mc = CoreAudioMediaController(
            on_next=MagicMock(), on_prev=MagicMock(), on_play_pause=MagicMock()
        )
        mc.update(_make_track(tmp_path), playing=True, position=0.0, duration=300.0)


# ---------------------------------------------------------------------------
# make_media_controller dispatch
# ---------------------------------------------------------------------------


class TestMakeMediaController:
    def test_returns_core_audio_on_darwin(self) -> None:
        from kamp_core.media_controller import (
            CoreAudioMediaController,
            make_media_controller,
        )

        mc = make_media_controller(
            on_next=MagicMock(), on_prev=MagicMock(), on_play_pause=MagicMock()
        )
        assert isinstance(mc, CoreAudioMediaController)

    def test_returns_null_if_init_raises(self) -> None:
        from kamp_core.media_controller import (
            NullMediaController,
            make_media_controller,
        )

        with patch(
            "kamp_core.media_controller.CoreAudioMediaController.__init__",
            side_effect=RuntimeError("boom"),
        ):
            mc = make_media_controller(
                on_next=MagicMock(), on_prev=MagicMock(), on_play_pause=MagicMock()
            )
        assert isinstance(mc, NullMediaController)
