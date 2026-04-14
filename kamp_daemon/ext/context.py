"""KampGround: the host API surface available to backend extensions.

KampGround is a picklable snapshot object constructed by the host before
spawning a worker subprocess. Extensions receive it via their constructor
and use it to query library state, read playback state, make proxied network
requests, and register event callbacks. All fields are Python primitives or
other picklable types — no file paths, database cursors, or internal daemon
objects ever appear here.

Usage (extension author perspective)::

    class MyTagger(BaseTagger):
        def __init__(self, ctx: KampGround) -> None:
            self._ctx = ctx

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            # Query the library snapshot
            related = self._ctx.search(track.album)
            # Fetch data from the network (requires network.domains declaration)
            resp = self._ctx.fetch("https://example.com/api")
            return track
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .types import ArtworkResult, TrackMetadata


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
class FetchResponse:
    """Response returned by KampGround.fetch().

    All fields are primitives so the response is picklable and can cross the
    subprocess boundary if needed.
    """

    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass
class UpdateMetadataMutation:
    """A queued request to update track metadata fields in the library.

    Collected during extension execution and applied by the host after the
    worker completes — the worker subprocess never writes to the database.

    Args:
        mbid: MusicBrainz recording ID identifying the track to update.
        fields: Mapping of field names to new values (str or int).
    """

    mbid: str  # MusicBrainz recording ID (matches tracks.mb_recording_id in the DB)
    fields: dict[str, str | int]


@dataclass
class SetArtworkMutation:
    """A queued request to write artwork for a track in the library.

    Collected during extension execution and applied by the host after the
    worker completes — the worker subprocess never writes to the database.

    Args:
        mbid: MusicBrainz recording ID identifying the track to update.
        artwork: ArtworkResult containing image bytes and MIME type.
    """

    mbid: str
    artwork: ArtworkResult


@dataclass
class StageMutation:
    """A queued request to deposit a file in the watch folder.

    Collected during extension execution (typically by a BaseSyncer) and
    applied by the host after the worker completes.  The host writes
    ``content`` to ``watch_folder / filename``.  Extensions must not write
    to the filesystem directly — use ``KampGround.stage()`` instead.

    Args:
        filename: Base filename (no path separators) for the staged file.
        content: Raw bytes to write (e.g. a downloaded ZIP archive).
    """

    filename: str
    content: bytes


# Union type for all mutation variants.
Mutation = UpdateMetadataMutation | SetArtworkMutation | StageMutation


@dataclass
class KampGround:
    """Host API surface passed to backend extension constructors.

    Provides read-only access to a snapshot of library tracks and playback
    state, a proxied network interface, and an event subscription mechanism
    for daemon lifecycle events. All state is frozen at construction time
    (a snapshot, not a live view).

    Args:
        playback: Snapshot of playback state when the worker was spawned.
        library_tracks: Snapshot of library tracks relevant to this invocation.
        permissions: Capability strings declared by this extension in its
            ``[tool.kampground]`` manifest section.  Methods that require a
            capability raise ``PermissionError`` when the corresponding string
            is absent (e.g. ``"network.fetch"``, ``"library.write"``).
        allowed_domains: Hostnames the extension may contact via fetch(). Set
            by the host from the extension manifest's ``network.domains`` list.
            An empty frozenset (the default) means no network access.
    """

    playback: PlaybackSnapshot = field(default_factory=PlaybackSnapshot)
    library_tracks: list[TrackMetadata] = field(default_factory=list)
    permissions: frozenset[str] = field(default_factory=frozenset)
    allowed_domains: frozenset[str] = field(default_factory=frozenset)
    # Event callbacks stored by event name; host fires them before invocations.
    # Not exposed directly — use subscribe() instead.
    _callbacks: dict[str, list[Callable[[], None]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    # Mutations queued by the extension during its run; applied by the host
    # after the worker completes. Not exposed directly — use update_metadata()
    # and set_artwork() instead.
    _pending_mutations: list[Mutation] = field(
        default_factory=list, repr=False, compare=False
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
    # Network
    # ------------------------------------------------------------------

    def fetch(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
    ) -> FetchResponse:
        """Make an HTTP request on behalf of the extension.

        This is the proxied network interface for extensions.  Declaring
        ``network.fetch`` scopes access to this method and routes requests
        through a host-audited, domain-checked channel.

        **Scope of enforcement:** ``network.fetch`` is an *API gate*, not a
        network sandbox.  Extensions that call ``requests``, ``urllib``, or
        other network libraries directly bypass this permission entirely.
        ``library.write`` is the only permission that provides a hard security
        boundary today.  A subprocess-level sandbox (seccomp/sandbox-exec) is
        tracked separately and would make this a true gate.

        Extensions must declare the domains they need under
        ``kampground_network_domains`` on their class; the host populates
        ``allowed_domains`` from that declaration before spawning the worker.

        Args:
            url: Absolute HTTP/HTTPS URL to request.
            method: HTTP method (default ``"GET"``).
            body: Request body bytes (default ``None``).

        Returns:
            FetchResponse with status_code, headers, and body.

        Raises:
            PermissionError: If ``network.fetch`` is not in this
                extension's declared permissions, or if the URL's hostname is
                not in ``allowed_domains``.

        Example::

            resp = ctx.fetch("https://musicbrainz.org/ws/2/release/123")
            data = resp.body
        """
        if "network.fetch" not in self.permissions:
            raise PermissionError(
                "network.fetch permission not declared. "
                "Add it to kampground_permissions on your extension class."
            )
        hostname = urlparse(url).hostname or ""
        if hostname not in self.allowed_domains:
            raise PermissionError(
                f"Network request to '{hostname}' is blocked. "
                f"Declare it under network.domains in your extension manifest."
            )
        req = Request(url, data=body, method=method)
        with urlopen(req) as resp:
            return FetchResponse(
                status_code=resp.status,
                headers=dict(resp.headers),
                body=resp.read(),
            )

    # ------------------------------------------------------------------
    # Library writes
    # ------------------------------------------------------------------

    @property
    def pending_mutations(self) -> list[Mutation]:
        """Read-only view of mutations queued so far.

        Consumed by the host after the worker exits — extension code should
        not call this directly.
        """
        return list(self._pending_mutations)

    def update_metadata(self, mbid: str, fields: dict[str, str | int]) -> None:
        """Queue a metadata update to be applied by the host.

        The host applies this mutation to the library database after the
        worker subprocess exits. Extensions must not write to the database
        directly — all library writes go through this method.

        Args:
            mbid: MusicBrainz recording ID of the track to update.
            fields: Mapping of field names to new values. Allowed keys match
                TrackMetadata field names (e.g. ``"title"``, ``"artist"``).

        Raises:
            PermissionError: If ``library.write`` is not in this extension's
                declared permissions.

        Example::

            ctx.update_metadata(track.mbid, {"title": "Alright", "year": "2015"})
        """
        if "library.write" not in self.permissions:
            raise PermissionError(
                "library.write permission not declared. "
                "Add it to kampground_permissions on your extension class."
            )
        self._pending_mutations.append(UpdateMetadataMutation(mbid=mbid, fields=fields))

    def set_artwork(self, mbid: str, artwork: ArtworkResult) -> None:
        """Queue an artwork write to be applied by the host.

        The host applies this mutation to the library database after the
        worker subprocess exits. Extensions must not write to the database
        directly — all library writes go through this method.

        Args:
            mbid: MusicBrainz recording ID of the track to update.
            artwork: ArtworkResult containing image bytes and MIME type.

        Raises:
            PermissionError: If ``library.write`` is not in this extension's
                declared permissions.

        Example::

            result = ctx.fetch("https://coverartarchive.org/...")
            ctx.set_artwork(track.mbid, ArtworkResult(result.body, "image/jpeg"))
        """
        if "library.write" not in self.permissions:
            raise PermissionError(
                "library.write permission not declared. "
                "Add it to kampground_permissions on your extension class."
            )
        self._pending_mutations.append(SetArtworkMutation(mbid=mbid, artwork=artwork))

    def stage(self, filename: str, content: bytes) -> None:
        """Queue a file to be deposited in the watch folder by the host.

        The host writes ``content`` to ``watch_folder / filename`` after the
        extension completes.  Extensions (particularly ``BaseSyncer``
        implementations) must use this method rather than writing to the
        filesystem directly — doing so would couple the extension to a
        specific path, breaking the extension isolation contract.

        Args:
            filename: Base filename for the staged file.  Must not contain
                path separators (``/`` or ``\\``).  The host rejects names
                that would escape the watch folder.
            content: Raw bytes to write (e.g. a downloaded ZIP archive).

        Raises:
            PermissionError: If ``library.write`` is not in this extension's
                declared permissions.
            ValueError: If *filename* contains a path separator.

        Example::

            ctx.stage("artist-album.zip", zip_bytes)
        """
        if "library.write" not in self.permissions:
            raise PermissionError(
                "library.write permission not declared. "
                "Add it to kampground_permissions on your extension class."
            )
        if "/" in filename or "\\" in filename:
            raise ValueError(
                f"stage() filename must be a base name with no path separators; "
                f"got {filename!r}"
            )
        self._pending_mutations.append(
            StageMutation(filename=filename, content=content)
        )

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
