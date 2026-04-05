"""KampGround: the host API surface available to backend extensions.

KampGround is a picklable snapshot object constructed by the host before
spawning a worker subprocess. Extensions receive it via their constructor
and use it to query library state, read playback state, and register event
callbacks. All fields are Python primitives or other picklable types — no
file paths, database cursors, or internal daemon objects ever appear here.

Usage (extension author perspective)::

    class MyTagger(BaseTagger):
        def __init__(self, ctx: KampGround) -> None:
            self._ctx = ctx

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            # Query the library snapshot
            related = self._ctx.search(track.album)
            # Read playback state
            if self._ctx.playback.playing:
                ...
            return track
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .types import TrackMetadata


@dataclass(frozen=True)
class PlaybackSnapshot:
    """Immutable snapshot of playback state at the time the worker was spawned.

    Constructed from MpvPlaybackEngine.state before the subprocess is started.
    All fields are primitives so the snapshot is picklable.
    """

    playing: bool = False
    position: float = 0.0
    duration: float = 0.0
    volume: int = 100


@dataclass
class KampGround:
    """Host API surface passed to backend extension constructors.

    Provides read-only access to a snapshot of library tracks and playback
    state, plus an event subscription mechanism for daemon lifecycle events.
    All state is frozen at construction time (a snapshot, not a live view).

    Args:
        playback: Snapshot of playback state when the worker was spawned.
        library_tracks: Snapshot of library tracks relevant to this invocation.
    """

    playback: PlaybackSnapshot = field(default_factory=PlaybackSnapshot)
    library_tracks: list[TrackMetadata] = field(default_factory=list)
    # Event callbacks stored by event name; host fires them before invocations.
    # Not exposed directly — use subscribe() instead.
    _callbacks: dict[str, list[Callable[[], None]]] = field(
        default_factory=dict, repr=False, compare=False
    )

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------

    def search(self, query: str) -> list[TrackMetadata]:
        """Return library tracks whose title, artist, or album match *query*.

        Matching is case-insensitive substring search across title, artist,
        album, and album_artist fields. Returns all tracks if *query* is empty.

        Args:
            query: Search string to match against track metadata fields.

        Returns:
            Matching TrackMetadata objects from the library snapshot.

        Example::

            results = ctx.search("Madvillainy")
            for track in results:
                print(track.title, track.artist)
        """
        if not query:
            return list(self.library_tracks)
        q = query.lower()
        return [
            t
            for t in self.library_tracks
            if q in t.title.lower()
            or q in t.artist.lower()
            or q in t.album.lower()
            or q in t.album_artist.lower()
        ]

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def subscribe(self, event: str, callback: Callable[[], None]) -> None:
        """Register *callback* to be called when *event* fires.

        Callbacks are stored on the KampGround instance and invoked by the
        host synchronously before or after the relevant lifecycle event.
        Supported events: ``"track_start"``, ``"track_end"``, ``"daemon_stop"``.

        Args:
            event: Event name to subscribe to.
            callback: Zero-argument callable to invoke when the event fires.

        Example::

            def on_start() -> None:
                print("Track started")

            ctx.subscribe("track_start", on_start)
        """
        self._callbacks.setdefault(event, []).append(callback)

    def fire(self, event: str) -> None:
        """Invoke all callbacks registered for *event*.

        Called by the host — extension code should not call this directly.
        """
        for cb in self._callbacks.get(event, []):
            cb()
