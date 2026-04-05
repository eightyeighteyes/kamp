"""Abstract base classes for kamp backend extensions.

These ABCs define the minimum conformance surface for the two extension types
kamp supports today. Method signatures are intentionally minimal stubs — they
will be fleshed out as real first-party extensions are built (per the TASK-17
architecture invariant: extract the surface from working code, don't design it
in the abstract).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseTagger(ABC):
    """Resolves and writes track metadata.

    A tagger receives a batch of audio file paths and is expected to look up
    and write canonical tag data (title, artist, album, year, etc.).
    """

    @abstractmethod
    def tag(self, paths: list[str]) -> None:
        """Tag the given audio files.

        Args:
            paths: Absolute paths to audio files that need tagging.
        """
        raise NotImplementedError


class BaseArtworkSource(ABC):
    """Fetches and embeds front cover artwork.

    An artwork source receives an MBID and a list of audio file paths and is
    expected to embed qualifying cover art into each file.
    """

    @abstractmethod
    def fetch_artwork(self, mbid: str, paths: list[str]) -> None:
        """Fetch and embed artwork for the release identified by *mbid*.

        Args:
            mbid: MusicBrainz release ID.
            paths: Absolute paths to audio files that should receive the art.
        """
        raise NotImplementedError
