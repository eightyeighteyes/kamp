"""Tests for KAMP-309: deferred tag/rename operation queue."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, call

import mutagen.id3 as id3
import pytest

from kamp_core.deferred_ops import MAX_ATTEMPTS, drain_all, drain_for_track, execute_op
from kamp_core.library import DeferredOp, LibraryIndex, Track

# ---------------------------------------------------------------------------
# track_artist_retag + artist-augmented track_retag (KAMP-582)
# ---------------------------------------------------------------------------


class TestTrackArtistRetagOp:
    def test_execute_writes_artist_tag_and_completes(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)
        index = LibraryIndex(tmp_path / "db.sqlite")
        index.upsert_track(_sample_track(mp3))
        row = index.get_track_by_path(mp3)
        assert row is not None
        op_id = index.queue_deferred_op(
            "track_artist_retag", row.id, json.dumps({"artist": "New Guy"})
        )
        op = next(
            o for o in index.pending_deferred_ops_for_track(row.id) if o.id == op_id
        )
        on_completed = MagicMock()
        notify = MagicMock()

        execute_op(op, index, None, on_completed, notify)

        tags = id3.ID3(str(mp3))
        assert str(tags["TPE1"]) == "New Guy"
        assert index.pending_deferred_ops_for_track(row.id) == []
        on_completed.assert_called_once_with(row.id, op_id)
        notify.assert_called_once()
        index.close()

    def test_execute_resolves_current_path_from_index(self, tmp_path: Path) -> None:
        """The payload carries no path: a rename between queueing and drain must
        not strand the write on a stale location."""
        old = tmp_path / "01.mp3"
        _make_mp3(old)
        index = LibraryIndex(tmp_path / "db.sqlite")
        index.upsert_track(_sample_track(old))
        row = index.get_track_by_path(old)
        assert row is not None
        op_id = index.queue_deferred_op(
            "track_artist_retag", row.id, json.dumps({"artist": "New Guy"})
        )
        # Simulate a later title-edit rename before the drain runs.
        new = tmp_path / "01 - Renamed.mp3"
        old.rename(new)
        index.move_track(old, new, "Renamed", time.time())
        op = next(
            o for o in index.pending_deferred_ops_for_track(row.id) if o.id == op_id
        )

        execute_op(op, index, None, MagicMock(), MagicMock())

        tags = id3.ID3(str(new))
        assert str(tags["TPE1"]) == "New Guy"
        index.close()

    def test_track_retag_with_artist_writes_both(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, title="Old")
        index = LibraryIndex(tmp_path / "db.sqlite")
        index.upsert_track(_sample_track(mp3, title="Old"))
        row = index.get_track_by_path(mp3)
        assert row is not None
        payload = json.dumps(
            {
                "old_path": str(mp3),
                "new_path": str(mp3),
                "title": "New Title",
                "is_case_only": False,
                "artist": "New Guy",
            }
        )
        op_id = index.queue_deferred_op("track_retag", row.id, payload)
        op = next(
            o for o in index.pending_deferred_ops_for_track(row.id) if o.id == op_id
        )

        execute_op(op, index, None, MagicMock(), MagicMock())

        tags = id3.ID3(str(mp3))
        assert str(tags["TIT2"]) == "New Title"
        assert str(tags["TPE1"]) == "New Guy"
        index.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mp3(
    path: Path, title: str = "T", album: str = "A", album_artist: str = "AA"
) -> None:
    t = id3.ID3()
    t["TIT2"] = id3.TIT2(encoding=3, text=title)
    t["TALB"] = id3.TALB(encoding=3, text=album)
    t["TPE2"] = id3.TPE2(encoding=3, text=album_artist)
    t["TPE1"] = id3.TPE1(encoding=3, text=album_artist)
    t["TRCK"] = id3.TRCK(encoding=3, text="1")
    t["TDRC"] = id3.TDRC(encoding=3, text="2024")
    path.write_bytes(b"\xff\xfb" * 64)
    t.save(str(path))


def _sample_track(file_path: Path, **overrides: object) -> Track:
    defaults: dict[str, object] = dict(
        file_path=file_path,
        title="T",
        artist="AA",
        album_artist="AA",
        album="A",
        release_date="2024",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="rel-1",
        mb_recording_id="rec-1",
    )
    defaults.update(overrides)
    return Track(**defaults)  # type: ignore[arg-type]


def _make_op(
    index: LibraryIndex,
    track_id: int,
    old_path: Path,
    new_path: Path,
    op_type: str = "track_retag",
    title: str = "New",
) -> DeferredOp:
    if op_type == "track_retag":
        payload = json.dumps(
            {
                "old_path": str(old_path),
                "new_path": str(new_path),
                "title": title,
                "is_case_only": False,
            }
        )
    else:
        payload = json.dumps(
            {
                "old_path": str(old_path),
                "new_path": str(new_path),
                "new_album": "New Album",
                "new_album_artist": "New Artist",
                "new_artist": None,
                "is_case_only": False,
            }
        )
    op_id = index.queue_deferred_op(op_type, track_id, payload)
    ops = index.pending_deferred_ops_for_track(track_id)
    return next(o for o in ops if o.id == op_id)


# ---------------------------------------------------------------------------
# LibraryIndex deferred-ops CRUD
# ---------------------------------------------------------------------------


class TestDeferredOpCrud:
    def test_queue_op_inserts_row(self, tmp_path: Path) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        payload = json.dumps(
            {"old_path": "/a", "new_path": "/b", "title": "T", "is_case_only": False}
        )
        op_id = db.queue_deferred_op("track_retag", 42, payload)
        assert op_id > 0
        ops = db.all_pending_deferred_ops()
        assert len(ops) == 1
        assert ops[0].track_id == 42
        assert ops[0].op_type == "track_retag"
        db.close()

    def test_queue_op_coalesces_duplicate_track_id(self, tmp_path: Path) -> None:
        """INSERT OR REPLACE means second edit for same locked track wins."""
        db = LibraryIndex(tmp_path / "db.sqlite")
        p = json.dumps(
            {
                "old_path": "/a",
                "new_path": "/b",
                "title": "First",
                "is_case_only": False,
            }
        )
        db.queue_deferred_op("track_retag", 5, p)
        p2 = json.dumps(
            {
                "old_path": "/a",
                "new_path": "/c",
                "title": "Second",
                "is_case_only": False,
            }
        )
        db.queue_deferred_op("track_retag", 5, p2)
        ops = db.all_pending_deferred_ops()
        assert len(ops) == 1
        assert json.loads(ops[0].payload_json)["title"] == "Second"
        db.close()

    def test_complete_op_deletes_row(self, tmp_path: Path) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        p = json.dumps(
            {"old_path": "/a", "new_path": "/b", "title": "T", "is_case_only": False}
        )
        op_id = db.queue_deferred_op("track_retag", 1, p)
        db.complete_deferred_op(op_id)
        assert db.all_pending_deferred_ops() == []
        db.close()

    def test_fail_op_increments_attempts(self, tmp_path: Path) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        p = json.dumps(
            {"old_path": "/a", "new_path": "/b", "title": "T", "is_case_only": False}
        )
        op_id = db.queue_deferred_op("track_retag", 1, p)
        db.fail_deferred_op(op_id, "boom")
        ops = db.all_pending_deferred_ops()
        assert ops[0].attempts == 1
        assert ops[0].last_error == "boom"
        db.close()

    def test_list_pending_summary_returns_op_and_track_ids(
        self, tmp_path: Path
    ) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        p = json.dumps(
            {"old_path": "/a", "new_path": "/b", "title": "T", "is_case_only": False}
        )
        op_id = db.queue_deferred_op("track_retag", 7, p)
        summary = db.list_pending_deferred_ops_summary()
        assert len(summary) == 1
        assert summary[0]["op_id"] == op_id
        assert summary[0]["track_id"] == 7
        db.close()


# ---------------------------------------------------------------------------
# execute_op
# ---------------------------------------------------------------------------


class TestExecuteOp:
    def test_execute_op_track_retag_moves_and_tags(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "old.mp3"
        _make_mp3(mp3, title="Old")
        new_mp3 = tmp_path / "new.mp3"

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(mp3, title="Old")
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op = _make_op(db, row.id, mp3, new_mp3, title="New")

        on_completed = MagicMock()
        notify_changed = MagicMock()
        execute_op(op, db, None, on_completed, notify_changed)

        assert new_mp3.exists()
        assert not mp3.exists()
        tags = id3.ID3(str(new_mp3))
        assert str(tags["TIT2"]) == "New"
        on_completed.assert_called_once_with(row.id, op.id)
        notify_changed.assert_called_once()
        assert db.all_pending_deferred_ops() == []
        db.close()

    def test_execute_op_track_retag_path_unchanged_no_move(
        self, tmp_path: Path
    ) -> None:
        mp3 = tmp_path / "same.mp3"
        _make_mp3(mp3, title="Old")

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(mp3, title="Old")
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op = _make_op(db, row.id, mp3, mp3, title="New")  # same path

        execute_op(op, db, None, MagicMock(), MagicMock())

        assert mp3.exists()
        tags = id3.ID3(str(mp3))
        assert str(tags["TIT2"]) == "New"
        db.close()

    def test_execute_op_album_retag_writes_tags_and_moves(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "Old Artist" / "2024 - Old Album"
        old_dir.mkdir(parents=True)
        mp3 = old_dir / "01 - T.mp3"
        _make_mp3(mp3, title="T", album="Old Album", album_artist="Old Artist")

        new_dir = tmp_path / "New Artist" / "2024 - New Album"
        new_mp3 = new_dir / "01 - T.mp3"

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(
            mp3, title="T", album="Old Album", album_artist="Old Artist"
        )
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op = _make_op(db, row.id, mp3, new_mp3, op_type="album_retag")

        execute_op(op, db, None, MagicMock(), MagicMock())

        assert new_mp3.exists()
        assert not mp3.exists()
        tags = id3.ID3(str(new_mp3))
        assert str(tags["TALB"]) == "New Album"
        assert str(tags["TPE2"]) == "New Artist"
        db.close()

    def test_execute_op_track_retag_calls_watcher_when_moved(
        self, tmp_path: Path
    ) -> None:
        """lib_watcher.suppress_paths and scan_now are called when the file is moved."""
        mp3 = tmp_path / "old.mp3"
        _make_mp3(mp3, title="Old")
        new_mp3 = tmp_path / "sub" / "new.mp3"

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(mp3, title="Old")
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op = _make_op(db, row.id, mp3, new_mp3, title="New")
        lib_watcher = MagicMock()
        execute_op(op, db, lib_watcher, MagicMock(), MagicMock())

        lib_watcher.suppress_paths.assert_called_once()
        lib_watcher.scan_now.assert_called_once()
        db.close()

    def test_execute_op_unknown_op_type_raises(self, tmp_path: Path) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        p = json.dumps(
            {"old_path": "/a", "new_path": "/b", "title": "T", "is_case_only": False}
        )
        db.queue_deferred_op("track_retag", 1, p)
        ops = db.all_pending_deferred_ops()
        op = ops[0]
        # Patch the op_type to an unknown value.
        bad_op = DeferredOp(
            id=op.id,
            op_type="bad_type",
            track_id=op.track_id,
            payload_json=p,
            created_at=op.created_at,
            attempts=0,
            last_error=None,
        )
        with pytest.raises(ValueError, match="unknown deferred op type"):
            execute_op(bad_op, db, None, MagicMock(), MagicMock())
        db.close()

    def test_execute_op_album_retag_same_path_no_move(self, tmp_path: Path) -> None:
        """album_retag with old_path == new_path skips file move (in-place tag update)."""
        mp3 = tmp_path / "t.mp3"
        _make_mp3(mp3, title="T", album="Old Album", album_artist="AA")

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(mp3, title="T", album="Old Album")
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op_id = db.queue_deferred_op(
            "album_retag",
            row.id,
            json.dumps(
                {
                    "old_path": str(mp3),
                    "new_path": str(mp3),  # same path
                    "new_album": "New Album",
                    "new_album_artist": "AA",
                    "new_artist": None,
                    "is_case_only": False,
                }
            ),
        )
        op = db.pending_deferred_ops_for_track(row.id)[0]

        execute_op(op, db, None, MagicMock(), MagicMock())

        assert mp3.exists()
        tags = id3.ID3(str(mp3))
        assert str(tags["TALB"]) == "New Album"
        db.close()

    def test_execute_op_completed_fires_before_library_changed(
        self, tmp_path: Path
    ) -> None:
        """deferred_op.completed must broadcast before library.changed (KAMP-309)."""
        mp3 = tmp_path / "old.mp3"
        _make_mp3(mp3, title="Old")

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(mp3, title="Old")
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op = _make_op(db, row.id, mp3, mp3, title="New")

        call_order: list[str] = []
        execute_op(
            op,
            db,
            None,
            lambda tid, oid: call_order.append("completed"),
            lambda: call_order.append("changed"),
        )
        assert call_order == ["completed", "changed"]
        db.close()


# ---------------------------------------------------------------------------
# drain_for_track
# ---------------------------------------------------------------------------


class TestDrainForTrack:
    def test_drain_for_track_runs_all_pending_in_order(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "t.mp3"
        _make_mp3(mp3, title="Old")

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(mp3, title="Old")
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op = _make_op(db, row.id, mp3, mp3, title="New")
        on_completed = MagicMock()
        drain_for_track(row.id, db, None, on_completed, MagicMock())

        on_completed.assert_called_once_with(row.id, op.id)
        assert db.all_pending_deferred_ops() == []
        db.close()

    def test_drain_for_track_handles_failure_and_retries(self, tmp_path: Path) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        # Use unsupported extension so write_title_to_file raises ValueError — reliable failure.
        p = json.dumps(
            {
                "old_path": str(tmp_path / "missing.wav"),
                "new_path": str(tmp_path / "also-missing.wav"),
                "title": "New",
                "is_case_only": False,
            }
        )
        db.queue_deferred_op("track_retag", 99, p)

        drain_for_track(99, db, None, MagicMock(), MagicMock())
        ops = db.all_pending_deferred_ops()
        # First failure: attempts=1, row still present.
        assert len(ops) == 1
        assert ops[0].attempts == 1
        db.close()

    def test_max_attempts_deletes_row_after_third_failure(self, tmp_path: Path) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        p = json.dumps(
            {
                "old_path": str(tmp_path / "missing.wav"),
                "new_path": str(tmp_path / "also-missing.wav"),
                "title": "New",
                "is_case_only": False,
            }
        )
        db.queue_deferred_op("track_retag", 99, p)

        # Three consecutive failures = max attempts → row deleted.
        for _ in range(MAX_ATTEMPTS):
            drain_for_track(99, db, None, MagicMock(), MagicMock())

        assert db.all_pending_deferred_ops() == []
        db.close()


# ---------------------------------------------------------------------------
# drain_all
# ---------------------------------------------------------------------------


class TestDrainAll:
    def test_drain_all_skips_locked_track(self, tmp_path: Path) -> None:
        db = LibraryIndex(tmp_path / "db.sqlite")
        p = json.dumps(
            {
                "old_path": str(tmp_path / "t.mp3"),
                "new_path": str(tmp_path / "t.mp3"),
                "title": "New",
                "is_case_only": False,
            }
        )
        db.queue_deferred_op("track_retag", 7, p)

        drain_all(db, None, MagicMock(), MagicMock(), is_locked=lambda tid: tid == 7)

        # Row still present — track was locked.
        assert len(db.all_pending_deferred_ops()) == 1
        db.close()

    def test_drain_all_respects_timeout(self, tmp_path: Path) -> None:
        mp3a = tmp_path / "a.mp3"
        mp3b = tmp_path / "b.mp3"
        _make_mp3(mp3a, title="A")
        _make_mp3(mp3b, title="B")

        db = LibraryIndex(tmp_path / "db.sqlite")
        for i, mp3 in enumerate([mp3a, mp3b], start=1):
            track = _sample_track(mp3, title=chr(64 + i), mb_recording_id=f"rec-{i}")
            db.upsert_track(track)
        rows = db.all_pending_deferred_ops()  # should be empty initially
        assert rows == []

        for mp3 in [mp3a, mp3b]:
            row = db.get_track_by_path(mp3)
            assert row is not None
            db.queue_deferred_op(
                "track_retag",
                row.id,
                json.dumps(
                    {
                        "old_path": str(mp3),
                        "new_path": str(mp3),
                        "title": "New",
                        "is_case_only": False,
                    }
                ),
            )

        # Zero timeout → neither op executes.
        drain_all(db, None, MagicMock(), MagicMock(), timeout_secs=0.0)
        assert len(db.all_pending_deferred_ops()) == 2
        db.close()

    def test_drain_all_handles_failing_op(self, tmp_path: Path) -> None:
        """Exception inside drain_all calls _handle_failure."""
        db = LibraryIndex(tmp_path / "db.sqlite")
        # Use unsupported extension to force failure.
        db.queue_deferred_op(
            "track_retag",
            99,
            json.dumps(
                {
                    "old_path": str(tmp_path / "t.wav"),
                    "new_path": str(tmp_path / "t2.wav"),
                    "title": "New",
                    "is_case_only": False,
                }
            ),
        )

        drain_all(db, None, MagicMock(), MagicMock())
        ops = db.all_pending_deferred_ops()
        assert len(ops) == 1
        assert ops[0].attempts == 1
        db.close()

    def test_drain_all_executes_unlocked_ops(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "t.mp3"
        _make_mp3(mp3, title="Old")

        db = LibraryIndex(tmp_path / "db.sqlite")
        track = _sample_track(mp3, title="Old")
        db.upsert_track(track)
        row = db.get_track_by_path(mp3)
        assert row is not None

        op_id = db.queue_deferred_op(
            "track_retag",
            row.id,
            json.dumps(
                {
                    "old_path": str(mp3),
                    "new_path": str(mp3),
                    "title": "New",
                    "is_case_only": False,
                }
            ),
        )

        on_completed = MagicMock()
        drain_all(db, None, on_completed, MagicMock())

        on_completed.assert_called_once_with(row.id, op_id)
        assert db.all_pending_deferred_ops() == []
        db.close()
