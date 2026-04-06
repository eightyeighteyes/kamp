"""kamp extension host — public symbols."""

from .abc import BaseArtworkSource, BaseTagger
from .context import FetchResponse, KampGround, PlaybackSnapshot
from .discovery import discover_extensions
from .registry import ExtensionRegistry
from .types import ArtworkQuery, ArtworkResult, TrackMetadata
from .worker import invoke_extension

__all__ = [
    "ArtworkQuery",
    "ArtworkResult",
    "BaseArtworkSource",
    "BaseTagger",
    "ExtensionRegistry",
    "FetchResponse",
    "KampGround",
    "PlaybackSnapshot",
    "TrackMetadata",
    "discover_extensions",
    "invoke_extension",
]
