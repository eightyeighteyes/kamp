"""Abstract base classes for kamp backend extensions."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ArtworkQuery, ArtworkResult, TrackMetadata


class BaseTagger(ABC):
    """Resolves track metadata.

    Receives a TrackMetadata object, enriches or corrects it, and returns
    the updated object.  The host writes the result to disk — the tagger
    never touches audio files directly.

    For album-level taggers (e.g. MusicBrainz) that resolve all tracks in
    one API round-trip, override ``tag_release`` instead of ``tag``.  The
    default ``tag_release`` implementation simply calls ``tag`` once per
    track; per-track taggers need not override it.
    """

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
    """

    @abstractmethod
    def fetch(self, query: ArtworkQuery) -> ArtworkResult | None:
        """Return artwork for *query*, or None if unavailable."""
        raise NotImplementedError
