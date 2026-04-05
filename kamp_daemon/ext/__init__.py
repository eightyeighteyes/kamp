"""kamp extension host — public symbols."""

from .abc import BaseArtworkSource, BaseTagger
from .discovery import discover_extensions
from .registry import ExtensionRegistry

__all__ = [
    "BaseArtworkSource",
    "BaseTagger",
    "ExtensionRegistry",
    "discover_extensions",
]
