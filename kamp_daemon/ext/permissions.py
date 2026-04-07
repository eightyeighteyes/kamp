"""Extension permission extraction.

Backend extensions declare capabilities as class attributes on their ABC
subclass.  The host reads these at discovery time — no extra packaging,
configuration file, or pyproject.toml section is required.

Example::

    class DiscogsTagger(BaseTagger):
        kampground_permissions = ["network.fetch"]
        kampground_network_domains = ["api.discogs.com"]

    class BandcampSyncer(BaseSyncer):
        kampground_permissions = ["library.write"]

The base classes (BaseTagger, BaseArtworkSource, BaseSyncer) define
``kampground_permissions = []`` and ``kampground_network_domains = []`` as
defaults, so extensions that need no capabilities require no declaration.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtensionPermissions:
    """Declared capabilities for a backend extension.

    Args:
        permissions: Set of capability strings declared by the extension
            (e.g. ``"network.fetch"``, ``"library.write"``).
        allowed_domains: Hostnames the extension may contact via
            ``KampGround.fetch()``.  Only meaningful when
            ``"network.fetch"`` is in ``permissions``.
    """

    permissions: frozenset[str] = field(default_factory=frozenset)
    allowed_domains: frozenset[str] = field(default_factory=frozenset)


def extract_permissions(cls: type) -> ExtensionPermissions:
    """Read declared permissions from an extension class's class attributes.

    Reads ``kampground_permissions`` and ``kampground_network_domains`` from
    *cls* (falling back to empty lists if the attributes are absent or not
    list-typed).  The base ABCs define these attributes with empty-list
    defaults so extension subclasses that need no capabilities require no
    explicit declaration.

    Args:
        cls: Extension class to inspect (a subclass of BaseTagger,
            BaseArtworkSource, or BaseSyncer).

    Returns:
        Parsed permissions, or empty permissions if none declared.
    """
    raw_perms = getattr(cls, "kampground_permissions", [])
    raw_domains = getattr(cls, "kampground_network_domains", [])

    permissions: frozenset[str] = (
        frozenset(p for p in raw_perms if isinstance(p, str))
        if isinstance(raw_perms, list)
        else frozenset()
    )

    allowed_domains: frozenset[str] = (
        frozenset(d for d in raw_domains if isinstance(d, str))
        if isinstance(raw_domains, list)
        else frozenset()
    )

    return ExtensionPermissions(
        permissions=permissions,
        allowed_domains=allowed_domains,
    )
