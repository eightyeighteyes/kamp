"""SQLite-backed library index and filesystem scanner."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import stat
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import keyring
import keyring.errors
import mutagen.flac
import mutagen.id3 as id3
import mutagen.mp4
import mutagen.oggvorbis

logger = logging.getLogger(__name__)

_AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".flac", ".ogg"})

_SCHEMA_VERSION = 12

_DDL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path        TEXT    NOT NULL UNIQUE,
    title            TEXT    NOT NULL DEFAULT '',
    artist           TEXT    NOT NULL DEFAULT '',
    album_artist     TEXT    NOT NULL DEFAULT '',
    album            TEXT    NOT NULL DEFAULT '',
    year             TEXT    NOT NULL DEFAULT '',
    track_number     INTEGER NOT NULL DEFAULT 0,
    disc_number      INTEGER NOT NULL DEFAULT 1,
    ext              TEXT    NOT NULL DEFAULT '',
    embedded_art     INTEGER NOT NULL DEFAULT 0,
    mb_release_id    TEXT    NOT NULL DEFAULT '',
    mb_recording_id  TEXT    NOT NULL DEFAULT '',
    date_added       REAL,    -- file birthtime/ctime at first scan (Unix timestamp)
    last_played      REAL,    -- Unix timestamp of last natural EOF; NULL until played
    favorite         INTEGER NOT NULL DEFAULT 0,
    play_count       INTEGER NOT NULL DEFAULT 0,
    file_mtime       REAL     -- st_mtime at last scan; NULL until v6 migration backfill
);

-- FTS5 virtual table for full-text search across track metadata.
-- Indexed fields: title, artist, album_artist, album.
-- rowid maps to tracks.id so we can JOIN back for full track data.
CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
    title, artist, album_artist, album,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS player_state (
    id         INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table
    track_path TEXT    NOT NULL,
    position   REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS queue_state (
    id      INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table
    tracks  TEXT    NOT NULL,                    -- JSON array of file paths in playback order
    pos     INTEGER NOT NULL DEFAULT -1,
    shuffle INTEGER NOT NULL DEFAULT 0,
    repeat  INTEGER NOT NULL DEFAULT 0
);

-- Append-only audit trail for all library.write mutations issued by extensions.
-- track_mbid is the MusicBrainz recording ID of the affected track.
-- old_value / new_value are JSON-encoded field dicts so arbitrary mutation
-- payloads can be captured without schema changes.
CREATE TABLE IF NOT EXISTS extension_audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    extension_id TEXT    NOT NULL,
    track_mbid   TEXT    NOT NULL DEFAULT '',
    operation    TEXT    NOT NULL,
    old_value    TEXT    NOT NULL DEFAULT '',
    new_value    TEXT    NOT NULL DEFAULT '',
    timestamp    REAL    NOT NULL
);

-- Per-service session storage (Bandcamp, Last.fm, future integrations).
-- session_json is NULL when credentials are stored in the OS keychain (keyring).
CREATE TABLE IF NOT EXISTS sessions (
    service      TEXT NOT NULL PRIMARY KEY,
    session_json TEXT,
    updated_at   REAL NOT NULL
);

-- Application settings (replaces config.toml; see TASK-132).
-- All 13 active config keys are stored here as TEXT; type coercion happens in Python.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
);

-- Enforce append-only invariant at the DB level so no code path can silently
-- erase the audit trail.
CREATE TRIGGER IF NOT EXISTS _audit_log_no_delete
BEFORE DELETE ON extension_audit_log
BEGIN
    SELECT RAISE(ABORT, 'extension_audit_log is append-only: DELETE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS _audit_log_no_update
BEFORE UPDATE ON extension_audit_log
BEGIN
    SELECT RAISE(ABORT, 'extension_audit_log is append-only: UPDATE is not permitted');
END;
"""

# Characters that have special meaning in FTS5 MATCH expressions.
_FTS_SPECIAL = re.compile(r'["*^()]')


