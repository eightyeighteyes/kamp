"""Structured data types for the kamp extension host.

Extensions receive and return these types exclusively — no file paths, no
database handles, no internal daemon objects.  All fields are Python primitives
so instances are picklable across the worker subprocess IPC boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrackMetadata:
    """Canonical metadata for a single track.

    Passed to BaseTagger.tag(); the tagger returns an updated instance.
    Fields that the tagger cannot resolve should be returned unchanged.
    """

    title: str
    artist: str
    album: str
    album_artist: str
    year: str
    track_number: int
    mbid: str  # MusicBrainz release MBID


@dataclass
class ArtworkQuery:
    """Parameters passed to BaseArtworkSource.fetch().

    Provides enough context for an artwork source to locate the correct
    image without receiving any file path or internal database handle.
    """

    mbid: str  # MusicBrainz release MBID
    release_group_mbid: str
    album: str
    artist: str


@dataclass
class ArtworkResult:
    """Artwork returned by BaseArtworkSource.fetch().

    image_bytes is the raw image data; mime_type is "image/jpeg" or
    "image/png".  The host is responsible for embedding the bytes into
    audio files — the extension never touches the filesystem.
    """

    image_bytes: bytes
    mime_type: str
