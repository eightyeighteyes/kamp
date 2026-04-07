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
from .permissions import extract_permissions
from .pins import verify_or_pin
from .probe import probe_extension
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

    # --- Import-time execution probe ---
    # Derive the module name from the entry point value (e.g. "my_ext.tagger:MyTagger"
    # → "my_ext.tagger") and probe it before loading the class into this process.
    module_name = ep.value.split(":")[0]
    if not probe_extension(module_name, package_name=dist_name):
        return

    # --- Hash verification ---
    # Verify (or pin on first encounter) the distribution's installed files.
    # Skipped when the distribution is unknown — probe already ran, and we
    # cannot enumerate files without a Distribution object.
    dist = getattr(ep, "dist", None)
    if dist is not None:
        if not verify_or_pin(dist_name, dist):
            return

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

    # --- Extract permissions ---
    # Read kampground_permissions / kampground_network_domains class attributes.
    # These are defined on the ABC base classes with empty-list defaults, so
    # extensions that need no capabilities require no explicit declaration.
    permissions = extract_permissions(cls)
    # Warn when an extension can both read library data (via ctx.search) and
    # contact external servers — a combination that could exfiltrate metadata.
    if (
        "library.write" in permissions.permissions
        and "network.fetch" in permissions.permissions
    ):
        _logger.warning(
            "Extension %r from package %r declares both library.write and "
            "network.fetch — review this extension carefully before granting access.",
            ep.name,
            dist_name,
        )

    # --- Register ---
    registry.register(cls, permissions)
    _logger.info(
        "Registered extension %r from package %r as %s (permissions: %s)",
        ep.name,
        dist_name,
        matching_abc.__name__,
        sorted(permissions.permissions) if permissions.permissions else "none",
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
