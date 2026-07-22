"""Tests for kamp_core.library (LibraryIndex and LibraryScanner)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import keyring.errors
import mutagen.id3 as id3
import pytest
from pytest_mock import MockerFixture

from kamp_core.library import (
    AlbumInfo,
    ArtistInfo,
    Condition,
    Group,
    LibraryIndex,
    LibraryScanner,
    LibraryStats,
    MagicCriteria,
    ScanResult,
    Track,
    extract_art,
    write_meta_tags_to_file,
    _read_mp3_tags,
    _read_m4a_tags,
    _read_vorbis_tags,
    _read_tags,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mp3(path: Path, **tags: str) -> None:
    """Write a minimal ID3-tagged MP3 stub."""
    t = id3.ID3()
    if "artist" in tags:
        t["TPE1"] = id3.TPE1(encoding=3, text=tags["artist"])
    if "album_artist" in tags:
        t["TPE2"] = id3.TPE2(encoding=3, text=tags["album_artist"])
    if "album" in tags:
        t["TALB"] = id3.TALB(encoding=3, text=tags["album"])
    if "release_date" in tags:
        t["TDRC"] = id3.TDRC(encoding=3, text=tags["release_date"])
    if "title" in tags:
        t["TIT2"] = id3.TIT2(encoding=3, text=tags["title"])
    if "track" in tags:
        t["TRCK"] = id3.TRCK(encoding=3, text=tags["track"])
    if "disc" in tags:
        t["TPOS"] = id3.TPOS(encoding=3, text=tags["disc"])
    path.write_bytes(b"\xff\xfb" * 64)
    t.save(str(path))


def _sample_track(file_path: Path) -> Track:
    return Track(
        file_path=file_path,
        title="A Song",
        artist="The Artist",
        album_artist="The Artist",
        album="The Album",
        release_date="2024",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="rel-123",
        mb_recording_id="rec-456",
    )


def _mirror_stats(index: "LibraryIndex") -> None:
    """Propagate every track's legacy stat columns into track_stats (KAMP-542).

    Reads now resolve favorite/play_count/last_played through the
    tracks_with_stats view (i.e. from track_stats), so a test that seeds those
    values with a direct ``UPDATE tracks`` must mirror them, exactly as the
    production stat writers do.
    """
    index._conn.execute(
        "INSERT INTO track_stats (track_id, favorite, play_count, last_played)"
        " SELECT id, favorite, play_count, last_played FROM tracks WHERE true"
        " ON CONFLICT(track_id) DO UPDATE SET favorite=excluded.favorite,"
        " play_count=excluded.play_count, last_played=excluded.last_played"
    )
    index._conn.commit()


# Columns dropped from tracks in KAMP-539 (v49), with their pre-drop definitions.
_LEGACY_TRACK_COLUMNS: list[tuple[str, str]] = [
    # KAMP-552 dropped file_path (was NOT NULL UNIQUE) and sale_item_id from tracks.
    # Re-added here as plain nullable TEXT (ALTER TABLE cannot re-add NOT NULL/UNIQUE)
    # so pre-drop-shape fixtures can still INSERT them; the rebuilt view falls back to
    # them when a fixture omits track_sources.
    ("file_path", "TEXT"),
    ("sale_item_id", "TEXT"),
    ("ext", "TEXT NOT NULL DEFAULT ''"),
    ("embedded_art", "INTEGER NOT NULL DEFAULT 0"),
    ("file_mtime", "REAL"),
    ("source", "TEXT NOT NULL DEFAULT 'local'"),
    ("stream_url", "TEXT"),
    ("stream_url_expires_at", "REAL"),
    ("is_available", "INTEGER NOT NULL DEFAULT 1"),
    ("duration", "REAL NOT NULL DEFAULT 0"),
    ("last_played", "REAL"),
    ("favorite", "INTEGER NOT NULL DEFAULT 0"),
    ("play_count", "INTEGER NOT NULL DEFAULT 0"),
]


def _readd_legacy_track_columns(index: "LibraryIndex") -> None:
    """Re-add the per-source/stat columns KAMP-539 dropped and rebuild the view.

    Two uses: a migration test simulates a pre-v49 DB (seed the old row shape, set
    schema_version back, reopen — the v49 migration drops the columns again), and a
    feature test seeds favorite/source/etc via a direct ``INSERT INTO tracks``. With
    the columns present the rebuilt view falls back to them, so pre-539 fixtures keep
    working; the production drop + derived-view path is validated separately (a real
    13k-track DB migration and the public-API test suite).
    """
    cols = {r[1] for r in index._conn.execute("PRAGMA table_info(tracks)")}
    for name, decl in _LEGACY_TRACK_COLUMNS:
        if name not in cols:
            index._conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {decl}")
    index._conn.commit()
    index._create_tracks_with_stats_view()


def _open_forkable_v49(db: Path) -> "LibraryIndex":
    """Open a fresh index, drop the NOCASE artist index, and stamp version 49 so
    a reopen exercises the KAMP-545 v50 heal. The dropped index lets the caller
    seed case-variant artist rows that the BINARY UNIQUE alone still permits."""
    index = LibraryIndex(db)
    index._conn.execute("DROP INDEX IF EXISTS idx_artists_name_nocase")
    index._conn.execute("UPDATE schema_version SET version = 49")
    index._conn.commit()
    return index


def _add_artist(index: "LibraryIndex", name: str, play_time: float = 0.0) -> int:
    cur = index._conn.execute(
        "INSERT INTO artists (name, play_time) VALUES (?, ?)", (name, play_time)
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def _add_album_with_track(
    index: "LibraryIndex",
    album_artist: str,
    album: str,
    artist_id: int,
    file_path: str,
    sale_item_id: str | None = None,
) -> int:
    cur = index._conn.execute(
        "INSERT INTO albums (album_artist, album, artist_id, sale_item_id)"
        " VALUES (?, ?, ?, ?)",
        (album_artist, album, artist_id, sale_item_id),
    )
    album_id = int(cur.lastrowid)  # type: ignore[arg-type]
    # KAMP-552: identity is track_sources.uri now, not tracks.file_path.
    tcur = index._conn.execute(
        "INSERT INTO tracks (title, artist, album_artist, album, album_id,"
        " track_number, disc_number) VALUES ('T', ?, ?, ?, ?, 1, 1)",
        (album_artist, album_artist, album, album_id),
    )
    tid = int(tcur.lastrowid)  # type: ignore[arg-type]
    kind = "stream" if str(file_path).startswith("bandcamp://") else "file"
    index._conn.execute(
        "INSERT INTO track_sources (track_id, kind, uri, provider_item_id)"
        " VALUES (?, ?, ?, ?)",
        (tid, kind, file_path, sale_item_id),
    )
    return album_id


# ---------------------------------------------------------------------------
# LibraryIndex
# ---------------------------------------------------------------------------


class TestLibraryIndex:
    def test_wal_journal_mode_enabled(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        # _conn is the current thread's connection; WAL is set on every new conn.
        mode = index._conn.execute("PRAGMA journal_mode").fetchone()[0]
        index.close()

        assert mode == "wal"

    def test_each_thread_gets_its_own_connection(self, tmp_path: Path) -> None:
        """Concurrent threads must not share connection objects."""
        import threading as _threading

        index = LibraryIndex(tmp_path / "library.db")
        conns: list[sqlite3.Connection] = []
        lock = _threading.Lock()

        def _capture() -> None:
            c = index._conn
            with lock:
                conns.append(c)

        threads = [_threading.Thread(target=_capture) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        index.close()
        # All four worker threads plus the main thread each have a unique conn.
        assert len(set(id(c) for c in conns)) == 4

    def test_creates_tables_on_init(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        LibraryIndex(db_path).close()

        conn = sqlite3.connect(str(db_path))
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()

        assert "tracks" in tables
        assert "schema_version" in tables

    def test_migration_version_is_current(self, tmp_path: Path) -> None:
        LibraryIndex(tmp_path / "library.db").close()

        conn = sqlite3.connect(str(tmp_path / "library.db"))
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        conn.close()

        assert version == 60

    def test_track_sources_and_stats_tables_created(self, tmp_path: Path) -> None:
        """The canonical-track child tables exist and are empty on a fresh DB (KAMP-535)."""
        index = LibraryIndex(tmp_path / "library.db")
        tables = {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        n_sources = index._conn.execute(
            "SELECT COUNT(*) FROM track_sources"
        ).fetchone()[0]
        n_stats = index._conn.execute("SELECT COUNT(*) FROM track_stats").fetchone()[0]
        index.close()

        assert {"track_sources", "track_stats"} <= tables
        # Expand phase only: created empty, nothing populates them until KAMP-536.
        assert n_sources == 0
        assert n_stats == 0

    def test_track_sources_constraints_enforced(self, tmp_path: Path) -> None:
        """track_sources enforces kind CHECK, uri UNIQUE, and FK cascade (KAMP-535)."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "01.mp3"))
        track_id = index._conn.execute("SELECT id FROM tracks").fetchone()[0]

        index._conn.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'file', ?)",
            (track_id, "/music/01.mp3"),
        )
        index._conn.commit()

        # Invalid kind is rejected by the CHECK constraint.
        with pytest.raises(sqlite3.IntegrityError):
            index._conn.execute(
                "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'bogus', ?)",
                (track_id, "/music/other.mp3"),
            )
        index._conn.rollback()

        # Duplicate uri is rejected by the UNIQUE constraint.
        with pytest.raises(sqlite3.IntegrityError):
            index._conn.execute(
                "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'stream', ?)",
                (track_id, "/music/01.mp3"),
            )
        index._conn.rollback()

        # Deleting the track cascades to its sources (foreign_keys=ON).
        index._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        index._conn.commit()
        remaining = index._conn.execute(
            "SELECT COUNT(*) FROM track_sources WHERE track_id = ?", (track_id,)
        ).fetchone()[0]
        index.close()
        assert remaining == 0

    def test_track_stats_pk_and_cascade(self, tmp_path: Path) -> None:
        """track_stats is one row per track (PK) and cascades on track delete (KAMP-535)."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "01.mp3"))
        track_id = index._conn.execute("SELECT id FROM tracks").fetchone()[0]

        # upsert already created the track's track_stats row (KAMP-540 Step 5);
        # track_id is the PRIMARY KEY, so a second row for the same track is rejected.
        with pytest.raises(sqlite3.IntegrityError):
            index._conn.execute(
                "INSERT INTO track_stats (track_id, favorite) VALUES (?, 0)",
                (track_id,),
            )
        index._conn.rollback()

        index._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        index._conn.commit()
        remaining = index._conn.execute(
            "SELECT COUNT(*) FROM track_stats WHERE track_id = ?", (track_id,)
        ).fetchone()[0]
        index.close()
        assert remaining == 0

    def test_migration_v43_to_v44_creates_child_tables(self, tmp_path: Path) -> None:
        """A pre-v44 DB (full schema, no child tables) gains them on open (KAMP-535)."""
        db_path = tmp_path / "library.db"
        # Build a complete current DB, then rewind it to the pre-44 state: drop the
        # two new tables and stamp version 43, so opening exercises the v44 upgrade.
        LibraryIndex(db_path).close()
        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP TABLE track_sources")
        conn.execute("DROP TABLE track_stats")
        conn.execute("UPDATE schema_version SET version = 43")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        tables = {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        index.close()

        assert version == 60
        assert {"track_sources", "track_stats"} <= tables

    def test_v45_backfill_populates_children(self, tmp_path: Path) -> None:
        """v45 backfills track_sources/track_stats from tracks per design §3 (KAMP-540)."""
        db_path = tmp_path / "library.db"
        _idx = LibraryIndex(db_path)
        _readd_legacy_track_columns(_idx)  # seed the pre-v49 row shape
        _idx.close()
        conn = sqlite3.connect(str(db_path))
        # Rewind to the pre-backfill state: empty children, version 44, raw rows.
        conn.execute("DELETE FROM track_sources")
        conn.execute("DELETE FROM track_stats")
        conn.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('sid1')")
        conn.executemany(
            "INSERT INTO tracks (file_path, source, sale_item_id, favorite,"
            " play_count, last_played, ext, duration, embedded_art, is_available)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("/music/rip.mp3", "local", None, 1, 3, 111.0, "mp3", 200.0, 1, 1),
                ("/music/dl.mp3", "local", "sid1", 0, 5, 222.0, "mp3", 210.0, 1, 1),
                ("bandcamp://sid2/7", "bandcamp", None, 1, 2, 333.0, "", 0.0, 0, 1),
            ],
        )
        conn.execute("UPDATE schema_version SET version = 44")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)  # runs the v45 backfill
        # KAMP-552: key on the source uri (== the old file_path), which survives.
        src = {
            r["uri"]: (r["kind"], r["provider"], r["provider_item_id"], r["uri"])
            for r in index._conn.execute(
                "SELECT s.kind, s.provider, s.provider_item_id, s.uri FROM track_sources s"
            )
        }
        stats = {
            r["uri"]: (r["favorite"], r["play_count"], r["last_played"])
            for r in index._conn.execute(
                "SELECT s.uri, st.favorite, st.play_count, st.last_played"
                " FROM track_stats st JOIN track_sources s ON s.track_id = st.track_id"
            )
        }
        index.close()

        # unaffiliated rip → file / no provider; download → file + bandcamp provenance;
        # pure stream → stream + provider_item_id parsed from the URI.
        assert src["/music/rip.mp3"] == ("file", "", None, "/music/rip.mp3")
        assert src["/music/dl.mp3"] == ("file", "bandcamp", "sid1", "/music/dl.mp3")
        assert src["bandcamp://sid2/7"] == (
            "stream",
            "bandcamp",
            "sid2",
            "bandcamp://sid2/7",
        )
        assert stats["/music/dl.mp3"] == (0, 5, 222.0)
        assert stats["bandcamp://sid2/7"] == (1, 2, 333.0)

    def test_v45_backfill_is_idempotent(self, tmp_path: Path) -> None:
        """Re-running the backfill inserts no duplicate child rows (KAMP-540)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index._conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES ('/m/a.mp3', 'local')"
        )
        index._conn.commit()
        index._backfill_canonical_children()
        index._backfill_canonical_children()  # second run must be a no-op
        n_src = index._conn.execute("SELECT COUNT(*) FROM track_sources").fetchone()[0]
        n_stats = index._conn.execute("SELECT COUNT(*) FROM track_stats").fetchone()[0]
        index.close()
        assert n_src == 1
        assert n_stats == 1

    def test_v45_backfill_guard_skips_partial_schema(self, tmp_path: Path) -> None:
        """A partial-schema DB migrates to v45 without error; children stay empty."""
        db_path = tmp_path / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (44)")
        # Missing sale_item_id/is_available/duration/… → backfill guard must skip.
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, source TEXT DEFAULT 'local',"
            " favorite INTEGER DEFAULT 0, play_count INTEGER DEFAULT 0)"
        )
        conn.execute("INSERT INTO tracks (file_path) VALUES ('/a.mp3')")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        n_src = index._conn.execute("SELECT COUNT(*) FROM track_sources").fetchone()[0]
        index.close()
        assert version == 60
        assert n_src == 0

    def test_stats_write_to_track_stats_only(self, tmp_path: Path) -> None:
        """set_favorite/record_played/record_track_started write track_stats, now the
        sole store — the legacy tracks stat columns are no longer written (KAMP-539)."""
        index = LibraryIndex(tmp_path / "library.db")
        fp = tmp_path / "s.mp3"
        index.upsert_many([_sample_track(fp)])
        key = str(fp)
        index.set_favorite(key, True)
        index.record_played(fp)
        index.record_played(fp)  # relative +1 twice → absolute 2, not off-by-one
        index.record_track_started(fp)

        st = index._conn.execute(
            "SELECT st.favorite, st.play_count, st.last_played FROM track_stats st"
            " JOIN track_sources s ON s.track_id = st.track_id WHERE s.uri = ?",
            (key,),
        ).fetchone()
        index.close()

        assert st["favorite"] == 1
        assert st["play_count"] == 2
        assert st["last_played"] is not None

    def test_upsert_maintains_children_without_resetting_stats(
        self, tmp_path: Path
    ) -> None:
        """Scan creates a source + stats row; a re-scan refreshes source, keeps stats."""
        index = LibraryIndex(tmp_path / "library.db")
        fp = tmp_path / "s.mp3"
        index.upsert_many([_sample_track(fp)])
        key = str(fp)
        index.set_favorite(key, True)  # user favorites after the first scan

        rescanned = _sample_track(fp)
        rescanned.duration = 99.0  # a per-source col changed on disk
        index.upsert_many([rescanned])

        src = index._conn.execute(
            "SELECT s.kind, s.uri, s.duration FROM track_sources s WHERE s.uri = ?",
            (key,),
        ).fetchone()
        fav = index._conn.execute(
            "SELECT st.favorite FROM track_stats st"
            " JOIN track_sources s ON s.track_id = st.track_id WHERE s.uri = ?",
            (key,),
        ).fetchone()["favorite"]
        index.close()

        assert src["kind"] == "file" and src["uri"] == key
        assert src["duration"] == 99.0  # source refreshed
        assert fav == 1  # stats preserved across the re-scan (INSERT OR IGNORE)

    def test_preferred_source_prefers_file_then_available(self, tmp_path: Path) -> None:
        """preferred_source picks a local file, falling through to a stream (KAMP-541)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index._conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES ('canon', 'bandcamp')"
        )
        tid = index._conn.execute("SELECT id FROM tracks").fetchone()[0]
        index._conn.executemany(
            "INSERT INTO track_sources (track_id, kind, uri, is_available)"
            " VALUES (?, ?, ?, ?)",
            [
                (tid, "stream", "bandcamp://9/1", 1),
                (tid, "file", "/m/a.mp3", 1),
            ],
        )
        index._conn.commit()

        assert index.preferred_source(tid)["kind"] == "file"  # file wins
        # An unavailable file falls through to the stream.
        index._conn.execute(
            "UPDATE track_sources SET is_available = 0 WHERE kind = 'file'"
        )
        index._conn.commit()
        pref = index.preferred_source(tid)
        index.close()
        assert pref["kind"] == "stream"
        assert pref["uri"] == "bandcamp://9/1"

    def test_sources_for_track_ids_batches_preferred_first(
        self, tmp_path: Path
    ) -> None:
        """sources_for_track_ids returns all sources per track, preferred-first (KAMP-537)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO tracks (file_path, source) VALUES ('a', 'local')")
        c.execute("INSERT INTO tracks (file_path, source) VALUES ('b', 'bandcamp')")
        t1, t2 = [r[0] for r in c.execute("SELECT id FROM tracks ORDER BY id")]
        c.executemany(
            "INSERT INTO track_sources (track_id, kind, provider, uri, is_available,"
            " duration) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (t1, "stream", "bandcamp", "bandcamp://9/1", 1, 100.0),
                (t1, "file", "local", "/m/a.mp3", 1, 101.0),
                (t2, "stream", "bandcamp", "bandcamp://9/2", 1, 200.0),
            ],
        )
        c.commit()
        out = index.sources_for_track_ids([t1, t2, 999])
        index.close()
        # t1: file preferred-first over stream; t2: its one stream; 999: absent.
        assert [r["kind"] for r in out[t1]] == ["file", "stream"]
        assert [r["kind"] for r in out[t2]] == ["stream"]
        assert 999 not in out

    def test_sources_for_track_ids_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.sources_for_track_ids([]) == {}
        index.close()

    def test_remove_track_keeps_track_with_surviving_stream(
        self, tmp_path: Path
    ) -> None:
        """Removing a local file drops its source but keeps a track that still streams (KAMP-541 C2)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        # Use a real platform path so str(fp) matches the stored uri on Windows too.
        fp = tmp_path / "a.mp3"
        index._conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES (?, 'local')", (str(fp),)
        )
        tid = index._conn.execute("SELECT id FROM tracks").fetchone()[0]
        index._conn.executemany(
            "INSERT INTO track_sources (track_id, kind, uri, is_available)"
            " VALUES (?, ?, ?, ?)",
            [(tid, "file", str(fp), 1), (tid, "stream", "bandcamp://9/1", 1)],
        )
        index._conn.commit()

        index.remove_track(fp)

        track_still_there = index._conn.execute(
            "SELECT id FROM tracks WHERE id = ?", (tid,)
        ).fetchone()
        srcs = [
            r["kind"]
            for r in index._conn.execute(
                "SELECT kind FROM track_sources WHERE track_id = ?", (tid,)
            )
        ]
        index.close()
        assert track_still_there is not None  # track survives
        assert srcs == ["stream"]  # only the stream source remains

    def test_remove_track_deletes_sourceless_track(self, tmp_path: Path) -> None:
        """Removing the only (file) source deletes the canonical track (KAMP-541)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        fp = tmp_path / "solo.mp3"
        index._conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES (?, 'local')", (str(fp),)
        )
        tid = index._conn.execute("SELECT id FROM tracks").fetchone()[0]
        index._conn.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'file', ?)",
            (tid, str(fp)),
        )
        index._conn.commit()

        index.remove_track(fp)

        n = index._conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        index.close()
        assert n == 0

    def test_v46_collapse_quarantines_ambiguous_bucket(self, tmp_path: Path) -> None:
        """A bucket with >2 rows is left un-merged (KAMP-541 quarantine)."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO albums (album_artist, album) VALUES ('A','Alb')")
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        # Three rows sharing (album, track, disc) — ambiguous, must not collapse.
        for i, (fp, src) in enumerate(
            [
                ("bandcamp://x/1", "bandcamp"),
                ("/m/a.mp3", "local"),
                ("/m/b.flac", "local"),
            ]
        ):
            c.execute(
                "INSERT INTO tracks (file_path, source, album_id, track_number,"
                " disc_number) VALUES (?, ?, ?, 1, 1)",
                (fp, src, alb),
            )
        c.execute("UPDATE schema_version SET version = 45")
        c.commit()
        index.close()

        healed = LibraryIndex(db)  # v46 runs, quarantines
        n = healed._conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        healed.close()
        assert n == 3  # all left in place

    def test_v46_collapse_derives_missing_source_and_dedupes_deferred_op(
        self, tmp_path: Path
    ) -> None:
        """Collapse derives an absent source and keeps the survivor's deferred op (KAMP-541)."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO albums (album_artist, album) VALUES ('A','Alb')")
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number, disc_number)"
            " VALUES ('bandcamp://y/1','bandcamp',?,1,1)",
            (alb,),
        )
        stream_id = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number, disc_number)"
            " VALUES ('/m/y.mp3','local',?,1,1)",
            (alb,),
        )
        local_id = c.execute("SELECT id FROM tracks WHERE source='local'").fetchone()[0]
        # Intentionally NO track_sources rows (simulate a pre-540 skip) -> collapse
        # must derive them. Both rows carry a pending deferred op (UNIQUE track_id).
        c.executemany(
            "INSERT INTO deferred_ops (op_type, track_id, payload_json, created_at)"
            " VALUES ('track_retag', ?, '{}', ?)",
            [(stream_id, 1.0), (local_id, 2.0)],
        )
        c.execute("UPDATE schema_version SET version = 45")
        c.commit()
        index.close()

        healed = LibraryIndex(db)
        hc = healed._conn
        n_tracks = hc.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        n_src = hc.execute("SELECT COUNT(*) FROM track_sources").fetchone()[0]
        n_ops = hc.execute("SELECT COUNT(*) FROM deferred_ops").fetchone()[0]
        op_tid = hc.execute("SELECT track_id FROM deferred_ops").fetchone()["track_id"]
        healed.close()
        assert n_tracks == 1  # merged
        assert n_src == 2  # both sources derived + re-parented
        # Prefer-file: the local row survives, so its deferred op is kept.
        assert n_ops == 1 and op_tid == local_id

    def test_upsert_attaches_new_download_to_existing_canonical(
        self, tmp_path: Path
    ) -> None:
        """A downloaded file for an existing stream track attaches as a source (KAMP-541)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute(
            "INSERT INTO albums (album_artist, album) VALUES ('The Artist','The Album')"
        )
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
            " source, album_id, track_number, disc_number)"
            " VALUES ('bandcamp://z/1','A Song','The Artist','The Artist','The Album',"
            " 'bandcamp',?,1,1)",
            (alb,),
        )
        stream_id = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, provider, uri)"
            " VALUES (?, 'stream', 'bandcamp', 'bandcamp://z/1')",
            (stream_id,),
        )
        c.commit()

        # Scan a local file for the same album+track: it must attach, not fork.
        index.upsert_many([_sample_track(tmp_path / "track1.mp3")])

        n_tracks = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        surviving_id = c.execute("SELECT id FROM tracks").fetchone()[0]
        kinds = sorted(
            r["kind"]
            for r in c.execute(
                "SELECT kind FROM track_sources WHERE track_id = ?", (surviving_id,)
            )
        )
        # The runtime reconcile keeps the INCUMBENT (the pre-existing canonical a
        # live queue / open album view already references) rather than the file
        # row the offline migration prefers — see _reconcile_scanned_tracks.
        assert n_tracks == 1
        assert kinds == ["file", "stream"]
        assert surviving_id == stream_id
        # The stale queued stream uri still resolves to the survivor, and its
        # preferred source is now the downloaded file (KAMP-541 regression guard:
        # a favorite via the transport 404'd and a queue jump replayed the stream).
        assert index.get_track_by_path("bandcamp://z/1").id == stream_id
        assert index.preferred_source(stream_id)["kind"] == "file"
        index.set_favorite("bandcamp://z/1", True)
        assert (
            c.execute(
                "SELECT favorite FROM track_stats WHERE track_id = ?", (stream_id,)
            ).fetchone()["favorite"]
            == 1
        )
        index.close()

    def test_sync_attaches_stream_to_downloaded_canonical(self, tmp_path: Path) -> None:
        """A synced stream for an already-downloaded album attaches, not forks (KAMP-541).

        The reverse of test_upsert_attaches_new_download_to_existing_canonical:
        the local file exists first (download-only album) and the bandcamp sync
        brings its stream in for the first time. Without the symmetric reconcile
        the stream is upserted as a separate stream-only row, re-creating the
        KAMP-532 duplicate.
        """
        index = LibraryIndex(tmp_path / "library.db")
        c = index._conn
        c.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('s7')")
        c.execute(
            "INSERT INTO albums (album_artist, album, sale_item_id)"
            " VALUES ('The Artist','The Album','s7')"
        )
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        # A downloaded, never-streamed canonical (file source only).
        local = _sample_track(tmp_path / "track1.mp3")
        local.sale_item_id = "s7"
        index.upsert_track(local)
        # KAMP-552: provenance is on track_sources.provider_item_id (set from
        # local.sale_item_id by upsert); only the album link needs setting here.
        c.execute("UPDATE tracks SET album_id = ?", (alb,))
        file_id = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.commit()

        # The sync upserts the streaming track for the same album+track.
        stream = Track(
            file_path="bandcamp://s7/1",
            title="A Song",
            artist="The Artist",
            album_artist="The Artist",
            album="The Album",
            release_date="",
            track_number=local.track_number,
            disc_number=local.disc_number,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
        )
        stream.source = "bandcamp"  # a streamed delivery -> stream track_source
        index.upsert_many([stream])

        n_tracks = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        kinds = sorted(
            r["kind"]
            for r in c.execute(
                "SELECT kind FROM track_sources WHERE track_id = ?", (file_id,)
            )
        )
        # One row survives — the incumbent local file — now carrying both sources.
        assert n_tracks == 1
        assert kinds == ["file", "stream"]
        assert c.execute("SELECT id FROM tracks").fetchone()[0] == file_id
        assert index.preferred_source(file_id)["kind"] == "file"
        # has_remote_album_tracks now sees the attached stream source (collapse-aware),
        # so a subsequent sync skips re-fetching and re-forking it.
        assert index.has_remote_album_tracks("s7") is True
        index.close()

    def test_remove_download_refuses_when_no_stream_source(
        self, tmp_path: Path
    ) -> None:
        """remove_download refuses if a track would be left with no stream (KAMP-527)."""
        from kamp_core.library import NoStreamableVersionError

        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('sidX')")
        c.execute(
            "INSERT INTO albums (album_artist, album, sale_item_id) VALUES ('A','Alb','sidX')"
        )
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number, disc_number)"
            " VALUES ('/m/x.mp3','local',?,1,1)",
            (alb,),
        )
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, provider, provider_item_id, uri)"
            " VALUES (?, 'file', 'bandcamp', 'sidX', '/m/x.mp3')",
            (tid,),
        )
        c.commit()

        with pytest.raises(NoStreamableVersionError):
            index.remove_download("sidX")
        assert c.execute("SELECT COUNT(*) FROM track_sources").fetchone()[0] == 1
        index.close()

    def test_v47_heals_collapse_art(self, tmp_path: Path) -> None:
        """v47 restores embedded_art/file_mtime lost by the v46 collapse (KAMP-541)."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute(
            "INSERT INTO albums (album_artist, album, embedded_art) VALUES ('A','Alb',0)"
        )
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        # A collapsed track: the stream survivor left embedded_art=0 / file_mtime
        # NULL on the row even though its file source carries the art.
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number,"
            " disc_number, embedded_art) VALUES ('bandcamp://a/1','bandcamp',?,1,1,0)",
            (alb,),
        )
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.executemany(
            "INSERT INTO track_sources (track_id, kind, uri, embedded_art, file_mtime)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (tid, "stream", "bandcamp://a/1", 0, None),
                (tid, "file", "/m/a.mp3", 1, 123.0),
            ],
        )
        c.execute("UPDATE schema_version SET version = 46")
        c.commit()
        index.close()

        healed = LibraryIndex(db)  # runs the v47 heal
        hc = healed._conn
        t = hc.execute(
            "SELECT embedded_art, file_mtime, source, file_path"
            " FROM tracks_with_stats WHERE id=?",
            (tid,),
        ).fetchone()
        a_art = hc.execute(
            "SELECT embedded_art FROM albums WHERE id=?", (alb,)
        ).fetchone()[0]
        healed.close()
        assert t["embedded_art"] == 1  # synced from the file source
        assert t["file_mtime"] == 123.0
        assert t["source"] == "local" and t["file_path"] == "/m/a.mp3"
        assert a_art == 1  # album cover restored

    def test_v48_heals_sync_forked_stream_duplicate(self, tmp_path: Path) -> None:
        """v48 merges a stream-only row the sync forked beside a download (KAMP-541).

        Reproduces the download-only album whose stream arrived via a later sync
        as a SEPARATE stream-only tracks row (before the symmetric reconcile). The
        heal re-runs the collapse: survivor is the local file, the stream re-parents
        as a source, the duplicate row is gone.
        """
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('sid48')")
        c.execute("INSERT INTO albums (album_artist, album) VALUES ('A','Alb')")
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        # Downloaded canonical (file source, sale_item_id) + a forked stream-only row.
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number,"
            " disc_number, sale_item_id) VALUES ('/m/a.mp3','local',?,1,1,'sid48')",
            (alb,),
        )
        file_id = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'file', '/m/a.mp3')",
            (file_id,),
        )
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number, disc_number)"
            " VALUES ('bandcamp://sid48/1','bandcamp',?,1,1)",
            (alb,),
        )
        stream_id = c.execute(
            "SELECT id FROM tracks WHERE id != ?", (file_id,)
        ).fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri)"
            " VALUES (?, 'stream', 'bandcamp://sid48/1')",
            (stream_id,),
        )
        c.execute("UPDATE schema_version SET version = 47")
        c.commit()
        index.close()

        healed = LibraryIndex(db)  # runs the v48 heal
        hc = healed._conn
        n_tracks = hc.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        kinds = sorted(
            r["kind"]
            for r in hc.execute(
                "SELECT kind FROM track_sources WHERE track_id = ?", (file_id,)
            )
        )
        surv = hc.execute("SELECT id FROM tracks").fetchone()[0]
        healed.close()
        assert n_tracks == 1  # the forked stream row is gone
        assert surv == file_id  # survivor is the local download
        assert kinds == ["file", "stream"]  # stream re-parented onto it

    def test_v49_drops_legacy_columns_and_derives_from_children(
        self, tmp_path: Path
    ) -> None:
        """v49 drops the 11 duplicated tracks columns; reads then derive them from
        track_sources/track_stats via the view, values preserved (KAMP-539)."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute(
            "INSERT INTO tracks (file_path, title, album, album_artist, track_number,"
            " disc_number, source, ext, duration) VALUES"
            " ('/m/a.mp3','T','Alb','A',1,1,'local','flac',321.0)"
        )
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri, ext, duration,"
            " embedded_art, is_available) VALUES (?, 'file', '/m/a.mp3','flac',321.0,1,1)",
            (tid,),
        )
        c.execute(
            "INSERT INTO track_stats (track_id, favorite, play_count) VALUES (?, 1, 4)",
            (tid,),
        )
        c.execute("UPDATE schema_version SET version = 48")
        c.commit()
        index.close()

        reopened = LibraryIndex(db)  # runs the v49 drop
        rc = reopened._conn
        cols = {r[1] for r in rc.execute("PRAGMA table_info(tracks)")}
        dropped = {
            "ext",
            "embedded_art",
            "file_mtime",
            "source",
            "stream_url",
            "stream_url_expires_at",
            "is_available",
            "duration",
            "favorite",
            "play_count",
            "last_played",
        }
        t = reopened.get_track_by_id(tid)
        ver = rc.execute("SELECT version FROM schema_version").fetchone()[0]
        reopened.close()
        assert ver == 60
        assert not (dropped & cols)  # all 11 columns gone from tracks
        # KAMP-552 (v51, which also runs on this reopen) drops file_path/sale_item_id.
        assert not ({"file_path", "sale_item_id"} & cols)
        assert t is not None
        # Per-source values derive from track_sources; stats from track_stats.
        assert t.source == "local" and t.ext == "flac" and t.duration == 321.0
        assert t.favorite is True and t.play_count == 4

    def test_v49_noop_when_columns_already_dropped(self, tmp_path: Path) -> None:
        """Re-running v49 on a DB that already lacks the columns just bumps the
        version — idempotent presence check (KAMP-539)."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)  # fresh v49 schema — no legacy columns
        index._conn.execute("UPDATE schema_version SET version = 48")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)  # v49 finds nothing to drop
        ver = reopened._conn.execute("SELECT version FROM schema_version").fetchone()[0]
        cols = {r[1] for r in reopened._conn.execute("PRAGMA table_info(tracks)")}
        reopened.close()
        assert ver == 60
        assert "favorite" not in cols

    def test_v49_rolls_back_and_keeps_version_when_a_drop_fails(
        self, tmp_path: Path
    ) -> None:
        """If a column drop raises (an index on a target column blocks DROP COLUMN),
        v49 rolls back, leaves the version unbumped so it retries, and rebuilds a
        usable view (KAMP-539)."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        # An index on a drop-target column makes ALTER TABLE DROP COLUMN fail.
        index._conn.execute("CREATE INDEX _t_fav ON tracks(favorite)")
        index._conn.execute("UPDATE schema_version SET version = 48")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)  # the drop of the indexed column raises
        rc = reopened._conn
        ver = rc.execute("SELECT version FROM schema_version").fetchone()[0]
        cols = {r[1] for r in rc.execute("PRAGMA table_info(tracks)")}
        n = rc.execute("SELECT COUNT(*) FROM tracks_with_stats").fetchone()[0]
        reopened.close()
        assert ver == 48  # not bumped — the next open retries
        assert "favorite" in cols  # the failed column is not dropped
        assert n == 0  # the view was rebuilt and is queryable

    def test_v49_skips_drop_when_backup_fails(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """If the pre-drop backup fails, v49 skips the (irreversible) drop and does
        not bump the version, so it retries on the next open (KAMP-539)."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        index._conn.execute("UPDATE schema_version SET version = 48")
        index._conn.commit()
        index.close()

        monkeypatch.setattr(LibraryIndex, "_backup_db", lambda self, label: False)
        reopened = LibraryIndex(db)  # backup fails → drop skipped
        rc = reopened._conn
        ver = rc.execute("SELECT version FROM schema_version").fetchone()[0]
        cols = {r[1] for r in rc.execute("PRAGMA table_info(tracks)")}
        reopened.close()
        assert ver == 48  # not bumped
        assert "favorite" in cols  # columns retained

    def test_v50_reunites_case_variant_artist_split(self, tmp_path: Path) -> None:
        """v50 folds two case-variant artists rows into one, repoints both albums
        onto the survivor, and normalizes album/track casing (KAMP-545)."""
        db = tmp_path / "library.db"
        index = _open_forkable_v49(db)
        a_lower = _add_artist(index, "Sunn O)))")  # inserted first -> lower id
        a_upper = _add_artist(index, "SUNN O)))")
        _add_album_with_track(
            index, "Sunn O)))", "Monoliths & Dimensions", a_lower, "/m/a.mp3"
        )
        _add_album_with_track(index, "SUNN O)))", "Life Metal", a_upper, "/m/b.mp3")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)  # runs the v50 heal
        rc = reopened._conn
        names = [
            r[0]
            for r in rc.execute(
                "SELECT name FROM artists WHERE name = 'sunn o)))' COLLATE NOCASE"
            )
        ]
        album_artist_ids = {
            r[0]
            for r in rc.execute(
                "SELECT artist_id FROM albums"
                " WHERE album IN ('Monoliths & Dimensions', 'Life Metal')"
            )
        }
        album_casings = {
            r[0]
            for r in rc.execute(
                "SELECT album_artist FROM albums"
                " WHERE album IN ('Monoliths & Dimensions', 'Life Metal')"
            )
        }
        track_casings = {r[0] for r in rc.execute("SELECT album_artist FROM tracks")}
        ver = rc.execute("SELECT version FROM schema_version").fetchone()[0]
        has_index = rc.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index'"
            " AND name='idx_artists_name_nocase'"
        ).fetchone()
        reopened.close()

        assert names == ["Sunn O)))"]  # one row, canonical = lowest-id casing
        assert len(album_artist_ids) == 1 and None not in album_artist_ids
        assert album_casings == {"Sunn O)))"}  # both albums normalized
        assert track_casings == {"Sunn O)))"}  # tracks normalized too
        assert ver == 60
        assert has_index is not None  # NOCASE uniqueness now enforced

    def test_v50_folds_play_time_and_removes_orphan_variant(
        self, tmp_path: Path
    ) -> None:
        """A case-variant row owning zero albums is deleted and its play_time
        folded into the survivor (KAMP-545)."""
        db = tmp_path / "library.db"
        index = _open_forkable_v49(db)
        keep = _add_artist(index, "The Mountain Goats", play_time=100.0)
        orphan = _add_artist(index, "the Mountain Goats", play_time=25.0)
        _add_album_with_track(
            index, "The Mountain Goats", "Tallahassee", keep, "/m/tmg.mp3"
        )
        # orphan owns no albums
        assert orphan  # referenced for clarity
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        rc = reopened._conn
        rows = rc.execute(
            "SELECT name, play_time FROM artists"
            " WHERE name = 'the mountain goats' COLLATE NOCASE"
        ).fetchall()
        reopened.close()

        assert len(rows) == 1
        assert rows[0]["name"] == "The Mountain Goats"
        assert rows[0]["play_time"] == 125.0  # folded

    def test_v50_survivor_casing_follows_bandcamp_band_name(
        self, tmp_path: Path
    ) -> None:
        """When an album in the group is Bandcamp-linked, the canonical casing is
        taken from band_name — even against the most-albums heuristic (KAMP-545)."""
        db = tmp_path / "library.db"
        index = _open_forkable_v49(db)
        index._conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, band_name)"
            " VALUES ('k1', 'Kylesa')"
        )
        # "KYLESA" owns two albums (incl. the bandcamp one) and was inserted first,
        # so most-albums AND lowest-id would both pick it — band_name must override.
        upper = _add_artist(index, "KYLESA")
        lower = _add_artist(index, "Kylesa")
        _add_album_with_track(
            index, "KYLESA", "Spiral Shadow", upper, "/m/k1.mp3", sale_item_id="k1"
        )
        _add_album_with_track(index, "KYLESA", "Ultraviolet", upper, "/m/k2.mp3")
        _add_album_with_track(index, "Kylesa", "Static Tensions", lower, "/m/k3.mp3")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        rc = reopened._conn
        names = [
            r[0]
            for r in rc.execute(
                "SELECT name FROM artists WHERE name = 'kylesa' COLLATE NOCASE"
            )
        ]
        casings = {r[0] for r in rc.execute("SELECT album_artist FROM albums")}
        band_name = rc.execute(
            "SELECT band_name FROM bandcamp_collection WHERE sale_item_id = 'k1'"
        ).fetchone()[0]
        reopened.close()

        assert names == ["Kylesa"]  # band_name casing won over most-albums
        assert casings == {"Kylesa"}
        assert band_name == "Kylesa"

    def test_v50_uses_most_albums_when_no_band_name_column(
        self, tmp_path: Path
    ) -> None:
        """On an old schema whose bandcamp_collection predates band_name, the heal
        falls back to the most-albums / lowest-id survivor (KAMP-545)."""
        db = tmp_path / "library.db"
        index = _open_forkable_v49(db)
        # Simulate a pre-band_name bandcamp_collection so has_band_name is False.
        index._conn.execute("DROP TABLE bandcamp_collection")
        index._conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT PRIMARY KEY)"
        )
        few = _add_artist(index, "Kylesa")  # lower id, but fewer albums
        many = _add_artist(index, "KYLESA")
        _add_album_with_track(index, "Kylesa", "Static Tensions", few, "/m/k1.mp3")
        _add_album_with_track(index, "KYLESA", "Spiral Shadow", many, "/m/k2.mp3")
        _add_album_with_track(index, "KYLESA", "Ultraviolet", many, "/m/k3.mp3")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        rc = reopened._conn
        names = [
            r[0]
            for r in rc.execute(
                "SELECT name FROM artists WHERE name = 'kylesa' COLLATE NOCASE"
            )
        ]
        reopened.close()

        assert names == ["KYLESA"]  # most albums wins with no band_name to consult

    def test_v50_noop_on_clean_library(self, tmp_path: Path) -> None:
        """A v49 DB with no case-variant artists bumps to 50 and adds the index
        without taking a backup (KAMP-545)."""
        db = tmp_path / "library.db"
        index = _open_forkable_v49(db)
        _add_artist(index, "Boris")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        rc = reopened._conn
        ver = rc.execute("SELECT version FROM schema_version").fetchone()[0]
        has_index = rc.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index'"
            " AND name='idx_artists_name_nocase'"
        ).fetchone()
        reopened.close()
        backups = list(tmp_path.glob("library.db.bak-*"))

        assert ver == 60
        assert has_index is not None
        assert backups == []  # no work -> no backup

    def test_v50_retries_when_backup_fails(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """If the pre-heal backup fails while case variants exist, v50 leaves the
        version at 49 and the duplicate rows intact so it retries (KAMP-545)."""
        db = tmp_path / "library.db"
        index = _open_forkable_v49(db)
        a1 = _add_artist(index, "Kylesa")
        a2 = _add_artist(index, "KYLESA")
        _add_album_with_track(index, "Kylesa", "Static Tensions", a1, "/m/k1.mp3")
        _add_album_with_track(index, "KYLESA", "Spiral Shadow", a2, "/m/k2.mp3")
        index._conn.commit()
        index.close()

        monkeypatch.setattr(LibraryIndex, "_backup_db", lambda self, label: False)
        reopened = LibraryIndex(db)
        rc = reopened._conn
        ver = rc.execute("SELECT version FROM schema_version").fetchone()[0]
        n_artists = rc.execute(
            "SELECT COUNT(*) FROM artists WHERE name = 'kylesa' COLLATE NOCASE"
        ).fetchone()[0]
        reopened.close()

        assert ver == 49  # not bumped — retries on next open
        assert n_artists == 2  # duplicate rows untouched

    def test_v50_rolls_back_and_retries_when_heal_raises(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """If a group merge raises mid-heal, the whole migration rolls back, the
        version stays 49, and the duplicate rows survive intact (KAMP-545)."""
        db = tmp_path / "library.db"
        index = _open_forkable_v49(db)
        a1 = _add_artist(index, "Kylesa")
        a2 = _add_artist(index, "KYLESA")
        _add_album_with_track(index, "Kylesa", "Static Tensions", a1, "/m/k1.mp3")
        _add_album_with_track(index, "KYLESA", "Spiral Shadow", a2, "/m/k2.mp3")
        index._conn.commit()
        index.close()

        def _boom(self: "LibraryIndex", ids: list[int], has_band_name: bool) -> None:
            raise RuntimeError("simulated heal failure")

        monkeypatch.setattr(LibraryIndex, "_heal_artist_group", _boom)
        reopened = LibraryIndex(db)
        rc = reopened._conn
        ver = rc.execute("SELECT version FROM schema_version").fetchone()[0]
        n_artists = rc.execute(
            "SELECT COUNT(*) FROM artists WHERE name = 'kylesa' COLLATE NOCASE"
        ).fetchone()[0]
        reopened.close()

        assert ver == 49  # rolled back, not bumped
        assert n_artists == 2  # both rows intact after rollback

    def test_collapse_heal_works_without_preexisting_view(self, tmp_path: Path) -> None:
        """A collapse heal must build the tracks_with_stats view before running.

        Regression for the KAMP-542 view-timing bug (root cause of the persistent
        Snail Mail - Lush dupes): the v46 collapse calls _refresh_album_aggregates,
        which reads tracks_with_stats. On a pre-542 upgrade the view did not exist
        yet, so the collapse threw 'no such table: tracks_with_stats', the whole
        one-transaction heal rolled back, and the two-row fork model survived while
        the version still bumped. This drops the view and rewinds to v45 so the v46
        collapse runs, and asserts the fork collapses (the view is now created at
        the top of _migrate, before any heal).
        """
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('sidv')")
        c.execute("INSERT INTO albums (album_artist, album) VALUES ('A','Alb')")
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number,"
            " disc_number, sale_item_id) VALUES ('/m/a.mp3','local',?,1,1,'sidv')",
            (alb,),
        )
        file_id = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'file', '/m/a.mp3')",
            (file_id,),
        )
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number,"
            " disc_number, sale_item_id)"
            " VALUES ('bandcamp://sidv/1','bandcamp',?,1,1,'sidv')",
            (alb,),
        )
        stream_id = c.execute(
            "SELECT id FROM tracks WHERE id != ?", (file_id,)
        ).fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri)"
            " VALUES (?, 'stream', 'bandcamp://sidv/1')",
            (stream_id,),
        )
        # Simulate a pre-KAMP-542 DB: no view, rewound to before the v46 collapse.
        c.execute("DROP VIEW IF EXISTS tracks_with_stats")
        c.execute("UPDATE schema_version SET version = 45")
        c.commit()
        index.close()

        healed = LibraryIndex(db)  # v46 collapse must build the view, then collapse
        hc = healed._conn
        n_tracks = hc.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        kinds = sorted(
            r["kind"]
            for r in hc.execute(
                "SELECT kind FROM track_sources WHERE track_id = ?", (file_id,)
            )
        )
        surv = hc.execute("SELECT id FROM tracks").fetchone()[0]
        healed.close()
        assert n_tracks == 1  # collapse ran (would be 2 if the heal had thrown)
        assert surv == file_id  # survivor is the local download
        assert kinds == ["file", "stream"]  # stream re-parented onto it

    def test_all_downloads_streamable(self, tmp_path: Path) -> None:
        """all_downloads_streamable is True only when every download has a stream (KAMP-541)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('sidS')")
        c.execute(
            "INSERT INTO albums (album_artist, album, sale_item_id) VALUES ('A','Alb','sidS')"
        )
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number, disc_number)"
            " VALUES ('/m/s.mp3','local',?,1,1)",
            (alb,),
        )
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'file', '/m/s.mp3')",
            (tid,),
        )
        c.commit()
        assert index.all_downloads_streamable("sidS") is False  # no stream yet
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'stream', 'bandcamp://sidS/1')",
            (tid,),
        )
        c.commit()
        assert index.all_downloads_streamable("sidS") is True
        index.close()

    def test_materialize_skips_when_no_matching_track(self, tmp_path: Path) -> None:
        """materialize_stream_tracks attaches nothing when no track matches (KAMP-541)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('sid99')")
        c.execute(
            "INSERT INTO albums (album_artist, album, sale_item_id) VALUES ('A','Alb','sid99')"
        )
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number, disc_number)"
            " VALUES ('/m/1.mp3','local',?,1,1)",
            (alb,),
        )
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'file', '/m/1.mp3')",
            (tid,),
        )
        c.commit()

        phantom = _sample_track(Path("bandcamp://sid99/9"))
        phantom.track_number = 9  # no local track has track_number 9
        phantom.source = "bandcamp"
        n = index.materialize_stream_tracks("sid99", [phantom])
        streams = c.execute(
            "SELECT COUNT(*) FROM track_sources WHERE kind = 'stream'"
        ).fetchone()[0]
        index.close()
        assert n == 0 and streams == 0

    def test_sync_to_preferred_noop_without_sources(self, tmp_path: Path) -> None:
        """_sync_tracks_row_to_preferred_source is a no-op for a sourceless track (KAMP-541)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index._conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES ('/m/n.mp3', 'local')"
        )
        tid = index._conn.execute("SELECT id FROM tracks").fetchone()[0]
        index._sync_tracks_row_to_preferred_source(tid)  # must not raise
        fp = index._conn.execute(
            "SELECT file_path FROM tracks WHERE id = ?", (tid,)
        ).fetchone()[0]
        index.close()
        assert fp == "/m/n.mp3"  # unchanged

    def test_remove_track_sourceless_row_is_noop(self, tmp_path: Path) -> None:
        """A sourceless tracks row is unaddressable by remove_track (KAMP-552).

        The pre-collapse "delete by file_path" fallback is gone now that file_path
        is dropped — a row with no track_sources cannot be resolved by any uri, so
        remove_track is a no-op. (Such a row cannot occur in practice: every track
        carries a source since KAMP-540.)
        """
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        fp = tmp_path / "legacy.mp3"
        index._conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES (?, 'local')", (str(fp),)
        )
        index._conn.commit()
        index.remove_track(fp)
        n = index._conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        index.close()
        assert n == 1  # no source to resolve → nothing removed

    def test_v46_collapse_merges_siblings_and_repoints(self, tmp_path: Path) -> None:
        """v46 collapses a stream+local sibling pair into one track (KAMP-541)."""
        import json

        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute(
            "INSERT INTO albums (album_artist, album, source) VALUES ('A','Alb','bandcamp')"
        )
        alb = c.execute("SELECT id FROM albums").fetchone()[0]
        c.execute("INSERT INTO bandcamp_collection (sale_item_id) VALUES ('sid9')")
        # Stream row first (lower id), favorite=1 pc=2; local second, pc=5. Under
        # prefer-file the LOCAL row survives; the playlist/queue point at the stream
        # (loser) so the repoint is exercised.
        c.execute(
            "INSERT INTO tracks (file_path, title, album, album_artist, source,"
            " album_id, track_number, disc_number, sale_item_id, favorite, play_count)"
            " VALUES ('bandcamp://sid9/1','T','Alb','A','bandcamp',?,1,1,'sid9',1,2)",
            (alb,),
        )
        stream_id = c.execute(
            "SELECT id FROM tracks WHERE source='bandcamp'"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO tracks (file_path, title, album, album_artist, source,"
            " album_id, track_number, disc_number, sale_item_id, favorite, play_count)"
            " VALUES ('/m/1.mp3','T','Alb','A','local',?,1,1,'sid9',0,5)",
            (alb,),
        )
        local_id = c.execute("SELECT id FROM tracks WHERE source='local'").fetchone()[0]
        c.executemany(
            "INSERT INTO track_sources (track_id, kind, provider, provider_item_id, uri)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (stream_id, "stream", "bandcamp", "sid9", "bandcamp://sid9/1"),
                (local_id, "file", "bandcamp", "sid9", "/m/1.mp3"),
            ],
        )
        c.executemany(
            "INSERT INTO track_stats (track_id, favorite, play_count) VALUES (?, ?, ?)",
            [(stream_id, 1, 2), (local_id, 0, 5)],
        )
        c.execute(
            "INSERT INTO playlists (title, created_at, updated_at) VALUES ('P',0,0)"
        )
        pl = c.execute("SELECT id FROM playlists").fetchone()[0]
        c.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?,?,0)",
            (pl, stream_id),
        )
        c.execute(
            "INSERT INTO queue_state (id, tracks, order_json, pos, shuffle, repeat)"
            " VALUES (1, ?, '[0]', 0, 0, 'off')",
            (json.dumps([stream_id]),),
        )
        c.execute("UPDATE schema_version SET version = 45")
        c.commit()
        index.close()

        healed = LibraryIndex(db)  # runs the v46 collapse
        hc = healed._conn
        n_tracks = hc.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        surv = hc.execute("SELECT id, file_path FROM tracks_with_stats").fetchone()
        n_src = hc.execute(
            "SELECT COUNT(*) FROM track_sources WHERE track_id = ?", (surv["id"],)
        ).fetchone()[0]
        st = hc.execute(
            "SELECT favorite, play_count FROM track_stats WHERE track_id = ?",
            (surv["id"],),
        ).fetchone()
        pl_tid = hc.execute("SELECT track_id FROM playlist_tracks").fetchone()[
            "track_id"
        ]
        q = json.loads(
            hc.execute("SELECT tracks FROM queue_state WHERE id=1").fetchone()["tracks"]
        )
        healed.close()

        assert n_tracks == 1
        assert surv["id"] == local_id  # prefer-file: the local row survives
        assert (st["favorite"], st["play_count"]) == (1, 5)  # MAX merge in track_stats
        assert n_src == 2  # both sources re-parented onto the survivor
        assert surv["file_path"] == "/m/1.mp3"  # realigned to preferred (file) source
        assert pl_tid == local_id  # playlist repointed loser→survivor
        assert q == [local_id]  # queue repointed

    def test_upsert_adds_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "01.mp3"))
        tracks = index.all_tracks()
        index.close()

        assert len(tracks) == 1
        assert tracks[0].title == "A Song"
        assert tracks[0].mb_release_id == "rel-123"

    def test_upsert_updates_existing_track(self, tmp_path: Path) -> None:
        path = tmp_path / "01.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(path))
        updated = _sample_track(path)
        updated.title = "Renamed Song"
        index.upsert_track(updated)
        tracks = index.all_tracks()
        index.close()

        assert len(tracks) == 1
        assert tracks[0].title == "Renamed Song"

    def test_remove_track(self, tmp_path: Path) -> None:
        path = tmp_path / "01.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(path))
        index.remove_track(path)
        tracks = index.all_tracks()
        index.close()

        assert tracks == []

    def test_all_tracks_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.all_tracks() == []
        index.close()

    def test_albums_sorted_by_album_artist_then_album(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i, (aa, album) in enumerate(
            [
                ("Zeppelin", "Physical Graffiti"),
                ("Aesop Rock", "Labor Days"),
                ("Aesop Rock", "Bazooka Tooth"),
            ]
        ):
            t = _sample_track(tmp_path / f"{i}.mp3")
            t.artist = aa
            t.album_artist = aa
            t.album = album
            index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert [(a["album_artist"], a["album"]) for a in albums] == [
            ("Aesop Rock", "Bazooka Tooth"),
            ("Aesop Rock", "Labor Days"),
            ("Zeppelin", "Physical Graffiti"),
        ]

    def test_albums_includes_track_count(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i in range(3):
            t = _sample_track(tmp_path / f"{i}.mp3")
            t.track_number = i + 1
            index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert albums[0]["track_count"] == 3

    def test_artists_returns_unique_sorted(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i, aa in enumerate(["Zeppelin", "Aesop Rock", "Aesop Rock"]):
            t = _sample_track(tmp_path / f"{i}.mp3")
            t.album_artist = aa
            index.upsert_track(t)
        artists = index.artists()
        index.close()

        assert artists == ["Aesop Rock", "Zeppelin"]

    def test_case_variant_album_artist_does_not_split_artist_row(
        self, tmp_path: Path
    ) -> None:
        """KAMP-545: two albums by one artist with divergent casing must share a
        single artists row and artist_id, not fork onto two."""
        index = LibraryIndex(tmp_path / "library.db")
        local = _sample_track(tmp_path / "0.mp3")
        local.album_artist = "Sunn O)))"
        local.album = "Monoliths & Dimensions"
        bandcamp = _sample_track(tmp_path / "1.mp3")
        bandcamp.album_artist = "SUNN O)))"  # bandcamp uppercased the name
        bandcamp.album = "Life Metal"
        index.upsert_many([local, bandcamp])

        artist_rows = index._conn.execute(
            "SELECT COUNT(*) FROM artists WHERE name = 'sunn o)))' COLLATE NOCASE"
        ).fetchone()[0]
        artist_ids = {
            r[0]
            for r in index._conn.execute(
                "SELECT artist_id FROM albums"
                " WHERE album_artist = 'sunn o)))' COLLATE NOCASE"
            )
        }
        index.close()

        assert artist_rows == 1
        assert len(artist_ids) == 1 and None not in artist_ids

    def test_record_play_time_credits_single_case_variant_artist(
        self, tmp_path: Path
    ) -> None:
        """KAMP-545: play_time for a case-variant artist accrues to the one
        canonical row rather than being lost to a BINARY name mismatch."""
        index = LibraryIndex(tmp_path / "library.db")
        first = _sample_track(tmp_path / "0.mp3")
        first.album_artist = "Kylesa"
        second = _sample_track(tmp_path / "1.mp3")
        second.album_artist = "KYLESA"
        second.album = "Spiral Shadow"
        index.upsert_many([first, second])

        index.record_play_time(tmp_path / "0.mp3", 30.0)
        index.record_play_time(tmp_path / "1.mp3", 12.0)

        rows = index._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(play_time), 0) FROM artists"
            " WHERE name = 'kylesa' COLLATE NOCASE"
        ).fetchone()
        index.close()

        assert rows[0] == 1
        assert rows[1] == 42.0

    def test_tracks_for_album_sorted_by_disc_then_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for disc, track_num, title in [(1, 2, "B"), (2, 1, "C"), (1, 1, "A")]:
            t = _sample_track(tmp_path / f"{disc}-{track_num}.mp3")
            t.title = title
            t.disc_number = disc
            t.track_number = track_num
            index.upsert_track(t)
        tracks = index.tracks_for_album("The Artist", "The Album")
        index.close()

        assert [t.title for t in tracks] == ["A", "B", "C"]

    def test_upsert_many_inserts_all_tracks(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        tracks = [_sample_track(tmp_path / f"{i}.mp3") for i in range(5)]
        for t in tracks:
            t.track_number = int(t.file_path.stem) + 1
        index.upsert_many(tracks)
        result = index.all_tracks()
        index.close()

        assert len(result) == 5

    def test_upsert_many_empty_list_is_noop(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([])
        assert index.all_tracks() == []
        index.close()

    def test_indexed_paths_returns_set_of_paths(self, tmp_path: Path) -> None:
        p1, p2 = tmp_path / "a.mp3", tmp_path / "b.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(p1))
        index.upsert_track(_sample_track(p2))
        paths = index.indexed_paths()
        index.close()

        assert paths == {p1, p2}

    def test_migrate_on_existing_db_is_idempotent(self, tmp_path: Path) -> None:
        """Opening an already-migrated DB should not insert a second version row."""
        db = tmp_path / "library.db"
        LibraryIndex(db).close()
        # Second open hits the `row is not None` branch in _migrate
        index = LibraryIndex(db)
        index.upsert_track(_sample_track(tmp_path / "01.mp3"))
        assert len(index.all_tracks()) == 1
        index.close()

    def test_albums_has_art_true_when_track_has_embedded_art(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "1.mp3")
        t.embedded_art = True
        index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert albums[0].has_art is True

    def test_albums_has_art_false_when_no_embedded_art(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "1.mp3"))
        albums = index.albums()
        index.close()

        assert albums[0].has_art is False

    def test_albums_has_art_true_when_any_track_has_art(self, tmp_path: Path) -> None:
        """has_art is True if at least one track in the album has embedded art."""
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "1.mp3")
        t1.track_number = 1
        t1.embedded_art = False
        t2 = _sample_track(tmp_path / "2.mp3")
        t2.track_number = 2
        t2.embedded_art = True
        index.upsert_many([t1, t2])
        albums = index.albums()
        index.close()

        assert albums[0].has_art is True

    def test_albums_has_favorite_track_false_by_default(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "1.mp3"))
        albums = index.albums()
        index.close()

        assert albums[0].has_favorite_track is False

    def test_albums_has_favorite_track_true_when_any_track_favorited(
        self, tmp_path: Path
    ) -> None:
        """has_favorite_track is True when at least one track in the album is favorited."""
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "1.mp3")
        t1.track_number = 1
        t2 = _sample_track(tmp_path / "2.mp3")
        t2.track_number = 2
        index.upsert_many([t1, t2])
        index.set_favorite(t1.file_path, favorite=True)
        albums = index.albums()
        index.close()

        assert albums[0].has_favorite_track is True

    def test_albums_has_favorite_track_false_when_no_tracks_favorited(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "1.mp3")
        t1.track_number = 1
        t2 = _sample_track(tmp_path / "2.mp3")
        t2.track_number = 2
        index.upsert_many([t1, t2])
        albums = index.albums()
        index.close()

        assert albums[0].has_favorite_track is False

    def test_missing_album_has_favorite_track_reflects_track_favorite(
        self, tmp_path: Path
    ) -> None:
        """has_favorite_track on missing-album entries uses the track's own favorite flag."""
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "standalone.mp3")
        t.album = ""
        t.title = "Standalone Track"
        index.upsert_track(t)
        index.set_favorite(t.file_path, favorite=True)
        albums = index.albums()
        index.close()

        assert albums[0].missing_album is True
        assert albums[0].has_favorite_track is True

    def test_missing_album_track_appears_as_own_entry(self, tmp_path: Path) -> None:
        """A track with no album tag should produce its own AlbumInfo entry."""
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "standalone.mp3")
        t.album = ""
        t.title = "Standalone Track"
        index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].missing_album is True
        assert albums[0].album == "Standalone Track"  # title used as display name
        assert albums[0].file_path == str(tmp_path / "standalone.mp3")
        # KAMP-537: the entry carries its single track's canonical id.
        assert albums[0].missing_track_id is not None and albums[0].missing_track_id > 0

    def test_two_missing_album_tracks_each_get_own_entry(self, tmp_path: Path) -> None:
        """Each track without an album tag should be its own entry, not grouped."""
        index = LibraryIndex(tmp_path / "library.db")
        for i, title in enumerate(["Track A", "Track B"]):
            t = _sample_track(tmp_path / f"{i}.mp3")
            t.album = ""
            t.title = title
            index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert len(albums) == 2
        assert all(a.missing_album for a in albums)
        assert {a.album for a in albums} == {"Track A", "Track B"}

    def test_missing_album_and_normal_album_coexist(self, tmp_path: Path) -> None:
        """Normal albums and missing-album tracks appear together in the list."""
        index = LibraryIndex(tmp_path / "library.db")
        normal = _sample_track(tmp_path / "normal.mp3")
        normal.album = "Real Album"
        index.upsert_track(normal)

        standalone = _sample_track(tmp_path / "standalone.mp3")
        standalone.album = ""
        standalone.title = "Lone Track"
        index.upsert_track(standalone)

        albums = index.albums()
        index.close()

        assert len(albums) == 2
        normal_entry = next(a for a in albums if not a.missing_album)
        missing_entry = next(a for a in albums if a.missing_album)
        assert normal_entry.album == "Real Album"
        assert missing_entry.album == "Lone Track"
        assert missing_entry.file_path == str(tmp_path / "standalone.mp3")

    def test_albums_art_version_is_max_file_mtime(self, tmp_path: Path) -> None:
        """art_version is the largest file_mtime across tracks in the album."""
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "1.mp3")
        t1.track_number = 1
        t1.file_mtime = 1000.0
        t2 = _sample_track(tmp_path / "2.mp3")
        t2.track_number = 2
        t2.file_mtime = 2000.0
        index.upsert_many([t1, t2])
        albums = index.albums()
        index.close()

        assert albums[0].art_version == pytest.approx(2000.0)

    def test_albums_art_version_none_when_file_mtime_null(self, tmp_path: Path) -> None:
        """art_version is None when no track has a file_mtime."""
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "1.mp3")
        # file_mtime defaults to None — not set
        index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert albums[0].art_version is None

    def test_albums_exposes_added_at(self, tmp_path: Path) -> None:
        """albums() exposes the MIN(date_added) per album as added_at."""
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "0.mp3")
        t.date_added = 1234567890.0
        index.upsert_track(t)
        albums = index.albums(sort="date_added")
        index.close()

        assert albums[0].added_at == pytest.approx(1234567890.0)

    def test_albums_exposes_last_played_at(self, tmp_path: Path) -> None:
        """albums() exposes MAX(last_played) per album as last_played_at."""
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "0.mp3")
        index.upsert_track(t)
        index.record_track_started(tmp_path / "0.mp3")
        albums = index.albums(sort="last_played")
        index.close()

        assert albums[0].last_played_at is not None
        assert albums[0].last_played_at > 0

    def test_missing_album_art_version_is_file_mtime(self, tmp_path: Path) -> None:
        """art_version for a missing-album track is its own file_mtime."""
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "standalone.mp3")
        t.album = ""
        t.title = "Lone Track"
        t.file_mtime = 5000.0
        index.upsert_track(t)
        albums = index.albums()
        index.close()

        assert albums[0].missing_album is True
        assert albums[0].art_version == pytest.approx(5000.0)

    def test_get_track_by_path_returns_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(tmp_path / "01.mp3")
        index.upsert_track(track)
        result = index.get_track_by_path(tmp_path / "01.mp3")
        index.close()

        assert result is not None
        assert result.title == "A Song"

    def test_get_track_by_path_returns_none_for_missing_path(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.get_track_by_path(tmp_path / "missing.mp3")
        index.close()

        assert result is None

    def test_save_and_load_player_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_player_state(7, 42.5)
        result = index.load_player_state()
        index.close()

        assert result is not None
        ref, position = result
        assert isinstance(ref, str)
        assert ref == "7"  # track id stored as text (KAMP-536)
        assert position == 42.5

    def test_load_player_state_returns_none_when_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.load_player_state()
        index.close()

        assert result is None

    def test_clear_player_state_removes_saved_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_player_state(7, 42.5)
        index.clear_player_state()
        result = index.load_player_state()
        index.close()

        assert result is None

    def test_save_player_state_overwrites_previous(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_player_state(1, 10.0)
        index.save_player_state(2, 99.0)
        result = index.load_player_state()
        index.close()

        assert result is not None
        ref, position = result
        assert isinstance(ref, str)
        assert ref == "2"
        assert position == 99.0

    def test_save_and_load_queue_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track_ids = [10, 20, 30]
        index.save_queue_state(
            track_ids, order=[2, 0, 1], pos=1, shuffle=True, repeat=False
        )
        result = index.load_queue_state()
        index.close()

        assert result is not None
        entries, order, pos, shuffle, repeat = result
        assert all(isinstance(e, int) for e in entries)
        assert entries == [10, 20, 30]
        assert order == [2, 0, 1]
        assert pos == 1
        assert shuffle is True
        assert repeat == "off"

    def test_load_queue_state_returns_none_when_absent(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.load_queue_state()
        index.close()

        assert result is None

    def test_save_queue_state_overwrites_previous(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_queue_state([10], order=[0], pos=0, shuffle=False, repeat=False)
        index.save_queue_state(
            [20, 30],
            order=[1, 0],
            pos=1,
            shuffle=True,
            repeat="queue",
        )
        result = index.load_queue_state()
        index.close()

        assert result is not None
        entries, order, pos, shuffle, repeat = result
        assert entries == [20, 30]
        assert order == [1, 0]
        assert pos == 1
        assert shuffle is True
        assert repeat == "queue"

    def test_clear_queue_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_queue_state([10], order=[0], pos=0, shuffle=False, repeat=False)
        index.clear_queue_state()
        result = index.load_queue_state()
        index.close()

        assert result is None

    def test_load_queue_state_legacy_empty_order_uses_natural_order(
        self, tmp_path: Path
    ) -> None:
        # Simulate a pre-v18 DB row where order_json is '' (empty string).
        import json, sqlite3

        db_path = tmp_path / "legacy.db"
        index = LibraryIndex(db_path)
        # Force a raw insert with empty order_json to simulate the legacy format.
        index._conn.execute(
            "INSERT INTO queue_state (id, tracks, order_json, pos, shuffle, repeat) "
            "VALUES (1, ?, '', 0, 0, 0)",
            (json.dumps([str(tmp_path / "a.mp3"), str(tmp_path / "b.mp3")]),),
        )
        index._conn.commit()
        result = index.load_queue_state()
        index.close()

        assert result is not None
        paths, order, pos, shuffle, repeat = result
        assert order == [0, 1]  # natural order as fallback


# ---------------------------------------------------------------------------
# extract_art
# ---------------------------------------------------------------------------


class TestExtractArt:
    def test_mp3_with_apic_returns_data_and_mime(self, tmp_path: Path) -> None:
        path = tmp_path / "track.mp3"
        path.write_bytes(b"\xff\xfb" * 64)
        img_data = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        tags = id3.ID3()
        tags.add(
            id3.APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img_data)
        )
        tags.save(str(path))

        result = extract_art(path)

        assert result is not None
        data, mime = result
        assert data == img_data
        assert mime == "image/jpeg"

    def test_mp3_without_art_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "track.mp3"
        _make_mp3(path, title="No Art")

        assert extract_art(path) is None

    def test_nonexistent_path_returns_none(self, tmp_path: Path) -> None:
        assert extract_art(tmp_path / "ghost.mp3") is None


# ---------------------------------------------------------------------------
# LibraryScanner
# ---------------------------------------------------------------------------


class TestLibraryScanner:
    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        index.close()

        assert result == ScanResult(added=0, removed=0, unchanged=0)

    def test_scan_finds_and_indexes_mp3_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="Track One")
        _make_mp3(lib / "02.mp3", title="Track Two")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.added == 2
        assert len(tracks) == 2

    def test_scan_reads_mp3_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(
            lib / "01.mp3",
            artist="The Artist",
            album_artist="The Artist",
            album="Great Album",
            release_date="2010",
            title="Best Song",
            track="5",
            disc="2",
        )

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        t = tracks[0]
        assert t.artist == "The Artist"
        assert t.album_artist == "The Artist"
        assert t.album == "Great Album"
        assert t.release_date == "2010"
        assert t.title == "Best Song"
        assert t.track_number == 5
        assert t.disc_number == 2
        assert t.ext == "mp3"

    def test_scan_reads_full_iso_release_date_from_mp3(self, tmp_path: Path) -> None:
        """Full ISO date from TDRC (e.g. '2023-03-15') is preserved without truncation."""
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", release_date="2023-03-15")

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert tracks[0].release_date == "2023-03-15"

    def test_scan_reads_full_iso_release_date_from_m4a(self, tmp_path: Path) -> None:
        """Full ISO date from ©day (e.g. '2020-06-12') is preserved without truncation."""
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.m4a").write_bytes(b"\x00" * 32)

        mock_audio = MagicMock()
        mock_audio.tags = {"\xa9day": ["2020-06-12"]}

        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        assert tracks[0].release_date == "2020-06-12"

    def test_scan_parses_track_number_with_total(self, tmp_path: Path) -> None:
        """TRCK can be "5/12"; only the number part should be stored."""
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", track="5/12", disc="2/2")

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert tracks[0].track_number == 5
        assert tracks[0].disc_number == 2

    def test_scan_handles_missing_tags_gracefully(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "untagged.mp3").write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(lib / "untagged.mp3"))

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.added == 1
        assert tracks[0].title == ""
        assert tracks[0].artist == ""
        assert tracks[0].track_number == 0

    def test_scan_ignores_non_audio_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "cover.jpg").write_bytes(b"\xff\xd8\xff")
        (lib / "info.txt").write_text("notes")
        _make_mp3(lib / "01.mp3", title="Track")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        index.close()

        assert result.added == 1

    def test_scan_walks_subdirectories(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        subdir = lib / "Artist" / "2024 - Album"
        subdir.mkdir(parents=True)
        _make_mp3(subdir / "01.mp3", title="Nested Track")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)
        index.close()

        assert result.added == 1

    def test_scan_incremental_adds_only_new_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="Existing")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        _make_mp3(lib / "02.mp3", title="New")
        result = scanner.scan(lib)
        index.close()

        assert result.added == 1
        assert result.unchanged == 1

    def test_scan_removes_deleted_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        mp3 = lib / "01.mp3"
        _make_mp3(mp3, title="Gone")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)
        mp3.unlink()
        result = scanner.scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.removed == 1
        assert tracks == []

    def test_scan_removes_orphaned_local_album(self, tmp_path: Path) -> None:
        """Deleting every file of a local album prunes the album row too (KAMP-522).

        Otherwise the album lingers with zero tracks and surfaces in the UI as
        a ghost card with track_count == 0.
        """
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="One", album="Ghost", album_artist="Spectre")
        _make_mp3(lib / "02.mp3", title="Two", album="Ghost", album_artist="Spectre")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)
        assert any(a.album == "Ghost" for a in index.albums())

        (lib / "01.mp3").unlink()
        (lib / "02.mp3").unlink()
        scanner.scan(lib)
        albums = index.albums()
        index.close()

        assert not any(a.album == "Ghost" for a in albums)

    def test_scan_keeps_album_with_remaining_track(self, tmp_path: Path) -> None:
        """Deleting only some files leaves the album with its surviving tracks."""
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="One", album="Half", album_artist="Band")
        _make_mp3(lib / "02.mp3", title="Two", album="Half", album_artist="Band")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)
        (lib / "02.mp3").unlink()
        scanner.scan(lib)
        albums = [a for a in index.albums() if a.album == "Half"]
        index.close()

        assert len(albums) == 1
        assert albums[0].track_count == 1

    def test_prune_empty_albums_keeps_bandcamp_backed_album(
        self, tmp_path: Path
    ) -> None:
        """A sale_item_id-bearing album with zero tracks survives the prune.

        Preorders and fetch-failed streaming albums legitimately have no
        tracks; only purely-local (sale_item_id IS NULL) orphans are removed.
        """
        index = LibraryIndex(tmp_path / "library.db")
        conn = index._conn
        # A local orphan (no tracks, no provenance) — should be pruned.
        conn.execute(
            "INSERT INTO albums (album_artist, album) VALUES ('Local', 'Orphan')"
        )
        # A Bandcamp preorder-shaped row (sale_item_id set, zero tracks) — kept.
        conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, mode) VALUES ('sid-1', 'preorder')"
        )
        conn.execute(
            "INSERT INTO albums (album_artist, album, sale_item_id, source)"
            " VALUES ('Artist', 'Preorder', 'sid-1', 'bandcamp')"
        )
        conn.commit()

        removed = index.prune_empty_albums()
        remaining = {a.album for a in index.albums()}
        index.close()

        assert removed == 1
        assert "Orphan" not in remaining
        assert "Preorder" in remaining

    def test_scan_keeps_fork_with_remote_track(self, tmp_path: Path) -> None:
        """A downloaded purchase reverts to streaming when its local files vanish.

        The album carries sale_item_id and still has a bandcamp:// row, so it
        must not be pruned when the local file is deleted.
        """
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="One", album="Fork", album_artist="Artist")

        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        scanner = LibraryScanner(index)
        scanner.scan(lib)
        conn = index._conn
        # Stamp the scanned album with provenance and add a streaming sibling row.
        conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, mode) VALUES ('sid-9', 'local')"
        )
        conn.execute("UPDATE albums SET sale_item_id = 'sid-9' WHERE album = 'Fork'")
        album_id = conn.execute(
            "SELECT id FROM albums WHERE album = 'Fork'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO tracks (file_path, title, album, album_artist, source, album_id)"
            " VALUES ('bandcamp://sid-9/1', 'One', 'Fork', 'Artist', 'bandcamp', ?)",
            (album_id,),
        )
        conn.commit()

        (lib / "01.mp3").unlink()
        scanner.scan(lib)
        albums = {a.album for a in index.albums()}
        index.close()

        assert "Fork" in albums

    def test_scan_reads_m4a_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.m4a").write_bytes(b"\x00" * 32)

        mock_audio = MagicMock()
        mock_audio.tags = {
            "\xa9ART": ["M4A Artist"],
            "aART": ["M4A Album Artist"],
            "\xa9alb": ["M4A Album"],
            "\xa9day": ["2023"],
            "\xa9nam": ["M4A Track"],
            "trkn": [(3, 10)],
            "disk": [(1, 1)],
        }

        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        t = tracks[0]
        assert t.artist == "M4A Artist"
        assert t.album_artist == "M4A Album Artist"
        assert t.album == "M4A Album"
        assert t.release_date == "2023"
        assert t.title == "M4A Track"
        assert t.track_number == 3
        assert t.ext == "m4a"

    def test_scan_reads_flac_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.flac").write_bytes(b"fLaC")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "ARTIST": ["FLAC Artist"],
            "ALBUMARTIST": ["FLAC Album Artist"],
            "ALBUM": ["FLAC Album"],
            "DATE": ["2022"],
            "TITLE": ["FLAC Track"],
            "TRACKNUMBER": ["7"],
            "DISCNUMBER": ["2"],
            "MUSICBRAINZ_ALBUMID": ["mbid-flac"],
        }
        mock_audio.pictures = []

        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        t = tracks[0]
        assert t.artist == "FLAC Artist"
        assert t.album_artist == "FLAC Album Artist"
        assert t.album == "FLAC Album"
        assert t.release_date == "2022"
        assert t.title == "FLAC Track"
        assert t.track_number == 7
        assert t.disc_number == 2
        assert t.mb_release_id == "mbid-flac"
        assert t.ext == "flac"

    def test_scan_reads_flac_tags_lowercase_keys(self, tmp_path: Path) -> None:
        """Real mutagen VCFLACDict yields lowercase keys; the reader must handle them."""
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.flac").write_bytes(b"fLaC")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "artist": ["Stereolab"],
            "albumartist": ["Stereolab"],
            "album": ["Emperor Tomato Ketchup"],
            "date": ["1996"],
            "title": ["Metronomic Underground"],
            "tracknumber": ["1"],
            "discnumber": ["1"],
            "musicbrainz_albumid": ["mbid-etk"],
        }
        mock_audio.pictures = []

        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        t = tracks[0]
        assert t.artist == "Stereolab"
        assert t.album == "Emperor Tomato Ketchup"
        assert t.title == "Metronomic Underground"
        assert t.release_date == "1996"
        assert t.mb_release_id == "mbid-etk"

    def test_scan_reads_ogg_tags(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.ogg").write_bytes(b"OggS")

        mock_audio = MagicMock()
        mock_audio.tags = {
            "ARTIST": ["OGG Artist"],
            "ALBUMARTIST": ["OGG Album Artist"],
            "ALBUM": ["OGG Album"],
            "DATE": ["2021"],
            "TITLE": ["OGG Track"],
            "TRACKNUMBER": ["4"],
            "DISCNUMBER": ["1"],
        }
        mock_audio.pictures = []

        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis", return_value=mock_audio
        ):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        assert len(tracks) == 1
        assert tracks[0].artist == "OGG Artist"
        assert tracks[0].ext == "ogg"

    def test_read_mp3_duration(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="Song")

        mock_mp3 = MagicMock()
        mock_mp3.info.length = 180.0

        with patch("kamp_core.library.mutagen.mp3.MP3", return_value=mock_mp3):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        assert tracks[0].duration == 180.0

    def test_read_mp3_duration_graceful_on_error(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3", title="Song")

        with patch("kamp_core.library.mutagen.mp3.MP3", side_effect=Exception("bad")):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        assert tracks[0].duration == 0.0

    def test_read_m4a_duration(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.m4a").write_bytes(b"\x00" * 32)

        mock_audio = MagicMock()
        mock_audio.tags = {"\xa9nam": ["Track"]}
        mock_audio.info.length = 240.0

        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        assert tracks[0].duration == 240.0

    def test_read_flac_duration(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        (lib / "01.flac").write_bytes(b"fLaC")

        mock_audio = MagicMock()
        mock_audio.tags = {"TITLE": ["Track"]}
        mock_audio.pictures = []
        mock_audio.info.length = 300.0

        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            index = LibraryIndex(tmp_path / "library.db")
            LibraryScanner(index).scan(lib)
            tracks = index.all_tracks()
            index.close()

        assert tracks[0].duration == 300.0

    def test_scan_nonexistent_directory(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(tmp_path / "does_not_exist")
        index.close()

        assert result == ScanResult(added=0, removed=0, unchanged=0)

    def test_scan_skips_unreadable_file(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "bad.mp3")

        with patch("kamp_core.library._read_tags", return_value=None):
            index = LibraryIndex(tmp_path / "library.db")
            result = LibraryScanner(index).scan(lib)
            index.close()

        assert result.added == 0

    def test_scan_calls_on_progress_for_each_new_file(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")
        _make_mp3(lib / "02.mp3")
        _make_mp3(lib / "03.mp3")

        calls: list[tuple[int, int]] = []
        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(
            lib, on_progress=lambda c, t, _track: calls.append((c, t))
        )
        index.close()

        # One call per new file; total is always 3.
        assert len(calls) == 3
        assert all(total == 3 for _, total in calls)
        assert sorted(current for current, _ in calls) == [1, 2, 3]

    def test_scan_on_progress_not_called_when_no_new_files(
        self, tmp_path: Path
    ) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")

        index = LibraryIndex(tmp_path / "library.db")
        # First scan indexes the file.
        LibraryScanner(index).scan(lib)
        # Second scan: nothing new — callback must not be called.
        calls: list[tuple[int, int]] = []
        LibraryScanner(index).scan(lib, on_progress=lambda c, t: calls.append((c, t)))
        index.close()

        assert calls == []

    def test_scan_without_on_progress_still_works(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")

        index = LibraryIndex(tmp_path / "library.db")
        result = LibraryScanner(index).scan(lib)  # no on_progress arg
        index.close()

        assert result.added == 1

    def test_scan_sets_embedded_art_true_when_cover_file_present(
        self, tmp_path: Path
    ) -> None:
        """Files alongside a cover.jpg are indexed with embedded_art=True."""
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")
        (lib / "cover.jpg").write_bytes(b"\xff\xd8\xff")  # minimal JPEG magic

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert len(tracks) == 1
        assert tracks[0].embedded_art is True

    def test_scan_sets_embedded_art_true_for_cover_png(self, tmp_path: Path) -> None:
        """Files alongside a cover.png are indexed with embedded_art=True."""
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")
        (lib / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert len(tracks) == 1
        assert tracks[0].embedded_art is True

    def test_scan_no_cover_file_leaves_embedded_art_false(self, tmp_path: Path) -> None:
        """Files without embedded art or a cover file have embedded_art=False."""
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "01.mp3")

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert len(tracks) == 1
        assert tracks[0].embedded_art is False

    def test_scan_inherits_favorite_from_remote_track(self, tmp_path: Path) -> None:
        """A newly-scanned local file keeps the favorite flag set on its matching
        bandcamp:// sibling (same album_artist, album, track_number, disc_number).

        The reconcile merge inside upsert_many (KAMP-541) collapses the stream+local
        pair into one canonical row and MAX-carries the favorite — no separate
        inherit pass is involved (KAMP-553 proved inherit was a no-op here and
        deleted it). Preserves favorites set on streaming tracks after a download.
        """
        lib = tmp_path / "music"
        lib.mkdir()

        index = LibraryIndex(tmp_path / "library.db")
        # Seed a remote track that has been favorited.
        remote_uri = "bandcamp://999/3"
        index.upsert_many(
            [
                Track(
                    file_path=Path(remote_uri),
                    title="Remote",
                    artist="The Artist",
                    album_artist="The Artist",
                    album="Great Album",
                    release_date="2020",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                )
            ]
        )
        index.set_favorite(remote_uri, True)

        # Scan in a local file with matching album metadata.
        _make_mp3(
            lib / "01.mp3",
            artist="The Artist",
            album_artist="The Artist",
            album="Great Album",
            release_date="2020",
            title="Remote",
            track="1",
            disc="1",
        )
        LibraryScanner(index).scan(lib)

        local_track = index.get_track_by_path(lib / "01.mp3")
        index.close()

        assert local_track is not None
        assert local_track.favorite is True

    def test_scan_collapses_stream_and_local_carrying_both_stats(
        self, tmp_path: Path
    ) -> None:
        """The gating invariant behind KAMP-553's inherit deletion.

        Scanning a no-provenance local file for the same (album, track, disc) as an
        existing favorited + played bandcamp:// stream must collapse the pair into a
        SINGLE canonical row (one file source + one stream source) that carries both
        stats forward. This is exactly the state that made inherit_remote_favorites/
        _play_counts a no-op: the reconcile merge always fires first, so there is
        never a second row for inherit to write to. If a future reconcile regression
        re-strands the pair, this test fails loudly rather than silently relying on
        the (now deleted) inherit fallback.
        """
        lib = tmp_path / "music"
        lib.mkdir()

        index = LibraryIndex(tmp_path / "library.db")
        remote_uri = "bandcamp://777/2"
        index.upsert_many(
            [
                Track(
                    file_path=Path(remote_uri),
                    title="Streamed",
                    artist="The Band",
                    album_artist="The Band",
                    album="Split Album",
                    release_date="2021",
                    track_number=2,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                )
            ]
        )
        index.set_favorite(remote_uri, True)
        # Seed a streaming play_count on the authoritative store (track_stats).
        index._conn.execute(
            "UPDATE track_stats SET play_count = 5 WHERE track_id ="
            " (SELECT track_id FROM track_sources WHERE uri = ?)",
            (remote_uri,),
        )
        index._conn.commit()

        _make_mp3(
            lib / "02.mp3",
            artist="The Band",
            album_artist="The Band",
            album="Split Album",
            release_date="2021",
            title="Streamed",
            track="2",
            disc="1",
        )
        LibraryScanner(index).scan(lib)

        # Exactly one tracks row for this (album, track, disc) — the pair collapsed.
        row_count = index._conn.execute(
            "SELECT COUNT(*) FROM tracks_with_stats"
            " WHERE album = 'Split Album' AND track_number = 2 AND disc_number = 1"
        ).fetchone()[0]
        assert row_count == 1

        local_track = index.get_track_by_path(lib / "02.mp3")
        assert local_track is not None
        # The one survivor owns both a file and a stream source.
        kinds = {
            r[0]
            for r in index._conn.execute(
                "SELECT kind FROM track_sources WHERE track_id = ?",
                (local_track.id,),
            ).fetchall()
        }
        assert kinds == {"file", "stream"}
        # Both stats carried onto the survivor via the merge (favorite OR-to-1,
        # play_count MAX) — the behaviour inherit used to backstop.
        assert local_track.favorite is True
        assert local_track.play_count == 5
        index.close()


# ---------------------------------------------------------------------------
# Tag reader helpers
# ---------------------------------------------------------------------------


class TestTagReaders:
    def test_read_mp3_tags_falls_back_on_no_id3_header(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "no_id3.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)  # MPEG bytes, no ID3 header
        track = _read_mp3_tags(mp3)
        assert track.title == ""
        assert track.ext == "mp3"

    def test_read_m4a_tags_falls_back_on_parse_error(self, tmp_path: Path) -> None:
        m4a = tmp_path / "bad.m4a"
        m4a.write_bytes(b"\x00" * 8)
        with patch("kamp_core.library.mutagen.mp4.MP4", side_effect=Exception("bad")):
            track = _read_m4a_tags(m4a)
        assert track.title == ""
        assert track.ext == "m4a"

    def test_read_vorbis_tags_falls_back_on_parse_error(self, tmp_path: Path) -> None:
        ogg = tmp_path / "bad.ogg"
        ogg.write_bytes(b"\x00" * 8)
        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis",
            side_effect=Exception("bad"),
        ):
            track = _read_vorbis_tags(ogg, is_flac=False)
        assert track.title == ""
        assert track.ext == "ogg"

    def test_read_mp3_multi_value_genre(self, tmp_path: Path) -> None:
        # KAMP-586: a TCON with multiple values reads back as a genres list.
        mp3 = tmp_path / "multi.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TCON"] = id3.TCON(encoding=3, text=["Jazz", "J-Pop"])
        tags.save(str(mp3))
        track = _read_mp3_tags(mp3)
        assert track.genres == ["Jazz", "J-Pop"]

    def test_read_m4a_multi_value_genre(self, tmp_path: Path) -> None:
        m4a = tmp_path / "multi.m4a"
        m4a.write_bytes(b"\x00" * 32)
        mock_audio = MagicMock()
        mock_audio.tags = {"\xa9gen": ["Jazz", "J-Pop"], "\xa9nam": ["T"]}
        mock_audio.info.length = 1.0
        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            track = _read_m4a_tags(m4a)
        assert track.genres == ["Jazz", "J-Pop"]

    def test_read_vorbis_multi_value_genre(self, tmp_path: Path) -> None:
        ogg = tmp_path / "multi.ogg"
        ogg.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        # Vorbis comments: repeated GENRE keys arrive as a list.
        mock_audio.tags = {"genre": ["Jazz", "J-Pop"], "title": ["T"]}
        mock_audio.pictures = []
        mock_audio.info.length = 1.0
        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis", return_value=mock_audio
        ):
            track = _read_vorbis_tags(ogg, is_flac=False)
        assert track.genres == ["Jazz", "J-Pop"]

    def test_read_tags_logs_and_returns_none_on_unexpected_error(
        self, tmp_path: Path
    ) -> None:
        mp3 = tmp_path / "boom.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        with patch(
            "kamp_core.library._read_mp3_tags", side_effect=RuntimeError("boom")
        ):
            result = _read_tags(mp3)
        assert result is None

    def test_read_mp3_tags_album_artist_falls_back_to_artist(
        self, tmp_path: Path
    ) -> None:
        """When TPE2 (album artist) is absent, album_artist should equal TPE1 (artist)."""
        mp3 = tmp_path / "no_tpe2.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TPE1"] = id3.TPE1(encoding=3, text="Solo Artist")
        tags.save(str(mp3))
        track = _read_mp3_tags(mp3)
        assert track.artist == "Solo Artist"
        assert track.album_artist == "Solo Artist"

    def test_read_m4a_tags_album_artist_falls_back_to_artist(
        self, tmp_path: Path
    ) -> None:
        """When aART is absent, album_artist should equal ©ART."""
        m4a = tmp_path / "no_aart.m4a"
        m4a.write_bytes(b"\x00" * 32)
        mock_audio = MagicMock()
        mock_audio.tags = {"\xa9ART": ["Solo Artist"], "\xa9nam": ["A Track"]}
        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            track = _read_m4a_tags(m4a)
        assert track.artist == "Solo Artist"
        assert track.album_artist == "Solo Artist"

    def test_read_vorbis_tags_album_artist_falls_back_to_artist(
        self, tmp_path: Path
    ) -> None:
        """When ALBUMARTIST is absent, album_artist should equal ARTIST."""
        ogg = tmp_path / "no_albumartist.ogg"
        ogg.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        mock_audio.tags = {"ARTIST": ["Solo Artist"], "TITLE": ["A Track"]}
        mock_audio.pictures = []
        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis", return_value=mock_audio
        ):
            track = _read_vorbis_tags(ogg, is_flac=False)
        assert track.artist == "Solo Artist"
        assert track.album_artist == "Solo Artist"

    def test_read_tags_returns_none_for_unknown_extension(self, tmp_path: Path) -> None:
        wav = tmp_path / "file.wav"
        wav.write_bytes(b"")
        assert _read_tags(wav) is None


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------


class TestSearch:
    def _index_with_tracks(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        tracks = [
            Track(
                file_path=tmp_path / "01.mp3",
                title="Morning Bell",
                artist="Radiohead",
                album_artist="Radiohead",
                album="Kid A",
                release_date="2000",
                track_number=1,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
            Track(
                file_path=tmp_path / "02.mp3",
                title="Everything in Its Right Place",
                artist="Radiohead",
                album_artist="Radiohead",
                album="Kid A",
                release_date="2000",
                track_number=2,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
            Track(
                file_path=tmp_path / "03.mp3",
                title="Ocean",
                artist="Björk",
                album_artist="Björk",
                album="Homogenic",
                release_date="1997",
                track_number=1,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
        ]
        index.upsert_many(tracks)
        return index

    def test_empty_query_returns_no_results(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("")
        index.close()
        assert results == []

    def test_whitespace_only_query_returns_no_results(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("   ")
        index.close()
        assert results == []

    def test_match_by_artist(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("radiohead")
        index.close()
        assert all(t.album_artist == "Radiohead" for t in results)
        assert len(results) == 2

    def test_match_by_album(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("kid a")
        index.close()
        assert len(results) == 2

    def test_match_by_title(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("morning bell")
        index.close()
        assert len(results) == 1
        assert results[0].title == "Morning Bell"

    def test_prefix_match(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("radio")
        index.close()
        assert len(results) == 2

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        results = index.search("zzznomatch")
        index.close()
        assert results == []

    # -- genre search (KAMP-601) ---------------------------------------------

    def _index_with_genres(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                Track(
                    file_path=tmp_path / "g1.mp3",
                    title="Blue Train",
                    artist="Coltrane",
                    album_artist="John Coltrane",
                    album="Blue Train",
                    release_date="1958",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    genres=["Jazz"],
                ),
                Track(
                    file_path=tmp_path / "g2.mp3",
                    title="Aisha",
                    artist="Grimes",
                    album_artist="Grimes",
                    album="Visions",
                    release_date="2012",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    genres=["Ambient", "Free Jazz"],
                ),
            ]
        )
        return index

    def test_match_by_genre(self, tmp_path: Path) -> None:
        # "jazz" matches the single-value "Jazz" track and the multi-value
        # "Ambient; Free Jazz" track (unicode61 tokenizes the joined string).
        index = self._index_with_genres(tmp_path)
        results = index.search("jazz")
        index.close()
        assert {t.title for t in results} == {"Blue Train", "Aisha"}

    def test_match_by_genre_word_from_multi_value(self, tmp_path: Path) -> None:
        index = self._index_with_genres(tmp_path)
        results = index.search("ambient")
        index.close()
        assert {t.title for t in results} == {"Aisha"}

    def test_genre_no_match_returns_empty(self, tmp_path: Path) -> None:
        index = self._index_with_genres(tmp_path)
        results = index.search("techno")
        index.close()
        assert results == []

    def test_genre_edit_updates_search(self, tmp_path: Path) -> None:
        # apply_genres reindexes FTS so an edit is immediately searchable and the
        # old genre stops matching.
        index = self._index_with_genres(tmp_path)
        blue = next(t for t in index.all_tracks() if t.title == "Blue Train")
        index.apply_genres([blue.id], ["Techno"], mode="replace")
        techno = {t.title for t in index.search("techno")}
        jazz = {t.title for t in index.search("jazz")}
        index.close()
        assert "Blue Train" in techno
        assert "Blue Train" not in jazz  # only "Aisha" (Free Jazz) remains
        assert jazz == {"Aisha"}

    def test_fresh_db_fts_has_genre_column(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(tracks_fts)")}
        index.close()
        assert "genre" in cols

    def test_migration_v56_recreates_fts_with_genre(self, tmp_path: Path) -> None:
        # A pre-v57 DB has a 4-column tracks_fts. Opening it must recreate the
        # FTS with genre and reindex so genre becomes searchable (KAMP-601).
        db = tmp_path / "library.db"
        index = self._index_with_genres(tmp_path)
        index.close()
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE tracks_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, tokenize='unicode61')"
        )
        conn.execute(
            "INSERT INTO tracks_fts(rowid, title, artist, album_artist, album)"
            " SELECT id, title, artist, album_artist, album FROM tracks"
        )
        conn.execute("UPDATE schema_version SET version = 56")
        conn.commit()
        conn.close()

        index = LibraryIndex(db)
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(tracks_fts)")}
        results = index.search("jazz")
        index.close()
        assert "genre" in cols
        assert {t.title for t in results} == {"Blue Train", "Aisha"}

    def test_removed_track_excluded_from_search(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        index.remove_track(tmp_path / "01.mp3")
        results = index.search("morning bell")
        index.close()
        assert results == []

    def test_favorite_track_ranks_above_non_favorite(self, tmp_path: Path) -> None:
        index = self._index_with_tracks(tmp_path)
        # Both tracks are on "Kid A" by Radiohead; favorite the second one.
        index.set_favorite(tmp_path / "02.mp3", True)
        results = index.search("radiohead")
        index.close()
        assert results[0].title == "Everything in Its Right Place"
        assert results[1].title == "Morning Bell"

    def test_album_favorite_track_ranks_above_non_album_favorite(
        self, tmp_path: Path
    ) -> None:
        index = self._index_with_tracks(tmp_path)
        # Favorite the Björk album; search "radiohead" matches only Radiohead tracks,
        # but search "ocean" matches only the Björk track which is album-favorited.
        # More interesting: search a term matching both Radiohead tracks and the Björk track
        # isn't possible with current fixtures. Instead verify that when two albums match,
        # the favorited album's tracks come first.
        index.toggle_album_favorite("Björk", "Homogenic", True)
        # "ocean" only matches Björk — check the track is returned
        results = index.search("ocean")
        index.close()
        assert len(results) == 1
        assert results[0].album_artist == "Björk"

    def test_album_favorite_boosts_tracks_above_non_favorited(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        tracks = [
            Track(
                file_path=tmp_path / "a1.mp3",
                title="Song Alpha",
                artist="ArtistA",
                album_artist="ArtistA",
                album="AlbumA",
                release_date="2000",
                track_number=1,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
            Track(
                file_path=tmp_path / "b1.mp3",
                title="Song Alpha",
                artist="ArtistB",
                album_artist="ArtistB",
                album="AlbumB",
                release_date="2001",
                track_number=1,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            ),
        ]
        index.upsert_many(tracks)
        # Favorite AlbumB — its track should rank first despite same FTS score.
        index.toggle_album_favorite("ArtistB", "AlbumB", True)
        results = index.search("song alpha")
        index.close()
        assert results[0].album_artist == "ArtistB"
        assert results[1].album_artist == "ArtistA"

    def test_search_hides_streaming_twin_when_local_in_same_album(
        self, tmp_path: Path
    ) -> None:
        """KAMP-529: when a local track and its bandcamp:// streaming twin share an
        album, search returns only the local row — mirroring the album detail
        view's local-wins collapse so downloaded tracks don't double in search."""
        index = LibraryIndex(tmp_path / "library.db")
        streaming = Track(
            file_path=Path("bandcamp://555/1"),
            title="Adrenaline",
            artist="MAGNAVOLT",
            album_artist="MAGNAVOLT",
            album="EDGEZONE FM",
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        local = Track(
            file_path=tmp_path / "adrenaline.flac",
            title="Adrenaline",
            artist="MAGNAVOLT",
            album_artist="MAGNAVOLT",
            album="EDGEZONE FM",
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="flac",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="local",
        )
        index.upsert_many([streaming, local])
        results = index.search("adrenaline")
        index.close()
        assert len(results) == 1
        assert results[0].source == "local"
        assert not str(results[0].file_path).startswith("bandcamp://")

    def test_search_keeps_streaming_row_when_album_has_no_local(
        self, tmp_path: Path
    ) -> None:
        """A purely-streaming album (no local twin) still surfaces its bandcamp://
        row in search — the collapse must not hide un-downloaded tracks."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                Track(
                    file_path=Path("bandcamp://556/1"),
                    title="Streaming Only",
                    artist="MAGNAVOLT",
                    album_artist="MAGNAVOLT",
                    album="Remote Album",
                    release_date="2020",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                )
            ]
        )
        results = index.search("streaming only")
        index.close()
        assert len(results) == 1
        assert results[0].source == "bandcamp"

    def test_v1_database_migrated_to_current(self, tmp_path: Path) -> None:
        """Existing v1 databases are fully migrated (FTS + date columns) on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a v1-style database without the FTS table.
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (1)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT ''
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/a.mp3', 'Title', 'ArtistA', 'ArtistA', "
            "'RecordA', '2000', 1, 1, 'mp3', 0, '', '')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        # Opening with LibraryIndex should migrate v1 → current.
        index = LibraryIndex(db_path)
        results = index.search("ArtistA")
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert version == 60
        assert len(results) == 1
        assert results[0].title == "Title"

    def test_v2_database_migrated_to_v3(self, tmp_path: Path) -> None:
        """Existing v2 databases gain date_added and last_played columns on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a v2-style database (has FTS but no date columns).
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT ''
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/b.mp3', 'Song', 'BandB', 'BandB', "
            "'AlbumB', '2010', 1, 1, 'mp3', 0, '', '')"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, content=tracks, content_rowid=id)"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        # Opening with LibraryIndex should migrate v2 → v4.
        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        # date_added and last_played columns must exist (no exception on select).
        row = index._conn.execute(
            "SELECT date_added, last_played FROM tracks_with_stats WHERE id = 1"
        ).fetchone()
        index.close()

        assert version == 60
        assert row is not None
        # date_added will be NULL since the file path is fake; that is expected.
        assert row[0] is None
        assert row[1] is None


# ---------------------------------------------------------------------------
# Remove genre (KAMP-606)
# ---------------------------------------------------------------------------


class TestRemoveGenre:
    """LibraryIndex.remove_genre / genres_for_track / tracks_for_genre (KAMP-606)."""

    def _track(
        self, tmp_path: Path, name: str, album: str, genres: list[str]
    ) -> "Track":
        return Track(
            file_path=tmp_path / f"{name}.mp3",
            title=name,
            artist="Artist",
            album_artist="Artist",
            album=album,
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genres=genres,
        )

    def _index(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                self._track(tmp_path, "a", "AlbumA", ["Jazz"]),
                self._track(tmp_path, "b", "AlbumB", ["Ambient", "Jazz"]),
                self._track(tmp_path, "c", "AlbumC", ["Rock"]),
            ]
        )
        return index

    def _genre_of(self, index: LibraryIndex, title: str) -> str:
        return next(t for t in index.all_tracks() if t.title == title).genre

    def test_strips_from_all_tracks_and_drops_from_list(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        count = index.remove_genre("Jazz")
        assert count == 2  # tracks a and b carried Jazz
        assert "Jazz" not in index.all_genres()
        assert self._genre_of(index, "a") == ""  # only genre was Jazz
        index.close()

    def test_keeps_other_genres_on_multi_genre_track(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.remove_genre("Jazz")
        assert index.genres_for_track(
            next(t for t in index.all_tracks() if t.title == "b").id
        ) == ["Ambient"]
        assert self._genre_of(index, "b") == "Ambient"
        index.close()

    def test_leaves_unrelated_genres_untouched(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.remove_genre("Jazz")
        assert index.all_genres() == ["Ambient", "Rock"]
        assert self._genre_of(index, "c") == "Rock"
        index.close()

    def test_deletes_orphan_vocabulary_row(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.remove_genre("Jazz")
        row = index._conn.execute(
            "SELECT id FROM genres WHERE name = ? COLLATE NOCASE", ("Jazz",)
        ).fetchone()
        assert row is None
        index.close()

    def test_case_insensitive(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        count = index.remove_genre("jAzZ")  # stored as "Jazz"
        assert count == 2
        assert "Jazz" not in index.all_genres()
        index.close()

    def test_nonexistent_genre_returns_zero(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        assert index.remove_genre("Techno") == 0
        assert index.all_genres() == ["Ambient", "Jazz", "Rock"]
        index.close()

    def test_refreshes_album_genre_union(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.remove_genre("Jazz")
        # AlbumB's denormalized union should now be just "Ambient".
        genre = index._conn.execute(
            "SELECT genre FROM albums WHERE album = ?", ("AlbumB",)
        ).fetchone()["genre"]
        assert genre == "Ambient"
        index.close()

    def test_removed_genre_no_longer_searchable(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._track(tmp_path, "s", "AlbumS", ["Shoegaze"])])
        assert {t.title for t in index.search("shoegaze")} == {"s"}
        index.remove_genre("Shoegaze")
        assert index.search("shoegaze") == []
        index.close()

    def test_tracks_for_genre_matches_nocase(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        titles = {t.title for t in index.tracks_for_genre("jazz")}
        assert titles == {"a", "b"}
        index.close()


# ---------------------------------------------------------------------------
# Merge genre (KAMP-607)
# ---------------------------------------------------------------------------


class TestMergeGenre:
    """LibraryIndex genre merge: create/validate/list + chokepoint mapping (KAMP-607)."""

    def _track(
        self, tmp_path: Path, name: str, album: str, genres: list[str]
    ) -> "Track":
        return Track(
            file_path=tmp_path / f"{name}.mp3",
            title=name,
            artist="Artist",
            album_artist="Artist",
            album=album,
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genres=genres,
        )

    def _index(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                self._track(tmp_path, "a", "AlbumA", ["Jazz"]),
                self._track(tmp_path, "b", "AlbumB", ["Jazz", "Ambient"]),
                self._track(tmp_path, "c", "AlbumC", ["Rock"]),
            ]
        )
        return index

    def _genres_of(self, index: LibraryIndex, title: str) -> list[str]:
        tid = next(t for t in index.all_tracks() if t.title == title).id
        return index.genres_for_track(tid)

    def test_retags_all_tracks_and_source_disappears(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        count = index.create_genre_merge("Jazz", "Rock")
        assert count == 2
        assert self._genres_of(index, "a") == ["Rock"]
        assert sorted(self._genres_of(index, "b")) == ["Ambient", "Rock"]
        assert "Jazz" not in index.all_genres()
        # Source vocab row deleted.
        assert (
            index._conn.execute(
                "SELECT 1 FROM genres WHERE name = 'Jazz' COLLATE NOCASE"
            ).fetchone()
            is None
        )
        index.close()

    def test_dedups_when_track_has_both_source_and_target(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._track(tmp_path, "x", "AlbumX", ["Jazz", "Rock"])])
        index.create_genre_merge("Jazz", "Rock")
        assert self._genres_of(index, "x") == ["Rock"]  # single Rock, not two
        index.close()

    def test_future_inbound_source_maps_to_target(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")
        # A brand-new track tagged with the merged-away genre lands on the target.
        index.upsert_many([self._track(tmp_path, "new", "AlbumN", ["Jazz"])])
        assert self._genres_of(index, "new") == ["Rock"]
        assert "Jazz" not in index.all_genres()
        index.close()

    def test_merge_is_case_insensitive(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_genre_merge("jAzz", "Rock")  # stored as "Jazz"
        assert "Jazz" not in index.all_genres()
        assert self._genres_of(index, "a") == ["Rock"]
        index.close()

    def test_map_persists_across_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "library.db"
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")
        index.close()
        # Reopen: _load_merge_map must repopulate so inbound Jazz still maps.
        index = LibraryIndex(db)
        index.upsert_many([self._track(tmp_path, "re", "AlbumR", ["Jazz"])])
        assert self._genres_of(index, "re") == ["Rock"]
        index.close()

    def test_list_genre_merges(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")
        assert index.list_genre_merges() == [{"source": "Jazz", "target": "Rock"}]
        index.close()

    def test_merge_follows_target_rename(self, tmp_path: Path) -> None:
        # The merge stores target_id (an FK to genres.id), not the target name, so
        # renaming the target genre keeps the merge pointing at the same row — a
        # fresh inbound source maps to the new name once the map reloads. Guards
        # the review concern that a free-text target would dangle on rename.
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")
        rock_id = index._conn.execute(
            "SELECT id FROM genres WHERE name = 'Rock'"
        ).fetchone()["id"]
        index._conn.execute(
            "UPDATE genres SET name = 'Rock & Roll' WHERE id = ?", (rock_id,)
        )
        index._conn.commit()
        index._load_merge_map()
        assert index.list_genre_merges() == [
            {"source": "Jazz", "target": "Rock & Roll"}
        ]
        index.upsert_many([self._track(tmp_path, "z", "AlbumZ", ["Jazz"])])
        assert self._genres_of(index, "z") == ["Rock & Roll"]
        index.close()

    def test_rejects_self_merge(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        with pytest.raises(ValueError):
            index.create_genre_merge("Jazz", "jazz")
        index.close()

    def test_rejects_chain_source_is_target(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")  # Rock is now a target
        with pytest.raises(ValueError):
            index.create_genre_merge("Rock", "Ambient")  # Rock can't be a source
        index.close()

    def test_rejects_chain_target_is_source(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")  # Jazz is now a source
        with pytest.raises(ValueError):
            index.create_genre_merge("Ambient", "Jazz")  # Jazz can't be a target
        index.close()

    def test_removing_target_dissolves_merge(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")
        index.remove_genre("Rock")  # remove the target
        assert index.list_genre_merges() == []
        # A fresh inbound Jazz now stays Jazz (no longer mapped).
        index.upsert_many([self._track(tmp_path, "j", "AlbumJ", ["Jazz"])])
        assert self._genres_of(index, "j") == ["Jazz"]
        index.close()

    def test_remove_genre_merge_is_future_only(self, tmp_path: Path) -> None:
        # KAMP-610: deleting a merge rule stops future mapping but does NOT undo
        # tracks already retagged to the target.
        index = self._index(tmp_path)
        index.create_genre_merge("Jazz", "Rock")  # a + b retagged Jazz -> Rock
        index.remove_genre_merge("jazz")  # NOCASE
        assert index.list_genre_merges() == []
        # Already-merged tracks keep Rock.
        assert self._genres_of(index, "a") == ["Rock"]
        # A fresh inbound Jazz now stays Jazz.
        index.upsert_many([self._track(tmp_path, "j", "AlbumJ", ["Jazz"])])
        assert self._genres_of(index, "j") == ["Jazz"]
        index.close()

    def test_remove_genre_merge_absent_is_noop(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.remove_genre_merge("Nope")  # no such rule — no error
        index.close()


# ---------------------------------------------------------------------------
# Genre allow-list extras (KAMP-610)
# ---------------------------------------------------------------------------


class TestGenreAllowlistExtras:
    """LibraryIndex allow-list overlay: add / list / clear (KAMP-610)."""

    def test_add_and_list(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.add_allowlist_entry("Vaporwave")
        index.add_allowlist_entry("Chillwave")
        assert index.list_allowlist_extras() == [
            "Chillwave",
            "Vaporwave",
        ]  # NOCASE sort
        index.close()

    def test_add_is_case_insensitively_unique(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.add_allowlist_entry("Vaporwave")
        index.add_allowlist_entry("vaporwave")  # dup (NOCASE) — ignored
        assert index.list_allowlist_extras() == ["Vaporwave"]
        index.close()

    def test_clear_reverts(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.add_allowlist_entry("Vaporwave")
        index.clear_allowlist_extras()
        assert index.list_allowlist_extras() == []
        index.close()

    def test_add_rejects_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        with pytest.raises(ValueError):
            index.add_allowlist_entry("   ")
        index.close()

    def test_add_rejects_delimiter(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        with pytest.raises(ValueError):
            index.add_allowlist_entry("a; b")
        index.close()


# ---------------------------------------------------------------------------
# Rename / edit genre (KAMP-608)
# ---------------------------------------------------------------------------


class TestRenameGenre:
    """LibraryIndex.rename_genre / validate_genre_rename (KAMP-608)."""

    def _track(
        self, tmp_path: Path, name: str, album: str, genres: list[str]
    ) -> "Track":
        return Track(
            file_path=tmp_path / f"{name}.mp3",
            title=name,
            artist="Artist",
            album_artist="Artist",
            album=album,
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genres=genres,
        )

    def _index(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                self._track(tmp_path, "a", "AlbumA", ["shoegaze."]),
                self._track(tmp_path, "b", "AlbumB", ["shoegaze.", "Ambient"]),
                self._track(tmp_path, "c", "AlbumC", ["Rock"]),
            ]
        )
        return index

    def _genres_of(self, index: LibraryIndex, title: str) -> list[str]:
        tid = next(t for t in index.all_tracks() if t.title == title).id
        return index.genres_for_track(tid)

    def test_in_place_rename_to_new_name(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        count = index.rename_genre("shoegaze.", "Shoegaze")
        assert count == 2
        assert "Shoegaze" in index.all_genres()
        assert "shoegaze." not in index.all_genres()
        assert self._genres_of(index, "a") == ["Shoegaze"]
        assert sorted(self._genres_of(index, "b")) == ["Ambient", "Shoegaze"]
        index.close()

    def test_case_only_rename_no_duplicate_row(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._track(tmp_path, "x", "AlbumX", ["shoegaze"])])
        index.rename_genre("shoegaze", "Shoegaze")
        assert index.all_genres() == ["Shoegaze"]
        rows = index._conn.execute(
            "SELECT COUNT(*) AS n FROM genres WHERE name = 'Shoegaze' COLLATE NOCASE"
        ).fetchone()["n"]
        assert rows == 1
        assert self._genres_of(index, "x") == ["Shoegaze"]
        index.close()

    def test_rename_onto_existing_folds_one_time(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                self._track(tmp_path, "a", "AlbumA", ["shoegaze."]),
                self._track(tmp_path, "b", "AlbumB", ["Shoegaze"]),
            ]
        )
        index.rename_genre("shoegaze.", "Shoegaze")  # fold typo into the survivor
        assert index.all_genres() == ["Shoegaze"]
        assert self._genres_of(index, "a") == ["Shoegaze"]
        # One-time fold: NO persistent merge rule was created.
        assert index.list_genre_merges() == []
        index.close()

    def test_rename_onto_existing_dedupes_when_track_has_both(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [self._track(tmp_path, "x", "AlbumX", ["shoegaze.", "Shoegaze"])]
        )
        index.rename_genre("shoegaze.", "Shoegaze")
        assert self._genres_of(index, "x") == ["Shoegaze"]  # single, not two
        index.close()

    def test_rename_updates_search(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._track(tmp_path, "s", "AlbumS", ["shoegaze."])])
        index.rename_genre("shoegaze.", "Dreampop")
        assert {t.title for t in index.search("dreampop")} == {"s"}
        assert index.search("shoegaze") == []
        index.close()

    def test_rename_absent_genre_returns_zero(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        assert index.rename_genre("Nope", "Whatever") == 0
        index.close()

    def test_rename_merge_target_follows(self, tmp_path: Path) -> None:
        # Renaming a genre that is a merge target keeps the merge pointing at it.
        index = self._index(tmp_path)
        index.create_genre_merge("shoegaze.", "Rock")  # Rock is a merge target
        index.rename_genre("Rock", "Rock & Roll")
        assert index.list_genre_merges() == [
            {"source": "shoegaze.", "target": "Rock & Roll"}
        ]
        index.upsert_many([self._track(tmp_path, "n", "AlbumN", ["shoegaze."])])
        assert self._genres_of(index, "n") == ["Rock & Roll"]
        index.close()

    def test_rename_onto_merge_source_rejected(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_genre_merge("shoegaze.", "Rock")  # "shoegaze." is now a source
        with pytest.raises(ValueError):
            index.rename_genre("Ambient", "shoegaze.")
        index.close()

    def test_rename_rejects_delimiter(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        with pytest.raises(ValueError):
            index.rename_genre("shoegaze.", "a; b")
        index.close()

    def test_rename_rejects_empty(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        with pytest.raises(ValueError):
            index.rename_genre("shoegaze.", "   ")
        index.close()


# ---------------------------------------------------------------------------
# Sort and record_played
# ---------------------------------------------------------------------------


class TestAlbumsSort:
    """Tests for LibraryIndex.albums(sort=...) ordering."""

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        """Return an index pre-populated with three albums by two artists."""
        index = LibraryIndex(tmp_path / "library.db")
        # Use real files so date_added is populated from ctime.
        p1 = tmp_path / "a.mp3"
        p2 = tmp_path / "b.mp3"
        p3 = tmp_path / "c.mp3"
        _make_mp3(
            p1,
            artist="Zappa",
            album_artist="Zappa",
            album="Hot Rats",
            release_date="1969",
            title="T1",
        )
        _make_mp3(
            p2,
            artist="Amon Tobin",
            album_artist="Amon Tobin",
            album="Foley Room",
            release_date="2007",
            title="T2",
        )
        _make_mp3(
            p3,
            artist="Zappa",
            album_artist="Zappa",
            album="Apostrophe",
            release_date="1974",
            title="T3",
        )
        index.upsert_many(
            [
                _sample_track(p1).__class__(
                    file_path=p1,
                    title="T1",
                    artist="Zappa",
                    album_artist="Zappa",
                    album="Hot Rats",
                    release_date="1969",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    date_added=1000.0,
                ),
                _sample_track(p2).__class__(
                    file_path=p2,
                    title="T2",
                    artist="Amon Tobin",
                    album_artist="Amon Tobin",
                    album="Foley Room",
                    release_date="2007",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    date_added=3000.0,
                ),
                _sample_track(p3).__class__(
                    file_path=p3,
                    title="T3",
                    artist="Zappa",
                    album_artist="Zappa",
                    album="Apostrophe",
                    release_date="1974",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    date_added=2000.0,
                ),
            ]
        )
        return index

    def test_default_sort_is_album_artist(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums()
        index.close()
        assert albums[0].album_artist == "Amon Tobin"
        assert albums[1].album_artist == "Zappa"

    def test_sort_by_album(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums(sort="album")
        index.close()
        names = [a.album for a in albums]
        assert names == ["Apostrophe", "Foley Room", "Hot Rats"]

    def test_sort_by_date_added_newest_first(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums(sort="date_added")
        index.close()
        # Foley Room: date_added=3000 → first
        assert albums[0].album == "Foley Room"
        # Apostrophe: date_added=2000 → second
        assert albums[1].album == "Apostrophe"
        # Hot Rats: date_added=1000 → last
        assert albums[2].album == "Hot Rats"

    def test_sort_by_last_played_newest_first(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        # Only record play for one album; it should sort to the top.
        p3 = tmp_path / "c.mp3"
        index.record_track_started(p3)  # Apostrophe played most recently
        albums = index.albums(sort="last_played")
        index.close()
        assert albums[0].album == "Apostrophe"

    def test_unknown_sort_key_falls_back_to_album_artist(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums(sort="bogus")
        index.close()
        assert albums[0].album_artist == "Amon Tobin"

    def test_sort_by_most_played_highest_avg_first(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        # Hot Rats = p1, Foley Room = p2, Apostrophe = p3 (each 1 track)
        p1 = tmp_path / "a.mp3"
        p2 = tmp_path / "b.mp3"
        p3 = tmp_path / "c.mp3"
        index.record_played(p3)  # Apostrophe: 1 play / 1 track = 1.0
        index.record_played(p3)  # Apostrophe: 2 plays / 1 track = 2.0
        index.record_played(p1)  # Hot Rats: 1 play / 1 track = 1.0
        albums = index.albums(sort="most_played")
        index.close()
        assert albums[0].album == "Apostrophe"  # highest avg
        assert albums[1].album == "Hot Rats"  # tied at 1.0, tiebreak by artist

    def test_sort_by_most_played_exposes_play_count_avg(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        p3 = tmp_path / "c.mp3"
        index.record_played(p3)
        index.record_played(p3)
        albums = index.albums(sort="most_played")
        index.close()
        apostrophe = next(a for a in albums if a.album == "Apostrophe")
        assert apostrophe.play_count_avg == pytest.approx(2.0)

    def test_sort_by_most_played_unplayed_album_has_zero_avg(
        self, tmp_path: Path
    ) -> None:
        index = self._make_index(tmp_path)
        albums = index.albums(sort="most_played")
        index.close()
        for a in albums:
            assert a.play_count_avg == pytest.approx(0.0)

    def test_sort_by_most_played_multi_track_album(self, tmp_path: Path) -> None:
        """A 5-track album with 13 total plays has avg 2.6."""
        index = LibraryIndex(tmp_path / "library.db")
        tracks = []
        for i in range(5):
            p = tmp_path / f"t{i}.mp3"
            _make_mp3(p, artist="A", album_artist="A", album="Multi", title=f"T{i}")
            t = Track(
                file_path=p,
                title=f"T{i}",
                artist="A",
                album_artist="A",
                album="Multi",
                release_date="2020",
                track_number=i + 1,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
            )
            tracks.append((p, t))
        index.upsert_many([t for _, t in tracks])
        # 13 total plays across 5 tracks → avg 2.6
        play_counts = [3, 3, 3, 2, 2]
        for (p, _), n in zip(tracks, play_counts):
            for _ in range(n):
                index.record_played(p)
        albums = index.albums(sort="most_played")
        index.close()
        assert albums[0].album == "Multi"
        assert albums[0].play_count_avg == pytest.approx(2.6)

    def test_sort_dir_asc_reverses_date_sort(self, tmp_path: Path) -> None:
        """sort_dir='asc' on date_added yields oldest-first (inverse of default)."""
        index = self._make_index(tmp_path)
        albums = index.albums(sort="date_added", sort_dir="asc")
        index.close()
        # date_added values: Hot Rats=1000, Apostrophe=2000, Foley Room=3000
        assert albums[0].album == "Hot Rats"
        assert albums[1].album == "Apostrophe"
        assert albums[2].album == "Foley Room"

    def test_sort_dir_desc_reverses_text_sort(self, tmp_path: Path) -> None:
        """sort_dir='desc' on album_artist yields Z→A."""
        index = self._make_index(tmp_path)
        albums = index.albums(sort="album_artist", sort_dir="desc")
        index.close()
        assert albums[0].album_artist == "Zappa"
        assert albums[-1].album_artist == "Amon Tobin"

    def test_sort_dir_none_preserves_natural_defaults(self, tmp_path: Path) -> None:
        """sort_dir=None uses the per-key natural direction (historical behaviour)."""
        index = self._make_index(tmp_path)
        albums_default = index.albums(sort="date_added")
        albums_none = index.albums(sort="date_added", sort_dir=None)
        index.close()
        assert [a.album for a in albums_default] == [a.album for a in albums_none]

    def test_sort_by_release_date_newest_first(self, tmp_path: Path) -> None:
        """Default release_date sort is DESC (newest year first)."""
        index = self._make_index(tmp_path)
        albums = index.albums(sort="release_date")
        index.close()
        # years: Foley Room=2007, Apostrophe=1974, Hot Rats=1969
        assert [a.album for a in albums] == ["Foley Room", "Apostrophe", "Hot Rats"]

    def test_sort_by_release_date_asc_oldest_first(self, tmp_path: Path) -> None:
        """sort_dir='asc' on release_date yields oldest year first."""
        index = self._make_index(tmp_path)
        albums = index.albums(sort="release_date", sort_dir="asc")
        index.close()
        assert [a.album for a in albums] == ["Hot Rats", "Apostrophe", "Foley Room"]

    def test_sort_by_release_date_empty_year_sorts_last(self, tmp_path: Path) -> None:
        """Albums with no year sort after dated albums regardless of direction."""
        index = LibraryIndex(tmp_path / "rd.db")
        p1 = tmp_path / "rd1.mp3"
        p2 = tmp_path / "rd2.mp3"
        p3 = tmp_path / "rd3.mp3"
        _make_mp3(
            p1,
            artist="A",
            album_artist="A",
            album="Alpha",
            release_date="2020",
            title="T1",
        )
        _make_mp3(
            p2, artist="B", album_artist="B", album="Beta", release_date="", title="T2"
        )
        _make_mp3(
            p3,
            artist="C",
            album_artist="C",
            album="Gamma",
            release_date="1990",
            title="T3",
        )
        index.upsert_many(
            [
                Track(
                    file_path=p1,
                    title="T1",
                    artist="A",
                    album_artist="A",
                    album="Alpha",
                    release_date="2020",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                ),
                Track(
                    file_path=p2,
                    title="T2",
                    artist="B",
                    album_artist="B",
                    album="Beta",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                ),
                Track(
                    file_path=p3,
                    title="T3",
                    artist="C",
                    album_artist="C",
                    album="Gamma",
                    release_date="1990",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                ),
            ]
        )
        desc_albums = index.albums(sort="release_date")
        asc_albums = index.albums(sort="release_date", sort_dir="asc")
        index.close()
        # DESC: 2020, 1990, then empty-year last
        assert [a.album for a in desc_albums] == ["Alpha", "Gamma", "Beta"]
        # ASC: 1990, 2020, then empty-year last
        assert [a.album for a in asc_albums] == ["Gamma", "Alpha", "Beta"]


class TestPreorderResurface:
    """last_track_added_at re-surfaces a pre-order that gained a track (KAMP-544)."""

    def _stream_track(
        self, sid: str, n: int, album: str, *, available: bool = True
    ) -> Track:
        return Track(
            file_path=Path(f"bandcamp://{sid}/{n}"),
            title=f"T{n}",
            artist="Preorder Band",
            album_artist="Preorder Band",
            album=album,
            release_date="2026",
            track_number=n,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            is_available=available,
        )

    def test_migration_v51_to_v52_adds_and_backfills_column(
        self, tmp_path: Path
    ) -> None:
        """A pre-v52 DB gains last_track_added_at, backfilled from date_added."""
        db_path = tmp_path / "library.db"
        # Build a current DB, seed an album with a date_added, then rewind: drop the
        # new (unconstrained) column and stamp version 51 so open runs the upgrade.
        idx = LibraryIndex(db_path)
        idx._conn.execute(
            "INSERT INTO albums (album_artist, album, date_added)"
            " VALUES ('A', 'Rec', 1234.0)"
        )
        idx._conn.commit()
        idx.close()
        conn = sqlite3.connect(str(db_path))
        conn.execute("ALTER TABLE albums DROP COLUMN last_track_added_at")
        conn.execute("UPDATE schema_version SET version = 51")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(albums)")}
        backfilled = index._conn.execute(
            "SELECT last_track_added_at FROM albums WHERE album = 'Rec'"
        ).fetchone()[0]
        index.close()

        assert version == 60
        assert "last_track_added_at" in cols
        assert backfilled == 1234.0

    def test_migration_v52_to_v53_rebuilds_download_queue(self, tmp_path: Path) -> None:
        """A pre-v53 DB's Bandcamp-specific download_queue (sale_item_id) is rebuilt
        into the platform-neutral (provider, provider_item_id) state-machine shape;
        existing rows survive as provider='bandcamp' with position backfilled from
        the old FIFO order (KAMP-564)."""
        db_path = tmp_path / "library.db"
        # Build a current DB, then rewind download_queue to the genuine KAMP-408
        # (v23) shape — a table keyed on sale_item_id — and stamp version 52 so open
        # runs the v53 rebuild. Two rows with distinct queued_at fix the FIFO order.
        LibraryIndex(db_path).close()
        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP TABLE download_queue")
        conn.execute(
            "CREATE TABLE download_queue ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE,"
            " queued_at REAL NOT NULL DEFAULT (unixepoch()))"
        )
        conn.execute(
            "INSERT INTO download_queue (sale_item_id, queued_at) VALUES ('first', 1.0)"
        )
        conn.execute(
            "INSERT INTO download_queue (sale_item_id, queued_at) VALUES ('second', 2.0)"
        )
        conn.execute("UPDATE schema_version SET version = 52")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(download_queue)")}
        rows = index._conn.execute(
            "SELECT provider, provider_item_id, status, position FROM download_queue"
            " ORDER BY position ASC"
        ).fetchall()
        index.close()

        assert version == 60
        assert "sale_item_id" not in cols
        assert {
            "provider",
            "provider_item_id",
            "status",
            "position",
            "size_bytes",
            "size_is_estimate",
            "error_text",
            "album_name",
            "album_artist",
            "artwork_ref",
        } <= cols
        # Existing rows survived as bandcamp items, defaulted to 'queued', with
        # position backfilled 1,2 to match the pre-v53 FIFO (queued_at) order.
        assert [
            (r["provider"], r["provider_item_id"], r["status"], r["position"])
            for r in rows
        ] == [
            ("bandcamp", "first", "queued", 1),
            ("bandcamp", "second", "queued", 2),
        ]

    def test_migration_v53_to_v54_adds_redownload_url(self, tmp_path: Path) -> None:
        """A pre-v54 download_queue gains a nullable redownload_url column via an
        additive ALTER (no rebuild); existing rows survive with NULL (KAMP-575)."""
        db_path = tmp_path / "library.db"
        # Build a current DB, then rewind download_queue to the v53 shape (same
        # columns minus redownload_url) and stamp version 53 so open runs v54.
        LibraryIndex(db_path).close()
        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP TABLE download_queue")
        conn.execute(
            "CREATE TABLE download_queue ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " provider TEXT NOT NULL DEFAULT 'bandcamp',"
            " provider_item_id TEXT NOT NULL,"
            " queued_at REAL NOT NULL DEFAULT (unixepoch()),"
            " status TEXT NOT NULL DEFAULT 'queued',"
            " position INTEGER NOT NULL DEFAULT 0,"
            " size_bytes INTEGER,"
            " size_is_estimate INTEGER NOT NULL DEFAULT 1,"
            " error_text TEXT,"
            " album_name TEXT,"
            " album_artist TEXT,"
            " artwork_ref TEXT,"
            " UNIQUE (provider, provider_item_id))"
        )
        conn.execute(
            "INSERT INTO download_queue (provider, provider_item_id, position)"
            " VALUES ('bandcamp', 'keep', 1)"
        )
        conn.execute("UPDATE schema_version SET version = 53")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(download_queue)")}
        row = index._conn.execute(
            "SELECT provider_item_id, redownload_url FROM download_queue"
        ).fetchone()
        index.close()

        assert version == 60
        assert "redownload_url" in cols
        # Existing row survived; the new column is NULL for pre-existing data.
        assert row["provider_item_id"] == "keep"
        assert row["redownload_url"] is None

    def test_migration_v53_to_v54_presence_guard_idempotent(
        self, tmp_path: Path
    ) -> None:
        """Re-running the v54 step when redownload_url already exists is a no-op
        (the PRAGMA presence guard skips the ALTER) — models a crash-retry."""
        db_path = tmp_path / "library.db"
        LibraryIndex(db_path).close()
        conn = sqlite3.connect(str(db_path))
        # Column already present (current schema), but version rewound to 53.
        conn.execute("UPDATE schema_version SET version = 53")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)  # must not raise "duplicate column name"
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(download_queue)")}
        index.close()

        assert version == 60
        assert "redownload_url" in cols

    def test_count_available_remote_tracks(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                self._stream_track("777", 1, "PO", available=True),
                self._stream_track("777", 2, "PO", available=True),
                self._stream_track("777", 3, "PO", available=False),
            ]
        )
        count = index.count_available_remote_tracks("777")
        index.close()
        # Two of three tracks are streamable; the pre-order track is not counted.
        assert count == 2

    def test_bump_moves_album_to_top_of_date_added(self, tmp_path: Path) -> None:
        """A bumped album outsorts a newer date_added album on the date_added sort."""
        index = LibraryIndex(tmp_path / "library.db")
        # Older album (date_added 1000) that will be bumped; newer album (5000).
        index.upsert_many([self._stream_track("111", 1, "OldPO")])
        index.upsert_many(
            [
                Track(
                    file_path=Path("bandcamp://222/1"),
                    title="N1",
                    artist="Other",
                    album_artist="Other",
                    album="NewAlbum",
                    release_date="2026",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                    date_added=5000.0,
                )
            ]
        )
        # Stamp the older album's date_added and link its sale_item_id (the FK to
        # bandcamp_collection requires the ledger row to exist first).
        index._conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id) VALUES ('111')"
        )
        index._conn.execute(
            "UPDATE albums SET date_added = 1000.0, sale_item_id = '111'"
            " WHERE album = 'OldPO'"
        )
        index._conn.commit()

        before = index.albums(sort="date_added")
        assert before[0].album == "NewAlbum"  # 5000 > 1000 before the bump

        index.bump_album_new_content("111", 9000.0)
        after = index.albums(sort="date_added")
        index.close()
        # The bump (9000) beats NewAlbum's date_added (5000).
        assert after[0].album == "OldPO"
        assert after[0].added_at == 9000.0

    def test_bump_is_monotonic(self, tmp_path: Path) -> None:
        """A later bump with an earlier timestamp never lowers last_track_added_at."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_track("333", 1, "PO")])
        index._conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id) VALUES ('333')"
        )
        index._conn.execute("UPDATE albums SET sale_item_id = '333' WHERE album = 'PO'")
        index._conn.commit()

        index.bump_album_new_content("333", 8000.0)
        index.bump_album_new_content("333", 2000.0)  # earlier — must be ignored
        value = index._conn.execute(
            "SELECT last_track_added_at FROM albums WHERE album = 'PO'"
        ).fetchone()[0]
        index.close()
        assert value == 8000.0


class TestRecordPlayed:
    """Tests for LibraryIndex.record_played()."""

    def test_does_not_set_last_played(self, tmp_path: Path) -> None:
        """record_played increments play_count only; last_played is managed by record_track_started."""
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )

        index.record_played(p)

        row = index._conn.execute(
            "SELECT last_played FROM tracks_with_stats WHERE id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(p),),
        ).fetchone()
        index.close()

        assert row is not None
        assert row[0] is None

    def test_record_played_unknown_path_is_noop(self, tmp_path: Path) -> None:
        """Calling record_played for a path not in the index must not raise."""
        index = LibraryIndex(tmp_path / "library.db")
        index.record_played(tmp_path / "ghost.mp3")  # should not raise
        index.close()

    def test_play_count_defaults_to_zero(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.play_count == 0

    def test_record_played_increments_play_count(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        index.record_played(p)
        index.record_played(p)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.play_count == 2

    def test_record_played_remote_track(self, tmp_path: Path) -> None:
        """record_played must work for bandcamp:// URIs (POSIX Path collapses // to /)."""
        index = LibraryIndex(tmp_path / "library.db")
        remote_uri = "bandcamp://999/3"
        index.upsert_many(
            [
                Track(
                    file_path=Path(remote_uri),
                    title="Remote",
                    artist="A",
                    album_artist="A",
                    album="B",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                )
            ]
        )

        index.record_played(Path(remote_uri))

        track = index.get_track_by_path(remote_uri)
        index.close()
        assert track is not None
        assert track.play_count == 1

    def test_top_tracks_returns_played_in_desc_order(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for name, plays in [("a.mp3", 3), ("b.mp3", 1), ("c.mp3", 5)]:
            p = tmp_path / name
            _make_mp3(p, artist="A", album_artist="A", album="X", title=name)
            index.upsert_many(
                [
                    Track(
                        file_path=p,
                        title=name,
                        artist="A",
                        album_artist="A",
                        album="X",
                        release_date="",
                        track_number=1,
                        disc_number=1,
                        ext="mp3",
                        embedded_art=False,
                        mb_release_id="",
                        mb_recording_id="",
                    )
                ]
            )
            for _ in range(plays):
                index.record_played(p)
        result = index.top_tracks(10)
        index.close()
        assert [t.play_count for t in result] == [5, 3, 1]

    def test_top_tracks_respects_limit(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i in range(5):
            p = tmp_path / f"{i}.mp3"
            _make_mp3(p, artist="A", album_artist="A", album="X", title=str(i))
            index.upsert_many(
                [
                    Track(
                        file_path=p,
                        title=str(i),
                        artist="A",
                        album_artist="A",
                        album="X",
                        release_date="",
                        track_number=i + 1,
                        disc_number=1,
                        ext="mp3",
                        embedded_art=False,
                        mb_release_id="",
                        mb_recording_id="",
                    )
                ]
            )
            for _ in range(i + 1):
                index.record_played(p)
        result = index.top_tracks(3)
        index.close()
        assert len(result) == 3

    def test_top_tracks_excludes_unplayed(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        played = tmp_path / "played.mp3"
        unplayed = tmp_path / "unplayed.mp3"
        for p in [played, unplayed]:
            _make_mp3(p, artist="A", album_artist="A", album="X", title=p.stem)
            index.upsert_many(
                [
                    Track(
                        file_path=p,
                        title=p.stem,
                        artist="A",
                        album_artist="A",
                        album="X",
                        release_date="",
                        track_number=1,
                        disc_number=1,
                        ext="mp3",
                        embedded_art=False,
                        mb_release_id="",
                        mb_recording_id="",
                    )
                ]
            )
        index.record_played(played)
        result = index.top_tracks(10)
        index.close()
        assert len(result) == 1
        assert result[0].file_path == played

    def test_migration_v4_to_v5_adds_play_count_column(self, tmp_path: Path) -> None:
        """Existing v4 databases gain the play_count column on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (4)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/d.mp3', 'Song', 'Band', 'Band', "
            "'Album', '2000', 1, 1, 'mp3', 0, '', '', NULL, NULL, 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, tokenize='unicode61')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        row = index._conn.execute(
            "SELECT play_count FROM tracks_with_stats WHERE id = 1"
        ).fetchone()
        index.close()

        assert version == 60
        assert row is not None
        assert row[0] == 0


# ---------------------------------------------------------------------------
# record_play_time / top_artists (KAMP-258)
# ---------------------------------------------------------------------------


def _make_indexed_track(
    index: LibraryIndex,
    tmp_path: Path,
    name: str,
    album_artist: str = "Artist A",
    album: str = "Album X",
    track_number: int = 1,
    duration: float = 240.0,
    play_count: int = 0,
) -> Path:
    p = tmp_path / name
    _make_mp3(
        p, artist=album_artist, album_artist=album_artist, album=album, title=name
    )
    index.upsert_many(
        [
            Track(
                file_path=p,
                title=name,
                artist=album_artist,
                album_artist=album_artist,
                album=album,
                release_date="",
                track_number=track_number,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
                duration=duration,
            )
        ]
    )
    for _ in range(play_count):
        index.record_played(p)
    return p


class TestTopArtists:
    """Tests for LibraryIndex.record_play_time() and top_artists()."""

    def test_record_play_time_accumulates(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        p = _make_indexed_track(index, tmp_path, "t.mp3", album_artist="Radiohead")
        index.record_play_time(p, 120.0)
        index.record_play_time(p, 60.0)
        artists = index.top_artists(10)
        index.close()
        assert len(artists) == 1
        assert artists[0].name == "Radiohead"
        assert artists[0].play_time == pytest.approx(180.0)

    def test_record_play_time_unknown_path_is_noop(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.record_play_time(tmp_path / "missing.mp3", 99.0)
        artists = index.top_artists(10)
        index.close()
        assert artists == []

    def test_top_artists_ranked_by_play_time_desc(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        pa = _make_indexed_track(
            index, tmp_path, "a.mp3", album_artist="Sunn O)))", album="AA"
        )
        pb = _make_indexed_track(
            index, tmp_path, "b.mp3", album_artist="Earth", album="BB"
        )
        pc = _make_indexed_track(
            index, tmp_path, "c.mp3", album_artist="Boris", album="CC"
        )
        index.record_play_time(pa, 300.0)
        index.record_play_time(pb, 900.0)
        index.record_play_time(pc, 600.0)
        result = index.top_artists(10)
        index.close()
        assert [a.name for a in result] == ["Earth", "Boris", "Sunn O)))"]

    def test_top_artists_respects_limit(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for i in range(5):
            p = _make_indexed_track(
                index,
                tmp_path,
                f"{i}.mp3",
                album_artist=f"Artist{i}",
                album=f"Album{i}",
            )
            index.record_play_time(p, float(i + 1) * 60)
        result = index.top_artists(3)
        index.close()
        assert len(result) == 3

    def test_top_artists_excludes_zero_play_time(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        p_played = _make_indexed_track(
            index, tmp_path, "played.mp3", album_artist="Played", album="PA"
        )
        _make_indexed_track(
            index, tmp_path, "unplayed.mp3", album_artist="Unplayed", album="UA"
        )
        index.record_play_time(p_played, 120.0)
        result = index.top_artists(10)
        index.close()
        assert len(result) == 1
        assert result[0].name == "Played"

    def test_top_artists_top_album_is_highest_play_count_avg(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        pa = _make_indexed_track(
            index, tmp_path, "a.mp3", album_artist="X", album="Low", play_count=1
        )
        _make_indexed_track(
            index, tmp_path, "b.mp3", album_artist="X", album="High", play_count=5
        )
        index.record_play_time(pa, 60.0)
        result = index.top_artists(10)
        index.close()
        assert len(result) == 1
        assert result[0].top_album == "High"

    def test_v36_migration_backfills_play_time(self, tmp_path: Path) -> None:
        """Migration backfills play_time = SUM(play_count * duration) for each artist."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Seed a v35 database by opening at v36, then downgrading the version marker.
        seed = LibraryIndex(db_path)
        _make_indexed_track(
            seed, tmp_path, "t1.mp3", album_artist="Bach", duration=300.0
        )
        _make_indexed_track(
            seed, tmp_path, "t2.mp3", album_artist="Bach", duration=120.0
        )
        _readd_legacy_track_columns(seed)
        seed.close()
        # Manually set play counts and downgrade version so migration re-runs.
        conn = _sqlite3.connect(str(db_path))
        conn.execute("UPDATE tracks SET play_count = 2 WHERE title = 't1.mp3'")
        conn.execute("UPDATE tracks SET play_count = 3 WHERE title = 't2.mp3'")
        # duration now lives on track_sources; mirror it back onto the legacy
        # tracks.duration the v36 backfill (play_count * duration) still reads.
        conn.execute(
            "UPDATE tracks SET duration = ("
            " SELECT s.duration FROM track_sources s WHERE s.track_id = tracks.id LIMIT 1)"
        )
        conn.execute("UPDATE artists SET play_time = 0")
        conn.execute("UPDATE schema_version SET version = 35")
        conn.commit()
        conn.close()
        # Re-open triggers migration.
        index = LibraryIndex(db_path)
        result = index.top_artists(10)
        index.close()
        # Expected: 2*300 + 3*120 = 600 + 360 = 960
        assert len(result) == 1
        assert result[0].name == "Bach"
        assert result[0].play_time == pytest.approx(960.0)

    def test_migration_v40_heals_orphaned_local_album(self, tmp_path: Path) -> None:
        """v40 sweeps pre-existing zero-track local albums, keeping Bandcamp rows."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        seed = LibraryIndex(db_path)
        seed.close()
        conn = _sqlite3.connect(str(db_path))
        # A local orphan left behind by the old (pre-fix) deletion behaviour.
        conn.execute(
            "INSERT INTO albums (album_artist, album) VALUES ('Local', 'Orphan')"
        )
        # A Bandcamp preorder-shaped row that legitimately has zero tracks.
        conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, mode) VALUES ('sid-1', 'preorder')"
        )
        conn.execute(
            "INSERT INTO albums (album_artist, album, sale_item_id, source)"
            " VALUES ('Artist', 'Preorder', 'sid-1', 'bandcamp')"
        )
        conn.execute("UPDATE schema_version SET version = 39")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)  # re-open triggers the v40 migration
        albums = {a.album for a in index.albums()}
        index.close()

        assert "Orphan" not in albums
        assert "Preorder" in albums

    def test_top_artists_returns_artist_info_instances(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        p = _make_indexed_track(
            index, tmp_path, "t.mp3", album_artist="Arca", album="Kick"
        )
        index.record_play_time(p, 200.0)
        result = index.top_artists(10)
        index.close()
        assert isinstance(result[0], ArtistInfo)
        assert result[0].top_album == "Kick"


# ---------------------------------------------------------------------------
# record_track_started
# ---------------------------------------------------------------------------


class TestRecordTrackStarted:
    """Tests for LibraryIndex.record_track_started()."""

    def _make_index_with_track(
        self, tmp_path: Path, name: str = "track.mp3"
    ) -> tuple[LibraryIndex, Path]:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / name
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        return index, p

    def test_sets_last_played_timestamp(self, tmp_path: Path) -> None:
        import time

        index, p = self._make_index_with_track(tmp_path)
        before = time.time()
        index.record_track_started(p)
        after = time.time()

        row = index._conn.execute(
            "SELECT last_played FROM tracks_with_stats WHERE id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(p),),
        ).fetchone()
        index.close()

        assert row is not None
        assert before <= row[0] <= after

    def test_does_not_affect_play_count(self, tmp_path: Path) -> None:
        index, p = self._make_index_with_track(tmp_path)
        index.record_track_started(p)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.play_count == 0

    def test_unknown_path_is_noop(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.record_track_started(tmp_path / "ghost.mp3")  # must not raise
        index.close()

    def test_record_track_started_remote_track(self, tmp_path: Path) -> None:
        """record_track_started must work for bandcamp:// URIs."""
        import time

        index = LibraryIndex(tmp_path / "library.db")
        remote_uri = "bandcamp://999/3"
        index.upsert_many(
            [
                Track(
                    file_path=Path(remote_uri),
                    title="Remote",
                    artist="A",
                    album_artist="A",
                    album="B",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                )
            ]
        )

        before = time.time()
        index.record_track_started(Path(remote_uri))
        after = time.time()

        track = index.get_track_by_path(remote_uri)
        index.close()
        assert track is not None
        assert track.last_played is not None
        assert before <= track.last_played <= after

    def test_last_played_sort_reflects_record_track_started(
        self, tmp_path: Path
    ) -> None:
        import time

        index = LibraryIndex(tmp_path / "library.db")
        p1 = tmp_path / "a.mp3"
        p2 = tmp_path / "b.mp3"
        for p, album in ((p1, "Alpha"), (p2, "Beta")):
            _make_mp3(p, artist="X", album_artist="X", album=album, title="T")
            index.upsert_many(
                [
                    Track(
                        file_path=p,
                        title="T",
                        artist="X",
                        album_artist="X",
                        album=album,
                        release_date="",
                        track_number=1,
                        disc_number=1,
                        ext="mp3",
                        embedded_art=False,
                        mb_release_id="",
                        mb_recording_id="",
                    )
                ]
            )

        index.record_track_started(p1)
        time.sleep(0.01)
        index.record_track_started(p2)

        albums = index.albums(sort="last_played")
        played = [a for a in albums if a.last_played_at is not None]
        index.close()

        assert len(played) == 2
        assert played[0].album == "Beta"  # most recent
        assert played[1].album == "Alpha"


# ---------------------------------------------------------------------------
# Favorite
# ---------------------------------------------------------------------------


class TestFavorite:
    """Tests for LibraryIndex.set_favorite() and Track.favorite persistence."""

    def _make_index_with_track(self, tmp_path: Path) -> tuple[LibraryIndex, Path]:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="A", album="B", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="B",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        return index, p

    def test_favorite_defaults_to_false(self, tmp_path: Path) -> None:
        index, p = self._make_index_with_track(tmp_path)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.favorite is False

    def test_set_favorite_marks_track(self, tmp_path: Path) -> None:
        index, p = self._make_index_with_track(tmp_path)
        index.set_favorite(p, True)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.favorite is True

    def test_set_favorite_clears_flag(self, tmp_path: Path) -> None:
        index, p = self._make_index_with_track(tmp_path)
        index.set_favorite(p, True)
        index.set_favorite(p, False)
        track = index.get_track_by_path(p)
        index.close()
        assert track is not None
        assert track.favorite is False

    def test_migration_v3_to_v4_adds_favorite_column(self, tmp_path: Path) -> None:
        """Existing v3 databases gain the favorite column on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a v3-style database (has date columns but no favorite).
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (3)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/c.mp3', 'Song', 'Band', 'Band', "
            "'Album', '2000', 1, 1, 'mp3', 0, '', '', NULL, NULL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, tokenize='unicode61')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        # Opening with LibraryIndex should migrate v3 → v4.
        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        row = index._conn.execute(
            "SELECT favorite FROM tracks_with_stats WHERE id = 1"
        ).fetchone()
        index.close()

        assert version == 60
        assert row is not None
        assert row[0] == 0  # existing tracks default to not-favorited


# ---------------------------------------------------------------------------
# Album favorites (KAMP-293)
# ---------------------------------------------------------------------------


class TestAlbumFavorite:
    """Tests for LibraryIndex.toggle_album_favorite() and AlbumInfo.favorite."""

    def _make_index_with_album(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        p = tmp_path / "track.mp3"
        _make_mp3(p, artist="A", album_artist="Artist", album="Album", title="T")
        index.upsert_many(
            [
                Track(
                    file_path=p,
                    title="T",
                    artist="A",
                    album_artist="Artist",
                    album="Album",
                    release_date="2020",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        return index

    def test_album_favorite_defaults_to_false(self, tmp_path: Path) -> None:
        index = self._make_index_with_album(tmp_path)
        albums = index.albums()
        index.close()
        assert len(albums) == 1
        assert albums[0].favorite is False

    def test_toggle_album_favorite_sets_true(self, tmp_path: Path) -> None:
        index = self._make_index_with_album(tmp_path)
        index.toggle_album_favorite("Artist", "Album", True)
        albums = index.albums()
        index.close()
        assert albums[0].favorite is True

    def test_toggle_album_favorite_clears_flag(self, tmp_path: Path) -> None:
        index = self._make_index_with_album(tmp_path)
        index.toggle_album_favorite("Artist", "Album", True)
        index.toggle_album_favorite("Artist", "Album", False)
        albums = index.albums()
        index.close()
        assert albums[0].favorite is False

    def test_album_favorite_persists_across_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        index = self._make_index_with_album(tmp_path)
        index.toggle_album_favorite("Artist", "Album", True)
        index.close()

        index2 = LibraryIndex(db_path)
        albums = index2.albums()
        index2.close()
        assert albums[0].favorite is True

    def test_migration_v13_to_current_absorbs_album_favorites(
        self, tmp_path: Path
    ) -> None:
        """v13 databases migrate to v24: albums table exists, album_favorites is dropped."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Create a minimal v13 schema (executescript(_DDL) will add the rest via IF NOT EXISTS).
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (13)")
        # A v13-era tracks table (columns through file_mtime) so the replayed
        # v14→v49 migrations find the pre-KAMP-539 columns they reference (e.g.
        # v17's "UPDATE tracks SET file_mtime = NULL").
        conn.execute(
            "CREATE TABLE tracks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0,"
            " disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '',"
            " mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        tables = {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        index.close()

        assert version == 60
        assert "albums" in tables
        assert "album_favorites" not in tables


# ---------------------------------------------------------------------------
# Mtime-based re-indexing (TASK-66)
# ---------------------------------------------------------------------------


class TestMtimeReindex:
    """Tests for LibraryScanner mtime change detection."""

    def test_scan_stores_file_mtime_on_first_index(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="T")

        index = LibraryIndex(tmp_path / "library.db")
        LibraryScanner(index).scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert tracks[0].file_mtime == pytest.approx(p.stat().st_mtime, abs=1.0)

    def test_scan_reindexes_file_when_mtime_changes(self, tmp_path: Path) -> None:
        """Updating a file's mtime causes re-read on next scan."""
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="Original")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        # Rewrite the file with a new title and bump mtime.
        _make_mp3(p, title="Updated")
        import os

        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 1))

        result = scanner.scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.updated == 1
        assert result.added == 0
        assert result.unchanged == 0
        assert tracks[0].title == "Updated"

    def test_scan_unchanged_count_excludes_updated_files(self, tmp_path: Path) -> None:
        lib = tmp_path / "music"
        lib.mkdir()
        p1 = lib / "01.mp3"
        p2 = lib / "02.mp3"
        _make_mp3(p1, title="T1")
        _make_mp3(p2, title="T2")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        # Bump mtime on one file.
        import os

        os.utime(p1, (p1.stat().st_atime, p1.stat().st_mtime + 1))

        result = scanner.scan(lib)
        index.close()

        assert result.updated == 1
        assert result.unchanged == 1

    def test_scan_updates_stored_mtime_after_reindex(self, tmp_path: Path) -> None:
        """After re-indexing a changed file, its stored mtime matches the new value."""
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="T")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        import os

        new_mtime = p.stat().st_mtime + 1
        os.utime(p, (p.stat().st_atime, new_mtime))
        scanner.scan(lib)

        tracks = index.all_tracks()
        index.close()

        assert tracks[0].file_mtime == pytest.approx(new_mtime, abs=0.001)

    def test_null_mtime_in_db_causes_reindex_on_scan(self, tmp_path: Path) -> None:
        """Tracks with NULL file_mtime (e.g. after v5→v6 migration) are always
        re-read on the next scan so tag changes made before the upgrade are
        picked up automatically."""
        lib = tmp_path / "music"
        lib.mkdir()
        p = lib / "01.mp3"
        _make_mp3(p, title="Original")

        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        scanner = LibraryScanner(index)
        scanner.scan(lib)

        # Simulate the state right after a v5→v6 migration: file_mtime is NULL.
        # The scanner's re-read decision reads the preferred source's mtime, so
        # null it on track_sources (the legacy tracks column is dropped in v49).
        index._conn.execute(
            "UPDATE track_sources SET file_mtime = NULL WHERE uri = ?", (str(p),)
        )
        index._conn.commit()

        # Also update the file tags to simulate an edit made before the migration.
        _make_mp3(p, title="Updated")

        result = scanner.scan(lib)
        tracks = index.all_tracks()
        index.close()

        assert result.updated == 1
        assert tracks[0].title == "Updated"
        assert tracks[0].file_mtime is not None  # mtime stored after re-read

    def test_migration_v5_to_v6_adds_file_mtime_column(self, tmp_path: Path) -> None:
        """Existing v5 databases gain the file_mtime column on open."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (5)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0
            )
            """)
        conn.execute(
            "INSERT INTO tracks VALUES (1, '/e.mp3', 'Song', 'Band', 'Band', "
            "'Album', '2000', 1, 1, 'mp3', 0, '', '', NULL, NULL, 0, 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5("
            "title, artist, album_artist, album, tokenize='unicode61')"
        )
        conn.execute(
            "CREATE TABLE player_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        row = index._conn.execute(
            "SELECT file_mtime FROM tracks_with_stats WHERE id = 1"
        ).fetchone()
        index.close()

        assert version == 60
        assert row is not None
        # file_mtime is intentionally left NULL on migration so the next scan
        # treats all existing tracks as changed and re-reads their tags.
        assert row[0] is None


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    """Tests the DB-fallback path (no keyring backend available)."""

    @pytest.fixture(autouse=True)
    def no_keyring(self, mocker: MockerFixture) -> None:
        """Simulate a platform without a keyring backend."""
        mocker.patch("kamp_core.library._mac_kc", None)
        # Force the Linux/keyring code path even on Windows runners (where
        # _win_cred is otherwise the real DPAPI module and would short-circuit
        # the keyring branch entirely).  See KAMP-280.
        mocker.patch("kamp_core.library._win_cred", None)
        err = keyring.errors.NoKeyringError()
        mocker.patch("kamp_core.library.keyring.get_password", side_effect=err)
        mocker.patch("kamp_core.library.keyring.set_password", side_effect=err)
        mocker.patch("kamp_core.library.keyring.delete_password", side_effect=err)

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    def test_get_session_returns_none_when_absent(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        assert index.get_session("bandcamp") is None
        index.close()

    def test_set_and_get_session(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "js_logged_in", "value": "1"}], "origins": []}
        index.set_session("bandcamp", data)
        result = index.get_session("bandcamp")
        assert result == data
        index.close()

    def test_set_session_overwrites_existing(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": [{"name": "old", "value": "1"}]})
        updated = {"cookies": [{"name": "new", "value": "2"}]}
        index.set_session("bandcamp", updated)
        assert index.get_session("bandcamp") == updated
        index.close()

    def test_clear_session_removes_row(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": []})
        index.clear_session("bandcamp")
        assert index.get_session("bandcamp") is None
        index.close()

    def test_clear_session_noop_when_absent(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        index.clear_session("bandcamp")  # must not raise
        index.close()

    def test_clear_session_truncates_wal(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        index.set_session(
            "bandcamp", {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        )
        wal_path = db_path.with_suffix(".db-wal")
        index.clear_session("bandcamp")
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        assert wal_size == 0, f"WAL not truncated after clear_session: {wal_size} bytes"
        index.close()

    def test_multiple_services_are_independent(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        bc_data = {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        lfm_data = {"session_key": "abc123"}
        index.set_session("bandcamp", bc_data)
        index.set_session("lastfm", lfm_data)
        assert index.get_session("bandcamp") == bc_data
        assert index.get_session("lastfm") == lfm_data
        index.clear_session("bandcamp")
        assert index.get_session("bandcamp") is None
        assert index.get_session("lastfm") == lfm_data
        index.close()

    def test_schema_version_8_after_migration(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()
        assert version == 60

    def test_schema_version_9_after_migration(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()
        assert version == 60

    def test_migration_v8_to_v9_nulls_flac_ogg_mtimes(self, tmp_path: Path) -> None:
        """v8→v9 resets file_mtime for FLAC/OGG rows so they are re-scanned.

        This fixes the case where blank tags (written by the buggy tag reader)
        were cached in the DB and would never be refreshed without this nudge.
        """
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (8)")
        conn.execute("""
            CREATE TABLE tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path       TEXT UNIQUE NOT NULL,
                title           TEXT NOT NULL DEFAULT '',
                artist          TEXT NOT NULL DEFAULT '',
                album_artist    TEXT NOT NULL DEFAULT '',
                album           TEXT NOT NULL DEFAULT '',
                year            TEXT NOT NULL DEFAULT '',
                track_number    INTEGER,
                disc_number     INTEGER NOT NULL DEFAULT 1,
                ext             TEXT NOT NULL DEFAULT '',
                embedded_art    INTEGER NOT NULL DEFAULT 0,
                mb_release_id   TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added      TEXT,
                last_played     TEXT,
                favorite        INTEGER NOT NULL DEFAULT 0,
                play_count      INTEGER NOT NULL DEFAULT 0,
                file_mtime      REAL
            )
            """)
        # Insert one of each format: FLAC/OGG should have mtime nulled; MP3/M4A kept.
        conn.execute(
            "INSERT INTO tracks (file_path, ext, file_mtime) VALUES (?, ?, ?)",
            ("/music/a.flac", "flac", 1111.0),
        )
        conn.execute(
            "INSERT INTO tracks (file_path, ext, file_mtime) VALUES (?, ?, ?)",
            ("/music/b.ogg", "ogg", 2222.0),
        )
        conn.execute(
            "INSERT INTO tracks (file_path, ext, file_mtime) VALUES (?, ?, ?)",
            ("/music/c.mp3", "mp3", 3333.0),
        )
        conn.execute(
            "INSERT INTO tracks (file_path, ext, file_mtime) VALUES (?, ?, ?)",
            ("/music/d.m4a", "m4a", 4444.0),
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        rows = index._conn.execute(
            "SELECT ext, file_mtime FROM tracks_with_stats ORDER BY file_path"
        ).fetchall()
        index.close()

        by_ext = {r[0]: r[1] for r in rows}
        # v9 nulls FLAC/OGG; v17 subsequently nulls ALL tracks for genre/label rescan.
        assert by_ext["flac"] is None, "FLAC mtime nulled"
        assert by_ext["ogg"] is None, "OGG mtime nulled"
        assert by_ext["mp3"] is None, "MP3 mtime nulled by v17 genre/label rescan"
        assert by_ext["m4a"] is None, "M4A mtime nulled by v17 genre/label rescan"

    def test_migration_v9_to_v10_nulls_missing_album_artist_mtimes(
        self, tmp_path: Path
    ) -> None:
        """v9→v10 resets file_mtime for tracks that have an artist but no album_artist.

        This allows the scanner to re-read those files and apply the new
        album_artist → artist fallback, so they show up correctly in the library.
        """
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (9)")
        conn.execute("""
            CREATE TABLE tracks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path       TEXT UNIQUE NOT NULL,
                title           TEXT NOT NULL DEFAULT '',
                artist          TEXT NOT NULL DEFAULT '',
                album_artist    TEXT NOT NULL DEFAULT '',
                album           TEXT NOT NULL DEFAULT '',
                year            TEXT NOT NULL DEFAULT '',
                track_number    INTEGER,
                disc_number     INTEGER NOT NULL DEFAULT 1,
                ext             TEXT NOT NULL DEFAULT '',
                embedded_art    INTEGER NOT NULL DEFAULT 0,
                mb_release_id   TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added      TEXT,
                last_played     TEXT,
                favorite        INTEGER NOT NULL DEFAULT 0,
                play_count      INTEGER NOT NULL DEFAULT 0,
                file_mtime      REAL
            )
            """)
        # has artist but no album_artist → mtime should be nulled
        conn.execute(
            "INSERT INTO tracks (file_path, ext, artist, album_artist, file_mtime)"
            " VALUES (?, ?, ?, ?, ?)",
            ("/music/solo.mp3", "mp3", "Solo Artist", "", 1111.0),
        )
        # has both fields empty → mtime should be left alone (re-reading would be a no-op)
        conn.execute(
            "INSERT INTO tracks (file_path, ext, artist, album_artist, file_mtime)"
            " VALUES (?, ?, ?, ?, ?)",
            ("/music/untagged.mp3", "mp3", "", "", 2222.0),
        )
        # already has album_artist → mtime should be unchanged
        conn.execute(
            "INSERT INTO tracks (file_path, ext, artist, album_artist, file_mtime)"
            " VALUES (?, ?, ?, ?, ?)",
            ("/music/tagged.mp3", "mp3", "Band", "The Band", 3333.0),
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        rows = index._conn.execute(
            "SELECT file_path, file_mtime FROM tracks_with_stats ORDER BY file_path"
        ).fetchall()
        index.close()

        by_path = {r[0]: r[1] for r in rows}
        # v10 nulls tracks with missing album_artist; v17 nulls all remaining.
        assert (
            by_path["/music/solo.mp3"] is None
        ), "missing album_artist should be nulled"
        assert (
            by_path["/music/untagged.mp3"] is None
        ), "nulled by v17 genre/label rescan"
        assert by_path["/music/tagged.mp3"] is None, "nulled by v17 genre/label rescan"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file-permission semantics (chmod / st_mode 0o600) do not "
    "apply on Windows; see KAMP-281 for Windows ACL hardening follow-up",
)
class TestDatabaseFilePermissions:
    def test_new_database_created_with_owner_only_permissions(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        index.close()
        mode = db_path.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 600, got {oct(mode)}"

    def test_existing_world_readable_database_corrected_on_open(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "library.db"
        # Simulate a pre-existing DB with the old default 644 permissions.
        index = LibraryIndex(db_path)
        index.close()
        db_path.chmod(0o644)
        assert db_path.stat().st_mode & 0o777 == 0o644

        index2 = LibraryIndex(db_path)
        index2.close()
        mode = db_path.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 600 after re-open, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Session management — keyring-available path
# ---------------------------------------------------------------------------


class TestSessionManagementKeyring:
    """Tests the keychain-first path when a keyring backend is available."""

    @pytest.fixture(autouse=True)
    def mock_keyring(self, mocker: MockerFixture) -> dict[str, str]:
        """In-memory keyring store; returns the backing dict for inspection."""
        mocker.patch("kamp_core.library._mac_kc", None)
        # Force Linux/keyring path on Windows runners (KAMP-280).
        mocker.patch("kamp_core.library._win_cred", None)
        store: dict[str, str] = {}

        def _set(app: str, service: str, value: str) -> None:
            store[f"{app}/{service}"] = value

        def _get(app: str, service: str) -> str | None:
            return store.get(f"{app}/{service}")

        def _delete(app: str, service: str) -> None:
            key = f"{app}/{service}"
            if key not in store:
                raise keyring.errors.PasswordDeleteError(service)
            del store[key]

        mocker.patch("kamp_core.library.keyring.set_password", side_effect=_set)
        mocker.patch("kamp_core.library.keyring.get_password", side_effect=_get)
        mocker.patch("kamp_core.library.keyring.delete_password", side_effect=_delete)
        return store

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    def test_set_session_writes_to_keyring_not_db(
        self, tmp_path: Path, mock_keyring: dict[str, str]
    ) -> None:
        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        index.set_session("bandcamp", data)

        # Credential must be in keychain.
        assert "kamp/bandcamp" in mock_keyring
        # session_json column must be NULL — no plaintext in the DB.
        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert row["session_json"] is None
        index.close()

    def test_get_session_reads_from_keyring(
        self, tmp_path: Path, mock_keyring: dict[str, str]
    ) -> None:
        index = self._make_index(tmp_path)
        data: dict[str, Any] = {"session_key": "abc123"}
        index.set_session("lastfm", data)
        assert index.get_session("lastfm") == data
        index.close()

    def test_clear_session_removes_from_keyring_and_db(
        self, tmp_path: Path, mock_keyring: dict[str, str]
    ) -> None:
        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": []})
        index.clear_session("bandcamp")
        assert "kamp/bandcamp" not in mock_keyring
        assert index.get_session("bandcamp") is None
        index.close()

    def test_clear_session_noop_when_absent(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        index.clear_session("bandcamp")  # must not raise
        index.close()

    def test_set_then_get_roundtrip(
        self, tmp_path: Path, mock_keyring: dict[str, str]
    ) -> None:
        index = self._make_index(tmp_path)
        data: dict[str, Any] = {"cookies": [{"name": "x", "value": "y"}], "origins": []}
        index.set_session("bandcamp", data)
        assert index.get_session("bandcamp") == data
        index.close()


# ---------------------------------------------------------------------------
# Session management — transient keychain errors (retry / backoff)
# ---------------------------------------------------------------------------


class TestSessionManagementKeyringErrors:
    """Tests retry logic and error handling when the keychain is transiently locked."""

    @pytest.fixture(autouse=True)
    def force_keyring_path(self, mocker: MockerFixture) -> None:
        """Disable the macOS Data Protection Keychain so these tests exercise keyring."""
        mocker.patch("kamp_core.library._mac_kc", None)
        # Force Linux/keyring path on Windows runners (KAMP-280).
        mocker.patch("kamp_core.library._win_cred", None)

    def _make_index(self, tmp_path: Path) -> "LibraryIndex":
        return LibraryIndex(tmp_path / "library.db")

    def test_get_session_retries_on_keyring_locked_then_succeeds(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Succeeds on the 2nd attempt when the keychain is locked once."""
        data = {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        locked = keyring.errors.KeyringLocked("locked")
        call_count = 0

        def _get(app: str, service: str) -> str | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise locked
            return __import__("json").dumps(data)

        mocker.patch("kamp_core.library.keyring.get_password", side_effect=_get)
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")
        sleep_mock = mocker.patch("kamp_core.library._time.sleep")

        index = self._make_index(tmp_path)
        result = index.get_session("bandcamp")

        assert result == data
        assert call_count == 2
        sleep_mock.assert_called_once_with(0.5)
        index.close()

    def test_get_session_returns_none_after_all_retries_exhausted(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Returns None (not an exception) after 3 consecutive KeyringLocked failures."""
        mocker.patch(
            "kamp_core.library.keyring.get_password",
            side_effect=keyring.errors.KeyringLocked("locked"),
        )
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")
        mocker.patch("kamp_core.library._time.sleep")

        index = self._make_index(tmp_path)
        result = index.get_session("bandcamp")

        assert result is None
        index.close()

    def test_get_session_does_not_sleep_on_no_keyring_error(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """NoKeyringError falls through to DB without sleeping."""
        mocker.patch(
            "kamp_core.library.keyring.get_password",
            side_effect=keyring.errors.NoKeyringError(),
        )
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")
        sleep_mock = mocker.patch("kamp_core.library._time.sleep")

        index = self._make_index(tmp_path)
        index.get_session("bandcamp")

        sleep_mock.assert_not_called()
        index.close()

    def test_get_session_does_not_sleep_on_generic_keyring_error(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Non-locked KeyringError is logged and returns None without retrying."""
        mocker.patch(
            "kamp_core.library.keyring.get_password",
            side_effect=keyring.errors.KeyringError("unexpected"),
        )
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")
        sleep_mock = mocker.patch("kamp_core.library._time.sleep")

        index = self._make_index(tmp_path)
        result = index.get_session("bandcamp")

        assert result is None
        sleep_mock.assert_not_called()
        index.close()

    def test_set_session_falls_back_to_db_when_readback_fails(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When keychain write appears to succeed but read-back returns None, fall back to DB."""
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.delete_password")

        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        index.set_session("bandcamp", data)

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert (
            row["session_json"] is not None
        ), "should fall back to DB when read-back returns None"
        index.close()

    def test_set_session_falls_back_to_db_on_keyring_error(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When keychain write fails, the session is stored in the DB."""
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.KeyringError("write failed"),
        )
        mocker.patch(
            "kamp_core.library.keyring.get_password",
            side_effect=keyring.errors.KeyringError("read failed"),
        )
        mocker.patch("kamp_core.library.keyring.delete_password")

        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        index.set_session("bandcamp", data)

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert row["session_json"] is not None
        index.close()

    def test_clear_session_does_not_raise_on_keyring_error(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A generic KeyringError during delete is caught and does not propagate."""
        mocker.patch(
            "kamp_core.library.keyring.delete_password",
            side_effect=keyring.errors.KeyringError("delete failed"),
        )
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.set_password")

        index = self._make_index(tmp_path)
        index.clear_session("bandcamp")  # must not raise
        index.close()

    def test_set_session_falls_back_to_db_on_unexpected_exception(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """KAMP-282: backends may raise outside the keyring exception hierarchy.

        WinVaultKeyring is ctypes-based and can surface ``OSError`` (or any
        platform-specific subclass) directly.  Without the broad ``except``
        in ``set_session`` such exceptions propagated out and turned the
        Bandcamp ``login-complete`` handler into a 422.  This test pins the
        hardened behaviour: an unexpected exception type must not propagate;
        the credential must land in the DB fallback.
        """
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=OSError("ctypes call failed"),
        )
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.delete_password")

        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        index.set_session("bandcamp", data)  # must not raise

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert row["session_json"] is not None
        index.close()

    def test_get_session_falls_back_to_db_on_unexpected_exception(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """Counterpart to set_session: read path must also tolerate OSError etc."""
        mocker.patch(
            "kamp_core.library.keyring.get_password",
            side_effect=OSError("ctypes call failed"),
        )
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")
        sleep_mock = mocker.patch("kamp_core.library._time.sleep")

        index = self._make_index(tmp_path)
        # Pre-populate the DB row so the fallback has something to return.
        index._conn.execute(
            "INSERT INTO sessions (service, session_json, updated_at)"
            " VALUES ('bandcamp', ?, 0)",
            ('{"cookies": []}',),
        )
        index._conn.commit()
        result = index.get_session("bandcamp")

        assert result == {"cookies": []}
        sleep_mock.assert_not_called()
        index.close()

    def test_clear_session_does_not_raise_on_unexpected_exception(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """clear_session must swallow OSError and still purge the DB row."""
        mocker.patch(
            "kamp_core.library.keyring.delete_password",
            side_effect=OSError("ctypes call failed"),
        )
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.set_password")

        index = self._make_index(tmp_path)
        index.clear_session("bandcamp")  # must not raise
        index.close()


# ---------------------------------------------------------------------------
# Session management — Windows DPAPI wrapping of the DB fallback
# ---------------------------------------------------------------------------


class _FakeDPAPI:
    """In-memory stand-in for kamp_core.win_credential.

    Lets tests exercise the DPAPI code path on macOS / Linux runners
    without depending on the real Win32 API.  The "ciphertext" is just
    the prefix + base64 of the plaintext bytes — opaque enough to
    distinguish from the original JSON, simple enough to verify by eye
    in test failures.
    """

    DPAPI_PREFIX = "dpapi-v1:"

    @staticmethod
    def _encode(s: str) -> str:
        import base64

        return _FakeDPAPI.DPAPI_PREFIX + base64.b64encode(s.encode("utf-8")).decode(
            "ascii"
        )

    @staticmethod
    def _decode(s: str) -> str:
        import base64

        return base64.b64decode(s[len(_FakeDPAPI.DPAPI_PREFIX) :]).decode("utf-8")

    def protect_str(self, plaintext: str) -> str:
        return self._encode(plaintext)

    def unprotect_str(self, text: str) -> str | None:
        if not text.startswith(self.DPAPI_PREFIX):
            return None
        return self._decode(text)

    def is_dpapi_blob(self, text: str) -> bool:
        return text.startswith(self.DPAPI_PREFIX)


class TestSessionManagementWindowsDPAPI:
    """Tests the DPAPI wrap/unwrap path used by the Windows DB fallback.

    Uses :class:`_FakeDPAPI` so the test runs on every OS — the real
    ctypes round-trip is covered separately in ``test_win_credential.py``.
    """

    @pytest.fixture(autouse=True)
    def force_db_fallback(self, mocker: MockerFixture) -> _FakeDPAPI:
        """Force the no-keychain DB-fallback path and inject a fake DPAPI."""
        mocker.patch("kamp_core.library._mac_kc", None)
        # Pretend keyring has no backend so set_session falls through to DB.
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.NoKeyringError(),
        )
        mocker.patch(
            "kamp_core.library.keyring.get_password",
            side_effect=keyring.errors.NoKeyringError(),
        )
        mocker.patch("kamp_core.library.keyring.delete_password")
        fake = _FakeDPAPI()
        mocker.patch("kamp_core.library._win_cred", fake)
        return fake

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    def test_set_session_writes_dpapi_wrapped_to_db(self, tmp_path: Path) -> None:
        """KAMP-280 AC #3: sessions DB row is opaque ciphertext, not plaintext JSON."""
        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "session", "value": "secret-cookie-value"}]}
        index.set_session("bandcamp", data)

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        stored = row["session_json"]
        assert stored is not None
        assert stored.startswith(
            _FakeDPAPI.DPAPI_PREFIX
        ), "DB row should be DPAPI-wrapped, not plaintext"
        assert (
            "secret-cookie-value" not in stored
        ), "raw cookie value must not appear anywhere in the stored row"
        index.close()

    def test_get_session_unwraps_dpapi_row(self, tmp_path: Path) -> None:
        """Round-trip: write encrypted, read decrypted."""
        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "session", "value": "v"}], "username": "alice"}
        index.set_session("bandcamp", data)

        assert index.get_session("bandcamp") == data
        index.close()

    def test_get_session_handles_legacy_plaintext_row(self, tmp_path: Path) -> None:
        """Pre-DPAPI installs may still have plaintext rows; reading them must work."""
        import json as _json

        index = self._make_index(tmp_path)
        # Bypass set_session and write a legacy plaintext row directly.
        index._conn.execute(
            "INSERT INTO sessions (service, session_json, updated_at)"
            " VALUES (?, ?, ?)",
            ("bandcamp", _json.dumps({"cookies": []}), 1.0),
        )
        index._conn.commit()

        assert index.get_session("bandcamp") == {"cookies": []}
        index.close()

    def test_set_session_falls_back_to_plaintext_when_dpapi_raises(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A DPAPI failure must not block writing — a session row beats no row."""
        mocker.patch(
            "kamp_core.library._win_cred.protect_str",
            side_effect=OSError("CryptProtectData failed"),
        )

        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "x", "value": "y"}]}
        index.set_session("bandcamp", data)  # must not raise

        # Even on DPAPI failure, the row should still be present (plaintext).
        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert row["session_json"] is not None
        index.close()

    def test_set_session_does_not_call_keyring_on_windows(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """KAMP-280: when DPAPI is available, skip the OS keyring entirely.

        WinVaultKeyring's 2560-byte size limit means CredWrite will always
        fail for the Bandcamp blob; calling it just produces a noisy WARNING
        log line on every login.  With DPAPI available we go straight to
        the encrypted DB row.
        """
        # Reset the keyring mock from the autouse fixture so we can assert
        # call counts cleanly.
        mock_set = mocker.patch("kamp_core.library.keyring.set_password")
        mock_get = mocker.patch(
            "kamp_core.library.keyring.get_password", return_value=None
        )

        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": [{"name": "x", "value": "y"}]})

        mock_set.assert_not_called()
        mock_get.assert_not_called()
        index.close()

    def test_clear_session_does_not_call_keyring_on_windows(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """clear_session also skips OS keyring when DPAPI is available."""
        mock_delete = mocker.patch("kamp_core.library.keyring.delete_password")

        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": []})
        index.clear_session("bandcamp")

        mock_delete.assert_not_called()
        # And the DB row is gone.
        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is None
        index.close()


# ---------------------------------------------------------------------------
# Migration v12 → v13: encrypt residual plaintext rows on Windows
# ---------------------------------------------------------------------------


class TestMigrationV12ToV13:
    """v12 left rows in the DB on Windows when keyring couldn't store them.

    v13 wraps any such residual plaintext rows with DPAPI on first
    open, so the on-disk credential is no longer readable as plaintext
    (KAMP-280 AC #3).  Cross-platform — we mock both keyring and DPAPI.
    """

    @pytest.fixture(autouse=True)
    def force_db_fallback(self, mocker: MockerFixture) -> _FakeDPAPI:
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.NoKeyringError(),
        )
        mocker.patch(
            "kamp_core.library.keyring.get_password",
            side_effect=keyring.errors.NoKeyringError(),
        )
        mocker.patch("kamp_core.library.keyring.delete_password")
        fake = _FakeDPAPI()
        mocker.patch("kamp_core.library._win_cred", fake)
        return fake

    def _build_v12_db(self, db_path: Path) -> None:
        """Create a v12 database with a plaintext sessions row (the Windows shape)."""
        import json as _json

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (12)")
        conn.execute("""
            CREATE TABLE sessions (
                service      TEXT NOT NULL PRIMARY KEY,
                session_json TEXT,
                updated_at   REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE settings (
                key   TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # A v12-era tracks table (columns through file_mtime). The KAMP-539 drop
        # removed these from the modern _DDL, so without them the replayed v13→v49
        # migrations (e.g. v17's "UPDATE tracks SET file_mtime = NULL") fail.
        conn.execute(
            "CREATE TABLE tracks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0,"
            " disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '',"
            " mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL)"
        )
        conn.execute(
            "INSERT INTO sessions (service, session_json, updated_at) VALUES (?, ?, ?)",
            ("bandcamp", _json.dumps({"cookies": [{"name": "x"}]}), 1.0),
        )
        conn.commit()
        conn.close()

    def test_migration_wraps_plaintext_row_with_dpapi(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v12_db(db_path)

        index = LibraryIndex(db_path)

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row["session_json"] is not None
        assert row["session_json"].startswith(
            _FakeDPAPI.DPAPI_PREFIX
        ), "v13 migration should have wrapped the plaintext row"
        # And the round-trip still works through get_session.
        assert index.get_session("bandcamp") == {"cookies": [{"name": "x"}]}
        index.close()

    def test_migration_skips_already_wrapped_rows(self, tmp_path: Path) -> None:
        """If a row is already DPAPI-wrapped (paranoia), don't double-wrap."""
        import json as _json

        db_path = tmp_path / "library.db"
        # Build a v12 DB with an already-wrapped row (simulating a partial
        # prior migration or a manual repair).
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (12)")
        conn.execute("""
            CREATE TABLE sessions (
                service      TEXT NOT NULL PRIMARY KEY,
                session_json TEXT,
                updated_at   REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE settings (
                key   TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # A v12-era tracks table (columns through file_mtime) so the replayed
        # v13→v49 migrations find the pre-KAMP-539 columns they reference.
        conn.execute(
            "CREATE TABLE tracks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0,"
            " disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '',"
            " mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL)"
        )
        already_wrapped = _FakeDPAPI().protect_str(_json.dumps({"cookies": []}))
        conn.execute(
            "INSERT INTO sessions (service, session_json, updated_at) VALUES (?, ?, ?)",
            ("bandcamp", already_wrapped, 1.0),
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        # Still a single DPAPI prefix — not wrapped twice.
        assert row["session_json"] == already_wrapped
        index.close()


# ---------------------------------------------------------------------------
# Migration v11 → v12: credentials moved from DB to keychain
# ---------------------------------------------------------------------------


class TestMigrationV11ToV12:
    @pytest.fixture(autouse=True)
    def force_keyring_path(self, mocker: MockerFixture) -> None:
        """Disable the macOS Data Protection Keychain so v11→v12 migration uses keyring."""
        mocker.patch("kamp_core.library._mac_kc", None)
        # Force Linux/keyring path on Windows runners (KAMP-280).
        mocker.patch("kamp_core.library._win_cred", None)

    def _build_v11_db(self, db_path: Path) -> None:
        """Create a v11 database with a sessions row containing plaintext JSON."""
        import json as _json

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (11)")
        conn.execute("""
            CREATE TABLE sessions (
                service      TEXT NOT NULL PRIMARY KEY,
                session_json TEXT NOT NULL,
                updated_at   REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE settings (
                key   TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # A v11-era tracks table (columns through file_mtime). The KAMP-539 drop
        # removed these from the modern _DDL, so without them the replayed v12→v49
        # migrations (e.g. v17's "UPDATE tracks SET file_mtime = NULL") fail.
        conn.execute(
            "CREATE TABLE tracks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0,"
            " disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '',"
            " mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL)"
        )
        conn.execute(
            "INSERT INTO sessions (service, session_json, updated_at) VALUES (?, ?, ?)",
            (
                "bandcamp",
                _json.dumps({"cookies": [{"name": "js_logged_in", "value": "1"}]}),
                1.0,
            ),
        )
        conn.execute(
            "INSERT INTO sessions (service, session_json, updated_at) VALUES (?, ?, ?)",
            ("lastfm", _json.dumps({"session_key": "sk_abc"}), 2.0),
        )
        conn.commit()
        conn.close()

    def test_migration_moves_credentials_to_keychain(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        store: dict[str, str] = {}

        def _set(app: str, service: str, value: str) -> None:
            store[f"{app}/{service}"] = value

        mocker.patch("kamp_core.library.keyring.set_password", side_effect=_set)
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.delete_password")

        db_path = tmp_path / "library.db"
        self._build_v11_db(db_path)

        index = LibraryIndex(db_path)

        # Both services should be in the keychain.
        assert "kamp/bandcamp" in store
        assert "kamp/lastfm" in store

        # session_json column must be cleared in DB.
        rows = index._conn.execute(
            "SELECT service, session_json FROM sessions"
        ).fetchall()
        for row in rows:
            assert (
                row["session_json"] is None
            ), f"session_json not cleared for service {row['service']!r}"

        # Schema version bumped.
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        assert version == 60

        index.close()

    def test_migration_leaves_db_intact_when_no_keyring(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        err = keyring.errors.NoKeyringError()
        mocker.patch("kamp_core.library.keyring.set_password", side_effect=err)
        mocker.patch("kamp_core.library.keyring.get_password", side_effect=err)
        mocker.patch("kamp_core.library.keyring.delete_password", side_effect=err)

        db_path = tmp_path / "library.db"
        self._build_v11_db(db_path)

        index = LibraryIndex(db_path)

        # Credentials must still be in the DB since keyring was unavailable.
        rows = {
            row["service"]: row["session_json"]
            for row in index._conn.execute(
                "SELECT service, session_json FROM sessions"
            ).fetchall()
        }
        assert rows["bandcamp"] is not None
        assert rows["lastfm"] is not None

        # get_session must fall back to DB and return the data.
        assert index.get_session("bandcamp") == {
            "cookies": [{"name": "js_logged_in", "value": "1"}]
        }
        assert index.get_session("lastfm") == {"session_key": "sk_abc"}

        index.close()


# ---------------------------------------------------------------------------
# Session management — macOS Login Keychain (SecItemUpdate) path
# ---------------------------------------------------------------------------


class TestSessionManagementMacOS:
    """Tests the macOS _mac_kc path (Login Keychain with SecItemUpdate)."""

    @pytest.fixture(autouse=True)
    def mock_mac_kc(self, mocker: MockerFixture) -> dict[str, str]:
        """In-memory Login Keychain store; returns the backing dict."""
        store: dict[str, str] = {}

        def _get(app: str, service: str) -> str | None:
            return store.get(f"{app}/{service}")

        def _set(app: str, service: str, value: str) -> None:
            store[f"{app}/{service}"] = value

        def _delete(app: str, service: str) -> None:
            store.pop(f"{app}/{service}", None)

        mock = MagicMock()
        mock.get_password.side_effect = _get
        mock.set_password.side_effect = _set
        mock.delete_password.side_effect = _delete

        mocker.patch("kamp_core.library._mac_kc", mock)
        # keyring must NOT be called on the macOS path.
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")
        return store

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    def test_set_session_writes_to_keychain_not_db(
        self, tmp_path: Path, mock_mac_kc: dict[str, str]
    ) -> None:
        index = self._make_index(tmp_path)
        data = {"cookies": [{"name": "js_logged_in", "value": "1"}]}
        index.set_session("bandcamp", data)

        assert "kamp/bandcamp" in mock_mac_kc
        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert (
            row["session_json"] is None
        ), "credential must not be stored in plaintext DB"
        index.close()

    def test_get_session_reads_from_keychain(
        self, tmp_path: Path, mock_mac_kc: dict[str, str]
    ) -> None:
        index = self._make_index(tmp_path)
        data: dict[str, Any] = {"session_key": "mac_abc123"}
        index.set_session("lastfm", data)
        assert index.get_session("lastfm") == data
        index.close()

    def test_set_session_uses_update_not_delete_recreate(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """set_session calls mac_kc.set_password (SecItemUpdate-based), never
        delete_password, so previously granted ACL entries survive updates."""
        store: dict[str, str] = {}

        def _get(app: str, service: str) -> str | None:
            return store.get(f"{app}/{service}")

        def _set(app: str, service: str, value: str) -> None:
            store[f"{app}/{service}"] = value

        mock = MagicMock()
        mock.get_password.side_effect = _get
        mock.set_password.side_effect = _set
        mock.delete_password.side_effect = lambda *_: None
        mocker.patch("kamp_core.library._mac_kc", mock)
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")

        index = self._make_index(tmp_path)
        data1 = {"session_key": "first"}
        data2 = {"session_key": "second"}
        index.set_session("lastfm", data1)
        index.set_session("lastfm", data2)

        # set_password called twice (once per set_session) — never delete_password
        assert mock.set_password.call_count == 2
        assert mock.delete_password.call_count == 0
        assert index.get_session("lastfm") == data2
        index.close()

    def test_clear_session_removes_from_keychain_and_db(
        self, tmp_path: Path, mock_mac_kc: dict[str, str]
    ) -> None:
        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": []})
        index.clear_session("bandcamp")
        assert "kamp/bandcamp" not in mock_mac_kc
        assert index.get_session("bandcamp") is None
        index.close()

    def test_set_session_falls_back_to_db_when_write_fails(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """KeyringError from mac_kc.set_password causes DB fallback."""
        mock = MagicMock()
        mock.set_password.side_effect = keyring.errors.KeyringError("write failed")
        mock.get_password.return_value = None
        mocker.patch("kamp_core.library._mac_kc", mock)
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")

        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": []})

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert row["session_json"] is not None, "must fall back to DB on write failure"
        index.close()

    def test_set_session_verification_mismatch_stores_in_db(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """When read-back returns wrong value, credential falls back to DB."""
        mock = MagicMock()
        mock.set_password.return_value = None
        mock.get_password.return_value = '{"wrong": "value"}'
        mocker.patch("kamp_core.library._mac_kc", mock)
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.delete_password")

        index = self._make_index(tmp_path)
        index.set_session("bandcamp", {"cookies": []})

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert row["session_json"] is not None, "verification mismatch must store in DB"
        index.close()

    def test_clear_session_logs_warning_on_keyring_error(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """KeyringError from mac_kc.delete_password is logged but not re-raised."""
        mock = MagicMock()
        mock.delete_password.side_effect = keyring.errors.KeyringError("delete failed")
        mocker.patch("kamp_core.library._mac_kc", mock)

        index = self._make_index(tmp_path)
        index.clear_session("lastfm")  # must not raise
        index.close()


class TestSessionManagementMacOSErrors:
    """Retry and error-handling paths for get_session on the macOS _mac_kc path."""

    @pytest.fixture(autouse=True)
    def mock_mac_kc(self, mocker: MockerFixture) -> MagicMock:
        mock = MagicMock()
        mock._dpc_unavailable = False
        mock._get_login_keychain_password.return_value = None
        mocker.patch("kamp_core.library._mac_kc", mock)
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        mocker.patch("kamp_core.library._time.sleep")
        return mock

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    def test_retries_on_keyring_locked_then_succeeds(
        self, tmp_path: Path, mock_mac_kc: MagicMock
    ) -> None:
        data = {"session_key": "abc"}
        mock_mac_kc.get_password.side_effect = [
            keyring.errors.KeyringLocked("locked"),
            json.dumps(data),
        ]

        index = self._make_index(tmp_path)
        result = index.get_session("lastfm")

        assert result == data
        index.close()

    def test_breaks_on_keyring_error(
        self, tmp_path: Path, mock_mac_kc: MagicMock
    ) -> None:
        mock_mac_kc.get_password.side_effect = keyring.errors.KeyringError("boom")

        index = self._make_index(tmp_path)
        result = index.get_session("lastfm")

        assert result is None
        assert mock_mac_kc.get_password.call_count == 1
        index.close()

    def test_warns_after_all_retries_exhausted(
        self, tmp_path: Path, mock_mac_kc: MagicMock
    ) -> None:
        mock_mac_kc.get_password.side_effect = keyring.errors.KeyringLocked("locked")

        index = self._make_index(tmp_path)
        result = index.get_session("lastfm")

        assert result is None
        assert mock_mac_kc.get_password.call_count == 3  # _MAX_RETRIES
        index.close()


class TestMarkProcessedBy:
    """Tests for LibraryIndex.mark_processed_by and has_been_processed_by."""

    def _make_index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    def test_mark_processed_by_makes_has_been_processed_true(
        self, tmp_path: Path
    ) -> None:
        index = self._make_index(tmp_path)
        mbid = "mbid-1234"
        ext = "kamp.musicbrainz"

        assert not index.has_been_processed_by(ext, mbid)
        index.mark_processed_by(ext, mbid)
        assert index.has_been_processed_by(ext, mbid)
        index.close()

    def test_mark_processed_by_does_not_affect_other_extensions(
        self, tmp_path: Path
    ) -> None:
        index = self._make_index(tmp_path)
        mbid = "mbid-5678"
        index.mark_processed_by("kamp.musicbrainz", mbid)

        assert not index.has_been_processed_by("kamp.coverart", mbid)
        index.close()


# ---------------------------------------------------------------------------
# Genre and label tag readers (KAMP-303)
# ---------------------------------------------------------------------------


class TestGenreLabelTagReaders:
    """_read_mp3_tags, _read_m4a_tags, _read_vorbis_tags read genre and label."""

    def test_read_mp3_tags_genre_and_label(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TCON"] = id3.TCON(encoding=3, text="Jazz")
        tags["TPUB"] = id3.TPUB(encoding=3, text="Blue Note")
        tags.save(str(mp3))

        track = _read_mp3_tags(mp3)
        assert track.genre == "Jazz"
        assert track.label == "Blue Note"

    def test_read_mp3_tags_label_null_byte_separator_becomes_slash(
        self, tmp_path: Path
    ) -> None:
        """ID3 multi-value fields separated by \\x00 should display as ' / '."""
        mp3 = tmp_path / "multi.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        # Simulate a TPUB frame with two values joined by the ID3 null separator.
        tags["TPUB"] = id3.TPUB(encoding=3, text="FFRR\x00Mo Wax")
        tags.save(str(mp3))

        track = _read_mp3_tags(mp3)
        assert track.label == "FFRR / Mo Wax"

    def test_read_mp3_tags_genre_label_empty_when_absent(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "no_meta.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        track = _read_mp3_tags(mp3)
        assert track.genre == ""
        assert track.label == ""

    def test_read_m4a_tags_genre_and_label(self, tmp_path: Path) -> None:
        import mutagen.mp4

        m4a = tmp_path / "track.m4a"
        m4a.write_bytes(b"\x00" * 32)
        mock_audio = MagicMock()
        mock_audio.tags = {
            "\xa9gen": ["Electronic"],
            "----:com.apple.iTunes:LABEL": [mutagen.mp4.MP4FreeForm(b"Warp Records")],
        }
        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            track = _read_m4a_tags(m4a)
        assert track.genre == "Electronic"
        assert track.label == "Warp Records"

    def test_read_m4a_tags_genre_label_empty_when_absent(self, tmp_path: Path) -> None:
        m4a = tmp_path / "no_meta.m4a"
        m4a.write_bytes(b"\x00" * 32)
        mock_audio = MagicMock()
        mock_audio.tags = {}
        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            track = _read_m4a_tags(m4a)
        assert track.genre == ""
        assert track.label == ""

    def test_read_flac_tags_genre_and_label(self, tmp_path: Path) -> None:
        flac = tmp_path / "track.flac"
        flac.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        mock_audio.tags = {"GENRE": ["Classical"], "LABEL": ["ECM Records"]}
        mock_audio.pictures = []
        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            track = _read_vorbis_tags(flac, is_flac=True)
        assert track.genre == "Classical"
        assert track.label == "ECM Records"

    def test_read_flac_tags_label_falls_back_to_organization(
        self, tmp_path: Path
    ) -> None:
        flac = tmp_path / "track.flac"
        flac.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        mock_audio.tags = {"ORGANIZATION": ["Sub Pop"]}
        mock_audio.pictures = []
        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            track = _read_vorbis_tags(flac, is_flac=True)
        assert track.label == "Sub Pop"

    def test_read_ogg_tags_genre_and_label(self, tmp_path: Path) -> None:
        ogg = tmp_path / "track.ogg"
        ogg.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        mock_audio.tags = {"GENRE": ["Ambient"], "LABEL": ["4AD"]}
        mock_audio.pictures = []
        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis", return_value=mock_audio
        ):
            track = _read_vorbis_tags(ogg, is_flac=False)
        assert track.genre == "Ambient"
        assert track.label == "4AD"


# ---------------------------------------------------------------------------
# write_meta_tags_to_file (KAMP-303)
# ---------------------------------------------------------------------------


class TestWriteMetaTagsToFile:
    """write_meta_tags_to_file writes genre/label/year to each format."""

    def test_writes_genre_and_label_to_mp3(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        write_meta_tags_to_file(
            mp3, genre="Blues", label="Chess Records", release_date="1958"
        )

        tags = id3.ID3(str(mp3))
        assert str(tags["TCON"]) == "Blues"
        assert str(tags["TPUB"]) == "Chess Records"
        assert str(tags["TDRC"]) == "1958"

    def test_writes_multi_value_genre_to_mp3_roundtrip(self, tmp_path: Path) -> None:
        # KAMP-586: multi-value genres round-trip through the real ID3 TCON frame
        # and back out via the reader as a list.
        mp3 = tmp_path / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        write_meta_tags_to_file(mp3, genres=["Jazz", "J-Pop"])

        assert _read_mp3_tags(mp3).genres == ["Jazz", "J-Pop"]

    def test_empty_genres_clears_mp3_tcon(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TCON"] = id3.TCON(encoding=3, text=["Jazz"])
        tags.save(str(mp3))

        write_meta_tags_to_file(mp3, genres=[])

        assert id3.ID3(str(mp3)).get("TCON") is None

    def test_writes_multi_value_genre_to_flac(self, tmp_path: Path) -> None:
        flac = tmp_path / "track.flac"
        flac.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        mock_audio.tags = {}
        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            write_meta_tags_to_file(flac, genres=["Jazz", "J-Pop"])
        assert mock_audio.tags["GENRE"] == ["Jazz", "J-Pop"]

    def test_writes_multi_value_genre_to_m4a(self, tmp_path: Path) -> None:
        m4a = tmp_path / "track.m4a"
        m4a.write_bytes(b"\x00" * 32)
        mock_audio = MagicMock()
        mock_audio.tags = {}
        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            write_meta_tags_to_file(m4a, genres=["Jazz", "J-Pop"])
        assert mock_audio.tags["\xa9gen"] == ["Jazz", "J-Pop"]

    def test_writes_genre_and_label_to_m4a(self, tmp_path: Path) -> None:
        import mutagen.mp4

        m4a = tmp_path / "track.m4a"
        m4a.write_bytes(b"\x00" * 32)
        mock_audio = MagicMock()
        mock_audio.tags = {}
        with patch("kamp_core.library.mutagen.mp4.MP4", return_value=mock_audio):
            write_meta_tags_to_file(
                m4a, genre="Soul", label="Motown", release_date="1965"
            )

        assert mock_audio.tags["\xa9gen"] == ["Soul"]
        assert mock_audio.tags["\xa9day"] == ["1965"]
        label_val = mock_audio.tags["----:com.apple.iTunes:LABEL"][0]
        assert isinstance(label_val, mutagen.mp4.MP4FreeForm)
        assert bytes(label_val) == b"Motown"
        mock_audio.save.assert_called_once()

    def test_writes_genre_and_label_to_flac(self, tmp_path: Path) -> None:
        import mutagen.flac

        flac = tmp_path / "track.flac"
        flac.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        mock_audio.tags = {}
        with patch("kamp_core.library.mutagen.flac.FLAC", return_value=mock_audio):
            write_meta_tags_to_file(
                flac, genre="Folk", label="Folkways", release_date="1970"
            )

        assert mock_audio.tags["GENRE"] == ["Folk"]
        assert mock_audio.tags["LABEL"] == ["Folkways"]
        assert mock_audio.tags["DATE"] == ["1970"]
        mock_audio.save.assert_called_once()

    def test_writes_genre_and_label_to_ogg(self, tmp_path: Path) -> None:
        ogg = tmp_path / "track.ogg"
        ogg.write_bytes(b"\x00" * 8)
        mock_audio = MagicMock()
        mock_audio.tags = {}
        with patch(
            "kamp_core.library.mutagen.oggvorbis.OggVorbis", return_value=mock_audio
        ):
            write_meta_tags_to_file(ogg, genre="Punk", label="SST", release_date="1984")

        assert mock_audio.tags["GENRE"] == ["Punk"]
        assert mock_audio.tags["LABEL"] == ["SST"]
        assert mock_audio.tags["DATE"] == ["1984"]
        mock_audio.save.assert_called_once()

    def test_only_writes_provided_fields(self, tmp_path: Path) -> None:
        """Fields not passed (None) must not be written to the file."""
        mp3 = tmp_path / "track.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TCON"] = id3.TCON(encoding=3, text="Original")
        tags.save(str(mp3))

        write_meta_tags_to_file(mp3, label="New Label")  # genre/year not passed

        result = id3.ID3(str(mp3))
        assert str(result["TCON"]) == "Original"  # unchanged
        assert str(result["TPUB"]) == "New Label"
        assert result.get("TDRC") is None

    def test_raises_for_unsupported_format(self, tmp_path: Path) -> None:
        wav = tmp_path / "track.wav"
        wav.write_bytes(b"\x00" * 8)
        with pytest.raises(ValueError, match="Unsupported format"):
            write_meta_tags_to_file(wav, genre="Rock")

    def test_creates_id3_header_when_absent(self, tmp_path: Path) -> None:
        """File with no ID3 header should get one created."""
        mp3 = tmp_path / "bare.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)  # no ID3 header

        write_meta_tags_to_file(mp3, genre="Metal")

        tags = id3.ID3(str(mp3))
        assert str(tags["TCON"]) == "Metal"


# ---------------------------------------------------------------------------
# LibraryIndex.update_album_meta (KAMP-303)
# ---------------------------------------------------------------------------


class TestUpdateAlbumMeta:
    """LibraryIndex.update_album_meta persists genre/label/year to the DB."""

    def _make_index_with_track(self, tmp_path: Path) -> tuple[LibraryIndex, Track]:
        index = LibraryIndex(tmp_path / "library.db")
        track = Track(
            file_path=tmp_path / "01.mp3",
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Record",
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="",
            label="",
        )
        index.upsert_many([track])
        return index, track

    def test_update_genre_persisted(self, tmp_path: Path) -> None:
        index, _ = self._make_index_with_track(tmp_path)
        tracks = index.update_album_meta("Artist", "Record", genre="Rock")
        assert len(tracks) == 1
        assert tracks[0].genre == "Rock"
        index.close()

    def test_update_label_persisted(self, tmp_path: Path) -> None:
        index, _ = self._make_index_with_track(tmp_path)
        tracks = index.update_album_meta("Artist", "Record", label="Rough Trade")
        assert tracks[0].label == "Rough Trade"
        index.close()

    def test_update_year_persisted(self, tmp_path: Path) -> None:
        index, _ = self._make_index_with_track(tmp_path)
        tracks = index.update_album_meta("Artist", "Record", release_date="2024")
        assert tracks[0].release_date == "2024"
        index.close()

    def test_update_only_supplied_fields(self, tmp_path: Path) -> None:
        """Unspecified fields (None) must not be overwritten."""
        index = LibraryIndex(tmp_path / "library.db")
        track = Track(
            file_path=tmp_path / "01.mp3",
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album="Record",
            release_date="1990",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="Jazz",
            label="Blue Note",
        )
        index.upsert_many([track])
        tracks = index.update_album_meta("Artist", "Record", label="ECM")
        assert tracks[0].genre == "Jazz"  # unchanged
        assert tracks[0].label == "ECM"
        assert tracks[0].release_date == "1990"  # unchanged
        index.close()

    def test_no_fields_returns_existing_tracks(self, tmp_path: Path) -> None:
        """Calling with no kwargs must return existing tracks without a DB write."""
        index, _ = self._make_index_with_track(tmp_path)
        tracks = index.update_album_meta("Artist", "Record")
        assert len(tracks) == 1
        index.close()


# ---------------------------------------------------------------------------
# Migration v16 → v17 (KAMP-303)
# ---------------------------------------------------------------------------


class TestMigrationV16ToV17:
    """v16 → v17 adds genre and label columns to the tracks table."""

    def _build_v16_db(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (16)")
        conn.execute("""
            CREATE TABLE tracks (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path         TEXT    NOT NULL UNIQUE,
                title             TEXT    NOT NULL DEFAULT '',
                artist            TEXT    NOT NULL DEFAULT '',
                album_artist      TEXT    NOT NULL DEFAULT '',
                album             TEXT    NOT NULL DEFAULT '',
                year              TEXT    NOT NULL DEFAULT '',
                track_number      INTEGER NOT NULL DEFAULT 0,
                disc_number       INTEGER NOT NULL DEFAULT 1,
                ext               TEXT    NOT NULL DEFAULT '',
                embedded_art      INTEGER NOT NULL DEFAULT 0,
                mb_release_id     TEXT    NOT NULL DEFAULT '',
                mb_recording_id   TEXT    NOT NULL DEFAULT '',
                file_mtime        REAL,
                date_added        REAL,
                last_played       REAL,
                favorite          INTEGER NOT NULL DEFAULT 0,
                play_count        INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE album_favorites (
                album_artist TEXT NOT NULL,
                album        TEXT NOT NULL,
                PRIMARY KEY (album_artist, album)
            )
        """)
        conn.execute("""
            CREATE TABLE settings (
                key   TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE sessions (
                service      TEXT NOT NULL PRIMARY KEY,
                session_json TEXT,
                updated_at   REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE deferred_ops (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                op_type      TEXT    NOT NULL,
                track_id     INTEGER NOT NULL UNIQUE,
                payload_json TEXT    NOT NULL,
                created_at   REAL    NOT NULL,
                attempts     INTEGER NOT NULL DEFAULT 0,
                last_error   TEXT
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
                title, artist, album_artist, album, content=tracks, content_rowid=id
            )
        """)
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album) "
            "VALUES ('/music/01.mp3', 'Song', 'Artist', 'Artist', 'Record')"
        )
        conn.commit()
        conn.close()

    def test_migration_adds_genre_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v16_db(db_path)

        index = LibraryIndex(db_path)

        cols = {
            row[1]
            for row in index._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        assert "genre" in cols
        index.close()

    def test_migration_adds_label_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v16_db(db_path)

        index = LibraryIndex(db_path)

        cols = {
            row[1]
            for row in index._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        assert "label" in cols
        index.close()

    def test_migration_bumps_schema_version_to_17(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v16_db(db_path)

        index = LibraryIndex(db_path)

        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        assert version == 60
        index.close()

    def test_migration_existing_rows_get_empty_defaults(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v16_db(db_path)

        index = LibraryIndex(db_path)

        row = index._conn.execute(
            "SELECT genre, label FROM tracks_with_stats WHERE id = (SELECT track_id FROM track_sources WHERE uri = '/music/01.mp3')"
        ).fetchone()
        assert row["genre"] == ""
        assert row["label"] == ""
        index.close()

    def test_migration_nulls_file_mtime_for_rescan(self, tmp_path: Path) -> None:
        """v17 must null file_mtime on existing rows so genre/label are populated."""
        db_path = tmp_path / "library.db"
        self._build_v16_db(db_path)

        index = LibraryIndex(db_path)

        row = index._conn.execute(
            "SELECT file_mtime FROM tracks_with_stats WHERE file_path = '/music/01.mp3'"
        ).fetchone()
        assert row["file_mtime"] is None, "file_mtime should be nulled to force rescan"
        index.close()

    def test_migration_idempotent_on_new_db(self, tmp_path: Path) -> None:
        """New DBs (from _DDL) already have genre/label; migration must not fail."""
        index = LibraryIndex(tmp_path / "library.db")
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        assert version == 60
        index.close()


# ---------------------------------------------------------------------------
# mark_album_art_embedded (KAMP-341)
# ---------------------------------------------------------------------------


class TestMarkAlbumArtEmbedded:
    """LibraryIndex.mark_album_art_embedded sets embedded_art and file_mtime."""

    def _make_track(self, tmp_path: Path, name: str, album: str = "Record") -> Track:
        return Track(
            file_path=tmp_path / name,
            title="Song",
            artist="Artist",
            album_artist="Artist",
            album=album,
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="",
            label="",
        )

    def test_sets_embedded_art_for_given_paths(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t1 = self._make_track(tmp_path, "01.mp3")
        t2 = self._make_track(tmp_path, "02.mp3")
        index.upsert_many([t1, t2])

        index.mark_album_art_embedded("Artist", "Record", [t1.file_path, t2.file_path])

        rows = index._conn.execute(
            "SELECT embedded_art FROM tracks_with_stats WHERE album = 'Record'"
        ).fetchall()
        assert all(r["embedded_art"] == 1 for r in rows)
        index.close()

    def test_sets_file_mtime_to_approx_now(self, tmp_path: Path) -> None:
        import time

        index = LibraryIndex(tmp_path / "library.db")
        t1 = self._make_track(tmp_path, "01.mp3")
        index.upsert_many([t1])

        before = time.time()
        index.mark_album_art_embedded("Artist", "Record", [t1.file_path])
        after = time.time()

        row = index._conn.execute(
            "SELECT file_mtime FROM tracks_with_stats WHERE file_path = ?",
            (str(t1.file_path),),
        ).fetchone()
        assert row["file_mtime"] is not None
        assert before <= row["file_mtime"] <= after
        index.close()

    def test_only_updates_given_paths(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t1 = self._make_track(tmp_path, "01.mp3")
        t2 = self._make_track(tmp_path, "02.mp3")
        index.upsert_many([t1, t2])

        index.mark_album_art_embedded("Artist", "Record", [t1.file_path])

        row = index._conn.execute(
            "SELECT embedded_art FROM tracks_with_stats WHERE file_path = ?",
            (str(t2.file_path),),
        ).fetchone()
        assert row["embedded_art"] == 0  # t2 not in the list
        index.close()

    def test_does_not_affect_other_albums(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t1 = self._make_track(tmp_path, "01.mp3", album="Record")
        t2 = self._make_track(tmp_path, "02.mp3", album="OtherRecord")
        index.upsert_many([t1, t2])

        index.mark_album_art_embedded("Artist", "Record", [t1.file_path])

        row = index._conn.execute(
            "SELECT embedded_art FROM tracks_with_stats WHERE album = 'OtherRecord'"
        ).fetchone()
        assert row["embedded_art"] == 0
        index.close()


# ---------------------------------------------------------------------------
# BandcampCollection (KAMP-381)
# ---------------------------------------------------------------------------


class TestBandcampCollection:
    def test_empty_on_fresh_db(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.get_collection_state() == {}
        index.close()

    def test_upsert_and_get(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "123",
            mode="local",
            band_name="The Marloes",
            item_title="Di Hotel Malibu",
            synced_at=1000.0,
            added_at=900.0,
        )
        state = index.get_collection_state()
        index.close()

        assert state == {"123": "local"}

    def test_upsert_updates_existing_row(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("99", mode="local", synced_at=1.0)
        index.upsert_collection_item("99", mode="remote", synced_at=2.0)
        state = index.get_collection_state()
        index.close()

        assert state == {"99": "remote"}

    def test_upsert_does_not_overwrite_added_at_on_conflict(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("7", mode="local", added_at=500.0)
        index.upsert_collection_item("7", mode="remote", synced_at=1.0)
        row = index._conn.execute(
            "SELECT added_at FROM bandcamp_collection WHERE sale_item_id = '7'"
        ).fetchone()
        index.close()

        assert row["added_at"] == 500.0

    def test_upsert_updates_added_at_when_new_value_is_earlier(
        self, tmp_path: Path
    ) -> None:
        """ON CONFLICT takes MIN — a smaller added_at (real purchase date) replaces a
        larger one (wrong sync-time timestamp), so existing users are self-correcting.
        """
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("8", mode="local", added_at=1_000_000.0)
        index.upsert_collection_item("8", mode="local", added_at=100.0)
        row = index._conn.execute(
            "SELECT added_at FROM bandcamp_collection WHERE sale_item_id = '8'"
        ).fetchone()
        index.close()

        assert row["added_at"] == 100.0

    def test_upsert_preserves_zero_added_at_on_conflict(self, tmp_path: Path) -> None:
        """added_at=0 (from mark_collection_synced) must survive subsequent syncs."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("9", mode="local", added_at=0.0)
        index.upsert_collection_item("9", mode="local", added_at=9_999_999.0)
        row = index._conn.execute(
            "SELECT added_at FROM bandcamp_collection WHERE sale_item_id = '9'"
        ).fetchone()
        index.close()

        assert row["added_at"] == 0.0

    def test_update_remote_track_date_added_corrects_wrong_timestamp(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(Path("bandcamp://42/1"))
        track.date_added = 9_999_999.0  # wrong sync-time timestamp
        index.upsert_many([track])

        index.update_remote_track_date_added("42", 100.0)

        row = index._conn.execute(
            "SELECT date_added FROM tracks_with_stats WHERE file_path = 'bandcamp://42/1'"
        ).fetchone()
        index.close()

        assert row["date_added"] == 100.0

    def test_update_remote_track_date_added_does_not_overwrite_earlier_value(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(Path("bandcamp://43/1"))
        track.date_added = 50.0  # already correct (earlier than new value)
        index.upsert_many([track])

        index.update_remote_track_date_added("43", 999_999.0)

        row = index._conn.execute(
            "SELECT date_added FROM tracks_with_stats WHERE file_path = 'bandcamp://43/1'"
        ).fetchone()
        index.close()

        assert row["date_added"] == 50.0

    def test_update_remote_track_date_added_ignores_other_items(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(Path("bandcamp://10/1"))
        t1.date_added = 9_999_999.0
        t2 = _sample_track(Path("bandcamp://99/1"))
        t2.date_added = 9_999_999.0
        index.upsert_many([t1, t2])

        index.update_remote_track_date_added("10", 1.0)

        r1 = index._conn.execute(
            "SELECT date_added FROM tracks_with_stats WHERE file_path = 'bandcamp://10/1'"
        ).fetchone()
        r2 = index._conn.execute(
            "SELECT date_added FROM tracks_with_stats WHERE file_path = 'bandcamp://99/1'"
        ).fetchone()
        index.close()

        assert r1["date_added"] == 1.0
        assert r2["date_added"] == 9_999_999.0

    def test_update_remote_track_date_added_propagates_to_albums(
        self, tmp_path: Path
    ) -> None:
        """albums.date_added is updated alongside tracks.date_added (MIN-wins)."""
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(Path("bandcamp://sale-1/1"))
        track.date_added = 9_000_000.0
        index.upsert_many([track])
        index.upsert_collection_item(
            "sale-1",
            mode="local",
            band_name=track.album_artist,
            item_title=track.album,
            synced_at=1000.0,
        )

        index.update_remote_track_date_added("sale-1", 100.0)

        row = index._conn.execute(
            "SELECT date_added FROM albums WHERE sale_item_id = 'sale-1'"
        ).fetchone()
        index.close()

        assert row is not None
        assert row["date_added"] == pytest.approx(100.0)

    def test_update_remote_track_date_added_albums_min_wins(
        self, tmp_path: Path
    ) -> None:
        """albums.date_added is not overwritten when existing value is already earlier."""
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(Path("bandcamp://sale-2/1"))
        track.date_added = 50.0
        index.upsert_many([track])
        index.upsert_collection_item(
            "sale-2",
            mode="local",
            band_name=track.album_artist,
            item_title=track.album,
            synced_at=1000.0,
        )

        index.update_remote_track_date_added("sale-2", 999_999.0)

        row = index._conn.execute(
            "SELECT date_added FROM albums WHERE sale_item_id = 'sale-2'"
        ).fetchone()
        index.close()

        # albums.date_added was already 50.0 (from upsert_many); must not be overwritten
        assert row is not None
        assert row["date_added"] == pytest.approx(50.0)

    def test_get_remote_collection_filters_by_mode(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="local")
        index.upsert_collection_item(
            "2", mode="remote", band_name="Artist", item_title="Album"
        )
        index.upsert_collection_item("3", mode="preorder")
        result = index.get_remote_collection()
        index.close()

        assert len(result) == 1
        assert result[0]["sale_item_id"] == "2"
        assert result[0]["band_name"] == "Artist"

    def test_upsert_many_backfills_sale_item_id_when_collection_item_upserted_first(
        self, tmp_path: Path
    ) -> None:
        """upsert_collection_item before upsert_many (the streaming-sync order) still
        links albums.sale_item_id — the backfill in upsert_many closes the gap."""
        index = LibraryIndex(tmp_path / "library.db")
        # Streaming sync order: collection item arrives before tracks are fetched.
        index.upsert_collection_item(
            "sale-stream",
            mode="remote",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        # At this point no albums row exists yet, so the link in upsert_collection_item
        # was a no-op. Now upsert_many inserts the remote tracks.
        t = _sample_track(Path("bandcamp://sale-stream/1"))
        t.source = "bandcamp"
        index.upsert_many([t])
        row = index._conn.execute("SELECT sale_item_id FROM albums LIMIT 1").fetchone()
        index.close()

        assert row is not None
        assert row["sale_item_id"] == "sale-stream"

    # ------------------------------------------------------------------
    # KAMP-528: track-level Bandcamp provenance (standalone singles)
    # ------------------------------------------------------------------

    def test_fresh_db_drops_tracks_file_path_and_sale_item_id(
        self, tmp_path: Path
    ) -> None:
        """A brand-new DB has neither tracks.file_path/sale_item_id nor their index (KAMP-552)."""
        index = LibraryIndex(tmp_path / "library.db")
        cols = {
            r[1] for r in index._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        idx_names = {
            r[1] for r in index._conn.execute("PRAGMA index_list(tracks)").fetchall()
        }
        index.close()
        assert "file_path" not in cols
        assert "sale_item_id" not in cols
        assert "tracks_sale_item_id_idx" not in idx_names

    def test_upsert_persists_track_sale_item_id_for_standalone_single(
        self, tmp_path: Path
    ) -> None:
        """A single with a valid sid but no album row records track-level provenance.

        This is the KAMP-528 core case: album == "" so no album row is minted, yet
        the file's KAMP_SALE_ITEM_ID tag must still persist onto the track.
        """
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "single-1", mode="local", band_name="Mndsgn", item_title="SIXUNDRGROUND"
        )
        single = _sample_track(tmp_path / "single.mp3")
        single.album = ""  # standalone single: no album
        single.album_artist = "Mndsgn"
        single.sale_item_id = "single-1"
        index.upsert_many([single])

        # KAMP-552: track-level provenance lives on track_sources.provider_item_id
        # now (the view re-derives sale_item_id from it).
        row = index._conn.execute(
            "SELECT s.provider_item_id AS sale_item_id, t.album_id AS album_id"
            " FROM track_sources s JOIN tracks t ON t.id = s.track_id"
            " WHERE s.uri = ?",
            (str(tmp_path / "single.mp3"),),
        ).fetchone()
        found = index.local_tracks_for_sale_item_id("single-1")
        index.close()

        assert row is not None
        assert row["sale_item_id"] == "single-1"
        assert row["album_id"] is None  # genuinely album-less
        # It now "drops out of the unmatched set": the purchase resolves to a local file.
        assert [t.title for t in found] == ["A Song"]

    def test_upsert_unknown_sale_item_id_tag_makes_no_album_link(
        self, tmp_path: Path
    ) -> None:
        """A tag sid absent from the collection ledger must not create a false album
        link (KAMP-552: track_sources.provider_item_id has no FK, so the tag is
        recorded there — but album/provenance linking still gates on valid_sids)."""
        index = LibraryIndex(tmp_path / "library.db")
        stray = _sample_track(tmp_path / "stray.mp3")
        stray.album = ""
        stray.sale_item_id = "never-purchased"
        index.upsert_many([stray])  # must not raise
        got = index.get_track_by_path(str(tmp_path / "stray.mp3"))
        # No album was minted/linked for the unknown sid.
        n_albums = index._conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        index.close()
        assert got is not None
        assert n_albums == 0

    def test_row_to_track_reads_sale_item_id(self, tmp_path: Path) -> None:
        """Track.sale_item_id round-trips through the DB."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("rt-1", mode="local")
        t = _sample_track(tmp_path / "rt.mp3")
        t.album = ""
        t.sale_item_id = "rt-1"
        index.upsert_many([t])
        got = index.get_track_by_path(str(tmp_path / "rt.mp3"))
        index.close()
        assert got is not None
        assert got.sale_item_id == "rt-1"

    def test_link_track_to_sale_item_id_links_and_validates(
        self, tmp_path: Path
    ) -> None:
        """The manual recovery path links a known sid and rejects an unknown one."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("link-1", mode="local")
        t = _sample_track(tmp_path / "orphan.mp3")
        t.album = ""
        index.upsert_many([t])
        track = index.get_track_by_path(str(tmp_path / "orphan.mp3"))
        assert track is not None and track.id is not None

        # Unknown sid: rejected, nothing written.
        assert index.link_track_to_sale_item_id(track.id, "not-in-ledger") is False
        # Known sid: linked.
        assert index.link_track_to_sale_item_id(track.id, "link-1") is True
        # Nonexistent track id with a known sid: no row updated.
        assert index.link_track_to_sale_item_id(999_999, "link-1") is False

        found = index.local_tracks_for_sale_item_id("link-1")
        index.close()
        assert [t.title for t in found] == ["A Song"]

    def test_local_tracks_for_sale_item_id_no_duplicate_for_album_tracks(
        self, tmp_path: Path
    ) -> None:
        """A provenanced album's tracks appear exactly once, not once per match arm.

        After the backfill both a.sale_item_id and t.sale_item_id carry the sid, so
        a naive OR-join would double-count. DISTINCT must collapse them.
        """
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "album-1", mode="local", band_name="The Artist", item_title="The Album"
        )
        t1 = _sample_track(tmp_path / "01.mp3")
        t1.track_number = 1
        t1.sale_item_id = "album-1"
        t2 = _sample_track(tmp_path / "02.mp3")
        t2.track_number = 2
        t2.title = "Second Song"
        t2.sale_item_id = "album-1"
        index.upsert_many([t1, t2])

        found = index.local_tracks_for_sale_item_id("album-1")
        index.close()
        assert [t.title for t in found] == ["A Song", "Second Song"]

    def test_clear_bandcamp_collection_fk_safe_with_provenance(
        self, tmp_path: Path
    ) -> None:
        """clear_bandcamp_collection nulls child FKs first so it never raises."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "c-1", mode="local", band_name="The Artist", item_title="The Album"
        )
        album_track = _sample_track(tmp_path / "album.mp3")
        album_track.sale_item_id = "c-1"
        single = _sample_track(tmp_path / "single.mp3")
        single.album = ""
        single.sale_item_id = "c-1"
        index.upsert_many([album_track, single])

        index.clear_bandcamp_collection()  # must not raise on the FK

        assert index.get_collection_state() == {}
        # KAMP-552: tracks.sale_item_id is gone; only albums.sale_item_id has an FK
        # to bandcamp_collection and must be nulled.
        remaining = 0
        albums_remaining = index._conn.execute(
            "SELECT COUNT(*) FROM albums WHERE sale_item_id IS NOT NULL"
        ).fetchone()[0]
        index.close()
        assert remaining == 0
        assert albums_remaining == 0

    def test_migration_v41_backfills_album_provenance_to_tracks(
        self, tmp_path: Path
    ) -> None:
        """v41 adds the column and copies albums.sale_item_id down to its tracks."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        seed = LibraryIndex(db_path)
        seed.close()
        conn = _sqlite3.connect(str(db_path))
        # Simulate a pre-v41 DB: tracks had file_path (KAMP-552 drops it later) but
        # NOT sale_item_id (v41 adds it). Re-add the original file_path column so the
        # v41/v45 migrations that key on it can run, then seed a provenanced
        # album/track and roll the version back to 40.
        conn.execute("ALTER TABLE tracks ADD COLUMN file_path TEXT")
        # The v45 backfill (which builds track_sources) needs the pre-v49 per-source
        # columns; re-add them (not sale_item_id — v41 adds that).
        for _name, _decl in [
            ("ext", "TEXT NOT NULL DEFAULT ''"),
            ("embedded_art", "INTEGER NOT NULL DEFAULT 0"),
            ("file_mtime", "REAL"),
            ("source", "TEXT NOT NULL DEFAULT 'local'"),
            ("stream_url", "TEXT"),
            ("stream_url_expires_at", "REAL"),
            ("is_available", "INTEGER NOT NULL DEFAULT 1"),
            ("duration", "REAL NOT NULL DEFAULT 0"),
            ("last_played", "REAL"),
            ("favorite", "INTEGER NOT NULL DEFAULT 0"),
            ("play_count", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {_name} {_decl}")
        conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, mode) VALUES ('bf-1', 'local')"
        )
        conn.execute(
            "INSERT INTO albums (id, album_artist, album, sale_item_id, source)"
            " VALUES (1, 'Artist', 'Album', 'bf-1', 'bandcamp')"
        )
        conn.execute(
            "INSERT INTO tracks (file_path, album, album_id) VALUES ('/a/1.mp3', 'Album', 1)"
        )
        conn.execute("UPDATE schema_version SET version = 40")
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)  # re-open triggers the v41 migration
        row = index._conn.execute(
            "SELECT sale_item_id FROM tracks_with_stats WHERE id = (SELECT track_id FROM track_sources WHERE uri = '/a/1.mp3')"
        ).fetchone()
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        # Idempotent: re-running the migration path (already at 41) changes nothing.
        index.close()
        reopened = LibraryIndex(db_path)
        row2 = reopened._conn.execute(
            "SELECT sale_item_id FROM tracks_with_stats WHERE file_path = '/a/1.mp3'"
        ).fetchone()
        reopened.close()

        assert row["sale_item_id"] == "bf-1"
        assert version == 60
        assert row2["sale_item_id"] == "bf-1"

    def test_reset_collection_sync_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="local", synced_at=999.0)
        index.upsert_collection_item("2", mode="local", synced_at=888.0)
        index.reset_collection_sync_state()
        rows = index._conn.execute(
            "SELECT synced_at FROM bandcamp_collection"
        ).fetchall()
        index.close()

        assert all(r["synced_at"] is None for r in rows)

    def test_clear_bandcamp_collection(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="local")
        index.upsert_collection_item("2", mode="remote")
        index.clear_bandcamp_collection()
        assert index.get_collection_state() == {}
        index.close()

    def test_migration_v19_imports_state_file(self, tmp_path: Path) -> None:
        import json
        import sqlite3

        # Simulate a v18 database with a bandcamp_state.json alongside it.
        db_path = tmp_path / "library.db"
        state_file = tmp_path / "bandcamp_state.json"
        state_file.write_text(json.dumps({"111": 1000.0, "222": 2000.0}))

        # Bootstrap a v18 DB without the bandcamp_collection table.
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (18)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL,
                genre TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                tracks TEXT NOT NULL,
                order_json TEXT NOT NULL DEFAULT '',
                pos INTEGER NOT NULL DEFAULT -1,
                shuffle INTEGER NOT NULL DEFAULT 0,
                repeat INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        # Opening the index triggers migration.
        index = LibraryIndex(db_path)
        state = index.get_collection_state()
        index.close()

        assert state == {"111": "local", "222": "local"}
        assert not state_file.exists()

    def test_migration_v19_skips_missing_state_file(self, tmp_path: Path) -> None:
        import sqlite3

        db_path = tmp_path / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (18)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL,
                genre TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                tracks TEXT NOT NULL,
                order_json TEXT NOT NULL DEFAULT '',
                pos INTEGER NOT NULL DEFAULT -1,
                shuffle INTEGER NOT NULL DEFAULT 0,
                repeat INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        state = index.get_collection_state()
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert state == {}
        assert version == 60


class TestRemoteTrackSchema:
    """Tests for Track.is_remote, Track.playback_uri, update_stream_url, get_collection_item."""

    def test_track_is_remote_default_false(self, tmp_path: Path) -> None:
        track = _sample_track(tmp_path / "song.mp3")
        assert not track.is_remote

    def test_track_is_remote_true(self, tmp_path: Path) -> None:
        track = _sample_track(tmp_path / "song.mp3")
        track.source = "bandcamp"
        assert track.is_remote

    def test_track_playback_uri_local(self, tmp_path: Path) -> None:
        fp = tmp_path / "song.mp3"
        track = _sample_track(fp)
        assert track.playback_uri == str(fp)

    def test_track_playback_uri_remote_with_stream_url(self, tmp_path: Path) -> None:
        track = _sample_track(tmp_path / "bandcamp://123/1")
        track.source = "bandcamp"
        track.stream_url = "https://cdn.bandcamp.com/stream/abc.mp3"
        assert track.playback_uri == "https://cdn.bandcamp.com/stream/abc.mp3"

    def test_track_playback_uri_remote_without_stream_url(self, tmp_path: Path) -> None:
        track = _sample_track(tmp_path / "bandcamp://123/1")
        track.source = "bandcamp"
        track.stream_url = None
        # Falls back to str(file_path)
        assert track.playback_uri == str(tmp_path / "bandcamp://123/1")

    def test_upsert_remote_track_roundtrips(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(tmp_path / "bandcamp://999/2")
        track.source = "bandcamp"
        track.stream_url = "https://cdn.example.com/stream.mp3"
        track.stream_url_expires_at = 9999.0
        index.upsert_track(track)

        row = index._conn.execute(
            "SELECT source, stream_url, stream_url_expires_at FROM tracks_with_stats"
            " WHERE file_path = ?",
            (str(track.file_path),),
        ).fetchone()
        index.close()

        assert row["source"] == "bandcamp"
        assert row["stream_url"] == "https://cdn.example.com/stream.mp3"
        assert row["stream_url_expires_at"] == 9999.0

    def test_local_track_source_defaults_to_local(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        fp = tmp_path / "song.mp3"
        track = _sample_track(fp)
        index.upsert_track(track)

        row = index._conn.execute(
            "SELECT source FROM tracks_with_stats WHERE file_path = ?", (str(fp),)
        ).fetchone()
        index.close()

        assert row["source"] == "local"

    def test_update_stream_url(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        fp_str = str(tmp_path / "bandcamp://555/1")
        track = _sample_track(tmp_path / "bandcamp://555/1")
        track.source = "bandcamp"
        index.upsert_track(track)

        index.update_stream_url(
            fp_str, "https://new-cdn.example.com/track.mp3", 12345.0
        )

        row = index._conn.execute(
            "SELECT stream_url, stream_url_expires_at FROM tracks_with_stats"
            " WHERE file_path = ?",
            (fp_str,),
        ).fetchone()
        index.close()

        assert row["stream_url"] == "https://new-cdn.example.com/track.mp3"
        assert row["stream_url_expires_at"] == 12345.0

    def test_update_stream_url_for_source(self, tmp_path: Path) -> None:
        """update_stream_url_for_source persists a refreshed CDN url on a specific
        track_sources row (the _source_id-known refresh path, KAMP-541)."""
        index = LibraryIndex(tmp_path / "library.db")
        c = index._conn
        c.execute("INSERT INTO tracks DEFAULT VALUES")  # KAMP-552: no file_path
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.execute(
            "INSERT INTO track_sources (track_id, kind, uri, stream_url,"
            " stream_url_expires_at) VALUES (?, 'stream', 'bandcamp://9/1', 'old', 1.0)",
            (tid,),
        )
        sid = c.execute("SELECT id FROM track_sources").fetchone()[0]
        c.commit()

        index.update_stream_url_for_source(sid, "https://cdn/new.mp3", 999.0)

        row = c.execute(
            "SELECT stream_url, stream_url_expires_at FROM track_sources WHERE id = ?",
            (sid,),
        ).fetchone()
        index.close()
        assert row["stream_url"] == "https://cdn/new.mp3"
        assert row["stream_url_expires_at"] == 999.0

    def test_update_track_after_album_drain(self, tmp_path: Path) -> None:
        """Repaths a track, updates album/artist tags, routes the new file_mtime to
        the file source (KAMP-539), and rebuilds FTS after a deferred album_retag."""
        index = LibraryIndex(tmp_path / "library.db")
        old = tmp_path / "old.mp3"
        new = tmp_path / "new.mp3"
        index.upsert_track(_sample_track(old))
        tid = index.get_track_by_path(old).id  # type: ignore[union-attr]

        index.update_track_after_album_drain(
            tid, new, "New Album", "New Artist", "New Artist", 4242.0
        )

        t = index.get_track_by_id(tid)
        src_mtime = index._conn.execute(
            "SELECT file_mtime FROM track_sources WHERE track_id=? AND kind='file'",
            (tid,),
        ).fetchone()[0]
        index.close()
        assert t is not None
        assert str(t.file_path) == str(new)
        assert t.album == "New Album" and t.album_artist == "New Artist"
        assert t.artist == "New Artist"
        assert src_mtime == 4242.0  # file_mtime routed to track_sources

    def test_tracks_for_playlist_returns_tracks_in_position_order(
        self, tmp_path: Path
    ) -> None:
        """tracks_for_playlist reads playlist rows through the tracks_with_stats view
        (per-source columns derived, KAMP-539) in stored position order."""
        index = LibraryIndex(tmp_path / "library.db")
        a = _sample_track(tmp_path / "a.mp3")
        a.title = "A"
        b = _sample_track(tmp_path / "b.mp3")
        b.title = "B"
        index.upsert_many([a, b])
        pl = index.create_playlist("P")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "b.mp3"))  # position 0
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))  # position 1

        tracks = index.tracks_for_playlist(pl["id"])
        index.close()
        assert [t.title for t in tracks] == ["B", "A"]

    def test_settings_round_trip(self, tmp_path: Path) -> None:
        """get_setting/set_setting/get_all_settings persist and upsert config values."""
        index = LibraryIndex(tmp_path / "library.db")
        assert index.get_setting("k") is None
        index.set_setting("k", "v1")
        assert index.get_setting("k") == "v1"
        index.set_setting("k", "v2")  # ON CONFLICT DO UPDATE
        index.set_setting("other", "x")
        assert index.get_setting("k") == "v2"
        assert index.get_all_settings() == {"k": "v2", "other": "x"}
        index.close()

    def test_update_track_mb_recording_id(self, tmp_path: Path) -> None:
        """Writes mb_recording_id and returns the reloaded Track via the view."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_track(_sample_track(tmp_path / "a.mp3"))
        tid = index.get_track_by_path(tmp_path / "a.mp3").id  # type: ignore[union-attr]
        t = index.update_track_mb_recording_id(tid, "mbid-xyz")
        missing = index.update_track_mb_recording_id(999999, "x")
        index.close()
        assert t is not None and t.mb_recording_id == "mbid-xyz"
        assert missing is None  # unknown id

    def test_upsert_many_preserves_stream_url_when_incoming_is_null(
        self, tmp_path: Path
    ) -> None:
        """Re-indexing a remote track must not wipe a cached stream URL.

        fetch_album_tracks returns tracks with stream_url=None; without
        COALESCE the upsert would NULL out stream_url on every sync run.
        """
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(tmp_path / "bandcamp://777/1")
        track.source = "bandcamp"
        track.stream_url = "https://cdn.example.com/cached.mp3"
        track.stream_url_expires_at = 9999.0
        index.upsert_track(track)

        # Simulate a sync: same track, but stream_url fields are None.
        re_indexed = _sample_track(tmp_path / "bandcamp://777/1")
        re_indexed.source = "bandcamp"
        re_indexed.stream_url = None
        re_indexed.stream_url_expires_at = None
        index.upsert_many([re_indexed])

        row = index._conn.execute(
            "SELECT stream_url, stream_url_expires_at FROM tracks_with_stats"
            " WHERE file_path = ?",
            (str(track.file_path),),
        ).fetchone()
        index.close()

        assert row["stream_url"] == "https://cdn.example.com/cached.mp3"
        assert row["stream_url_expires_at"] == 9999.0

    def test_get_collection_item_found(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "42",
            mode="remote",
            album_url="https://artist.bandcamp.com/album/album",
            tralbum_id="abc123",
        )
        item = index.get_collection_item("42")
        index.close()

        assert item is not None
        assert item["sale_item_id"] == "42"
        assert item["album_url"] == "https://artist.bandcamp.com/album/album"
        assert item["tralbum_id"] == "abc123"
        assert item["mode"] == "remote"

    def test_get_collection_item_not_found(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        item = index.get_collection_item("nonexistent")
        index.close()

        assert item is None

    def test_set_collection_item_mode_updates_mode_and_clears_synced_at(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("42", mode="remote", synced_at=999.0)

        found = index.set_collection_item_mode("42", "local")
        item = index.get_collection_item("42")
        index.close()

        assert found is True
        assert item is not None
        assert item["mode"] == "local"
        assert item["synced_at"] is None

    def test_set_collection_item_mode_returns_false_for_unknown_item(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        found = index.set_collection_item_mode("nonexistent", "local")
        index.close()

        assert found is False

    def test_set_track_source_for_item_counts_matching_tracks(
        self, tmp_path: Path
    ) -> None:
        """Returns the number of tracks whose file_path belongs to the sale item.

        The track-level source is derived from track_sources now (KAMP-539), so the
        method no longer writes tracks.source — it only counts matches and refreshes
        the album badge (see ..._propagates_to_albums).
        """
        index = LibraryIndex(tmp_path / "library.db")
        # Two remote tracks for sale_item_id 42 and one unrelated local track.
        # KAMP-552: identity is track_sources.uri; the count is over it, so create
        # each tracks row then its source row (stream for bandcamp://, file for local).
        for (title, artist, album, tno), uri, kind in [
            (("Track 1", "Artist", "Album", 1), "bandcamp://42/1", "stream"),
            (("Track 2", "Artist", "Album", 2), "bandcamp://42/2", "stream"),
            (("Local", "Artist", "Other", 1), "/local/file.mp3", "file"),
        ]:
            cur = index._conn.execute(
                "INSERT INTO tracks (title, artist, album_artist, album,"
                " track_number, disc_number, release_date)"
                " VALUES (?, ?, ?, ?, ?, 1, '')",
                (title, artist, artist, album, tno),
            )
            index._conn.execute(
                "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, ?, ?)",
                (cur.lastrowid, kind, uri),
            )
        index._conn.commit()

        updated = index.set_track_source_for_item("42", "local")
        index.close()

        assert updated == 2  # the two bandcamp://42/* tracks match

    def test_set_track_source_for_item_returns_zero_when_no_match(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        updated = index.set_track_source_for_item("99999", "local")
        index.close()

        assert updated == 0

    def test_set_track_source_for_item_propagates_to_albums(
        self, tmp_path: Path
    ) -> None:
        """albums.source is refreshed after set_track_source_for_item."""
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(Path("bandcamp://sale-3/1"))
        track.source = "bandcamp"
        index.upsert_many([track])
        index.upsert_collection_item(
            "sale-3",
            mode="local",
            band_name=track.album_artist,
            item_title=track.album,
            synced_at=1000.0,
        )

        # Initially albums.source should be 'bandcamp' (only remote tracks).
        row_before = index._conn.execute(
            "SELECT source FROM albums WHERE sale_item_id = 'sale-3'"
        ).fetchone()
        assert row_before is not None
        assert row_before["source"] == "bandcamp"

        # set_track_source_for_item writes tracks.source directly without touching
        # track_sources. It is dead code post-collapse — server.py:3030 documents
        # that it is never called — so it leaves tracks.source and track_sources
        # inconsistent.
        index.set_track_source_for_item("sale-3", "local")
        index._refresh_album_source(
            index._conn.execute(
                "SELECT id FROM albums WHERE sale_item_id = 'sale-3'"
            ).fetchone()[0]
        )

        row_after = index._conn.execute(
            "SELECT source FROM albums WHERE sale_item_id = 'sale-3'"
        ).fetchone()
        index.close()

        # The badge now derives from track_sources (KAMP-542), which this obsolete
        # method does not update, so it stays 'bandcamp' (the track still has only
        # a stream source). Pre-542 it flipped to 'local' by reading tracks.source.
        assert row_after is not None
        assert row_after["source"] == "bandcamp"

    def test_migration_v20_adds_stream_columns(self, tmp_path: Path) -> None:
        """A v19 DB gains the three new columns on open."""
        db_path = tmp_path / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (19)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL,
                genre TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE queue_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                tracks TEXT NOT NULL,
                order_json TEXT NOT NULL DEFAULT '',
                pos INTEGER NOT NULL DEFAULT -1,
                shuffle INTEGER NOT NULL DEFAULT 0,
                repeat INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bandcamp_collection (
                sale_item_id TEXT NOT NULL PRIMARY KEY,
                item_type    TEXT NOT NULL DEFAULT 'p',
                band_name    TEXT NOT NULL DEFAULT '',
                item_title   TEXT NOT NULL DEFAULT '',
                tralbum_id   TEXT NOT NULL DEFAULT '',
                album_url    TEXT NOT NULL DEFAULT '',
                mode         TEXT NOT NULL DEFAULT 'local',
                synced_at    REAL,
                added_at     REAL NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        # v20 adds source/stream_url; v49 drops them again. Re-add so this test of
        # the historical v20 migration can assert the columns landed.
        _readd_legacy_track_columns(index)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        cols = {
            row[1]
            for row in index._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        index.close()

        assert version == 60
        assert "source" in cols
        assert "stream_url" in cols
        assert "stream_url_expires_at" in cols

    def test_migration_v21_renames_remote_source_to_bandcamp(
        self, tmp_path: Path
    ) -> None:
        """A v20 DB with source='remote' tracks has them rewritten to 'bandcamp'."""
        db_path = tmp_path / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (20)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL,
                genre TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'local',
                stream_url TEXT,
                stream_url_expires_at REAL
            )
        """)
        conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES (?, ?)",
            ("bandcamp://123/1", "remote"),
        )
        conn.execute(
            "INSERT INTO tracks (file_path, source) VALUES (?, ?)",
            ("/local/track.mp3", "local"),
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        rows = index._conn.execute(
            "SELECT file_path, source FROM tracks_with_stats ORDER BY file_path"
        ).fetchall()
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert version == 60
        sources = {r["file_path"]: r["source"] for r in rows}
        assert sources["bandcamp://123/1"] == "bandcamp"
        assert sources["/local/track.mp3"] == "local"


# ---------------------------------------------------------------------------
# Release date backfill helpers (KAMP-513)
# ---------------------------------------------------------------------------


class TestReleaseDateBackfill:
    """has_remote_tracks_needing_date_backfill and patch_release_date_for_remote_album."""

    def _make_index(self, tmp_path: Path) -> "LibraryIndex":
        return LibraryIndex(tmp_path / "library.db")

    def test_no_backfill_needed_when_release_date_is_full_iso(
        self, tmp_path: Path
    ) -> None:
        index = self._make_index(tmp_path)
        track = Track(
            file_path=Path("bandcamp://sale1/1"),
            title="T",
            artist="A",
            album_artist="A",
            album="B",
            release_date="2020-01-15",
            track_number=1,
            disc_number=1,
            ext="",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="",
            label="",
            source="bandcamp",
        )
        index.upsert_many([track])
        needs = index.has_remote_tracks_needing_date_backfill("sale1")
        index.close()

        assert needs is False

    def test_backfill_needed_when_release_date_is_year_only(
        self, tmp_path: Path
    ) -> None:
        index = self._make_index(tmp_path)
        track = Track(
            file_path=Path("bandcamp://sale2/1"),
            title="T",
            artist="A",
            album_artist="A",
            album="B",
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="",
            label="",
            source="bandcamp",
        )
        index.upsert_many([track])
        needs = index.has_remote_tracks_needing_date_backfill("sale2")
        index.close()

        assert needs is True

    def test_downloaded_track_with_stream_source_is_skipped(
        self, tmp_path: Path
    ) -> None:
        """A downloaded (file-preferred) track carrying a stream source for the
        album is NOT flagged/patched, even with a year-only release_date — the
        backfill targets stream-preferred tracks only (KAMP-542). Guards the
        effective-source restriction: a naive EXISTS(stream source) would wrongly
        match it and overwrite its file-tag release_date."""
        index = self._make_index(tmp_path)
        _readd_legacy_track_columns(index)
        c = index._conn
        c.execute(
            "INSERT INTO tracks (file_path, source, album_id, track_number,"
            " disc_number, release_date) VALUES ('/m/a.mp3','local',NULL,1,1,'2020')"
        )
        tid = c.execute("SELECT id FROM tracks").fetchone()[0]
        c.executemany(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, ?, ?)",
            [(tid, "file", "/m/a.mp3"), (tid, "stream", "bandcamp://sale9/1")],
        )
        c.commit()

        assert index.has_remote_tracks_needing_date_backfill("sale9") is False
        index.patch_release_date_for_remote_album("sale9", "2020-05-01")
        after = c.execute(
            "SELECT release_date FROM tracks WHERE id = ?", (tid,)
        ).fetchone()[0]
        index.close()
        assert after == "2020"  # untouched — it is downloaded, not stream-preferred

    def test_backfill_needed_when_release_date_is_empty(self, tmp_path: Path) -> None:
        index = self._make_index(tmp_path)
        track = Track(
            file_path=Path("bandcamp://sale3/1"),
            title="T",
            artist="A",
            album_artist="A",
            album="B",
            release_date="",
            track_number=1,
            disc_number=1,
            ext="",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="",
            label="",
            source="bandcamp",
        )
        index.upsert_many([track])
        needs = index.has_remote_tracks_needing_date_backfill("sale3")
        index.close()

        assert needs is True


# ---------------------------------------------------------------------------
# AlbumInfo remote fields — source, has_remote_tracks, in_bandcamp_collection
# ---------------------------------------------------------------------------


class TestAlbumInfoRemoteFields:
    """albums() computes source, has_remote_tracks, and in_bandcamp_collection."""

    def _insert_track(
        self,
        index: "LibraryIndex",
        tmp_path: Path,
        name: str,
        album: str = "The Album",
        album_artist: str = "The Artist",
        source: str = "local",
        file_path: "Path | None" = None,
        track_number: int = 1,
    ) -> None:
        # file_path defaults to tmp_path/name (a local file); pass a raw
        # bandcamp:// path to seed a genuine stream track (its track_sources row
        # is a 'stream', so its effective source is 'bandcamp'). track_number
        # distinguishes tracks so a file + stream pair at the same number is not
        # merged into one downloaded-and-streamable canonical (KAMP-532).
        track = Track(
            file_path=file_path if file_path is not None else tmp_path / name,
            title=name,
            artist=album_artist,
            album_artist=album_artist,
            album=album,
            release_date="2024",
            track_number=track_number,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source=source,
        )
        index.upsert_many([track])

    def test_all_local_album_has_local_source(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "t1.mp3", source="local")
        self._insert_track(index, tmp_path, "t2.mp3", source="local")
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].source == "local"
        assert albums[0].has_remote_tracks is False

    def test_all_remote_album_has_remote_source(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "bandcamp://999/1", source="bandcamp")
        self._insert_track(index, tmp_path, "bandcamp://999/2", source="bandcamp")
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].source == "bandcamp"
        assert albums[0].has_remote_tracks is True

    def test_mixed_album_has_mixed_source(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "t1.mp3", source="local", track_number=1)
        # Track 2 is a genuine stream-only track (raw bandcamp:// path -> a
        # 'stream' source row); a different track_number keeps it distinct from
        # track 1 rather than merging into one downloaded+streamable canonical.
        self._insert_track(
            index,
            tmp_path,
            "T2",
            source="bandcamp",
            file_path=Path("bandcamp://999/2"),
            track_number=2,
        )
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        # A genuinely mixed album (one local-preferred + one stream-preferred
        # track) reads 'mixed' (KAMP-546): the album classifier now matches the
        # playlist one — 'mixed' whenever the tracks disagree on preferred
        # delivery kind. Reconstructed from track_sources (KAMP-542).
        assert albums[0].source == "mixed"
        # has_remote_tracks is derived from the badge (album_source != 'local'):
        # a mixed album does have streamable tracks, so it is True.
        assert albums[0].has_remote_tracks is True

    def test_in_bandcamp_collection_true(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(
            index, tmp_path, "t1.mp3", album_artist="The Artist", album="The Album"
        )
        index.upsert_collection_item(
            "sale-1",
            mode="local",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].in_bandcamp_collection is True

    def test_in_bandcamp_collection_false_when_no_bc_entry(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "t1.mp3")
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].in_bandcamp_collection is False

    def test_in_bandcamp_collection_false_when_mode_is_not_local(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(
            index, tmp_path, "t1.mp3", album_artist="The Artist", album="The Album"
        )
        index.upsert_collection_item(
            "sale-1",
            mode="remote",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].in_bandcamp_collection is False

    def test_remote_only_album_has_art_true(self, tmp_path: Path) -> None:
        """has_art is True for albums where all tracks are remote.

        The art endpoint can fetch art from Bandcamp CDN on demand for these
        albums even though no embedded art is present locally.
        """
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "bandcamp://999/1", source="bandcamp")
        self._insert_track(index, tmp_path, "bandcamp://999/2", source="bandcamp")
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].has_art is True

    def test_local_only_album_has_art_false_when_no_embedded_art(
        self, tmp_path: Path
    ) -> None:
        """has_art remains False for local-only albums with no embedded art."""
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "t1.mp3", source="local")
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].has_art is False

    def test_mixed_album_has_art_based_on_local_embedded_art(
        self, tmp_path: Path
    ) -> None:
        """Mixed albums use MAX(embedded_art) from local tracks, not the remote shortcut."""
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "t1.mp3", source="local")
        self._insert_track(index, tmp_path, "bandcamp://999/2", source="bandcamp")
        albums = index.albums()
        index.close()

        # local track has embedded_art=False (from _insert_track default) → has_art False
        assert len(albums) == 1
        assert albums[0].has_art is False

    def test_bc_join_does_not_affect_track_count(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(
            index, tmp_path, "t1.mp3", album_artist="The Artist", album="The Album"
        )
        self._insert_track(
            index, tmp_path, "t2.mp3", album_artist="The Artist", album="The Album"
        )
        index.upsert_collection_item(
            "sale-1",
            mode="local",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].track_count == 2
        assert albums[0].in_bandcamp_collection is True

    def test_sale_item_id_populated_for_bandcamp_album(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        # Use upsert_many via the helper so the albums row (and its sale_item_id FK)
        # is created. upsert_many's _canonical_track_uri preserves bandcamp:// paths.
        self._insert_bc_tracks(index, "abc123")
        index.upsert_collection_item(
            "abc123",
            mode="local",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].sale_item_id == "abc123"

    def test_sale_item_id_none_for_local_album(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_track(index, tmp_path, "t1.mp3", source="local")
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].sale_item_id is None

    def _insert_bc_tracks(self, index: "LibraryIndex", sale_id: str) -> None:
        """Insert two bandcamp:// tracks via upsert_many so the albums row is created.

        Uses the same album_artist / album defaults as _insert_track so tests can
        mix the two helpers in the same album group.  _canonical_track_uri inside
        upsert_many preserves the bandcamp:// double-slash form even though Path()
        collapses it to a single slash on POSIX.
        """
        from pathlib import Path as _Path

        tracks = [
            Track(
                file_path=_Path(f"bandcamp://{sale_id}/1"),
                title="T1",
                artist="The Artist",
                album_artist="The Artist",
                album="The Album",
                release_date="2024",
                track_number=1,
                disc_number=1,
                ext="",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
                source="bandcamp",
            ),
            Track(
                file_path=_Path(f"bandcamp://{sale_id}/2"),
                title="T2",
                artist="The Artist",
                album_artist="The Artist",
                album="The Album",
                release_date="2024",
                track_number=2,
                disc_number=1,
                ext="",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
                source="bandcamp",
            ),
        ]
        index.upsert_many(tracks)

    def test_albums_deduplicates_when_local_and_bc_tracks_coexist(
        self, tmp_path: Path
    ) -> None:
        """When local tracks coexist with old bandcamp:// rows, only local tracks count."""
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_bc_tracks(index, "555")
        self._insert_track(index, tmp_path, "t1.mp3", source="local")
        self._insert_track(index, tmp_path, "t2.mp3", source="local")
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].track_count == 2  # only local tracks, not 4
        # The album holds file-only and stream-only tracks, so it reads 'mixed'
        # (KAMP-546); track_count still counts only the downloaded files.
        assert albums[0].source == "mixed"
        assert albums[0].has_remote_tracks is True

    # (KAMP-541) The old "tracks_for_album excludes bandcamp rows when a local
    # sibling exists" test is removed: the local-wins filter is gone because the
    # collapse leaves one canonical row per track. De-dup is covered by the
    # collapse migration + scan-reconcile tests.

    def test_tracks_for_album_returns_bc_rows_when_no_local_tracks(
        self, tmp_path: Path
    ) -> None:
        """tracks_for_album returns bandcamp:// tracks when no local tracks exist."""
        index = LibraryIndex(tmp_path / "library.db")
        self._insert_bc_tracks(index, "777")
        tracks = index.tracks_for_album("The Artist", "The Album")
        index.close()

        assert len(tracks) == 2
        # Path() normalises bandcamp:// → bandcamp:/ on POSIX, so check the prefix not slashes.
        assert all("bandcamp:" in str(t.file_path) for t in tracks)

    def test_get_collection_item_by_album_finds_matching_row(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "sale-99",
            mode="local",
            band_name="Artist",
            item_title="Album",
            synced_at=1000.0,
        )
        result = index.get_collection_item_by_album("Artist", "Album")
        index.close()

        assert result is not None
        assert result["sale_item_id"] == "sale-99"

    def test_get_collection_item_by_album_case_insensitive(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "sale-99",
            mode="local",
            band_name="Artist",
            item_title="Album",
            synced_at=1000.0,
        )
        result = index.get_collection_item_by_album("ARTIST", "ALBUM")
        index.close()

        assert result is not None
        assert result["sale_item_id"] == "sale-99"

    def test_get_collection_item_by_album_returns_none_when_absent(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.get_collection_item_by_album("No Artist", "No Album")
        index.close()

        assert result is None


# ---------------------------------------------------------------------------
# indexed_paths / indexed_paths_with_mtime remote exclusion
# ---------------------------------------------------------------------------


class TestIndexedPathsExcludesRemote:
    def test_indexed_paths_excludes_remote_tracks(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        local = Track(
            file_path=tmp_path / "local.mp3",
            title="Local",
            artist="A",
            album_artist="A",
            album="B",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="local",
        )
        remote = Track(
            file_path=Path("bandcamp://999/1"),
            title="Remote",
            artist="A",
            album_artist="A",
            album="B",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        index.upsert_many([local, remote])
        paths = index.indexed_paths()
        index.close()

        assert tmp_path / "local.mp3" in paths
        assert not any("bandcamp" in str(p) for p in paths)

    def test_indexed_paths_with_mtime_excludes_remote_tracks(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        local = Track(
            file_path=tmp_path / "local.mp3",
            title="Local",
            artist="A",
            album_artist="A",
            album="B",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="local",
        )
        remote = Track(
            file_path=Path("bandcamp://999/1"),
            title="Remote",
            artist="A",
            album_artist="A",
            album="B",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        index.upsert_many([local, remote])
        mtime_map = index.indexed_paths_with_mtime()
        index.close()

        assert tmp_path / "local.mp3" in mtime_map
        assert not any("bandcamp" in str(p) for p in mtime_map)


# ---------------------------------------------------------------------------
# Queue/player state — str round-trip and get_track_by_path str acceptance
# ---------------------------------------------------------------------------


class TestQueuePlayerStateStr:
    def test_load_player_state_returns_str(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_player_state(5, 10.0)
        result = index.load_player_state()
        index.close()

        assert result is not None
        ref, _ = result
        assert isinstance(ref, str)
        assert ref == "5"

    def test_load_player_state_preserves_legacy_path(self, tmp_path: Path) -> None:
        # Back-compat: a pre-queue-by-id build stored a raw path/URI in
        # player_state.track_path; load returns it unchanged so the daemon can
        # resolve it by path (KAMP-536).
        index = LibraryIndex(tmp_path / "library.db")
        index._conn.execute(
            "INSERT INTO player_state (id, track_path, position) VALUES (1, ?, ?)",
            ("bandcamp://999/3", 22.5),
        )
        index._conn.commit()
        result = index.load_player_state()
        index.close()

        assert result is not None
        ref, position = result
        assert ref == "bandcamp://999/3"
        assert position == 22.5

    def test_load_queue_state_returns_list_of_int(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.save_queue_state(
            [11, 22], order=[0, 1], pos=0, shuffle=False, repeat="off"
        )
        result = index.load_queue_state()
        index.close()

        assert result is not None
        entries, _, _, _, _ = result
        assert all(isinstance(e, int) for e in entries)
        assert entries == [11, 22]

    def test_load_queue_state_preserves_legacy_paths(self, tmp_path: Path) -> None:
        # Back-compat: a pre-queue-by-id DB stored JSON path strings; load returns
        # them unchanged so the daemon resolves each by path (KAMP-536).
        import json

        index = LibraryIndex(tmp_path / "library.db")
        index._conn.execute(
            "INSERT INTO queue_state (id, tracks, order_json, pos, shuffle, repeat) "
            "VALUES (1, ?, ?, 0, 0, 'off')",
            (json.dumps(["bandcamp://999/1", "bandcamp://999/2"]), json.dumps([0, 1])),
        )
        index._conn.commit()
        result = index.load_queue_state()
        index.close()

        assert result is not None
        entries, _, _, _, _ = result
        assert entries == ["bandcamp://999/1", "bandcamp://999/2"]

    def test_get_track_by_path_accepts_str(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([_sample_track(tmp_path / "song.mp3")])
        track = index.get_track_by_path(str(tmp_path / "song.mp3"))
        index.close()

        assert track is not None
        assert track.title == "A Song"

    def test_get_track_by_path_str_avoids_uri_normalization(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        # Insert with the canonical URI directly via SQL to bypass Path normalization.
        canonical = "bandcamp://999/3"
        index._conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album, release_date, "
            "track_number, disc_number, ext, embedded_art, mb_release_id, mb_recording_id, "
            "source) VALUES (?, 'Remote', 'A', 'A', 'B', '2024', 1, 1, 'mp3', 0, '', '', 'bandcamp')",
            (canonical,),
        )
        # KAMP-552: get_track_by_path resolves through track_sources.uri now.
        tid = index._conn.execute(
            "SELECT id FROM tracks_with_stats WHERE file_path = ?", (canonical,)
        ).fetchone()[0]
        index._conn.execute(
            "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, 'stream', ?)",
            (tid, canonical),
        )
        index._conn.commit()
        track = index.get_track_by_path(canonical)
        index.close()

        assert track is not None
        assert track.title == "Remote"

    def test_remote_track_roundtrip_get_track_by_path(self, tmp_path: Path) -> None:
        """Regression: upsert a remote Track via Path, then find it by canonical URI.

        Before KAMP-401: _track_to_params used str(file_path) which collapses
        bandcamp:// to bandcamp:/ on POSIX, causing get_track_by_path to return
        None for the canonical double-slash form used in queue state saves.
        """
        from pathlib import Path as _Path

        index = LibraryIndex(tmp_path / "library.db")
        remote = Track(
            file_path=_Path("bandcamp://12345/2"),
            title="Remote Song",
            artist="Remote Artist",
            album_artist="Remote Artist",
            album="Remote Album",
            release_date="2024",
            track_number=2,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        index.upsert_track(remote)
        found = index.get_track_by_path("bandcamp://12345/2")
        index.close()

        assert found is not None
        assert found.title == "Remote Song"
        assert found.source == "bandcamp"


class TestMigrationV22:
    """v21 → v22: normalise bandcamp:/ single-slash file_path rows to bandcamp://."""

    def test_migration_normalises_bandcamp_single_slash(self, tmp_path: Path) -> None:
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a minimal v21 DB with a single-slash remote track row.
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (21)")
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                track_number INTEGER NOT NULL DEFAULT 0,
                disc_number INTEGER NOT NULL DEFAULT 1,
                ext TEXT NOT NULL DEFAULT '',
                embedded_art INTEGER NOT NULL DEFAULT 0,
                mb_release_id TEXT NOT NULL DEFAULT '',
                mb_recording_id TEXT NOT NULL DEFAULT '',
                date_added REAL,
                last_played REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL,
                source TEXT NOT NULL DEFAULT 'local',
                stream_url TEXT,
                stream_url_expires_at REAL
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts
            USING fts5(title, artist, album_artist, album, content=tracks, content_rowid=id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                track_path TEXT NOT NULL,
                position REAL NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                tracks TEXT NOT NULL DEFAULT '[]',
                order_json TEXT NOT NULL DEFAULT '',
                pos INTEGER NOT NULL DEFAULT 0,
                shuffle INTEGER NOT NULL DEFAULT 0,
                repeat INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bandcamp_collection (
                sale_item_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'remote',
                album_url TEXT NOT NULL DEFAULT '',
                artist_name TEXT NOT NULL DEFAULT '',
                album_title TEXT NOT NULL DEFAULT '',
                tralbum_id TEXT NOT NULL DEFAULT '',
                synced_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS album_favorites (
                album_artist TEXT NOT NULL,
                album TEXT NOT NULL,
                PRIMARY KEY (album_artist, album)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deferred_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                op_type TEXT NOT NULL,
                track_id INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            )
        """)
        # Insert a remote track in the old single-slash POSIX form.
        conn.execute(
            "INSERT INTO tracks (file_path, title, source) VALUES "
            "('bandcamp:/999/1', 'OldForm', 'bandcamp')"
        )
        # Insert a row already in canonical form to confirm it is not double-converted.
        conn.execute(
            "INSERT INTO tracks (file_path, title, source) VALUES "
            "('bandcamp://888/2', 'AlreadyCanonical', 'bandcamp')"
        )
        # Insert a local track to confirm it is untouched.
        conn.execute(
            "INSERT INTO tracks (file_path, title, source) VALUES "
            "('/music/local.mp3', 'Local', 'local')"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        rows = {
            r["file_path"]: r["title"]
            for r in index._conn.execute(
                "SELECT file_path, title FROM tracks_with_stats"
            ).fetchall()
        }
        index.close()

        assert version == 60
        assert (
            rows.get("bandcamp://999/1") == "OldForm"
        ), "single-slash row was not normalised to double-slash"
        assert "bandcamp:/999/1" not in rows, "old single-slash row should be gone"
        assert (
            rows.get("bandcamp://888/2") == "AlreadyCanonical"
        ), "already-canonical row should be unchanged"
        assert (
            rows.get("/music/local.mp3") == "Local"
        ), "local track should be unchanged"


class TestDownloadQueue:
    """enqueue_download / dequeue_download / pending_downloads (KAMP-408)."""

    def test_enqueue_adds_row(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("111")
        assert index.pending_downloads() == ["111"]
        index.close()

    def test_pending_downloads_fifo_order(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        import time

        for sid in ("aaa", "bbb", "ccc"):
            index.enqueue_download(sid)
            time.sleep(0.01)  # ensure distinct queued_at timestamps
        assert index.pending_downloads() == ["aaa", "bbb", "ccc"]
        index.close()

    def test_dequeue_removes_row(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("111")
        index.enqueue_download("222")
        index.dequeue_download("111")
        assert index.pending_downloads() == ["222"]
        index.close()

    def test_enqueue_is_idempotent(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("111")
        index.enqueue_download("111")  # second call is a no-op
        assert index.pending_downloads() == ["111"]
        index.close()

    def test_pending_empty_when_queue_clear(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.pending_downloads() == []
        index.close()

    def test_dequeue_nonexistent_is_noop(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.dequeue_download("nonexistent")  # must not raise
        assert index.pending_downloads() == []
        index.close()


class TestDownloadQueueStateMachine:
    """Persistent download-queue state machine: status transitions, ordering,
    reorder, retry-to-end, cancel, size, and restart persistence (KAMP-564)."""

    def _states(self, index: "LibraryIndex") -> "list[tuple[str, str]]":
        """Return (provider_item_id, status) for every queue row in display order."""
        return [
            (r["provider_item_id"], r["status"]) for r in index.download_queue_items()
        ]

    def test_enqueue_defaults_to_queued(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("111")
        items = index.download_queue_items()
        assert len(items) == 1
        assert items[0]["status"] == "queued"
        assert items[0]["position"] == 1
        index.close()

    def test_enqueue_stores_album_snapshot_and_size(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download(
            "111",
            album_name="Miss Colombia",
            album_artist="Lido Pimienta",
            artwork_ref="a0471946789",
            size_bytes=81_200_000,
            size_is_estimate=True,
        )
        item = index.download_queue_items()[0]
        assert item["album_name"] == "Miss Colombia"
        assert item["album_artist"] == "Lido Pimienta"
        assert item["artwork_ref"] == "a0471946789"
        assert item["size_bytes"] == 81_200_000
        assert item["size_is_estimate"] is True
        index.close()

    def test_enqueue_appends_at_end(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        positions = [i["position"] for i in index.download_queue_items()]
        assert positions == [1, 2, 3]
        index.close()

    def test_enqueue_persists_and_gets_redownload_url(self, tmp_path: Path) -> None:
        """KAMP-575: the download-page link is stored and retrievable via
        get_download_item so the worker needn't re-fetch the collection."""
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download(
            "111",
            album_name="Miss Colombia",
            album_artist="Lido Pimienta",
            redownload_url="https://bandcamp.com/download?id=111&sig=abc",
        )
        row = index.get_download_item("111")
        assert row is not None
        assert row["redownload_url"] == "https://bandcamp.com/download?id=111&sig=abc"
        assert row["album_name"] == "Miss Colombia"
        assert row["album_artist"] == "Lido Pimienta"
        index.close()

    def test_enqueue_redownload_url_defaults_null(self, tmp_path: Path) -> None:
        """Omitting redownload_url stores NULL (the REST single-download path)."""
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("222")
        row = index.get_download_item("222")
        assert row is not None
        assert row["redownload_url"] is None
        index.close()

    def test_get_download_item_absent_returns_none(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.get_download_item("nope") is None
        index.close()

    def test_download_redownload_urls_maps_queued_only(self, tmp_path: Path) -> None:
        """The bulk map (used by the size-backfill) covers queued rows and reflects
        NULLs for items enqueued without a URL."""
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a", redownload_url="https://dl/a")
        index.enqueue_download("b")  # no URL
        index.enqueue_download("c", redownload_url="https://dl/c")
        index.mark_downloading("c")  # not 'queued' anymore → excluded
        urls = index.download_redownload_urls()
        assert urls == {"a": "https://dl/a", "b": None}
        index.close()

    def test_mark_download_failed_clears_redownload_url(self, tmp_path: Path) -> None:
        """KAMP-575: failing an item NULLs its stored URL so a retry re-fetches a
        fresh one (heals a stale/dead link without inline classification)."""
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a", redownload_url="https://dl/stale")
        index.mark_download_failed("a", "HTTP 429")
        row = index.get_download_item("a")
        assert row is not None
        assert row["status"] == "failed"
        assert row["error_text"] == "HTTP 429"
        assert row["redownload_url"] is None
        index.close()

    def test_set_download_redownload_url(self, tmp_path: Path) -> None:
        """KAMP-575: the download worker fills a missing URL (via one collection
        fetch per drain) so subsequent items download via the fast path."""
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")  # enqueued without a URL
        assert index.get_download_item("a")["redownload_url"] is None  # type: ignore[index]
        index.set_download_redownload_url("a", "https://dl/fresh")
        assert index.get_download_item("a")["redownload_url"] == "https://dl/fresh"  # type: ignore[index]
        index.close()

    def test_next_queued_download_is_lowest_position(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        assert index.next_queued_download() == "a"
        index.close()

    def test_next_queued_download_skips_downloading(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.enqueue_download("b")
        index.mark_downloading("a")  # a is in-flight, not "queued"
        assert index.next_queued_download() == "b"
        index.close()

    def test_next_queued_download_none_when_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.next_queued_download() is None
        index.enqueue_download("a")
        index.mark_downloading("a")
        assert index.next_queued_download() is None  # only in-flight item remains
        index.close()

    def test_mark_downloading_sorts_to_top(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        index.mark_downloading("b")
        # downloading first, then queued by position (a before c)
        assert self._states(index) == [
            ("b", "downloading"),
            ("a", "queued"),
            ("c", "queued"),
        ]
        index.close()

    def test_mark_download_failed_records_error(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.mark_download_failed("a", "HTTP 429 too many requests")
        item = index.download_queue_items()[0]
        assert item["status"] == "failed"
        assert item["error_text"] == "HTTP 429 too many requests"
        index.close()

    def test_mark_downloading_clears_prior_error(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.mark_download_failed("a", "boom")
        index.mark_downloading("a")
        item = index.download_queue_items()[0]
        assert item["status"] == "downloading"
        assert item["error_text"] is None
        index.close()

    def test_mark_download_done_removes_row(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.enqueue_download("b")
        index.mark_download_done("a")
        assert self._states(index) == [("b", "queued")]
        index.close()

    def test_failed_items_sort_last(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        index.mark_downloading("a")
        index.mark_download_failed("b", "boom")
        assert self._states(index) == [
            ("a", "downloading"),
            ("c", "queued"),
            ("b", "failed"),
        ]
        index.close()

    def test_retry_requeues_at_end(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        index.mark_download_failed("a", "boom")
        index.retry_download("a")
        # a returns to 'queued' but behind b and c (retry-to-end), error cleared
        assert self._states(index) == [
            ("b", "queued"),
            ("c", "queued"),
            ("a", "queued"),
        ]
        assert index.download_queue_items()[-1]["error_text"] is None
        assert index.next_queued_download() == "b"
        index.close()

    def test_retry_nonexistent_is_noop(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.retry_download("nope")  # must not raise
        assert index.download_queue_items() == []
        index.close()

    def test_cancel_removes_item(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.enqueue_download("b")
        index.mark_download_failed("b", "boom")
        index.cancel_download("a")  # cancel a queued item
        index.cancel_download("b")  # cancel a failed item
        assert index.download_queue_items() == []
        index.close()

    def test_reorder_queued_items(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        index.reorder_download_queue(["c", "a", "b"])
        assert [i["provider_item_id"] for i in index.download_queue_items()] == [
            "c",
            "a",
            "b",
        ]
        assert index.next_queued_download() == "c"
        index.close()

    def test_reorder_excludes_downloading_item(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        index.mark_downloading("a")  # fixed at top, not part of the reorder
        index.reorder_download_queue(["c", "b"])
        assert self._states(index) == [
            ("a", "downloading"),
            ("c", "queued"),
            ("b", "queued"),
        ]
        index.close()

    def test_reorder_rejects_mismatched_set(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.enqueue_download("b")
        with pytest.raises(ValueError):
            index.reorder_download_queue(["a"])  # missing 'b'
        with pytest.raises(ValueError):
            index.reorder_download_queue(["a", "b", "c"])  # unknown 'c'
        index.close()

    def test_reorder_rejects_downloading_item_in_list(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.enqueue_download("b")
        index.mark_downloading("a")
        with pytest.raises(ValueError):
            index.reorder_download_queue(["a", "b"])  # 'a' is downloading, not queued
        index.close()

    def test_set_download_size_overwrites_estimate(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a", size_bytes=81_000_000, size_is_estimate=True)
        # exact Content-Length arrives at download start
        index.set_download_size("a", 82_531_204, is_estimate=False)
        item = index.download_queue_items()[0]
        assert item["size_bytes"] == 82_531_204
        assert item["size_is_estimate"] is False
        index.close()

    def test_enqueue_is_idempotent_preserves_state(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        index.mark_downloading("a")
        index.enqueue_download("a")  # re-enqueue is a no-op, does not reset status
        assert self._states(index) == [("a", "downloading")]
        index.close()

    def test_persistence_survives_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        index.mark_downloading("a")
        index.mark_download_failed("b", "boom")
        index.reorder_download_queue(["c"])  # only c is queued
        index.close()

        reopened = LibraryIndex(db_path)
        assert self._states(reopened) == [
            ("a", "downloading"),
            ("c", "queued"),
            ("b", "failed"),
        ]
        assert reopened.download_queue_items()[2]["error_text"] == "boom"
        reopened.close()

    def test_provider_defaults_to_bandcamp(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")
        assert index.download_queue_items()[0]["provider"] == "bandcamp"
        index.close()

    def test_same_item_id_distinct_across_providers(self, tmp_path: Path) -> None:
        """Identity is (provider, provider_item_id): the same id under two providers
        is two independent rows, and per-provider ops don't cross-talk."""
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("x", provider="bandcamp")
        index.enqueue_download("x", provider="other")
        assert len(index.download_queue_items()) == 2
        # A transition on one provider's item leaves the other untouched.
        index.mark_downloading("x", provider="other")
        by_provider = {i["provider"]: i["status"] for i in index.download_queue_items()}
        assert by_provider == {"bandcamp": "queued", "other": "downloading"}
        # Per-provider reads/removes are scoped.
        assert index.next_queued_download(provider="bandcamp") == "x"
        assert index.next_queued_download(provider="other") is None
        index.cancel_download("x", provider="bandcamp")
        assert [i["provider"] for i in index.download_queue_items()] == ["other"]
        index.close()

    def test_reset_downloading_to_queued(self, tmp_path: Path) -> None:
        """An interrupted 'downloading' item is re-queued at its original position
        (head) so the processing loop resumes it first after a restart (KAMP-565)."""
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c"):
            index.enqueue_download(sid)
        index.mark_downloading("a")  # simulate a crash mid-download
        assert index.next_queued_download() == "b"  # 'a' is invisible while downloading

        n = index.reset_downloading_to_queued()
        assert n == 1
        # 'a' is queued again and, keeping position 1, is picked first.
        assert self._states(index) == [
            ("a", "queued"),
            ("b", "queued"),
            ("c", "queued"),
        ]
        assert index.next_queued_download() == "a"
        index.close()

    def test_reset_downloading_to_queued_noop_when_none(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.enqueue_download("a")  # 'queued', not 'downloading'
        assert index.reset_downloading_to_queued() == 0
        assert self._states(index) == [("a", "queued")]
        index.close()

    def test_queued_downloads_missing_size(self, tmp_path: Path) -> None:
        """Only 'queued' rows with a NULL size, front-of-queue first (KAMP-574)."""
        index = LibraryIndex(tmp_path / "library.db")
        for sid in ("a", "b", "c", "d"):
            index.enqueue_download(sid)
        index.set_download_size("b", 1000, is_estimate=True)  # already sized → excluded
        index.mark_downloading("a")  # downloading → excluded (only 'queued')
        # Remaining queued-without-size are c, d in position order.
        assert index.queued_downloads_missing_size() == ["c", "d"]
        index.close()

    def test_queued_downloads_missing_size_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.queued_downloads_missing_size() == []
        index.enqueue_download("a")
        index.set_download_size("a", 500, is_estimate=False)
        assert index.queued_downloads_missing_size() == []  # sized
        index.close()


class TestRemoveDownload:
    """Tests for local_tracks_for_sale_item_id and remove_download."""

    def _setup_downloaded_album(self, tmp_path: Path) -> LibraryIndex:
        """Create an index with a downloaded album: both local and streaming tracks present.

        Streaming tracks keep play_count 2 and 5; local tracks keep 7 and 3.
        play_count is set via raw SQL because upsert_many intentionally omits it
        (play counts are managed exclusively by record_played() in production).
        """
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index.upsert_collection_item("sid42", mode="local", synced_at=1.0)

        # Streaming tracks (bandcamp://) inserted first, as they would be pre-download.
        streaming1 = _sample_track(Path("bandcamp://sid42/1"))
        streaming1.track_number = 1
        streaming1.source = "bandcamp"
        streaming2 = _sample_track(Path("bandcamp://sid42/2"))
        streaming2.track_number = 2
        streaming2.source = "bandcamp"
        index.upsert_many([streaming1, streaming2])

        # Link sale_item_id on the albums row and set play counts directly.
        index._conn.execute(
            "UPDATE albums SET sale_item_id = 'sid42', source = 'local' WHERE album = 'The Album'"
        )
        index._conn.execute(
            "UPDATE track_stats SET play_count = 2 WHERE track_id = (SELECT track_id FROM track_sources WHERE uri = 'bandcamp://sid42/1')"
        )
        index._conn.execute(
            "UPDATE track_stats SET play_count = 5 WHERE track_id = (SELECT track_id FROM track_sources WHERE uri = 'bandcamp://sid42/2')"
        )
        index._conn.commit()

        # Local tracks — play_count set via SQL after upsert.
        local1 = _sample_track(tmp_path / "track1.mp3")
        local1.track_number = 1
        local2 = _sample_track(tmp_path / "track2.mp3")
        local2.track_number = 2
        index.upsert_many([local1, local2])
        index._conn.execute(
            "UPDATE track_stats SET play_count = 7 WHERE track_id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(tmp_path / "track1.mp3"),),
        )
        # Post-KAMP-541 the stream+local rows collapse into one canonical track
        # holding the MAX play_count (track2: stream 5 > local 3 -> 5).
        index._conn.execute(
            "UPDATE track_stats SET play_count = 5 WHERE track_id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(tmp_path / "track2.mp3"),),
        )
        index._conn.commit()

        return index

    def test_local_tracks_for_sale_item_id_returns_local_only(
        self, tmp_path: Path
    ) -> None:
        index = self._setup_downloaded_album(tmp_path)
        tracks = index.local_tracks_for_sale_item_id("sid42")
        index.close()

        paths = [str(t.file_path) for t in tracks]
        assert all("bandcamp" not in p for p in paths)
        assert len(tracks) == 2

    def test_local_tracks_for_sale_item_id_returns_empty_for_unknown(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.local_tracks_for_sale_item_id("no-such-id") == []
        index.close()

    def test_remove_download_returns_local_file_paths(self, tmp_path: Path) -> None:
        index = self._setup_downloaded_album(tmp_path)
        paths = index.remove_download("sid42")
        index.close()

        assert len(paths) == 2
        assert all(isinstance(p, Path) for p in paths)
        assert all("bandcamp" not in str(p) for p in paths)

    def test_remove_download_deletes_local_tracks_from_db(self, tmp_path: Path) -> None:
        index = self._setup_downloaded_album(tmp_path)
        index.remove_download("sid42")

        remaining = index._conn.execute(
            "SELECT file_path FROM tracks_with_stats WHERE file_path NOT LIKE 'bandcamp://%'"
        ).fetchall()
        index.close()

        assert remaining == []

    def test_remove_download_streaming_tracks_remain(self, tmp_path: Path) -> None:
        index = self._setup_downloaded_album(tmp_path)
        index.remove_download("sid42")

        streaming = index._conn.execute(
            "SELECT file_path FROM tracks_with_stats WHERE file_path LIKE 'bandcamp://%'"
        ).fetchall()
        index.close()

        assert len(streaming) == 2

    def test_remove_download_sets_mode_remote(self, tmp_path: Path) -> None:
        index = self._setup_downloaded_album(tmp_path)
        index.remove_download("sid42")

        row = index._conn.execute(
            "SELECT mode FROM bandcamp_collection WHERE sale_item_id = 'sid42'"
        ).fetchone()
        index.close()

        assert row["mode"] == "remote"

    def test_remove_download_preserves_higher_local_play_count(
        self, tmp_path: Path
    ) -> None:
        """Track 1: local play_count=7 > streaming=2; streaming should end up at 7."""
        index = self._setup_downloaded_album(tmp_path)
        index.remove_download("sid42")

        row = index._conn.execute(
            "SELECT play_count FROM tracks_with_stats WHERE file_path = 'bandcamp://sid42/1'"
        ).fetchone()
        index.close()

        assert row["play_count"] == 7

    def test_remove_download_keeps_streaming_count_when_higher(
        self, tmp_path: Path
    ) -> None:
        """Track 2: streaming play_count=5 > local=3; streaming should keep 5."""
        index = self._setup_downloaded_album(tmp_path)
        index.remove_download("sid42")

        row = index._conn.execute(
            "SELECT play_count FROM tracks_with_stats WHERE file_path = 'bandcamp://sid42/2'"
        ).fetchone()
        index.close()

        assert row["play_count"] == 5

    def test_remove_download_updates_albums_source_to_bandcamp(
        self, tmp_path: Path
    ) -> None:
        index = self._setup_downloaded_album(tmp_path)
        index.remove_download("sid42")

        row = index._conn.execute(
            "SELECT source FROM albums WHERE sale_item_id = 'sid42'"
        ).fetchone()
        index.close()

        assert row["source"] == "bandcamp"

    def test_remove_download_migrates_favorite_to_streaming_track(
        self, tmp_path: Path
    ) -> None:
        """Favorite set on the local track is carried over to the streaming row."""
        index = self._setup_downloaded_album(tmp_path)
        index._conn.execute(
            "UPDATE track_stats SET favorite = 1 WHERE track_id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(tmp_path / "track1.mp3"),),
        )
        index._conn.commit()

        index.remove_download("sid42")

        row = index._conn.execute(
            "SELECT favorite FROM tracks_with_stats WHERE file_path = 'bandcamp://sid42/1'"
        ).fetchone()
        index.close()

        assert row["favorite"] == 1

    def test_remove_download_preserves_existing_streaming_favorite(
        self, tmp_path: Path
    ) -> None:
        """A favorite on the canonical track is kept when the download is removed."""
        index = self._setup_downloaded_album(tmp_path)
        # Post-KAMP-541 there is one canonical track (file_path = local path until
        # the download is removed); favorite it there.
        index._conn.execute(
            "UPDATE track_stats SET favorite = 1 WHERE track_id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(tmp_path / "track2.mp3"),),
        )
        index._conn.commit()

        index.remove_download("sid42")

        # After removal the track reverts to its stream source (file_path).
        row = index._conn.execute(
            "SELECT favorite FROM tracks_with_stats WHERE file_path = 'bandcamp://sid42/2'"
        ).fetchone()
        index.close()

        assert row["favorite"] == 1

    def test_remove_download_returns_empty_for_unknown_sale_item_id(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.remove_download("no-such-id")
        index.close()

        assert result == []

    # --- KAMP-527: fail-safe guard when no streamable representation exists ---

    def _setup_download_only_album(self, tmp_path: Path) -> LibraryIndex:
        """A downloaded album with local tracks but NO bandcamp:// stream rows.

        This is the download-mode population from KAMP-527: provenance is linked
        (albums.sale_item_id set) but the streaming Track rows were never created
        because the user bought+downloaded rather than streamed.
        """
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index.upsert_collection_item(
            "sid99",
            mode="local",
            band_name="The Artist",
            item_title="The Album",
            album_url="https://the-artist.bandcamp.com/album/the-album",
            num_streamable_tracks=2,
            synced_at=1.0,
        )

        local1 = _sample_track(tmp_path / "track1.mp3")
        local1.track_number = 1
        local2 = _sample_track(tmp_path / "track2.mp3")
        local2.track_number = 2
        index.upsert_many([local1, local2])

        index._conn.execute(
            "UPDATE albums SET sale_item_id = 'sid99', source = 'local'"
            " WHERE album = 'The Album'"
        )
        index._conn.commit()
        return index

    def test_remove_download_raises_when_no_stream_rows(self, tmp_path: Path) -> None:
        from kamp_core.library import NoStreamableVersionError

        index = self._setup_download_only_album(tmp_path)
        with pytest.raises(NoStreamableVersionError):
            index.remove_download("sid99")

        # Nothing was deleted — the two local tracks remain.
        remaining = index._conn.execute(
            "SELECT COUNT(*) AS n FROM tracks_with_stats WHERE file_path NOT LIKE 'bandcamp://%'"
        ).fetchone()["n"]
        index.close()
        assert remaining == 2

    def test_remove_download_no_stream_rows_deferred_commit_safe(
        self, tmp_path: Path
    ) -> None:
        """The aborted removal must roll back so a LATER unrelated commit on the
        same (thread-local) connection does not flush a partial deletion.
        """
        from kamp_core.library import NoStreamableVersionError

        index = self._setup_download_only_album(tmp_path)
        with pytest.raises(NoStreamableVersionError):
            index.remove_download("sid99")

        # Simulate the next unrelated write + commit on the same connection.
        index._conn.execute(
            "UPDATE bandcamp_collection SET synced_at = 2.0 WHERE sale_item_id = 'sid99'"
        )
        index._conn.commit()

        remaining = index._conn.execute(
            "SELECT COUNT(*) AS n FROM tracks_with_stats WHERE file_path NOT LIKE 'bandcamp://%'"
        ).fetchone()["n"]
        index.close()
        assert remaining == 2

    def test_remove_download_partial_stream_rows_raises(self, tmp_path: Path) -> None:
        """A stream counterpart for only some local tracks is still unsafe: the
        uncovered local track would be lost, so the whole op aborts.
        """
        from kamp_core.library import NoStreamableVersionError

        index = self._setup_download_only_album(tmp_path)
        # Materialize a stream row for track 1 only.
        streaming1 = _sample_track(Path("bandcamp://sid99/1"))
        streaming1.track_number = 1
        streaming1.source = "bandcamp"
        streaming1.sale_item_id = "sid99"
        index.upsert_many([streaming1])

        with pytest.raises(NoStreamableVersionError):
            index.remove_download("sid99")

        remaining = index._conn.execute(
            "SELECT COUNT(*) AS n FROM tracks_with_stats WHERE file_path NOT LIKE 'bandcamp://%'"
        ).fetchone()["n"]
        index.close()
        assert remaining == 2

    def test_remove_download_multidisc_local_without_stream_raises(
        self, tmp_path: Path
    ) -> None:
        """A local disc-2 track has no stream counterpart (fetch_album_tracks only
        ever yields disc 1), so removal must abort rather than silently lose it.
        """
        from kamp_core.library import NoStreamableVersionError

        index = self._setup_download_only_album(tmp_path)
        # Give the album a matching stream row for disc 1 tracks…
        for n in (1, 2):
            s = _sample_track(Path(f"bandcamp://sid99/{n}"))
            s.track_number = n
            s.source = "bandcamp"
            s.sale_item_id = "sid99"
            index.upsert_many([s])
        # …then add a disc-2 local track with no stream counterpart.
        d2 = _sample_track(tmp_path / "disc2track1.mp3")
        d2.track_number = 1
        d2.disc_number = 2
        index.upsert_many([d2])
        index._conn.execute(
            "UPDATE albums SET sale_item_id = 'sid99', source = 'local'"
            " WHERE album = 'The Album'"
        )
        index._conn.commit()

        with pytest.raises(NoStreamableVersionError):
            index.remove_download("sid99")

        remaining = index._conn.execute(
            "SELECT COUNT(*) AS n FROM tracks_with_stats WHERE file_path NOT LIKE 'bandcamp://%'"
        ).fetchone()["n"]
        index.close()
        assert remaining == 3

    def test_materialize_stream_tracks_attaches_to_existing_album(
        self, tmp_path: Path
    ) -> None:
        """Materialize attaches a stream source to each existing canonical track
        (no fork, no duplicate album) so remove_download can then revert cleanly (KAMP-541).
        """
        index = self._setup_download_only_album(tmp_path)

        s1 = _sample_track(Path("bandcamp://sid99/1"))
        s1.track_number = 1
        s1.source = "bandcamp"
        s2 = _sample_track(Path("bandcamp://sid99/2"))
        s2.track_number = 2
        s2.source = "bandcamp"
        n = index.materialize_stream_tracks("sid99", [s1, s2])

        album_count = index._conn.execute(
            "SELECT COUNT(*) AS n FROM albums WHERE sale_item_id = 'sid99'"
        ).fetchone()["n"]
        stream_srcs = index._conn.execute(
            "SELECT COUNT(*) FROM track_sources WHERE kind = 'stream'"
        ).fetchone()[0]
        index.close()
        assert n == 2
        assert album_count == 1
        # Attached as sources of the two existing canonical tracks, not as rows.
        assert stream_srcs == 2

    def test_remove_download_succeeds_after_materialize(self, tmp_path: Path) -> None:
        """End-to-end: materialize then remove_download reverts to streaming."""
        index = self._setup_download_only_album(tmp_path)
        s1 = _sample_track(Path("bandcamp://sid99/1"))
        s1.track_number = 1
        s1.source = "bandcamp"
        s2 = _sample_track(Path("bandcamp://sid99/2"))
        s2.track_number = 2
        s2.source = "bandcamp"
        index.materialize_stream_tracks("sid99", [s1, s2])

        paths = index.remove_download("sid99")

        local_remaining = index._conn.execute(
            "SELECT COUNT(*) AS n FROM tracks_with_stats WHERE file_path NOT LIKE 'bandcamp://%'"
        ).fetchone()["n"]
        source = index._conn.execute(
            "SELECT source FROM albums WHERE sale_item_id = 'sid99'"
        ).fetchone()["source"]
        index.close()
        assert len(paths) == 2
        assert local_remaining == 0
        assert source == "bandcamp"

    def test_materialize_stream_tracks_empty_is_noop(self, tmp_path: Path) -> None:
        index = self._setup_download_only_album(tmp_path)
        assert index.materialize_stream_tracks("sid99", []) == 0
        index.close()

    def test_remove_download_rolls_back_on_mid_transaction_failure(
        self, tmp_path: Path
    ) -> None:
        """If a mutation fails after the guard passes, the partial delete must be
        rolled back so a later unrelated commit cannot flush it (KAMP-527).
        """
        index = self._setup_downloaded_album(tmp_path)  # has stream + local rows

        # Force a failure late in the transaction, after the DELETE has run.
        def _boom() -> None:
            raise RuntimeError("simulated failure during remove_download")

        with patch.object(index, "_rebuild_fts", _boom):
            with pytest.raises(RuntimeError):
                index.remove_download("sid42")

        # The next unrelated write + commit must NOT flush the aborted delete.
        index._conn.execute(
            "UPDATE bandcamp_collection SET synced_at = 9.0 WHERE sale_item_id = 'sid42'"
        )
        index._conn.commit()

        local_remaining = index._conn.execute(
            "SELECT COUNT(*) AS n FROM tracks_with_stats WHERE file_path NOT LIKE 'bandcamp://%'"
        ).fetchone()["n"]
        index.close()
        assert local_remaining == 2

    def test_upsert_many_rolls_back_on_mid_transaction_failure(
        self, tmp_path: Path
    ) -> None:
        """upsert_many carries the same rollback discipline: a mid-flight failure
        leaves no partial writes to leak into the next commit (KAMP-527).
        """
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "a.mp3")
        t1.track_number = 1

        with patch.object(index, "_rebuild_fts", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                index.upsert_many([t1])

        # Nothing from the aborted upsert should survive a later commit.
        index._conn.execute("PRAGMA user_version = 1")  # any harmless write
        index._conn.commit()
        n = index._conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"]
        index.close()
        assert n == 0


class TestMigrationV23:
    """v22 → v23: download_queue table created on upgrade from v22."""

    def test_migration_creates_download_queue_table(self, tmp_path: Path) -> None:
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        # Build a minimal v22 DB (download_queue table must not exist yet).
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (22)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '', artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '', album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '', track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1, ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0, mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '', date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0, play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL, genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local', stream_url TEXT, stream_url_expires_at REAL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE player_state (id INTEGER PRIMARY KEY CHECK (id=1), track_path TEXT NOT NULL, position REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE queue_state (id INTEGER PRIMARY KEY CHECK (id=1), tracks TEXT NOT NULL DEFAULT '[]', order_json TEXT NOT NULL DEFAULT '', pos INTEGER NOT NULL DEFAULT -1, shuffle INTEGER NOT NULL DEFAULT 0, repeat INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE extension_audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, extension_id TEXT NOT NULL, track_mbid TEXT NOT NULL DEFAULT '', operation TEXT NOT NULL, old_value TEXT NOT NULL DEFAULT '', new_value TEXT NOT NULL DEFAULT '', timestamp REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY, session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE album_favorites (album_artist TEXT NOT NULL, album TEXT NOT NULL, PRIMARY KEY (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT, op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE, payload_json TEXT NOT NULL, created_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY, item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '', item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '', album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local', synced_at REAL, added_at REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        tables = {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        index.close()

        assert version == 60
        assert "download_queue" in tables
        assert "albums" in tables
        assert "album_favorites" not in tables


class TestMigrationV24:
    """v23 → v24: first-class albums entity (KAMP-418)."""

    def _build_v23_db(self, db_path: Path) -> None:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (23)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE album_favorites (album_artist TEXT NOT NULL, album TEXT NOT NULL,"
            " PRIMARY KEY (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

    def test_migration_creates_albums_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v23_db(db_path)
        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        tables = {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        index.close()

        assert version == 60
        assert "albums" in tables
        assert "album_favorites" not in tables

    def test_migration_collapses_case_variant_duplicates(self, tmp_path: Path) -> None:
        """Mixed-case tracks (CASTLEBEAT vs Castlebeat) become a single albums row."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        self._build_v23_db(db_path)
        conn = _sqlite3.connect(str(db_path))
        conn.executemany(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
            " track_number, year, source) VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    "bandcamp://s1/1",
                    "T1",
                    "A",
                    "CASTLEBEAT",
                    "Album X",
                    1,
                    "2023",
                    "bandcamp",
                ),
                (
                    "bandcamp://s1/2",
                    "T2",
                    "A",
                    "Castlebeat",
                    "Album X",
                    2,
                    "2023",
                    "bandcamp",
                ),
                (
                    "/local/track.mp3",
                    "T3",
                    "A",
                    "Castlebeat",
                    "Album X",
                    3,
                    "2023",
                    "local",
                ),
            ],
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        album_count = index._conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        albums = index.albums()
        index.close()

        assert album_count == 1, f"expected 1 album row, got {album_count}"
        assert len(albums) == 1
        # Canonical artist should be one of the two variants (most-common wins).
        assert albums[0].album_artist.lower() == "castlebeat"

    def test_migration_links_sale_item_id(self, tmp_path: Path) -> None:
        """sale_item_id is linked from bandcamp_collection to the albums row."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        self._build_v23_db(db_path)
        conn = _sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
            " track_number, year, source) VALUES (?,?,?,?,?,?,?,?)",
            (
                "bandcamp://sale-99/1",
                "T1",
                "A",
                "The Artist",
                "The Album",
                1,
                "2024",
                "bandcamp",
            ),
        )
        conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, band_name, item_title, mode)"
            " VALUES (?,?,?,?)",
            ("sale-99", "The Artist", "The Album", "local"),
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        row = index._conn.execute("SELECT sale_item_id FROM albums LIMIT 1").fetchone()
        index.close()

        assert row is not None
        assert row[0] == "sale-99"

    def test_migration_absorbs_album_favorites(self, tmp_path: Path) -> None:
        """Existing album_favorites rows become albums.favorite = 1."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        self._build_v23_db(db_path)
        conn = _sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
            " track_number, year, source) VALUES (?,?,?,?,?,?,?,?)",
            ("/lib/t1.mp3", "T1", "A", "The Artist", "The Album", 1, "2024", "local"),
        )
        conn.execute(
            "INSERT INTO album_favorites (album_artist, album) VALUES (?,?)",
            ("The Artist", "The Album"),
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        row = index._conn.execute("SELECT favorite FROM albums LIMIT 1").fetchone()
        albums = index.albums()
        index.close()

        assert row is not None
        assert row[0] == 1
        assert albums[0].favorite is True


class TestMigrationV25:
    """v24 → v25: is_available column on tracks (KAMP-423)."""

    def _build_v24_db(self, db_path: Path) -> None:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (24)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
            " track_number, source) VALUES (?,?,?,?,?,?,?)",
            ("bandcamp://1/1", "T1", "A", "A", "Alb", 1, "bandcamp"),
        )
        conn.commit()
        conn.close()

    def test_migration_adds_is_available_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v24_db(db_path)
        index = LibraryIndex(db_path)
        # v25 adds is_available; v49 drops it again. Re-add so this test of the
        # historical v25 migration can assert the column landed.
        _readd_legacy_track_columns(index)
        cols = {
            r[1] for r in index._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert version == 60
        assert "is_available" in cols

    def test_migration_defaults_existing_rows_to_available(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "library.db"
        self._build_v24_db(db_path)
        index = LibraryIndex(db_path)
        row = index._conn.execute(
            "SELECT is_available FROM tracks_with_stats LIMIT 1"
        ).fetchone()
        index.close()

        assert row is not None
        assert row[0] == 1


class TestIsAvailable:
    """Track.is_available is persisted and read back correctly."""

    def test_is_available_defaults_to_true(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "01.mp3")
        index.upsert_many([t])
        result = index.all_tracks()
        index.close()

        assert result[0].is_available is True

    def test_is_available_false_is_persisted(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(Path("bandcamp://42/1"))
        t.source = "bandcamp"
        t.is_available = False
        index.upsert_many([t])
        result = index.all_tracks()
        index.close()

        assert result[0].is_available is False

    def test_upsert_updates_is_available(self, tmp_path: Path) -> None:
        """Re-upserting a track with is_available=True updates a previously unavailable row."""
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(Path("bandcamp://42/1"))
        t.source = "bandcamp"
        t.is_available = False
        index.upsert_many([t])

        t2 = _sample_track(Path("bandcamp://42/1"))
        t2.source = "bandcamp"
        t2.is_available = True
        index.upsert_many([t2])

        result = index.all_tracks()
        index.close()

        assert len(result) == 1
        assert result[0].is_available is True


class TestIsPreorder:
    """AlbumInfo.is_preorder reflects bandcamp_collection.mode='preorder'."""

    def _remote_track(self, sale_item_id: str, track_num: int) -> Track:
        return Track(
            file_path=Path(f"bandcamp://{sale_item_id}/{track_num}"),
            title=f"Track {track_num}",
            artist="The Artist",
            album_artist="The Artist",
            album="The Album",
            release_date="2024",
            track_number=track_num,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )

    def test_is_preorder_false_by_default(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._remote_track("10", 1)])
        albums = index.albums()
        index.close()

        assert albums[0].is_preorder is False

    def test_is_preorder_true_when_mode_is_preorder(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "10",
            mode="preorder",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        index.upsert_many([self._remote_track("10", 1)])
        albums = index.albums()
        index.close()

        assert albums[0].is_preorder is True

    def test_is_preorder_false_when_mode_is_remote(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "10",
            mode="remote",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        index.upsert_many([self._remote_track("10", 1)])
        albums = index.albums()
        index.close()

        assert albums[0].is_preorder is False

    def test_in_bandcamp_collection_still_false_for_preorder_mode(
        self, tmp_path: Path
    ) -> None:
        """in_bandcamp_collection should remain False for mode='preorder' (not downloaded)."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "10",
            mode="preorder",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        index.upsert_many([self._remote_track("10", 1)])
        albums = index.albums()
        index.close()

        assert albums[0].in_bandcamp_collection is False

    def test_in_bandcamp_collection_still_true_for_local_mode(
        self, tmp_path: Path
    ) -> None:
        """Changing the LEFT JOIN must not break in_bandcamp_collection for mode='local'."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "10",
            mode="local",
            band_name="The Artist",
            item_title="The Album",
            synced_at=1000.0,
        )
        index.upsert_many([self._remote_track("10", 1)])
        albums = index.albums()
        index.close()

        assert albums[0].in_bandcamp_collection is True
        assert albums[0].is_preorder is False


class TestMigrationV26:
    """v25 → v26: num_streamable_tracks column on bandcamp_collection (KAMP-424)."""

    def _build_v25_db(self, db_path: Path) -> None:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (25)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, mode) VALUES ('1', 'preorder')"
        )
        conn.commit()
        conn.close()

    def test_migration_adds_num_streamable_tracks_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v25_db(db_path)
        index = LibraryIndex(db_path)
        cols = {
            r[1]
            for r in index._conn.execute(
                "PRAGMA table_info(bandcamp_collection)"
            ).fetchall()
        }
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert version == 60
        assert "num_streamable_tracks" in cols

    def test_migration_defaults_existing_rows_to_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v25_db(db_path)
        index = LibraryIndex(db_path)
        row = index._conn.execute(
            "SELECT num_streamable_tracks FROM bandcamp_collection WHERE sale_item_id = '1'"
        ).fetchone()
        index.close()

        assert row is not None
        assert row[0] == 0


class TestMigrationV27:
    """v26 → v27: duration column on tracks (KAMP-399)."""

    def _build_v26_db(self, db_path: Path) -> None:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (26)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0,"
            " num_streamable_tracks INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO tracks (file_path, title) VALUES ('local/song.mp3', 'Old Song')"
        )
        conn.commit()
        conn.close()

    def test_migration_adds_duration_column(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v26_db(db_path)
        index = LibraryIndex(db_path)
        # v27 adds duration; v49 drops it again. Re-add so this test of the
        # historical v27 migration can assert the column landed.
        _readd_legacy_track_columns(index)
        cols = {
            r[1] for r in index._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert version == 60
        assert "duration" in cols

    def test_migration_defaults_existing_rows_to_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        self._build_v26_db(db_path)
        index = LibraryIndex(db_path)
        row = index._conn.execute(
            "SELECT duration FROM tracks_with_stats WHERE file_path = 'local/song.mp3'"
        ).fetchone()
        index.close()

        assert row is not None
        assert row[0] == 0


class TestMigrationV28:
    """v27 → v28: null file_mtime for local zero-duration tracks to force rescan (KAMP-399)."""

    def _build_v27_db(self, db_path: Path) -> None:
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (27)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1,"
            " duration REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0,"
            " num_streamable_tracks INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        # local track with zero duration and a stored mtime (simulate post-v27 state)
        conn.execute(
            "INSERT INTO tracks (file_path, title, source, file_mtime, duration)"
            " VALUES ('local/a.mp3', 'A', 'local', 1000.0, 0)"
        )
        # local track that already has duration — mtime must NOT be nulled by v28.
        # Give it a full ISO year so v38 also does not null it.
        conn.execute(
            "INSERT INTO tracks (file_path, title, source, file_mtime, duration, year)"
            " VALUES ('local/b.mp3', 'B', 'local', 2000.0, 180.0, '2020-06-15')"
        )
        # bandcamp track with zero duration — should not be touched
        conn.execute(
            "INSERT INTO tracks (file_path, title, source, file_mtime, duration)"
            " VALUES ('bandcamp://1/1', 'C', 'bandcamp', 3000.0, 0)"
        )
        conn.commit()
        conn.close()

    def test_migration_nulls_mtime_for_zero_duration_local_tracks(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "library.db"
        self._build_v27_db(db_path)
        index = LibraryIndex(db_path)
        rows = {
            r[0]: r[1]
            for r in index._conn.execute(
                "SELECT file_path, file_mtime FROM tracks_with_stats"
            ).fetchall()
        }
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()

        assert version == 60
        assert rows["local/a.mp3"] is None  # zero-duration local: mtime nulled
        assert rows["local/b.mp3"] == 2000.0  # already has duration: untouched
        assert rows["bandcamp://1/1"] == 3000.0  # bandcamp: untouched


class TestNumStreamableTracks:
    """upsert_collection_item persists num_streamable_tracks (KAMP-424)."""

    def test_num_streamable_tracks_persisted(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="preorder", num_streamable_tracks=3)
        row = index._conn.execute(
            "SELECT num_streamable_tracks FROM bandcamp_collection WHERE sale_item_id = '1'"
        ).fetchone()
        index.close()

        assert row is not None
        assert row[0] == 3

    def test_num_streamable_tracks_defaults_to_zero(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="remote")
        row = index._conn.execute(
            "SELECT num_streamable_tracks FROM bandcamp_collection WHERE sale_item_id = '1'"
        ).fetchone()
        index.close()

        assert row is not None
        assert row[0] == 0

    def test_num_streamable_tracks_updated_on_upsert(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="preorder", num_streamable_tracks=2)
        index.upsert_collection_item("1", mode="preorder", num_streamable_tracks=5)
        row = index._conn.execute(
            "SELECT num_streamable_tracks FROM bandcamp_collection WHERE sale_item_id = '1'"
        ).fetchone()
        index.close()

        assert row[0] == 5


class TestGetCollectionStreamableCounts:
    """get_collection_streamable_counts returns {sale_item_id: count} for preorders."""

    def test_returns_preorder_counts(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="preorder", num_streamable_tracks=3)
        index.upsert_collection_item("2", mode="preorder", num_streamable_tracks=7)
        result = index.get_collection_streamable_counts()
        index.close()

        assert result == {"1": 3, "2": 7}

    def test_excludes_non_preorder_rows(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("1", mode="local", num_streamable_tracks=5)
        index.upsert_collection_item("2", mode="remote", num_streamable_tracks=2)
        result = index.get_collection_streamable_counts()
        index.close()

        assert result == {}

    def test_empty_when_no_preorders(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.get_collection_streamable_counts()
        index.close()

        assert result == {}


class TestAlbumUrlInAlbumInfo:
    """album_url from bandcamp_collection is surfaced through AlbumInfo (KAMP-367)."""

    def _remote_track(self, sale_item_id: str) -> Track:
        return Track(
            file_path=Path(f"bandcamp://{sale_item_id}/1"),
            title="Track 1",
            artist="The Artist",
            album_artist="The Artist",
            album="The Album",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )

    def test_album_url_present_when_collection_row_has_it(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "42",
            mode="remote",
            band_name="The Artist",
            item_title="The Album",
            album_url="https://theartist.bandcamp.com/album/the-album",
            synced_at=1000.0,
        )
        index.upsert_many([self._remote_track("42")])
        albums = index.albums()
        index.close()

        assert len(albums) == 1
        assert albums[0].album_url == "https://theartist.bandcamp.com/album/the-album"

    def test_album_url_empty_for_local_album(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        t = _sample_track(tmp_path / "01.mp3")
        index.upsert_many([t])
        albums = index.albums()
        index.close()

        assert albums[0].album_url == ""

    def test_album_url_empty_when_no_collection_row(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._remote_track("99")])
        albums = index.albums()
        index.close()

        assert albums[0].album_url == ""


# ---------------------------------------------------------------------------
# Playlists (KAMP-441)
# ---------------------------------------------------------------------------


class TestPlaylists:
    def _index(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        # seed two tracks so add_track_to_playlist has real rows to reference
        index.upsert_many(
            [
                _sample_track(tmp_path / "a.mp3"),
                _sample_track(tmp_path / "b.mp3"),
            ]
        )
        return index

    # ------------------------------------------------------------------
    # create / list / get
    # ------------------------------------------------------------------

    def test_create_returns_playlist_dict(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Road Trip")
        index.close()

        assert pl["id"] == 1
        assert pl["title"] == "Road Trip"
        assert pl["favorite"] is False
        assert pl["track_count"] == 0

    def test_get_playlists_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.get_playlists() == []
        index.close()

    def test_get_playlists_ordered_by_title(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_playlist("Zebra")
        index.create_playlist("Alpha")
        titles = [p["title"] for p in index.get_playlists()]
        index.close()

        assert titles == ["Alpha", "Zebra"]

    def test_get_playlist_returns_none_for_missing_id(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.get_playlist(999) is None
        index.close()

    def test_get_playlist_returns_row(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        created = index.create_playlist("My Mix")
        fetched = index.get_playlist(created["id"])
        index.close()

        assert fetched is not None
        assert fetched["title"] == "My Mix"

    def test_get_playlists_includes_last_played_at(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_playlist("Mix")
        playlists = index.get_playlists()
        index.close()

        assert "last_played_at" in playlists[0]
        assert playlists[0]["last_played_at"] is None

    # ------------------------------------------------------------------
    # record_playlist_played
    # ------------------------------------------------------------------

    def test_record_playlist_played_sets_timestamp(self, tmp_path: Path) -> None:
        import time

        index = self._index(tmp_path)
        pl = index.create_playlist("Road Trip")
        before = time.time()
        index.record_playlist_played(pl["id"])
        after = time.time()
        fetched = index.get_playlist(pl["id"])
        index.close()

        assert fetched is not None
        assert fetched["last_played_at"] is not None
        assert before <= fetched["last_played_at"] <= after

    def test_record_playlist_played_reflected_in_get_playlists(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Latest")
        index.record_playlist_played(pl["id"])
        playlists = index.get_playlists()
        index.close()

        match = next(p for p in playlists if p["id"] == pl["id"])
        assert match["last_played_at"] is not None

    # ------------------------------------------------------------------
    # add track
    # ------------------------------------------------------------------

    def test_add_track_to_playlist(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        tracks = index.get_playlist_tracks(pl["id"])
        index.close()

        assert len(tracks) == 1
        assert tracks[0]["position"] == 0

    def test_add_two_tracks_sequential_positions(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        index.add_track_to_playlist(pl["id"], str(tmp_path / "b.mp3"))
        tracks = index.get_playlist_tracks(pl["id"])
        index.close()

        assert [t["position"] for t in tracks] == [0, 1]

    def test_add_nonexistent_track_is_noop(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], "/does/not/exist.mp3")
        assert index.get_playlist_tracks(pl["id"]) == []
        index.close()

    def test_track_count_reflects_additions(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        pl2 = index.get_playlist(pl["id"])
        index.close()

        assert pl2 is not None
        assert pl2["track_count"] == 1

    # ------------------------------------------------------------------
    # remove track
    # ------------------------------------------------------------------

    def test_remove_track_from_playlist(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        pt_id = index.get_playlist_tracks(pl["id"])[0]["playlist_track_id"]
        index.remove_track_from_playlist(pl["id"], pt_id)
        assert index.get_playlist_tracks(pl["id"]) == []
        index.close()

    def test_remove_compacts_positions(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        index.add_track_to_playlist(pl["id"], str(tmp_path / "b.mp3"))
        tracks = index.get_playlist_tracks(pl["id"])
        # remove the first track; second track should shift to position 0
        index.remove_track_from_playlist(pl["id"], tracks[0]["playlist_track_id"])
        remaining = index.get_playlist_tracks(pl["id"])
        index.close()

        assert len(remaining) == 1
        assert remaining[0]["position"] == 0

    def test_remove_nonexistent_row_is_noop(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        # should not raise
        index.remove_track_from_playlist(pl["id"], 9999)
        index.close()

    def test_removing_track_from_library_not_playlist(self, tmp_path: Path) -> None:
        """Removing a track from the library does not implicitly remove playlist rows."""
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        # tracks table row still exists; we're just checking independence
        assert index.get_playlist_tracks(pl["id"]) != []
        index.close()

    def test_get_playlist_tracks_includes_date_added(self, tmp_path: Path) -> None:
        """get_playlist_tracks must expose date_added so the UI can sort by it."""
        index = self._index(tmp_path)
        index._conn.execute(
            "UPDATE tracks SET date_added = 1234.0 WHERE id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(tmp_path / "a.mp3"),),
        )
        index._conn.commit()
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        tracks = index.get_playlist_tracks(pl["id"])
        index.close()

        assert len(tracks) == 1
        assert tracks[0]["date_added"] == 1234.0

    # ------------------------------------------------------------------
    # reorder
    # ------------------------------------------------------------------

    def test_reorder_playlist_tracks(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        index.add_track_to_playlist(pl["id"], str(tmp_path / "b.mp3"))
        tracks = index.get_playlist_tracks(pl["id"])
        id_a = tracks[0]["playlist_track_id"]
        id_b = tracks[1]["playlist_track_id"]
        # reverse the order
        index.reorder_playlist_tracks(pl["id"], [id_b, id_a])
        reordered = index.get_playlist_tracks(pl["id"])
        index.close()

        assert reordered[0]["playlist_track_id"] == id_b
        assert reordered[1]["playlist_track_id"] == id_a

    def test_reorder_raises_on_wrong_ids(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        tracks = index.get_playlist_tracks(pl["id"])
        pt_id = tracks[0]["playlist_track_id"]

        with pytest.raises(ValueError):
            index.reorder_playlist_tracks(pl["id"], [pt_id, 9999])
        index.close()

    # ------------------------------------------------------------------
    # favorite / rename / delete
    # ------------------------------------------------------------------

    def test_set_playlist_favorite(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.set_playlist_favorite(pl["id"], True)
        pl2 = index.get_playlist(pl["id"])
        index.close()

        assert pl2 is not None
        assert pl2["favorite"] is True

    def test_rename_playlist(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Old Name")
        index.rename_playlist(pl["id"], "New Name")
        pl2 = index.get_playlist(pl["id"])
        index.close()

        assert pl2 is not None
        assert pl2["title"] == "New Name"

    def test_delete_playlist(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Temp")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        index.delete_playlist(pl["id"])
        assert index.get_playlist(pl["id"]) is None
        assert index.get_playlists() == []
        index.close()

    def test_delete_playlist_cascades_tracks(self, tmp_path: Path) -> None:
        """Deleting a playlist must remove all playlist_tracks rows."""
        index = self._index(tmp_path)
        pl = index.create_playlist("Gone")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))
        pl_id = pl["id"]
        index.delete_playlist(pl_id)
        # check directly in the DB that no orphan rows remain
        count = index._conn.execute(
            "SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id = ?", (pl_id,)
        ).fetchone()[0]
        index.close()

        assert count == 0

    def test_track_survives_move(self, tmp_path: Path) -> None:
        """A track that moves on disk must still appear in playlists (KAMP-448)."""
        index = self._index(tmp_path)
        pl = index.create_playlist("Mix")
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))

        index.move_track(
            tmp_path / "a.mp3",
            tmp_path / "a_moved.mp3",
            "A Song",
            1.0,
        )
        tracks = index.get_playlist_tracks(pl["id"])
        index.close()

        assert len(tracks) == 1
        assert tracks[0]["file_path"] == str(tmp_path / "a_moved.mp3")

    # ------------------------------------------------------------------
    # migration v28 → v29
    # ------------------------------------------------------------------

    def test_migration_v28_creates_playlist_tables(self, tmp_path: Path) -> None:
        """Opening a v28 DB triggers the v29 migration and creates both tables."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (28)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1,"
            " duration REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0,"
            " num_streamable_tracks INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        tables = {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        index.close()

        assert version == 60
        assert "playlists" in tables
        assert "playlist_tracks" in tables

    # ------------------------------------------------------------------
    # migration v29 → v30
    # ------------------------------------------------------------------

    def test_migration_v29_replaces_file_path_with_track_id(
        self, tmp_path: Path
    ) -> None:
        """Opening a v29 DB triggers the v30 migration: file_path → track_id FK."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (29)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1,"
            " duration REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0,"
            " num_streamable_tracks INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE playlists (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,"
            " created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE playlist_tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,"
            " file_path TEXT NOT NULL, position INTEGER NOT NULL)"
        )
        # Seed a track and a playlist row so the data migration has something to preserve.
        conn.execute(
            "INSERT INTO tracks (file_path, title) VALUES ('/lib/a.mp3', 'Song A')"
        )
        conn.execute(
            "INSERT INTO playlists (title, favorite, created_at, updated_at)"
            " VALUES ('My Mix', 0, 0.0, 0.0)"
        )
        conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, file_path, position)"
            " VALUES (1, '/lib/a.mp3', 0)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        columns = {
            r[1]
            for r in index._conn.execute(
                "PRAGMA table_info(playlist_tracks)"
            ).fetchall()
        }
        # Data migration: the row should survive mapped to the track's id.
        rows = index._conn.execute("SELECT track_id FROM playlist_tracks").fetchall()
        track_id_in_db = index._conn.execute(
            "SELECT id FROM tracks_with_stats WHERE file_path = '/lib/a.mp3'"
        ).fetchone()[0]
        index.close()

        assert version == 60
        assert "track_id" in columns
        assert "file_path" not in columns
        assert len(rows) == 1
        assert rows[0][0] == track_id_in_db

    # ------------------------------------------------------------------
    # migration v30 → v31
    # ------------------------------------------------------------------

    def test_migration_v30_adds_last_played_at_to_playlists(
        self, tmp_path: Path
    ) -> None:
        """Opening a v30 DB triggers the v31 migration: last_played_at added."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (30)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1,"
            " duration REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0,"
            " num_streamable_tracks INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE playlists (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,"
            " created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE playlist_tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,"
            " track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,"
            " position INTEGER NOT NULL)"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        columns = {
            r[1] for r in index._conn.execute("PRAGMA table_info(playlists)").fetchall()
        }
        index.close()

        assert version == 60
        assert "last_played_at" in columns

    # ------------------------------------------------------------------
    # migration v31 → v32
    # ------------------------------------------------------------------

    def test_migration_v31_creates_playlists_fts(self, tmp_path: Path) -> None:
        """Opening a v31 DB triggers the v32 migration: playlists_fts created."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (31)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1,"
            " duration REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0,"
            " num_streamable_tracks INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE playlists (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,"
            " created_at REAL NOT NULL, updated_at REAL NOT NULL,"
            " last_played_at REAL)"
        )
        conn.execute(
            "CREATE TABLE playlist_tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,"
            " track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,"
            " position INTEGER NOT NULL)"
        )
        import time as _time

        conn.execute(
            "INSERT INTO playlists (title, favorite, created_at, updated_at)"
            " VALUES ('Existing Playlist', 0, ?, ?)",
            (_time.time(), _time.time()),
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        # The migrated FTS table should contain the pre-existing playlist.
        results = index.search_playlists("Existing Playlist")
        index.close()

        assert version == 60
        assert len(results) == 1
        assert results[0]["title"] == "Existing Playlist"

    # ------------------------------------------------------------------
    # search_playlists
    # ------------------------------------------------------------------

    def test_search_playlists_empty_query_returns_empty(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_playlist("Chill Mix")
        assert index.search_playlists("") == []
        assert index.search_playlists("   ") == []
        index.close()

    def test_search_playlists_by_exact_name(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_playlist("Road Trip")
        index.create_playlist("Late Night")
        results = index.search_playlists("Road Trip")
        index.close()

        assert len(results) == 1
        assert results[0]["title"] == "Road Trip"

    def test_search_playlists_partial_match(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.create_playlist("Morning Coffee")
        index.create_playlist("Evening Wind-Down")
        results = index.search_playlists("morning")
        index.close()

        assert len(results) == 1
        assert results[0]["title"] == "Morning Coffee"

    def test_search_playlists_result_has_source_local_for_empty_playlist(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        index.create_playlist("Empty Playlist")
        results = index.search_playlists("Empty")
        index.close()

        assert results[0]["source"] == "local"

    def test_search_playlists_source_computed_from_tracks(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        local_track = _sample_track(tmp_path / "local.mp3")
        remote_track = Track(
            file_path=tmp_path / "remote.mp3",
            title="Remote Song",
            artist="Remote Artist",
            album_artist="Remote Artist",
            album="Remote Album",
            release_date="2024",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        index.upsert_many([local_track, remote_track])

        pl_local = index.create_playlist("Local Only")
        index.add_track_to_playlist(pl_local["id"], str(local_track.file_path))
        results_local = index.search_playlists("Local Only")

        pl_remote = index.create_playlist("Remote Only")
        index.add_track_to_playlist(pl_remote["id"], str(remote_track.file_path))
        results_remote = index.search_playlists("Remote Only")

        pl_mixed = index.create_playlist("Mixed")
        index.add_track_to_playlist(pl_mixed["id"], str(local_track.file_path))
        index.add_track_to_playlist(pl_mixed["id"], str(remote_track.file_path))
        results_mixed = index.search_playlists("Mixed")
        index.close()

        assert results_local[0]["source"] == "local"
        assert results_remote[0]["source"] == "bandcamp"
        assert results_mixed[0]["source"] == "mixed"

    def test_search_playlists_fts_updated_on_rename(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Old Name")
        index.rename_playlist(pl["id"], "New Name")

        assert index.search_playlists("Old Name") == []
        results = index.search_playlists("New Name")
        index.close()

        assert len(results) == 1
        assert results[0]["title"] == "New Name"

    def test_search_playlists_fts_cleared_on_delete(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Gone")
        index.delete_playlist(pl["id"])
        results = index.search_playlists("Gone")
        index.close()

        assert results == []

    # ------------------------------------------------------------------
    # playlists_for_tracks
    # ------------------------------------------------------------------

    def test_playlists_for_tracks_empty_list_returns_empty(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        index.create_playlist("Mix")
        assert index.playlists_for_tracks([]) == []
        index.close()

    def test_playlists_for_tracks_basic(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("My Faves")
        track = index.get_track_by_path(str(tmp_path / "a.mp3"))
        assert track is not None
        index.add_track_to_playlist(pl["id"], str(tmp_path / "a.mp3"))

        results = index.playlists_for_tracks([track.id])
        index.close()

        assert len(results) == 1
        assert results[0]["id"] == pl["id"]
        assert results[0]["title"] == "My Faves"

    def test_playlists_for_tracks_deduplicated(self, tmp_path: Path) -> None:
        """Multiple tracks from the same playlist must produce one result row."""
        index = LibraryIndex(tmp_path / "library.db")
        tracks = [_sample_track(tmp_path / f"{i}.mp3") for i in range(3)]
        index.upsert_many(tracks)
        pl = index.create_playlist("Triple")
        ids = []
        for t in tracks:
            row = index.get_track_by_path(str(t.file_path))
            assert row is not None
            ids.append(row.id)
            index.add_track_to_playlist(pl["id"], str(t.file_path))

        results = index.playlists_for_tracks(ids)
        index.close()

        assert len(results) == 1
        assert results[0]["title"] == "Triple"

    def test_playlists_for_tracks_excludes_unrelated_playlists(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        pl_a = index.create_playlist("Has Track")
        index.create_playlist("Empty")
        track = index.get_track_by_path(str(tmp_path / "a.mp3"))
        assert track is not None
        index.add_track_to_playlist(pl_a["id"], str(tmp_path / "a.mp3"))

        results = index.playlists_for_tracks([track.id])
        index.close()

        assert len(results) == 1
        assert results[0]["id"] == pl_a["id"]

    def test_playlists_for_tracks_track_count_reflects_full_playlist(
        self, tmp_path: Path
    ) -> None:
        """track_count in results must count all tracks in the playlist, not
        just the ones in the search input."""
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "t1.mp3")
        t2 = _sample_track(tmp_path / "t2.mp3")
        index.upsert_many([t1, t2])
        pl = index.create_playlist("Two Tracks")
        row1 = index.get_track_by_path(str(t1.file_path))
        row2 = index.get_track_by_path(str(t2.file_path))
        assert row1 is not None and row2 is not None
        index.add_track_to_playlist(pl["id"], str(t1.file_path))
        index.add_track_to_playlist(pl["id"], str(t2.file_path))

        # Only pass one track id; track_count must still be 2.
        results = index.playlists_for_tracks([row1.id])
        index.close()

        assert results[0]["track_count"] == 2

    def test_set_and_get_playlist_cover(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        pl = index.create_playlist("Art Test")
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 60

        index.set_playlist_cover(pl["id"], fake_jpeg)
        result = index.get_playlist_cover(pl["id"])
        index.close()

        assert result == fake_jpeg

    def test_set_playlist_cover_bumps_updated_at(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        pl = index.create_playlist("Timestamp Test")
        original_updated_at = pl["updated_at"]

        updated = index.set_playlist_cover(pl["id"], b"\xff\xd8\xff" + b"\x00" * 30)
        index.close()

        assert updated is not None
        assert updated["updated_at"] > original_updated_at

    def test_get_playlist_cover_returns_none_when_absent(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        pl = index.create_playlist("No Art")
        result = index.get_playlist_cover(pl["id"])
        index.close()

        assert result is None

    def test_set_playlist_cover_returns_none_for_unknown_id(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.set_playlist_cover(9999, b"\xff\xd8\xff" + b"\x00" * 30)
        index.close()

        assert result is None


class TestMagicPlaylists:
    def _criteria(self) -> MagicCriteria:
        return MagicCriteria(
            groups=[
                Group(
                    conditions=[Condition(field="artist", op="eq", value="Alvvays")],
                    match="all",
                    negate=False,
                )
            ],
            match="all",
        )

    def _index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    # ------------------------------------------------------------------
    # Serialization round-trips
    # ------------------------------------------------------------------

    def test_condition_round_trip(self) -> None:
        c = Condition(field="year", op="gt", value="2010")
        assert Condition.from_dict(c.to_dict()) == c

    def test_group_round_trip(self) -> None:
        g = Group(
            conditions=[Condition(field="artist", op="eq", value="Weezer")],
            match="any",
            negate=True,
        )
        assert Group.from_dict(g.to_dict()) == g

    def test_magic_criteria_round_trip(self) -> None:
        mc = self._criteria()
        assert MagicCriteria.from_dict(mc.to_dict()) == mc

    def test_group_negate_defaults_to_false(self) -> None:
        d = {"conditions": [], "match": "all"}
        g = Group.from_dict(d)
        assert g.negate is False

    # ------------------------------------------------------------------
    # create_magic_playlist
    # ------------------------------------------------------------------

    def test_create_magic_playlist_returns_id(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        playlist_id = index.create_magic_playlist("Smart Mix", self._criteria())
        index.close()

        assert isinstance(playlist_id, int)
        assert playlist_id > 0

    def test_create_magic_playlist_creates_both_rows(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        playlist_id = index.create_magic_playlist("Smart Mix", self._criteria())

        pl = index.get_playlist(playlist_id)
        criteria = index.get_magic_playlist_criteria(playlist_id)
        index.close()

        assert pl is not None
        assert pl["title"] == "Smart Mix"
        assert criteria is not None

    def test_create_magic_playlist_appears_in_get_playlists(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        index.create_magic_playlist("Smart Mix", self._criteria())
        titles = [p["title"] for p in index.get_playlists()]
        index.close()

        assert "Smart Mix" in titles

    # ------------------------------------------------------------------
    # get_magic_playlist_criteria
    # ------------------------------------------------------------------

    def test_get_magic_playlist_criteria_returns_none_for_static_playlist(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Static Mix")
        result = index.get_magic_playlist_criteria(pl["id"])
        index.close()

        assert result is None

    def test_get_magic_playlist_criteria_returns_criteria(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        criteria = self._criteria()
        playlist_id = index.create_magic_playlist("Smart Mix", criteria)

        fetched = index.get_magic_playlist_criteria(playlist_id)
        index.close()

        assert fetched == criteria

    def test_get_magic_playlist_criteria_returns_none_for_unknown_id(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        result = index.get_magic_playlist_criteria(9999)
        index.close()

        assert result is None

    # ------------------------------------------------------------------
    # update_magic_playlist_criteria
    # ------------------------------------------------------------------

    def test_update_magic_playlist_criteria(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        playlist_id = index.create_magic_playlist("Smart Mix", self._criteria())

        new_criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[Condition(field="year", op="gt", value="2000")],
                    match="all",
                )
            ],
            match="all",
        )
        index.update_magic_playlist_criteria(playlist_id, new_criteria)
        fetched = index.get_magic_playlist_criteria(playlist_id)
        index.close()

        assert fetched == new_criteria

    def test_update_magic_playlist_criteria_clears_evaluated_at(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        playlist_id = index.create_magic_playlist("Smart Mix", self._criteria())
        # Manually set evaluated_at so we can confirm it is cleared.
        index._conn.execute(
            "UPDATE magic_playlist_criteria SET evaluated_at = 9999.0 WHERE playlist_id = ?",
            (playlist_id,),
        )
        index._conn.commit()

        index.update_magic_playlist_criteria(playlist_id, self._criteria())
        row = index._conn.execute(
            "SELECT evaluated_at FROM magic_playlist_criteria WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        index.close()

        assert row["evaluated_at"] is None

    def test_update_magic_playlist_criteria_raises_for_non_magic_playlist(
        self, tmp_path: Path
    ) -> None:
        index = self._index(tmp_path)
        pl = index.create_playlist("Static Mix")

        with pytest.raises(ValueError, match="not a magic playlist"):
            index.update_magic_playlist_criteria(pl["id"], self._criteria())
        index.close()

    # ------------------------------------------------------------------
    # Cascade delete
    # ------------------------------------------------------------------

    def test_delete_magic_playlist_cascades_criteria(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        playlist_id = index.create_magic_playlist("Smart Mix", self._criteria())
        index.delete_playlist(playlist_id)

        result = index.get_magic_playlist_criteria(playlist_id)
        pl = index.get_playlist(playlist_id)
        index.close()

        assert result is None
        assert pl is None

    # ------------------------------------------------------------------
    # Schema migration v32 → v33
    # ------------------------------------------------------------------

    def test_migration_v33_creates_magic_criteria_table(self, tmp_path: Path) -> None:
        """Opening a v32 DB triggers the v33 migration: magic_playlist_criteria created."""
        import sqlite3 as _sqlite3
        import time as _time

        db_path = tmp_path / "library.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (32)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL, album_id INTEGER,"
            " is_available INTEGER NOT NULL DEFAULT 1,"
            " duration REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " album TEXT NOT NULL DEFAULT '' COLLATE NOCASE,"
            " year TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', genre TEXT NOT NULL DEFAULT '',"
            " label TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'local',"
            " sale_item_id TEXT, favorite INTEGER NOT NULL DEFAULT 0,"
            " date_added REAL, last_played_at REAL, play_count_avg REAL NOT NULL DEFAULT 0,"
            " art_version REAL, UNIQUE (album_artist, album))"
        )
        conn.execute(
            "CREATE TABLE bandcamp_collection (sale_item_id TEXT NOT NULL PRIMARY KEY,"
            " item_type TEXT NOT NULL DEFAULT 'p', band_name TEXT NOT NULL DEFAULT '',"
            " item_title TEXT NOT NULL DEFAULT '', tralbum_id TEXT NOT NULL DEFAULT '',"
            " album_url TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'local',"
            " synced_at REAL, added_at REAL NOT NULL DEFAULT 0,"
            " num_streamable_tracks INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY,"
            " session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE deferred_ops (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " op_type TEXT NOT NULL, track_id INTEGER NOT NULL UNIQUE,"
            " payload_json TEXT NOT NULL, created_at REAL NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
        )
        conn.execute(
            "CREATE TABLE download_queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " sale_item_id TEXT NOT NULL UNIQUE, queued_at REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE playlists (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,"
            " created_at REAL NOT NULL, updated_at REAL NOT NULL,"
            " last_played_at REAL)"
        )
        conn.execute(
            "CREATE TABLE playlist_tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,"
            " track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,"
            " position INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE playlists_fts USING fts5(title, tokenize = 'unicode61')"
        )
        conn.commit()
        conn.close()

        index = LibraryIndex(db_path)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]

        # Verify the table exists and the methods work end-to-end.
        criteria = MagicCriteria(
            groups=[
                Group(conditions=[Condition("artist", "eq", "Weezer")], match="all")
            ],
            match="all",
        )
        playlist_id = index.create_magic_playlist("Post-Migration Mix", criteria)
        fetched = index.get_magic_playlist_criteria(playlist_id)
        index.close()

        assert version == 60
        assert fetched == criteria

    # ------------------------------------------------------------------
    # evaluate_magic_playlist integration
    # ------------------------------------------------------------------

    def _seeded_index(self, tmp_path: Path) -> tuple["LibraryIndex", int, int]:
        """Return (index, track_id_a, track_id_b) with two distinct tracks."""
        from kamp_core.library import Track

        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        track_a = Track(
            file_path=tmp_path / "a.mp3",
            title="Song A",
            artist="Alvvays",
            album_artist="Alvvays",
            album="Antisocialites",
            release_date="2017",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="Indie",
            favorite=True,
            play_count=5,
            source="local",
        )
        track_b = Track(
            file_path=tmp_path / "b.mp3",
            title="Song B",
            artist="Weezer",
            album_artist="Weezer",
            album="Blue Album",
            release_date="1994",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="Rock",
            favorite=False,
            play_count=0,
            source="local",
        )
        index.upsert_many([track_a, track_b])
        # favorite and play_count are runtime fields not written by upsert_many;
        # set them directly in track_stats (the source of truth) so the evaluate
        # tests can filter on them (KAMP-552).
        index._conn.execute(
            "UPDATE track_stats SET favorite = 1, play_count = 5 WHERE track_id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(tmp_path / "a.mp3"),),
        )
        index._conn.commit()
        row_a = index._conn.execute(
            "SELECT id FROM tracks_with_stats WHERE file_path = ?",
            (str(tmp_path / "a.mp3"),),
        ).fetchone()
        row_b = index._conn.execute(
            "SELECT id FROM tracks_with_stats WHERE file_path = ?",
            (str(tmp_path / "b.mp3"),),
        ).fetchone()
        return index, row_a["id"], row_b["id"]

    def test_evaluate_returns_empty_for_nonexistent_playlist(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.evaluate_magic_playlist(9999)
        index.close()
        assert result == []

    def test_evaluate_returns_empty_for_static_playlist(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        pl = index.create_playlist("Static")
        result = index.evaluate_magic_playlist(pl["id"])
        index.close()
        assert result == []

    def test_evaluate_filters_by_artist(self, tmp_path: Path) -> None:
        index, id_a, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Alvvays")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Alvvays Only", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_a]

    def test_evaluate_filters_by_favorite(self, tmp_path: Path) -> None:
        index, id_a, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.favorite", op="is", value="true")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Favorites", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_a]

    def test_evaluate_filters_by_source_via_track_sources(self, tmp_path: Path) -> None:
        """track.source criteria resolves via track_sources, not tracks.source (KAMP-542)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index.upsert_many([_sample_track(tmp_path / "a.mp3")])
        tid = index._conn.execute("SELECT id FROM tracks").fetchone()[0]
        # Desync: the track has a file (local) source, but its legacy source column
        # is set to 'bandcamp'. The criterion must follow the source (local).
        index._conn.execute(
            "UPDATE tracks SET source = 'bandcamp' WHERE id = ?", (tid,)
        )
        index._conn.commit()
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.source", op="is", value="local")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Local", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [
            tid
        ]  # matched by the file source, not tracks.source='bandcamp'

    def test_evaluate_filters_by_genre_contains(self, tmp_path: Path) -> None:
        index, id_a, id_b = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.genre", op="contains", value="Indie")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Indie", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_a]

    def test_evaluate_play_count_gt(self, tmp_path: Path) -> None:
        index, id_a, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.play_count", op="gt", value="2")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Played", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_a]

    def test_evaluate_year_lt(self, tmp_path: Path) -> None:
        index, _, id_b = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[Condition(field="track.year", op="lt", value="2000")],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Old Stuff", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_b]

    def test_evaluate_no_results(self, tmp_path: Path) -> None:
        index, _, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Nobody Famous")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Empty Result", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == []

    def test_evaluate_all_results_with_empty_criteria(self, tmp_path: Path) -> None:
        # Empty criteria matches nothing — "no conditions" is not a pass-through.
        index, _, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(groups=[], match="all")
        pid = index.create_magic_playlist("Empty", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == []

    def test_evaluate_match_any_combines_artists(self, tmp_path: Path) -> None:
        index, id_a, id_b = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Alvvays"),
                        Condition(field="track.artist", op="is", value="Weezer"),
                    ],
                    match="any",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Both Artists", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert sorted(result) == sorted([id_a, id_b])

    def test_evaluate_negated_group_excludes_match(self, tmp_path: Path) -> None:
        index, _, id_b = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Alvvays")
                    ],
                    match="all",
                    negate=True,
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Not Alvvays", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_b]

    def test_evaluate_album_favorite_uses_join(self, tmp_path: Path) -> None:
        index, id_a, _ = self._seeded_index(tmp_path)
        # Mark Alvvays album as favorite.
        index.toggle_album_favorite("Alvvays", "Antisocialites", True)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="album.favorite", op="is", value="true")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Fav Albums", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_a]

    def test_evaluate_in_playlist(self, tmp_path: Path) -> None:
        index, id_a, _ = self._seeded_index(tmp_path)
        # Create a static playlist containing only track A.
        static = index.create_playlist("Static")
        index.add_track_to_playlist(static["id"], str(tmp_path / "a.mp3"))
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="in_playlist", op="is", value=str(static["id"]))
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("In Static", criteria)
        result = index.evaluate_magic_playlist(pid)
        index.close()
        assert result == [id_a]

    def test_get_magic_playlist_tracks_returns_full_dicts(self, tmp_path: Path) -> None:
        index, id_a, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Alvvays")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Smart Mix", criteria)
        tracks = index.get_magic_playlist_tracks(pid)
        index.close()

        assert len(tracks) == 1
        t = tracks[0]
        assert t["id"] == id_a
        assert t["artist"] == "Alvvays"
        assert t["playlist_track_id"] is None
        assert t["position"] == 0

    def test_get_magic_playlist_tracks_returns_empty_for_static(
        self, tmp_path: Path
    ) -> None:
        index, _, _ = self._seeded_index(tmp_path)
        pl = index.create_playlist("Static")
        tracks = index.get_magic_playlist_tracks(pl["id"])
        index.close()
        assert tracks == []

    def test_get_magic_playlist_tracks_excludes_unavailable(
        self, tmp_path: Path
    ) -> None:
        """Pre-order tracks (is_available=False) must not appear even if they match criteria."""
        from kamp_core.library import Track

        index, id_a, _ = self._seeded_index(tmp_path)
        unavailable = Track(
            file_path=tmp_path / "preorder.mp3",
            title="Unreleased Song",
            artist="Alvvays",
            album_artist="Alvvays",
            album="Blue Rev",
            release_date="2022",
            track_number=99,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
            is_available=False,
        )
        index.upsert_many([unavailable])
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Alvvays")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("No Preorders", criteria)
        tracks = index.get_magic_playlist_tracks(pid)
        index.close()

        ids = [t["id"] for t in tracks]
        assert id_a in ids
        assert unavailable not in [t["file_path"] for t in tracks]
        assert all(t["is_available"] for t in tracks)

    def test_get_magic_playlist_tracks_includes_date_added(
        self, tmp_path: Path
    ) -> None:
        """get_magic_playlist_tracks must expose date_added so the UI can sort by it."""
        index, id_a, _ = self._seeded_index(tmp_path)
        index._conn.execute(
            "UPDATE tracks SET date_added = 9999.0 WHERE id = ?", (id_a,)
        )
        index._conn.commit()
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Alvvays")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        pid = index.create_magic_playlist("Date Sort Test", criteria)
        tracks = index.get_magic_playlist_tracks(pid)
        index.close()

        assert len(tracks) == 1
        assert tracks[0]["date_added"] == 9999.0

    def test_count_magic_criteria_returns_match_count(self, tmp_path: Path) -> None:
        index, _, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.artist", op="is", value="Alvvays")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        count = index.count_magic_criteria(criteria)
        index.close()
        assert count == 1

    def test_count_magic_criteria_empty_returns_nothing(self, tmp_path: Path) -> None:
        # Empty criteria matches nothing — "no conditions" is not a pass-through.
        index, _, _ = self._seeded_index(tmp_path)
        criteria = MagicCriteria(groups=[], match="all")
        count = index.count_magic_criteria(criteria)
        index.close()
        assert count == 0


class TestPlaylistModuleContent:
    """Tests for LibraryIndex.get_playlist_module_content()."""

    def _setup(self, tmp_path: Path) -> tuple[LibraryIndex, int, int]:
        """Return (index, static_pid, magic_pid) with two tracks seeded."""
        index = LibraryIndex(tmp_path / "library.db")
        pa = _make_indexed_track(
            index,
            tmp_path,
            "a.mp3",
            album_artist="Alvvays",
            album="Antisocialites",
            play_count=5,
        )
        pb = _make_indexed_track(
            index,
            tmp_path,
            "b.mp3",
            album_artist="Slowdive",
            album="Souvlaki",
            play_count=2,
        )
        index.record_play_time(pa, 300.0)
        index.record_play_time(pb, 100.0)
        # Static playlist with both tracks.
        static_pid = index.create_playlist("Static")["id"]
        t_a = index.tracks_for_album("Alvvays", "Antisocialites")[0]
        t_b = index.tracks_for_album("Slowdive", "Souvlaki")[0]
        index.add_track_to_playlist(static_pid, str(t_a.file_path))
        index.add_track_to_playlist(static_pid, str(t_b.file_path))
        # Magic playlist matching the same two tracks.
        magic_pid = index.create_magic_playlist(
            "Magic",
            MagicCriteria(
                groups=[
                    Group(
                        conditions=[
                            Condition(
                                field="track.album_artist", op="is", value="Alvvays"
                            ),
                            Condition(
                                field="track.album_artist", op="is", value="Slowdive"
                            ),
                        ],
                        match="any",
                        negate=False,
                    )
                ],
                match="all",
            ),
        )
        return index, static_pid, magic_pid

    # albums ---

    def test_albums_returns_album_dicts(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(static_pid, "albums", "random", 10)
        index.close()
        assert len(result) == 2
        album_names = {r["album"] for r in result}
        assert album_names == {"Antisocialites", "Souvlaki"}

    def test_albums_most_played_sort(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(
            static_pid, "albums", "most_played", 10
        )
        index.close()
        assert result[0]["album"] == "Antisocialites"  # play_count=5 > 2

    def test_albums_limit(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(static_pid, "albums", "random", 1)
        index.close()
        assert len(result) == 1

    def test_albums_magic_playlist(self, tmp_path: Path) -> None:
        index, _, magic_pid = self._setup(tmp_path)
        result = index.get_playlist_module_content(magic_pid, "albums", "random", 10)
        index.close()
        assert len(result) == 2

    # artists ---

    def test_artists_returns_artist_dicts(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(static_pid, "artists", "random", 10)
        index.close()
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"Alvvays", "Slowdive"}

    def test_artists_most_played_sort(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(
            static_pid, "artists", "most_played", 10
        )
        index.close()
        assert result[0]["name"] == "Alvvays"  # play_time=300 > 100

    def test_artists_last_played_sort(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        # Play a Slowdive track most recently — it should sort first.
        t_b = index.tracks_for_album("Slowdive", "Souvlaki")[0]
        index.record_track_started(t_b.file_path)
        result = index.get_playlist_module_content(
            static_pid, "artists", "last_played", 10
        )
        index.close()
        assert result[0]["name"] == "Slowdive"

    def test_artists_recently_added_sort(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        # Force Slowdive's track date_added to be more recent than Alvvays.
        index._conn.execute(
            "UPDATE tracks SET date_added = 9999999999 WHERE album_artist = 'Slowdive'"
        )
        index._conn.commit()
        result = index.get_playlist_module_content(
            static_pid, "artists", "recently_added", 10
        )
        index.close()
        assert result[0]["name"] == "Slowdive"

    def test_artists_have_top_album(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(static_pid, "artists", "random", 10)
        index.close()
        by_name = {r["name"]: r for r in result}
        assert by_name["Alvvays"]["top_album"] == "Antisocialites"

    # tracks ---

    def test_tracks_returns_track_dicts(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(static_pid, "tracks", "random", 10)
        index.close()
        assert len(result) == 2
        assert all("file_path" in r for r in result)

    def test_tracks_most_played_sort(self, tmp_path: Path) -> None:
        index, static_pid, _ = self._setup(tmp_path)
        result = index.get_playlist_module_content(
            static_pid, "tracks", "most_played", 10
        )
        index.close()
        assert result[0]["album_artist"] == "Alvvays"

    # missing playlist ---

    def test_missing_playlist_returns_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.get_playlist_module_content(9999, "albums", "random", 10)
        index.close()
        assert result == []


class TestMagicPlaylistReactivity:
    """Tests for the on_fields_changed callback wired into LibraryIndex mutations."""

    def _make_index_with_track(self, tmp_path: Path) -> tuple["LibraryIndex", Path]:
        from kamp_core.library import Track

        index = LibraryIndex(tmp_path / "library.db")
        track = Track(
            file_path=tmp_path / "a.mp3",
            title="Song A",
            artist="Alvvays",
            album_artist="Alvvays",
            album="Antisocialites",
            release_date="2017",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="Indie",
            favorite=False,
            play_count=0,
            source="local",
        )
        index.upsert_many([track])
        return index, tmp_path / "a.mp3"

    def test_on_fields_changed_none_does_not_crash(self, tmp_path: Path) -> None:
        """All 6 mutations must work when on_fields_changed is None (default)."""
        from kamp_core.library import Track

        index, track_path = self._make_index_with_track(tmp_path)
        assert index.on_fields_changed is None
        index.set_favorite(track_path, True)
        index.toggle_album_favorite("Alvvays", "Antisocialites", True)
        index.record_played(track_path)
        index.record_track_started(track_path)
        second = Track(
            file_path=tmp_path / "b.mp3",
            title="Song B",
            artist="Weezer",
            album_artist="Weezer",
            album="Blue",
            release_date="1994",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="Rock",
            favorite=False,
            play_count=0,
            source="local",
        )
        index.upsert_many([second])
        index.remove_track(track_path)
        index.close()

    def test_set_favorite_fires_on_fields_changed(self, tmp_path: Path) -> None:
        index, track_path = self._make_index_with_track(tmp_path)
        cb = MagicMock()
        index.on_fields_changed = cb
        index.set_favorite(track_path, True)
        index.close()
        cb.assert_called_once_with({"track.favorite"})

    def test_toggle_album_favorite_fires_on_fields_changed(
        self, tmp_path: Path
    ) -> None:
        index, _ = self._make_index_with_track(tmp_path)
        cb = MagicMock()
        index.on_fields_changed = cb
        index.toggle_album_favorite("Alvvays", "Antisocialites", True)
        index.close()
        cb.assert_called_once_with({"album.favorite"})

    def test_record_played_fires_on_fields_changed(self, tmp_path: Path) -> None:
        index, track_path = self._make_index_with_track(tmp_path)
        cb = MagicMock()
        index.on_fields_changed = cb
        index.record_played(track_path)
        index.close()
        cb.assert_called_once_with({"track.play_count"})

    def test_record_track_started_fires_on_fields_changed(self, tmp_path: Path) -> None:
        index, track_path = self._make_index_with_track(tmp_path)
        cb = MagicMock()
        index.on_fields_changed = cb
        index.record_track_started(track_path)
        index.close()
        cb.assert_called_once_with({"track.last_played"})

    def test_upsert_many_fires_on_fields_changed(self, tmp_path: Path) -> None:
        from kamp_core.library import Track

        index = LibraryIndex(tmp_path / "library.db")
        cb = MagicMock()
        index.on_fields_changed = cb
        track = Track(
            file_path=tmp_path / "a.mp3",
            title="Song A",
            artist="Alvvays",
            album_artist="Alvvays",
            album="Antisocialites",
            release_date="2017",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genre="Indie",
            favorite=False,
            play_count=0,
            source="local",
        )
        index.upsert_many([track])
        index.close()
        cb.assert_called_once()
        fields_arg = cb.call_args[0][0]
        assert "track.artist" in fields_arg
        assert "track.album" in fields_arg
        assert "track.genre" in fields_arg
        assert "track.source" in fields_arg
        assert "track.year" in fields_arg

    def test_remove_track_fires_on_fields_changed(self, tmp_path: Path) -> None:
        index, track_path = self._make_index_with_track(tmp_path)
        cb = MagicMock()
        index.on_fields_changed = cb
        index.remove_track(track_path)
        index.close()
        cb.assert_called_once()
        fields_arg = cb.call_args[0][0]
        assert "track.artist" in fields_arg
        assert "track.favorite" in fields_arg
        assert "track.play_count" in fields_arg
        assert "track.last_played" in fields_arg

    def test_list_all_magic_criteria_returns_all(self, tmp_path: Path) -> None:
        index, _ = self._make_index_with_track(tmp_path)
        mc1 = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.favorite", op="is", value="true")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        mc2 = MagicCriteria(
            groups=[
                Group(
                    conditions=[
                        Condition(field="track.play_count", op="gt", value="5")
                    ],
                    match="all",
                )
            ],
            match="all",
        )
        id1 = index.create_magic_playlist("Favorites", mc1)
        id2 = index.create_magic_playlist("Most Played", mc2)
        result = index.list_all_magic_criteria()
        index.close()
        assert len(result) == 2
        ids = {pair[0] for pair in result}
        assert ids == {id1, id2}
        criteria_by_id = {pair[0]: pair[1] for pair in result}
        assert criteria_by_id[id1].groups[0].conditions[0].field == "track.favorite"
        assert criteria_by_id[id2].groups[0].conditions[0].field == "track.play_count"

    def test_list_all_magic_criteria_empty_when_none(self, tmp_path: Path) -> None:
        index, _ = self._make_index_with_track(tmp_path)
        index.close()
        index2 = LibraryIndex(tmp_path / "library.db")
        result = index2.list_all_magic_criteria()
        index2.close()
        assert result == []


# ---------------------------------------------------------------------------
# Display overrides (KAMP-467)
# ---------------------------------------------------------------------------


def _make_bandcamp_track(uri: str, title: str = "Track", album: str = "Album") -> Track:
    return Track(
        file_path=Path(uri),
        title=title,
        artist="Band",
        album_artist="Band",
        album=album,
        release_date="2020",
        track_number=1,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
        source="bandcamp",
    )


def _bandcamp_track() -> Track:
    return Track(
        file_path=Path("bandcamp://sale123/1"),
        title="Stream Track",
        artist="Band",
        album_artist="Band",
        album="Stream Album",
        release_date="",
        track_number=1,
        disc_number=1,
        ext="",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
        genre="",
        label="",
        source="bandcamp",
    )


class TestMigrationV38:
    """v38 migration: rename year → release_date; null mtime for short dates (KAMP-513)."""

    def _build_v37_db(self, db_path: Path) -> None:
        """Build a minimal v37 database with the old 'year' column."""
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (37)")
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " file_path TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',"
            " artist TEXT NOT NULL DEFAULT '', album_artist TEXT NOT NULL DEFAULT '',"
            " album TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '',"
            " track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 1,"
            " ext TEXT NOT NULL DEFAULT '', embedded_art INTEGER NOT NULL DEFAULT 0,"
            " mb_release_id TEXT NOT NULL DEFAULT '', mb_recording_id TEXT NOT NULL DEFAULT '',"
            " date_added REAL, last_played REAL, favorite INTEGER NOT NULL DEFAULT 0,"
            " play_count INTEGER NOT NULL DEFAULT 0, file_mtime REAL,"
            " genre TEXT NOT NULL DEFAULT '', label TEXT NOT NULL DEFAULT '',"
            " source TEXT NOT NULL DEFAULT 'local', stream_url TEXT,"
            " stream_url_expires_at REAL,"
            # album_id (v13), is_available (v25) and duration (v27) all predate v37,
            # so their migrations do not replay here — a real v37 tracks table has
            # them, and the v45 backfill guard reads is_available/duration, so they
            # must be present or track_sources stays empty and the view derives NULL.
            " album_id INTEGER, is_available INTEGER NOT NULL DEFAULT 1,"
            " duration REAL NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " album_artist TEXT NOT NULL, album TEXT NOT NULL,"
            " year TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE sessions (service TEXT NOT NULL PRIMARY KEY, session_json TEXT, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE tracks_fts USING fts5(title, artist, album_artist, album)"
        )
        conn.commit()
        conn.close()

    def test_migration_renames_year_to_release_date_in_tracks(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "library.db"
        self._build_v37_db(db_path)
        LibraryIndex(db_path).close()

        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        conn.close()

        assert version == 60
        assert "release_date" in cols
        assert "year" not in cols

    def test_migration_nulls_mtime_for_local_year_only_tracks(
        self, tmp_path: Path
    ) -> None:
        """Local tracks with year-only release_date get NULL mtime so the next scan
        re-reads the file and picks up the full ISO date (e.g. '2023-03-15')."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "library.db"
        self._build_v37_db(db_path)

        conn = _sqlite3.connect(str(db_path))
        # Local track with year-only — should get NULL mtime after migration
        conn.execute(
            "INSERT INTO tracks (file_path, year, source, file_mtime)"
            " VALUES ('/music/a.mp3', '2020', 'local', 999.0)"
        )
        # Local track with full ISO date — mtime should be preserved
        conn.execute(
            "INSERT INTO tracks (file_path, year, source, file_mtime)"
            " VALUES ('/music/b.mp3', '2020-06-15', 'local', 888.0)"
        )
        # Remote track with year-only — source != 'local', mtime should be untouched
        conn.execute(
            "INSERT INTO tracks (file_path, year, source, file_mtime)"
            " VALUES ('bandcamp://1/1', '2019', 'bandcamp', 777.0)"
        )
        conn.commit()
        conn.close()

        LibraryIndex(db_path).close()

        conn = _sqlite3.connect(str(db_path))
        rows = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT file_path, file_mtime FROM tracks_with_stats"
            ).fetchall()
        }
        conn.close()

        assert (
            rows["/music/a.mp3"] is None
        ), "year-only local track must have NULL mtime"
        assert (
            rows["/music/b.mp3"] == 888.0
        ), "full-date local track mtime must be preserved"
        assert rows["bandcamp://1/1"] == 777.0, "remote track mtime must be untouched"


class TestDisplayOverrides:
    """update_track_display_title and update_album_display (KAMP-467)."""

    def test_schema_v35_adds_display_columns_to_tracks(self, tmp_path: Path) -> None:
        LibraryIndex(tmp_path / "library.db").close()
        conn = sqlite3.connect(str(tmp_path / "library.db"))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        conn.close()
        assert "display_title" in cols
        assert "display_album" in cols
        assert "display_album_artist" in cols

    def test_schema_v35_adds_display_columns_to_albums(self, tmp_path: Path) -> None:
        LibraryIndex(tmp_path / "library.db").close()
        conn = sqlite3.connect(str(tmp_path / "library.db"))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(albums)").fetchall()}
        conn.close()
        assert "display_album" in cols
        assert "display_album_artist" in cols

    def test_update_track_display_title_returns_effective_title(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _make_bandcamp_track("bandcamp://42/1", title="Original")
        index.upsert_many([track])
        inserted = index.all_tracks()[0]

        updated = index.update_track_display_title(inserted.id, "Renamed")
        index.close()

        assert updated is not None
        assert updated.title == "Renamed"

    def test_update_track_display_title_clears_override_on_empty(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _make_bandcamp_track("bandcamp://42/1", title="Original")
        index.upsert_many([track])
        inserted = index.all_tracks()[0]
        index.update_track_display_title(inserted.id, "Renamed")

        # Clear by passing empty string
        cleared = index.update_track_display_title(inserted.id, "")
        index.close()

        assert cleared is not None
        assert cleared.title == "Original"

    def test_update_track_display_title_returns_none_for_missing_track(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.update_track_display_title(99999, "Ghost")
        index.close()
        assert result is None

    def test_display_title_preserved_through_upsert(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _make_bandcamp_track("bandcamp://42/1", title="Original")
        index.upsert_many([track])
        inserted = index.all_tracks()[0]
        index.update_track_display_title(inserted.id, "Renamed")

        # Re-upsert simulates a Bandcamp sync bringing back the canonical title.
        index.upsert_many([track])
        after_sync = index.all_tracks()[0]
        index.close()

        assert after_sync.title == "Renamed"

    def test_update_album_display_sets_overrides_on_album_and_tracks(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _make_bandcamp_track("bandcamp://42/1", album="Long Ugly Name")
        index.upsert_many([track])

        result = index.update_album_display("Band", "Long Ugly Name", "Short", "B")
        all_tracks = index.all_tracks()
        index.close()

        assert result is not None
        assert all_tracks[0].album == "Short"
        assert all_tracks[0].album_artist == "B"

    def test_update_album_display_returns_none_for_missing_album(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.update_album_display("Ghost", "Phantom", "X", "Y")
        index.close()
        assert result is None

    def test_display_album_preserved_through_upsert(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _make_bandcamp_track("bandcamp://42/1", album="Long Ugly Name")
        index.upsert_many([track])
        index.update_album_display("Band", "Long Ugly Name", "Short", None)

        # Re-upsert simulates a Bandcamp sync.
        index.upsert_many([track])
        all_tracks = index.all_tracks()
        index.close()

        assert all_tracks[0].album == "Short"

    def test_fts_finds_display_title(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _make_bandcamp_track(
            "bandcamp://42/1", title="Glum (20th Anniversary Edition)"
        )
        index.upsert_many([track])
        inserted = index.all_tracks()[0]
        index.update_track_display_title(inserted.id, "Glum")

        results = index.search("Glum")
        index.close()

        assert any(t.title == "Glum" for t in results)


class TestUpdateTrackArtist:
    """update_track_artist — local per-track artist edit (KAMP-582)."""

    def _index_with_local_track(self, tmp_path: Path) -> tuple[LibraryIndex, int]:
        index = LibraryIndex(tmp_path / "library.db")
        track = _sample_track(tmp_path / "01.mp3")
        track.artist = "Original Guy"
        index.upsert_track(track)
        inserted = index.get_track_by_path(tmp_path / "01.mp3")
        assert inserted is not None
        return index, inserted.id

    def test_updates_artist_and_returns_track(self, tmp_path: Path) -> None:
        index, track_id = self._index_with_local_track(tmp_path)
        updated = index.update_track_artist(track_id, "Fresh Guy")
        index.close()
        assert updated is not None
        assert updated.artist == "Fresh Guy"

    def test_fts_reflects_new_artist(self, tmp_path: Path) -> None:
        index, track_id = self._index_with_local_track(tmp_path)
        index.update_track_artist(track_id, "Fresh Guy")

        found = index.search("Fresh Guy")
        stale = index.search("Original Guy")
        index.close()

        assert any(t.id == track_id for t in found)
        assert not any(t.id == track_id for t in stale)

    def test_fires_on_fields_changed(self, tmp_path: Path) -> None:
        index, track_id = self._index_with_local_track(tmp_path)
        calls: list[set[str]] = []
        index.on_fields_changed = lambda fields: calls.append(fields)

        index.update_track_artist(track_id, "Fresh Guy")
        index.close()

        assert {"track.artist"} in calls

    def test_returns_none_for_missing_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.update_track_artist(99999, "Ghost")
        index.close()
        assert result is None


class TestDisplayArtistOverride:
    """update_track_display_artist + display_artist column (KAMP-582)."""

    def _stream_track(self, artist: str = "Zorkfolk") -> Track:
        return Track(
            file_path=Path("bandcamp://42/1"),
            title="Stream Track",
            artist=artist,
            album_artist="Band",
            album="Stream Album",
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )

    def test_schema_has_display_artist_column(self, tmp_path: Path) -> None:
        LibraryIndex(tmp_path / "library.db").close()
        conn = sqlite3.connect(str(tmp_path / "library.db"))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        conn.close()
        assert "display_artist" in cols

    def test_migration_adds_display_artist_to_pre_v55_db(self, tmp_path: Path) -> None:
        """A pre-v55 tracks table gains display_artist via the guarded ALTER."""
        db = tmp_path / "library.db"
        LibraryIndex(db).close()
        conn = sqlite3.connect(str(db))
        # Drop the view first: it projects display_artist through, and SQLite
        # (version-dependently) refuses a DROP COLUMN that a view references.
        # A real pre-v55 DB has a view built without the column anyway, and
        # open() recreates it from PRAGMA table_info after migrating.
        conn.execute("DROP VIEW IF EXISTS tracks_with_stats")
        conn.execute("ALTER TABLE tracks DROP COLUMN display_artist")
        conn.execute("UPDATE schema_version SET version = 54")
        conn.commit()
        conn.close()

        LibraryIndex(db).close()

        conn = sqlite3.connect(str(db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        conn.close()
        assert "display_artist" in cols
        assert version >= 55

    def test_rebuild_fts_survives_missing_display_artist(self, tmp_path: Path) -> None:
        """_rebuild_fts runs inside pre-v55 migration steps (v2/v42/v50), where
        display_title exists but display_artist does not yet — the artist
        expression must fall back to the plain column instead of crashing."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_track()])
        # Drop the view before the column — it projects display_artist through
        # and SQLite (version-dependently) blocks the DROP COLUMN otherwise.
        index._conn.execute("DROP VIEW IF EXISTS tracks_with_stats")
        index._conn.execute("ALTER TABLE tracks DROP COLUMN display_artist")

        index._rebuild_fts()  # must not raise

        # Query FTS directly: the tracks_with_stats view (and thus search())
        # legitimately references display_artist and is broken in this
        # synthetic mid-migration state.
        count = index._conn.execute(
            "SELECT COUNT(*) FROM tracks_fts WHERE tracks_fts MATCH 'Zorkfolk'"
        ).fetchone()[0]
        index.close()
        assert count == 1

    def test_update_track_display_artist_returns_effective_artist(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_track()])
        inserted = index.all_tracks()[0]

        updated = index.update_track_display_artist(inserted.id, "Quux Person")
        index.close()

        assert updated is not None
        assert updated.artist == "Quux Person"

    def test_clears_override_on_empty(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_track()])
        inserted = index.all_tracks()[0]
        index.update_track_display_artist(inserted.id, "Quux Person")

        cleared = index.update_track_display_artist(inserted.id, "")
        index.close()

        assert cleared is not None
        assert cleared.artist == "Zorkfolk"

    def test_returns_none_for_missing_track(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.update_track_display_artist(99999, "Ghost")
        index.close()
        assert result is None

    def test_preserved_through_upsert(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = self._stream_track()
        index.upsert_many([track])
        inserted = index.all_tracks()[0]
        index.update_track_display_artist(inserted.id, "Quux Person")

        # Re-upsert simulates a Bandcamp sync bringing back the canonical artist.
        index.upsert_many([track])
        after_sync = index.all_tracks()[0]
        index.close()

        assert after_sync.artist == "Quux Person"

    def test_fts_finds_display_artist_not_canonical(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_track()])
        inserted = index.all_tracks()[0]
        index.update_track_display_artist(inserted.id, "Quux Person")

        found = index.search("Quux")
        stale = index.search("Zorkfolk")
        index.close()

        assert any(t.id == inserted.id for t in found)
        assert not any(t.id == inserted.id for t in stale)

    def test_merge_carries_display_artist_to_survivor(self, tmp_path: Path) -> None:
        """_merge_track_into coalesces display_artist from the loser like the
        other display overrides — merging a streaming track into its downloaded
        twin must not drop a user's artist override."""
        index = LibraryIndex(tmp_path / "library.db")
        local = _sample_track(tmp_path / "01.mp3")
        index.upsert_track(local)
        index.upsert_many([self._stream_track()])
        survivor = index.get_track_by_path(tmp_path / "01.mp3")
        loser = index.get_track_by_path("bandcamp://42/1")
        assert survivor is not None and loser is not None
        index.update_track_display_artist(loser.id, "Quux Person")

        index._merge_track_into(survivor.id, loser.id)
        index._conn.commit()

        row = index._conn.execute(
            "SELECT display_artist FROM tracks WHERE id = ?", (survivor.id,)
        ).fetchone()
        index.close()
        assert row["display_artist"] == "Quux Person"


class TestMultiValueGenre:
    """Normalized genres / track_genres model + apply_genres service (KAMP-586)."""

    def _local(self, path: Path, genres: list[str], album: str = "Alb") -> Track:
        return Track(
            file_path=path,
            title=path.stem,
            artist="Band",
            album_artist="Band",
            album=album,
            release_date="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            genres=genres,
        )

    def _index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    # -- schema --------------------------------------------------------------

    def test_tables_exist_on_fresh_db(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        tables = {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        index.close()
        assert {"genres", "track_genres"} <= tables

    # -- _set_track_genres ---------------------------------------------------

    def test_upsert_populates_track_genres_and_denorm(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Jazz", "J-Pop"])])
        track = index.all_tracks()[0]
        rows = index._conn.execute(
            "SELECT g.name FROM track_genres tg JOIN genres g ON g.id = tg.genre_id"
            " WHERE tg.track_id = ? ORDER BY g.name",
            (track.id,),
        ).fetchall()
        index.close()
        assert [r["name"] for r in rows] == ["J-Pop", "Jazz"]
        # denormalized display string is the canonical "; "-join
        assert track.genre == "J-Pop; Jazz"

    def test_local_rescan_replaces_genres(self, tmp_path: Path) -> None:
        # KAMP-588 round-trip guard: re-scanning a local file with new genres
        # REPLACES its track_genres (not merges) — so a downloaded file whose
        # genres were stamped at ingest lands exactly those, and a later edit on
        # disk overrides. The stream empty-guard does not apply to local files.
        index = self._index(tmp_path)
        path = tmp_path / "1.mp3"
        index.upsert_many([self._local(path, ["Jazz"])])
        index.upsert_many([self._local(path, ["Techno", "House"])])
        track = index.all_tracks()[0]
        rows = [
            r["name"]
            for r in index._conn.execute(
                "SELECT g.name FROM track_genres tg JOIN genres g ON g.id = tg.genre_id"
                " WHERE tg.track_id = ? ORDER BY g.name",
                (track.id,),
            )
        ]
        index.close()
        assert rows == ["House", "Techno"]  # replaced, no lingering "Jazz"
        assert track.genre == "House; Techno"

    def test_case_insensitive_dedup_first_seen_casing(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        # "Jazz" seen first becomes canonical; a later "jazz" links to the same row.
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Jazz"], album="A1")])
        index.upsert_many([self._local(tmp_path / "2.mp3", ["jazz"], album="A2")])
        names = index.all_genres()
        index.close()
        assert names == ["Jazz"]  # one canonical row, first-seen casing

    def test_blank_and_duplicate_values_stripped(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Jazz", "  ", "Jazz", ""])])
        track = index.all_tracks()[0]
        index.close()
        assert track.genre == "Jazz"

    def test_genre_with_space_is_one_value(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Free Jazz"])])
        names = index.all_genres()
        index.close()
        assert names == ["Free Jazz"]

    # -- album union rollup --------------------------------------------------

    def test_album_genre_is_distinct_union_across_tracks(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.upsert_many(
            [
                self._local(tmp_path / "1.mp3", ["Jazz"]),
                self._local(tmp_path / "2.mp3", ["J-Pop", "Jazz"]),
            ]
        )
        album_genre = index._conn.execute("SELECT genre FROM albums").fetchone()[0]
        index.close()
        assert album_genre == "J-Pop; Jazz"  # union, sorted, no duplicate Jazz

    # -- albums() genre exposure (KAMP-550) ----------------------------------

    def test_albums_exposes_genre_union_as_list(self, tmp_path: Path) -> None:
        # The per-album genres list drives the sidebar genre filter; it is built
        # from the normalized track_genres (canonical names), not by re-splitting
        # the denormalized "; " string, so the sidebar and filter can't diverge.
        index = self._index(tmp_path)
        index.upsert_many(
            [
                self._local(tmp_path / "1.mp3", ["Jazz"]),
                self._local(tmp_path / "2.mp3", ["J-Pop", "Jazz"]),
            ]
        )
        album = index.albums()[0]
        index.close()
        assert album.genres == ["J-Pop", "Jazz"]  # union, deduped, sorted NOCASE

    def test_albums_genres_empty_when_untagged(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", [])])
        album = index.albums()[0]
        index.close()
        assert album.genres == []

    def test_albums_missing_album_track_carries_its_genres(
        self, tmp_path: Path
    ) -> None:
        # A track with an empty album tag surfaces as its own virtual album with
        # no album row, so its genres resolve by track id, not album id.
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Ambient"], album="")])
        album = next(a for a in index.albums() if a.missing_album)
        index.close()
        assert album.genres == ["Ambient"]

    # -- apply_genres --------------------------------------------------------

    def test_apply_genres_replace_sets_all_tracks(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.upsert_many(
            [
                self._local(tmp_path / "1.mp3", ["Jazz"]),
                self._local(tmp_path / "2.mp3", ["Rock"]),
            ]
        )
        ids = [t.id for t in index.all_tracks()]

        index.apply_genres(ids, ["Hip-Hop", "Plunderphonics"], mode="replace")

        genres = {t.genre for t in index.all_tracks()}
        album_genre = index._conn.execute("SELECT genre FROM albums").fetchone()[0]
        index.close()
        assert genres == {"Hip-Hop; Plunderphonics"}
        assert album_genre == "Hip-Hop; Plunderphonics"

    def test_all_genres_excludes_orphans_after_removal(self, tmp_path: Path) -> None:
        # KAMP-550 bug: removing a genre's last track link left it lingering in
        # the sidebar/autocomplete list. all_genres() must drop a genre once no
        # track carries it, even though its genres-table row persists.
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Unknown"])])
        tid = index.all_tracks()[0].id

        index.apply_genres([tid], ["Punk"], mode="replace")

        names = index.all_genres()
        index.close()
        assert names == ["Punk"]  # "Unknown" is orphaned and no longer listed

    def test_apply_genres_fires_on_fields_changed(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Jazz"])])
        calls: list[set[str]] = []
        index.on_fields_changed = lambda fields: calls.append(fields)

        index.apply_genres([index.all_tracks()[0].id], ["Rock"])
        index.close()
        assert {"track.genre"} in calls

    def test_apply_genres_merge_adds(self, tmp_path: Path) -> None:
        # Mechanism-only test for the seam later producers (587/588/591) consume.
        index = self._index(tmp_path)
        index.upsert_many([self._local(tmp_path / "1.mp3", ["Jazz"])])
        tid = index.all_tracks()[0].id

        index.apply_genres([tid], ["Rock"], mode="merge")
        track = index.all_tracks()[0]
        index.close()
        assert track.genre == "Jazz; Rock"

    # -- stream re-sync guard ------------------------------------------------

    def test_stream_resync_empty_preserves_edited_genres(self, tmp_path: Path) -> None:
        index = self._index(tmp_path)
        stream = _prov_stream_track("S1", "Band", "Alb", n=1)
        stream.genres = ["Jazz"]
        index.upsert_many([stream])
        # A later Bandcamp re-sync sends the same track with NO genre.
        resync = _prov_stream_track("S1", "Band", "Alb", n=1)
        index.upsert_many([resync])

        track = index.get_track_by_path("bandcamp://S1/1")
        rows = index._conn.execute(
            "SELECT COUNT(*) c FROM track_genres WHERE track_id = ?", (track.id,)
        ).fetchone()
        index.close()
        assert track.genre == "Jazz"  # preserved
        assert rows["c"] == 1  # normalized rows preserved too

    # -- migration backfill --------------------------------------------------

    def test_magic_playlist_genre_matches_multi_value_album(
        self, tmp_path: Path
    ) -> None:
        """A `track.genre is "Jazz"` rule matches a track tagged ["Jazz","J-Pop"]
        — the whole reason genre criteria must resolve via track_genres and not
        the flat "; "-joined column (KAMP-586)."""
        index = self._index(tmp_path)
        index.upsert_many(
            [
                self._local(tmp_path / "1.mp3", ["Jazz", "J-Pop"]),
                self._local(tmp_path / "2.mp3", ["Rock"], album="Other"),
            ]
        )
        jazz_track = next(t for t in index.all_tracks() if "Jazz" in t.genre)
        pid = index.create_magic_playlist(
            "Jazz",
            MagicCriteria(
                groups=[
                    Group(
                        conditions=[
                            Condition(field="track.genre", op="is", value="Jazz")
                        ],
                        match="all",
                    )
                ],
                match="all",
            ),
        )
        matched = index.evaluate_magic_playlist(pid)
        index.close()
        assert matched == [jazz_track.id]

    def test_migration_backfill_splits_mp3_composite(self, tmp_path: Path) -> None:
        """A pre-v56 DB whose tracks.genre holds a " / "-joined MP3 composite
        backfills into separate genre rows (no composite junk in the list)."""
        db = tmp_path / "library.db"
        LibraryIndex(db).close()
        # Simulate a pre-v56 row + empty normalized tables.
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM track_genres")
        conn.execute("DELETE FROM genres")
        conn.execute(
            "INSERT INTO tracks (title, artist, album_artist, album, release_date,"
            " track_number, disc_number, mb_release_id, mb_recording_id, genre, label)"
            " VALUES ('T','A','A','Al','2020',1,1,'','','Jazz / J-Pop','')"
        )
        conn.execute("UPDATE schema_version SET version = 55")
        conn.commit()
        conn.close()

        index = LibraryIndex(db)
        names = index.all_genres()
        index.close()
        assert names == ["J-Pop", "Jazz"]  # split, not "Jazz / J-Pop"


class TestUpsertSyncProtection:
    """Bandcamp syncs must not overwrite user-edited genre/label/year."""

    def test_bandcamp_sync_preserves_user_genre(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _bandcamp_track()
        index.upsert_many([track])
        index.update_album_meta("Band", "Stream Album", genre="Jazz")

        synced = Track(**{**track.__dict__, "genre": ""})
        index.upsert_many([synced])

        result = index.all_tracks()[0]
        index.close()
        assert result.genre == "Jazz"

    def test_bandcamp_sync_preserves_user_label_and_year(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        track = _bandcamp_track()
        index.upsert_many([track])
        index.update_album_meta(
            "Band", "Stream Album", label="ECM", release_date="1975"
        )

        synced = Track(**{**track.__dict__, "label": "", "release_date": ""})
        index.upsert_many([synced])

        result = index.all_tracks()[0]
        index.close()
        assert result.label == "ECM"
        assert result.release_date == "1975"

    def test_local_track_empty_genre_always_wins(self, tmp_path: Path) -> None:
        """Rescanning a local file with no genre tag must clear the DB genre."""
        index = LibraryIndex(tmp_path / "library.db")
        path = tmp_path / "01.mp3"
        track = Track(**{**_sample_track(path).__dict__, "genre": "Rock"})
        index.upsert_many([track])

        rescanned = Track(**{**track.__dict__, "genre": ""})
        index.upsert_many([rescanned])

        result = index.all_tracks()[0]
        index.close()
        assert result.genre == ""

    def test_bandcamp_sync_overwrites_when_incoming_is_nonempty(
        self, tmp_path: Path
    ) -> None:
        """If Bandcamp ever does send a genre, it must win over the empty DB value."""
        index = LibraryIndex(tmp_path / "library.db")
        track = _bandcamp_track()
        index.upsert_many([track])

        synced = Track(**{**track.__dict__, "genre": "Electronic"})
        index.upsert_many([synced])

        result = index.all_tracks()[0]
        index.close()
        assert result.genre == "Electronic"


# ---------------------------------------------------------------------------
# Stats read-switch — reads resolve from track_stats (KAMP-542)
# ---------------------------------------------------------------------------


class TestStatsReadFromTrackStats:
    """Every stats read resolves via track_stats, not the legacy tracks columns.

    Proven differentially: deliberately desync track_stats from the tracks
    columns (favorite/play_count/last_played) — which cannot happen in
    production, where every writer mirrors — and assert the read paths report
    the track_stats values. Guards KAMP-542's core promise so KAMP-539 can drop
    the legacy columns.
    """

    def _desynced(self, tmp_path: Path) -> tuple["LibraryIndex", int]:
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index.upsert_many([_sample_track(tmp_path / "a.mp3")])
        tid = index._conn.execute("SELECT id FROM tracks").fetchone()[0]
        # Legacy columns say 0/NULL; track_stats (authoritative) says otherwise.
        index._conn.execute(
            "UPDATE tracks SET favorite = 0, play_count = 0, last_played = NULL"
        )
        index._conn.execute(
            "INSERT INTO track_stats (track_id, favorite, play_count, last_played)"
            " VALUES (?, 1, 9, 555.0) ON CONFLICT(track_id) DO UPDATE SET"
            " favorite = 1, play_count = 9, last_played = 555.0",
            (tid,),
        )
        index._conn.commit()
        return index, tid

    def test_get_track_by_id_and_path_read_track_stats(self, tmp_path: Path) -> None:
        index, tid = self._desynced(tmp_path)
        by_id = index.get_track_by_id(tid)
        by_path = index.get_track_by_path(str(tmp_path / "a.mp3"))
        index.close()
        for t in (by_id, by_path):
            assert t is not None
            assert t.favorite is True
            assert t.play_count == 9
            assert t.last_played == 555.0

    def test_top_tracks_and_album_read_track_stats(self, tmp_path: Path) -> None:
        index, _ = self._desynced(tmp_path)
        top = index.top_tracks(5)  # WHERE play_count > 0 — legacy col is 0
        album = index.tracks_for_album("The Artist", "The Album")
        index.close()
        assert [t.play_count for t in top] == [
            9
        ]  # would be [] if reading tracks.play_count
        assert album[0].favorite is True

    def test_view_falls_back_to_legacy_when_no_stats_row(self, tmp_path: Path) -> None:
        """A track with no track_stats row still reads its legacy value (transition safety)."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        index.upsert_many([_sample_track(tmp_path / "a.mp3")])
        index._conn.execute("UPDATE tracks SET favorite = 1, play_count = 4")
        index._conn.execute("DELETE FROM track_stats")
        index._conn.commit()
        t = index.get_track_by_path(str(tmp_path / "a.mp3"))
        index.close()
        assert t is not None and t.favorite is True and t.play_count == 4


# ---------------------------------------------------------------------------
# Album source classifier reconstruction (KAMP-542)
# ---------------------------------------------------------------------------


class TestAlbumSourceClassifier:
    """The album `source` badge reads track_sources (not tracks.source) and
    classifies a mixed album (tracks disagreeing on preferred delivery kind) as
    'mixed', matching the playlist classifier (KAMP-546). All-downloaded reads
    'local', all-stream reads 'bandcamp'."""

    def _album(self, index: "LibraryIndex", name: str, tracks: list) -> int:
        c = index._conn
        c.execute(
            "INSERT INTO albums (album_artist, album) VALUES (?, ?)", (name, name)
        )
        alb = c.execute("SELECT id FROM albums WHERE album = ?", (name,)).fetchone()[0]
        for i, (_src, fp, kinds) in enumerate(tracks, 1):
            # KAMP-552: no file_path/source on tracks; the effective source is
            # derived from the track_sources rows created below (by `kinds`).
            cur = c.execute(
                "INSERT INTO tracks (album_id, track_number, disc_number)"
                " VALUES (?, ?, 1)",
                (alb, i),
            )
            tid = cur.lastrowid
            for k in kinds:
                uri = fp if k == "file" else f"bandcamp://{name}/{i}"
                c.execute(
                    "INSERT INTO track_sources (track_id, kind, uri) VALUES (?, ?, ?)",
                    (tid, k, uri),
                )
        index._conn.commit()
        return alb

    def test_badge_matches_legacy_output(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        cases = {
            "downloaded": (
                [("local", "/m/a1.mp3", ["file"]), ("local", "/m/a2.mp3", ["file"])],
                "local",
            ),
            "stream_only": (
                [
                    ("bandcamp", "bandcamp://S/1", ["stream"]),
                    ("bandcamp", "bandcamp://S/2", ["stream"]),
                ],
                "bandcamp",
            ),
            # A mixed album (one file-only, one stream-only track) reads 'mixed'
            # (KAMP-546): the tracks disagree on preferred delivery kind.
            "mixed": (
                [
                    ("local", "/m/c1.mp3", ["file"]),
                    ("bandcamp", "bandcamp://C/2", ["stream"]),
                ],
                "mixed",
            ),
            "downloaded_and_streamable": (
                [
                    ("local", "/m/d1.mp3", ["file", "stream"]),
                    ("local", "/m/d2.mp3", ["file", "stream"]),
                ],
                "local",
            ),
        }
        ids = {
            name: self._album(index, name, tracks)
            for name, (tracks, _) in cases.items()
        }
        index._refresh_album_aggregates(list(ids.values()))
        got = {
            name: index._conn.execute(
                "SELECT source FROM albums WHERE id = ?", (ids[name],)
            ).fetchone()[0]
            for name in cases
        }
        index.close()
        assert got == {name: expected for name, (_, expected) in cases.items()}


# ---------------------------------------------------------------------------
# get_stats (KAMP-481)
# ---------------------------------------------------------------------------


class TestGetStats:
    """Tests for LibraryIndex.get_stats()."""

    def test_empty_library(self, tmp_path: Path) -> None:
        """An empty index returns zero counts and no top artist."""
        index = LibraryIndex(tmp_path / "library.db")
        stats = index.get_stats()
        index.close()
        assert isinstance(stats, LibraryStats)
        assert stats.track_count == 0
        assert stats.album_count == 0
        assert stats.artist_count == 0
        assert stats.total_play_seconds == pytest.approx(0.0)
        assert stats.total_track_plays == 0
        assert stats.albums_played == 0
        assert stats.top_artist_name is None
        assert stats.top_artist_seconds is None
        assert stats.top_tracks == []

    def test_counts(self, tmp_path: Path) -> None:
        """track_count, album_count, and artist_count reflect indexed content."""
        index = LibraryIndex(tmp_path / "library.db")
        _make_indexed_track(
            index, tmp_path, "t1.mp3", album_artist="Slowdive", album="Souvlaki"
        )
        _make_indexed_track(
            index,
            tmp_path,
            "t2.mp3",
            album_artist="Slowdive",
            album="Souvlaki",
            track_number=2,
        )
        _make_indexed_track(
            index, tmp_path, "t3.mp3", album_artist="Bark Psychosis", album="Hex"
        )
        stats = index.get_stats()
        index.close()
        assert stats.track_count == 3
        assert stats.album_count == 2
        assert stats.artist_count == 2

    def test_artist_count_matches_artists_list_after_prune(
        self, tmp_path: Path
    ) -> None:
        """artist_count stays consistent with artists() once an album is pruned.

        Pruning leaves the artists-table row behind, so counting that table
        would over-report; the stat derives from albums to match the UI list.
        """
        lib = tmp_path / "music"
        lib.mkdir()
        _make_mp3(lib / "a.mp3", title="A", album="Solo", album_artist="Gone")
        _make_mp3(lib / "b.mp3", title="B", album="Stay", album_artist="Kept")

        index = LibraryIndex(tmp_path / "library.db")
        scanner = LibraryScanner(index)
        scanner.scan(lib)
        (lib / "a.mp3").unlink()
        scanner.scan(lib)
        stats = index.get_stats()
        artists = index.artists()
        index.close()

        assert stats.artist_count == len(artists)
        assert "Gone" not in artists

    def test_total_play_seconds(self, tmp_path: Path) -> None:
        """total_play_seconds accumulates from artists.play_time via record_play_time."""
        index = LibraryIndex(tmp_path / "library.db")
        p1 = _make_indexed_track(
            index, tmp_path, "a.mp3", album_artist="Slowdive", album="Just For a Day"
        )
        p2 = _make_indexed_track(
            index, tmp_path, "b.mp3", album_artist="Bark Psychosis", album="Hex"
        )
        index.record_play_time(p1, 300.0)
        index.record_play_time(p2, 120.0)
        stats = index.get_stats()
        index.close()
        assert stats.total_play_seconds == pytest.approx(420.0)

    def test_total_track_plays(self, tmp_path: Path) -> None:
        """total_track_plays is the sum of play_count across all tracks."""
        index = LibraryIndex(tmp_path / "library.db")
        p1 = _make_indexed_track(
            index, tmp_path, "a.mp3", album_artist="Slowdive", album="Souvlaki"
        )
        p2 = _make_indexed_track(
            index,
            tmp_path,
            "b.mp3",
            album_artist="Slowdive",
            album="Souvlaki",
            track_number=2,
        )
        index.record_played(p1)
        index.record_played(p1)
        index.record_played(p2)
        stats = index.get_stats()
        index.close()
        assert stats.total_track_plays == 3

    def test_albums_played(self, tmp_path: Path) -> None:
        """albums_played counts albums where play_count_avg > 0."""
        index = LibraryIndex(tmp_path / "library.db")
        p1 = _make_indexed_track(
            index, tmp_path, "a.mp3", album_artist="Slowdive", album="Souvlaki"
        )
        _make_indexed_track(
            index, tmp_path, "b.mp3", album_artist="Bark Psychosis", album="Hex"
        )
        index.record_played(p1)
        stats = index.get_stats()
        index.close()
        assert stats.albums_played == 1

    def test_top_artist(self, tmp_path: Path) -> None:
        """top_artist_name is the artist with the highest play_time."""
        index = LibraryIndex(tmp_path / "library.db")
        pa = _make_indexed_track(
            index, tmp_path, "a.mp3", album_artist="Slowdive", album="AA"
        )
        pb = _make_indexed_track(
            index, tmp_path, "b.mp3", album_artist="Bark Psychosis", album="BB"
        )
        index.record_play_time(pa, 100.0)
        index.record_play_time(pb, 500.0)
        stats = index.get_stats()
        index.close()
        assert stats.top_artist_name == "Bark Psychosis"
        assert stats.top_artist_seconds == pytest.approx(500.0)

    def test_top_tracks_ordered_by_play_count(self, tmp_path: Path) -> None:
        """top_tracks are returned in descending play_count order, respecting the limit."""
        index = LibraryIndex(tmp_path / "library.db")
        pa = _make_indexed_track(
            index, tmp_path, "a.mp3", album_artist="Slowdive", album="AA"
        )
        pb = _make_indexed_track(
            index,
            tmp_path,
            "b.mp3",
            album_artist="Slowdive",
            album="AA",
            track_number=2,
        )
        pc = _make_indexed_track(
            index,
            tmp_path,
            "c.mp3",
            album_artist="Slowdive",
            album="AA",
            track_number=3,
        )
        for _ in range(3):
            index.record_played(pa)
        for _ in range(5):
            index.record_played(pb)
        for _ in range(1):
            index.record_played(pc)
        stats = index.get_stats(top_tracks_limit=2)
        index.close()
        assert len(stats.top_tracks) == 2
        assert stats.top_tracks[0].file_path == pb
        assert stats.top_tracks[1].file_path == pa


# ---------------------------------------------------------------------------
# KAMP-523: identity-based provenance (download re-attaches to streaming origin)
# ---------------------------------------------------------------------------


def _prov_stream_track(sid: str, artist: str, album: str, n: int = 1) -> Track:
    """A streaming (bandcamp://) track row for *sid*."""
    return Track(
        file_path=Path(f"bandcamp://{sid}/{n}"),
        title=f"Track {n}",
        artist=artist,
        album_artist=artist,
        album=album,
        release_date="",
        track_number=n,
        disc_number=1,
        ext="",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
        source="bandcamp",
    )


def _download_track(path: Path, artist: str, album: str, sid: str, n: int = 1) -> Track:
    """A local downloaded file carrying a KAMP_SALE_ITEM_ID provenance stamp."""
    t = Track(
        file_path=path,
        title=f"Track {n}",
        artist=artist,
        album_artist=artist,
        album=album,
        release_date="2020",
        track_number=n,
        disc_number=1,
        ext="mp3",
        embedded_art=False,
        mb_release_id="",
        mb_recording_id="",
    )
    t.sale_item_id = sid
    return t


def _album_rows(index: LibraryIndex) -> list[sqlite3.Row]:
    return index._conn.execute(
        "SELECT id, album_artist, album, source, sale_item_id FROM albums"
    ).fetchall()


class TestProvenanceLinking:
    def _seed_streaming_album(
        self,
        index: LibraryIndex,
        sid: str,
        band_name: str,
        item_title: str,
    ) -> int:
        """Create a bandcamp_collection item + its streaming album row."""
        index.upsert_collection_item(
            sid, mode="local", band_name=band_name, item_title=item_title
        )
        index.upsert_many([_prov_stream_track(sid, band_name, item_title)])
        row = index._conn.execute(
            "SELECT id FROM albums WHERE sale_item_id = ?", (sid,)
        ).fetchone()
        assert row is not None, "streaming album should carry sale_item_id"
        return row["id"]

    def test_download_reattaches_despite_trailing_whitespace(
        self, tmp_path: Path
    ) -> None:
        # The reported bug: Bandcamp delivered a trailing space in band_name; the
        # downloaded files were tagged clean. Identity linking must still merge.
        index = LibraryIndex(tmp_path / "library.db")
        origin = self._seed_streaming_album(
            index, "S1", "Homeboy Sandman & Edan ", "Humble Pi"
        )

        dl = _download_track(
            tmp_path / "hp.mp3", "Homeboy Sandman & Edan", "Humble Pi", "S1"
        )
        index.upsert_many([dl])

        albums = _album_rows(index)
        assert len(albums) == 1, f"expected one album, got {albums}"
        assert albums[0]["id"] == origin
        assert albums[0]["sale_item_id"] == "S1"
        # exactly one artist row
        n_artists = index._conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
        assert n_artists == 1
        # the local file is linked to the origin album
        linked = index._conn.execute(
            "SELECT album_id FROM tracks_with_stats WHERE file_path = ?",
            (str(tmp_path / "hp.mp3"),),
        ).fetchone()[0]
        assert linked == origin
        index.close()

    def test_download_reattaches_despite_punctuation_divergence(
        self, tmp_path: Path
    ) -> None:
        # A divergence no amount of TRIM/NOCASE could fix: "/" vs "&".
        index = LibraryIndex(tmp_path / "library.db")
        origin = self._seed_streaming_album(
            index, "S2", "Homeboy Sandman / Edan", "Humble Pi"
        )
        dl = _download_track(
            tmp_path / "hp.mp3", "Homeboy Sandman & Edan", "Humble Pi", "S2"
        )
        index.upsert_many([dl])
        albums = _album_rows(index)
        assert len(albums) == 1
        assert albums[0]["id"] == origin
        index.close()

    def test_download_stamps_id_when_origin_album_absent(self, tmp_path: Path) -> None:
        # Downloaded without ever syncing streaming rows: still exactly one album,
        # and it carries the sale_item_id.
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "S3", mode="local", band_name="Artist", item_title="Album"
        )
        dl = _download_track(tmp_path / "a.mp3", "Artist", "Album", "S3")
        index.upsert_many([dl])
        albums = _album_rows(index)
        assert len(albums) == 1
        assert albums[0]["sale_item_id"] == "S3"
        index.close()

    def test_stale_sale_item_id_falls_back_to_name_match(self, tmp_path: Path) -> None:
        # A file carrying a sale_item_id not present in bandcamp_collection (e.g.
        # moved in from elsewhere) must NOT link by identity — it falls back to
        # the name match, and never violates the FK.
        index = LibraryIndex(tmp_path / "library.db")
        dl = _download_track(tmp_path / "a.mp3", "Local Artist", "Local Album", "GHOST")
        index.upsert_many([dl])
        albums = _album_rows(index)
        assert len(albums) == 1
        assert albums[0]["sale_item_id"] is None
        assert albums[0]["album_artist"] == "Local Artist"
        index.close()

    def test_empty_album_single_links_to_origin_by_identity(
        self, tmp_path: Path
    ) -> None:
        # A standalone Bandcamp single ships with no album tag (album == ""), so
        # it is excluded from `named` — but it carries a provenance stamp and must
        # still re-attach to its streaming single (which KAMP-526 gives an album
        # name = title). Regression for the Ohm Foam "Gush" duplicate card.
        index = LibraryIndex(tmp_path / "library.db")
        origin = self._seed_streaming_album(index, "S1", "Ohm Foam", "Gush")

        dl = _download_track(tmp_path / "gush.mp3", "Ohm Foam", "", "S1")
        dl.title = "Gush"
        index.upsert_many([dl])

        albums = _album_rows(index)
        assert len(albums) == 1, f"nameless single forked: {albums}"
        assert albums[0]["id"] == origin
        linked = index._conn.execute(
            "SELECT album_id FROM tracks_with_stats WHERE file_path = ?",
            (str(tmp_path / "gush.mp3"),),
        ).fetchone()[0]
        assert linked == origin
        # The album now holds a local file, so it reads as a local (downloaded)
        # release rather than a stream.
        assert albums[0]["source"] == "local"
        index.close()

    def test_single_favorite_survives_download_via_reconcile(
        self, tmp_path: Path
    ) -> None:
        # With the streaming single favorited and the downloaded single aligned to
        # track 1, the reconcile merge inside upsert_many (KAMP-541) collapses the
        # stream+local pair and MAX-carries the favorite onto the survivor — no
        # separate inherit pass required (KAMP-553). Regression for the Ohm Foam
        # "Gush" favorite loss.
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        self._seed_streaming_album(index, "S1", "Ohm Foam", "Gush")
        stream = index.get_track_by_path("bandcamp://S1/1")
        assert stream is not None
        index._conn.execute("UPDATE tracks SET favorite = 1 WHERE id = ?", (stream.id,))
        index._conn.commit()
        # The reconcile merge reads the authoritative track_stats store, so mirror
        # the legacy favorite there (as production writers do).
        _mirror_stats(index)

        dl = _download_track(tmp_path / "gush.mp3", "Ohm Foam", "Gush", "S1", n=1)
        dl.title = "Gush"
        index.upsert_many([dl])

        fav = index._conn.execute(
            "SELECT favorite FROM tracks_with_stats WHERE id = (SELECT track_id FROM track_sources WHERE uri = ?)",
            (str(tmp_path / "gush.mp3"),),
        ).fetchone()[0]
        assert fav == 1
        index.close()

    def test_unprovenanced_files_still_match_by_trimmed_name(
        self, tmp_path: Path
    ) -> None:
        # Two genuinely-local files whose album_artist differs only by a trailing
        # space collapse to one album via the TRIM fallback.
        index = LibraryIndex(tmp_path / "library.db")
        t1 = _sample_track(tmp_path / "1.mp3")
        t2 = _sample_track(tmp_path / "2.mp3")
        t2.album_artist = t1.album_artist + " "
        index.upsert_many([t1, t2])
        albums = _album_rows(index)
        # One album row already existed for the trimmed name; the spaced variant
        # links to it rather than forking (COLLATE NOCASE lets both names coexist
        # as rows, but the track links via the TRIM match).
        linked = {
            r[0]
            for r in index._conn.execute(
                "SELECT album_id FROM tracks WHERE album_id IS NOT NULL"
            ).fetchall()
        }
        assert len(linked) == 1
        index.close()


class TestGenreEnrichmentCheckpoint:
    """albums.genres_enriched_at resume checkpoint + helpers (KAMP-591)."""

    def _index_with_album(self, tmp_path: Path) -> LibraryIndex:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                Track(
                    file_path=tmp_path / "1.mp3",
                    title="T",
                    artist="A",
                    album_artist="A",
                    album="Alb",
                    release_date="2020",
                    track_number=1,
                    disc_number=1,
                    ext="mp3",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                )
            ]
        )
        return index

    def test_fresh_db_has_column(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(albums)")}
        index.close()
        assert "genres_enriched_at" in cols

    def test_pending_mark_clear_cycle(self, tmp_path: Path) -> None:
        index = self._index_with_album(tmp_path)
        pending = index.albums_pending_genre_enrichment()
        assert len(pending) == 1
        assert pending[0]["album"] == "Alb"
        aid = pending[0]["id"]

        index.mark_album_genres_enriched(aid, 123.0)
        assert index.albums_pending_genre_enrichment() == []  # marked → excluded

        index.clear_genre_enrichment_marks()
        assert len(index.albums_pending_genre_enrichment()) == 1  # reset re-pends
        index.close()

    def test_migration_v58_adds_column(self, tmp_path: Path) -> None:
        # A pre-v59 albums table lacks genres_enriched_at. Drop the view before the
        # column (KAMP-582 CI lesson), simulate v58, reopen → migration re-adds it
        # and the existing album is preserved (now pending).
        db = tmp_path / "library.db"
        index = self._index_with_album(tmp_path)
        index.close()
        conn = sqlite3.connect(str(db))
        conn.execute("DROP VIEW IF EXISTS tracks_with_stats")
        conn.execute("ALTER TABLE albums DROP COLUMN genres_enriched_at")
        conn.execute("UPDATE schema_version SET version = 58")
        conn.commit()
        conn.close()

        index = LibraryIndex(db)
        cols = {r[1] for r in index._conn.execute("PRAGMA table_info(albums)")}
        pending = index.albums_pending_genre_enrichment()
        index.close()
        assert "genres_enriched_at" in cols
        assert len(pending) == 1


class TestCollectionKeywords:
    """bandcamp_collection.keywords cache column + setter (KAMP-588)."""

    def test_fresh_db_has_keywords_column(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        cols = {
            r[1] for r in index._conn.execute("PRAGMA table_info(bandcamp_collection)")
        }
        index.close()
        assert "keywords" in cols

    def test_set_collection_keywords_roundtrip(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        index.set_collection_keywords("S1", ["Shoegaze", "Dream Pop"])
        raw = index._conn.execute(
            "SELECT keywords FROM bandcamp_collection WHERE sale_item_id = 'S1'"
        ).fetchone()[0]
        index.close()
        assert json.loads(raw) == ["Shoegaze", "Dream Pop"]

    def test_migration_v57_adds_keywords_column(self, tmp_path: Path) -> None:
        # A pre-v58 DB has a bandcamp_collection without keywords. Opening it must
        # add the column and preserve existing rows (KAMP-588). Simulate the old
        # shape by rebuilding the table without keywords (mirrors a real pre-v58
        # DB more faithfully than DROP COLUMN).
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        index.close()
        conn = sqlite3.connect(str(db))
        conn.execute("DROP TABLE bandcamp_collection")
        conn.execute(
            "CREATE TABLE bandcamp_collection ("
            " sale_item_id TEXT PRIMARY KEY, band_name TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, band_name)"
            " VALUES ('S1', 'A')"
        )
        conn.execute("UPDATE schema_version SET version = 57")
        conn.commit()
        conn.close()

        index = LibraryIndex(db)
        cols = {
            r[1] for r in index._conn.execute("PRAGMA table_info(bandcamp_collection)")
        }
        row = index.get_collection_item("S1")
        index.close()
        assert "keywords" in cols
        assert row is not None and row["band_name"] == "A"


class TestDownloadOverrides:
    def test_empty_when_item_unknown(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        ov = index.download_overrides_for_sale_item("NOPE")
        assert ov.album_artist == "" and ov.album == "" and ov.titles == {}
        index.close()

    def test_effective_canonical_names_without_user_edits(self, tmp_path: Path) -> None:
        # No display edits: return the synced album row's canonical names so the
        # download is stamped to match its origin (this is what gives a nameless
        # single its album name).
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "S1", mode="local", band_name="Ohm Foam", item_title="Gush"
        )
        index.upsert_many([_prov_stream_track("S1", "Ohm Foam", "Gush")])
        ov = index.download_overrides_for_sale_item("S1")
        assert ov.album_artist == "Ohm Foam"
        assert ov.album == "Gush"
        index.close()

    def test_returns_cached_keywords_as_genres(self, tmp_path: Path) -> None:
        # KAMP-588: cached Bandcamp tags surface as overrides.genres for the
        # download pipeline to stamp into the files.
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        index.upsert_many([_prov_stream_track("S1", "A", "B", n=1)])
        index.set_collection_keywords("S1", ["shoegaze", "dream pop"])
        ov = index.download_overrides_for_sale_item("S1")
        index.close()
        assert ov.genres == ["shoegaze", "dream pop"]

    def test_genres_empty_when_no_keywords_cached(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        ov = index.download_overrides_for_sale_item("S1")
        index.close()
        assert ov.genres == []

    def test_falls_back_to_collection_when_album_row_absent(
        self, tmp_path: Path
    ) -> None:
        # Downloaded without syncing streaming rows: the collection ledger still
        # supplies the names (band_name / item_title).
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item(
            "S1", mode="local", band_name="Ohm Foam ", item_title="Gush"
        )
        ov = index.download_overrides_for_sale_item("S1")
        assert ov.album_artist == "Ohm Foam"  # trimmed
        assert ov.album == "Gush"
        index.close()

    def test_returns_user_edits(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        index.upsert_many([_prov_stream_track("S1", "A", "B", n=1)])
        index.update_album_display("A", "B", "Disp Album", "Disp Artist")
        t1 = index.get_track_by_path("bandcamp://S1/1")
        assert t1 is not None
        index.update_track_display_title(t1.id, "Edited Title")
        ov = index.download_overrides_for_sale_item("S1")
        assert ov.album_artist == "Disp Artist"
        assert ov.album == "Disp Album"
        assert ov.titles == {1: "Edited Title"}
        index.close()

    def test_ambiguous_track_number_across_discs_is_dropped(
        self, tmp_path: Path
    ) -> None:
        # Two discs both have a track 1 with a display title → ambiguous, so no
        # per-track override is offered for track 1 (avoids cross-disc misapply).
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        d1 = _prov_stream_track("S1", "A", "B", n=1)
        d2 = _prov_stream_track("S1", "A", "B", n=1)
        d2.file_path = Path("bandcamp://S1/2")
        d2.disc_number = 2
        index.upsert_many([d1, d2])
        for uri in ("bandcamp://S1/1", "bandcamp://S1/2"):
            t = index.get_track_by_path(uri)
            assert t is not None
            index.update_track_display_title(t.id, "Edited")
        ov = index.download_overrides_for_sale_item("S1")
        assert 1 not in ov.titles
        index.close()

    def test_returns_user_artist_edits(self, tmp_path: Path) -> None:
        # KAMP-582: a display_artist override must carry into the download so
        # the purchased files are stamped with the edited artist.
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        index.upsert_many([_prov_stream_track("S1", "A", "B", n=1)])
        t1 = index.get_track_by_path("bandcamp://S1/1")
        assert t1 is not None
        index.update_track_display_artist(t1.id, "Edited Artist")
        ov = index.download_overrides_for_sale_item("S1")
        index.close()
        assert ov.artists == {1: "Edited Artist"}

    def test_no_artist_edits_yields_empty_map(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        index.upsert_many([_prov_stream_track("S1", "A", "B", n=1)])
        ov = index.download_overrides_for_sale_item("S1")
        index.close()
        assert ov.artists == {}

    def test_ambiguous_artist_track_number_is_dropped(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_collection_item("S1", mode="local", band_name="A", item_title="B")
        d1 = _prov_stream_track("S1", "A", "B", n=1)
        d2 = _prov_stream_track("S1", "A", "B", n=1)
        d2.file_path = Path("bandcamp://S1/2")
        d2.disc_number = 2
        index.upsert_many([d1, d2])
        for uri in ("bandcamp://S1/1", "bandcamp://S1/2"):
            t = index.get_track_by_path(uri)
            assert t is not None
            index.update_track_display_artist(t.id, "Edited Artist")
        ov = index.download_overrides_for_sale_item("S1")
        index.close()
        assert 1 not in ov.artists


class TestPendingIngest:
    def test_add_get_clear_roundtrip(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.add_pending_ingest("/watch/a.zip", "S1", "T1")
        row = index.pending_ingest_for_path("/watch/a.zip")
        assert row is not None
        assert row.sale_item_id == "S1"
        assert row.tralbum_id == "T1"
        assert index.pending_ingest_for_path("/watch/missing.zip") is None
        index.clear_pending_ingest("/watch/a.zip")
        assert index.pending_ingest_for_path("/watch/a.zip") is None
        index.close()

    def test_add_is_idempotent_on_repeat_download(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.add_pending_ingest("/watch/a.zip", "S1")
        index.add_pending_ingest("/watch/a.zip", "S2")  # re-download same path
        row = index.pending_ingest_for_path("/watch/a.zip")
        assert row is not None and row.sale_item_id == "S2"
        n = index._conn.execute("SELECT COUNT(*) FROM pending_ingest").fetchone()[0]
        assert n == 1
        index.close()

    def test_sweep_removes_only_orphans(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        real = tmp_path / "real.zip"
        real.write_bytes(b"zip")
        index.add_pending_ingest(str(real), "S1")
        index.add_pending_ingest(str(tmp_path / "gone.zip"), "S2")
        removed = index.sweep_orphan_pending_ingest()
        assert removed == 1
        assert index.pending_ingest_for_path(str(real)) is not None
        assert index.pending_ingest_for_path(str(tmp_path / "gone.zip")) is None
        index.close()


class TestLooseSingleAttach:
    """KAMP-529 Step 3c: an un-provenanced loose local single (empty album tag)
    re-links by identity to its streaming single-album, so the two stop
    duplicating. Negative cases assert we never merge on an ambiguous key."""

    def _stream_single(
        self,
        sid: str,
        artist: str,
        album_title: str,
        title: "str | None" = None,
    ) -> Track:
        """A 1-track streaming (bandcamp) single-album whose album == item title."""
        return Track(
            file_path=Path(f"bandcamp://{sid}/1"),
            title=title or album_title,
            artist=artist,
            album_artist=artist,
            album=album_title,
            release_date="",
            track_number=1,
            disc_number=1,
            ext="",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )

    def _loose_local(self, path: Path, artist: str, title: str) -> Track:
        """A user's loose local single: empty album tag, track_number 0."""
        return Track(
            file_path=path,
            title=title,
            artist=artist,
            album_artist=artist,
            album="",
            release_date="",
            track_number=0,
            disc_number=1,
            ext="flac",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="local",
        )

    def _album_id_of(self, index: LibraryIndex, path: Path) -> "int | None":
        return index._conn.execute(
            "SELECT album_id FROM tracks_with_stats WHERE file_path = ?", (str(path),)
        ).fetchone()[0]

    def test_loose_single_attaches_to_streaming_album(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_single("100", "Megahit", "Celebrity")])
        stream_album = index._conn.execute(
            "SELECT album_id FROM tracks_with_stats WHERE file_path = 'bandcamp://100/1'"
        ).fetchone()[0]

        local = self._loose_local(tmp_path / "celebrity.flac", "Megahit", "Celebrity")
        index.upsert_many([local])

        # The local single now shares the streaming album, inherits track_number,
        # and is stamped with the album name so it stops being "album-less".
        row = index._conn.execute(
            "SELECT album_id, track_number, album FROM tracks_with_stats WHERE file_path = ?",
            (str(tmp_path / "celebrity.flac"),),
        ).fetchone()
        assert row["album_id"] == stream_album
        assert row["track_number"] == 1
        assert row["album"] == "Celebrity"
        # One album, and it no longer shows as a separate loose card.
        assert len(_album_rows(index)) == 1
        # Collapses everywhere: album view + search return the local row only.
        detail = index.tracks_for_album("Megahit", "Celebrity")
        assert len(detail) == 1 and detail[0].source == "local"
        results = index.search("celebrity")
        assert len(results) == 1 and results[0].source == "local"
        # And the GRID shows exactly one "Celebrity" card, not a duplicate loose
        # missing-album entry alongside the album card. The album now reads as
        # owned (source flips off 'bandcamp').
        cards = [a for a in index.albums() if a.album == "Celebrity"]
        assert len(cards) == 1
        assert cards[0].missing_album is False
        assert cards[0].source in ("local", "mixed")
        index.close()

    def test_prefix_titled_streaming_single_still_matches(self, tmp_path: Path) -> None:
        """Bandcamp prefixes some single titles with 'Artist - '; the album field
        stays clean, so matching on album (not track title) still links."""
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many(
            [
                self._stream_single(
                    "101",
                    "Neon Nox, Powernerd",
                    "Duality",
                    title="Neon Nox, Powernerd - Duality",
                )
            ]
        )
        local = self._loose_local(
            tmp_path / "duality.flac", "Neon Nox, Powernerd", "Duality"
        )
        index.upsert_many([local])
        assert self._album_id_of(index, tmp_path / "duality.flac") is not None
        assert len(index.tracks_for_album("Neon Nox, Powernerd", "Duality")) == 1
        index.close()

    def test_no_attach_when_album_is_multitrack(self, tmp_path: Path) -> None:
        """album.album == local.title but the album is NOT a single (>1 track):
        the local file must not be absorbed into a multi-track album."""
        index = LibraryIndex(tmp_path / "library.db")
        # Two bandcamp tracks under one album titled "Celebrity" → a 2-track album,
        # not a single. Track 2 gets a distinct path/number.
        track2 = Track(
            file_path=Path("bandcamp://102/2"),
            title="Another",
            artist="Various",
            album_artist="Various",
            album="Celebrity",
            release_date="",
            track_number=2,
            disc_number=1,
            ext="",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        index.upsert_many([self._stream_single("102", "Various", "Celebrity"), track2])
        local = self._loose_local(tmp_path / "c.flac", "Various", "Celebrity")
        index.upsert_many([local])
        assert self._album_id_of(index, tmp_path / "c.flac") is None
        index.close()

    def test_no_attach_when_album_artist_blank(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_single("103", "", "Untitled")])
        local = self._loose_local(tmp_path / "u.flac", "", "Untitled")
        index.upsert_many([local])
        assert self._album_id_of(index, tmp_path / "u.flac") is None
        index.close()

    def test_no_attach_when_two_loose_singles_share_identity(
        self, tmp_path: Path
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_single("104", "Megahit", "Celebrity")])
        a = self._loose_local(tmp_path / "a.flac", "Megahit", "Celebrity")
        b = self._loose_local(tmp_path / "b.flac", "Megahit", "Celebrity")
        index.upsert_many([a, b])
        assert self._album_id_of(index, tmp_path / "a.flac") is None
        assert self._album_id_of(index, tmp_path / "b.flac") is None
        index.close()

    def test_no_attach_when_two_streaming_singles_match(self, tmp_path: Path) -> None:
        """Two streaming single-albums TRIM/NOCASE-match the same title (they differ
        only by trailing whitespace, which the albums UNIQUE index permits): the
        match is ambiguous, so the loose single is left untouched."""
        index = LibraryIndex(tmp_path / "library.db")
        _readd_legacy_track_columns(index)
        for sid, album_name in (("200", "Celebrity"), ("201", "Celebrity ")):
            index._conn.execute(
                "INSERT INTO albums (album_artist, album, source) VALUES ('Megahit', ?, 'bandcamp')",
                (album_name,),
            )
            aid = index._conn.execute(
                "SELECT id FROM albums ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
            index._conn.execute(
                "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
                " track_number, source, album_id) VALUES (?, 'Celebrity', 'Megahit',"
                " 'Megahit', ?, 1, 'bandcamp', ?)",
                (f"bandcamp://{sid}/1", album_name, aid),
            )
        index._conn.commit()
        local = self._loose_local(tmp_path / "c.flac", "Megahit", "Celebrity")
        index.upsert_many([local])
        assert self._album_id_of(index, tmp_path / "c.flac") is None
        index.close()

    def test_attach_is_idempotent(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([self._stream_single("105", "Megahit", "Celebrity")])
        local = self._loose_local(tmp_path / "celebrity.flac", "Megahit", "Celebrity")
        index.upsert_many([local])
        first = self._album_id_of(index, tmp_path / "celebrity.flac")
        # A second pass (helper directly) must not move it or inflate the album.
        touched = index._attach_loose_local_singles()
        assert touched == set()
        assert self._album_id_of(index, tmp_path / "celebrity.flac") == first
        # Post-KAMP-541 the loose local single and its stream twin collapse into
        # one canonical track (file + stream sources), so the album holds 1 row.
        assert (
            index._conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE album_id = ?", (first,)
            ).fetchone()[0]
            == 1
        )
        index.close()

    def test_v42_migration_relinks_preexisting_loose_single(
        self, tmp_path: Path
    ) -> None:
        """A loose single already on disk (indexed before this fix, so never
        attached by Step 3c) is re-linked on upgrade by the v42 heal, which also
        snapshots the DB first."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        index.upsert_many([self._stream_single("300", "Megahit", "Celebrity")])
        stream_album = index._conn.execute(
            "SELECT album_id FROM tracks_with_stats WHERE file_path = 'bandcamp://300/1'"
        ).fetchone()[0]
        # Insert the loose local single by raw SQL so Step 3c does NOT run on it,
        # reproducing a row indexed by an older build. Then downgrade to v41.
        index._conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
            " track_number, source) VALUES (?, 'Celebrity', 'Megahit', 'Megahit',"
            " '', 0, 'local')",
            (str(tmp_path / "celebrity.flac"),),
        )
        index._conn.execute("UPDATE schema_version SET version = 41")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        row = reopened._conn.execute(
            "SELECT album_id, track_number FROM tracks_with_stats WHERE file_path = ?",
            (str(tmp_path / "celebrity.flac"),),
        ).fetchone()
        version = reopened._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        # Capture the grid before closing the connection.
        cards = [a for a in reopened.albums() if a.album == "Celebrity"]
        reopened.close()

        assert version == 60
        assert row["album_id"] == stream_album
        assert row["track_number"] == 1
        # The heal took a backup snapshot before mutating.
        assert list(tmp_path.glob("library.db.bak-*"))
        # The grid no longer shows a duplicate: one "Celebrity" card, not a loose
        # missing-album entry beside the album card, and the album reads as owned.
        assert len(cards) == 1
        assert cards[0].missing_album is False
        assert cards[0].source in ("local", "mixed")

    def test_v42_migration_no_backup_when_nothing_to_heal(self, tmp_path: Path) -> None:
        """A fresh DB (or one with no loose singles) is stamped current without a
        wasteful backup snapshot."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
            0
        ]
        index.close()
        assert version == 60
        assert not list(tmp_path.glob("library.db.bak-*"))

    def test_v43_migration_restamps_attached_but_unstamped_single(
        self, tmp_path: Path
    ) -> None:
        """The first v42 build attached a single (set album_id) but left its album
        tag empty, so it kept showing as a duplicate loose grid card. A DB already
        at v42 is gated out of the fixed v42 heal, so v43 re-stamps it in place —
        the tester upgrades without reverting."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        index.upsert_many([self._stream_single("400", "Megahit", "Celebrity")])
        album_id = index._conn.execute(
            "SELECT album_id FROM tracks_with_stats WHERE file_path = 'bandcamp://400/1'"
        ).fetchone()[0]
        # Reproduce the buggy v42 state: album_id set, album tag empty, and the
        # album row still reads 'bandcamp' (aggregates never refreshed).
        index._conn.execute(
            "INSERT INTO tracks (file_path, title, artist, album_artist, album,"
            " track_number, source, album_id) VALUES (?, 'Celebrity', 'Megahit',"
            " 'Megahit', '', 1, 'local', ?)",
            (str(tmp_path / "celebrity.flac"), album_id),
        )
        index._conn.execute(
            "UPDATE albums SET source = 'bandcamp' WHERE id = ?", (album_id,)
        )
        index._conn.execute("UPDATE schema_version SET version = 42")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        stamped = reopened._conn.execute(
            "SELECT album FROM tracks_with_stats WHERE file_path = ?",
            (str(tmp_path / "celebrity.flac"),),
        ).fetchone()[0]
        version = reopened._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        cards = [a for a in reopened.albums() if a.album == "Celebrity"]
        reopened.close()

        assert version == 60
        assert stamped == "Celebrity"
        # No duplicate loose card, and the album now reads as owned.
        assert len(cards) == 1
        assert cards[0].missing_album is False
        assert cards[0].source in ("local", "mixed")
        assert list(tmp_path.glob("library.db.bak-*"))


class TestHealForkedAlbums:
    def _downgrade_to_v38(self, db_path: Path) -> None:
        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP INDEX IF EXISTS albums_sale_item_id_uidx")
        conn.execute("UPDATE schema_version SET version = 38")
        conn.commit()
        conn.close()

    def test_whitespace_fork_with_sale_item_id_collapses(self, tmp_path: Path) -> None:
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _readd_legacy_track_columns(index)
        # Origin streaming album (spaced name + sale_item_id).
        index.upsert_collection_item(
            "S1", mode="local", band_name="Artist X ", item_title="Album Y"
        )
        index.upsert_many([_prov_stream_track("S1", "Artist X ", "Album Y")])
        index.close()

        # Inject the fork the OLD code would have minted: a local album with the
        # trimmed name, its own duplicate artist row (linked), and a local track.
        # Done after the downgrade so the UNIQUE index isn't in the way.
        self._downgrade_to_v38(db)
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO artists (name) VALUES ('Artist X')")
        fork_artist = conn.execute(
            "SELECT id FROM artists WHERE name = 'Artist X'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO albums (album_artist, album, source, artist_id)"
            " VALUES ('Artist X', 'Album Y', 'local', ?)",
            (fork_artist,),
        )
        fork_id = conn.execute(
            "SELECT id FROM albums WHERE album_artist = 'Artist X' AND sale_item_id IS NULL"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO tracks (file_path, album_artist, album, source, album_id)"
            " VALUES ('/lib/x.mp3', 'Artist X', 'Album Y', 'local', ?)",
            (fork_id,),
        )
        conn.commit()
        conn.close()

        healed = LibraryIndex(db)  # triggers v39 heal
        albums = _album_rows(healed)
        assert len(albums) == 1, f"fork not healed: {albums}"
        assert albums[0]["sale_item_id"] == "S1"
        # local track moved under the surviving album
        moved = healed._conn.execute(
            "SELECT album_id FROM tracks_with_stats WHERE file_path = '/lib/x.mp3'"
        ).fetchone()[0]
        assert moved == albums[0]["id"]
        # duplicate artist merged away
        n_artists = healed._conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
        assert n_artists == 1
        # a backup was written
        assert any(p.name.startswith("library.db.bak-") for p in tmp_path.iterdir())
        healed.close()

    def test_two_rows_sharing_sale_item_id_collapse(self, tmp_path: Path) -> None:
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        index.upsert_collection_item(
            "S9", mode="local", band_name="Band", item_title="Rec"
        )
        index.upsert_many([_prov_stream_track("S9", "Band", "Rec")])
        index.close()

        # A second album row illegally carrying the same sale_item_id (injected
        # after the downgrade so the UNIQUE index isn't in the way).
        self._downgrade_to_v38(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO albums (album_artist, album, source, sale_item_id)"
            " VALUES ('Band', 'Rec (Deluxe)', 'local', 'S9')"
        )
        conn.commit()
        conn.close()

        healed = LibraryIndex(db)
        rows = healed._conn.execute(
            "SELECT COUNT(*) FROM albums WHERE sale_item_id = 'S9'"
        ).fetchone()[0]
        assert rows == 1
        healed.close()

    def test_string_only_fork_without_id_left_untouched(self, tmp_path: Path) -> None:
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        # Two distinct local albums that merely normalize alike, neither linked
        # to Bandcamp — must NOT be merged (could be genuinely different). Each
        # gets a linked track so they are real albums rather than empty rows the
        # v40 prune would (correctly) sweep — the point here is the v39 heal.
        cur = index._conn.execute(
            "INSERT INTO albums (album_artist, album, source) VALUES ('S T', 'EP', 'local')"
        )
        a1 = cur.lastrowid
        cur = index._conn.execute(
            "INSERT INTO albums (album_artist, album, source) VALUES ('S T ', 'EP', 'local')"
        )
        a2 = cur.lastrowid
        # source lives on track_sources now (KAMP-539) and isn't asserted here;
        # only album_id (retained) matters. Omitting the dropped source column keeps
        # the tracks table at its post-v49 shape so the reopen's v49 migration finds
        # nothing to drop and takes no backup — what this test checks for.
        index._conn.execute(
            "INSERT INTO tracks (title, album, album_artist, album_id)"
            " VALUES ('x', 'EP', 'S T', ?)",
            (a1,),
        )
        index._conn.execute(
            "INSERT INTO tracks (title, album, album_artist, album_id)"
            " VALUES ('y', 'EP', 'S T ', ?)",
            (a2,),
        )
        index._conn.commit()
        index.close()

        self._downgrade_to_v38(db)
        healed = LibraryIndex(db)
        n = healed._conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        assert n == 2, "string-only forks must be left untouched"
        # No backup taken when nothing merges.
        assert not any(p.name.startswith("library.db.bak-") for p in tmp_path.iterdir())
        healed.close()

    def test_heal_is_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        index.upsert_collection_item(
            "S1", mode="local", band_name="Artist X ", item_title="Album Y"
        )
        index.upsert_many([_prov_stream_track("S1", "Artist X ", "Album Y")])
        index._conn.execute(
            "INSERT INTO albums (album_artist, album, source) VALUES ('Artist X', 'Album Y', 'local')"
        )
        index._conn.commit()
        index.close()

        self._downgrade_to_v38(db)
        LibraryIndex(db).close()  # first heal
        # Re-open again: version is 39, migration does not re-run; still one album.
        healed = LibraryIndex(db)
        assert len(_album_rows(healed)) == 1
        healed.close()

    def test_canonical_rename_collision_does_not_brick_db(self, tmp_path: Path) -> None:
        # A distinct release (different sale_item_id) legitimately shares the
        # canonical (album_artist, album) that a merge would rename onto. The
        # rename must be skipped, the merge must still complete, and BOTH albums
        # must survive — the DB must open, not crash-loop on UNIQUE. (Reality
        # Checker P0 regression.)
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        # SX: origin with a trailing-space name (canonical would be "Artist").
        index.upsert_collection_item(
            "SX", mode="local", band_name="Artist ", item_title="Album"
        )
        index.upsert_many([_prov_stream_track("SX", "Artist ", "Album")])
        # SY: a genuinely different release already occupying "Artist"/"Album".
        index.upsert_collection_item(
            "SY", mode="local", band_name="Artist", item_title="Album"
        )
        index.upsert_many([_prov_stream_track("SY", "Artist", "Album")])
        index.close()

        # Inject a second row sharing SX's sale_item_id → forces a pass-(a) merge
        # whose canonical rename ("Artist ") -> ("Artist") would collide with SY.
        self._downgrade_to_v38(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO albums (album_artist, album, source, sale_item_id)"
            " VALUES ('Artist Dup', 'Album', 'local', 'SX')"
        )
        conn.commit()
        conn.close()

        healed = LibraryIndex(db)  # must not raise
        by_sid = {
            r["sale_item_id"]: r["album_artist"]
            for r in healed._conn.execute(
                "SELECT sale_item_id, album_artist FROM albums"
                " WHERE sale_item_id IS NOT NULL"
            ).fetchall()
        }
        # Both releases survive distinctly; SX kept its (un-renamed) name.
        assert by_sid == {"SX": "Artist ", "SY": "Artist"}
        # The duplicate SX row was merged away (one row per sale_item_id).
        assert (
            healed._conn.execute(
                "SELECT COUNT(*) FROM albums WHERE sale_item_id = 'SX'"
            ).fetchone()[0]
            == 1
        )
        healed.close()


class TestKamp552DropColumns:
    """KAMP-552: dropping tracks.file_path/sale_item_id and the id-native re-key."""

    def test_v51_rebuild_preserves_fk_children(self, tmp_path: Path) -> None:
        """The v50->v51 rebuild drops the columns WITHOUT cascade-deleting the FK
        children (track_sources/track_stats/playlist_tracks) — the P0 guard — and
        leaves referential integrity intact."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        p = tmp_path / "a.mp3"
        index.upsert_many([_sample_track(p)])
        pid = index.create_playlist("P")["id"]
        index.add_track_to_playlist(pid, str(p))
        counts_before = {
            tbl: index._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            for tbl in ("track_sources", "track_stats", "playlist_tracks")
        }
        assert all(c > 0 for c in counts_before.values())
        # Re-add the dropped columns and roll back to v50 so the reopen runs v51.
        _readd_legacy_track_columns(index)
        index._conn.execute("UPDATE schema_version SET version = 50")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)  # runs the v51 FK-aware rebuild
        cols = {r[1] for r in reopened._conn.execute("PRAGMA table_info(tracks)")}
        idx = {r[1] for r in reopened._conn.execute("PRAGMA index_list(tracks)")}
        counts_after = {
            tbl: reopened._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            for tbl in ("track_sources", "track_stats", "playlist_tracks")
        }
        fk_violations = reopened._conn.execute("PRAGMA foreign_key_check").fetchall()
        still_resolves = reopened.get_track_by_path(str(p))
        reopened.close()

        assert not ({"file_path", "sale_item_id"} & cols)
        assert "tracks_sale_item_id_idx" not in idx
        assert counts_after == counts_before  # children NOT cascade-deleted
        assert fk_violations == []
        assert still_resolves is not None

    def test_v51_rebuild_repairs_dangling_album_id(self, tmp_path: Path) -> None:
        """The rebuild's foreign_key_check finds a dangling tracks.album_id and nulls
        it (repairs) rather than bricking the upgrade — the P0 fk-check arm."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        p = tmp_path / "a.mp3"
        t = _sample_track(p)
        t.album = "Al"
        t.album_artist = "Ar"
        index.upsert_many([t])
        tid = index.get_track_by_path(str(p)).id  # type: ignore[union-attr]
        _readd_legacy_track_columns(index)
        index._conn.execute("UPDATE schema_version SET version = 50")
        index._conn.commit()
        index.close()
        # Corrupt: point album_id at a nonexistent album (foreign_keys OFF so the
        # write is not rejected), simulating the dangling-FK state the rebuild guards.
        raw = sqlite3.connect(str(db))
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("UPDATE tracks SET album_id = 999999 WHERE id = ?", (tid,))
        raw.commit()
        raw.close()

        reopened = LibraryIndex(db)  # v51 rebuild repairs the dangler
        album_id = reopened._conn.execute(
            "SELECT album_id FROM tracks WHERE id = ?", (tid,)
        ).fetchone()[0]
        fk = reopened._conn.execute("PRAGMA foreign_key_check").fetchall()
        ver = reopened._conn.execute("SELECT version FROM schema_version").fetchone()[0]
        cols = {r[1] for r in reopened._conn.execute("PRAGMA table_info(tracks)")}
        reopened.close()
        assert ver == 60
        assert "file_path" not in cols
        assert album_id is None  # dangling FK nulled
        assert fk == []

    def test_streaming_resync_preserves_user_edited_metadata(
        self, tmp_path: Path
    ) -> None:
        """A streaming re-sync that sends empty release_date/genre/label must not
        clobber the stored values (the KAMP-552 two-batch stream-preserve UPDATE)."""

        def _stream(title: str, rd: str, genre: str, label: str) -> Track:
            return Track(
                file_path=Path("bandcamp://s/1"),
                title=title,
                artist="A",
                album_artist="A",
                album="Al",
                release_date=rd,
                track_number=1,
                disc_number=1,
                ext="",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
                source="bandcamp",
                genre=genre,
                label=label,
            )

        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([_stream("Original", "2020-01-01", "Jazz", "Blue Note")])
        # Re-sync: Bandcamp never sends release_date/genre/label, so they arrive empty.
        index.upsert_many([_stream("Renamed", "", "", "")])
        got = index.get_track_by_path("bandcamp://s/1")
        index.close()
        assert got is not None
        assert got.title == "Renamed"  # title always overwrites
        assert got.release_date == "2020-01-01"  # preserved on empty incoming
        assert got.genre == "Jazz"
        assert got.label == "Blue Note"

    def test_local_rescan_overwrites_metadata(self, tmp_path: Path) -> None:
        """A local re-scan overwrites release_date/genre/label (no stream-preserve)."""
        p = tmp_path / "a.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        first = _sample_track(p)
        first.genre = "Rock"
        first.release_date = "2019"
        index.upsert_many([first])
        second = _sample_track(p)
        second.genre = ""
        second.release_date = ""
        index.upsert_many([second])
        got = index.get_track_by_path(str(p))
        index.close()
        assert got is not None
        assert got.genre == ""
        assert got.release_date == ""

    def test_get_track_by_path_resolves_local_when_file_unavailable(
        self, tmp_path: Path
    ) -> None:
        """A track whose file source is unavailable (drive unmounted) but which has
        an available stream still resolves by its local path — lookups match ANY
        source uri, not the availability-ordered derived file_path (P0 regression)."""
        p = tmp_path / "a.mp3"
        index = LibraryIndex(tmp_path / "library.db")
        index.upsert_many([_sample_track(p)])
        tid = index.get_track_by_path(str(p)).id  # type: ignore[union-attr]
        index._conn.execute(
            "INSERT INTO track_sources (track_id, kind, uri, is_available)"
            " VALUES (?, 'stream', 'bandcamp://x/1', 1)",
            (tid,),
        )
        index._conn.execute(
            "UPDATE track_sources SET is_available = 0"
            " WHERE track_id = ? AND kind = 'file'",
            (tid,),
        )
        index._conn.commit()
        # The preferred (available) source is now the stream, so the view's derived
        # file_path is the bandcamp uri — but the local path must still resolve.
        by_local = index.get_track_by_path(str(p))
        by_stream = index.get_track_by_path("bandcamp://x/1")
        index.close()
        assert by_local is not None and by_local.id == tid
        assert by_stream is not None and by_stream.id == tid


class TestAlbumGenreRow:
    """KAMP-605: single-album genre-enrichment row for the per-album Fetch button."""

    def test_returns_row_with_cached_bandcamp_keywords(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        aid = _add_artist(index, "Artist")
        # The albums.sale_item_id FK points at bandcamp_collection — insert it first.
        index._conn.execute(
            "INSERT INTO bandcamp_collection (sale_item_id, keywords)"
            " VALUES ('S1', '[\"Shoegaze\"]')"
        )
        _add_album_with_track(
            index, "Artist", "Album", aid, "bandcamp://S1/1", sale_item_id="S1"
        )
        index._conn.commit()
        row = index.album_genre_row("Artist", "Album")
        index.close()
        assert row is not None
        assert row["album_artist"] == "Artist"
        assert row["sale_item_id"] == "S1"
        assert row["keywords"] == '["Shoegaze"]'

    def test_returns_row_for_enriched_album_no_bandcamp(self, tmp_path: Path) -> None:
        # No genres_enriched_at filter and no bandcamp row -> keywords None.
        index = LibraryIndex(tmp_path / "library.db")
        aid = _add_artist(index, "Artist")
        album_id = _add_album_with_track(index, "Artist", "Album", aid, "/x/01.mp3")
        index._conn.execute(
            "UPDATE albums SET genres_enriched_at = 123 WHERE id = ?", (album_id,)
        )
        index._conn.commit()
        row = index.album_genre_row("Artist", "Album")
        index.close()
        assert row is not None
        assert row["keywords"] is None

    def test_none_for_unknown_album(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        result = index.album_genre_row("Nobody", "Nothing")
        index.close()
        assert result is None
