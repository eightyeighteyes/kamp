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
from .invoker import invoke_extensions_for_new_tracks
from .permissions import ExtensionPermissions, extract_permissions
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
    "ExtensionPermissions",
    "ExtensionRegistry",
    "FetchResponse",
    "KampGround",
    "Mutation",
    "PlaybackSnapshot",
    "SetArtworkMutation",
    "TrackMetadata",
    "UpdateMetadataMutation",
    "discover_extensions",
    "invoke_extensions_for_new_tracks",
    "apply_mutations",
    "extract_permissions",
    "invoke_extension",
    "probe_extension",
]
