"""Entry point discovery and ABC conformance validation for kamp extensions.

Scans all installed packages for entry points in the ``kamp.extensions`` group,
loads each declared class, and validates it against the known ABCs before
registering it. Invalid or unloadable entry points are rejected with a
descriptive log message; the daemon continues regardless.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import logging

from .abc import BaseArtworkSource, BaseTagger
from .registry import ExtensionRegistry

_logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "kamp.extensions"
_KNOWN_ABCS: tuple[type, ...] = (BaseTagger, BaseArtworkSource)


def discover_extensions(registry: ExtensionRegistry) -> None:
    """Discover and register all installed kamp extensions.

    Loads each entry point in the ``kamp.extensions`` group, checks ABC
    conformance, and registers conforming classes in *registry*. Any entry
    point that fails to load or does not conform to a known ABC is logged and
    skipped — it never reaches the registry.
    """
    eps = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
    for ep in eps:
        _load_and_register(ep, registry)


def _load_and_register(
    ep: importlib.metadata.EntryPoint,
    registry: ExtensionRegistry,
) -> None:
    """Load a single entry point and register it if it passes validation."""
    # Identify the owning distribution for useful error messages.
    dist_name = _dist_name(ep)

    # --- Load ---
    try:
        cls = ep.load()
    except Exception as exc:
        _logger.error(
            "Extension %r from package %r failed to load: %s",
            ep.name,
            dist_name,
            exc,
        )
        return

    # --- Must be a class ---
    if not inspect.isclass(cls):
        _logger.error(
            "Extension %r from package %r is not a class (got %r) — skipping",
            ep.name,
            dist_name,
            type(cls).__name__,
        )
        return

    # --- Must subclass a known ABC ---
    matching_abc = next((abc for abc in _KNOWN_ABCS if issubclass(cls, abc)), None)
    if matching_abc is None:
        _logger.error(
            "Extension %r from package %r (%s) does not implement any known "
            "kamp extension ABC (%s) — skipping",
            ep.name,
            dist_name,
            cls.__qualname__,
            ", ".join(a.__name__ for a in _KNOWN_ABCS),
        )
        return

    # --- Must implement all abstract methods (no residual abstracts) ---
    missing = _missing_abstracts(cls)
    if missing:
        _logger.error(
            "Extension %r from package %r (%s) is missing required method(s): "
            "%s — skipping",
            ep.name,
            dist_name,
            cls.__qualname__,
            ", ".join(sorted(missing)),
        )
        return

    # --- Register ---
    registry.register(cls)
    _logger.info(
        "Registered extension %r from package %r as %s",
        ep.name,
        dist_name,
        matching_abc.__name__,
    )


def _missing_abstracts(cls: type) -> frozenset[str]:
    """Return the set of abstract method names not implemented by *cls*."""
    return getattr(cls, "__abstractmethods__", frozenset())


def _dist_name(ep: importlib.metadata.EntryPoint) -> str:
    """Return the distribution name that declared *ep*, or '<unknown>'."""
    try:
        # .dist is available on Python 3.9+ EntryPoint objects
        dist = getattr(ep, "dist", None)
        if dist is not None:
            return dist.metadata["Name"]  # type: ignore[no-any-return]
    except AttributeError:
        pass
    return "<unknown>"
