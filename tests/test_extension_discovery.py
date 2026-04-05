"""Tests for extension entry-point discovery and ABC conformance validation."""

from __future__ import annotations

import importlib.metadata
from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.ext import (
    BaseArtworkSource,
    BaseTagger,
    ExtensionRegistry,
    discover_extensions,
)
from kamp_daemon.ext.discovery import _ENTRY_POINT_GROUP

# ---------------------------------------------------------------------------
# Helpers — concrete extension classes for testing
# ---------------------------------------------------------------------------


class GoodTagger(BaseTagger):
    def tag(self, paths: list[str]) -> None:
        pass


class GoodArtworkSource(BaseArtworkSource):
    def fetch_artwork(self, mbid: str, paths: list[str]) -> None:
        pass


class BadExtension:
    """Does not subclass any known ABC."""

    pass


class IncompleteTagger(BaseTagger):
    """Subclasses BaseTagger but does not implement tag() — still abstract."""

    pass


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_ep(name: str, cls: type, dist_name: str = "test-pkg") -> MagicMock:
    """Build a mock EntryPoint that loads *cls*."""
    ep = MagicMock(spec=importlib.metadata.EntryPoint)
    ep.name = name
    ep.load.return_value = cls
    dist = MagicMock()
    dist.metadata = {"Name": dist_name}
    ep.dist = dist
    return ep


def _patch_eps(*eps: MagicMock):
    """Patch importlib.metadata.entry_points to return *eps*."""
    return patch(
        "kamp_daemon.ext.discovery.importlib.metadata.entry_points",
        return_value=list(eps),
    )


# ---------------------------------------------------------------------------
# AC #1 / AC #3 — conforming extensions are discovered and registered
# ---------------------------------------------------------------------------


def test_conforming_tagger_is_registered():
    ep = _make_ep("my_tagger", GoodTagger)
    registry = ExtensionRegistry()
    with _patch_eps(ep):
        discover_extensions(registry)
    assert GoodTagger in registry.taggers


def test_conforming_artwork_source_is_registered():
    ep = _make_ep("my_art", GoodArtworkSource)
    registry = ExtensionRegistry()
    with _patch_eps(ep):
        discover_extensions(registry)
    assert GoodArtworkSource in registry.artwork_sources


def test_multiple_extensions_registered_in_order():
    ep_t = _make_ep("t", GoodTagger)
    ep_a = _make_ep("a", GoodArtworkSource)
    registry = ExtensionRegistry()
    with _patch_eps(ep_t, ep_a):
        discover_extensions(registry)
    assert GoodTagger in registry.taggers
    assert GoodArtworkSource in registry.artwork_sources


# ---------------------------------------------------------------------------
# AC #2 — non-conforming classes rejected with descriptive message
# ---------------------------------------------------------------------------


def test_non_abc_class_rejected(caplog):
    ep = _make_ep("bad", BadExtension, dist_name="bad-pkg")
    registry = ExtensionRegistry()
    with (
        _patch_eps(ep),
        pytest.raises(SystemExit, match="") if False else _patch_eps(ep),
    ):
        pass
    # Re-run properly
    with _patch_eps(ep):
        with caplog.at_level("ERROR", logger="kamp_daemon.ext.discovery"):
            discover_extensions(registry)
    assert registry.taggers == []
    assert registry.artwork_sources == []
    assert "bad-pkg" in caplog.text
    assert "bad" in caplog.text  # entry point name


def test_incomplete_tagger_rejected_names_missing_method(caplog):
    ep = _make_ep("incomplete", IncompleteTagger, dist_name="incomplete-pkg")
    registry = ExtensionRegistry()
    with _patch_eps(ep):
        with caplog.at_level("ERROR", logger="kamp_daemon.ext.discovery"):
            discover_extensions(registry)
    assert registry.taggers == []
    assert "tag" in caplog.text
    assert "incomplete-pkg" in caplog.text


# ---------------------------------------------------------------------------
# AC #2 — ImportError is handled gracefully
# ---------------------------------------------------------------------------


def test_import_error_logged_and_skipped(caplog):
    ep = MagicMock(spec=importlib.metadata.EntryPoint)
    ep.name = "broken"
    ep.load.side_effect = ImportError("missing dependency")
    dist = MagicMock()
    dist.metadata = {"Name": "broken-pkg"}
    ep.dist = dist

    registry = ExtensionRegistry()
    with _patch_eps(ep):
        with caplog.at_level("ERROR", logger="kamp_daemon.ext.discovery"):
            discover_extensions(registry)
    assert registry.taggers == []
    assert "broken-pkg" in caplog.text
    assert "missing dependency" in caplog.text


# ---------------------------------------------------------------------------
# AC #4 — no kamp.extensions entry points → empty registry, no error
# ---------------------------------------------------------------------------


def test_no_entry_points_yields_empty_registry(caplog):
    registry = ExtensionRegistry()
    with _patch_eps():
        with caplog.at_level("ERROR", logger="kamp_daemon.ext.discovery"):
            discover_extensions(registry)
    assert registry.taggers == []
    assert registry.artwork_sources == []
    assert caplog.records == []


# ---------------------------------------------------------------------------
# Misc — non-class value loaded from entry point
# ---------------------------------------------------------------------------


def test_non_class_entry_point_rejected(caplog):
    ep = _make_ep("not_a_class", "just a string")  # type: ignore[arg-type]
    registry = ExtensionRegistry()
    with _patch_eps(ep):
        with caplog.at_level("ERROR", logger="kamp_daemon.ext.discovery"):
            discover_extensions(registry)
    assert registry.taggers == []
    assert "not a class" in caplog.text.lower()