def _get_mtime(path: Path) -> float | None:
    """Return st_mtime for *path*, or None on OS error."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _get_date_added(path: Path) -> float | None:
    """Return the best available creation timestamp for *path*.

    Prefers st_birthtime (macOS/BSD) over st_ctime (Linux inode-change time),
    which is a closer approximation to "when the file first appeared".
    Returns None on any OS error (e.g. file missing during concurrent scan).
    """
    try:
        st = path.stat()
        birthtime: float | None = getattr(st, "st_birthtime", None)
        return birthtime if birthtime is not None else st.st_ctime
    except OSError:
        return None


# Mapping from public sort key → SQL ORDER BY clause used in albums().
# Keys are validated against this dict so no user input reaches the query.
_SORT_CLAUSES: dict[str, str] = {
    "album_artist": "album_artist COLLATE NOCASE, album COLLATE NOCASE",
    "album": "album COLLATE NOCASE, album_artist COLLATE NOCASE",
    # Newest first (largest timestamp); NULLs sort last in DESC.
    # These reference the sort_date_added / sort_last_played columns produced
    # by the UNION ALL query in albums() (aliases for MIN/MAX in the grouped
    # part and the raw value in the single-track missing-album part).
    "date_added": "sort_date_added DESC, album_artist COLLATE NOCASE",
    "last_played": "sort_last_played DESC, album_artist COLLATE NOCASE",
}


def _make_fts_query(q: str) -> str:
    """Convert a plain user query into an FTS5 MATCH expression.

    Each whitespace-delimited token is stripped of FTS5 syntax characters and
    given a trailing ``*`` for prefix matching, then joined with implicit AND.
    Returns an empty string when the query contains no usable tokens.
    """
    tokens = [_FTS_SPECIAL.sub("", t) for t in q.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return ""
    return " ".join(f"{t}*" for t in tokens)


@dataclass
class Track:
    file_path: Path
    title: str
    artist: str
    album_artist: str
    album: str
    year: str
    track_number: int
    disc_number: int
    ext: str
    embedded_art: bool
    mb_release_id: str
    mb_recording_id: str
    id: int = field(default=0, compare=False)
    date_added: float | None = field(default=None, compare=False)
    last_played: float | None = field(default=None, compare=False)
    favorite: bool = field(default=False, compare=False)
    play_count: int = field(default=0, compare=False)
    file_mtime: float | None = field(default=None, compare=False)


@dataclass
class AlbumInfo:
    album_artist: str
    album: str
    year: str
    track_count: int
    has_art: bool = False
    # True when the track has no album tag; album field holds the track title
    # as a display name, and file_path uniquely identifies this virtual album.
    missing_album: bool = False
    file_path: str = ""
    # MAX(file_mtime) across the album's tracks — used as a cache-busting key
    # in image URLs so the browser only re-fetches art when files change.
    art_version: float | None = None

    # Allow dict-style access so callers can use a["album_artist"] etc.
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class ScanResult:
    added: int
    removed: int
    unchanged: int
    updated: int = 0
    new_tracks: list[Track] = field(default_factory=list)


class LibraryIndex:
    """Persistent SQLite index of all tracks in the music library."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._local = threading.local()
        # Track every connection opened across threads so close() can shut them
        # all down cleanly at server shutdown.
        self._all_conns: list[sqlite3.Connection] = []
        self._all_conns_lock = threading.Lock()
        # Correct permissions on existing installs where the file was created
        # with the default umask (644).
        if db_path.exists():
            db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self._migrate()

    def _make_conn(self) -> sqlite3.Connection:
        """Open a new SQLite connection configured for use in this index."""
        # check_same_thread=False: each connection is only *used* by the thread
        # that created it (enforced by threading.local), but close() is called
        # from the main thread at shutdown and needs to reach all connections.
        # Apply a restrictive umask so SQLite creates library.db (and its WAL/SHM
        # sidecar files) with 600 permissions rather than the default 644.
        old_umask = os.umask(0o077)
        try:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        finally:
            os.umask(old_umask)
        conn.row_factory = sqlite3.Row
        # WAL allows concurrent reads from other thread-connections while a
        # write (e.g. library scan) is in progress.
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        """Return the SQLite connection for the current thread.

        Python's sqlite3 wrapper shares internal cursor state on a single
        connection object, causing InterfaceError when multiple threads call
        execute() concurrently. One connection per thread eliminates this.
        """
        if not hasattr(self._local, "conn"):
            conn = self._make_conn()
            self._local.conn = conn
            with self._all_conns_lock:
                self._all_conns.append(conn)
        return self._local.conn  # type: ignore[no-any-return]

    def _migrate(self) -> None:
        """Create schema and run any pending version migrations."""
        self._conn.executescript(_DDL)
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            # Brand-new database — stamp current version and populate FTS.
            self._rebuild_fts()
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,)
            )
            self._conn.commit()
            return

        version = row["version"]
        if version < 2:
            # v1 → v2: FTS table added; backfill from existing tracks.
            self._rebuild_fts()
            self._conn.execute("UPDATE schema_version SET version = 2")
            self._conn.commit()
            version = 2

        if version < 3:
            # v2 → v3: date_added and last_played columns added.
            self._conn.execute("ALTER TABLE tracks ADD COLUMN date_added REAL")
            self._conn.execute("ALTER TABLE tracks ADD COLUMN last_played REAL")
            # Backfill date_added from file system for tracks that still exist.
            rows = self._conn.execute("SELECT id, file_path FROM tracks").fetchall()
            for r in rows:
                ts = _get_date_added(Path(r["file_path"]))
                if ts is not None:
                    self._conn.execute(
                        "UPDATE tracks SET date_added = ? WHERE id = ?",
                        (ts, r["id"]),
                    )
            self._conn.execute("UPDATE schema_version SET version = 3")
            self._conn.commit()
            version = 3

        if version < 4:
            # v3 → v4: favorite column added.
            self._conn.execute(
                "ALTER TABLE tracks ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.execute("UPDATE schema_version SET version = 4")
            self._conn.commit()
            version = 4

        if version < 5:
            # v4 → v5: play_count column added.
            self._conn.execute(
                "ALTER TABLE tracks ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.execute("UPDATE schema_version SET version = 5")
            self._conn.commit()
            version = 5

        if version < 6:
            # v5 → v6: file_mtime column added.
            # Intentionally left NULL for all existing rows so that the next
            # scan treats every track as "changed" and re-reads its tags.
            # This ensures tag edits made before the upgrade (e.g. adding cover
            # art) are picked up on the first scan after migration.
            self._conn.execute("ALTER TABLE tracks ADD COLUMN file_mtime REAL")
            self._conn.execute("UPDATE schema_version SET version = 6")
            self._conn.commit()
            version = 6

        if version < 7:
            # v6 → v7: extension_audit_log table and append-only triggers added.
            # The table and triggers are created by _DDL via executescript at the
            # top of _migrate, so we only need to bump the version here.
            self._conn.execute("UPDATE schema_version SET version = 7")
            self._conn.commit()
            version = 7

        if version < 8:
            # v7 → v8: sessions table added for secure per-service auth storage.
            # The table is created by _DDL via executescript at the top of
            # _migrate, so we only need to bump the version here.
            self._conn.execute("UPDATE schema_version SET version = 8")
            self._conn.commit()

        if version < 9:
            # v8 → v9: fix blank tags caused by the buggy FLAC/OGG tag reader
            # (which looked up uppercase keys in a lowercase dict).  Nulling
            # file_mtime for all FLAC/OGG tracks forces the scanner to re-read
            # their tags on the next startup scan.
            self._conn.execute(
                "UPDATE tracks SET file_mtime = NULL WHERE ext IN ('flac', 'ogg')"
            )
            self._conn.execute("UPDATE schema_version SET version = 9")
            self._conn.commit()

        if version < 10:
            # v9 → v10: tag readers now fall back album_artist → artist when the
            # album-artist tag is absent.  Null file_mtime for tracks that have
            # an artist but no album_artist so the scanner re-reads them and
            # derives album_artist from artist on the next scan.
            # Tracks with both fields empty are left alone — re-reading them
            # would produce the same empty result.
            self._conn.execute(
                "UPDATE tracks SET file_mtime = NULL"
                " WHERE album_artist = '' AND artist != ''"
            )
            self._conn.execute("UPDATE schema_version SET version = 10")
            self._conn.commit()

        if version < 11:
            # v10 → v11: settings table added for DB-backed config (replaces config.toml).
            # The table is created by _DDL via executescript at the top of _migrate.
            self._conn.execute("UPDATE schema_version SET version = 11")
            self._conn.commit()
            version = 11

        if version < 12:
            # v11 → v12: migrate session credentials from plaintext DB column to the
            # OS keychain.  Recreate the sessions table so session_json is nullable
            # (credentials stored in keychain leave the column NULL).  Then attempt
            # to move each existing row into keychain and null it out.  On platforms
            # without a keyring backend the rows are left intact as fallback storage.
            self._conn.executescript("""
                CREATE TABLE sessions_new (
                    service      TEXT NOT NULL PRIMARY KEY,
                    session_json TEXT,
                    updated_at   REAL NOT NULL
                );
                INSERT INTO sessions_new SELECT * FROM sessions;
                DROP TABLE sessions;
                ALTER TABLE sessions_new RENAME TO sessions;
            """)
            rows = self._conn.execute(
                "SELECT service, session_json FROM sessions"
                " WHERE session_json IS NOT NULL"
            ).fetchall()
            for row in rows:
                try:
                    keyring.set_password("kamp", row["service"], row["session_json"])
                    self._conn.execute(
                        "UPDATE sessions SET session_json = NULL WHERE service = ?",
                        (row["service"],),
                    )
                except keyring.errors.NoKeyringError:
                    # No keyring on this platform; leave remaining rows in DB.
                    break
            self._conn.execute("UPDATE schema_version SET version = 12")
            self._conn.commit()

    def _rebuild_fts(self) -> None:
        """Rebuild the FTS index from the current contents of the tracks table."""
        self._conn.execute("DELETE FROM tracks_fts")
        self._conn.execute(
            "INSERT INTO tracks_fts(rowid, title, artist, album_artist, album) "
            "SELECT id, title, artist, album_artist, album FROM tracks"
        )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def get_session(self, service: str) -> dict[str, Any] | None:
        """Return the stored session data for *service*, or None if absent.

        Reads from the OS keychain when available; falls back to the DB column
        on platforms without a keyring backend.  Retries up to 3 times with
        exponential backoff when the keychain is transiently locked (e.g. brief
        race at wake/login before the login-keychain unlocks).
        """
        logger.debug("get_session: reading keychain for service=%s", service)
        _MAX_RETRIES = 3
        for attempt in range(_MAX_RETRIES):
            try:
                raw = keyring.get_password("kamp", service)
                if raw is not None:
                    return json.loads(raw)  # type: ignore[no-any-return]
                break  # key not present — no point retrying
            except keyring.errors.NoKeyringError:
                # No keyring backend — fall through to DB
                break
            except keyring.errors.KeyringLocked as exc:
                delay = 0.5 * (2**attempt)
                logger.debug(
                    "get_session: keychain locked for service=%s (attempt %d/%d, retry in %.1fs): %s",
                    service,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    exc,
                )
                _time.sleep(delay)
            except keyring.errors.KeyringError as exc:
                logger.warning(
                    "get_session: keychain read failed for service=%s (%s: %s)",
                    service,
                    type(exc).__name__,
                    exc,
                )
                break
        else:
            logger.warning(
                "get_session: keychain still locked after %d retries for service=%s;"
                " credentials may appear missing until the keychain unlocks",
                _MAX_RETRIES,
                service,
            )
        row = self._conn.execute(
            "SELECT session_json FROM sessions WHERE service = ?", (service,)
        ).fetchone()
        if row is None or row["session_json"] is None:
            return None
        return dict(json.loads(row["session_json"]))  # type: ignore[no-any-return]

    def set_session(self, service: str, data: dict[str, Any]) -> None:
        """Persist session data for *service*, replacing any existing entry.

        Writes to the OS keychain when available, leaving session_json NULL in
        the DB so the credential is absent from backups.  Falls back to the DB
        column on platforms without a keyring backend.
        """
        payload = json.dumps(data)
        session_json: str | None = payload
        try:
            keyring.set_password("kamp", service, payload)
            session_json = None  # stored in keychain; keep DB row metadata-only
        except keyring.errors.NoKeyringError:
            pass
        except keyring.errors.KeyringError as exc:
            logger.warning(
                "set_session: keychain write failed for service=%s (%s: %s);"
                " credential stored in DB fallback",
                service,
                type(exc).__name__,
                exc,
            )
        self._conn.execute(
            """
            INSERT INTO sessions (service, session_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(service) DO UPDATE SET
                session_json = excluded.session_json,
                updated_at   = excluded.updated_at
            """,
            (service, session_json, _time.time()),
        )
        self._conn.commit()

    def clear_session(self, service: str) -> None:
        """Remove the session entry for *service* from keychain and DB."""
        try:
            keyring.delete_password("kamp", service)
        except (keyring.errors.NoKeyringError, keyring.errors.PasswordDeleteError):
            pass
        except keyring.errors.KeyringError as exc:
            logger.warning(
                "clear_session: keychain delete failed for service=%s (%s: %s)",
                service,
                type(exc).__name__,
                exc,
            )
        self._conn.execute("DELETE FROM sessions WHERE service = ?", (service,))
        self._conn.commit()
        # Truncate the WAL so deleted credential data (cookies, session keys) is
        # not recoverable from the WAL file after disconnect.
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ------------------------------------------------------------------
    # Settings (application configuration)
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        """Return the stored value for *key*, or None if absent."""
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        """Persist a config key/value, replacing any existing row."""
        self._conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self._conn.commit()

    def get_all_settings(self) -> dict[str, str]:
        """Return all stored config key/value pairs."""
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def upsert_track(self, track: Track) -> None:
        """Insert or replace a single track record keyed on file_path."""
        self.upsert_many([track])

    def upsert_many(self, tracks: list[Track]) -> None:
        """Insert or replace multiple tracks in a single transaction."""
        if not tracks:
            return
        self._conn.executemany(
            """
            INSERT INTO tracks
                (file_path, title, artist, album_artist, album, year,
                 track_number, disc_number, ext, embedded_art,
                 mb_release_id, mb_recording_id, date_added, file_mtime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                title           = excluded.title,
                artist          = excluded.artist,
                album_artist    = excluded.album_artist,
                album           = excluded.album,
                year            = excluded.year,
                track_number    = excluded.track_number,
                disc_number     = excluded.disc_number,
                ext             = excluded.ext,
                embedded_art    = excluded.embedded_art,
                mb_release_id   = excluded.mb_release_id,
                mb_recording_id = excluded.mb_recording_id,
                file_mtime      = excluded.file_mtime
                -- date_added intentionally omitted: preserve original scan date on re-scan
                -- last_played intentionally omitted: managed exclusively by record_played()
            """,
            [_track_to_params(t) for t in tracks],
        )
        # Rebuild the FTS index so new/updated tracks are immediately searchable.
        self._rebuild_fts()
        self._conn.commit()

    def remove_track(self, file_path: Path) -> None:
        """Remove the track with the given file path from the index."""
        self._conn.execute("DELETE FROM tracks WHERE file_path = ?", (str(file_path),))
        # Sync FTS — rebuilding is simpler than per-row deletes with FTS5 content tables.
        self._rebuild_fts()
        self._conn.commit()

    def all_tracks(self) -> list[Track]:
        """Return all indexed tracks in insertion order."""
        rows = self._conn.execute("SELECT * FROM tracks").fetchall()
        return [_row_to_track(r) for r in rows]

    def record_played(self, file_path: Path) -> None:
        """Record the current time as last_played for the track at *file_path*.

        Called when a track reaches natural end-of-file so that Last Played
        sort order reflects actual listening history.
        """
        import time

        self._conn.execute(
            "UPDATE tracks SET last_played = ?, play_count = play_count + 1 WHERE file_path = ?",
            (time.time(), str(file_path)),
        )
        self._conn.commit()

    def set_favorite(self, file_path: Path, favorite: bool) -> None:
        """Set or clear the favorite flag for the track at *file_path*."""
        self._conn.execute(
            "UPDATE tracks SET favorite = ? WHERE file_path = ?",
            (int(favorite), str(file_path)),
        )
        self._conn.commit()

    def albums(self, sort: str = "album_artist") -> list[AlbumInfo]:
        """Return one AlbumInfo per (album_artist, album) pair.

        Tracks that have no album tag are each returned as their own entry
        with ``missing_album=True``; the ``album`` field is set to the track
        title (for display) and ``file_path`` uniquely identifies the entry.

        *sort* must be one of: ``album_artist`` (default), ``album``,
        ``date_added``, ``last_played``.  Unknown values fall back to
        ``album_artist`` so callers never have to guard against bad input.
        """
        order_by = _SORT_CLAUSES.get(sort, _SORT_CLAUSES["album_artist"])
        rows = self._conn.execute(f"""
            SELECT album_artist, album, year, track_count, has_art,
                   missing_album, file_path, art_version
            FROM (
                SELECT album_artist, album, year, COUNT(*) AS track_count,
                       MAX(embedded_art) AS has_art,
                       0 AS missing_album, '' AS file_path,
                       MIN(date_added) AS sort_date_added,
                       MAX(last_played) AS sort_last_played,
                       MAX(file_mtime) AS art_version
                FROM tracks
                WHERE album != ''
                GROUP BY album_artist, album
                UNION ALL
                SELECT album_artist, title AS album, year, 1 AS track_count,
                       embedded_art AS has_art,
                       1 AS missing_album, file_path,
                       date_added AS sort_date_added,
                       last_played AS sort_last_played,
                       file_mtime AS art_version
                FROM tracks
                WHERE album = ''
            )
            ORDER BY {order_by}
            """).fetchall()  # noqa: S608 — order_by is from a whitelist, not user input
        return [
            AlbumInfo(
                album_artist=r["album_artist"],
                album=r["album"],
                year=r["year"],
                track_count=r["track_count"],
                has_art=bool(r["has_art"]),
                missing_album=bool(r["missing_album"]),
                file_path=r["file_path"],
                art_version=r["art_version"],
            )
            for r in rows
        ]

    def artists(self) -> list[str]:
        """Return a sorted, deduplicated list of album_artist values."""
        rows = self._conn.execute(
            "SELECT DISTINCT album_artist FROM tracks ORDER BY album_artist COLLATE NOCASE"
        ).fetchall()
        return [r["album_artist"] for r in rows]

    def tracks_for_album(self, album_artist: str, album: str) -> list[Track]:
        """Return tracks for a given album sorted by disc then track number."""
        rows = self._conn.execute(
            """
            SELECT * FROM tracks
            WHERE album_artist = ? AND album = ?
            ORDER BY disc_number, track_number
            """,
            (album_artist, album),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    def indexed_paths(self) -> set[Path]:
        """Return the set of all file paths currently in the index."""
        rows = self._conn.execute("SELECT file_path FROM tracks").fetchall()
        return {Path(r["file_path"]) for r in rows}

    def indexed_paths_with_mtime(self) -> dict[Path, float | None]:
        """Return a mapping of indexed file paths to their stored file_mtime."""
        rows = self._conn.execute("SELECT file_path, file_mtime FROM tracks").fetchall()
        return {Path(r["file_path"]): r["file_mtime"] for r in rows}

    def get_track_by_path(self, path: Path) -> "Track | None":
        """Return the track for *path*, or None if not indexed."""
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE file_path = ?", (str(path),)
        ).fetchone()
        return _row_to_track(row) if row else None

    def search(self, query: str) -> list[Track]:
        """Full-text search across title, artist, album_artist, and album.

        Returns tracks ranked by relevance (best match first).
        Returns an empty list when *query* is blank.
        """
        fts_expr = _make_fts_query(query)
        if not fts_expr:
            return []
        rows = self._conn.execute(
            """
            SELECT t.*
            FROM tracks_fts f
            JOIN tracks t ON f.rowid = t.id
            WHERE tracks_fts MATCH ?
            ORDER BY f.rank
            """,
            (fts_expr,),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    def save_player_state(self, track_path: Path, position: float) -> None:
        """Persist the current track path and playback position."""
        self._conn.execute(
            """
            INSERT INTO player_state (id, track_path, position) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_path = excluded.track_path,
                position   = excluded.position
            """,
            (str(track_path), position),
        )
        self._conn.commit()

    def clear_player_state(self) -> None:
        """Remove the persisted player state (e.g. after the queue is exhausted)."""
        self._conn.execute("DELETE FROM player_state WHERE id = 1")
        self._conn.commit()

    def load_player_state(self) -> "tuple[Path, float] | None":
        """Return (track_path, position) from the last session, or None."""
        row = self._conn.execute(
            "SELECT track_path, position FROM player_state WHERE id = 1"
        ).fetchone()
        return (Path(row["track_path"]), row["position"]) if row else None

    def save_queue_state(
        self, tracks: list[Path], pos: int, shuffle: bool, repeat: bool
    ) -> None:
        """Persist the full queue in playback order alongside pos and flags."""
        import json

        payload = json.dumps([str(p) for p in tracks])
        self._conn.execute(
            """
            INSERT INTO queue_state (id, tracks, pos, shuffle, repeat)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                tracks  = excluded.tracks,
                pos     = excluded.pos,
                shuffle = excluded.shuffle,
                repeat  = excluded.repeat
            """,
            (payload, pos, int(shuffle), int(repeat)),
        )
        self._conn.commit()

    def load_queue_state(self) -> "tuple[list[Path], int, bool, bool] | None":
        """Return (tracks_in_playback_order, pos, shuffle, repeat) or None."""
        import json

        row = self._conn.execute(
            "SELECT tracks, pos, shuffle, repeat FROM queue_state WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        paths = [Path(p) for p in json.loads(row["tracks"])]
        return paths, row["pos"], bool(row["shuffle"]), bool(row["repeat"])

    def clear_queue_state(self) -> None:
        """Remove the persisted queue state (e.g. after the queue is exhausted)."""
        self._conn.execute("DELETE FROM queue_state WHERE id = 1")
        self._conn.commit()

    def close(self) -> None:
        with self._all_conns_lock:
            for conn in self._all_conns:
                conn.close()
            self._all_conns.clear()

    # ------------------------------------------------------------------
    # Extension library writes + audit log
    # ------------------------------------------------------------------

    def apply_metadata_update(
        self,
        extension_id: str,
        mbid: str,
        fields: dict[str, str | int],
    ) -> None:
        """Log and apply a metadata update mutation from an extension.

        Reads the current field values before writing so the audit log
        captures a meaningful old_value. Only fields in
        _WRITABLE_TRACK_FIELDS are applied; others are silently discarded
        so extensions cannot touch internal columns (e.g. embedded_art,
        play_count).

        The audit entry is written in the same transaction as the UPDATE
        so the log is always consistent with the applied state (AC #2).
        """
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE mb_recording_id = ?", (mbid,)
        ).fetchone()
        old_fields: dict[str, Any] = {}
        if row:
            for k in fields:
                if k in row.keys():
                    old_fields[k] = row[k]

        # Write audit entry before the mutation (AC #2).
        self._conn.execute(
            """
            INSERT INTO extension_audit_log
                (extension_id, track_mbid, operation, old_value, new_value, timestamp)
            VALUES (?, ?, 'update_metadata', ?, ?, ?)
            """,
            (
                extension_id,
                mbid,
                json.dumps(old_fields),
                json.dumps(dict(fields)),
                _time.time(),
            ),
        )

        # Validate against the allowlist — load-bearing; do not remove.
        # Column names are interpolated directly into SQL; any name outside
        # this set must raise, not be silently skipped.
        unknown = set(fields) - _WRITABLE_TRACK_FIELDS
        if unknown:
            raise ValueError(f"Unexpected column names in metadata update: {unknown}")

        safe = {k: v for k, v in fields.items() if k in _WRITABLE_TRACK_FIELDS}
        if row and safe:
            set_clause = ", ".join(f"{k} = ?" for k in safe)
            params: list[Any] = [*safe.values(), mbid]
            self._conn.execute(
                f"UPDATE tracks SET {set_clause} WHERE mb_recording_id = ?",
                params,
            )
        self._conn.commit()

    def apply_set_artwork(
        self,
        extension_id: str,
        mbid: str,
        mime_type: str,
    ) -> None:
        """Log and apply a set_artwork mutation from an extension.

        Records the old embedded_art flag in the audit log, then marks
        the track as having embedded art. The mime_type is stored in
        new_value for informational purposes.

        The audit entry is written in the same transaction as the UPDATE
        (AC #2).
        """
        row = self._conn.execute(
            "SELECT embedded_art FROM tracks WHERE mb_recording_id = ?", (mbid,)
        ).fetchone()
        old_embedded_art: bool | None = bool(row["embedded_art"]) if row else None

        self._conn.execute(
            """
            INSERT INTO extension_audit_log
                (extension_id, track_mbid, operation, old_value, new_value, timestamp)
            VALUES (?, ?, 'set_artwork', ?, ?, ?)
            """,
            (
                extension_id,
                mbid,
                json.dumps({"embedded_art": old_embedded_art}),
                json.dumps({"mime_type": mime_type}),
                _time.time(),
            ),
        )
        if row:
            self._conn.execute(
                "UPDATE tracks SET embedded_art = 1 WHERE mb_recording_id = ?",
                (mbid,),
            )
        self._conn.commit()

    def rollback_extension(self, extension_id: str) -> int:
        """Revert all library writes performed by *extension_id*.

        Reads the audit log for the given extension and reverses each
        entry in reverse-chronological order (newest write undone first)
        so the final state matches the pre-extension library state.

        Returns the number of mutations reverted.
        """
        rows = self._conn.execute(
            """
            SELECT track_mbid, operation, old_value
            FROM extension_audit_log
            WHERE extension_id = ?
            ORDER BY timestamp DESC, id DESC
            """,
            (extension_id,),
        ).fetchall()

        reverted = 0
        for row in rows:
            op = row["operation"]
            mbid = row["track_mbid"]
            old: dict[str, Any] = json.loads(row["old_value"])

            if op == "update_metadata":
                # Audit log entries are written by apply_metadata_update, which
                # already validated keys against _WRITABLE_TRACK_FIELDS. Raise
                # here too so a corrupt/tampered log entry cannot inject column names.
                unknown = set(old) - _WRITABLE_TRACK_FIELDS
                if unknown:
                    raise ValueError(f"Unexpected column names in audit log: {unknown}")
                safe = {k: v for k, v in old.items() if k in _WRITABLE_TRACK_FIELDS}
                if safe:
                    set_clause = ", ".join(f"{k} = ?" for k in safe)
                    params = [*safe.values(), mbid]
                    self._conn.execute(
                        f"UPDATE tracks SET {set_clause} WHERE mb_recording_id = ?",
                        params,
                    )
            elif op == "set_artwork":
                embedded_art = old.get("embedded_art")
                if embedded_art is not None:
                    self._conn.execute(
                        "UPDATE tracks SET embedded_art = ? WHERE mb_recording_id = ?",
                        (int(embedded_art), mbid),
                    )
            reverted += 1

        self._conn.commit()
        return reverted

    def has_been_processed_by(self, extension_id: str, mb_recording_id: str) -> bool:
        """Return True if *extension_id* has a prior audit log entry for *mb_recording_id*.

        Used by the invocation policy to enforce the single-invocation guarantee:
        the host checks this before offering a track to an extension so that
        re-scan events do not trigger redundant mutations.
        """
        row = self._conn.execute(
            "SELECT 1 FROM extension_audit_log WHERE extension_id = ? AND track_mbid = ? LIMIT 1",
            (extension_id, mb_recording_id),
        ).fetchone()
        return row is not None

    def mark_processed_by(self, extension_id: str, mb_recording_id: str) -> None:
        """Record that *extension_id* has processed *mb_recording_id*.

        Writes a sentinel audit log entry so that has_been_processed_by()
        returns True and the post-scan invoker skips this track.  Used by
        the pipeline to mark built-in extensions (MusicBrainz tagger,
        Cover Art Archive) that run in-process during ingest — their results
        would otherwise be redundantly re-fetched on every library re-scan.
        """
        self._conn.execute(
            """
            INSERT INTO extension_audit_log
                (extension_id, track_mbid, operation, old_value, new_value, timestamp)
            VALUES (?, ?, 'pipeline', '{}', '{}', ?)
            """,
            (extension_id, mb_recording_id, _time.time()),
        )
        self._conn.commit()

    def audit_log_for(self, extension_id: str) -> list[dict[str, Any]]:
        """Return all audit log rows for *extension_id* in ascending order.

        Primarily useful for inspection and testing.
        """
        rows = self._conn.execute(
            """
            SELECT id, extension_id, track_mbid, operation,
                   old_value, new_value, timestamp
            FROM extension_audit_log
            WHERE extension_id = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (extension_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# Track fields that extensions are permitted to write via apply_metadata_update.
# Excludes internal columns (embedded_art, play_count, last_played, etc.) so
# extensions cannot corrupt playback state or override quality signals.
_WRITABLE_TRACK_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "artist",
        "album_artist",
        "album",
        "year",
        "track_number",
        "disc_number",
        "mb_release_id",
    }
)


def _track_to_params(
    t: Track,
) -> tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    int,
    str,
    int,
    str,
    str,
    float | None,
    float | None,
]:
    return (
        str(t.file_path),
        t.title,
        t.artist,
        t.album_artist,
        t.album,
        t.year,
        t.track_number,
        t.disc_number,
        t.ext,
        int(t.embedded_art),
        t.mb_release_id,
        t.mb_recording_id,
        t.date_added,
        t.file_mtime,
    )


def _row_to_track(row: sqlite3.Row) -> Track:
    return Track(
        id=row["id"],
        file_path=Path(row["file_path"]),
        title=row["title"],
        artist=row["artist"],
        album_artist=row["album_artist"],
        album=row["album"],
        year=row["year"],
        track_number=row["track_number"],
        disc_number=row["disc_number"],
        ext=row["ext"],
        embedded_art=bool(row["embedded_art"]),
        mb_release_id=row["mb_release_id"],
        mb_recording_id=row["mb_recording_id"],
        date_added=row["date_added"],
        last_played=row["last_played"],
        favorite=bool(row["favorite"]),
        play_count=row["play_count"],
        file_mtime=row["file_mtime"],
    )


# ---------------------------------------------------------------------------
# Artwork extraction
# ---------------------------------------------------------------------------


def extract_art(path: Path) -> tuple[bytes, str] | None:
    """Extract the first embedded cover image from an audio file.

    Returns (data, mime_type) or None if no art is found or the file
    cannot be read.  Supports MP3, M4A, FLAC, and OGG.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            tags = id3.ID3(str(path))
            for key in tags:
                if key.startswith("APIC"):
                    frame = tags[key]
                    return bytes(frame.data), str(frame.mime)
        elif suffix == ".m4a":  # pragma: no branch
            audio = mutagen.mp4.MP4(str(path))
            covr = (audio.tags or {}).get("covr")  # type: ignore[call-overload]
            if covr:
                img = covr[0]
                mime = (
                    "image/jpeg"
                    if img.imageformat == mutagen.mp4.MP4Cover.FORMAT_JPEG
                    else "image/png"
                )
                return bytes(img), mime
        elif suffix == ".flac":
            audio = mutagen.flac.FLAC(str(path))
            if audio.pictures:
                pic = audio.pictures[0]
                return bytes(pic.data), str(pic.mime)
        elif suffix == ".ogg":
            import base64

            from mutagen.flac import Picture

            audio = mutagen.oggvorbis.OggVorbis(str(path))
            blocks = (audio.tags or {}).get("metadata_block_picture", [])  # type: ignore[call-overload]
            if blocks:
                pic = Picture(base64.b64decode(blocks[0]))
                return bytes(pic.data), str(pic.mime)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Tag readers — one per format
# ---------------------------------------------------------------------------


def _parse_num(value: str) -> int:
    """Parse "5" or "5/12" into 5; return 0 on failure."""
    try:
        return int(value.split("/")[0])
    except (ValueError, IndexError):
        return 0


def _read_mp3_tags(path: Path) -> Track:
    try:
        tags = id3.ID3(str(path))
    except Exception:
        tags = id3.ID3()

    def _str(frame_key: str) -> str:
        frame = tags.get(frame_key)
        return str(frame) if frame else ""

    artist = _str("TPE1")
    return Track(
        file_path=path,
        ext="mp3",
        artist=artist,
        album_artist=_str("TPE2") or artist,  # fall back to artist when TPE2 absent
        album=_str("TALB"),
        year=_str("TDRC"),
        title=_str("TIT2"),
        track_number=_parse_num(_str("TRCK")),
        disc_number=_parse_num(_str("TPOS")) or 1,
        embedded_art=any(k.startswith("APIC") for k in tags),
        mb_release_id=_str("TXXX:MusicBrainz Release Id"),
        mb_recording_id=_str("TXXX:MusicBrainz Track Id"),
    )


def _read_m4a_tags(path: Path) -> Track:
    try:
        audio = mutagen.mp4.MP4(str(path))
        tags = audio.tags or {}
    except Exception:
        tags = {}

    def _s(key: str) -> str:
        vals = tags.get(key)
        if not vals:
            return ""
        v = vals[0]
        # MP4FreeForm (MBID fields) needs decoding
        return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)

    trkn = tags.get("trkn")
    track_number = trkn[0][0] if trkn else 0
    disk = tags.get("disk")
    disc_number = disk[0][0] if disk else 1

    artist = _s("\xa9ART")
    return Track(
        file_path=path,
        ext="m4a",
        artist=artist,
        album_artist=_s("aART") or artist,  # fall back to artist when aART absent
        album=_s("\xa9alb"),
        year=_s("\xa9day"),
        title=_s("\xa9nam"),
        track_number=track_number,
        disc_number=disc_number or 1,
        embedded_art=bool(tags.get("covr")),
        mb_release_id=_s("----:com.apple.iTunes:MusicBrainz Release Id"),
        mb_recording_id=_s("----:com.apple.iTunes:MusicBrainz Track Id"),
    )


def _read_vorbis_tags(path: Path, *, is_flac: bool) -> Track:
    """Read tags from a FLAC or OGG Vorbis file."""
    try:
        if is_flac:
            audio = mutagen.flac.FLAC(str(path))
        else:
            audio = mutagen.oggvorbis.OggVorbis(str(path))
        # Vorbis comment keys are case-insensitive; real mutagen VCFLACDict/
        # VCommentDict yields lowercase keys from dict(), so normalise to
        # uppercase so that _s("ARTIST") etc. always find a match.
        tags: dict[str, list[str]] = {
            k.upper(): (v if isinstance(v, list) else [v])
            for k, v in dict(audio.tags or {}).items()
        }
        pictures = getattr(audio, "pictures", [])
    except Exception:
        tags = {}
        pictures = []

    def _s(key: str) -> str:
        vals = tags.get(key)
        return vals[0] if vals else ""

    artist = _s("ARTIST")
    return Track(
        file_path=path,
        ext="flac" if is_flac else "ogg",
        artist=artist,
        album_artist=_s("ALBUMARTIST")
        or artist,  # fall back to artist when ALBUMARTIST absent
        album=_s("ALBUM"),
        year=_s("DATE"),
        title=_s("TITLE"),
        track_number=_parse_num(_s("TRACKNUMBER")),
        disc_number=_parse_num(_s("DISCNUMBER")) or 1,
        embedded_art=bool(pictures),
        mb_release_id=_s("MUSICBRAINZ_ALBUMID"),
        mb_recording_id=_s("MUSICBRAINZ_TRACKID"),
    )


def _read_tags(path: Path) -> Track | None:
    """Dispatch to the appropriate tag reader; return None on unrecognised format.

    Populates ``date_added`` from the file's birthtime/ctime so the library
    scanner can persist when each track first appeared on disk.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            track = _read_mp3_tags(path)
        elif suffix == ".m4a":
            track = _read_m4a_tags(path)
        elif suffix == ".flac":
            track = _read_vorbis_tags(path, is_flac=True)
        elif suffix == ".ogg":
            track = _read_vorbis_tags(path, is_flac=False)
        else:
            return None
        track.date_added = _get_date_added(path)
        track.file_mtime = _get_mtime(path)
        return track
    except Exception:
        logger.warning("Could not read tags from %s", path, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class LibraryScanner:
    """Walk a library directory and keep the LibraryIndex in sync."""

    def __init__(self, index: LibraryIndex) -> None:
        self._index = index

    def scan(
        self,
        library_path: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ScanResult:
        """Scan *library_path* recursively and update the index.

        New files are read and added. Files whose mtime has changed since the
        last scan are re-read so tag edits (e.g. adding cover art) are picked
        up automatically. Index entries whose files no longer exist on disk are
        removed.

        *on_progress*, if provided, is called after each processed file's tags
        are read with (current, total) where total = number of files to index
        (new + updated).
        """
        if not library_path.exists():
            return ScanResult(added=0, removed=0, unchanged=0)

        on_disk: set[Path] = {
            p
            for p in library_path.rglob("*")
            if p.is_file() and p.suffix.lower() in _AUDIO_SUFFIXES
        }
        indexed = self._index.indexed_paths_with_mtime()
        in_index = set(indexed.keys())

        to_add = on_disk - in_index

        # Re-read any existing file whose mtime differs from what was stored.
        # A None stored mtime (pre-v6 rows) is treated as changed so they get
        # backfilled on the first scan after the migration.
        to_update: set[Path] = set()
        for path in on_disk & in_index:
            current_mtime = _get_mtime(path)
            if current_mtime is None:
                continue  # can't stat — leave it alone
            if indexed[path] != current_mtime:
                to_update.add(path)

        to_process = to_add | to_update
        total = len(to_process)
        tracks_to_upsert: list[Track] = []
        for current, path in enumerate(to_process, start=1):
            track = _read_tags(path)
            if track is not None:
                tracks_to_upsert.append(track)
            else:
                logger.warning("Skipped unreadable file: %s", path)
            if on_progress is not None:
                on_progress(current, total)
        self._index.upsert_many(tracks_to_upsert)

        newly_added = [t for t in tracks_to_upsert if t.file_path in to_add]
        added = len(newly_added)
        updated = len([t for t in tracks_to_upsert if t.file_path in to_update])

        removed = 0
        for path in in_index - on_disk:
            self._index.remove_track(path)
            removed += 1

        unchanged = len(on_disk & in_index) - len(to_update)

        return ScanResult(
            added=added,
            removed=removed,
            unchanged=unchanged,
            updated=updated,
            new_tracks=newly_added,
        )
