"""Abstract base classes for kamp backend extensions."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import ArtworkQuery, ArtworkResult, TrackMetadata


class BaseTagger(ABC):
    """Resolves track metadata.

    Receives a TrackMetadata object, enriches or corrects it, and returns
    the updated object.  The host writes the result to disk — the tagger
    never touches audio files directly.
    """

    @abstractmethod
    def tag(self, track: TrackMetadata) -> TrackMetadata:
        """Return an updated copy of *track* with resolved metadata."""
        raise NotImplementedError


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
