"""SQLite-backed library index and filesystem scanner."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import stat
import sys
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import keyring
import keyring.errors

if sys.platform == "darwin":
    try:
        from . import macos_keychain as _mac_kc
    except Exception:
        _mac_kc = None  # type: ignore[assignment]
else:
    _mac_kc = None  # type: ignore[assignment]

if sys.platform == "win32":
    try:
        from . import win_credential as _win_cred
    except Exception:
        _win_cred = None  # type: ignore[assignment]
else:
    _win_cred = None  # type: ignore[assignment]

import mutagen.flac
import mutagen.id3 as id3
import mutagen.mp4
import mutagen.oggvorbis

logger = logging.getLogger(__name__)


def _maybe_protect(plaintext: str) -> str:
    """DPAPI-wrap *plaintext* on Windows, return as-is elsewhere.

    On Windows the SQLite ``sessions`` row sits in
    ``%APPDATA%\\kamp\\library.db`` in a form readable by anyone with
    file access; DPAPI ties the encryption key to the current Windows
    user account so a copy of the DB cannot be decrypted off-machine
    (KAMP-280 AC #3).  If DPAPI itself fails we still write the row —
    a failed login is worse than a non-encrypted credential — and log
    a warning.
    """
    if _win_cred is None:
        return plaintext
    try:
        return _win_cred.protect_str(plaintext)
    except Exception as exc:
        logger.warning(
            "DPAPI protect failed (%s: %s); storing plaintext fallback",
            type(exc).__name__,
            exc,
        )
        return plaintext


def _maybe_unprotect(text: str) -> str:
    """Strip DPAPI wrapping from *text* if it carries the DPAPI prefix.

    Returns the input unchanged when the value is plaintext (legacy
    rows that pre-date the DPAPI rollout) or when DPAPI is unavailable
    on the current platform.
    """
    if _win_cred is None:
        return text
    try:
        unwrapped = _win_cred.unprotect_str(text)
    except Exception as exc:
        logger.warning(
            "DPAPI unprotect failed (%s: %s); treating as plaintext",
            type(exc).__name__,
            exc,
        )
        return text
    return unwrapped if unwrapped is not None else text


_AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".flac", ".ogg"})

_SCHEMA_VERSION = 20

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
    file_mtime       REAL,    -- st_mtime at last scan; NULL until v6 migration backfill
    genre                TEXT    NOT NULL DEFAULT '',
    label                TEXT    NOT NULL DEFAULT '',
    source               TEXT    NOT NULL DEFAULT 'local',
    stream_url           TEXT,
    stream_url_expires_at REAL
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
    id         INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table
    tracks     TEXT    NOT NULL,                    -- JSON array of file paths in original load order
    order_json TEXT    NOT NULL DEFAULT '',          -- JSON array of indices (playback permutation)
    pos        INTEGER NOT NULL DEFAULT -1,
    shuffle    INTEGER NOT NULL DEFAULT 0,
    repeat     INTEGER NOT NULL DEFAULT 0
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

-- Albums marked as favorites by the user (KAMP-293).
-- Stored independently of track-level favorites so album and track favorites
-- can be toggled without affecting each other.
CREATE TABLE IF NOT EXISTS album_favorites (
    album_artist TEXT NOT NULL,
    album        TEXT NOT NULL,
    PRIMARY KEY (album_artist, album)
);

-- Application settings (replaces config.toml; see TASK-132).
-- All 13 active config keys are stored here as TEXT; type coercion happens in Python.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
);

-- Deferred tag/rename operations queued while the target track is playing (KAMP-309).
-- UNIQUE(track_id) enforces at-most-one pending op per track; a second edit while
-- the first is still queued replaces the row via INSERT OR REPLACE so only the
-- newest user intent survives to drain.
CREATE TABLE IF NOT EXISTS deferred_ops (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type      TEXT    NOT NULL,          -- 'track_retag' | 'album_retag'
    track_id     INTEGER NOT NULL UNIQUE,
    payload_json TEXT    NOT NULL,          -- pre-computed paths + new tag values
    created_at   REAL    NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
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

-- Bandcamp collection ownership state (replaces bandcamp_state.json from KAMP-381).
-- mode: 'local' = downloaded/owned locally, 'remote' = stream-only,
--       'preorder' = purchased but not yet available, 'ignored' = excluded from sync.
-- tralbum_id and album_url are populated by the streaming URL resolver (KAMP-382);
-- migration rows start with '' and are backfilled on next sync.
CREATE TABLE IF NOT EXISTS bandcamp_collection (
    sale_item_id   TEXT NOT NULL PRIMARY KEY,
    item_type      TEXT NOT NULL DEFAULT 'p',
    band_name      TEXT NOT NULL DEFAULT '',
    item_title     TEXT NOT NULL DEFAULT '',
    tralbum_id     TEXT NOT NULL DEFAULT '',
    album_url      TEXT NOT NULL DEFAULT '',
    mode           TEXT NOT NULL DEFAULT 'local',
    synced_at      REAL,
    added_at       REAL NOT NULL DEFAULT 0
);
"""

# Characters that have special meaning in FTS5 MATCH expressions.
_FTS_SPECIAL = re.compile(r'["*^()]')


def _get_mtime(path: Path) -> float | None:
    """Return st_mtime for *path*, or None on OS error."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


_COVER_FILENAMES = ("cover.jpg", "cover.png")


def _has_cover_file(directory: Path) -> bool:
    """Return True if *directory* contains a cover.jpg or cover.png."""
    return any((directory / name).is_file() for name in _COVER_FILENAMES)


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
    # Highest average plays per track first; NULLs (unplayed) sort last via COALESCE.
    "most_played": "sort_play_count_avg DESC, album_artist COLLATE NOCASE",
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
    genre: str = field(default="")
    label: str = field(default="")
    id: int = field(default=0, compare=False)
    date_added: float | None = field(default=None, compare=False)
    last_played: float | None = field(default=None, compare=False)
    favorite: bool = field(default=False, compare=False)
    play_count: int = field(default=0, compare=False)
    file_mtime: float | None = field(default=None, compare=False)
    source: str = field(default="local", compare=False)
    stream_url: str | None = field(default=None, compare=False)
    stream_url_expires_at: float | None = field(default=None, compare=False)

    @property
    def is_remote(self) -> bool:
        return self.source != "local"

    @property
    def playback_uri(self) -> str:
        """URL or file-path string to pass to mpv for playback."""
        return self.stream_url or str(self.file_path)


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
    # MIN(date_added) across the album's tracks — exposed so callers can filter
    # by recency without a separate query.
    added_at: float | None = None
    # MAX(last_played) across the album's tracks — exposed for the Last Played module.
    last_played_at: float | None = None
    # SUM(play_count) / COUNT(*) across tracks — exposed for the Top Albums module.
    play_count_avg: float = 0.0
    # True when the user has favorited this album (KAMP-293).
    favorite: bool = False
    # True when any track in this album is individually favorited (KAMP-294).
    has_favorite_track: bool = False
    # 'local' when all tracks are local, the source value when all are the same
    # remote source, or 'mixed' when both local and remote tracks are present.
    source: str = "local"
    # True when any track in this album has source != 'local'.
    has_remote_tracks: bool = False
    # True when this local album is also in bandcamp_collection with mode='local'
    # (i.e. the user owns it on Bandcamp but has it downloaded locally).
    in_bandcamp_collection: bool = False

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


@dataclass
class DeferredOp:
    """A tag/rename operation queued while the target track was playing (KAMP-309)."""

    id: int
    op_type: str  # 'track_retag' | 'album_retag'
    track_id: int
    payload_json: str
    created_at: float
    attempts: int
    last_error: str | None


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
            conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False, timeout=30
            )
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
        # In Python 3.12+, sqlite3 no longer implicitly commits an open
        # transaction before DDL statements (ALTER TABLE, CREATE TABLE, etc.).
        # The SELECT above opens an implicit deferred read transaction; commit
        # it now so subsequent ALTER TABLE calls can acquire an exclusive write
        # lock without hitting "database is locked".
        self._conn.commit()
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
                except Exception as exc:
                    # On Windows, large blobs (e.g. the Bandcamp session)
                    # exceed CRED_MAX_CREDENTIAL_BLOB_SIZE and CredWrite raises
                    # OSError outside the keyring exception hierarchy.  Leave
                    # the row in the DB column so the v13 migration can wrap
                    # it with DPAPI.  See KAMP-280 / KAMP-282.
                    logger.warning(
                        "v11->v12: keyring write failed for service=%s (%s: %s);"
                        " leaving in DB fallback",
                        row["service"],
                        type(exc).__name__,
                        exc,
                    )
                    continue
            self._conn.execute("UPDATE schema_version SET version = 12")
            self._conn.commit()
            version = 12

        if version < 13:
            # v12 -> v13: encrypt any plaintext credential rows with DPAPI on
            # Windows.  No-op on other platforms (the keyring backends there
            # already encrypt at rest).  See KAMP-280.
            if _win_cred is not None:
                rows = self._conn.execute(
                    "SELECT service, session_json FROM sessions"
                    " WHERE session_json IS NOT NULL"
                ).fetchall()
                for row in rows:
                    if _win_cred.is_dpapi_blob(row["session_json"]):
                        continue  # already wrapped (paranoia)
                    wrapped = _maybe_protect(row["session_json"])
                    if wrapped == row["session_json"]:
                        continue  # protect failed, already logged
                    self._conn.execute(
                        "UPDATE sessions SET session_json = ? WHERE service = ?",
                        (wrapped, row["service"]),
                    )
            self._conn.execute("UPDATE schema_version SET version = 13")
            self._conn.commit()
            version = 13

        if version < 14:
            # v13 → v14: album_favorites table added (KAMP-293).
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS album_favorites (
                    album_artist TEXT NOT NULL,
                    album        TEXT NOT NULL,
                    PRIMARY KEY (album_artist, album)
                )
            """)
            self._conn.execute("UPDATE schema_version SET version = 14")
            self._conn.commit()
            version = 14

        if version < 15:
            # v14 → v15: index on (album_artist, album) for album-level fan-out (KAMP-308).
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS tracks_album_idx ON tracks(album_artist, album)"
            )
            self._conn.execute("UPDATE schema_version SET version = 15")
            self._conn.commit()
            version = 15

        if version < 16:
            # v15 → v16: deferred_ops table for playing-track rename deferral (KAMP-309).
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS deferred_ops (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    op_type      TEXT    NOT NULL,
                    track_id     INTEGER NOT NULL UNIQUE,
                    payload_json TEXT    NOT NULL,
                    created_at   REAL    NOT NULL,
                    attempts     INTEGER NOT NULL DEFAULT 0,
                    last_error   TEXT
                )
            """)
            self._conn.execute("UPDATE schema_version SET version = 16")
            self._conn.commit()
            version = 16

        if version < 17:
            # v16 → v17: genre and label columns added (KAMP-303).
            # Guard each ALTER with a PRAGMA check: new databases created
            # from the current _DDL already have these columns, so the
            # ALTER would fail with "duplicate column name".
            existing = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "genre" not in existing:
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN genre TEXT NOT NULL DEFAULT ''"
                )
            if "label" not in existing:
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN label TEXT NOT NULL DEFAULT ''"
                )
            # Null file_mtime so all existing tracks are rescanned and pick
            # up genre/label on the next library scan.
            self._conn.execute("UPDATE tracks SET file_mtime = NULL")
            self._conn.execute("UPDATE schema_version SET version = 17")
            self._conn.commit()
            version = 17

        if version < 18:
            # v17 → v18: add order_json to queue_state so the original load
            # order is preserved separately from the shuffled playback order
            # (KAMP-353 bug fix).  Guard with PRAGMA so fresh DBs (which
            # already have the column via _DDL) don't fail.
            existing = {
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(queue_state)"
                ).fetchall()
            }
            if "order_json" not in existing:
                self._conn.execute(
                    "ALTER TABLE queue_state ADD COLUMN order_json TEXT NOT NULL DEFAULT ''"
                )
            self._conn.execute("UPDATE schema_version SET version = 18")
            self._conn.commit()
            version = 18

        if version < 19:
            # v18 → v19: replace bandcamp_state.json with the bandcamp_collection
            # table (KAMP-381).  The table is created by _DDL above.  Import any
            # existing state file entries as mode='local' rows, then delete the
            # file so future startups skip this branch entirely.
            # The state file lives alongside library.db in the same directory.
            state_file = self._db_path.parent / "bandcamp_state.json"
            if state_file.exists():
                try:
                    raw: dict[str, float] = json.loads(state_file.read_text())
                except Exception:
                    raw = {}
                for sid, ts in raw.items():
                    self._conn.execute(
                        "INSERT OR IGNORE INTO bandcamp_collection"
                        " (sale_item_id, mode, synced_at, added_at)"
                        " VALUES (?, 'local', ?, ?)",
                        (sid, ts, ts),
                    )
                try:
                    state_file.unlink()
                except OSError:
                    pass
            self._conn.execute("UPDATE schema_version SET version = 19")
            self._conn.commit()

        if version < 20:
            # v19 → v20: add source, stream_url, stream_url_expires_at to tracks
            # so local and remote (Bandcamp stream-only) tracks coexist in one table.
            # Guard each ALTER with a PRAGMA check: new DBs already have these columns.
            existing = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "source" not in existing:
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN source TEXT NOT NULL DEFAULT 'local'"
                )
            if "stream_url" not in existing:
                self._conn.execute("ALTER TABLE tracks ADD COLUMN stream_url TEXT")
            if "stream_url_expires_at" not in existing:
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN stream_url_expires_at REAL"
                )
            self._conn.execute("UPDATE schema_version SET version = 20")
            self._conn.commit()
            version = 20

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

        On macOS, reads from the Data Protection Keychain (stable across app
        updates) with a one-time migration from the Login Keychain for existing
        credentials.  Falls back to the Login Keychain in unsigned dev builds.
        On other platforms, reads from the ``keyring`` backend with exponential
        backoff on transient lock errors.  Falls through to the DB column when
        no keychain is available.
        """
        logger.debug("get_session: reading keychain for service=%s", service)

        # --- macOS: Data Protection Keychain (or Login Keychain fallback) ----
        if _mac_kc is not None:
            _MAX_RETRIES = 3
            _dpc_responded = False  # True when DPC answered without error
            for attempt in range(_MAX_RETRIES):
                try:
                    raw = _mac_kc.get_password("kamp", service)
                    _dpc_responded = True
                    if raw is not None:
                        logger.debug(
                            "get_session: keychain hit for service=%s", service
                        )
                        return json.loads(raw)  # type: ignore[no-any-return]
                    break  # absent — check for migration, then fall through to DB
                except keyring.errors.KeyringLocked as exc:
                    delay = 0.5 * (2**attempt)
                    logger.debug(
                        "get_session: keychain locked for service=%s"
                        " (attempt %d/%d, retry in %.1fs): %s",
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
                logger.debug(
                    "get_session: no entry in keychain or DB for service=%s", service
                )
                return None
            logger.debug("get_session: DB fallback hit for service=%s", service)
            return dict(json.loads(_maybe_unprotect(row["session_json"])))  # type: ignore[no-any-return]

        # --- keyring path (non-macOS, non-Windows-DPAPI) -----
        # On Windows we skip the OS keyring entirely: WinVaultKeyring caps
        # credentials at 2560 bytes which the Bandcamp blob exceeds, so the
        # call always fails for that service.  DPAPI in the DB fallback
        # provides equivalent per-user encryption without the size limit.
        # See KAMP-280 / KAMP-282.
        if _win_cred is None:
            _MAX_RETRIES = 3
            for attempt in range(_MAX_RETRIES):
                try:
                    raw = keyring.get_password("kamp", service)
                    if raw is not None:
                        logger.debug(
                            "get_session: keychain hit for service=%s", service
                        )
                        return json.loads(raw)  # type: ignore[no-any-return]
                    logger.debug(
                        "get_session: keychain returned no entry for service=%s",
                        service,
                    )
                    break  # key not present — no point retrying
                except keyring.errors.NoKeyringError:
                    break
                except keyring.errors.KeyringLocked as exc:
                    delay = 0.5 * (2**attempt)
                    logger.debug(
                        "get_session: keychain locked for service=%s"
                        " (attempt %d/%d, retry in %.1fs): %s",
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
                except Exception as exc:
                    # Backend may raise OSError/RuntimeError outside the keyring
                    # exception hierarchy (e.g. ctypes failures from
                    # WinVaultKeyring).  Fall through to the DB row instead of
                    # letting it propagate.
                    logger.warning(
                        "get_session: keychain read raised unexpected %s"
                        " for service=%s: %s",
                        type(exc).__name__,
                        service,
                        exc,
                    )
                    break
            else:
                logger.warning(
                    "get_session: keychain still locked after %d retries for"
                    " service=%s; credentials may appear missing until the"
                    " keychain unlocks",
                    _MAX_RETRIES,
                    service,
                )

        # --- DB fallback -------------------------------------
        row = self._conn.execute(
            "SELECT session_json FROM sessions WHERE service = ?", (service,)
        ).fetchone()
        if row is None or row["session_json"] is None:
            logger.debug(
                "get_session: no entry in keychain or DB for service=%s", service
            )
            return None
        logger.debug("get_session: DB fallback hit for service=%s", service)
        return dict(json.loads(_maybe_unprotect(row["session_json"])))  # type: ignore[no-any-return]

    def set_session(self, service: str, data: dict[str, Any]) -> None:
        """Persist session data for *service*, replacing any existing entry.

        On macOS, writes to the Data Protection Keychain so that items remain
        accessible across app updates without prompts.  Falls back to the DB
        column when the keychain write fails.  On other platforms, writes via
        ``keyring``.
        """
        payload = json.dumps(data)
        session_json: str | None = payload

        if _mac_kc is not None:
            try:
                _mac_kc.set_password("kamp", service, payload)
                verified = _mac_kc.get_password("kamp", service)
                if verified == payload:
                    session_json = None  # stored in keychain; keep DB row metadata-only
                    logger.debug(
                        "set_session: keychain write verified for service=%s", service
                    )
                else:
                    logger.warning(
                        "set_session: keychain write for service=%s did not verify"
                        " (read-back returned %s); falling back to DB",
                        service,
                        "wrong value" if verified is not None else "None",
                    )
            except keyring.errors.KeyringError as exc:
                logger.warning(
                    "set_session: keychain write failed for service=%s (%s: %s);"
                    " credential stored in DB fallback",
                    service,
                    type(exc).__name__,
                    exc,
                )
        elif _win_cred is None:
            # Windows skips this branch — DPAPI-wrapped DB row is the
            # storage path there (see KAMP-280).
            try:
                keyring.set_password("kamp", service, payload)
                verified = keyring.get_password("kamp", service)
                if verified == payload:
                    session_json = None
                    logger.debug(
                        "set_session: keychain write verified for service=%s", service
                    )
                else:
                    logger.warning(
                        "set_session: keychain write for service=%s did not verify"
                        " (read-back returned %s); falling back to DB",
                        service,
                        "wrong value" if verified is not None else "None",
                    )
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
            except Exception as exc:
                # Backend may raise OSError/RuntimeError outside the keyring
                # exception hierarchy (e.g. ctypes failures from WinVaultKeyring).
                # Without this branch the exception bubbles out of set_session
                # and turns the bandcamp login-complete handler into a 422.
                # See KAMP-282.
                logger.warning(
                    "set_session: keychain write raised unexpected %s for service=%s"
                    " (%s); credential stored in DB fallback",
                    type(exc).__name__,
                    service,
                    exc,
                )

        # On Windows, wrap the DB fallback with DPAPI so the SQLite row is
        # not readable as plaintext (KAMP-280 AC #3).  No-op when the
        # credential lives in the OS keychain (session_json is None).
        if session_json is not None:
            session_json = _maybe_protect(session_json)
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
        if _mac_kc is not None:
            try:
                _mac_kc.delete_password("kamp", service)
            except keyring.errors.KeyringError as exc:
                logger.warning(
                    "clear_session: keychain delete failed for service=%s (%s: %s)",
                    service,
                    type(exc).__name__,
                    exc,
                )
        elif _win_cred is None:
            # Windows skips OS keyring entirely (see KAMP-280); the DELETE
            # below removes the DPAPI-wrapped row.
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
            except Exception as exc:
                logger.warning(
                    "clear_session: keychain delete raised unexpected %s for"
                    " service=%s: %s",
                    type(exc).__name__,
                    service,
                    exc,
                )
        self._conn.execute("DELETE FROM sessions WHERE service = ?", (service,))
        self._conn.commit()
        # Truncate the WAL so deleted credential data (cookies, session keys) is
        # not recoverable from the WAL file after disconnect.
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ------------------------------------------------------------------
    # Bandcamp collection (KAMP-381)
    # ------------------------------------------------------------------

    def get_collection_state(self) -> dict[str, str]:
        """Return {sale_item_id: mode} for every row in bandcamp_collection."""
        rows = self._conn.execute(
            "SELECT sale_item_id, mode FROM bandcamp_collection"
        ).fetchall()
        return {r["sale_item_id"]: r["mode"] for r in rows}

    def upsert_collection_item(
        self,
        sale_item_id: str,
        *,
        mode: str,
        item_type: str = "p",
        band_name: str = "",
        item_title: str = "",
        tralbum_id: str = "",
        album_url: str = "",
        synced_at: float | None = None,
        added_at: float | None = None,
    ) -> None:
        """Insert or update a single entry in bandcamp_collection."""
        now = _time.time()
        self._conn.execute(
            """
            INSERT INTO bandcamp_collection
                (sale_item_id, item_type, band_name, item_title,
                 tralbum_id, album_url, mode, synced_at, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sale_item_id) DO UPDATE SET
                item_type  = excluded.item_type,
                band_name  = excluded.band_name,
                item_title = excluded.item_title,
                tralbum_id = excluded.tralbum_id,
                album_url  = excluded.album_url,
                mode       = excluded.mode,
                synced_at  = COALESCE(excluded.synced_at, synced_at)
            """,
            (
                sale_item_id,
                item_type,
                band_name,
                item_title,
                tralbum_id,
                album_url,
                mode,
                synced_at,
                added_at if added_at is not None else now,
            ),
        )
        self._conn.commit()

    def get_remote_collection(self) -> list[dict[str, Any]]:
        """Return all bandcamp_collection rows with mode='remote'."""
        rows = self._conn.execute(
            "SELECT * FROM bandcamp_collection WHERE mode = 'remote'"
        ).fetchall()
        return [dict(r) for r in rows]

    def reset_collection_sync_state(self) -> None:
        """Set synced_at = NULL for all rows so the next sync re-downloads everything."""
        self._conn.execute("UPDATE bandcamp_collection SET synced_at = NULL")
        self._conn.commit()

    def clear_bandcamp_collection(self) -> None:
        """Delete all rows from bandcamp_collection (called on logout)."""
        self._conn.execute("DELETE FROM bandcamp_collection")
        self._conn.commit()

    def get_collection_item(self, sale_item_id: str) -> dict[str, Any] | None:
        """Return the bandcamp_collection row for *sale_item_id*, or None if absent."""
        row = self._conn.execute(
            "SELECT * FROM bandcamp_collection WHERE sale_item_id = ?",
            (sale_item_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_stream_url(
        self, file_path_uri: str, stream_url: str, expires_at: float
    ) -> None:
        """Persist a refreshed CDN stream URL and its expiry timestamp for a remote track."""
        self._conn.execute(
            "UPDATE tracks SET stream_url = ?, stream_url_expires_at = ? WHERE file_path = ?",
            (stream_url, expires_at, file_path_uri),
        )
        self._conn.commit()

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
                 mb_release_id, mb_recording_id, date_added, file_mtime,
                 genre, label, source, stream_url, stream_url_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                title                 = excluded.title,
                artist                = excluded.artist,
                album_artist          = excluded.album_artist,
                album                 = excluded.album,
                year                  = excluded.year,
                track_number          = excluded.track_number,
                disc_number           = excluded.disc_number,
                ext                   = excluded.ext,
                embedded_art          = excluded.embedded_art,
                mb_release_id         = excluded.mb_release_id,
                mb_recording_id       = excluded.mb_recording_id,
                file_mtime            = excluded.file_mtime,
                genre                 = excluded.genre,
                label                 = excluded.label,
                source                = excluded.source,
                stream_url            = excluded.stream_url,
                stream_url_expires_at = excluded.stream_url_expires_at
                -- date_added intentionally omitted: preserve original scan date on re-scan
                -- last_played intentionally omitted: managed exclusively by record_played()
            """,
            [_track_to_params(t) for t in tracks],
        )
        # Rebuild the FTS index so new/updated tracks are immediately searchable.
        self._rebuild_fts()
        self._conn.commit()

    def move_track(
        self,
        old_path: Path,
        new_path: Path,
        new_title: str,
        new_mtime: float,
    ) -> None:
        """Update file_path and title for a track, preserving id and all stats.

        Called by the tag-edit endpoint after a file is physically moved on
        disk.  The row's primary stats (date_added, play_count, last_played,
        favorite, mb IDs) are intentionally unchanged.
        """
        self._conn.execute(
            "UPDATE tracks SET file_path = ?, title = ?, file_mtime = ? WHERE file_path = ?",
            (str(new_path), new_title, new_mtime, str(old_path)),
        )
        self._rebuild_fts()
        self._conn.commit()

    def rename_album_track(
        self,
        old_path: Path,
        new_path: Path,
        new_album: str,
        new_album_artist: str,
        new_mtime: float,
    ) -> None:
        """Update file_path, album, and album_artist for a track after an album-level rename.

        Like move_track but also rewrites the album and album_artist columns.
        Primary stats (date_added, play_count, last_played, favorite) are unchanged.
        Called once per track during PATCH /api/v1/albums/tags fan-out.
        """
        self._conn.execute(
            """UPDATE tracks
               SET file_path = ?, album = ?, album_artist = ?, file_mtime = ?
               WHERE file_path = ?""",
            (str(new_path), new_album, new_album_artist, new_mtime, str(old_path)),
        )
        self._rebuild_fts()
        self._conn.commit()

    def rename_album_tracks_bulk(
        self,
        path_pairs: list[tuple[Path, Path]],
        new_album: str,
        new_album_artist: str,
        new_mtime: float,
        old_album_artist: str | None = None,
    ) -> None:
        """Update all tracks in the album in one transaction with a single FTS rebuild.

        Used after a directory-level rename where all files move atomically and only
        the DB rows need to be re-pointed.  Stats columns are untouched.

        old_album_artist, when provided, also updates the per-track artist column
        for any row where artist = old_album_artist (i.e. single-artist albums
        where TPE1 == TPE2).
        """
        for old_path, new_path in path_pairs:
            self._conn.execute(
                """UPDATE tracks
                   SET file_path = ?,
                       album = ?,
                       album_artist = ?,
                       artist = CASE WHEN ? IS NOT NULL AND artist = ? THEN ? ELSE artist END,
                       file_mtime = ?
                   WHERE file_path = ?""",
                (
                    str(new_path),
                    new_album,
                    new_album_artist,
                    old_album_artist,
                    old_album_artist,
                    new_album_artist,
                    new_mtime,
                    str(old_path),
                ),
            )
        self._rebuild_fts()
        self._conn.commit()

    # ------------------------------------------------------------------
    # Deferred ops (KAMP-309)
    # ------------------------------------------------------------------

    def queue_deferred_op(self, op_type: str, track_id: int, payload_json: str) -> int:
        """Insert or replace a pending deferred operation for *track_id*.

        UNIQUE(track_id) means a second edit while the first is still pending
        replaces the earlier row so only the newest user intent survives to drain.
        Returns the row id of the inserted/replaced row.
        """
        import time as _t

        cur = self._conn.execute(
            "INSERT OR REPLACE INTO deferred_ops"
            " (op_type, track_id, payload_json, created_at) VALUES (?,?,?,?)",
            (op_type, track_id, payload_json, _t.time()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def pending_deferred_ops_for_track(self, track_id: int) -> list[DeferredOp]:
        """Return pending ops for *track_id* in insertion order."""
        rows = self._conn.execute(
            "SELECT * FROM deferred_ops WHERE track_id=? ORDER BY id ASC",
            (track_id,),
        ).fetchall()
        return [_row_to_deferred_op(r) for r in rows]

    def all_pending_deferred_ops(self) -> list[DeferredOp]:
        """Return all pending ops ordered by creation time.

        ORDER BY created_at ASC is mandatory — it ensures chained edits for the
        same track execute in the user's intended sequence and gives deterministic
        behaviour for testing.
        """
        rows = self._conn.execute(
            "SELECT * FROM deferred_ops ORDER BY created_at ASC, id ASC"
        ).fetchall()
        return [_row_to_deferred_op(r) for r in rows]

    def complete_deferred_op(self, op_id: int) -> None:
        """Delete a deferred op row after successful execution."""
        self._conn.execute("DELETE FROM deferred_ops WHERE id=?", (op_id,))
        self._conn.commit()

    def fail_deferred_op(self, op_id: int, error: str) -> None:
        """Bump attempt count and record *error* without deleting the row."""
        self._conn.execute(
            "UPDATE deferred_ops SET attempts=attempts+1, last_error=? WHERE id=?",
            (error, op_id),
        )
        self._conn.commit()

    def rewrite_deferred_op_old_path(
        self, track_id: int, old_path_str: str, new_path_str: str
    ) -> None:
        """Update pending deferred_op payloads whose old_path matches *old_path_str*.

        Called after a per-file move in an album rename so that any previously
        queued op for the same track still points to the file's new location.
        """
        import json as _json

        rows = self._conn.execute(
            "SELECT id, payload_json FROM deferred_ops WHERE track_id=?",
            (track_id,),
        ).fetchall()
        for row in rows:
            payload = _json.loads(row["payload_json"])
            if payload.get("old_path") == old_path_str:
                payload["old_path"] = new_path_str
                self._conn.execute(
                    "UPDATE deferred_ops SET payload_json=? WHERE id=?",
                    (_json.dumps(payload), row["id"]),
                )
        self._conn.commit()

    def list_pending_deferred_ops_summary(self) -> list[dict[str, Any]]:
        """Return minimal {op_id, track_id} dicts for frontend reconciliation."""
        rows = self._conn.execute(
            "SELECT id, track_id FROM deferred_ops ORDER BY id ASC"
        ).fetchall()
        return [{"op_id": r["id"], "track_id": r["track_id"]} for r in rows]

    def update_track_after_album_drain(
        self,
        track_id: int,
        new_path: Path,
        album: str,
        album_artist: str,
        new_artist: str | None,
        mtime: float,
    ) -> None:
        """Update a single track's path + album tags after a deferred album_retag drains."""
        self._conn.execute(
            "UPDATE tracks SET file_path=?, album=?, album_artist=?, file_mtime=?"
            " WHERE id=?",
            (str(new_path), album, album_artist, mtime, track_id),
        )
        if new_artist is not None:
            self._conn.execute(
                "UPDATE tracks SET artist=? WHERE id=?",
                (new_artist, track_id),
            )
        # Rebuild FTS for the affected row.
        self._conn.execute("DELETE FROM tracks_fts WHERE rowid=?", (track_id,))
        self._conn.execute(
            "INSERT INTO tracks_fts(rowid, title, artist, album_artist, album)"
            " SELECT id, title, artist, album_artist, album FROM tracks WHERE id=?",
            (track_id,),
        )
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

    def record_track_started(self, file_path: Path) -> None:
        """Record the current time as last_played for the track at *file_path*.

        Called when a track begins playing so that Last Played sort order
        reflects when listening last occurred rather than when it ended.
        No-op if the path is not in the index.
        """
        import time

        self._conn.execute(
            "UPDATE tracks SET last_played = ? WHERE file_path = ?",
            (time.time(), str(file_path)),
        )
        self._conn.commit()

    def record_played(self, file_path: Path) -> None:
        """Increment play_count for the track at *file_path*.

        Called when a track reaches natural end-of-file. Only play_count is
        updated here; last_played is managed exclusively by record_track_started().
        """
        self._conn.execute(
            "UPDATE tracks SET play_count = play_count + 1 WHERE file_path = ?",
            (str(file_path),),
        )
        self._conn.commit()

    def set_favorite(self, file_path: Path, favorite: bool) -> None:
        """Set or clear the favorite flag for the track at *file_path*."""
        self._conn.execute(
            "UPDATE tracks SET favorite = ? WHERE file_path = ?",
            (int(favorite), str(file_path)),
        )
        self._conn.commit()

    def update_album_meta(
        self,
        album_artist: str,
        album: str,
        *,
        genre: str | None = None,
        label: str | None = None,
        year: str | None = None,
        mb_release_id: str | None = None,
    ) -> list[Track]:
        """Write genre, label, year, and/or mb_release_id to every track in *album*.

        Returns the updated Track objects.  Only the provided (non-None) fields
        are changed; the others are left as-is in the database.
        """
        sets: list[str] = []
        params: list[object] = []
        if genre is not None:
            sets.append("genre = ?")
            params.append(genre)
        if label is not None:
            sets.append("label = ?")
            params.append(label)
        if year is not None:
            sets.append("year = ?")
            params.append(year)
        if mb_release_id is not None:
            sets.append("mb_release_id = ?")
            params.append(mb_release_id)
        if not sets:
            return self.tracks_for_album(album_artist, album)
        params.extend([album_artist, album])
        self._conn.execute(
            f"UPDATE tracks SET {', '.join(sets)}"
            " WHERE album_artist = ? AND album = ?",
            params,
        )
        self._conn.commit()
        return self.tracks_for_album(album_artist, album)

    def mark_album_art_embedded(
        self, album_artist: str, album: str, file_paths: list[Path]
    ) -> None:
        """Mark successfully art-embedded tracks as having art and update their mtime.

        Sets ``embedded_art=1`` and ``file_mtime`` to the current time for
        every track whose path appears in *file_paths*.  Only tracks matching
        both the album identity and the given paths are touched — other tracks
        in the album (e.g. those skipped due to a playback lock) are left as-is.
        """
        import time

        now = time.time()
        str_paths = [str(p) for p in file_paths]
        placeholders = ",".join("?" * len(str_paths))
        self._conn.execute(
            f"UPDATE tracks SET embedded_art = 1, file_mtime = ?"
            f" WHERE album_artist = ? AND album = ?"
            f" AND file_path IN ({placeholders})",
            [now, album_artist, album, *str_paths],
        )
        self._conn.commit()

    def update_track_mb_recording_id(
        self, track_id: int, mb_recording_id: str
    ) -> Track | None:
        """Write mb_recording_id to a single track in the database.

        Returns the updated Track, or None if the track_id is not found.
        """
        self._conn.execute(
            "UPDATE tracks SET mb_recording_id = ? WHERE id = ?",
            (mb_recording_id, track_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return _row_to_track(row) if row else None

    def toggle_album_favorite(
        self, album_artist: str, album: str, favorite: bool
    ) -> None:
        """Insert or delete the album from album_favorites."""
        if favorite:
            self._conn.execute(
                "INSERT OR IGNORE INTO album_favorites (album_artist, album) VALUES (?, ?)",
                (album_artist, album),
            )
        else:
            self._conn.execute(
                "DELETE FROM album_favorites WHERE album_artist = ? AND album = ?",
                (album_artist, album),
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
                   missing_album, file_path, art_version,
                   sort_date_added, sort_last_played, sort_play_count_avg,
                   is_favorite, has_favorite_track,
                   album_source, has_remote_tracks, in_bandcamp_collection
            FROM (
                SELECT t.album_artist, t.album, t.year, COUNT(*) AS track_count,
                       MAX(t.embedded_art) AS has_art,
                       0 AS missing_album, '' AS file_path,
                       MIN(t.date_added) AS sort_date_added,
                       MAX(t.last_played) AS sort_last_played,
                       MAX(t.file_mtime) AS art_version,
                       CAST(SUM(t.play_count) AS REAL) / COUNT(*) AS sort_play_count_avg,
                       MAX(CASE WHEN af.album_artist IS NOT NULL THEN 1 ELSE 0 END) AS is_favorite,
                       MAX(t.favorite) AS has_favorite_track,
                       CASE WHEN COUNT(DISTINCT t.source) > 1 THEN 'mixed'
                            ELSE MIN(t.source) END AS album_source,
                       MAX(CASE WHEN t.source != 'local' THEN 1 ELSE 0 END) AS has_remote_tracks,
                       MAX(CASE WHEN bc.sale_item_id IS NOT NULL THEN 1 ELSE 0 END)
                           AS in_bandcamp_collection
                FROM tracks t
                LEFT JOIN album_favorites af
                    ON af.album_artist = t.album_artist AND af.album = t.album
                LEFT JOIN bandcamp_collection bc
                    ON bc.band_name = t.album_artist COLLATE NOCASE
                    AND bc.item_title = t.album COLLATE NOCASE
                    AND bc.mode = 'local'
                WHERE t.album != ''
                GROUP BY t.album_artist, t.album
                UNION ALL
                SELECT t.album_artist, t.title AS album, t.year, 1 AS track_count,
                       t.embedded_art AS has_art,
                       1 AS missing_album, t.file_path,
                       t.date_added AS sort_date_added,
                       t.last_played AS sort_last_played,
                       t.file_mtime AS art_version,
                       CAST(t.play_count AS REAL) AS sort_play_count_avg,
                       CASE WHEN af.album_artist IS NOT NULL THEN 1 ELSE 0 END AS is_favorite,
                       t.favorite AS has_favorite_track,
                       t.source AS album_source,
                       CASE WHEN t.source != 'local' THEN 1 ELSE 0 END AS has_remote_tracks,
                       0 AS in_bandcamp_collection
                FROM tracks t
                LEFT JOIN album_favorites af
                    ON af.album_artist = t.album_artist AND af.album = t.title
                WHERE t.album = ''
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
                added_at=r["sort_date_added"],
                last_played_at=r["sort_last_played"],
                play_count_avg=r["sort_play_count_avg"] or 0.0,
                favorite=bool(r["is_favorite"]),
                has_favorite_track=bool(r["has_favorite_track"]),
                source=r["album_source"],
                has_remote_tracks=bool(r["has_remote_tracks"]),
                in_bandcamp_collection=bool(r["in_bandcamp_collection"]),
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
        """Return the set of local file paths currently in the index.

        Remote tracks (source != 'local') are excluded so the scanner never
        tries to stat a bandcamp:// URI or treats it as a missing file.
        """
        rows = self._conn.execute(
            "SELECT file_path FROM tracks WHERE source = 'local'"
        ).fetchall()
        return {Path(r["file_path"]) for r in rows}

    def indexed_paths_with_mtime(self) -> dict[Path, float | None]:
        """Return a mapping of local indexed file paths to their stored file_mtime.

        Remote tracks (source != 'local') are excluded for the same reason as
        indexed_paths() — their URI is not a real filesystem path.
        """
        rows = self._conn.execute(
            "SELECT file_path, file_mtime FROM tracks WHERE source = 'local'"
        ).fetchall()
        return {Path(r["file_path"]): r["file_mtime"] for r in rows}

    def get_track_by_path(self, path: "str | Path") -> "Track | None":
        """Return the track for *path*, or None if not indexed.

        Accepts both Path objects and plain strings. Strings are used directly
        as the lookup key to avoid Path normalization corrupting remote URIs
        (e.g. Path("bandcamp://999/3") collapses to "bandcamp:/999/3" on POSIX).
        """
        key = path if isinstance(path, str) else str(path)
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE file_path = ?", (key,)
        ).fetchone()
        return _row_to_track(row) if row else None

    def get_track_by_id(self, track_id: int) -> "Track | None":
        """Return the track with *track_id*, or None if not indexed."""
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return _row_to_track(row) if row else None

    def get_track_by_recording_id(self, mb_recording_id: str) -> "Track | None":
        """Return the first track with *mb_recording_id*, or None."""
        if not mb_recording_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE mb_recording_id = ?", (mb_recording_id,)
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
            LEFT JOIN album_favorites af
                ON t.album_artist = af.album_artist AND t.album = af.album
            WHERE tracks_fts MATCH ?
            ORDER BY (t.favorite OR af.album_artist IS NOT NULL) DESC, f.rank
            """,
            (fts_expr,),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    def save_player_state(self, track_path: "str | Path", position: float) -> None:
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

    def load_player_state(self) -> "tuple[str, float] | None":
        """Return (track_path, position) from the last session, or None.

        Returns the raw string stored in the DB — callers must not wrap it in
        Path() since it may be a remote URI (bandcamp://) that Path normalizes.
        """
        row = self._conn.execute(
            "SELECT track_path, position FROM player_state WHERE id = 1"
        ).fetchone()
        return (row["track_path"], row["position"]) if row else None

    def save_queue_state(
        self,
        tracks: "Sequence[Path | str]",
        order: list[int],
        pos: int,
        shuffle: bool,
        repeat: bool,
    ) -> None:
        """Persist the queue in original load order with playback permutation."""
        import json

        payload = json.dumps([str(p) for p in tracks])
        order_payload = json.dumps(order)
        self._conn.execute(
            """
            INSERT INTO queue_state (id, tracks, order_json, pos, shuffle, repeat)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                tracks     = excluded.tracks,
                order_json = excluded.order_json,
                pos        = excluded.pos,
                shuffle    = excluded.shuffle,
                repeat     = excluded.repeat
            """,
            (payload, order_payload, pos, int(shuffle), int(repeat)),
        )
        self._conn.commit()

    def load_queue_state(
        self,
    ) -> "tuple[list[str], list[int], int, bool, bool] | None":
        """Return (tracks_in_original_order, order, pos, shuffle, repeat) or None.

        Tracks are returned as raw strings (not Path objects) so remote URIs
        (bandcamp://) are not corrupted by Path normalization.
        """
        import json

        row = self._conn.execute(
            "SELECT tracks, order_json, pos, shuffle, repeat FROM queue_state WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        paths: list[str] = list(json.loads(row["tracks"]))
        raw_order = row["order_json"]
        order: list[int] = (
            json.loads(raw_order) if raw_order else list(range(len(paths)))
        )
        return paths, order, row["pos"], bool(row["shuffle"]), bool(row["repeat"])

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
    str,
    str,
    str,
    str | None,
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
        t.genre,
        t.label,
        t.source,
        t.stream_url,
        t.stream_url_expires_at,
    )


def _row_to_deferred_op(row: sqlite3.Row) -> DeferredOp:
    return DeferredOp(
        id=row["id"],
        op_type=row["op_type"],
        track_id=row["track_id"],
        payload_json=row["payload_json"],
        created_at=row["created_at"],
        attempts=row["attempts"],
        last_error=row["last_error"],
    )


def _row_to_track(row: sqlite3.Row) -> Track:
    # KAMP-383 note: Path("bandcamp://sale_item_id/track_num") is safe on
    # POSIX (round-trips via str()) but corrupts on Windows (backslash
    # normalisation). Fix Track.file_path type when remote tracks are first
    # inserted so Windows is addressed before they reach prod.
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
        genre=row["genre"],
        label=row["label"],
        date_added=row["date_added"],
        last_played=row["last_played"],
        favorite=bool(row["favorite"]),
        play_count=row["play_count"],
        file_mtime=row["file_mtime"],
        source=row["source"],
        stream_url=row["stream_url"],
        stream_url_expires_at=row["stream_url_expires_at"],
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
        if not frame:
            return ""
        # ID3 text frames may encode multiple values separated by \x00.
        # Replace with " / " for human-readable display.
        return str(frame).replace("\x00", " / ")

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
        mb_release_id=_str("TXXX:MusicBrainz Album Id")
        or _str("TXXX:MusicBrainz Release Id"),
        mb_recording_id=_str("TXXX:MusicBrainz Track Id"),
        genre=_str("TCON"),
        label=_str("TPUB"),
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
        mb_release_id=_s("----:com.apple.iTunes:MusicBrainz Album Id")
        or _s("----:com.apple.iTunes:MusicBrainz Release Id"),
        mb_recording_id=_s("----:com.apple.iTunes:MusicBrainz Track Id"),
        genre=_s("\xa9gen"),
        label=_s("----:com.apple.iTunes:LABEL"),
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
        genre=_s("GENRE"),
        label=_s("LABEL") or _s("ORGANIZATION"),
    )


def write_title_to_file(path: Path, title: str) -> None:
    """Write a new title tag to an audio file without touching other tags."""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = id3.ID3(str(path))
        except Exception:
            tags = id3.ID3()
        tags["TIT2"] = id3.TIT2(encoding=3, text=title)
        tags.save(str(path))
    elif suffix == ".m4a":
        audio = mutagen.mp4.MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9nam"] = [title]  # type: ignore[index]
        audio.save()
    elif suffix == ".flac":
        audio = mutagen.flac.FLAC(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["TITLE"] = [title]  # type: ignore[index]
        audio.save()
    elif suffix == ".ogg":
        audio = mutagen.oggvorbis.OggVorbis(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["TITLE"] = [title]  # type: ignore[index]
        audio.save()
    else:
        raise ValueError(f"Unsupported format for title write: {path.suffix}")


def write_album_tags_to_file(
    path: Path, album: str, album_artist: str, artist: str | None = None
) -> None:
    """Write album and album_artist tags to an audio file without touching other tags.

    artist, when provided, also updates the per-track artist tag (TPE1 / ©ART /
    ARTIST).  Pass it when track.artist matched the old album_artist so that
    renaming a single-artist album keeps the per-track tag in sync.
    """
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = id3.ID3(str(path))
        except Exception:  # pragma: no cover
            tags = id3.ID3()
        tags["TALB"] = id3.TALB(encoding=3, text=album)
        tags["TPE2"] = id3.TPE2(encoding=3, text=album_artist)
        if artist is not None:
            tags["TPE1"] = id3.TPE1(encoding=3, text=artist)
        tags.save(str(path))
    elif suffix == ".m4a":
        audio = mutagen.mp4.MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9alb"] = [album]  # type: ignore[index]
        audio.tags["aART"] = [album_artist]  # type: ignore[index]
        if artist is not None:
            audio.tags["\xa9ART"] = [artist]  # type: ignore[index]
        audio.save()
    elif suffix == ".flac":
        audio = mutagen.flac.FLAC(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["ALBUM"] = [album]  # type: ignore[index]
        audio.tags["ALBUMARTIST"] = [album_artist]  # type: ignore[index]
        if artist is not None:
            audio.tags["ARTIST"] = [artist]  # type: ignore[index]
        audio.save()
    elif suffix == ".ogg":
        audio = mutagen.oggvorbis.OggVorbis(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["ALBUM"] = [album]  # type: ignore[index]
        audio.tags["ALBUMARTIST"] = [album_artist]  # type: ignore[index]
        if artist is not None:
            audio.tags["ARTIST"] = [artist]  # type: ignore[index]
        audio.save()
    else:
        raise ValueError(f"Unsupported format for album tag write: {path.suffix}")


def write_meta_tags_to_file(
    path: Path,
    *,
    genre: str | None = None,
    label: str | None = None,
    year: str | None = None,
    mb_release_id: str | None = None,
) -> None:
    """Write genre, label, year, and/or mb_release_id to an audio file without moving it.

    Only the fields that are not None are written; the others are left
    unchanged on disk.  This is a tag-only operation — no file rename occurs.
    """
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = id3.ID3(str(path))
        except Exception:
            tags = id3.ID3()
        if genre is not None:
            tags["TCON"] = id3.TCON(encoding=3, text=genre)
        if label is not None:
            tags["TPUB"] = id3.TPUB(encoding=3, text=label)
        if year is not None:
            tags["TDRC"] = id3.TDRC(encoding=3, text=year)
        if mb_release_id is not None:
            tags["TXXX:MusicBrainz Album Id"] = id3.TXXX(
                encoding=3, desc="MusicBrainz Album Id", text=mb_release_id
            )
        tags.save(str(path))
    elif suffix == ".m4a":
        audio = mutagen.mp4.MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        if genre is not None:
            audio.tags["\xa9gen"] = [genre]  # type: ignore[index]
        if label is not None:
            audio.tags["----:com.apple.iTunes:LABEL"] = [  # type: ignore[index]
                mutagen.mp4.MP4FreeForm(label.encode())
            ]
        if year is not None:
            audio.tags["\xa9day"] = [year]  # type: ignore[index]
        if mb_release_id is not None:
            audio.tags["----:com.apple.iTunes:MusicBrainz Album Id"] = [  # type: ignore[index]
                mutagen.mp4.MP4FreeForm(mb_release_id.encode())
            ]
        audio.save()
    elif suffix == ".flac":
        audio = mutagen.flac.FLAC(str(path))
        if audio.tags is None:
            audio.add_tags()
        if genre is not None:
            audio.tags["GENRE"] = [genre]  # type: ignore[index]
        if label is not None:
            audio.tags["LABEL"] = [label]  # type: ignore[index]
        if year is not None:
            audio.tags["DATE"] = [year]  # type: ignore[index]
        if mb_release_id is not None:
            audio.tags["MUSICBRAINZ_ALBUMID"] = [mb_release_id]  # type: ignore[index]
        audio.save()
    elif suffix == ".ogg":
        audio = mutagen.oggvorbis.OggVorbis(str(path))
        if audio.tags is None:
            audio.add_tags()
        if genre is not None:
            audio.tags["GENRE"] = [genre]  # type: ignore[index]
        if label is not None:
            audio.tags["LABEL"] = [label]  # type: ignore[index]
        if year is not None:
            audio.tags["DATE"] = [year]  # type: ignore[index]
        if mb_release_id is not None:
            audio.tags["MUSICBRAINZ_ALBUMID"] = [mb_release_id]  # type: ignore[index]
        audio.save()
    else:
        raise ValueError(f"Unsupported format for meta tag write: {path.suffix}")


def write_track_mbid_to_file(path: Path, *, mb_recording_id: str) -> None:
    """Write a MusicBrainz recording ID to an audio file without moving it.

    Tag-only operation — no file rename occurs.
    """
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = id3.ID3(str(path))
        except Exception:
            tags = id3.ID3()
        tags["TXXX:MusicBrainz Track Id"] = id3.TXXX(
            encoding=3, desc="MusicBrainz Track Id", text=mb_recording_id
        )
        tags.save(str(path))
    elif suffix == ".m4a":
        audio = mutagen.mp4.MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["----:com.apple.iTunes:MusicBrainz Track Id"] = [  # type: ignore[index]
            mutagen.mp4.MP4FreeForm(mb_recording_id.encode())
        ]
        audio.save()
    elif suffix == ".flac":
        audio = mutagen.flac.FLAC(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["MUSICBRAINZ_TRACKID"] = [mb_recording_id]  # type: ignore[index]
        audio.save()
    elif suffix == ".ogg":
        audio = mutagen.oggvorbis.OggVorbis(str(path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["MUSICBRAINZ_TRACKID"] = [mb_recording_id]  # type: ignore[index]
        audio.save()
    else:
        raise ValueError(f"Unsupported format for MBID tag write: {path.suffix}")


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
        on_progress: Callable[[int, int, "Track | None"], None] | None = None,
    ) -> ScanResult:
        """Scan *library_path* recursively and update the index.

        New files are read and added. Files whose mtime has changed since the
        last scan are re-read so tag edits (e.g. adding cover art) are picked
        up automatically. Index entries whose files no longer exist on disk are
        removed.

        *on_progress*, if provided, is called after each processed file's tags
        are read with (current, total, track) where total = number of files to
        index (new + updated) and track is the parsed Track (or None if parsing
        failed).
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
                if not track.embedded_art and _has_cover_file(path.parent):
                    track.embedded_art = True
                tracks_to_upsert.append(track)
            else:
                logger.warning("Skipped unreadable file: %s", path)
            if on_progress is not None:
                on_progress(current, total, track)

        # Defence in depth: if a "new" file shares a mb_recording_id with a
        # track that is about to be removed (i.e. the file was moved outside
        # Kamp), update the existing row's path rather than creating a duplicate.
        removed_paths = in_index - on_disk
        removed_by_mbid: dict[str, Track] = {}
        for p in removed_paths:
            t = self._index.get_track_by_path(p)
            if t is not None and t.mb_recording_id:
                removed_by_mbid[t.mb_recording_id] = t

        reconciled_old_paths: set[Path] = set()
        reconciled_new_paths: set[Path] = set()
        for track in tracks_to_upsert:
            if track.file_path not in to_add or not track.mb_recording_id:
                continue
            old = removed_by_mbid.get(track.mb_recording_id)
            if old is None:
                continue
            self._index.move_track(
                old.file_path, track.file_path, track.title, track.file_mtime or 0.0
            )
            logger.info(
                "Reconciled moved track by recording id: %s → %s",
                old.file_path,
                track.file_path,
            )
            reconciled_old_paths.add(old.file_path)
            reconciled_new_paths.add(track.file_path)

        upsert_subset = [
            t for t in tracks_to_upsert if t.file_path not in reconciled_new_paths
        ]
        self._index.upsert_many(upsert_subset)

        newly_added = [t for t in upsert_subset if t.file_path in to_add]
        added = len(newly_added) + len(reconciled_new_paths)
        updated = len([t for t in upsert_subset if t.file_path in to_update])

        removed = 0
        for path in removed_paths:
            if path not in reconciled_old_paths:
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
