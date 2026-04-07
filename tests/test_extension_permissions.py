"""Tests for extension permission extraction (permissions.py)."""

from __future__ import annotations

import pytest

from kamp_daemon.ext.abc import BaseArtworkSource, BaseSyncer, BaseTagger
from kamp_daemon.ext.context import KampGround
from kamp_daemon.ext.permissions import ExtensionPermissions, extract_permissions
from kamp_daemon.ext.types import ArtworkQuery, ArtworkResult, TrackMetadata

# ---------------------------------------------------------------------------
# Minimal concrete classes used across tests
# ---------------------------------------------------------------------------


class _MinimalTagger(BaseTagger):
    def tag(self, track: TrackMetadata) -> TrackMetadata:
        return track


class _MinimalArtSource(BaseArtworkSource):
    def fetch(self, query: ArtworkQuery) -> ArtworkResult | None:
        return None


# ---------------------------------------------------------------------------
# Default (no declaration) → empty permissions
# ---------------------------------------------------------------------------


def test_no_declaration_returns_empty_for_tagger() -> None:
    result = extract_permissions(_MinimalTagger)
    assert result == ExtensionPermissions()


def test_no_declaration_returns_empty_for_artwork_source() -> None:
    result = extract_permissions(_MinimalArtSource)
    assert result == ExtensionPermissions()


def test_base_classes_have_empty_default_attributes() -> None:
    """ABC defaults ensure subclasses that declare nothing get no permissions."""
    assert BaseTagger.kampground_permissions == []
    assert BaseTagger.kampground_network_domains == []
    assert BaseArtworkSource.kampground_permissions == []
    assert BaseArtworkSource.kampground_network_domains == []
    assert BaseSyncer.kampground_permissions == []
    assert BaseSyncer.kampground_network_domains == []


# ---------------------------------------------------------------------------
# Single permission declared
# ---------------------------------------------------------------------------


def test_library_write_permission() -> None:
    class _Writer(BaseTagger):
        kampground_permissions = ["library.write"]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    result = extract_permissions(_Writer)
    assert result.permissions == frozenset({"library.write"})
    assert result.allowed_domains == frozenset()


def test_network_external_with_domains() -> None:
    class _Fetcher(BaseTagger):
        kampground_permissions = ["network.fetch"]
        kampground_network_domains = ["api.discogs.com", "coverartarchive.org"]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    result = extract_permissions(_Fetcher)
    assert "network.fetch" in result.permissions
    assert result.allowed_domains == frozenset(
        {"api.discogs.com", "coverartarchive.org"}
    )


def test_multiple_permissions() -> None:
    class _MultiPerm(BaseTagger):
        kampground_permissions = ["network.fetch", "library.write"]
        kampground_network_domains = ["api.example.com"]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    result = extract_permissions(_MultiPerm)
    assert result.permissions == frozenset({"network.fetch", "library.write"})
    assert "api.example.com" in result.allowed_domains


# ---------------------------------------------------------------------------
# Inherited vs overridden attributes
# ---------------------------------------------------------------------------


def test_subclass_inherits_empty_defaults() -> None:
    """A subclass that doesn't override the attribute inherits the ABC default."""

    class _Plain(BaseTagger):
        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    result = extract_permissions(_Plain)
    assert result == ExtensionPermissions()


def test_subclass_override_does_not_affect_base() -> None:
    """Overriding on a subclass must not mutate the ABC class attribute."""

    class _Overriding(BaseTagger):
        kampground_permissions = ["library.write"]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    extract_permissions(_Overriding)
    # ABC default is still empty
    assert BaseTagger.kampground_permissions == []


# ---------------------------------------------------------------------------
# Defensive: malformed / unexpected attribute types are tolerated
# ---------------------------------------------------------------------------


def test_non_list_permissions_attribute_returns_empty() -> None:
    class _Bad(BaseTagger):
        kampground_permissions = "network.fetch"  # type: ignore[assignment]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    result = extract_permissions(_Bad)
    assert result.permissions == frozenset()


def test_non_string_entries_filtered_out() -> None:
    class _Mixed(BaseTagger):
        kampground_permissions = ["library.write", 42, None]  # type: ignore[list-item]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    result = extract_permissions(_Mixed)
    assert result.permissions == frozenset({"library.write"})


# ---------------------------------------------------------------------------
# Integration: extracted permissions reach KampGround gates
# ---------------------------------------------------------------------------


def test_extracted_permissions_gate_fetch() -> None:
    """Permissions extracted from class attributes are respected by KampGround."""

    class _NetworkExt(BaseTagger):
        kampground_permissions = ["network.fetch"]
        kampground_network_domains = ["api.example.com"]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    perms = extract_permissions(_NetworkExt)
    ctx = KampGround(
        permissions=perms.permissions,
        allowed_domains=perms.allowed_domains,
    )
    # Permission declared → no "network.fetch" error, but unlisted domain is blocked.
    with pytest.raises(PermissionError, match="evil.com"):
        ctx.fetch("https://evil.com/steal")  # blocked — evil.com not in allowlist


def test_extracted_permissions_gate_write() -> None:
    class _WriteExt(BaseTagger):
        kampground_permissions = ["library.write"]

        def tag(self, track: TrackMetadata) -> TrackMetadata:
            return track

    perms = extract_permissions(_WriteExt)
    ctx = KampGround(permissions=perms.permissions)
    # Should not raise — library.write declared
    ctx.update_metadata("mbid-1", {"title": "New Title"})
    assert len(ctx.pending_mutations) == 1


# ---------------------------------------------------------------------------
# ExtensionPermissions dataclass properties
# ---------------------------------------------------------------------------


def test_extension_permissions_is_frozen() -> None:
    p = ExtensionPermissions(
        permissions=frozenset({"network.fetch"}),
        allowed_domains=frozenset({"example.com"}),
    )
    with pytest.raises((AttributeError, TypeError)):
        p.permissions = frozenset()  # type: ignore[misc]


def test_extension_permissions_equality() -> None:
    a = ExtensionPermissions(permissions=frozenset({"library.write"}))
    b = ExtensionPermissions(permissions=frozenset({"library.write"}))
    assert a == b
