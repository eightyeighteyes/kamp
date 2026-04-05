"""kamp extension host — public symbols."""

from .abc import BaseArtworkSource, BaseTagger
from .discovery import discover_extensions
from .registry import ExtensionRegistry
from .worker import invoke_extension

__all__ = [
    "BaseArtworkSource",
    "BaseTagger",
    "ExtensionRegistry",
    "discover_extensions",
    "invoke_extension",
]
