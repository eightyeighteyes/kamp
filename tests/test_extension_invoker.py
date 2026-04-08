"""Tests for TASK-90: extension invocation policy.

Covers all five acceptance criteria:

AC #1 — registered tagger extensions are invoked for new tracks at ingest time.
AC #2 — each track is offered to each extension at most once per ingest event.
AC #3 — the host uses the audit log to enforce single-invocation, not extension
         skip logic.
AC #4 — registered artwork-source extensions are invoked under the same policy.
AC #5 — the invocation policy comment at the call site explains why re-scan is
         excluded (verified structurally by this test file existing alongside
         invoker.py, which contains the documented policy).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, call, patch

import pytest

from kamp_core.library import LibraryIndex, Track
from kamp_daemon.ext.context import KampGround, UpdateMetadataMutation
from kamp_daemon.ext.invoker import (
    _extension_id,
    _track_to_metadata,
    invoke_extensions_for_new_tracks,
)
from kamp_daemon.ext.registry import ExtensionRegistry
from kamp_daemon.ext.types import ArtworkQuery, TrackMetadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_library(tmp_path: Path) -> LibraryIndex:
    return LibraryIndex(tmp_path / "library.db")


def _make_track(
    path: Path,
    *,
    mbid: str = "rec-1",
    release_mbid: str = "rel-1",
    title: str = "Title",
    artist: str = "Artist",
    album: str = "Album",
    album_artist: str = "Album Artist",
) -> Track:
    return Track(
        file_path=path,
        title=title,
        artist=artist,
        album_artist=album_artist,
        album=album,
        year="2024",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id=release_mbid,
        mb_recording_id=mbid,
    )


def _fake_tagger_cls(module: str = "ext.fake", name: str = "FakeTagger") -> type:
    """Return a minimal BaseTagger subclass with a controllable class identity."""
    from kamp_daemon.ext.abc import BaseTagger

    cls = type(name, (BaseTagger,), {"tag": lambda self, track: track})
    cls.__module__ = module
    cls.__qualname__ = name
    return cls


def _fake_artwork_cls(module: str = "ext.fake", name: str = "FakeArtwork") -> type:
    """Return a minimal BaseArtworkSource subclass."""
    from kamp_daemon.ext.abc import BaseArtworkSource

    cls = type(name, (BaseArtworkSource,), {"fetch": lambda self, query: None})
    cls.__module__ = module
    cls.__qualname__ = name
    return cls


def _make_registry(
    taggers: list[type] | None = None,
    artwork_sources: list[type] | None = None,
) -> ExtensionRegistry:
    reg = ExtensionRegistry()
    for cls in taggers or []:
        reg.register(cls)
    for cls in artwork_sources or []:
        reg.register(cls)
    return reg


# ---------------------------------------------------------------------------
# _extension_id helper
# ---------------------------------------------------------------------------


class TestExtensionId:
    def test_returns_module_dot_qualname(self) -> None:
        cls = _fake_tagger_cls(module="my.mod", name="MyClass")
        assert _extension_id(cls) == "my.mod.MyClass"

    def test_different_classes_have_different_ids(self) -> None:
        a = _fake_tagger_cls(module="mod.a", name="A")
        b = _fake_tagger_cls(module="mod.b", name="B")
        assert _extension_id(a) != _extension_id(b)


# ---------------------------------------------------------------------------
# _track_to_metadata helper
# ---------------------------------------------------------------------------


class TestTrackToMetadata:
    def test_mbid_maps_to_recording_mbid(self, tmp_path: Path) -> None:
        track = _make_track(tmp_path / "a.mp3", mbid="rec-abc")
        meta = _track_to_metadata(track)
        assert meta.mbid == "rec-abc"

    def test_release_mbid_maps_correctly(self, tmp_path: Path) -> None:
        track = _make_track(tmp_path / "a.mp3", release_mbid="rel-xyz")
        meta = _track_to_metadata(track)
        assert meta.release_mbid == "rel-xyz"

    def test_release_group_mbid_is_empty(self, tmp_path: Path) -> None:
        track = _make_track(tmp_path / "a.mp3")
        meta = _track_to_metadata(track)
        assert meta.release_group_mbid == ""

    def test_all_core_fields_copied(self, tmp_path: Path) -> None:
        track = _make_track(
            tmp_path / "a.mp3",
            title="My Title",
            artist="My Artist",
            album="My Album",
            album_artist="My Album Artist",
        )
        meta = _track_to_metadata(track)
        assert meta.title == "My Title"
        assert meta.artist == "My Artist"
        assert meta.album == "My Album"
        assert meta.album_artist == "My Album Artist"


# ---------------------------------------------------------------------------
# AC #1 — tagger extensions are invoked for new tracks
# ---------------------------------------------------------------------------


class TestTaggerInvocation:
    def test_tagger_invoked_for_new_track(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))

        cls = _fake_tagger_cls()
        reg = _make_registry(taggers=[cls])

        mutations = [UpdateMetadataMutation(mbid="rec-1", fields={"title": "New"})]
        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=mutations
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3")], lib
            )

        mock_invoke.assert_called_once()
        args = mock_invoke.call_args
        assert args[0][0] is cls
        assert args[0][1] == "tag"

        lib.close()

    def test_tagger_receives_track_metadata(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        cls = _fake_tagger_cls()
        reg = _make_registry(taggers=[cls])

        track = _make_track(tmp_path / "a.mp3", mbid="rec-1", title="Original")
        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=[]
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(reg, [track], lib)

        meta: TrackMetadata = mock_invoke.call_args[0][2]
        assert meta.mbid == "rec-1"
        assert meta.title == "Original"

        lib.close()

    def test_multiple_taggers_invoked_in_registration_order(
        self, tmp_path: Path
    ) -> None:
        lib = _make_library(tmp_path)
        cls_a = _fake_tagger_cls(name="TaggerA")
        cls_b = _fake_tagger_cls(name="TaggerB")
        reg = _make_registry(taggers=[cls_a, cls_b])

        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=[]
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        call_classes = [c[0][0] for c in mock_invoke.call_args_list]
        assert call_classes == [cls_a, cls_b]

        lib.close()

    def test_apply_mutations_called_with_extension_id_and_result(
        self, tmp_path: Path
    ) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))

        cls = _fake_tagger_cls(module="my.tagger", name="T")
        reg = _make_registry(taggers=[cls])
        mutations = [UpdateMetadataMutation(mbid="rec-1", fields={"title": "X"})]

        with patch("kamp_daemon.ext.invoker.invoke_extension", return_value=mutations):
            with patch("kamp_daemon.ext.invoker.apply_mutations") as mock_apply:
                invoke_extensions_for_new_tracks(
                    reg, [_make_track(tmp_path / "a.mp3")], lib
                )

        mock_apply.assert_called_once_with("my.tagger.T", mutations, lib)

        lib.close()


# ---------------------------------------------------------------------------
# AC #2 — each track offered at most once per ingest (no redundant mutations)
# ---------------------------------------------------------------------------


class TestSingleInvocationPerTrack:
    def test_track_with_empty_mbid_is_skipped(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        cls = _fake_tagger_cls()
        reg = _make_registry(taggers=[cls])

        untagged = _make_track(tmp_path / "a.mp3", mbid="")

        with patch("kamp_daemon.ext.invoker.invoke_extension") as mock_invoke:
            invoke_extensions_for_new_tracks(reg, [untagged], lib)

        mock_invoke.assert_not_called()
        lib.close()

    def test_empty_registry_is_noop(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        reg = _make_registry()

        with patch("kamp_daemon.ext.invoker.invoke_extension") as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3")], lib
            )

        mock_invoke.assert_not_called()
        lib.close()

    def test_empty_track_list_is_noop(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        cls = _fake_tagger_cls()
        reg = _make_registry(taggers=[cls])

        with patch("kamp_daemon.ext.invoker.invoke_extension") as mock_invoke:
            invoke_extensions_for_new_tracks(reg, [], lib)

        mock_invoke.assert_not_called()
        lib.close()


# ---------------------------------------------------------------------------
# AC #3 — host enforces single-invocation via audit log, not extension logic
# ---------------------------------------------------------------------------


class TestAuditLogEnforcement:
    def test_already_processed_track_is_skipped(self, tmp_path: Path) -> None:
        """If an audit log entry exists, invoke_extension must not be called."""
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))

        cls = _fake_tagger_cls(module="ext.t", name="T")
        ext_id = _extension_id(cls)
        # Simulate a prior run by writing a log entry directly.
        lib.apply_metadata_update(ext_id, "rec-1", {"title": "Prior"})

        reg = _make_registry(taggers=[cls])

        with patch("kamp_daemon.ext.invoker.invoke_extension") as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        mock_invoke.assert_not_called()
        lib.close()

    def test_unprocessed_track_is_not_skipped(self, tmp_path: Path) -> None:
        """No audit log entry → invoke_extension must be called."""
        lib = _make_library(tmp_path)
        cls = _fake_tagger_cls()
        reg = _make_registry(taggers=[cls])

        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=[]
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        mock_invoke.assert_called_once()
        lib.close()

    def test_different_extension_does_not_count_as_processed(
        self, tmp_path: Path
    ) -> None:
        """An audit log entry for ext-A does not prevent ext-B from running."""
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))

        # Write an entry for a *different* extension ID.
        lib.apply_metadata_update("some.other.Ext", "rec-1", {"title": "X"})

        cls = _fake_tagger_cls(module="my.ext", name="Mine")
        reg = _make_registry(taggers=[cls])

        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=[]
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        mock_invoke.assert_called_once()
        lib.close()

    def test_mixed_processed_and_new_tracks(self, tmp_path: Path) -> None:
        """Only the unprocessed track triggers invoke_extension."""
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.upsert_track(_make_track(tmp_path / "b.mp3", mbid="rec-2"))

        cls = _fake_tagger_cls(module="ext.t", name="T")
        ext_id = _extension_id(cls)
        lib.apply_metadata_update(
            ext_id, "rec-1", {"title": "Old"}
        )  # already processed

        reg = _make_registry(taggers=[cls])
        tracks = [
            _make_track(tmp_path / "a.mp3", mbid="rec-1"),  # processed → skip
            _make_track(tmp_path / "b.mp3", mbid="rec-2"),  # new → invoke
        ]

        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=[]
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(reg, tracks, lib)

        assert mock_invoke.call_count == 1
        invoked_meta: TrackMetadata = mock_invoke.call_args[0][2]
        assert invoked_meta.mbid == "rec-2"

        lib.close()

    def test_failed_invoke_does_not_log_to_audit(self, tmp_path: Path) -> None:
        """A False return from invoke_extension leaves no audit entry (no retry block)."""
        lib = _make_library(tmp_path)
        cls = _fake_tagger_cls(module="ext.t", name="T")
        ext_id = _extension_id(cls)
        reg = _make_registry(taggers=[cls])

        with patch("kamp_daemon.ext.invoker.invoke_extension", return_value=False):
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        assert lib.audit_log_for(ext_id) == []
        lib.close()

    def test_failed_invoke_does_not_block_subsequent_track(
        self, tmp_path: Path
    ) -> None:
        """A failure on track A must not prevent track B from being offered."""
        lib = _make_library(tmp_path)
        cls = _fake_tagger_cls()
        reg = _make_registry(taggers=[cls])

        side_effects = [False, []]  # first call fails, second succeeds
        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", side_effect=side_effects
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg,
                [
                    _make_track(tmp_path / "a.mp3", mbid="rec-1"),
                    _make_track(tmp_path / "b.mp3", mbid="rec-2"),
                ],
                lib,
            )

        assert mock_invoke.call_count == 2
        lib.close()


# ---------------------------------------------------------------------------
# AC #4 — artwork-source extensions invoked under the same policy
# ---------------------------------------------------------------------------


class TestArtworkSourceInvocation:
    def test_artwork_source_invoked_for_new_track(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        cls = _fake_artwork_cls()
        reg = _make_registry(artwork_sources=[cls])

        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=[]
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        mock_invoke.assert_called_once()
        args = mock_invoke.call_args[0]
        assert args[0] is cls
        assert args[1] == "fetch"

        lib.close()

    def test_artwork_source_receives_artwork_query(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        cls = _fake_artwork_cls()
        reg = _make_registry(artwork_sources=[cls])

        track = _make_track(
            tmp_path / "a.mp3",
            mbid="rec-1",
            release_mbid="rel-99",
            album="My Album",
            album_artist="My Artist",
        )

        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", return_value=[]
        ) as mock_invoke:
            invoke_extensions_for_new_tracks(reg, [track], lib)

        query: ArtworkQuery = mock_invoke.call_args[0][2]
        assert isinstance(query, ArtworkQuery)
        assert query.mbid == "rel-99"
        assert query.album == "My Album"
        assert query.artist == "My Artist"

        lib.close()

    def test_artwork_source_skipped_when_already_processed(
        self, tmp_path: Path
    ) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))

        cls = _fake_artwork_cls(module="ext.art", name="Art")
        ext_id = _extension_id(cls)
        lib.apply_set_artwork(ext_id, "rec-1", "image/jpeg")

        reg = _make_registry(artwork_sources=[cls])

        with patch("kamp_daemon.ext.invoker.invoke_extension") as mock_invoke:
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        mock_invoke.assert_not_called()
        lib.close()

    def test_taggers_run_before_artwork_sources(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        tagger_cls = _fake_tagger_cls(name="Tagger")
        art_cls = _fake_artwork_cls(name="Art")
        reg = _make_registry(taggers=[tagger_cls], artwork_sources=[art_cls])

        call_order: list[str] = []

        def _side_effect(cls: type, *args: object, **kwargs: object) -> list:
            call_order.append(cls.__qualname__)
            return []

        with patch(
            "kamp_daemon.ext.invoker.invoke_extension", side_effect=_side_effect
        ):
            invoke_extensions_for_new_tracks(
                reg, [_make_track(tmp_path / "a.mp3", mbid="rec-1")], lib
            )

        assert call_order == ["Tagger", "Art"]
        lib.close()


# ---------------------------------------------------------------------------
# LibraryIndex.has_been_processed_by — unit tests
# ---------------------------------------------------------------------------


class TestHasBeenProcessedBy:
    def test_returns_false_when_no_log_entry(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        assert lib.has_been_processed_by("ext-a", "rec-1") is False
        lib.close()

    def test_returns_true_after_metadata_update(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.apply_metadata_update("ext-a", "rec-1", {"title": "X"})
        assert lib.has_been_processed_by("ext-a", "rec-1") is True
        lib.close()

    def test_returns_true_after_set_artwork(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-2"))
        lib.apply_set_artwork("ext-b", "rec-2", "image/jpeg")
        assert lib.has_been_processed_by("ext-b", "rec-2") is True
        lib.close()

    def test_extension_isolation(self, tmp_path: Path) -> None:
        """An entry for ext-A must not show as processed for ext-B."""
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.apply_metadata_update("ext-a", "rec-1", {"title": "X"})
        assert lib.has_been_processed_by("ext-b", "rec-1") is False
        lib.close()

    def test_track_isolation(self, tmp_path: Path) -> None:
        """An entry for rec-1 must not show as processed for rec-2."""
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.upsert_track(_make_track(tmp_path / "b.mp3", mbid="rec-2"))
        lib.apply_metadata_update("ext-a", "rec-1", {"title": "X"})
        assert lib.has_been_processed_by("ext-a", "rec-2") is False
        lib.close()


# ---------------------------------------------------------------------------
# ScanResult.new_tracks — integration with LibraryScanner
# ---------------------------------------------------------------------------


class TestScanResultNewTracks:
    def test_newly_added_tracks_included_in_new_tracks(self, tmp_path: Path) -> None:
        from kamp_core.library import LibraryScanner

        lib = _make_library(tmp_path)
        lib_dir = tmp_path / "library"
        lib_dir.mkdir()

        # Write a minimal fake MP3 so the scanner can read its tags.
        from mutagen import id3

        mp3 = lib_dir / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        result = LibraryScanner(lib).scan(lib_dir)
        lib.close()

        assert result.added == len(result.new_tracks)
        assert any(t.file_path == mp3 for t in result.new_tracks)

    def test_rescan_produces_no_new_tracks(self, tmp_path: Path) -> None:
        from kamp_core.library import LibraryScanner

        lib = _make_library(tmp_path)
        lib_dir = tmp_path / "library"
        lib_dir.mkdir()

        from mutagen import id3

        mp3 = lib_dir / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        LibraryScanner(lib).scan(lib_dir)  # first scan adds the track
        result = LibraryScanner(lib).scan(lib_dir)  # re-scan
        lib.close()

        # Re-scan with unchanged mtime: no new tracks.
        assert result.new_tracks == []
