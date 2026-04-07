"""Extension registry: holds validated extension classes by type."""

from __future__ import annotations

import logging

from .abc import BaseArtworkSource, BaseTagger
from .permissions import ExtensionPermissions

_logger = logging.getLogger(__name__)

# All known ABCs in registration order. Used by discover_extensions to validate
# and route each loaded class to the correct bucket.
_KNOWN_ABCS: tuple[type, ...] = (BaseTagger, BaseArtworkSource)


class ExtensionRegistry:
    """Stores extension classes that have passed ABC conformance validation."""

    def __init__(self) -> None:
        self._taggers: list[type[BaseTagger]] = []
        self._artwork_sources: list[type[BaseArtworkSource]] = []
        # Permissions declared by each registered class.
        self._permissions: dict[type, ExtensionPermissions] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        cls: type,
        permissions: ExtensionPermissions | None = None,
    ) -> bool:
        """Register *cls* if it conforms to a known extension ABC.

        Args:
            cls: Extension class to register.
            permissions: Capabilities declared by this extension.  Defaults
                to no permissions (cannot call any gated KampGround method).

        Returns:
            True if registered, False if the class does not match any
            known ABC (caller is responsible for logging the rejection).
        """
        perm = permissions if permissions is not None else ExtensionPermissions()
        if issubclass(cls, BaseTagger):
            self._taggers.append(cls)
            self._permissions[cls] = perm
            return True
        if issubclass(cls, BaseArtworkSource):
            self._artwork_sources.append(cls)
            self._permissions[cls] = perm
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

    def permissions_for(self, cls: type) -> ExtensionPermissions:
        """Return the declared permissions for *cls*, or empty if unregistered."""
        return self._permissions.get(cls, ExtensionPermissions())
