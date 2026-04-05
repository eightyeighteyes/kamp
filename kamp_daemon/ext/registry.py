"""Extension registry: holds validated extension classes by type."""

from __future__ import annotations

import logging

from .abc import BaseArtworkSource, BaseTagger

_logger = logging.getLogger(__name__)

# All known ABCs in registration order. Used by discover_extensions to validate
# and route each loaded class to the correct bucket.
_KNOWN_ABCS: tuple[type, ...] = (BaseTagger, BaseArtworkSource)


class ExtensionRegistry:
    """Stores extension classes that have passed ABC conformance validation."""

    def __init__(self) -> None:
        self._taggers: list[type[BaseTagger]] = []
        self._artwork_sources: list[type[BaseArtworkSource]] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, cls: type) -> bool:
        """Register *cls* if it conforms to a known extension ABC.

        Returns True if registered, False if the class does not match any
        known ABC (caller is responsible for logging the rejection).
        """
        if issubclass(cls, BaseTagger):
            self._taggers.append(cls)
            return True
        if issubclass(cls, BaseArtworkSource):
            self._artwork_sources.append(cls)
            return True
        return False

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    @property
    def taggers(self) -> list[type[BaseTagger]]:
        return list(self._taggers)

    @property
    def artwork_sources(self) -> list[type[BaseArtworkSource]]:
        return list(self._artwork_sources)
