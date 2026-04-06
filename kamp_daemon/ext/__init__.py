"""kamp extension host — public symbols."""

from .abc import BaseArtworkSource, BaseTagger
from .context import (
    FetchResponse,
    KampGround,
    Mutation,
    PlaybackSnapshot,
    SetArtworkMutation,
    UpdateMetadataMutation,
)
from .discovery import discover_extensions
from .probe import probe_extension
from .registry import ExtensionRegistry
from .types import ArtworkQuery, ArtworkResult, TrackMetadata
from .worker import invoke_extension
from .write_log import apply_mutations

__all__ = [
    "ArtworkQuery",
    "ArtworkResult",
    "BaseArtworkSource",
    "BaseTagger",
    "ExtensionRegistry",
    "FetchResponse",
    "KampGround",
    "Mutation",
    "PlaybackSnapshot",
    "SetArtworkMutation",
    "TrackMetadata",
    "UpdateMetadataMutation",
    "discover_extensions",
    "apply_mutations",
    "invoke_extension",
    "probe_extension",
]
