"""macOS Now Playing widget integration.

Populates MPNowPlayingInfoCenter with current track metadata (title, artist,
album, position) so the macOS Control Center widget stays in sync with
playback.

media keys (next/prev) are handled by Electron's globalShortcut in the
main process — MPRemoteCommandCenter callbacks require an AppKit/CFRunLoop
main loop which the kamp server process does not run.

This module is importable on all platforms.  CoreAudioMediaController.start()
will raise on non-macOS, and make_media_controller() returns NullMediaController
in that case.
"""

from __future__ import annotations

import logging
import platform
from abc import ABC, abstractmethod
from typing import Any

from kamp_core.library import Track

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class MediaController(ABC):
    @abstractmethod
    def start(self) -> None: ...  # pragma: no cover

    @abstractmethod
    def update(
        self,
        track: Track | None,
        playing: bool,
        position: float,
        duration: float,
    ) -> None: ...  # pragma: no cover

    @abstractmethod
    def stop(self) -> None: ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Null implementation (all platforms, tests)
# ---------------------------------------------------------------------------


class NullMediaController(MediaController):
    """No-op implementation used on non-macOS and in tests."""

    def start(self) -> None:
        pass

    def update(
        self,
        track: Track | None,
        playing: bool,
        position: float,
        duration: float,
    ) -> None:
        pass

    def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# macOS implementation
# ---------------------------------------------------------------------------

# MPNowPlayingInfoCenter key constants (strings, not framework imports, so
# tests can verify them without loading the real framework).
_KEY_TITLE = "MPMediaItemPropertyTitle"
_KEY_ARTIST = "MPMediaItemPropertyArtist"
_KEY_ALBUM = "MPMediaItemPropertyAlbumTitle"
_KEY_DURATION = "MPMediaItemPropertyPlaybackDuration"
_KEY_ELAPSED = "MPNowPlayingInfoPropertyElapsedPlaybackTime"
_KEY_RATE = "MPNowPlayingInfoPropertyPlaybackRate"
_KEY_ARTWORK = "MPMediaItemPropertyArtwork"


class CoreAudioMediaController(MediaController):
    """Writes playback state to MPNowPlayingInfoCenter (Control Center widget).

    Command-center (next/prev media keys) is intentionally omitted: callbacks
    require a CFRunLoop main loop which the kamp server process does not run.
    Media keys are handled instead by Electron globalShortcut in the main
    process.
    """

    def __init__(self) -> None:
        # Typed Any because PyObjC classes have no stubs.
        self._npc: Any = None  # MPNowPlayingInfoCenter instance

    def start(self) -> None:
        """Load MediaPlayer framework and grab the shared NowPlaying center."""
        import objc

        objc.loadBundle(
            "MediaPlayer",
            bundle_path="/System/Library/Frameworks/MediaPlayer.framework",
            module_globals={},
        )
        NPC = objc.lookUpClass("MPNowPlayingInfoCenter")
        self._npc = NPC.defaultCenter()
        logger.debug("MediaController started")

    def update(
        self,
        track: Track | None,
        playing: bool,
        position: float,
        duration: float,
    ) -> None:
        """Push current playback state to the Now Playing widget."""
        if self._npc is None:
            return

        if track is None:
            self._npc.setNowPlayingInfo_(None)
            return

        info: dict[str, object] = {
            _KEY_TITLE: track.title,
            _KEY_ARTIST: track.artist,
            _KEY_ALBUM: track.album,
            _KEY_ELAPSED: position,
            _KEY_DURATION: duration,
            _KEY_RATE: 1.0 if playing else 0.0,
        }

        # Artwork — best-effort; any failure is silently ignored.
        # Not covered by unit tests: requires live PyObjC + real audio files.
        try:  # pragma: no cover
            import objc
            from AppKit import NSData, NSImage

            from kamp_core.library import extract_art

            art_bytes = extract_art(track.file_path)
            if art_bytes:
                data = NSData.dataWithBytes_length_(art_bytes, len(art_bytes))
                img = NSImage.alloc().initWithData_(data)
                # initWithImage: is available on macOS and does not require a
                # block, unlike initWithBoundsSize:requestHandler:.
                MPMediaItemArtwork = objc.lookUpClass("MPMediaItemArtwork")
                info[_KEY_ARTWORK] = MPMediaItemArtwork.alloc().initWithImage_(img)
        except Exception:  # pragma: no cover
            pass

        self._npc.setNowPlayingInfo_(info)

    def stop(self) -> None:
        """Clear the Now Playing widget."""
        if self._npc is not None:
            try:
                self._npc.setNowPlayingInfo_(None)
            except Exception:  # pragma: no cover
                pass
        logger.debug("MediaController stopped")


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------


def make_media_controller() -> MediaController:
    """Return CoreAudioMediaController on macOS, NullMediaController elsewhere."""
    if platform.system() != "Darwin":
        return NullMediaController()
    try:
        return CoreAudioMediaController()
    except Exception:
        return NullMediaController()
