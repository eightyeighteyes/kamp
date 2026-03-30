"""macOS Now Playing widget and media key integration.

Populates MPNowPlayingInfoCenter with current track metadata and registers
next/prev handlers with MPRemoteCommandCenter. The play/pause key is left
to mpv's own MRUC registration so we don't have to proxy every command.

Must only be imported on macOS — raises ImportError otherwise.

Platform implementations are accessed via make_media_controller(), which
returns a CoreAudioMediaController on macOS or a NullMediaController
elsewhere, satisfying the ADR-7 dispatch-table requirement.
"""

from __future__ import annotations

import logging
import platform
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

if platform.system() != "Darwin":
    raise ImportError("kamp_core.media_controller is only available on macOS")

from kamp_core.library import Track, extract_art

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class MediaController(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def update(
        self,
        track: Track | None,
        playing: bool,
        position: float,
        duration: float,
    ) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...


# ---------------------------------------------------------------------------
# Null implementation
# ---------------------------------------------------------------------------


class NullMediaController(MediaController):
    """No-op implementation used in tests and on non-macOS platforms."""

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

# MPNowPlayingInfoCenter / MPRemoteCommandCenter key constants.
# These are string literals rather than framework imports so tests can run
# without a real MediaPlayer framework present.
_KEY_TITLE = "MPMediaItemPropertyTitle"
_KEY_ARTIST = "MPMediaItemPropertyArtist"
_KEY_ALBUM = "MPMediaItemPropertyAlbumTitle"
_KEY_DURATION = "MPMediaItemPropertyPlaybackDuration"
_KEY_ELAPSED = "MPNowPlayingInfoPropertyElapsedPlaybackTime"
_KEY_RATE = "MPNowPlayingInfoPropertyPlaybackRate"
_KEY_ARTWORK = "MPMediaItemPropertyArtwork"

# MPRemoteCommandHandlerStatus.success = 0
_STATUS_SUCCESS = 0

# Handler class is built lazily and cached so the ObjC class is only
# registered once (re-defining the same ObjC class name raises at runtime).
_HandlerClass: Any = None


def _get_handler_class() -> Any:
    """Return (building once) an NSObject subclass for target-action dispatch.

    Uses addTarget:action: instead of addTargetWithHandler: to avoid the
    PyObjC "block has no signature" error that arises when the MediaPlayer
    framework's block type metadata is unavailable at runtime.
    """
    global _HandlerClass
    if _HandlerClass is not None:
        return _HandlerClass

    import objc
    from Foundation import NSObject

    class _RemoteCommandHandler(NSObject):
        """Receives MPRemoteCommand target-action callbacks."""

        # Python callbacks stored as instance attributes after alloc/init.
        _on_next: Any = None
        _on_prev: Any = None

        # objc.selector gives the method the correct ObjC type encoding:
        # l = NSInteger (return), @ = id (self), : = SEL, @ = id (event)
        def handleNext_(self, event: Any) -> int:
            if self._on_next is not None:
                self._on_next()
            return _STATUS_SUCCESS

        def handlePrev_(self, event: Any) -> int:
            if self._on_prev is not None:
                self._on_prev()
            return _STATUS_SUCCESS

        handleNext_ = objc.selector(handleNext_, signature=b"l@:@")
        handlePrev_ = objc.selector(handlePrev_, signature=b"l@:@")

    _HandlerClass = _RemoteCommandHandler
    return _HandlerClass


class CoreAudioMediaController(MediaController):
    """Integrates with the macOS Now Playing widget and media keys.

    Loads the MediaPlayer framework lazily inside start() so the class
    can be constructed without triggering framework initialisation.
    """

    def __init__(
        self,
        on_next: Callable[[], None],
        on_prev: Callable[[], None],
        on_play_pause: Callable[[], None],
    ) -> None:
        self._on_next = on_next
        self._on_prev = on_prev
        self._on_play_pause = on_play_pause
        # Typed Any because PyObjC classes have no type stubs.
        self._cmd_handler: Any = None  # _RemoteCommandHandler NSObject instance
        self._npc: Any = None  # MPNowPlayingInfoCenter instance
        self._rcc_shared: Any = None  # MPRemoteCommandCenter shared instance

    def start(self) -> None:
        """Load MediaPlayer framework and register remote command handlers."""
        import objc

        objc.loadBundle(
            "MediaPlayer",
            bundle_path="/System/Library/Frameworks/MediaPlayer.framework",
            module_globals={},
        )
        NPC = objc.lookUpClass("MPNowPlayingInfoCenter")
        RCC = objc.lookUpClass("MPRemoteCommandCenter")

        self._npc = NPC.defaultCenter()
        shared = RCC.sharedCommandCenter()
        self._rcc_shared = shared

        # Build an NSObject-based handler and register via addTarget:action:.
        # This avoids addTargetWithHandler: which requires block type metadata
        # that the MediaPlayer framework doesn't expose to the ObjC runtime.
        Handler = _get_handler_class()
        self._cmd_handler = Handler.alloc().init()
        self._cmd_handler._on_next = self._on_next
        self._cmd_handler._on_prev = self._on_prev

        shared.nextTrackCommand().setEnabled_(True)
        shared.previousTrackCommand().setEnabled_(True)
        shared.nextTrackCommand().addTarget_action_(self._cmd_handler, b"handleNext:")
        shared.previousTrackCommand().addTarget_action_(
            self._cmd_handler, b"handlePrev:"
        )

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

        # Artwork — best-effort; skip quietly on any error.
        try:
            art_bytes = extract_art(track.file_path)
            if art_bytes:
                import objc
                from AppKit import NSData, NSImage
                from Foundation import NSMakeSize

                data = NSData.dataWithBytes_length_(art_bytes, len(art_bytes))
                img = NSImage.alloc().initWithData_(data)
                MPMediaItemArtwork = objc.lookUpClass("MPMediaItemArtwork")
                artwork = MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(
                    NSMakeSize(300, 300), lambda _size: img
                )
                info[_KEY_ARTWORK] = artwork
        except Exception:
            pass  # Missing artwork is not a fatal error.

        self._npc.setNowPlayingInfo_(info)

    def stop(self) -> None:
        """Clear Now Playing info and deregister command handlers."""
        if self._npc is not None:
            try:
                self._npc.setNowPlayingInfo_(None)
            except Exception:
                pass

        if self._rcc_shared is not None and self._cmd_handler is not None:
            try:
                self._rcc_shared.nextTrackCommand().removeTarget_(self._cmd_handler)
                self._rcc_shared.previousTrackCommand().removeTarget_(self._cmd_handler)
            except Exception:
                pass

        logger.debug("MediaController stopped")


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------


def make_media_controller(
    on_next: Callable[[], None],
    on_prev: Callable[[], None],
    on_play_pause: Callable[[], None],
) -> MediaController:
    """Return the correct MediaController for the current platform.

    Returns CoreAudioMediaController on macOS, NullMediaController elsewhere
    or if the framework fails to initialise.
    """
    try:
        return CoreAudioMediaController(
            on_next=on_next,
            on_prev=on_prev,
            on_play_pause=on_play_pause,
        )
    except Exception:
        return NullMediaController()
