"""Tests for kamp_core.media_controller."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kamp_core.media_controller import NullMediaController

_DARWIN = platform.system() == "Darwin"
_SKIP_NON_DARWIN = pytest.mark.skipif(not _DARWIN, reason="macOS only")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track(tmp_path: Path) -> "Track":  # noqa: F821
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
# NullMediaController (runs everywhere)
# ---------------------------------------------------------------------------


class TestNullMediaController:
    def test_start_is_noop(self) -> None:
        NullMediaController().start()

    def test_update_is_noop(self, tmp_path: Path) -> None:
        NullMediaController().update(
            _make_track(tmp_path), playing=True, position=30.0, duration=300.0
        )

    def test_stop_is_noop(self) -> None:
        NullMediaController().stop()

    def test_update_with_none_track(self) -> None:
        NullMediaController().update(None, playing=False, position=0.0, duration=0.0)


# ---------------------------------------------------------------------------
# CoreAudioMediaController — patched objc (macOS only)
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_objc(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Inject a mock objc module so no real framework is loaded."""
    mock = MagicMock(name="objc")
    mock.lookUpClass.side_effect = lambda name: MagicMock(name=name)
    monkeypatch.setitem(sys.modules, "objc", mock)
    return mock


class TestCoreAudioMediaController:
    def _make_mc(self) -> "CoreAudioMediaController":  # noqa: F821
        from kamp_core.media_controller import CoreAudioMediaController

        return CoreAudioMediaController()

    def test_start_loads_media_player_framework(self, mock_objc: MagicMock) -> None:
        mc = self._make_mc()
        mc.start()

        mock_objc.loadBundle.assert_called_once()
        assert mock_objc.loadBundle.call_args[0][0] == "MediaPlayer"

    def test_start_acquires_nowplaying_center(self, mock_objc: MagicMock) -> None:
        NPC = MagicMock(name="MPNowPlayingInfoCenter")
        mock_objc.lookUpClass.side_effect = lambda name: (
            NPC if name == "MPNowPlayingInfoCenter" else MagicMock(name=name)
        )
        mc = self._make_mc()
        mc.start()

        NPC.defaultCenter.assert_called_once()
        assert mc._npc is NPC.defaultCenter()

    def test_update_pushes_nowplaying_dict(
        self, mock_objc: MagicMock, tmp_path: Path
    ) -> None:
        NPC = MagicMock(name="MPNowPlayingInfoCenter")
        mock_objc.lookUpClass.side_effect = lambda name: (
            NPC if name == "MPNowPlayingInfoCenter" else MagicMock(name=name)
        )
        mc = self._make_mc()
        mc.start()

        mc.update(_make_track(tmp_path), playing=True, position=42.0, duration=300.0)

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
        self, mock_objc: MagicMock, tmp_path: Path
    ) -> None:
        mc = self._make_mc()
        mc.start()
        mc.update(_make_track(tmp_path), playing=False, position=0.0, duration=300.0)

        info = mc._npc.setNowPlayingInfo_.call_args[0][0]
        assert info["MPNowPlayingInfoPropertyPlaybackRate"] == 0.0

    def test_update_with_none_track_clears_info(self, mock_objc: MagicMock) -> None:
        mc = self._make_mc()
        mc.start()
        mc.update(None, playing=False, position=0.0, duration=0.0)

        mc._npc.setNowPlayingInfo_.assert_called_with(None)

    def test_stop_clears_nowplaying_info(self, mock_objc: MagicMock) -> None:
        mc = self._make_mc()
        mc.start()
        mc.stop()

        mc._npc.setNowPlayingInfo_.assert_called_with(None)

    def test_update_before_start_is_noop(self, tmp_path: Path) -> None:
        from kamp_core.media_controller import CoreAudioMediaController

        mc = CoreAudioMediaController()
        mc.update(_make_track(tmp_path), playing=True, position=0.0, duration=300.0)


# ---------------------------------------------------------------------------
# make_media_controller dispatch
# ---------------------------------------------------------------------------


class TestMakeMediaController:
    def test_returns_null_on_non_darwin(self) -> None:
        from kamp_core.media_controller import (
            NullMediaController,
            make_media_controller,
        )

        with patch("kamp_core.media_controller.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            mc = make_media_controller()
        assert isinstance(mc, NullMediaController)

    @_SKIP_NON_DARWIN
    def test_returns_core_audio_on_darwin(self) -> None:
        from kamp_core.media_controller import (
            CoreAudioMediaController,
            make_media_controller,
        )

        mc = make_media_controller()
        assert isinstance(mc, CoreAudioMediaController)

    def test_returns_null_if_core_audio_raises(self) -> None:
        from kamp_core.media_controller import (
            NullMediaController,
            make_media_controller,
        )

        with patch(
            "kamp_core.media_controller.CoreAudioMediaController.__init__",
            side_effect=RuntimeError("boom"),
        ):
            with patch("kamp_core.media_controller.platform") as mock_platform:
                mock_platform.system.return_value = "Darwin"
                mc = make_media_controller()
        assert isinstance(mc, NullMediaController)
