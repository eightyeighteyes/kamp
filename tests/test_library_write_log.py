"""Tests for TASK-85: library.write audit log.

Covers all five acceptance criteria:

AC #1 — extension_audit_log table has required columns.
AC #2 — every update_metadata / set_artwork call is logged before the write.
AC #3 — audit log is append-only (DELETE and UPDATE raise).
AC #4 — rollback_extension() reverts all writes by a given extension_id.
AC #5 — apply_mutations() rejects unknown mutation types; only
         UpdateMetadataMutation and SetArtworkMutation are permitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kamp_core.library import LibraryIndex, Track
from kamp_daemon.ext.context import (
    SetArtworkMutation,
    UpdateMetadataMutation,
)
from kamp_daemon.ext.types import ArtworkResult
from kamp_daemon.ext.write_log import apply_mutations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_track(
    path: Path,
    *,
    mbid: str = "rec-1",
    title: str = "Original Title",
    artist: str = "Original Artist",
    embedded_art: bool = False,
) -> Track:
    return Track(
        file_path=path,
        title=title,
        artist=artist,
        album_artist=artist,
        album="Some Album",
        year="2020",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=embedded_art,
        mb_release_id="rel-1",
        mb_recording_id=mbid,
    )


def _make_library(tmp_path: Path) -> LibraryIndex:
    return LibraryIndex(tmp_path / "library.db")


def _make_artwork() -> ArtworkResult:
    return ArtworkResult(image_bytes=b"\xff\xd8\xff", mime_type="image/jpeg")


# ---------------------------------------------------------------------------
# AC #1 — audit_log table has required columns
# ---------------------------------------------------------------------------


class TestAuditLogTableSchema:
    def test_audit_log_table_exists(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        row = lib._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='extension_audit_log'"
        ).fetchone()
        lib.close()
        assert row is not None, "extension_audit_log table must exist"

    def test_audit_log_has_required_columns(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        cols = {
            row["name"]
            for row in lib._conn.execute(
                "PRAGMA table_info(extension_audit_log)"
            ).fetchall()
        }
        lib.close()
        assert {
            "extension_id",
            "operation",
            "old_value",
            "new_value",
            "timestamp",
        } <= cols


# ---------------------------------------------------------------------------
# AC #2 — mutations are logged before the write is applied
# ---------------------------------------------------------------------------


class TestMutationLogging:
    def test_update_metadata_logged(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1", title="Old"))

        lib.apply_metadata_update("ext-a", "rec-1", {"title": "New Title"})

        rows = lib.audit_log_for("ext-a")
        lib.close()

        assert len(rows) == 1
        assert rows[0]["operation"] == "update_metadata"
        assert rows[0]["extension_id"] == "ext-a"
        assert rows[0]["track_mbid"] == "rec-1"
        assert json.loads(rows[0]["old_value"])["title"] == "Old"
        assert json.loads(rows[0]["new_value"])["title"] == "New Title"

    def test_update_metadata_actually_applied(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1", title="Old"))

        lib.apply_metadata_update("ext-a", "rec-1", {"title": "New Title"})

        row = lib._conn.execute(
            "SELECT title FROM tracks WHERE mb_recording_id = ?", ("rec-1",)
        ).fetchone()
        lib.close()
        assert row["title"] == "New Title"

    def test_set_artwork_logged(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(
            _make_track(tmp_path / "a.mp3", mbid="rec-2", embedded_art=False)
        )

        lib.apply_set_artwork("ext-b", "rec-2", "image/jpeg")

        rows = lib.audit_log_for("ext-b")
        lib.close()

        assert len(rows) == 1
        assert rows[0]["operation"] == "set_artwork"
        old = json.loads(rows[0]["old_value"])
        new = json.loads(rows[0]["new_value"])
        assert old["embedded_art"] is False
        assert new["mime_type"] == "image/jpeg"

    def test_set_artwork_actually_applied(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(
            _make_track(tmp_path / "a.mp3", mbid="rec-2", embedded_art=False)
        )

        lib.apply_set_artwork("ext-b", "rec-2", "image/jpeg")

        row = lib._conn.execute(
            "SELECT embedded_art FROM tracks WHERE mb_recording_id = ?", ("rec-2",)
        ).fetchone()
        lib.close()
        assert bool(row["embedded_art"]) is True

    def test_log_entry_written_even_when_track_not_found(self, tmp_path: Path) -> None:
        """Audit log must record the attempt even if no matching track exists."""
        lib = _make_library(tmp_path)

        lib.apply_metadata_update("ext-a", "nonexistent-mbid", {"title": "X"})

        rows = lib.audit_log_for("ext-a")
        lib.close()
        assert len(rows) == 1
        assert rows[0]["track_mbid"] == "nonexistent-mbid"

    def test_multiple_mutations_all_logged_in_order(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.upsert_track(_make_track(tmp_path / "b.mp3", mbid="rec-2"))

        lib.apply_metadata_update("ext-x", "rec-1", {"title": "T1"})
        lib.apply_set_artwork("ext-x", "rec-2", "image/png")
        lib.apply_metadata_update("ext-x", "rec-1", {"artist": "New Artist"})

        rows = lib.audit_log_for("ext-x")
        lib.close()
        assert len(rows) == 3
        assert rows[0]["operation"] == "update_metadata"
        assert rows[1]["operation"] == "set_artwork"
        assert rows[2]["operation"] == "update_metadata"


# ---------------------------------------------------------------------------
# AC #3 — audit log is append-only
# ---------------------------------------------------------------------------


class TestAuditLogAppendOnly:
    def test_delete_from_audit_log_raises(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.apply_metadata_update("ext-a", "rec-1", {"title": "X"})

        with pytest.raises(Exception):
            lib._conn.execute("DELETE FROM extension_audit_log")

        lib.close()

    def test_update_audit_log_raises(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.apply_metadata_update("ext-a", "rec-1", {"title": "X"})

        with pytest.raises(Exception):
            lib._conn.execute("UPDATE extension_audit_log SET operation = 'tampered'")

        lib.close()


# ---------------------------------------------------------------------------
# AC #4 — rollback_extension() reverts all writes by a given extension_id
# ---------------------------------------------------------------------------


class TestRollbackExtension:
    def test_rollback_metadata_restores_old_title(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1", title="Before"))

        lib.apply_metadata_update("ext-bad", "rec-1", {"title": "After"})
        lib.rollback_extension("ext-bad")

        row = lib._conn.execute(
            "SELECT title FROM tracks WHERE mb_recording_id = ?", ("rec-1",)
        ).fetchone()
        lib.close()
        assert row["title"] == "Before"

    def test_rollback_set_artwork_restores_embedded_art_flag(
        self, tmp_path: Path
    ) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(
            _make_track(tmp_path / "a.mp3", mbid="rec-2", embedded_art=False)
        )

        lib.apply_set_artwork("ext-bad", "rec-2", "image/jpeg")
        lib.rollback_extension("ext-bad")

        row = lib._conn.execute(
            "SELECT embedded_art FROM tracks WHERE mb_recording_id = ?", ("rec-2",)
        ).fetchone()
        lib.close()
        assert bool(row["embedded_art"]) is False

    def test_rollback_multiple_writes_in_reverse_order(self, tmp_path: Path) -> None:
        """Sequential writes on the same field must roll back to the pre-first-write value."""
        lib = _make_library(tmp_path)
        lib.upsert_track(
            _make_track(tmp_path / "a.mp3", mbid="rec-1", title="Original")
        )

        lib.apply_metadata_update("ext-bad", "rec-1", {"title": "First"})
        lib.apply_metadata_update("ext-bad", "rec-1", {"title": "Second"})
        lib.rollback_extension("ext-bad")

        row = lib._conn.execute(
            "SELECT title FROM tracks WHERE mb_recording_id = ?", ("rec-1",)
        ).fetchone()
        lib.close()
        assert row["title"] == "Original"

    def test_rollback_returns_count_of_reverted_mutations(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1"))
        lib.upsert_track(_make_track(tmp_path / "b.mp3", mbid="rec-2"))

        lib.apply_metadata_update("ext-bad", "rec-1", {"title": "X"})
        lib.apply_set_artwork("ext-bad", "rec-2", "image/jpeg")

        count = lib.rollback_extension("ext-bad")
        lib.close()
        assert count == 2

    def test_rollback_only_affects_given_extension(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1", title="Base"))

        lib.apply_metadata_update("ext-good", "rec-1", {"title": "GoodWrite"})
        lib.apply_metadata_update("ext-bad", "rec-1", {"title": "BadWrite"})
        lib.rollback_extension("ext-bad")

        row = lib._conn.execute(
            "SELECT title FROM tracks WHERE mb_recording_id = ?", ("rec-1",)
        ).fetchone()
        lib.close()
        # ext-good's write is still in place; only ext-bad's is reversed
        assert row["title"] == "GoodWrite"

    def test_rollback_returns_zero_when_no_mutations(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        count = lib.rollback_extension("nonexistent-ext")
        lib.close()
        assert count == 0


# ---------------------------------------------------------------------------
# AC #5 — apply_mutations() rejects unknown mutation types
# ---------------------------------------------------------------------------


class TestApplyMutationsDispatch:
    def test_apply_update_metadata_mutation(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-1", title="Old"))

        mutations = [UpdateMetadataMutation(mbid="rec-1", fields={"title": "New"})]
        apply_mutations("ext-a", mutations, lib)

        rows = lib.audit_log_for("ext-a")
        lib.close()
        assert len(rows) == 1
        assert rows[0]["operation"] == "update_metadata"

    def test_apply_set_artwork_mutation(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        lib.upsert_track(_make_track(tmp_path / "a.mp3", mbid="rec-2"))

        mutations = [SetArtworkMutation(mbid="rec-2", artwork=_make_artwork())]
        apply_mutations("ext-b", mutations, lib)

        rows = lib.audit_log_for("ext-b")
        lib.close()
        assert len(rows) == 1
        assert rows[0]["operation"] == "set_artwork"

    def test_unknown_mutation_type_raises_value_error(self, tmp_path: Path) -> None:
        """Any mutation type beyond the two permitted ones must be rejected."""
        lib = _make_library(tmp_path)

        class _UnknownMutation:
            mbid = "rec-1"

        with pytest.raises(ValueError, match="Unknown mutation type"):
            apply_mutations("ext-c", [_UnknownMutation()], lib)  # type: ignore[list-item]

        lib.close()

    def test_apply_mutations_empty_list_is_noop(self, tmp_path: Path) -> None:
        lib = _make_library(tmp_path)
        apply_mutations("ext-a", [], lib)  # must not raise
        assert lib.audit_log_for("ext-a") == []
        lib.close()
