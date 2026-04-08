"""Abstract base classes for kamp backend extensions."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ArtworkQuery, ArtworkResult, TrackMetadata


class BaseSyncer(ABC):
    """Polls an external source and deposits new downloads into staging.

    Implementations run in-process within the daemon — the extension host
    already provides process isolation, so a nested subprocess is not needed.
    Third-party syncers that cannot write to the filesystem directly should
    call ``ctx.stage(filename, content)`` to deposit files; the host writes
    them to the staging directory.

    Lifecycle
    ---------
    ``DaemonCore`` calls the following methods in order::

        syncer.start()        # begin background polling
        syncer.pause()        # temporarily stop polling (e.g. pipeline pause)
        syncer.resume()       # restart polling after pause
        syncer.stop()         # final shutdown; join background thread

    The default implementations of ``pause`` and ``resume`` delegate to
    ``stop`` / ``start`` respectively.  Override them if the implementation
    can cheaply suspend without fully tearing down (e.g. by setting an event).

    Manual triggers
    ---------------
    ``sync_once`` and ``mark_synced`` are optional capabilities.  Override
    them to support manual sync triggers (e.g. a "Sync now" menu item).

    Permissions
    -----------
    Declare required KampGround capabilities as class attributes.  The host
    reads these at discovery time — no extra packaging or config file needed::

        class MyDiscogsSyncer(BaseSyncer):
            kampground_permissions = ["network.fetch", "library.write"]
            kampground_network_domains = ["api.discogs.com"]

    Sandbox tier
    ------------
    ``_sandbox_tier`` controls which OS-level sandbox profile is applied when
    this extension runs via ``invoke_extension()``.  Override with
    ``"syncer"`` for extensions that need filesystem writes or subprocess
    spawning (e.g. Playwright-based syncers).  See
    ``kamp_daemon/ext/sandbox/__init__.py`` for available tiers.
    """

    kampground_permissions: list[str] = []
    kampground_network_domains: list[str] = []
    _sandbox_tier: str = "minimal"

    @abstractmethod
    def start(self) -> None:
        """Start the background polling thread."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop the background polling thread and join it."""
        raise NotImplementedError

    def pause(self) -> None:
        """Temporarily pause polling. Default: calls stop()."""
        self.stop()

    def resume(self) -> None:
        """Resume polling after pause(). Default: calls start()."""
        self.start()

    def sync_once(self, *, skip_auto_mark: bool = False) -> None:
        """Perform a single immediate sync without waiting for the poll interval.

        Override to support manual sync triggers.  The default raises
        ``NotImplementedError`` so callers can detect unsupported syncers.

        Args:
            skip_auto_mark: When True, skip the automatic "mark existing
                collection as synced" step that normally runs on first use.
        """
        raise NotImplementedError

    def mark_synced(self) -> None:
        """Mark the entire collection as already synced without downloading.

        Override to support the "mark synced" operation.  The default raises
        ``NotImplementedError`` so callers can detect unsupported syncers.
        """
        raise NotImplementedError


class BaseTagger(ABC):
    """Resolves track metadata.

    Receives a TrackMetadata object, enriches or corrects it, and returns
    the updated object.  The host writes the result to disk — the tagger
    never touches audio files directly.

    For album-level taggers (e.g. MusicBrainz) that resolve all tracks in
    one API round-trip, override ``tag_release`` instead of ``tag``.  The
    default ``tag_release`` implementation simply calls ``tag`` once per
    track; per-track taggers need not override it.

    Permissions
    -----------
    Declare required KampGround capabilities as class attributes::

        class MyTagger(BaseTagger):
            kampground_permissions = ["network.fetch"]
            kampground_network_domains = ["api.example.com"]

    Sandbox tier
    ------------
    Default ``"minimal"``: network only, no filesystem writes, no subprocess
    spawn.  Override if the tagger requires broader capabilities.
    """

    kampground_permissions: list[str] = []
    kampground_network_domains: list[str] = []
    _sandbox_tier: str = "minimal"

    @abstractmethod
    def tag(self, track: TrackMetadata) -> TrackMetadata:
        """Return an updated copy of *track* with resolved metadata."""
        raise NotImplementedError

    def tag_release(self, tracks: list[TrackMetadata]) -> list[TrackMetadata]:
        """Return updated copies of all *tracks* in a release.

        Default implementation calls ``tag`` once per track.  Album-level
        taggers should override this to resolve the whole release in a single
        API call rather than making N separate round-trips.

        Args:
            tracks: All tracks belonging to the same release, in track-number
                order.

        Returns:
            Updated TrackMetadata objects, one per input track, in the same
            order.
        """
        return [self.tag(t) for t in tracks]


class BaseArtworkSource(ABC):
    """Fetches front cover artwork.

    Receives an ArtworkQuery and returns an ArtworkResult, or None if no
    qualifying art could be found.  The host embeds the result — the source
    never touches audio files directly.

    Permissions
    -----------
    Declare required KampGround capabilities as class attributes::

        class MyArtSource(BaseArtworkSource):
            kampground_permissions = ["network.fetch"]
            kampground_network_domains = ["img.example.com"]

    Sandbox tier
    ------------
    Default ``"minimal"``: network only, no filesystem writes, no subprocess
    spawn.  Override if the source requires broader capabilities.
    """

    kampground_permissions: list[str] = []
    kampground_network_domains: list[str] = []
    _sandbox_tier: str = "minimal"

    @abstractmethod
    def fetch(self, query: ArtworkQuery) -> ArtworkResult | None:
        """Return artwork for *query*, or None if unavailable."""
        raise NotImplementedError
