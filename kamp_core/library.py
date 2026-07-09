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
import mutagen.mp3
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

_SCHEMA_VERSION = 48


class NoStreamableVersionError(Exception):
    """Raised by remove_download when the album cannot fall back to streaming.

    Reverting a downloaded album deletes its local track rows; that is only safe
    when every local track has a matching bandcamp:// stream row to fall back to.
    When one or more do not (download-mode purchase never streamed, a partial
    materialization, or a multi-disc download whose disc>1 tracks have no stream
    counterpart), removal would strand those tracks — so we abort and delete
    nothing rather than destroy the album (KAMP-527).
    """


@dataclass
class Condition:
    """A single field-level filter in a magic playlist."""

    field: str
    op: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Condition":
        return cls(field=d["field"], op=d["op"], value=d["value"])


@dataclass
class Group:
    """A group of conditions combined with a boolean match rule."""

    conditions: list[Condition]
    match: str  # "all" | "any"
    negate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "conditions": [c.to_dict() for c in self.conditions],
            "match": self.match,
            "negate": self.negate,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Group":
        return cls(
            conditions=[Condition.from_dict(c) for c in d["conditions"]],
            match=d["match"],
            negate=d.get("negate", False),
        )


@dataclass
class MagicCriteria:
    """Top-level criteria for a magic playlist: groups combined with a boolean match rule."""

    groups: list[Group]
    match: str  # "all" | "any"

    def to_dict(self) -> dict[str, Any]:
        return {"groups": [g.to_dict() for g in self.groups], "match": self.match}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MagicCriteria":
        return cls(groups=[Group.from_dict(g) for g in d["groups"]], match=d["match"])


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
    release_date     TEXT    NOT NULL DEFAULT '',
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
    stream_url_expires_at REAL,
    album_id             INTEGER REFERENCES albums(id),
    is_available         INTEGER NOT NULL DEFAULT 1,
    duration             REAL    NOT NULL DEFAULT 0,
    -- User-set display overrides for streaming tracks (KAMP-467).
    -- NULL means "use the canonical value from Bandcamp".
    display_title        TEXT,
    display_album        TEXT,
    display_album_artist TEXT,
    -- Track-level Bandcamp provenance (KAMP-528). Mirrors albums.sale_item_id but
    -- resolves standalone singles: a purchased single has no album row, so the
    -- only place to record "this file came from sale_item_id X" is here. Written
    -- from the KAMP_SALE_ITEM_ID file tag on upsert (valid_sids only, FK-safe).
    -- The supporting index (tracks_sale_item_id_idx) is created by
    -- _create_tracks_sale_item_id_index(), NOT here: on an upgrade, executescript
    -- runs the whole _DDL before the v41 migration adds this column, so a
    -- CREATE INDEX in _DDL would reference a not-yet-existing column and fail.
    sale_item_id         TEXT    REFERENCES bandcamp_collection(sale_item_id)
);

-- Canonical-track model, expand phase (KAMP-535, epic KAMP-533). A track's
-- identity (tracks) is being split from the per-delivery sources that provide
-- its bytes (track_sources) and its mutable listener stats (track_stats), so the
-- streaming and downloaded copies of one track stop diverging (KAMP-532). These
-- two child tables are created EMPTY here; nothing reads or writes them yet.
-- KAMP-536 populates them (collapsing sibling rows) and switches reads over;
-- KAMP-539 drops the now-duplicated columns from tracks. Both tables reference
-- tracks(id) — no FK points back the other way — so existing INSERT/DELETE paths
-- on tracks are unaffected while these stay empty. See
-- docs/design/KAMP-533-canonical-track-identity.md §2.
--
-- track_sources: one row per way-to-get-the-bytes. Two orthogonal axes replace
-- the old tracks.source enum — kind (how bytes arrive) and provider (which
-- catalog/adapter). provider_item_id generalizes sale_item_id with NO hard FK
-- (adapter-validated). Only uri is UNIQUE: .mp3+.flac of one track and the same
-- track streamable from two providers are legitimate plural sources.
CREATE TABLE IF NOT EXISTS track_sources (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id              INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    kind                  TEXT    NOT NULL CHECK (kind IN ('file', 'stream')),
    provider              TEXT    NOT NULL DEFAULT '',
    provider_item_id      TEXT,
    uri                   TEXT    NOT NULL UNIQUE,
    ext                   TEXT    NOT NULL DEFAULT '',
    duration              REAL    NOT NULL DEFAULT 0,
    embedded_art          INTEGER NOT NULL DEFAULT 0,
    file_mtime            REAL,
    is_available          INTEGER NOT NULL DEFAULT 1,
    stream_url            TEXT,
    stream_url_expires_at REAL
);
CREATE INDEX IF NOT EXISTS track_sources_track_idx    ON track_sources(track_id);
CREATE INDEX IF NOT EXISTS track_sources_provider_idx ON track_sources(provider, provider_item_id);

-- track_stats: mutable listener state, separated so identity stays immutable and
-- multi-client (single-listener endpoint fanout) writes have one home. No
-- profile_id — stats are one row per track. updated_at is a last-writer-wins
-- timestamp for concurrent edits from multiple devices.
CREATE TABLE IF NOT EXISTS track_stats (
    track_id    INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    favorite    INTEGER NOT NULL DEFAULT 0,
    play_count  INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    updated_at  REAL
);

-- First-class album entity (KAMP-418). Replaces the GROUP BY (album_artist, album)
-- derived-aggregate pattern. COLLATE NOCASE on the UNIQUE constraint prevents
-- case-variant duplicates (e.g. "CASTLEBEAT" vs "Castlebeat") from coexisting.
-- favorite is stored here directly; album_favorites table is dropped in v24.
CREATE TABLE IF NOT EXISTS artists (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    play_time REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS albums (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    album_artist   TEXT    NOT NULL DEFAULT '' COLLATE NOCASE,
    album          TEXT    NOT NULL DEFAULT '' COLLATE NOCASE,
    release_date   TEXT    NOT NULL DEFAULT '',
    embedded_art   INTEGER NOT NULL DEFAULT 0,
    mb_release_id  TEXT    NOT NULL DEFAULT '',
    genre          TEXT    NOT NULL DEFAULT '',
    label          TEXT    NOT NULL DEFAULT '',
    source         TEXT    NOT NULL DEFAULT 'local',
    sale_item_id   TEXT    REFERENCES bandcamp_collection(sale_item_id),
    favorite       INTEGER NOT NULL DEFAULT 0,
    date_added     REAL,
    last_played_at REAL,
    play_count_avg REAL    NOT NULL DEFAULT 0,
    art_version    REAL,
    artist_id      INTEGER REFERENCES artists(id),
    -- User-set display overrides for streaming albums (KAMP-467).
    -- NULL means "use the canonical value from Bandcamp".
    display_album        TEXT,
    display_album_artist TEXT,
    UNIQUE (album_artist, album)
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
    repeat     TEXT    NOT NULL DEFAULT 'off'
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
    sale_item_id          TEXT NOT NULL PRIMARY KEY,
    item_type             TEXT NOT NULL DEFAULT 'p',
    band_name             TEXT NOT NULL DEFAULT '',
    item_title            TEXT NOT NULL DEFAULT '',
    tralbum_id            TEXT NOT NULL DEFAULT '',
    album_url             TEXT NOT NULL DEFAULT '',
    mode                  TEXT NOT NULL DEFAULT 'local',
    synced_at             REAL,
    added_at              REAL NOT NULL DEFAULT 0,
    num_streamable_tracks INTEGER NOT NULL DEFAULT 0
);

-- Serialized album download queue (KAMP-408).
-- UNIQUE on sale_item_id prevents double-enqueue; INSERT OR IGNORE is idempotent.
-- queued_at is a Unix timestamp used for FIFO replay order on restart.
CREATE TABLE IF NOT EXISTS download_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_item_id TEXT    NOT NULL UNIQUE,
    queued_at    REAL    NOT NULL DEFAULT (unixepoch())
);

-- Download → pipeline provenance handoff (KAMP-523). When the Bandcamp
-- downloader drops an artifact (album ZIP or bare single) in the watch folder,
-- it records the artifact path -> sale_item_id here. The ingest pipeline runs
-- in a spawn subprocess that only receives (path, config); it looks this row up
-- to (a) write the metadata we already own instead of re-deriving it via
-- MusicBrainz and (b) stamp a durable provenance tag so the scan re-attaches the
-- files to their streaming origin by identity, not by fragile tag matching.
-- UNIQUE(artifact_path) makes a re-download idempotent. The row is deleted when
-- the pipeline finishes (success or quarantine); a startup sweep clears rows
-- whose artifact no longer exists (crash/restart mid-flight).
CREATE TABLE IF NOT EXISTS pending_ingest (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_path TEXT    NOT NULL UNIQUE,
    sale_item_id  TEXT    NOT NULL,
    tralbum_id    TEXT    NOT NULL DEFAULT '',
    created_at    REAL    NOT NULL
);

-- User-defined playlists (KAMP-441). Local-only; Bandcamp playlist sync is future work.
CREATE TABLE IF NOT EXISTS playlists (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL,
    favorite       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL    NOT NULL,
    updated_at     REAL    NOT NULL,
    last_played_at REAL
);

-- FTS5 index for playlist name search (KAMP-442). Non-content table; manually synced.
CREATE VIRTUAL TABLE IF NOT EXISTS playlists_fts USING fts5(
    title,
    tokenize = 'unicode61'
);

-- Ordered track membership in a playlist (KAMP-441).
-- position is a dense integer rank (0-based); reorder rewrites all affected rows.
-- ON DELETE CASCADE means deleting a playlist removes all its track rows atomically.
CREATE TABLE IF NOT EXISTS playlist_tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL
);

-- Magic playlist criteria (KAMP-459). A playlist is magic iff it has a row here.
-- criteria_json stores a MagicCriteria JSON blob. evaluated_at is the epoch time of
-- the last evaluation; NULL means the cache is stale and must be rebuilt.
-- cached_track_count stores the result count from the last evaluation (KAMP-464).
CREATE TABLE IF NOT EXISTS magic_playlist_criteria (
    playlist_id         INTEGER PRIMARY KEY REFERENCES playlists(id) ON DELETE CASCADE,
    criteria_json       TEXT NOT NULL,
    evaluated_at        REAL,
    cached_track_count  INTEGER
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
# {dir} is replaced with ASC or DESC at query time via albums(sort_dir=...).
# Only the primary sort field carries the direction; the album_artist tiebreaker
# is always ASC so results are deterministic regardless of chosen direction.
_SORT_CLAUSES: dict[str, str] = {
    "album_artist": "album_artist COLLATE NOCASE {dir}, album COLLATE NOCASE",
    "album": "sort_album COLLATE NOCASE {dir}, album_artist COLLATE NOCASE",
    # These reference the sort_date_added / sort_last_played / sort_play_count_avg
    # columns produced by the UNION ALL query in albums().
    "date_added": "sort_date_added {dir}, album_artist COLLATE NOCASE",
    "last_played": "sort_last_played {dir}, album_artist COLLATE NOCASE",
    "most_played": "sort_play_count_avg {dir}, album_artist COLLATE NOCASE",
    # sort_release_date is NULLIF(release_date,'') aliased in the UNION ALL branches so
    # empty strings sort last regardless of direction (NULLS LAST convention).
    "release_date": "sort_release_date {dir} NULLS LAST, album_artist COLLATE NOCASE",
}

# Natural default direction per sort key — used when sort_dir is not provided.
# Text sorts default to ascending (A→Z); date/play sorts to descending
# (newest/most first), preserving the historical behaviour.
_DEFAULT_SORT_DIR: dict[str, str] = {
    "album_artist": "ASC",
    "album": "ASC",
    "date_added": "DESC",
    "last_played": "DESC",
    "most_played": "DESC",
    "release_date": "DESC",
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
    release_date: str
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
    # False for pre-order tracks whose CDN file has not yet been released.
    is_available: bool = field(default=True, compare=False)
    duration: float = field(default=0.0, compare=False)
    # Runtime flag; not persisted to DB. False for stub tracks created during
    # queue restore when the DB row is missing (e.g. after a DB wipe).
    reachable: bool = field(default=True, compare=False)
    # Bandcamp provenance stamp read from the file's KAMP_SALE_ITEM_ID tag
    # (KAMP-523). Runtime-only: it is NOT stored on the tracks table — it is
    # consumed by upsert_many to link the file to its album by identity, and the
    # link persists via album_id, so a later re-scan re-reads it from the file.
    sale_item_id: str = field(default="", compare=False)

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
    release_date: str
    track_count: int
    has_art: bool = False
    # True when the track has no album tag; album field holds the track title
    # as a display name, and file_path uniquely identifies this virtual album.
    missing_album: bool = False
    file_path: str = ""
    # MAX(file_mtime) across the album's local tracks — cache-busting key for image URLs.
    art_version: float | None = None
    # MIN(date_added) across the album's tracks — for recency filtering.
    added_at: float | None = None
    # MAX(last_played) across the album's tracks — for the Last Played module.
    last_played_at: float | None = None
    # SUM(play_count) / COUNT(*) across tracks — for the Top Albums module.
    play_count_avg: float = 0.0
    # True when the user has favorited this album (KAMP-293/KAMP-418).
    # Stored directly on the albums row since KAMP-418; previously in album_favorites.
    favorite: bool = False
    # True when any track in this album is individually favorited (KAMP-294).
    has_favorite_track: bool = False
    # 'local' when all tracks are local, the service name (e.g. 'bandcamp') when
    # all tracks share one remote source, or 'mixed' when both are present.
    source: str = "local"
    # True when any track in this album has source != 'local'.
    has_remote_tracks: bool = False
    # True when this album has a sale_item_id link to bandcamp_collection.
    in_bandcamp_collection: bool = False
    # Bandcamp sale_item_id — stored on the albums row since KAMP-418.
    sale_item_id: str | None = None
    # True when the album is a Bandcamp pre-order (some tracks not yet released).
    is_preorder: bool = False
    # Number of streamable tracks Bandcamp reports for this purchase (KAMP-527).
    # 0 means no streamable version exists — "Remove download" would strand the
    # album, so the UI hides that action. Snapshot; the server re-verifies.
    num_streamable_tracks: int = 0
    # Bandcamp album page URL — non-empty for streaming/downloaded Bandcamp albums.
    album_url: str = ""
    # Stable integer PK of the albums row; 0 for missing-album virtual entries.
    album_id: int = field(default=0, compare=False)
    # User-set display overrides for streaming albums (KAMP-467). None means
    # no override; the canonical album/album_artist is the authoritative value.
    display_album: str | None = None
    display_album_artist: str | None = None

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
class ArtistInfo:
    """Aggregated info for a single artist, returned by top_artists()."""

    name: str
    play_time: float  # total elapsed playback seconds
    top_album: str | None  # album with highest play_count_avg for thumbnail


@dataclass
class LibraryStats:
    """Aggregate library and listening statistics, returned by get_stats()."""

    track_count: int
    album_count: int
    artist_count: int
    total_play_seconds: float
    total_track_plays: int
    albums_played: int
    top_artist_name: str | None
    top_artist_seconds: float | None
    top_tracks: "list[Track]"


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


@dataclass
class DownloadOverrides:
    """Effective album names + user title edits for a Bandcamp download (KAMP-523).

    ``album_artist`` / ``album`` are the *effective* names to stamp on the
    downloaded files (user display override, else synced canonical, else the
    collection ledger) so a download matches its streaming origin — empty only
    when the item is entirely unknown. ``titles`` is keyed by track_number and
    holds only the tracks the user actually renamed.
    """

    album_artist: str
    album: str
    titles: dict[int, str]


@dataclass
class PendingIngest:
    """A download → pipeline provenance handoff row (KAMP-523).

    Maps a watch-folder artifact (album ZIP or bare single) to the Bandcamp
    identity the downloader knew at download time, so the ingest pipeline can
    write known metadata and stamp a provenance tag instead of re-deriving
    identity from tags via MusicBrainz.
    """

    id: int
    artifact_path: str
    sale_item_id: str
    tralbum_id: str
    created_at: float


# A track's effective (preferred) delivery, reconstructed from track_sources
# (KAMP-542). Reproduces post-collapse tracks.source exactly: the preferred
# source is chosen by the same ordering as preferred_source(), and its kind is
# mapped file->'local' / stream->'bandcamp' — the identical mapping
# _sync_tracks_row_to_preferred_source writes to tracks.source. Correlated on the
# outer tracks alias `t`; lets the source classifiers read track_sources so
# KAMP-539 can drop tracks.source. `t.file_path LIKE 'bandcamp://%'` (which the
# album formula used) equals "this effective source is a stream", i.e.
# `<eff> <> 'local'`.
_EFFECTIVE_SOURCE_EXPR = (
    "COALESCE("
    "(SELECT CASE WHEN s.kind = 'file' THEN 'local' ELSE 'bandcamp' END"
    " FROM track_sources s WHERE s.track_id = t.id"
    " ORDER BY s.is_available DESC, (s.kind = 'file') DESC, s.id LIMIT 1)"
    ", t.source)"  # fall back to the legacy column when a track has no sources
    # (a pre-540 row or a bare test fixture); tracks.source is NOT NULL. The
    # fallback is dropped with the column in KAMP-539.
)

# Album `source` badge ('local' / 'bandcamp' / 'mixed'), correlated on the outer
# `albums.id`. Behavior-preserving reconstruction of the legacy formula (which
# read tracks.source and file_path LIKE 'bandcamp://%'): computes each track's
# effective source once in a derived table, then applies the identical CASE. The
# 'mixed' arm is unreachable post-collapse — preserved verbatim so the badge is
# byte-for-byte unchanged (see KAMP-542; the quirk is tracked separately).
_ALBUM_SOURCE_SUBQUERY = (
    "(SELECT CASE"
    "   WHEN COUNT(CASE WHEN es.eff = 'local' THEN 1 END) > 0"
    "    AND COUNT(CASE WHEN es.eff <> 'local' THEN 1 END) > 0 THEN 'local'"
    "   WHEN COUNT(DISTINCT es.eff) > 1 THEN 'mixed'"
    "   ELSE MIN(es.eff) END"
    "  FROM (SELECT " + _EFFECTIVE_SOURCE_EXPR + " AS eff"
    "        FROM tracks t WHERE t.album_id = albums.id) es)"
)


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
        # Injected by the server to push magic_playlist.updated WebSocket events
        # when a field referenced by magic criteria changes. Default None so
        # library mutations work without a server context (e.g. in tests).
        self.on_fields_changed: Callable[[set[str]], None] | None = None
        # Correct permissions on existing installs where the file was created
        # with the default umask (644).
        if db_path.exists():
            db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        self._migrate()
        self._create_tracks_with_stats_view()

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
        # Enforce referential integrity so orphaned album_id / sale_item_id
        # FKs are caught at write time rather than silently ignored.
        conn.execute("PRAGMA foreign_keys=ON")
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
            # Fresh DBs have no forks, so the identity-uniqueness index is safe
            # to create immediately (migrations, which also create it after the
            # heal, do not run for a brand-new DB).
            self._create_sale_item_id_unique_index()
            self._create_tracks_sale_item_id_index()
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

        if version < 21:
            # v20 → v21: rename generic source value 'remote' to the specific
            # service name 'bandcamp'. All remote tracks written before this
            # migration came from Bandcamp — no other remote service existed.
            self._conn.execute(
                "UPDATE tracks SET source = 'bandcamp' WHERE source = 'remote'"
            )
            self._conn.execute("UPDATE schema_version SET version = 21")
            self._conn.commit()
            version = 21

        if version == 21:
            # v21 → v22: normalise bandcamp:/ (POSIX-collapsed single-slash) file_path
            # rows to canonical bandcamp:// form.  str(Path("bandcamp://x/y")) on POSIX
            # collapses to "bandcamp:/x/y", so all remote tracks written before this
            # migration have the single-slash form.  The canonical form is required so
            # get_track_by_path("bandcamp://x/y") — used by the queue restore loop —
            # matches the stored row.  Windows backslash rows are unaffected (they are
            # already handled separately throughout the codebase).
            self._conn.execute("""
                UPDATE tracks
                SET file_path = 'bandcamp://' || substr(file_path, 11)
                WHERE file_path LIKE 'bandcamp:/%'
                  AND file_path NOT LIKE 'bandcamp://%'
                """)
            self._conn.execute("UPDATE schema_version SET version = 22")
            self._conn.commit()
            version = 22

        if version == 22:
            # v22 → v23: download_queue table for serialized album downloads (KAMP-408).
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS download_queue (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_item_id TEXT    NOT NULL UNIQUE,
                    queued_at    REAL    NOT NULL DEFAULT (unixepoch())
                )
            """)
            self._conn.execute("UPDATE schema_version SET version = 23")
            self._conn.commit()
            version = 23

        if version == 23:
            # v23 → v24: promote albums to a first-class entity (KAMP-418).
            #
            # Step 1: create the albums table (idempotent — _DDL may have already
            # created it on a fresh database, IF NOT EXISTS handles that).
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS albums (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_artist   TEXT    NOT NULL DEFAULT '' COLLATE NOCASE,
                    album          TEXT    NOT NULL DEFAULT '' COLLATE NOCASE,
                    year           TEXT    NOT NULL DEFAULT '',
                    embedded_art   INTEGER NOT NULL DEFAULT 0,
                    mb_release_id  TEXT    NOT NULL DEFAULT '',
                    genre          TEXT    NOT NULL DEFAULT '',
                    label          TEXT    NOT NULL DEFAULT '',
                    source         TEXT    NOT NULL DEFAULT 'local',
                    sale_item_id   TEXT    REFERENCES bandcamp_collection(sale_item_id),
                    favorite       INTEGER NOT NULL DEFAULT 0,
                    date_added     REAL,
                    last_played_at REAL,
                    play_count_avg REAL    NOT NULL DEFAULT 0,
                    art_version    REAL,
                    UNIQUE (album_artist, album)
                )
            """)
            # Step 2: populate albums from tracks with case-insensitive deduplication.
            # For each LOWER(album_artist)/LOWER(album) group, pick the most-common-case
            # variant as canonical (sub-select ordered by count DESC, then alphabetically
            # for determinism). Exclude tracks with no album tag — they remain virtual.
            # Build the SELECT dynamically: some old test DBs (and real DBs that started
            # on an early version) may be missing columns that later migrations added.
            # Rather than fail on a missing column, fall back to a literal default.
            _tc = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }

            def _col(name: str, expr: str, default: str = "''") -> str:
                return expr if name in _tc else default

            self._conn.execute(f"""
                INSERT OR IGNORE INTO albums
                    (album_artist, album, release_date, embedded_art, mb_release_id,
                     genre, label, source, date_added, last_played_at,
                     play_count_avg, art_version)
                SELECT
                    (SELECT t2.album_artist FROM tracks t2
                     WHERE LOWER(t2.album_artist) = LOWER(t.album_artist)
                       AND LOWER(t2.album) = LOWER(t.album)
                     GROUP BY t2.album_artist
                     ORDER BY COUNT(*) DESC, t2.album_artist ASC
                     LIMIT 1),
                    (SELECT t2.album FROM tracks t2
                     WHERE LOWER(t2.album_artist) = LOWER(t.album_artist)
                       AND LOWER(t2.album) = LOWER(t.album)
                     GROUP BY t2.album
                     ORDER BY COUNT(*) DESC, t2.album ASC
                     LIMIT 1),
                    {_col('release_date', "MAX(t.release_date)", _col('year', "MAX(t.year)"))},
                    MAX(t.embedded_art),
                    {_col('mb_release_id', "MAX(t.mb_release_id)")},
                    {_col('genre', "MAX(t.genre)")},
                    {_col('label', "MAX(t.label)")},
                    {_col('source',
                          "CASE WHEN COUNT(CASE WHEN t.source='local' THEN 1 END) > 0"
                          "          AND COUNT(CASE WHEN t.file_path LIKE 'bandcamp://%' THEN 1 END) > 0"
                          "     THEN 'local'"
                          "     WHEN COUNT(DISTINCT t.source) > 1 THEN 'mixed'"
                          "     ELSE MIN(t.source) END",
                          "'local'")},
                    {_col('date_added', "MIN(t.date_added)", "NULL")},
                    {_col('last_played', "MAX(t.last_played)", "NULL")},
                    {_col('play_count', "CAST(SUM(t.play_count) AS REAL) / COUNT(*)", "0")},
                    MAX(t.file_mtime)
                FROM tracks t
                WHERE t.album != ''
                GROUP BY LOWER(t.album_artist), LOWER(t.album)
            """)
            # Step 3: link sale_item_id on album rows via the bandcamp_collection ledger.
            # Guard: old bandcamp_collection schemas (before v19 settled on band_name/item_title)
            # may use different column names — skip if the expected columns are absent.
            _bcc = {
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(bandcamp_collection)"
                ).fetchall()
            }
            if "band_name" in _bcc and "item_title" in _bcc:
                self._conn.execute("""
                    UPDATE albums SET sale_item_id = (
                        SELECT bc.sale_item_id
                        FROM bandcamp_collection bc
                        WHERE LOWER(bc.band_name)  = LOWER(albums.album_artist)
                          AND LOWER(bc.item_title) = LOWER(albums.album)
                        LIMIT 1
                    )
                    WHERE sale_item_id IS NULL
                """)
            # Step 4: add album_id FK column to tracks (guard: new DBs already have it).
            existing_cols = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "album_id" not in existing_cols:
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN album_id INTEGER REFERENCES albums(id)"
                )
            self._conn.execute("""
                UPDATE tracks SET album_id = (
                    SELECT a.id FROM albums a
                    WHERE LOWER(a.album_artist) = LOWER(tracks.album_artist)
                      AND LOWER(a.album) = LOWER(tracks.album)
                )
                WHERE album != '' AND album_id IS NULL
            """)
            # Step 5: absorb album_favorites into albums.favorite, then drop the table.
            af_exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='album_favorites'"
            ).fetchone()
            if af_exists:
                self._conn.execute("""
                    UPDATE albums SET favorite = 1
                    WHERE EXISTS (
                        SELECT 1 FROM album_favorites af
                        WHERE LOWER(af.album_artist) = LOWER(albums.album_artist)
                          AND LOWER(af.album)        = LOWER(albums.album)
                    )
                """)
                self._conn.execute("DROP TABLE album_favorites")
            self._conn.execute("UPDATE schema_version SET version = 24")
            self._conn.commit()
            version = 24

        if version == 24:
            # v24 → v25: add is_available to tracks for pre-order support (KAMP-423).
            existing = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "is_available" not in existing:
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN is_available INTEGER NOT NULL DEFAULT 1"
                )
            self._conn.execute("UPDATE schema_version SET version = 25")
            self._conn.commit()
            version = 25

        if version == 25:
            # v25 → v26: add num_streamable_tracks to bandcamp_collection (KAMP-424).
            existing = {
                r[1]
                for r in self._conn.execute(
                    "PRAGMA table_info(bandcamp_collection)"
                ).fetchall()
            }
            if "num_streamable_tracks" not in existing:
                self._conn.execute(
                    "ALTER TABLE bandcamp_collection"
                    " ADD COLUMN num_streamable_tracks INTEGER NOT NULL DEFAULT 0"
                )
            self._conn.execute("UPDATE schema_version SET version = 26")
            self._conn.commit()
            version = 26

        if version == 26:
            # v26 → v27: add duration to tracks for per-track and album total display (KAMP-399).
            existing = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "duration" not in existing:
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN duration REAL NOT NULL DEFAULT 0"
                )
            self._conn.execute("UPDATE schema_version SET version = 27")
            self._conn.commit()
            version = 27

        if version == 27:
            # v27 → v28: null file_mtime for local tracks so the next scan
            # re-reads them and populates the duration column added in v27 (KAMP-399).
            self._conn.execute(
                "UPDATE tracks SET file_mtime = NULL"
                " WHERE source = 'local' AND duration = 0"
            )
            self._conn.execute("UPDATE schema_version SET version = 28")
            self._conn.commit()
            version = 28

        if version == 28:
            # v28 → v29: add playlists and playlist_tracks tables (KAMP-441).
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT    NOT NULL,
                    favorite   INTEGER NOT NULL DEFAULT 0,
                    created_at REAL    NOT NULL,
                    updated_at REAL    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlist_tracks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                    file_path   TEXT    NOT NULL,
                    position    INTEGER NOT NULL
                );
                """)
            self._conn.execute("UPDATE schema_version SET version = 29")
            self._conn.commit()
            version = 29

        if version == 29:
            # v29 → v30: replace file_path with track_id FK in playlist_tracks (KAMP-448).
            # SQLite doesn't support DROP COLUMN on older engines, so use rename/recreate.
            # Rows whose file_path no longer maps to a track are dropped — they were
            # already orphaned.
            # Guard: _DDL may have already created playlist_tracks with track_id on
            # fresh installs that upgrade through v28→v29 in the same session, in which
            # case the rename/recreate is not needed.
            existing_cols = {
                r[1]
                for r in self._conn.execute(
                    "PRAGMA table_info(playlist_tracks)"
                ).fetchall()
            }
            if "file_path" in existing_cols:
                self._conn.executescript("""
                    ALTER TABLE playlist_tracks RENAME TO playlist_tracks_old;
                    CREATE TABLE playlist_tracks (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                        track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                        position    INTEGER NOT NULL
                    );
                    INSERT INTO playlist_tracks (id, playlist_id, track_id, position)
                        SELECT pt.id, pt.playlist_id, t.id, pt.position
                        FROM playlist_tracks_old pt
                        JOIN tracks t ON t.file_path = pt.file_path;
                    DROP TABLE playlist_tracks_old;
                """)
            self._conn.execute("UPDATE schema_version SET version = 30")
            self._conn.commit()
            version = 30

        if version == 30:
            # v30 → v31: add last_played_at to playlists (KAMP-450).
            # Guard: _DDL already includes last_played_at on fresh installs that
            # ran v28→v29 in the same session (playlists table created with the
            # updated schema), so skip the ALTER if the column is already present.
            pl_cols = {
                r[1]
                for r in self._conn.execute("PRAGMA table_info(playlists)").fetchall()
            }
            if "last_played_at" not in pl_cols:
                self._conn.execute(
                    "ALTER TABLE playlists ADD COLUMN last_played_at REAL"
                )
            self._conn.execute("UPDATE schema_version SET version = 31")
            self._conn.commit()
            version = 31

        if version == 31:
            # v31 → v32: add playlists_fts FTS5 table for playlist name search (KAMP-442).
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS playlists_fts USING fts5(
                    title, tokenize = 'unicode61'
                )
            """)
            self._conn.execute(
                "INSERT INTO playlists_fts(rowid, title) SELECT id, title FROM playlists"
            )
            self._conn.execute("UPDATE schema_version SET version = 32")
            self._conn.commit()
            version = 32

        if version == 32:
            # v32 → v33: add magic_playlist_criteria table (KAMP-459).
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS magic_playlist_criteria (
                    playlist_id   INTEGER PRIMARY KEY REFERENCES playlists(id) ON DELETE CASCADE,
                    criteria_json TEXT NOT NULL,
                    evaluated_at  REAL
                )
            """)
            self._conn.execute("UPDATE schema_version SET version = 33")
            self._conn.commit()
            version = 33

        if version == 33:
            # v33 → v34: cache evaluated track count in magic_playlist_criteria.
            # Guard with PRAGMA in case a fresh DB already has the column via DDL.
            cols = {
                r[1]
                for r in self._conn.execute(
                    "PRAGMA table_info(magic_playlist_criteria)"
                )
            }
            if "cached_track_count" not in cols:
                self._conn.execute(
                    "ALTER TABLE magic_playlist_criteria"
                    " ADD COLUMN cached_track_count INTEGER"
                )
            self._conn.execute("UPDATE schema_version SET version = 34")
            self._conn.commit()
            version = 34

        if version == 34:
            # v34 → v35: add display override columns for streaming albums/tracks (KAMP-467).
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            for col in ("display_title", "display_album", "display_album_artist"):
                if col not in track_cols:
                    self._conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} TEXT")
            album_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(albums)").fetchall()
            }
            for col in ("display_album", "display_album_artist"):
                if col not in album_cols:
                    self._conn.execute(f"ALTER TABLE albums ADD COLUMN {col} TEXT")
            self._conn.execute("UPDATE schema_version SET version = 35")
            self._conn.commit()
            version = 35

        if version == 35:
            # v35 → v36: add artists table (KAMP-258).
            # Creates artists from distinct album_artist values, adds artist_id FK
            # column to albums, and backfills historical play_time from play_count * duration.
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS artists (
                    id        INTEGER PRIMARY KEY,
                    name      TEXT NOT NULL UNIQUE,
                    play_time REAL NOT NULL DEFAULT 0
                )
                """)
            self._conn.execute("""
                INSERT OR IGNORE INTO artists (name)
                SELECT DISTINCT album_artist FROM albums
                WHERE album_artist != '' AND album_artist != 'Various Artists'
                """)
            album_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(albums)").fetchall()
            }
            if "artist_id" not in album_cols:
                self._conn.execute("ALTER TABLE albums ADD COLUMN artist_id INTEGER")
            self._conn.execute(
                "UPDATE albums SET artist_id = (SELECT id FROM artists WHERE name = album_artist)"
            )
            self._conn.execute("""
                UPDATE artists SET play_time = (
                    SELECT COALESCE(SUM(t.play_count * t.duration), 0)
                    FROM tracks t JOIN albums a ON t.album_id = a.id
                    WHERE a.album_artist = artists.name
                )
                """)
            self._conn.execute("UPDATE schema_version SET version = 36")
            self._conn.commit()
            version = 36

        if version == 36:
            # v36 → v37: change queue_state.repeat from INTEGER to TEXT so repeat
            # mode can hold "off" | "queue" | "album" | "single" (KAMP-510).
            # SQLite does not support ALTER COLUMN, so we recreate the table.
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS queue_state_new (
                    id         INTEGER PRIMARY KEY CHECK (id = 1),
                    tracks     TEXT    NOT NULL,
                    order_json TEXT    NOT NULL DEFAULT '',
                    pos        INTEGER NOT NULL DEFAULT -1,
                    shuffle    INTEGER NOT NULL DEFAULT 0,
                    repeat     TEXT    NOT NULL DEFAULT 'off'
                );
                INSERT OR IGNORE INTO queue_state_new (id, tracks, order_json, pos, shuffle, repeat)
                SELECT id, tracks, order_json, pos, shuffle,
                       CASE WHEN repeat = 0 THEN 'off' ELSE 'queue' END
                FROM queue_state;
                DROP TABLE queue_state;
                ALTER TABLE queue_state_new RENAME TO queue_state;
            """)
            self._conn.execute("UPDATE schema_version SET version = 37")
            self._conn.commit()
            version = 37

        if version < 38:
            # v37 → v38: rename year → release_date (KAMP-513).
            # Store full date strings (e.g. "2023-03-15") rather than truncated
            # 4-digit years, enabling precise sort and display.
            # Guard: _DDL creates both tables with release_date for fresh databases,
            # so only rename if the old year column actually exists.
            _tracks_cols = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "year" in _tracks_cols:
                self._conn.execute(
                    "ALTER TABLE tracks RENAME COLUMN year TO release_date"
                )
            _albums_cols = {
                row[1]
                for row in self._conn.execute("PRAGMA table_info(albums)").fetchall()
            }
            if "year" in _albums_cols:
                self._conn.execute(
                    "ALTER TABLE albums RENAME COLUMN year TO release_date"
                )
            # Null file_mtime for local tracks whose release_date is year-only
            # (≤ 4 chars) so the next scan re-reads their tags from disk and
            # picks up the full ISO date (e.g. "2023-03-15") without requiring
            # any user action.
            self._conn.execute(
                "UPDATE tracks SET file_mtime = NULL"
                " WHERE source = 'local' AND length(release_date) <= 4"
            )
            self._conn.execute("UPDATE schema_version SET version = 38")
            self._conn.commit()
            version = 38

        if version < 39:
            # v38 → v39: heal albums forked by tag divergence (KAMP-523) and
            # enforce one album per sale_item_id.
            #
            # Only the *provably-same-release* forks are merged automatically:
            #   (a) two album rows carrying the same non-null sale_item_id, and
            #   (b) a source='local' row whose TRIM/NOCASE (album_artist, album)
            #       collapses onto a sale_item_id-bearing row AND matches that
            #       id's bandcamp_collection band/title.
            # Ambiguous string-only forks (no sale_item_id on either side) are
            # LEFT UNTOUCHED — auto-merging them risks collapsing genuinely
            # distinct releases (self-titled EPs, VA comps). A one-time DB backup
            # is taken before any merge, and every merge is logged at INFO.
            #
            # Defense in depth: a heal failure must never brick the DB. If any
            # merge raises unexpectedly, roll the heal back and continue — the
            # fork stays un-healed (identity linking still resolves it at runtime
            # via LIMIT 1) but the database always opens.
            try:
                self._heal_forked_albums()
            except Exception:
                self._conn.rollback()
                logger.exception(
                    "KAMP-523 heal failed — rolled back; forks left for runtime"
                    " identity linking to resolve"
                )
            self._create_sale_item_id_unique_index()
            self._conn.execute("UPDATE schema_version SET version = 39")
            self._conn.commit()
            version = 39

        if version < 40:
            # v39 → v40: heal albums orphaned by filesystem deletion of their
            # tracks (KAMP-522). Before the scan learned to prune, deleting an
            # album folder left a zero-track ghost row behind; sweep those once
            # on upgrade. Inlined (not via prune_empty_albums) so the migration's
            # behaviour stays frozen if that method later changes. The
            # sale_item_id guard preserves Bandcamp preorder/streaming rows.
            album_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(albums)").fetchall()
            }
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            # Real DBs gain these columns in the v24 albums migration; only the
            # narrow migration unit tests build a partial schema without them.
            if "sale_item_id" in album_cols and "album_id" in track_cols:
                self._conn.execute(
                    "DELETE FROM albums WHERE sale_item_id IS NULL"
                    " AND NOT EXISTS"
                    " (SELECT 1 FROM tracks WHERE tracks.album_id = albums.id)"
                )
            self._conn.execute("UPDATE schema_version SET version = 40")
            self._conn.commit()
            version = 40

        if version < 41:
            # v40 → v41: add track-level Bandcamp provenance (KAMP-528). Standalone
            # singles have no album row, so albums.sale_item_id cannot record their
            # origin; tracks.sale_item_id can. The KAMP_SALE_ITEM_ID file tag is
            # already read into Track.sale_item_id — this column lets it persist.
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "sale_item_id" not in track_cols:
                # Default is NULL, so SQLite accepts an added REFERENCES column
                # even under foreign_keys=ON (a non-NULL default would be rejected).
                self._conn.execute(
                    "ALTER TABLE tracks ADD COLUMN sale_item_id TEXT"
                    " REFERENCES bandcamp_collection(sale_item_id)"
                )
            self._create_tracks_sale_item_id_index()
            # The backfill/re-read below touch albums.sale_item_id, tracks.album_id
            # and tracks.source. Real DBs gain all of these by v24/v20; only the
            # narrow migration unit tests build a partial schema without them, so
            # guard on presence (mirrors the v40 block).
            album_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(albums)").fetchall()
            }
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if "sale_item_id" in album_cols and "album_id" in track_cols:
                # Backfill album → track: copy each provenanced album's sale_item_id
                # down onto its tracks. FK-safe (album sids already satisfy the
                # albums→collection FK) and idempotent (only fills NULLs).
                self._conn.execute("""
                    UPDATE tracks SET sale_item_id = (
                        SELECT a.sale_item_id FROM albums a WHERE a.id = tracks.album_id
                    )
                    WHERE album_id IS NOT NULL
                      AND sale_item_id IS NULL
                      AND (SELECT a.sale_item_id FROM albums a
                           WHERE a.id = tracks.album_id) IS NOT NULL
                    """)
            # A pre-existing standalone single (no album row) can't be reached by the
            # backfill above. It is not force-re-read here: nulling file_mtime for all
            # album-less local tracks would re-scan every loose file for the sake of a
            # rare historical case. Such singles recover via link_track_to_sale_item_id
            # or a natural re-scan; newly downloaded singles are always scanned fresh
            # and stamped by upsert_many.
            self._conn.execute("UPDATE schema_version SET version = 41")
            self._conn.commit()
            version = 41

        if version < 42:
            # v41 → v42: re-link un-provenanced loose local singles to their
            # streaming single-album (KAMP-529). A pre-existing standalone single
            # (empty album tag, no album row) duplicates the streaming single kamp
            # indexes for the same purchase. upsert_many Step 3c handles this going
            # forward, but an unchanged file is never re-scanned, so heal the rows
            # already on disk once. Guarded on the columns the helper needs (the
            # narrow migration-unit-test schemas build a partial tracks table).
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if {"album_id", "source", "album", "album_artist", "title"} <= track_cols:
                has_loose = self._conn.execute(
                    "SELECT 1 FROM tracks WHERE source = 'local' AND album_id IS NULL"
                    " AND TRIM(album) = '' AND TRIM(album_artist) != ''"
                    " AND TRIM(title) != '' LIMIT 1"
                ).fetchone()
                # Back up only when there is something to touch (mirrors the v39
                # heal); abort the heal if the snapshot fails.
                if has_loose and self._backup_db("KAMP-529 heal"):
                    touched = self._attach_loose_local_singles()
                    if touched:
                        # Recompute album source/aggregates and rebuild FTS so the
                        # grid and search immediately reflect the attached tracks —
                        # the scan path gets this via upsert_many, the migration
                        # must do it explicitly.
                        self._refresh_album_aggregates(list(touched))
                        self._rebuild_fts()
                    logger.info(
                        "KAMP-529 heal: re-linked loose local singles into"
                        " %d album(s)",
                        len(touched),
                    )
            self._conn.execute("UPDATE schema_version SET version = 42")
            self._conn.commit()
            version = 42

        if version < 43:
            # v42 → v43: an earlier v42 build attached loose singles by setting
            # album_id but left their album tag empty, so they kept rendering as
            # duplicate "missing album" grid cards (albums() keys that branch on
            # album='') and their album source was never refreshed. A DB already
            # advanced to 42 by that build is gated out of the fixed v42 heal, so
            # re-stamp the album name onto any local track carrying an album_id but
            # no album tag, then refresh aggregates. A DB healed correctly by the
            # fixed v42 (or a fresh upgrade) has no such rows and this is a no-op.
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if {"album_id", "album", "album_artist", "source"} <= track_cols:
                touched = [
                    r[0]
                    for r in self._conn.execute(
                        "SELECT DISTINCT t.album_id FROM tracks t"
                        " JOIN albums a ON a.id = t.album_id"
                        " WHERE t.source = 'local' AND t.album_id IS NOT NULL"
                        " AND TRIM(t.album) = '' AND TRIM(a.album) != ''"
                    ).fetchall()
                ]
                if touched and self._backup_db("KAMP-529 v43 heal"):
                    self._conn.execute(
                        "UPDATE tracks SET"
                        " album = (SELECT a.album FROM albums a WHERE a.id = tracks.album_id),"
                        " album_artist = (SELECT a.album_artist FROM albums a WHERE a.id = tracks.album_id)"
                        " WHERE source = 'local' AND album_id IS NOT NULL"
                        " AND TRIM(album) = ''"
                        " AND (SELECT TRIM(a.album) FROM albums a WHERE a.id = tracks.album_id) != ''"
                    )
                    self._refresh_album_aggregates(touched)
                    self._rebuild_fts()
                    logger.info(
                        "KAMP-529 v43 heal: re-stamped album name on attached"
                        " singles in %d album(s)",
                        len(touched),
                    )
            self._conn.execute("UPDATE schema_version SET version = 43")
            self._conn.commit()
            version = 43

        if version < 44:
            # v43 → v44: canonical-track model, expand phase (KAMP-535, epic
            # KAMP-533). track_sources and track_stats are created empty by _DDL
            # (executescript runs it before this block, and on a fresh DB the
            # row-is-None branch stamps the current version so no block runs) —
            # both fresh installs and upgrades converge on the empty tables. This
            # block only bumps the version; there is deliberately NO backfill (the
            # v7/v8 no-op pattern). KAMP-536 populates the tables from the still-
            # authoritative tracks columns and switches reads over. A backfill here
            # would be thrown away by that collapse and would crash the narrow
            # migration tests, which build a partial tracks table.
            self._conn.execute("UPDATE schema_version SET version = 44")
            self._conn.commit()
            version = 44

        if version < 45:
            # v44 → v45: populate track_sources/track_stats from the authoritative
            # tracks columns (KAMP-540). Additive and behavior-neutral — nothing
            # reads the children yet (KAMP-542 flips reads). Guarded on the columns
            # the backfill reads, since the narrow migration-unit-test schemas build
            # a partial tracks table; when they're absent the tables just stay empty
            # and we still bump the version. No backup: the backfill only INSERTs
            # into the (empty) child tables and never touches tracks, so it is
            # undone by dropping the child rows. Wrapped in try/except so a
            # pathological uri collision can't brick the DB — the children are left
            # for KAMP-541's collapse (which re-derives from tracks) to populate.
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            needed = {
                "id",
                "file_path",
                "source",
                "sale_item_id",
                "favorite",
                "play_count",
                "last_played",
                "ext",
                "duration",
                "embedded_art",
                "file_mtime",
                "is_available",
                "stream_url",
                "stream_url_expires_at",
            }
            if needed <= track_cols:
                try:
                    self._backfill_canonical_children()
                except Exception:
                    self._conn.rollback()
                    logger.exception(
                        "KAMP-540 v45 backfill failed — rolled back; children left"
                        " for KAMP-541 to populate"
                    )
            self._conn.execute("UPDATE schema_version SET version = 45")
            self._conn.commit()
            version = 45

        if version < 46:
            # v45 → v46: collapse each track's streaming+local sibling rows into
            # ONE canonical tracks row (KAMP-541, design §7). Irreversible — take a
            # backup first (mirrors the v39 heal), run the whole collapse in one
            # transaction, and bump the version LAST so a crash rolls back cleanly.
            # Guarded on the columns the bucketing reads (narrow migration-unit-test
            # schemas build a partial tracks table). On any failure, roll back and
            # leave the two-row model intact rather than brick the DB.
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            needed = {
                "id",
                "album_id",
                "track_number",
                "disc_number",
                "sale_item_id",
                "source",
            }
            # Only back up + collapse when there is actually a sibling pair to
            # merge — a library with no downloaded-streaming duplicates needs
            # neither the backup nor the work.
            if (
                needed <= track_cols
                and self._has_collapsible_siblings()
                and self._backup_db("KAMP-541 v46 collapse")
            ):
                try:
                    self._collapse_canonical_tracks()
                except Exception:
                    self._conn.rollback()
                    logger.exception(
                        "KAMP-541 v46 collapse failed — rolled back; the two-row"
                        " model is preserved. Restore from the pre-v46 backup if"
                        " needed."
                    )
            self._conn.execute("UPDATE schema_version SET version = 46")
            self._conn.commit()
            version = 46

        if version < 47:
            # v46 → v47: heal album art lost by the KAMP-541 collapse. The collapse
            # kept the streaming survivor's embedded_art=0 / file_mtime=NULL instead
            # of the downloaded file's, so downloaded-and-streamed albums (art comes
            # from tracks.embedded_art / file_mtime aggregates) showed no cover.
            # Re-sync every track's legacy columns from its preferred source (now
            # including embedded_art + file_mtime) and refresh album art. Gated on
            # the exact symptom so a clean DB does no work + takes no backup.
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            if {"embedded_art", "file_mtime", "file_path", "source"} <= track_cols:
                symptom = self._conn.execute(
                    "SELECT 1 FROM tracks t"
                    " JOIN track_sources s ON s.track_id = t.id AND s.kind = 'file'"
                    " WHERE s.embedded_art != t.embedded_art LIMIT 1"
                ).fetchone()
                if symptom is not None and self._backup_db("KAMP-541 v47 art heal"):
                    try:
                        self._heal_collapse_art()
                    except Exception:
                        self._conn.rollback()
                        logger.exception(
                            "KAMP-541 v47 art heal failed — rolled back; album art"
                            " may still be missing on collapsed albums."
                        )
            self._conn.execute("UPDATE schema_version SET version = 47")
            self._conn.commit()
            version = 47

        if version < 48:
            # v47 → v48: heal stream-only duplicate rows the bandcamp sync forked
            # for already-downloaded albums. Before the symmetric _reconcile_scanned_
            # tracks fix, a stream synced for a download-only album (its bandcamp://
            # tracks row was absent, so has_remote_album_tracks returned False) was
            # upserted as a SEPARATE stream-only row beside the local canonical,
            # re-creating the KAMP-532 split. Re-running the collapse merges each
            # such pair (survivor = the local file row) — idempotent, and a no-op on
            # a library with no sibling buckets. Same backup-first / one-transaction
            # / version-last discipline as v46.
            track_cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            needed = {
                "id",
                "album_id",
                "track_number",
                "disc_number",
                "sale_item_id",
                "source",
            }
            if (
                needed <= track_cols
                and self._has_collapsible_siblings()
                and self._backup_db("KAMP-541 v48 sync-fork heal")
            ):
                try:
                    self._collapse_canonical_tracks()
                except Exception:
                    self._conn.rollback()
                    logger.exception(
                        "KAMP-541 v48 sync-fork heal failed — rolled back; the"
                        " duplicate stream rows remain. Restore from the pre-v48"
                        " backup if needed."
                    )
            self._conn.execute("UPDATE schema_version SET version = 48")
            self._conn.commit()
            version = 48  # noqa: F841

    def _create_tracks_with_stats_view(self) -> None:
        """(Re)create the read-only ``tracks_with_stats`` view (KAMP-542).

        Projects every ``tracks`` column but shadows ``favorite``/``play_count``/
        ``last_played`` from ``track_stats`` — the authoritative store since the
        KAMP-540 dual-write — so read paths resolve stats from the child table and
        KAMP-539 can drop the legacy columns. While both coexist the shadow
        COALESCEs back to the legacy column, so a track that somehow lacks a
        ``track_stats`` row still reads its real value rather than a spurious 0;
        once 539 drops the legacy columns the fallback becomes the type default.

        Built from ``PRAGMA table_info`` and recreated on every open so it always
        matches the current ``tracks`` schema (survives 539's column drop with no
        edit here). Read-only by construction: every writer (UPDATE/INSERT/upsert,
        the ``tracks_fts`` rowid join, the stat mirrors) stays on the base table,
        whose ``id`` the view preserves so FTS rowid joins still resolve.
        """
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(tracks)")]
        # column -> SQL default used once the legacy column is dropped (KAMP-539).
        stats_default = {"favorite": "0", "play_count": "0", "last_played": "NULL"}
        projected = [f"t.{c}" for c in cols if c not in stats_default]
        for name, default in stats_default.items():
            fallback = f"t.{name}" if name in cols else default
            projected.append(f"COALESCE(ts.{name}, {fallback}) AS {name}")
        self._conn.executescript(
            "DROP VIEW IF EXISTS tracks_with_stats;\n"
            "CREATE VIEW tracks_with_stats AS SELECT "
            + ", ".join(projected)
            + " FROM tracks t LEFT JOIN track_stats ts ON ts.track_id = t.id;"
        )
        self._conn.commit()

    def _backup_db(self, label: str) -> bool:
        """Snapshot the live DB (incl. WAL) before a mutating heal; return success.

        Connection.backup copies consistently where a plain file copy could miss
        un-checkpointed WAL. Best-effort: on failure, log and return False so the
        caller can abort the mutation rather than run it without a restore point.
        """
        ts = _time.strftime("%Y%m%d-%H%M%S")
        backup_path = self._db_path.with_name(f"{self._db_path.name}.bak-{ts}")
        try:
            dest = sqlite3.connect(str(backup_path))
            with dest:
                self._conn.backup(dest)
            dest.close()
            logger.info("%s: backed up library to %s", label, backup_path)
            return True
        except Exception as exc:  # pragma: no cover - backup is best-effort
            logger.warning("%s: backup failed (%s); skipping heal", label, exc)
            return False

    def _backfill_canonical_children(self) -> None:
        """Populate track_sources/track_stats from existing tracks rows (KAMP-540).

        One track_sources row and one track_stats row per tracks row that has no
        source row yet (the NOT EXISTS guard makes re-runs a no-op). Derives the
        source fields via _derive_source_fields so the migration and the scan path
        produce identical rows. Additive only — never mutates tracks.
        """
        rows = self._conn.execute(
            "SELECT id, file_path, source, sale_item_id, ext, duration,"
            " embedded_art, file_mtime, is_available, stream_url,"
            " stream_url_expires_at, favorite, play_count, last_played"
            " FROM tracks"
            " WHERE NOT EXISTS"
            " (SELECT 1 FROM track_sources s WHERE s.track_id = tracks.id)"
        ).fetchall()
        source_params: list[tuple[Any, ...]] = []
        stats_params: list[tuple[Any, ...]] = []
        for r in rows:
            uri, kind, provider, provider_item_id = _derive_source_fields(
                r["file_path"], r["source"], r["sale_item_id"]
            )
            source_params.append(
                (
                    r["id"],
                    kind,
                    provider,
                    provider_item_id,
                    uri,
                    r["ext"],
                    r["duration"],
                    r["embedded_art"],
                    r["file_mtime"],
                    r["is_available"],
                    r["stream_url"],
                    r["stream_url_expires_at"],
                )
            )
            stats_params.append(
                (r["id"], r["favorite"], r["play_count"], r["last_played"])
            )
        if source_params:
            self._conn.executemany(
                "INSERT INTO track_sources"
                " (track_id, kind, provider, provider_item_id, uri, ext, duration,"
                "  embedded_art, file_mtime, is_available, stream_url,"
                "  stream_url_expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                source_params,
            )
        if stats_params:
            self._conn.executemany(
                "INSERT OR IGNORE INTO track_stats"
                " (track_id, favorite, play_count, last_played)"
                " VALUES (?, ?, ?, ?)",
                stats_params,
            )
        if source_params:
            logger.info(
                "KAMP-540 v45 backfill: populated %d track_sources/track_stats rows",
                len(source_params),
            )

    # ------------------------------------------------------------------
    # KAMP-541 canonical-track collapse (shared by the v46 migration and the
    # scan-time reconcile in upsert_many)
    # ------------------------------------------------------------------

    def _ensure_track_source(self, track_id: int) -> None:
        """Derive a track_sources row from the tracks columns if one is missing.

        Honours the KAMP-540 hand-off invariant: a track scanned before 540 (or
        whose 540 backfill was skipped) may have no source row. Never bare-merge
        assuming it exists. Skips on a uri-UNIQUE collision (slash-form dup).
        """
        if self._conn.execute(
            "SELECT 1 FROM track_sources WHERE track_id = ? LIMIT 1", (track_id,)
        ).fetchone():
            return
        r = self._conn.execute(
            "SELECT file_path, source, sale_item_id, ext, duration, embedded_art,"
            " file_mtime, is_available, stream_url, stream_url_expires_at"
            " FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if r is None:
            return
        uri, kind, provider, provider_item_id = _derive_source_fields(
            r["file_path"], r["source"], r["sale_item_id"]
        )
        if self._conn.execute(
            "SELECT 1 FROM track_sources WHERE uri = ?", (uri,)
        ).fetchone():
            return  # slash-form dup already present under another track — skip
        self._conn.execute(
            "INSERT INTO track_sources (track_id, kind, provider, provider_item_id,"
            " uri, ext, duration, embedded_art, file_mtime, is_available, stream_url,"
            " stream_url_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                track_id,
                kind,
                provider,
                provider_item_id,
                uri,
                r["ext"],
                r["duration"],
                r["embedded_art"],
                r["file_mtime"],
                r["is_available"],
                r["stream_url"],
                r["stream_url_expires_at"],
            ),
        )

    def _repoint_track_refs(self, loser_id: int, survivor_id: int) -> None:
        """Repoint every reference from *loser_id* to *survivor_id* (KAMP-541).

        playlist_tracks (no UNIQUE — dedupe within a playlist by hand), deferred_ops
        (UNIQUE track_id — keep the survivor's op, drop the loser's), and the id-
        keyed player_state / queue_state (KAMP-536). Callers commit.
        """
        import json

        c = self._conn
        # playlist_tracks: drop the loser's entry in any playlist that already
        # holds the survivor (would otherwise become a phantom duplicate — no
        # UNIQUE constraint to catch it), then repoint the rest. Position gaps are
        # harmless (reads ORDER BY position).
        c.execute(
            "DELETE FROM playlist_tracks WHERE track_id = ? AND playlist_id IN"
            " (SELECT playlist_id FROM playlist_tracks WHERE track_id = ?)",
            (loser_id, survivor_id),
        )
        c.execute(
            "UPDATE playlist_tracks SET track_id = ? WHERE track_id = ?",
            (survivor_id, loser_id),
        )
        # deferred_ops: UNIQUE(track_id) — a blind UPDATE would abort if both have
        # a pending op. Keep the survivor's, drop the loser's; else repoint.
        if c.execute(
            "SELECT 1 FROM deferred_ops WHERE track_id = ?", (survivor_id,)
        ).fetchone():
            c.execute("DELETE FROM deferred_ops WHERE track_id = ?", (loser_id,))
        else:
            c.execute(
                "UPDATE deferred_ops SET track_id = ? WHERE track_id = ?",
                (survivor_id, loser_id),
            )
        # player_state.track_path stores the id as text (KAMP-536).
        c.execute(
            "UPDATE player_state SET track_path = ? WHERE track_path = ?",
            (str(survivor_id), str(loser_id)),
        )
        # queue_state.tracks is a JSON array of ids (KAMP-536).
        qrow = c.execute("SELECT tracks FROM queue_state WHERE id = 1").fetchone()
        if qrow is not None:
            try:
                ids = json.loads(qrow["tracks"])
            except (ValueError, TypeError):
                ids = []
            if isinstance(ids, list) and loser_id in ids:
                ids = [survivor_id if x == loser_id else x for x in ids]
                c.execute(
                    "UPDATE queue_state SET tracks = ? WHERE id = 1", (json.dumps(ids),)
                )

    def _merge_track_into(self, survivor_id: int, loser_id: int) -> None:
        """Merge the loser track row into the survivor (KAMP-541, design §7).

        Both must be the same logical track. Combines stats onto the survivor's
        tracks columns (MAX/MAX/latest — so reads still on those columns converge,
        fixing KAMP-532), COALESCE-merges identity (streaming display_* win;
        non-empty mb/genre/label win), re-parents track_sources, repoints refs,
        deletes the loser, re-derives track_stats from the merged columns, and
        realigns the survivor's legacy columns to its preferred source. Caller
        owns the transaction/commit.
        """
        c = self._conn
        self._ensure_track_source(survivor_id)
        self._ensure_track_source(loser_id)
        # Stats onto the survivor's tracks columns.
        c.execute(
            "UPDATE tracks SET"
            " favorite = MAX(favorite, COALESCE((SELECT favorite FROM tracks WHERE id=?),0)),"
            " play_count = MAX(play_count, COALESCE((SELECT play_count FROM tracks WHERE id=?),0)),"
            " last_played = NULLIF(MAX(COALESCE(last_played,0),"
            "     COALESCE((SELECT last_played FROM tracks WHERE id=?),0)), 0)"
            " WHERE id=?",
            (loser_id, loser_id, loser_id, survivor_id),
        )
        # Identity: streaming display_* win if the survivor's is NULL; non-empty
        # mb/genre/label fill a blank survivor field from the loser.
        c.execute(
            "UPDATE tracks SET"
            " mb_release_id = CASE WHEN mb_release_id != '' THEN mb_release_id"
            "   ELSE (SELECT mb_release_id FROM tracks WHERE id=?) END,"
            " mb_recording_id = CASE WHEN mb_recording_id != '' THEN mb_recording_id"
            "   ELSE (SELECT mb_recording_id FROM tracks WHERE id=?) END,"
            " genre = CASE WHEN genre != '' THEN genre"
            "   ELSE (SELECT genre FROM tracks WHERE id=?) END,"
            " label = CASE WHEN label != '' THEN label"
            "   ELSE (SELECT label FROM tracks WHERE id=?) END,"
            " display_title = COALESCE(display_title, (SELECT display_title FROM tracks WHERE id=?)),"
            " display_album = COALESCE(display_album, (SELECT display_album FROM tracks WHERE id=?)),"
            " display_album_artist = COALESCE(display_album_artist,"
            "   (SELECT display_album_artist FROM tracks WHERE id=?))"
            " WHERE id=?",
            (
                loser_id,
                loser_id,
                loser_id,
                loser_id,
                loser_id,
                loser_id,
                loser_id,
                survivor_id,
            ),
        )
        # Re-parent the loser's sources onto the survivor.
        c.execute(
            "UPDATE track_sources SET track_id = ? WHERE track_id = ?",
            (survivor_id, loser_id),
        )
        self._repoint_track_refs(loser_id, survivor_id)
        # Delete the loser (its sources are re-parented; its stats cascade away).
        c.execute("DELETE FROM tracks WHERE id = ?", (loser_id,))
        # Re-derive the survivor's track_stats from the merged tracks columns.
        c.execute(
            "INSERT INTO track_stats (track_id, favorite, play_count, last_played)"
            " SELECT id, favorite, play_count, last_played FROM tracks WHERE id=?"
            " ON CONFLICT(track_id) DO UPDATE SET favorite=excluded.favorite,"
            " play_count=excluded.play_count, last_played=excluded.last_played",
            (survivor_id,),
        )
        self._sync_tracks_row_to_preferred_source(survivor_id)

    def _reconcile_scanned_tracks(self, canonical_keys: "list[str]") -> None:
        """Attach a newly-upserted delivery to its existing canonical (KAMP-541).

        Symmetric in the two directions a fork can arrive:
          - a downloaded FILE for an album that was streamed (the file scanner's
            "download a streamed album" case), and
          - a synced STREAM for an album that was already downloaded (bandcamp
            sync bringing a stream in for the first time — otherwise upsert_many
            forks a stream-only row next to the local canonical, re-creating the
            KAMP-532 duplicate).

        For each just-upserted row, find the ONE sibling that lacks the kind this
        row supplies (a stream-only sibling for a file row; a file-only sibling
        for a stream row) and merge into it, keeping the INCUMBENT (the sibling
        that existed before this upsert batch) as survivor so live references
        stay valid. Matches by (album_id, track_number, disc_number) with
        agreeing sale_item_id, or by sale_item_id for a loose single; a >1 or
        ambiguous match is left alone (the migration quarantines those). Caller
        owns the commit.
        """
        for key in canonical_keys:
            row = self._conn.execute(
                "SELECT id, album_id, track_number, disc_number, sale_item_id"
                " FROM tracks WHERE file_path = ?",
                (key,),
            ).fetchone()
            if row is None:
                continue
            # The sibling must be missing the kind this row supplies, so the merge
            # yields a clean file+stream canonical rather than a duplicate kind.
            row_has_file = self._conn.execute(
                "SELECT 1 FROM track_sources WHERE track_id = ? AND kind = 'file'",
                (row["id"],),
            ).fetchone()
            missing_kind = "file" if row_has_file else "stream"
            no_kind_src = (
                " AND NOT EXISTS (SELECT 1 FROM track_sources s"
                f" WHERE s.track_id = t.id AND s.kind = '{missing_kind}')"
            )
            if row["album_id"] is not None:
                cands = self._conn.execute(
                    "SELECT t.id FROM tracks t WHERE t.id != ? AND t.album_id = ?"
                    " AND t.track_number = ? AND t.disc_number = ?"
                    " AND (t.sale_item_id IS NULL OR ? IS NULL OR t.sale_item_id = ?)"
                    + no_kind_src,
                    (
                        row["id"],
                        row["album_id"],
                        row["track_number"],
                        row["disc_number"],
                        row["sale_item_id"],
                        row["sale_item_id"],
                    ),
                ).fetchall()
            elif row["sale_item_id"]:
                cands = self._conn.execute(
                    "SELECT t.id FROM tracks t WHERE t.id != ? AND t.album_id IS NULL"
                    " AND t.sale_item_id = ?" + no_kind_src,
                    (row["id"], row["sale_item_id"]),
                ).fetchall()
            else:
                continue
            if len(cands) == 1:
                # *row* is the just-upserted delivery; *cands[0]* is the
                # pre-existing sibling. Keep the INCUMBENT as survivor (opposite
                # of the offline migration, which prefers the file row): a live
                # queue / open album view already references the incumbent's id
                # and uri, and the merge deletes the loser — so surviving the
                # incumbent keeps those references valid (preferred_source still
                # resolves the file for playback, and a stale bandcamp:// uri
                # still resolves via resolve_track_id). Content and art are
                # realigned onto the survivor by _sync_tracks_row_to_preferred_
                # source inside the merge, so nothing owned is lost.
                self._merge_track_into(cands[0]["id"], row["id"])

    def _heal_collapse_art(self) -> None:
        """Re-sync every track's legacy per-source columns from its preferred source.

        The KAMP-541 v46 collapse omitted embedded_art/file_mtime when realigning a
        merged track to its preferred (file) source, so downloaded-and-streamed
        albums lost their cover art. Re-running the (now-fixed) sync over every
        track restores embedded_art + file_mtime, then album aggregates are
        refreshed. Idempotent — a single-source track syncs to itself. Caller owns
        the transaction/commit.
        """
        ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT DISTINCT track_id FROM track_sources"
            ).fetchall()
        ]
        for tid in ids:
            self._sync_tracks_row_to_preferred_source(tid)
        album_ids = [
            r[0] for r in self._conn.execute("SELECT id FROM albums").fetchall()
        ]
        self._refresh_album_aggregates(album_ids)
        self._rebuild_fts()

    def _has_collapsible_siblings(self) -> bool:
        """True if any bucket would hold >1 tracks row (KAMP-541 pre-flight).

        Cheap existence check so the v46 collapse skips the backup + work on a
        library that has no downloaded-streaming duplicates. Over-triggers only on
        buckets that later quarantine — acceptable (a backup is never wrong).
        """
        return (
            self._conn.execute(
                "SELECT 1 WHERE"
                " EXISTS (SELECT 1 FROM tracks WHERE album_id IS NOT NULL"
                "   GROUP BY album_id, track_number, disc_number HAVING COUNT(*) > 1)"
                " OR EXISTS (SELECT 1 FROM tracks"
                "   WHERE album_id IS NULL AND sale_item_id IS NOT NULL"
                "   GROUP BY sale_item_id HAVING COUNT(*) > 1)"
            ).fetchone()
            is not None
        )

    def _collapse_canonical_tracks(self) -> None:
        """Merge every streaming+local sibling pair into one canonical track.

        Buckets sibling tracks rows (album rows by (album_id, track_number,
        disc_number) with agreeing sale_item_id; loose singles by sale_item_id
        only; everything else solo), quarantines ambiguous buckets (>2 rows, or a
        duplicate (provider,kind)), and merges each mergeable bucket into its
        survivor — the local file row if present, else the lowest id. Caller wraps
        this in a transaction + backup.
        """
        rows = self._conn.execute(
            "SELECT id, album_id, track_number, disc_number, sale_item_id, source"
            " FROM tracks"
        ).fetchall()

        def _kind(r: sqlite3.Row) -> str:
            return "file" if r["source"] == "local" else "stream"

        def _provider(r: sqlite3.Row) -> str:
            return (
                "bandcamp" if (r["source"] == "bandcamp" or r["sale_item_id"]) else ""
            )

        buckets: dict[tuple[Any, ...], list[sqlite3.Row]] = {}
        for r in rows:
            if r["album_id"] is None:
                key = (
                    ("sid", r["sale_item_id"])
                    if r["sale_item_id"]
                    else ("solo", r["id"])
                )
            else:
                key = ("album", r["album_id"], r["track_number"], r["disc_number"])
            buckets.setdefault(key, []).append(r)

        touched_album_ids: set[int] = set()
        quarantined = 0
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            distinct_sids = {r["sale_item_id"] for r in bucket if r["sale_item_id"]}
            pks = [(_provider(r), _kind(r)) for r in bucket]
            if len(bucket) > 2 or len(distinct_sids) > 1 or len(set(pks)) != len(pks):
                quarantined += 1
                continue
            # Survivor = the local file row if present, else the lowest id. The
            # file row already carries the content we keep (art, mtime, path, and
            # the user's tags), so the merge transplants less and the canonical
            # defaults to the owned copy's metadata rather than the stream's.
            ordered = sorted(bucket, key=lambda r: (r["source"] != "local", r["id"]))
            survivor_id = ordered[0]["id"]
            for r in ordered:
                if r["album_id"] is not None:
                    touched_album_ids.add(r["album_id"])
            for loser in ordered[1:]:
                self._merge_track_into(survivor_id, loser["id"])

        if touched_album_ids:
            self._refresh_album_aggregates(list(touched_album_ids))
        self._rebuild_fts()
        if quarantined:
            logger.warning(
                "KAMP-541 collapse: %d ambiguous bucket(s) quarantined (left"
                " un-merged; resolve manually)",
                quarantined,
            )

    def _create_tracks_sale_item_id_index(self) -> None:
        """Create the lookup index on tracks.sale_item_id (KAMP-528).

        Kept out of _DDL: executescript runs the whole _DDL before the v41
        migration adds the column, so a CREATE INDEX there would reference a
        not-yet-existing column on an upgrading DB. Guarded on column presence so
        the narrow migration unit tests (which build a partial schema) don't fail.
        """
        track_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        if "sale_item_id" not in track_cols:
            return
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS tracks_sale_item_id_idx"
            " ON tracks(sale_item_id)"
        )

    def _create_sale_item_id_unique_index(self) -> None:
        """Enforce one album per sale_item_id via a partial UNIQUE index (KAMP-523).

        Defensive: if a duplicate somehow survives the heal, log and continue
        rather than aborting the migration (identity linking still resolves
        deterministically via LIMIT 1) — a failed migration would make the DB
        unopenable, which is far worse than a missing guard index.
        """
        album_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(albums)").fetchall()
        }
        if "sale_item_id" not in album_cols:
            # Minimal/partial schema (only seen in narrow migration unit tests);
            # a real DB gains the column in the v24 albums migration.
            return
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS albums_sale_item_id_uidx"
                " ON albums(sale_item_id) WHERE sale_item_id IS NOT NULL"
            )
        except sqlite3.IntegrityError:  # pragma: no cover - defensive
            logger.warning(
                "Could not create UNIQUE index on albums.sale_item_id — duplicate"
                " sale_item_ids remain; identity linking falls back to"
                " deterministic LIMIT-1 resolution."
            )

    def _heal_forked_albums(self) -> None:
        """Merge albums forked by tag divergence, for the provably-same subset.

        See the v39 migration comment for the safety rationale. This backs the DB
        up before any merge and logs every merge at INFO. Generalizes the
        human-in-the-loop scripts/merge_forked_album.sh for the identity-provable
        pairs only.
        """
        album_cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(albums)").fetchall()
        }
        if "sale_item_id" not in album_cols:
            # Minimal/partial schema (narrow migration unit tests only); a real
            # DB gains sale_item_id in the v24 albums migration, so there is
            # nothing Bandcamp-linked to heal here.
            return

        merge_into: dict[int, int] = {}

        # (a) Multiple album rows carrying the same non-null sale_item_id.
        for r in self._conn.execute(
            "SELECT sale_item_id, GROUP_CONCAT(id) AS ids, COUNT(*) AS n"
            " FROM albums WHERE sale_item_id IS NOT NULL"
            " GROUP BY sale_item_id HAVING n > 1"
        ).fetchall():
            ids = sorted(int(x) for x in r["ids"].split(","))
            keep = ids[0]
            for drop in ids[1:]:
                merge_into[drop] = keep

        # (b) A row WITH NO sale_item_id of its own that normalizes onto a
        #     sale_item_id-bearing row whose collection band/title also matches —
        #     the classic whitespace fork (the old code minted an un-provenanced
        #     local album next to the streaming origin).
        #
        # `loc.sale_item_id IS NULL` is load-bearing: a row that carries its OWN
        # non-null id is a distinct release (Bandcamp ids are 1:1 with releases),
        # so it must never be folded into a different id's album — doing so would
        # collapse two genuinely-different purchases that merely share a name.
        # Guarded on the settled band_name/item_title columns (older
        # bandcamp_collection schemas predate them), mirroring the v24 migration.
        bcc_cols = {
            r[1]
            for r in self._conn.execute(
                "PRAGMA table_info(bandcamp_collection)"
            ).fetchall()
        }
        for r in (self._conn.execute("""
            SELECT loc.id AS drop_id, org.id AS keep_id
            FROM albums loc
            JOIN albums org
              ON org.sale_item_id IS NOT NULL
             AND loc.id != org.id
             AND TRIM(loc.album_artist) = TRIM(org.album_artist) COLLATE NOCASE
             AND TRIM(loc.album)        = TRIM(org.album)        COLLATE NOCASE
            JOIN bandcamp_collection bc ON bc.sale_item_id = org.sale_item_id
            WHERE loc.sale_item_id IS NULL
              AND TRIM(loc.album_artist) = TRIM(bc.band_name)  COLLATE NOCASE
              AND TRIM(loc.album)        = TRIM(bc.item_title) COLLATE NOCASE
            """).fetchall() if {"band_name", "item_title"} <= bcc_cols else []):
            keep, drop = r["keep_id"], r["drop_id"]
            if drop != keep and drop not in merge_into:
                merge_into[drop] = keep

        if not merge_into:
            return

        # Back up before mutating. Connection.backup copies the live DB (incl.
        # WAL) consistently — a plain file copy could miss un-checkpointed WAL.
        ts = _time.strftime("%Y%m%d-%H%M%S")
        backup_path = self._db_path.with_name(f"{self._db_path.name}.bak-{ts}")
        try:
            dest = sqlite3.connect(str(backup_path))
            with dest:
                self._conn.backup(dest)
            dest.close()
            logger.info("KAMP-523 heal: backed up library to %s", backup_path)
        except Exception as exc:  # pragma: no cover - backup is best-effort
            logger.warning("KAMP-523 heal: backup failed (%s); aborting heal", exc)
            return

        for drop, keep in merge_into.items():
            self._merge_album_pair(keep, drop)
        logger.info("KAMP-523 heal: merged %d forked album row(s)", len(merge_into))

    def _merge_album_pair(self, keep: int, drop: int) -> None:
        """Fold album *drop* into *keep*, merging the duplicate artist too.

        Mirrors scripts/merge_forked_album.sh: move tracks, merge artists
        (preserving play_time), delete the orphan album, normalize the surviving
        names to their trimmed canonical form (so the pair can't re-fork on
        whitespace), and recompute albums.source.
        """
        keep_row = self._conn.execute(
            "SELECT album_artist, album, artist_id FROM albums WHERE id=?", (keep,)
        ).fetchone()
        drop_row = self._conn.execute(
            "SELECT artist_id FROM albums WHERE id=?", (drop,)
        ).fetchone()
        if keep_row is None or drop_row is None:  # pragma: no cover - defensive
            return
        canon = keep_row["album_artist"].strip()
        keep_album = keep_row["album"]
        keep_artist = keep_row["artist_id"]
        drop_artist = drop_row["artist_id"]

        logger.info(
            "KAMP-523 heal: merging album %d into %d (canonical artist %r)",
            drop,
            keep,
            canon,
        )

        # 1. Move the drop album's tracks onto keep.
        self._conn.execute(
            "UPDATE tracks SET album_id=? WHERE album_id=?", (keep, drop)
        )
        # 2. Delete the now-empty duplicate album.
        self._conn.execute("DELETE FROM albums WHERE id=?", (drop,))
        # 3. Merge the duplicate artist: fold play_time into the survivor,
        #    repoint album refs, delete the loser if nothing else references it.
        if keep_artist and drop_artist and keep_artist != drop_artist:
            self._conn.execute(
                "UPDATE artists SET play_time = play_time"
                " + (SELECT IFNULL(play_time,0) FROM artists WHERE id=?)"
                " WHERE id=?",
                (drop_artist, keep_artist),
            )
            self._conn.execute(
                "UPDATE albums SET artist_id=? WHERE artist_id=?",
                (keep_artist, drop_artist),
            )
            self._conn.execute(
                "DELETE FROM artists WHERE id=?"
                " AND NOT EXISTS (SELECT 1 FROM albums WHERE artist_id=?)",
                (drop_artist, drop_artist),
            )
        # 4. Normalize the surviving names to canonical (trimmed) form so the
        #    whitespace divergence that forked them cannot recur — BUT only if no
        #    other album already occupies the (canon, keep_album) slot. A distinct
        #    release that legitimately shares this name (different sale_item_id)
        #    would otherwise trip UNIQUE(album_artist, album) and abort the whole
        #    migration, bricking the DB. When a collision looms we keep the
        #    current name; the sale_item_id link preserves identity regardless.
        collision = self._conn.execute(
            "SELECT 1 FROM albums WHERE album_artist=? AND album=? AND id!=?",
            (canon, keep_album, keep),
        ).fetchone()
        if collision is None and canon != keep_row["album_artist"]:
            self._conn.execute(
                "UPDATE albums SET album_artist=? WHERE id=?", (canon, keep)
            )
            self._conn.execute(
                "UPDATE tracks SET album_artist=?, artist=? WHERE album_id=?",
                (canon, canon, keep),
            )
            if keep_artist:
                self._conn.execute(
                    "UPDATE artists SET name=? WHERE id=?"
                    " AND NOT EXISTS (SELECT 1 FROM artists WHERE name=? AND id!=?)",
                    (canon, keep_artist, canon, keep_artist),
                )
            # Keep bandcamp_collection.band_name consistent with the canonical name.
            self._conn.execute(
                "UPDATE bandcamp_collection SET band_name=?"
                " WHERE sale_item_id = (SELECT sale_item_id FROM albums WHERE id=?)"
                "   AND sale_item_id IS NOT NULL",
                (canon, keep),
            )
        elif collision is not None:
            logger.warning(
                "KAMP-523 heal: album %d kept name %r (canonical %r collides with a"
                " distinct release) — merge completed without rename",
                keep,
                keep_row["album_artist"],
                canon,
            )
        # 6. Recompute albums.source from the surviving tracks.
        self._conn.execute(
            f"UPDATE albums SET source = {_ALBUM_SOURCE_SUBQUERY} WHERE id=?",
            (keep,),
        )

    def _rebuild_fts(self) -> None:
        """Rebuild the FTS index from the current contents of the tracks table.

        Uses COALESCE with display override columns when they exist (v35+).
        Falls back to the canonical columns during early migration runs where
        the display columns have not yet been added.
        """
        cols = {
            r[1] for r in self._conn.execute("PRAGMA table_info(tracks)").fetchall()
        }
        self._conn.execute("DELETE FROM tracks_fts")
        if "display_title" in cols:
            self._conn.execute(
                "INSERT INTO tracks_fts(rowid, title, artist, album_artist, album) "
                "SELECT id"
                ", COALESCE(display_title, title)"
                ", artist"
                ", COALESCE(display_album_artist, album_artist)"
                ", COALESCE(display_album, album)"
                " FROM tracks"
            )
        else:
            self._conn.execute(
                "INSERT INTO tracks_fts(rowid, title, artist, album_artist, album) "
                "SELECT id, title, artist, album_artist, album FROM tracks"
            )
        self._conn.execute("DELETE FROM playlists_fts")
        self._conn.execute(
            "INSERT INTO playlists_fts(rowid, title) SELECT id, title FROM playlists"
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

    def get_collection_streamable_counts(self) -> dict[str, int]:
        """Return {sale_item_id: num_streamable_tracks} for all pre-order rows."""
        rows = self._conn.execute(
            "SELECT sale_item_id, num_streamable_tracks FROM bandcamp_collection"
            " WHERE mode = 'preorder'"
        ).fetchall()
        return {r["sale_item_id"]: r["num_streamable_tracks"] for r in rows}

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
        num_streamable_tracks: int = 0,
    ) -> None:
        """Insert or update a single entry in bandcamp_collection."""
        now = _time.time()
        self._conn.execute(
            """
            INSERT INTO bandcamp_collection
                (sale_item_id, item_type, band_name, item_title,
                 tralbum_id, album_url, mode, synced_at, added_at,
                 num_streamable_tracks)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sale_item_id) DO UPDATE SET
                item_type             = excluded.item_type,
                band_name             = excluded.band_name,
                item_title            = excluded.item_title,
                tralbum_id            = excluded.tralbum_id,
                album_url             = excluded.album_url,
                mode                  = excluded.mode,
                synced_at             = COALESCE(excluded.synced_at, synced_at),
                added_at              = MIN(added_at, COALESCE(excluded.added_at, added_at)),
                num_streamable_tracks = excluded.num_streamable_tracks
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
                num_streamable_tracks,
            ),
        )
        # Keep the albums row's sale_item_id FK in sync when a matching album exists.
        if band_name and item_title:
            self._conn.execute(
                """
                UPDATE albums SET sale_item_id = ?
                WHERE album_artist = ? COLLATE NOCASE
                  AND album        = ? COLLATE NOCASE
                  AND (sale_item_id IS NULL OR sale_item_id = ?)
                """,
                (sale_item_id, band_name, item_title, sale_item_id),
            )
        self._conn.commit()

    def update_remote_track_date_added(
        self, sale_item_id: str, date_added: float
    ) -> None:
        """Set date_added on remote tracks for *sale_item_id* if it is later than *date_added*.

        Corrects tracks whose date_added was recorded as the sync timestamp rather than
        the actual purchase date.  The MIN-wins rule mirrors upsert_collection_item so
        that an earlier purchase date can never be overwritten by a later sync.
        Also propagates the corrected date to the parent albums row via sale_item_id.
        """
        self._conn.execute(
            "UPDATE tracks SET date_added = ? "
            "WHERE (file_path LIKE ? OR file_path LIKE ?) AND date_added > ?",
            (
                date_added,
                f"bandcamp://{sale_item_id}/%",
                f"bandcamp:\\{sale_item_id}\\%",
                date_added,
            ),
        )
        # Propagate to the albums row. MIN-wins: only update when new value is earlier.
        self._conn.execute(
            "UPDATE albums SET date_added = ?"
            " WHERE sale_item_id = ?"
            " AND (date_added IS NULL OR date_added > ?)",
            (date_added, sale_item_id, date_added),
        )
        self._conn.commit()

    def set_track_source_for_item(self, sale_item_id: str, source: str) -> int:
        """Set source on every track whose file_path belongs to *sale_item_id*.

        Matches canonical bandcamp:// (POSIX) and Windows bandcamp:\\ path forms.
        Returns the number of rows updated. Also refreshes albums.source so the
        album entity stays consistent without waiting for the next full rescan.
        """
        cur = self._conn.execute(
            "UPDATE tracks SET source = ? WHERE file_path LIKE ? OR file_path LIKE ?",
            (source, f"bandcamp://{sale_item_id}/%", f"bandcamp:\\{sale_item_id}\\%"),
        )
        # Refresh albums.source via the sale_item_id FK. Remote tracks may not have
        # album_id populated yet, so join through albums.sale_item_id instead.
        self._conn.execute(
            f"UPDATE albums SET source = {_ALBUM_SOURCE_SUBQUERY}"
            " WHERE sale_item_id = ?",
            (sale_item_id,),
        )
        self._conn.commit()
        return cur.rowcount

    def set_collection_item_mode(self, sale_item_id: str, mode: str) -> bool:
        """Update mode for a collection item and clear synced_at so the next sync picks it up.

        Returns True if the item was found, False if it does not exist.
        """
        cur = self._conn.execute(
            "UPDATE bandcamp_collection SET mode = ?, synced_at = NULL WHERE sale_item_id = ?",
            (mode, sale_item_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_remote_collection(self) -> list[dict[str, Any]]:
        """Return all bandcamp_collection rows with mode='remote'."""
        rows = self._conn.execute(
            "SELECT * FROM bandcamp_collection WHERE mode = 'remote'"
        ).fetchall()
        return [dict(r) for r in rows]

    def has_remote_album_tracks(self, sale_item_id: str) -> bool:
        """Return True if this remote album's stream tracks are already present.

        Checks legacy bandcamp:// (POSIX) and Windows bandcamp:\\ tracks.file_path
        forms AND (post-KAMP-541 collapse) stream track_sources whose uri encodes
        this sale_item_id. After the collapse the stream lives as a source on the
        downloaded canonical rather than as its own tracks row, so a file_path-only
        check would miss it and make the incremental sync re-fetch and re-insert a
        duplicate stream row.
        """
        row = self._conn.execute(
            "SELECT 1 FROM tracks WHERE file_path LIKE ? OR file_path LIKE ?"
            " UNION ALL"
            " SELECT 1 FROM track_sources WHERE kind = 'stream'"
            "   AND (uri LIKE ? OR uri LIKE ?)"
            " LIMIT 1",
            (
                f"bandcamp://{sale_item_id}/%",
                f"bandcamp:\\{sale_item_id}\\%",
                f"bandcamp://{sale_item_id}/%",
                f"bandcamp:\\{sale_item_id}\\%",
            ),
        ).fetchone()
        return row is not None

    def has_remote_tracks_needing_date_backfill(self, sale_item_id: str) -> bool:
        """Return True if any remote tracks for this album have year-only or empty release_date.

        Used by sync_collection_stream to trigger a re-fetch of the album page so
        full ISO dates ("2020-01-01") replace year-only strings written before KAMP-513.
        """
        row = self._conn.execute(
            "SELECT 1 FROM tracks"
            " WHERE (file_path LIKE ? OR file_path LIKE ?)"
            "   AND source = 'bandcamp'"
            "   AND (release_date = '' OR length(release_date) <= 4)"
            " LIMIT 1",
            (f"bandcamp://{sale_item_id}/%", f"bandcamp:\\{sale_item_id}\\%"),
        ).fetchone()
        return row is not None

    def patch_release_date_for_remote_album(
        self, sale_item_id: str, release_date: str
    ) -> None:
        """Update release_date on all remote tracks for a Bandcamp album.

        Called after a backfill fetch to replace year-only strings with the full
        ISO date. Does not touch play_count, favorite, or any other field.
        """
        self._conn.execute(
            "UPDATE tracks SET release_date = ?"
            " WHERE (file_path LIKE ? OR file_path LIKE ?)"
            "   AND source = 'bandcamp'",
            (
                release_date,
                f"bandcamp://{sale_item_id}/%",
                f"bandcamp:\\{sale_item_id}\\%",
            ),
        )
        self._conn.commit()

    def local_tracks_for_sale_item_id(self, sale_item_id: str) -> "list[Track]":
        """Return local (non-bandcamp://) Track objects for a downloaded collection item.

        Used by the server layer to obtain track IDs for the playing-guard check
        and to collect file paths for deletion before calling remove_download().

        Honors provenance at BOTH levels (KAMP-528): a track whose album carries
        the sale_item_id, OR a track that carries it directly (a standalone single
        with no album row). LEFT JOIN so a NULL-album single still matches, and
        DISTINCT so a track that satisfies both arms after the album→track backfill
        is not returned twice.
        """
        rows = self._conn.execute(
            """
            SELECT DISTINCT t.* FROM tracks_with_stats t
            LEFT JOIN albums a ON a.id = t.album_id
            WHERE (a.sale_item_id = ? OR t.sale_item_id = ?)
              AND t.file_path NOT LIKE 'bandcamp://%'
              AND t.file_path NOT LIKE 'bandcamp:\\%'
            ORDER BY t.disc_number, t.track_number
            """,
            (sale_item_id, sale_item_id),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    def link_track_to_sale_item_id(self, track_id: int, sale_item_id: str) -> bool:
        """Attach Bandcamp provenance to a single track by id (KAMP-528).

        The recovery path for a standalone single that has no album row: there is
        nowhere to record its origin except tracks.sale_item_id. Validates that
        *sale_item_id* exists in bandcamp_collection first — both for a clear
        caller-facing failure and to keep the FK to bandcamp_collection satisfied
        (a bare UPDATE with an unknown sid would raise IntegrityError). Returns
        True if the track was found and linked, False otherwise.
        """
        known = self._conn.execute(
            "SELECT 1 FROM bandcamp_collection WHERE sale_item_id = ?",
            (sale_item_id,),
        ).fetchone()
        if known is None:
            return False
        cur = self._conn.execute(
            "UPDATE tracks SET sale_item_id = ? WHERE id = ?",
            (sale_item_id, track_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def _file_sources_for_item(self, sale_item_id: str) -> "list[sqlite3.Row]":
        """Rows (track_id, src_id, uri, album_id) for file sources of a download.

        A downloaded track is a canonical track with a kind='file' track_sources
        row tied to *sale_item_id* — by the source's provider_item_id, or via the
        album/track sale_item_id provenance (KAMP-528). (KAMP-541.)
        """
        return self._conn.execute(
            "SELECT DISTINCT t.id AS track_id, fs.id AS src_id, fs.uri AS uri,"
            " t.album_id AS album_id"
            " FROM tracks t"
            " JOIN track_sources fs ON fs.track_id = t.id AND fs.kind = 'file'"
            " LEFT JOIN albums a ON a.id = t.album_id"
            " WHERE fs.provider_item_id = ? OR a.sale_item_id = ? OR t.sale_item_id = ?"
            " ORDER BY t.disc_number, t.track_number",
            (sale_item_id, sale_item_id, sale_item_id),
        ).fetchall()

    def all_downloads_streamable(self, sale_item_id: str) -> bool:
        """True if every downloaded track for *sale_item_id* has a stream source.

        The KAMP-541 replacement for the has_remote_album_tracks gate in the
        delete-download flow: when False, the server materializes the missing
        stream sources before removing the download.
        """
        row = self._conn.execute(
            "SELECT 1 FROM tracks t"
            " JOIN track_sources fs ON fs.track_id = t.id AND fs.kind = 'file'"
            " LEFT JOIN albums a ON a.id = t.album_id"
            " WHERE (fs.provider_item_id = ? OR a.sale_item_id = ? OR t.sale_item_id = ?)"
            "   AND NOT EXISTS (SELECT 1 FROM track_sources s"
            "     WHERE s.track_id = t.id AND s.kind = 'stream')"
            " LIMIT 1",
            (sale_item_id, sale_item_id, sale_item_id),
        ).fetchone()
        return row is None

    def _refresh_album_source(self, album_id: int) -> None:
        """Recompute albums.source for one album from its tracks' legacy columns."""
        self._conn.execute(
            f"UPDATE albums SET source = {_ALBUM_SOURCE_SUBQUERY} WHERE id = ?",
            (album_id,),
        )

    def remove_download(self, sale_item_id: str) -> "list[Path]":
        """Revert a downloaded collection item back to streaming state.

        0. Fail-safe guard: raise NoStreamableVersionError (before touching
           anything) unless every local track has a matching bandcamp:// row to
           fall back to. The daemon materializes those stream rows on demand
           first; if it cannot, we must not delete the download (KAMP-527).
        1. Migrates play counts from local track rows to their matching streaming
           counterparts (MAX wins) so counts accumulated during local playback are
           not lost.
        2. Deletes local track rows for the album.
        3. Refreshes albums.source so the album immediately shows as 'bandcamp'.
        4. Sets bandcamp_collection.mode = 'remote'.

        Returns the list of local file paths that the caller should delete from
        the filesystem (file deletion is handled in the server layer). All
        mutations run under rollback discipline so a mid-flight failure never
        leaks a partial delete into a later commit on this pooled connection.
        """
        rows = self._file_sources_for_item(sale_item_id)
        if not rows:
            return []

        # KAMP-527 fail-safe: refuse unless EVERY downloaded track retains a stream
        # source after we drop its file source. Checked before any mutation so an
        # abort touches nothing (the daemon materializes missing streams first).
        for r in rows:
            if (
                self._conn.execute(
                    "SELECT 1 FROM track_sources WHERE track_id = ? AND kind = 'stream'"
                    " LIMIT 1",
                    (r["track_id"],),
                ).fetchone()
                is None
            ):
                raise NoStreamableVersionError(
                    f"track {r['track_id']} for sale_item_id={sale_item_id} has no "
                    "stream source; refusing to remove the download."
                )

        file_paths = [Path(r["uri"]) for r in rows]
        album_ids = {r["album_id"] for r in rows if r["album_id"] is not None}

        # All mutations under a single rollback-guarded transaction (KAMP-527
        # deferred-commit discipline).
        try:
            # Drop each file source and revert the track to its stream source.
            for r in rows:
                self._conn.execute(
                    "DELETE FROM track_sources WHERE id = ?", (r["src_id"],)
                )
                self._sync_tracks_row_to_preferred_source(r["track_id"])
            for aid in album_ids:
                self._refresh_album_source(aid)
            self._conn.execute(
                "UPDATE bandcamp_collection SET mode = 'remote', synced_at = NULL"
                " WHERE sale_item_id = ?",
                (sale_item_id,),
            )
            self._rebuild_fts()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if self.on_fields_changed:
            self.on_fields_changed(
                {
                    "track.source",
                    "track.play_count",
                    "track.favorite",
                    "album.source",
                }
            )

        return file_paths

    def materialize_stream_tracks(
        self, sale_item_id: str, tracks: "list[Track]"
    ) -> int:
        """Attach an on-demand stream source to each downloaded track (KAMP-541/527).

        Used by the DELETE-download endpoint to give a download-mode album (bought
        + downloaded, never streamed) the streamable fallback remove_download
        needs. *tracks* are streaming Track objects (bandcamp:// file_path); each
        is matched to its existing canonical track by (this item + track_number +
        disc_number) and a kind='stream' track_sources row is inserted when the
        track has none yet. Rollback-guarded. Returns the number attached.
        """
        if not tracks:
            return 0
        attached = 0
        try:
            for t in tracks:
                uri = _canonical_track_uri(t.file_path)
                row = self._conn.execute(
                    "SELECT t.id FROM tracks t"
                    " LEFT JOIN albums a ON a.id = t.album_id"
                    " WHERE (a.sale_item_id = ? OR t.sale_item_id = ?)"
                    "   AND t.track_number = ? AND t.disc_number = ?"
                    "   AND NOT EXISTS (SELECT 1 FROM track_sources s"
                    "     WHERE s.track_id = t.id AND s.kind = 'stream')"
                    " LIMIT 1",
                    (sale_item_id, sale_item_id, t.track_number, t.disc_number),
                ).fetchone()
                if row is None:
                    continue
                if self._conn.execute(
                    "SELECT 1 FROM track_sources WHERE uri = ?", (uri,)
                ).fetchone():
                    continue  # uri already present
                self._conn.execute(
                    "INSERT INTO track_sources (track_id, kind, provider,"
                    " provider_item_id, uri, ext, duration, stream_url,"
                    " stream_url_expires_at) VALUES (?, 'stream', 'bandcamp', ?, ?,"
                    " ?, ?, ?, ?)",
                    (
                        row["id"],
                        str(sale_item_id),
                        uri,
                        t.ext,
                        t.duration,
                        t.stream_url,
                        t.stream_url_expires_at,
                    ),
                )
                attached += 1
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return attached

    def reset_collection_sync_state(self) -> None:
        """Set synced_at = NULL for all rows so the next sync re-downloads everything."""
        self._conn.execute("UPDATE bandcamp_collection SET synced_at = NULL")
        self._conn.commit()

    def clear_bandcamp_collection(self) -> None:
        """Delete all rows from bandcamp_collection (explicit account reset).

        No longer called on logout — logout preserves the ledger (the file→purchase
        provenance map) so the next sync reconciles rather than rebuilds. This
        remains for an explicit account switch/reset. Null the child provenance
        FKs first (albums AND tracks, KAMP-528): with foreign_keys=ON, deleting a
        parent row still referenced by a provenanced album or track raises
        IntegrityError — which is exactly what used to make logout crash.
        """
        self._conn.execute("UPDATE albums SET sale_item_id = NULL")
        self._conn.execute("UPDATE tracks SET sale_item_id = NULL")
        self._conn.execute("DELETE FROM bandcamp_collection")
        self._conn.commit()

    def get_collection_item(self, sale_item_id: str) -> dict[str, Any] | None:
        """Return the bandcamp_collection row for *sale_item_id*, or None if absent."""
        row = self._conn.execute(
            "SELECT * FROM bandcamp_collection WHERE sale_item_id = ?",
            (sale_item_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_collection_item_by_album(
        self, album_artist: str, album: str
    ) -> dict[str, Any] | None:
        """Return the bandcamp_collection row matching (album_artist, album), or None.

        Used by the art endpoint to serve cached Bandcamp CDN art for local albums
        that were downloaded from Bandcamp but have no embedded artwork yet.
        Looks up sale_item_id from the albums row (set during v24 migration and
        maintained by upsert_many) rather than doing a cross-table string join.
        Falls back to the COLLATE NOCASE join for missing-album edge cases.
        """
        album_row = self._conn.execute(
            "SELECT sale_item_id FROM albums WHERE album_artist = ? COLLATE NOCASE AND album = ? COLLATE NOCASE",
            (album_artist, album),
        ).fetchone()
        sale_item_id = album_row["sale_item_id"] if album_row else None
        if sale_item_id:
            row = self._conn.execute(
                "SELECT * FROM bandcamp_collection WHERE sale_item_id = ?",
                (sale_item_id,),
            ).fetchone()
        else:
            # Fallback for missing-album tracks or albums not yet in the albums table.
            row = self._conn.execute(
                """SELECT * FROM bandcamp_collection
                   WHERE band_name = ? COLLATE NOCASE
                     AND item_title = ? COLLATE NOCASE
                   LIMIT 1""",
                (album_artist, album),
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

    def preferred_source(self, track_id: int) -> "sqlite3.Row | None":
        """Return the preferred track_sources row for playback (KAMP-541, design §3).

        Prefers an available source, then a local file over a stream, then the
        lowest source id. None if the track has no source rows (shouldn't happen
        after KAMP-540; callers fall back to the legacy Track columns). Once a
        collapsed track has both a file and a stream source, this selects the
        local file when it is available and falls through to the stream when the
        file is not (e.g. an unmounted drive) — availability is the primary key so
        the fallback actually happens (design §3; note the §3 draft listed
        kind before is_available, which would never fall through).
        """
        row = self._conn.execute(
            "SELECT id, kind, provider, provider_item_id, uri, ext, duration,"
            " embedded_art, file_mtime, is_available, stream_url, stream_url_expires_at"
            " FROM track_sources WHERE track_id = ?"
            " ORDER BY is_available DESC, (kind = 'file') DESC, id"
            " LIMIT 1",
            (track_id,),
        ).fetchone()
        return row  # type: ignore[no-any-return]

    def update_stream_url_for_source(
        self, source_id: int, stream_url: str, expires_at: float
    ) -> None:
        """Persist a refreshed CDN stream URL onto a track_sources row (KAMP-541)."""
        self._conn.execute(
            "UPDATE track_sources SET stream_url = ?, stream_url_expires_at = ?"
            " WHERE id = ?",
            (stream_url, expires_at, source_id),
        )
        self._conn.commit()

    def _sync_tracks_row_to_preferred_source(self, track_id: int) -> None:
        """Realign a track's legacy columns to its preferred source (KAMP-541).

        Keeps `tracks.file_path`/`source`/per-source columns coherent with
        `track_sources` while both coexist (the columns are dropped in KAMP-539),
        e.g. after a local file is removed and the track reverts to its stream
        source. Caller commits. No-op if the track has no sources.
        """
        src = self.preferred_source(track_id)
        if src is None:
            return
        self._conn.execute(
            "UPDATE tracks SET file_path = ?, source = ?, ext = ?, duration = ?,"
            " embedded_art = ?, file_mtime = ?, is_available = ?, stream_url = ?,"
            " stream_url_expires_at = ? WHERE id = ?",
            (
                src["uri"],
                "local" if src["kind"] == "file" else "bandcamp",
                src["ext"],
                src["duration"],
                src["embedded_art"],
                src["file_mtime"],
                src["is_available"],
                src["stream_url"],
                src["stream_url_expires_at"],
                track_id,
            ),
        )

    def update_track_display_title(
        self, track_id: int, display_title: str | None
    ) -> "Track | None":
        """Set (or clear) the display title override for a streaming track (KAMP-467).

        Passing None or an empty string clears the override and restores the
        Bandcamp canonical title.  Returns the updated Track, or None if the
        track is not found.
        """
        value = display_title or None  # normalise empty string → NULL
        self._conn.execute(
            "UPDATE tracks SET display_title = ? WHERE id = ?", (value, track_id)
        )
        # Update FTS so the new display title is immediately searchable.
        self._conn.execute("DELETE FROM tracks_fts WHERE rowid=?", (track_id,))
        self._conn.execute(
            "INSERT INTO tracks_fts(rowid, title, artist, album_artist, album)"
            " SELECT id"
            ", COALESCE(display_title, title)"
            ", artist"
            ", COALESCE(display_album_artist, album_artist)"
            ", COALESCE(display_album, album)"
            " FROM tracks WHERE id=?",
            (track_id,),
        )
        self._conn.commit()
        return self.get_track_by_id(track_id)

    def update_album_display(
        self,
        album_artist: str,
        album: str,
        display_album: str | None,
        display_album_artist: str | None,
    ) -> "AlbumInfo | None":
        """Set (or clear) display overrides for a streaming album (KAMP-467).

        Writes to the albums row and denormalizes onto all constituent tracks so
        that _row_to_track and FTS both see effective values without needing a
        JOIN.  Passing None or empty string for a field clears that override.
        Returns the updated AlbumInfo, or None if the album is not found.
        """
        album_id = self._album_id(album_artist, album)
        if album_id is None:
            return None
        d_album = display_album or None
        d_artist = display_album_artist or None
        self._conn.execute(
            "UPDATE albums SET display_album = ?, display_album_artist = ? WHERE id = ?",
            (d_album, d_artist, album_id),
        )
        # Denormalize onto tracks so _row_to_track needs no JOIN.
        self._conn.execute(
            "UPDATE tracks SET display_album = ?, display_album_artist = ? WHERE album_id = ?",
            (d_album, d_artist, album_id),
        )
        # Rebuild FTS for all affected tracks.
        track_ids = [
            r["id"]
            for r in self._conn.execute(
                "SELECT id FROM tracks WHERE album_id = ?", (album_id,)
            ).fetchall()
        ]
        for tid in track_ids:
            self._conn.execute("DELETE FROM tracks_fts WHERE rowid=?", (tid,))
            self._conn.execute(
                "INSERT INTO tracks_fts(rowid, title, artist, album_artist, album)"
                " SELECT id"
                ", COALESCE(display_title, title)"
                ", artist"
                ", COALESCE(display_album_artist, album_artist)"
                ", COALESCE(display_album, album)"
                " FROM tracks WHERE id=?",
                (tid,),
            )
        self._conn.commit()
        results = self.albums()
        for a in results:
            if (
                a.album_artist.lower() == album_artist.lower()
                and a.album.lower() == album.lower()
            ):
                return a
        return None

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
        """Insert or replace multiple tracks in a single transaction.

        Wrapped in rollback discipline: a mid-flight failure (FK violation, a
        locked database after the busy timeout, disk error) must not leave the
        partial writes uncommitted on this thread-local connection, or the next
        unrelated commit() would flush them (the KAMP-527 deferred-commit bug,
        also fixed in remove_download).
        """
        if not tracks:
            return
        try:
            self._upsert_many(tracks)
        except Exception:
            self._conn.rollback()
            raise

    def _upsert_many(self, tracks: list[Track]) -> None:
        """Body of upsert_many; see the wrapper for rollback discipline."""
        named = [t for t in tracks if t.album]

        # KAMP-523: resolve Bandcamp provenance up front. A track carrying a
        # KAMP_SALE_ITEM_ID tag (written by the download pipeline) links to its
        # album by *identity*, never by the fragile (album_artist, album) string
        # match — so a downloaded album re-attaches to its streaming origin even
        # when the tagger rewrote the names beyond whitespace/case. Only ids that
        # are actually present in bandcamp_collection are trusted (a stale tag on
        # a file the user moved elsewhere falls back to the string match, and the
        # FK on albums.sale_item_id can never be violated).
        # ALL tracks, not just `named`: a standalone single ships with no album
        # tag (album == ""), so it is excluded from `named` — but it still
        # carries a provenance stamp and must re-attach to its streaming origin
        # by identity (KAMP-523 single case). Its empty album just means it links
        # to an *existing* album row rather than minting one.
        prov_sids = {t.sale_item_id for t in tracks if t.sale_item_id}
        valid_sids: set[str] = set()
        existing_album_by_sid: dict[str, int] = {}
        if prov_sids:
            sid_list = list(prov_sids)
            sid_ph = ",".join("?" * len(sid_list))
            valid_sids = {
                r[0]
                for r in self._conn.execute(
                    "SELECT sale_item_id FROM bandcamp_collection"
                    f" WHERE sale_item_id IN ({sid_ph})",
                    sid_list,
                ).fetchall()
            }
            for r in self._conn.execute(
                f"SELECT id, sale_item_id FROM albums WHERE sale_item_id IN ({sid_ph})",
                sid_list,
            ).fetchall():
                # setdefault: the partial UNIQUE index makes this at most one row
                # per id post-migration; if an un-healed fork still has two, the
                # lowest id wins deterministically.
                existing_album_by_sid.setdefault(r["sale_item_id"], r["id"])

        def _provenanced(t: Track) -> bool:
            return bool(t.sale_item_id) and t.sale_item_id in valid_sids

        # A provenanced track whose album already exists by identity must NOT
        # spawn a name-keyed album row — that duplicate album (and its duplicate
        # artist row) is exactly the fork this ticket prevents.
        def _needs_name_album(t: Track) -> bool:
            return not (_provenanced(t) and t.sale_item_id in existing_album_by_sid)

        # Step 1: upsert parent album rows for named albums. INSERT OR IGNORE so
        # existing album metadata (e.g. favorite flag) is never overwritten here.
        seen: set[tuple[str, str]] = set()
        album_params: list[
            tuple[str, str, str, int, str, str, str, str, float | None]
        ] = []
        for t in named:
            if not _needs_name_album(t):
                continue
            key = (t.album_artist.lower(), t.album.lower())
            if key not in seen:
                seen.add(key)
                album_params.append(
                    (
                        t.album_artist,
                        t.album,
                        t.release_date,
                        int(t.embedded_art),
                        t.mb_release_id,
                        t.genre,
                        t.label,
                        t.source,
                        t.date_added,
                    )
                )
        if album_params:
            # Ensure each album_artist has an artists row before inserting albums.
            artist_names = list(
                {p[0] for p in album_params if p[0] and p[0] != "Various Artists"}
            )
            # Also ensure track-level artists from Various Artists albums have rows.
            va_track_artists = list(
                {
                    t.artist
                    for t in named
                    if t.album_artist == "Various Artists" and t.artist
                }
            )
            all_artist_names = list(set(artist_names + va_track_artists))
            self._conn.executemany(
                "INSERT OR IGNORE INTO artists (name) VALUES (?)",
                [(n,) for n in all_artist_names],
            )
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO albums
                    (album_artist, album, release_date, embedded_art, mb_release_id,
                     genre, label, source, date_added)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                album_params,
            )
            # Wire artist_id on any albums rows that are missing it.
            self._conn.execute("""
                UPDATE albums SET artist_id = (
                    SELECT id FROM artists WHERE name = album_artist
                )
                WHERE artist_id IS NULL
                """)

        # Step 2: upsert track rows.
        self._conn.executemany(
            """
            INSERT INTO tracks
                (file_path, title, artist, album_artist, album, release_date,
                 track_number, disc_number, ext, embedded_art,
                 mb_release_id, mb_recording_id, date_added, file_mtime,
                 genre, label, source, stream_url, stream_url_expires_at,
                 is_available, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                title                 = excluded.title,
                artist                = excluded.artist,
                album_artist          = excluded.album_artist,
                album                 = excluded.album,
                -- For streaming tracks, preserve user-edited release_date/genre/label if the
                -- incoming sync value is empty (Bandcamp never sends these fields).
                release_date          = CASE WHEN excluded.source = 'bandcamp'
                                             THEN COALESCE(NULLIF(excluded.release_date, ''), release_date)
                                             ELSE excluded.release_date END,
                track_number          = excluded.track_number,
                disc_number           = excluded.disc_number,
                ext                   = excluded.ext,
                embedded_art          = excluded.embedded_art,
                mb_release_id         = excluded.mb_release_id,
                mb_recording_id       = excluded.mb_recording_id,
                file_mtime            = excluded.file_mtime,
                genre                 = CASE WHEN excluded.source = 'bandcamp'
                                             THEN COALESCE(NULLIF(excluded.genre, ''), genre)
                                             ELSE excluded.genre END,
                label                 = CASE WHEN excluded.source = 'bandcamp'
                                             THEN COALESCE(NULLIF(excluded.label, ''), label)
                                             ELSE excluded.label END,
                source                = excluded.source,
                -- Preserve the cached CDN URL if the incoming row has none.
                -- fetch_album_tracks never populates stream_url; without this
                -- COALESCE a sync run would wipe every cached stream URL.
                stream_url            = COALESCE(excluded.stream_url, stream_url),
                stream_url_expires_at = COALESCE(excluded.stream_url_expires_at, stream_url_expires_at),
                is_available          = excluded.is_available,
                duration              = excluded.duration
                -- date_added intentionally omitted: preserve original scan date on re-scan
                -- last_played intentionally omitted: managed exclusively by record_played()
            """,
            [_track_to_params(t) for t in tracks],
        )

        # Step 3: assign album_id on the track rows we just inserted/updated.
        file_paths = [_canonical_track_uri(t.file_path) for t in named]

        # Step 3a (KAMP-523): link provenanced tracks by identity. Resolve each
        # trusted sale_item_id to its album — adopting the existing streaming
        # album where present, else stamping the id onto the newly-created
        # name-keyed row so the download and its origin share one album row.
        prov_paths: set[str] = set()
        prov_album_ids: set[int] = set()
        if valid_sids:
            prov_targets: dict[str, int] = {}
            for sid in prov_sids & valid_sids:
                if sid in existing_album_by_sid:
                    prov_targets[sid] = existing_album_by_sid[sid]
                    continue
                # No album carries this id yet. We can only mint/adopt one from a
                # track that HAS a name; a nameless single (album == "") with no
                # existing album row is left unlinked rather than creating a blank
                # album — in practice its streaming origin row already exists.
                rep = next(
                    (t for t in tracks if t.sale_item_id == sid and t.album), None
                )
                if rep is None:
                    continue
                # Stamp the id onto the just-inserted name-keyed row (or an
                # existing un-provenanced local row with the same names).
                self._conn.execute(
                    "UPDATE albums SET sale_item_id = ?"
                    " WHERE album_artist = ? COLLATE NOCASE"
                    "   AND album        = ? COLLATE NOCASE"
                    "   AND (sale_item_id IS NULL OR sale_item_id = ?)",
                    (sid, rep.album_artist, rep.album, sid),
                )
                row = self._conn.execute(
                    "SELECT id FROM albums WHERE sale_item_id = ?", (sid,)
                ).fetchone()
                if row is not None:
                    prov_targets[sid] = row["id"]
            # Link every provenanced track (named or nameless) to its album.
            for t in tracks:
                if not (_provenanced(t) and t.sale_item_id in prov_targets):
                    continue
                fp = _canonical_track_uri(t.file_path)
                prov_paths.add(fp)
                prov_album_ids.add(prov_targets[t.sale_item_id])
                self._conn.execute(
                    "UPDATE tracks SET album_id = ? WHERE file_path = ?",
                    (prov_targets[t.sale_item_id], fp),
                )

        # Step 3a' (KAMP-528): persist track-level provenance for EVERY provenanced
        # track, independent of whether an album link resolved above. This is the
        # standalone-single case: a nameless single has no album row (so it is
        # absent from prov_targets) yet must still record its origin — the only
        # place that can is tracks.sale_item_id. Gated on valid_sids so the FK to
        # bandcamp_collection can never be violated; only set, never clear (mirrors
        # album_id linking and the ON CONFLICT clause, which leaves it untouched).
        # Canonical key so bandcamp:// / Windows-form paths match their stored row.
        if valid_sids:
            for t in tracks:
                if not _provenanced(t):
                    continue
                self._conn.execute(
                    "UPDATE tracks SET sale_item_id = ? WHERE file_path = ?",
                    (t.sale_item_id, _canonical_track_uri(t.file_path)),
                )

        # Step 3b: link the remaining (un-provenanced) tracks by name. TRIM on
        # both sides folds the leading/trailing-whitespace divergence that used
        # to fork albums (belt-and-suspenders behind the identity link above).
        plain_paths = [fp for fp in file_paths if fp not in prov_paths]
        if plain_paths:
            placeholders = ",".join("?" * len(plain_paths))
            self._conn.execute(
                f"""
                UPDATE tracks SET album_id = (
                    SELECT a.id FROM albums a
                    WHERE TRIM(a.album_artist) = TRIM(tracks.album_artist) COLLATE NOCASE
                      AND TRIM(a.album)        = TRIM(tracks.album)        COLLATE NOCASE
                )
                WHERE file_path IN ({placeholders})
                  AND album != ''
                """,
                plain_paths,
            )

        # Step 3c (KAMP-529): attach un-provenanced loose local singles. A
        # standalone single's local file has an empty album tag, so Step 3b
        # skips it (album != '') and it has no album row to sid-link — it lingers
        # as a loose track duplicating the streaming single. Re-link it to its
        # streaming single-album by identity (local.title == the single-album's
        # album field). Shared with the v42 migration via one helper.
        single_paths = [
            _canonical_track_uri(t.file_path)
            for t in tracks
            if t.source == "local"
            and not t.album.strip()
            and t.album_artist.strip()
            and t.title.strip()
            and not _provenanced(t)
        ]
        touched_from_singles = self._attach_loose_local_singles(single_paths)

        # Step 4: refresh denormalized aggregate columns on the touched album rows.
        # Run once per batch (not per track) using a correlated subquery. Include
        # albums reached only through provenance linking (prov_album_ids) and
        # single re-linking (touched_from_singles) — a nameless single's target
        # album is not in file_paths (built from named), yet its source/aggregates
        # must be recomputed now that a local track attached to it.
        touched_album_ids: set[int] = set(prov_album_ids) | touched_from_singles
        if file_paths:
            all_placeholders = ",".join("?" * len(file_paths))
            touched_album_ids.update(
                r[0]
                for r in self._conn.execute(
                    f"SELECT DISTINCT album_id FROM tracks"
                    f" WHERE file_path IN ({all_placeholders}) AND album_id IS NOT NULL",
                    file_paths,
                ).fetchall()
            )
        if touched_album_ids:
            self._refresh_album_aggregates(list(touched_album_ids))

        # Step 5 (KAMP-540): keep the canonical child tables in sync. Every
        # upserted track gets a refreshed track_sources row (per-source cols read
        # back from the now-current tracks row) and, if it has none yet, a
        # track_stats row mirroring its stats (INSERT OR IGNORE so a re-scan never
        # resets favorite/play_count). Reads still use the old tracks columns —
        # this only populates the children for KAMP-541/542.
        src_params: list[tuple[Any, ...]] = []
        for t in tracks:
            key = _canonical_track_uri(t.file_path)
            _uri, kind, provider, provider_item_id = _derive_source_fields(
                t.file_path, t.source, t.sale_item_id
            )
            src_params.append((kind, provider, provider_item_id, key))
        if src_params:
            self._conn.executemany(
                "INSERT INTO track_sources"
                " (track_id, kind, provider, provider_item_id, uri, ext, duration,"
                "  embedded_art, file_mtime, is_available, stream_url,"
                "  stream_url_expires_at)"
                " SELECT id, ?, ?, ?, file_path, ext, duration, embedded_art,"
                "        file_mtime, is_available, stream_url, stream_url_expires_at"
                " FROM tracks WHERE file_path = ?"
                " ON CONFLICT(uri) DO UPDATE SET"
                "   track_id = excluded.track_id, kind = excluded.kind,"
                "   provider = excluded.provider,"
                "   provider_item_id = excluded.provider_item_id,"
                "   ext = excluded.ext, duration = excluded.duration,"
                "   embedded_art = excluded.embedded_art,"
                "   file_mtime = excluded.file_mtime,"
                "   is_available = excluded.is_available,"
                "   stream_url = excluded.stream_url,"
                "   stream_url_expires_at = excluded.stream_url_expires_at",
                src_params,
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO track_stats"
                " (track_id, favorite, play_count, last_played)"
                " SELECT id, favorite, play_count, last_played"
                " FROM tracks WHERE file_path = ?",
                [(p[3],) for p in src_params],
            )
            # Step 6 (KAMP-541): attach a newly-scanned file to its existing
            # canonical track (merging the fork) instead of leaving a duplicate
            # streaming+local pair. No-op when there is no stream-only sibling.
            self._reconcile_scanned_tracks([p[3] for p in src_params])

        # Rebuild the FTS index so new/updated tracks are immediately searchable.
        self._rebuild_fts()
        self._conn.commit()
        if self.on_fields_changed:
            self.on_fields_changed(
                {
                    "track.artist",
                    "track.album",
                    "track.genre",
                    "track.source",
                    "track.year",
                }
            )

    def _refresh_album_aggregates(self, album_ids: list[int]) -> None:
        """Recompute denormalized album columns from the album's tracks.

        Refreshes source/art/counts/dates and backfills sale_item_id from
        bandcamp_collection. Shared by upsert_many Step 4 and the v42
        single-attach migration: without this an album whose source was
        'bandcamp' keeps reading as remote after a local track attaches, so the
        grid never reflects the newly-owned copy.
        """
        if not album_ids:
            return
        id_placeholders = ",".join("?" * len(album_ids))
        self._conn.execute(
            f"""
            UPDATE albums SET
                embedded_art   = (SELECT MAX(t.embedded_art)  FROM tracks t WHERE t.album_id = albums.id),
                date_added     = (SELECT MIN(t.date_added)    FROM tracks t WHERE t.album_id = albums.id),
                last_played_at = (SELECT MAX(t.last_played)   FROM tracks_with_stats t WHERE t.album_id = albums.id),
                art_version    = (SELECT MAX(CASE WHEN t.source = 'local' THEN t.file_mtime END)
                                  FROM tracks t WHERE t.album_id = albums.id),
                play_count_avg = (SELECT CAST(SUM(t.play_count) AS REAL) / COUNT(*)
                                  FROM tracks_with_stats t WHERE t.album_id = albums.id),
                source         = {_ALBUM_SOURCE_SUBQUERY},
                release_date   = (SELECT MAX(t.release_date)   FROM tracks t WHERE t.album_id = albums.id),
                genre          = (SELECT MAX(t.genre)          FROM tracks t WHERE t.album_id = albums.id),
                label          = (SELECT MAX(t.label)          FROM tracks t WHERE t.album_id = albums.id),
                mb_release_id  = (SELECT MAX(t.mb_release_id)  FROM tracks t WHERE t.album_id = albums.id)
            WHERE id IN ({id_placeholders})
            """,
            album_ids,
        )
        # Backfill sale_item_id from bandcamp_collection for albums that were just
        # inserted without it. Covers the streaming-sync order where
        # upsert_collection_item fires before upsert_many, so the UPDATE inside
        # upsert_collection_item is a no-op (no albums row yet at that point).
        self._conn.execute(
            f"""
            UPDATE albums SET sale_item_id = (
                SELECT bc.sale_item_id FROM bandcamp_collection bc
                WHERE bc.band_name  = albums.album_artist COLLATE NOCASE
                  AND bc.item_title = albums.album        COLLATE NOCASE
                LIMIT 1
            )
            WHERE id IN ({id_placeholders})
              AND sale_item_id IS NULL
            """,
            album_ids,
        )

    def _attach_loose_local_singles(
        self, file_paths: "list[str] | None" = None
    ) -> set[int]:
        """Attach un-provenanced loose local singles to their streaming single-album (KAMP-529).

        A standalone Bandcamp single's local file carries an empty album tag, so
        the (album_artist, album) name-match in ``_upsert_many`` (Step 3b) skips
        it and it has no album row to sid-link — it lingers as a loose track that
        duplicates the streaming single kamp indexes for the same purchase. The
        streaming single is a 1-track album whose ``album`` field equals the item
        title, so the match key already lives there: ``local.title`` ==
        ``streaming_single_album.album``.

        Attach only on an *unambiguous* identity match: non-empty
        album_artist/title, the streaming side is a genuine 1-track ``bandcamp``
        single, and exactly one candidate exists on each side of the
        (album_artist, title) pair — otherwise skip and log. ``sale_item_id`` is
        deliberately NOT stamped on the local row: the album already carries it,
        and stamping a never-downloaded row would create a second claimant for
        the KAMP-523 download re-attach path. Shared by the scan path and the v42
        migration so the two never drift.

        When *file_paths* is given, only those local rows are considered (scan
        path); ``None`` scans every loose local single (one-shot migration).
        Idempotent: an attached single gains an album_id and drops out of the
        candidate set. Returns the set of album_ids that gained a track.
        """
        params: list[Any] = []
        scope = ""
        if file_paths is not None:
            if not file_paths:
                return set()
            scope = f" AND t.file_path IN ({','.join('?' * len(file_paths))})"
            params = list(file_paths)
        candidates = self._conn.execute(
            f"""
            SELECT t.id, t.file_path, t.album_artist, t.title
            FROM tracks t
            WHERE t.source = 'local'
              AND t.album_id IS NULL
              AND TRIM(t.album) = ''
              AND TRIM(t.album_artist) != ''
              AND TRIM(t.title) != ''
              {scope}
            """,
            params,
        ).fetchall()

        touched: set[int] = set()
        for c in candidates:
            # Ambiguity guard (DB-wide, not batch-scoped): more than one loose
            # local single sharing this (album_artist, title) is not safe to
            # auto-attach — leave all of them untouched.
            local_dupes = self._conn.execute(
                """
                SELECT COUNT(*) FROM tracks
                WHERE source = 'local' AND album_id IS NULL AND TRIM(album) = ''
                  AND TRIM(album_artist) = TRIM(?) COLLATE NOCASE
                  AND TRIM(title)        = TRIM(?) COLLATE NOCASE
                """,
                (c["album_artist"], c["title"]),
            ).fetchone()[0]
            if local_dupes != 1:
                logger.info(
                    "KAMP-529 single-attach: skipping ambiguous local single"
                    " (%s - %s): %d loose candidates",
                    c["album_artist"],
                    c["title"],
                    local_dupes,
                )
                continue
            # Streaming single-album: exactly one track, a bandcamp row, whose
            # album equals this single's title under the same artist.
            matches = self._conn.execute(
                """
                SELECT a.id FROM albums a
                WHERE TRIM(a.album_artist) = TRIM(?) COLLATE NOCASE
                  AND TRIM(a.album)        = TRIM(?) COLLATE NOCASE
                  AND (SELECT COUNT(*) FROM tracks x WHERE x.album_id = a.id) = 1
                  AND EXISTS (
                        SELECT 1 FROM tracks x
                        WHERE x.album_id = a.id AND x.source = 'bandcamp'
                      )
                """,
                (c["album_artist"], c["title"]),
            ).fetchall()
            if len(matches) != 1:
                if len(matches) > 1:
                    logger.info(
                        "KAMP-529 single-attach: skipping ambiguous streaming"
                        " match for (%s - %s): %d candidate albums",
                        c["album_artist"],
                        c["title"],
                        len(matches),
                    )
                continue
            album_id = matches[0]["id"]
            # Align track_number/disc to the streaming track so per-track joins
            # (favorite/play-count inheritance, remove_download) line up, and stamp
            # the album's canonical name onto the (previously album-less) single so
            # it stops rendering as its own "missing album" grid card — setting
            # album_id alone leaves album='' and the loose card persists (KAMP-529).
            stream = self._conn.execute(
                "SELECT track_number, disc_number FROM tracks"
                " WHERE album_id = ? AND source = 'bandcamp' LIMIT 1",
                (album_id,),
            ).fetchone()
            alb = self._conn.execute(
                "SELECT album_artist, album FROM albums WHERE id = ?", (album_id,)
            ).fetchone()
            self._conn.execute(
                "UPDATE tracks SET album_id = ?, track_number = ?, disc_number = ?,"
                " album = ?, album_artist = ? WHERE id = ?",
                (
                    album_id,
                    stream["track_number"],
                    stream["disc_number"],
                    alb["album"],
                    alb["album_artist"],
                    c["id"],
                ),
            )
            touched.add(album_id)
            logger.info(
                "KAMP-529 single-attach: linked local single '%s - %s'"
                " (track id %d) to streaming album %d",
                c["album_artist"],
                c["title"],
                c["id"],
                album_id,
            )
        return touched

    def _rename_file_source(
        self, old_path: "Path | str", new_path: "Path | str"
    ) -> None:
        """Repoint a track's file track_sources.uri after its file moves on disk.

        The scanner reads indexed file paths from track_sources (kind='file',
        KAMP-541), so a tag-edit / album rename that physically moved a file must
        update its file source uri in lock-step with tracks.file_path. Otherwise
        the next scan sees the old uri as a vanished file (remove_track drops the
        source — deleting a local-only track and its stats) and the new path as
        unindexed (re-fork). Caller owns the commit.
        """
        self._conn.execute(
            "UPDATE track_sources SET uri = ? WHERE kind = 'file' AND uri = ?",
            (str(new_path), str(old_path)),
        )

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
        favorite, mb IDs) are intentionally unchanged. The track's file source
        uri is repointed alongside file_path (see _rename_file_source).
        """
        self._conn.execute(
            "UPDATE tracks SET file_path = ?, title = ?, file_mtime = ? WHERE file_path = ?",
            (str(new_path), new_title, new_mtime, str(old_path)),
        )
        self._rename_file_source(old_path, new_path)
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
        self._rename_file_source(old_path, new_path)
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

        Also updates the albums row so album_artist/album stay in sync. Raises
        sqlite3.IntegrityError if new_album_artist/new_album already exists in
        the albums table (i.e. a rename collision — callers should catch and 409).
        """
        # Determine the album_id from the first old path's track row.
        old_path_str = str(path_pairs[0][0]) if path_pairs else None
        album_id: int | None = None
        if old_path_str:
            r = self._conn.execute(
                "SELECT album_id FROM tracks WHERE file_path = ?", (old_path_str,)
            ).fetchone()
            album_id = r["album_id"] if r else None

        try:
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
                self._rename_file_source(old_path, new_path)
            # Update the albums row. The UNIQUE (album_artist, album) COLLATE NOCASE
            # constraint raises IntegrityError if the new name already exists.
            if album_id is not None:
                self._conn.execute(
                    "UPDATE albums SET album_artist = ?, album = ? WHERE id = ?",
                    (new_album_artist, new_album, album_id),
                )
            self._rebuild_fts()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

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

    # ------------------------------------------------------------------
    # Pending ingest — download → pipeline provenance handoff (KAMP-523)
    # ------------------------------------------------------------------

    def add_pending_ingest(
        self, artifact_path: str, sale_item_id: str, tralbum_id: str = ""
    ) -> None:
        """Record that *artifact_path* is a Bandcamp download of *sale_item_id*.

        INSERT OR REPLACE on the UNIQUE(artifact_path) so a re-download of the
        same artifact path refreshes the identity rather than erroring.
        """
        import time as _t

        self._conn.execute(
            "INSERT OR REPLACE INTO pending_ingest"
            " (artifact_path, sale_item_id, tralbum_id, created_at) VALUES (?,?,?,?)",
            (artifact_path, sale_item_id, tralbum_id, _t.time()),
        )
        self._conn.commit()

    def pending_ingest_for_path(self, artifact_path: str) -> PendingIngest | None:
        """Return the pending-ingest row for *artifact_path*, or None.

        The pipeline receives the watch-folder path it was triggered on; this
        looks the provenance up before any extraction/move mutates the path.
        """
        row = self._conn.execute(
            "SELECT * FROM pending_ingest WHERE artifact_path=?",
            (artifact_path,),
        ).fetchone()
        return _row_to_pending_ingest(row) if row is not None else None

    def clear_pending_ingest(self, artifact_path: str) -> None:
        """Delete the pending-ingest row for *artifact_path* (idempotent).

        Called from the pipeline's finally/quarantine paths so a completed or
        failed ingest never leaves a stale handoff behind.
        """
        self._conn.execute(
            "DELETE FROM pending_ingest WHERE artifact_path=?", (artifact_path,)
        )
        self._conn.commit()

    def sweep_orphan_pending_ingest(self) -> int:
        """Delete pending-ingest rows whose artifact no longer exists on disk.

        Guards against rows left behind by a crash/restart between download and
        ingest. Returns the number of rows removed. Called once at daemon start.
        """
        rows = self._conn.execute(
            "SELECT id, artifact_path FROM pending_ingest"
        ).fetchall()
        stale = [r["id"] for r in rows if not Path(r["artifact_path"]).exists()]
        if stale:
            placeholders = ",".join("?" * len(stale))
            self._conn.execute(
                f"DELETE FROM pending_ingest WHERE id IN ({placeholders})", stale
            )
            self._conn.commit()
        return len(stale)

    # ------------------------------------------------------------------
    # Download queue (KAMP-408)
    # ------------------------------------------------------------------

    def enqueue_download(self, sale_item_id: str) -> None:
        """Add *sale_item_id* to the persistent download queue.

        INSERT OR IGNORE makes this idempotent — re-enqueuing an already-queued
        item is a no-op so double-clicks don't produce duplicate work.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO download_queue (sale_item_id) VALUES (?)",
            (sale_item_id,),
        )
        self._conn.commit()

    def dequeue_download(self, sale_item_id: str) -> None:
        """Remove *sale_item_id* from the persistent download queue after completion."""
        self._conn.execute(
            "DELETE FROM download_queue WHERE sale_item_id = ?", (sale_item_id,)
        )
        self._conn.commit()

    def pending_downloads(self) -> list[str]:
        """Return all queued sale_item_ids in FIFO order (oldest first).

        Used on daemon restart to replay any downloads that survived a shutdown.
        """
        rows = self._conn.execute(
            "SELECT sale_item_id FROM download_queue ORDER BY queued_at ASC, id ASC"
        ).fetchall()
        return [r["sale_item_id"] for r in rows]

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
            " SELECT id"
            ", COALESCE(display_title, title)"
            ", artist"
            ", COALESCE(display_album_artist, album_artist)"
            ", COALESCE(display_album, album)"
            " FROM tracks WHERE id=?",
            (track_id,),
        )
        self._conn.commit()

    def remove_track(self, file_path: Path) -> None:
        """Remove a local file from the index (KAMP-541).

        Drops the file's `track_sources` row. The canonical `tracks` row is
        deleted only when no source remains — a track that still has a stream
        source survives (reverting to stream-only) instead of being cascade-
        deleted along with its stream source and stats (the KAMP-527 data-loss
        class). Legacy fallback: a pre-collapse row with no source is deleted by
        `file_path` as before.
        """
        uri = str(file_path)
        src = self._conn.execute(
            "SELECT track_id FROM track_sources WHERE uri = ? AND kind = 'file'",
            (uri,),
        ).fetchone()
        if src is not None:
            track_id = src["track_id"]
            self._conn.execute(
                "DELETE FROM track_sources WHERE uri = ? AND kind = 'file'", (uri,)
            )
            remaining = self._conn.execute(
                "SELECT COUNT(*) FROM track_sources WHERE track_id = ?", (track_id,)
            ).fetchone()[0]
            if remaining == 0:
                self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            else:
                # Keep the track; realign its legacy columns to a surviving source
                # so file_path/source reads stay coherent and the freed local path
                # can be re-added later without a UNIQUE collision.
                self._sync_tracks_row_to_preferred_source(track_id)
        else:
            self._conn.execute("DELETE FROM tracks WHERE file_path = ?", (uri,))
        # Sync FTS — rebuilding is simpler than per-row deletes with FTS5 content tables.
        self._rebuild_fts()
        self._conn.commit()
        if self.on_fields_changed:
            self.on_fields_changed(
                {
                    "track.artist",
                    "track.album",
                    "track.genre",
                    "track.source",
                    "track.year",
                    "track.favorite",
                    "track.play_count",
                    "track.last_played",
                }
            )

    def prune_empty_albums(self) -> int:
        """Delete purely-local album rows that no longer have any tracks (KAMP-522).

        When the user deletes an album folder from disk, the scan removes the
        track rows but leaves the parent ``albums`` row orphaned; it then shows
        up in the UI as a ghost card with zero tracks. This sweeps those rows.

        The ``sale_item_id IS NULL`` guard is load-bearing: Bandcamp-backed rows
        (preorders, fetch-failed streaming albums, downloaded purchases) can
        legitimately have zero *local* tracks and must survive — a forked album
        also keeps its ``bandcamp://`` rows, so ``NOT EXISTS`` protects it too.
        Returns the number of album rows removed.
        """
        cur = self._conn.execute(
            "DELETE FROM albums WHERE sale_item_id IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM tracks WHERE tracks.album_id = albums.id)"
        )
        self._conn.commit()
        return cur.rowcount

    def all_tracks(self) -> list[Track]:
        """Return all indexed tracks in insertion order."""
        rows = self._conn.execute("SELECT * FROM tracks_with_stats").fetchall()
        return [_row_to_track(r) for r in rows]

    def top_tracks(self, limit: int) -> list[Track]:
        """Return the top *limit* tracks by play_count descending, excluding unplayed."""
        rows = self._conn.execute(
            "SELECT * FROM tracks_with_stats WHERE play_count > 0"
            " ORDER BY play_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    def _canonical_key_for(self, uri: "Path | str") -> str:
        """Translate *uri* to the tracks.file_path of the row it addresses.

        A track is addressable by its own file_path OR by any of its
        track_sources uris. After the KAMP-541 collapse a live client can still
        hold a delivery uri — e.g. a queued bandcamp:// stream uri — for a track
        that has since collapsed onto its downloaded local file, so that uri is
        now the track's *stream source* while file_path has realigned to the
        local path. Direct-match first (the common case); only when the uri is
        not itself a file_path do we redirect through track_sources. Falls back
        to the normalized uri when nothing matches, leaving the caller's write a
        harmless no-op exactly as before.
        """
        key = _canonical_track_uri(uri)
        if self._conn.execute(
            "SELECT 1 FROM tracks WHERE file_path = ? LIMIT 1", (key,)
        ).fetchone():
            return key
        row = self._conn.execute(
            "SELECT t.file_path FROM track_sources s JOIN tracks t"
            " ON t.id = s.track_id WHERE s.uri = ? AND s.kind = 'stream' LIMIT 1",
            (key,),
        ).fetchone()
        return str(row["file_path"]) if row is not None else key

    def _mirror_track_stats(self, key: str, cols: "tuple[str, ...]") -> None:
        """Mirror stat column(s) from the tracks row at *key* into track_stats.

        Absolute-value dual-write (KAMP-540): reads the *post-update* value(s)
        from tracks and upserts them into track_stats keyed by track_id — never a
        relative delta, so it survives the KAMP-541 collapse. No-op when *key*
        matches no track. Creates the track_stats row (mirroring current tracks
        values) if the v45 backfill / a scan hasn't yet. *cols* are internal
        column-name literals, never user input.
        """
        import time

        col_list = ", ".join(cols)
        assigns = ", ".join(f"{c} = excluded.{c}" for c in cols)
        self._conn.execute(
            f"INSERT INTO track_stats (track_id, {col_list}, updated_at)"
            f" SELECT id, {col_list}, ? FROM tracks WHERE file_path = ?"
            f" ON CONFLICT(track_id) DO UPDATE SET {assigns},"
            f" updated_at = excluded.updated_at",
            (time.time(), key),
        )

    def record_track_started(self, file_path: Path) -> None:
        """Record the current time as last_played for the track at *file_path*.

        Called when a track begins playing so that Last Played sort order
        reflects when listening last occurred rather than when it ended.
        No-op if the path is not in the index. Also propagates the timestamp
        to the parent album row's last_played_at aggregate.
        """
        import time

        now = time.time()
        key = self._canonical_key_for(file_path)
        self._conn.execute(
            "UPDATE tracks SET last_played = ? WHERE file_path = ?",
            (now, key),
        )
        # Keep album aggregate in sync.
        self._conn.execute(
            """
            UPDATE albums SET last_played_at = ?
            WHERE id = (SELECT album_id FROM tracks WHERE file_path = ?)
              AND (last_played_at IS NULL OR last_played_at < ?)
            """,
            (now, key, now),
        )
        self._mirror_track_stats(key, ("last_played",))
        self._conn.commit()
        if self.on_fields_changed:
            self.on_fields_changed({"track.last_played"})

    def record_played(self, file_path: Path) -> None:
        """Increment play_count for the track at *file_path*.

        Called when a track reaches natural end-of-file. Only play_count is
        updated here; last_played is managed exclusively by record_track_started().
        Also refreshes the parent album's play_count_avg.
        """
        key = self._canonical_key_for(file_path)
        self._conn.execute(
            "UPDATE tracks SET play_count = play_count + 1 WHERE file_path = ?",
            (key,),
        )
        # Mirror the post-increment absolute value (not a +1) into track_stats
        # BEFORE recomputing the album average — that recompute now reads
        # play_count through the tracks_with_stats view (i.e. from track_stats),
        # so the mirror must land first or the average sees the stale value.
        self._mirror_track_stats(key, ("play_count",))
        self._conn.execute(
            """
            UPDATE albums SET play_count_avg = (
                SELECT CAST(SUM(t.play_count) AS REAL) / COUNT(*)
                FROM tracks_with_stats t WHERE t.album_id = albums.id
            )
            WHERE id = (SELECT album_id FROM tracks WHERE file_path = ?)
            """,
            (key,),
        )
        self._conn.commit()
        if self.on_fields_changed:
            self.on_fields_changed({"track.play_count"})

    def record_play_time(self, file_path: Path, elapsed_seconds: float) -> None:
        """Add *elapsed_seconds* to the play_time of the artist for the track at *file_path*.

        Called periodically by the daemon's state-saver on track switch and on shutdown.
        Orthogonal to record_played: that updates tracks.play_count; this updates artists.play_time.
        For Various Artists albums, credits the track-level artist instead of the album artist.
        """
        key = _canonical_track_uri(file_path)
        row = self._conn.execute(
            "SELECT artist, album_artist FROM tracks WHERE file_path = ?", (key,)
        ).fetchone()
        if row is None:
            return
        artist_name = (
            row["artist"]
            if row["album_artist"] == "Various Artists"
            else row["album_artist"]
        )
        if not artist_name:
            return
        # Ensure the artist row exists (needed for track-level artists from VA albums).
        self._conn.execute(
            "INSERT OR IGNORE INTO artists (name) VALUES (?)", (artist_name,)
        )
        self._conn.execute(
            "UPDATE artists SET play_time = play_time + ? WHERE name = ?",
            (elapsed_seconds, artist_name),
        )
        self._conn.commit()

    def top_artists(self, limit: int) -> "list[ArtistInfo]":
        """Return the top *limit* artists by play_time descending, excluding unplayed."""
        rows = self._conn.execute(
            """
            SELECT ar.name, ar.play_time,
                   (SELECT album FROM albums
                    WHERE artist_id = ar.id
                    ORDER BY play_count_avg DESC LIMIT 1) AS top_album
            FROM artists ar
            WHERE ar.play_time > 0
            ORDER BY ar.play_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            ArtistInfo(
                name=r["name"], play_time=r["play_time"], top_album=r["top_album"]
            )
            for r in rows
        ]

    def get_stats(self, top_tracks_limit: int = 3) -> LibraryStats:
        """Return aggregate library and listening statistics."""
        c = self._conn
        track_count = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        # The albums table contains only real albums; virtual "missing_album" entries
        # are synthesised at query time from tracks with empty album tags — they are
        # not stored here, so no filter is required.
        album_count = c.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        # Count distinct album_artist values, matching the user-visible artists()
        # list — a pruned album's artist row lingers in the artists table (its
        # play_time still feeds total_play_seconds), so counting that table would
        # drift above the shown artist list after a deletion (KAMP-522).
        artist_count = c.execute(
            "SELECT COUNT(DISTINCT album_artist) FROM albums"
        ).fetchone()[0]
        total_seconds = c.execute(
            "SELECT COALESCE(SUM(play_time), 0) FROM artists"
        ).fetchone()[0]
        total_plays = c.execute(
            "SELECT COALESCE(SUM(play_count), 0) FROM tracks_with_stats"
        ).fetchone()[0]
        albums_played = c.execute(
            "SELECT COUNT(*) FROM albums WHERE play_count_avg > 0"
        ).fetchone()[0]
        top_artist_row = c.execute(
            "SELECT name, play_time FROM artists"
            " WHERE play_time > 0 ORDER BY play_time DESC LIMIT 1"
        ).fetchone()
        return LibraryStats(
            track_count=track_count,
            album_count=album_count,
            artist_count=artist_count,
            total_play_seconds=float(total_seconds),
            total_track_plays=int(total_plays),
            albums_played=albums_played,
            top_artist_name=top_artist_row["name"] if top_artist_row else None,
            top_artist_seconds=(
                float(top_artist_row["play_time"]) if top_artist_row else None
            ),
            top_tracks=self.top_tracks(top_tracks_limit),
        )

    def set_favorite(self, file_path: "Path | str", favorite: bool) -> None:
        """Set or clear the favorite flag for the track at *file_path*.

        *file_path* may be a track's own file_path or any of its delivery uris
        (a queued bandcamp:// stream uri whose track has since collapsed onto its
        local file); _canonical_key_for redirects the latter to the surviving
        row so the favorite lands on the canonical track (KAMP-541).
        """
        key = self._canonical_key_for(file_path)
        self._conn.execute(
            "UPDATE tracks SET favorite = ? WHERE file_path = ?",
            (int(favorite), key),
        )
        self._mirror_track_stats(key, ("favorite",))
        self._conn.commit()
        if self.on_fields_changed:
            self.on_fields_changed({"track.favorite"})

    def inherit_remote_favorites(self, new_tracks: "list[Track]") -> None:
        """Copy the favorite flag from a matching remote bandcamp:// row to each
        newly-added local track, if the remote version was favorited.

        Called by LibraryScanner after upserting newly-scanned local files so
        that favorites set on streaming tracks are not lost when an album is
        downloaded.  Matches by (album_id, track_number, disc_number) — the
        album_id FK eliminates the binary-collation string matching that caused
        silent data loss when MusicBrainz normalised album_artist capitalisation.
        Only updates rows where the local track has favorite=0 to avoid clearing
        a flag the user explicitly set on the local file.
        """
        for t in new_tracks:
            self._conn.execute(
                """
                UPDATE tracks SET favorite = 1
                WHERE file_path = ?
                  AND favorite = 0
                  AND EXISTS (
                      SELECT 1 FROM tracks r
                      WHERE r.album_id = (
                                SELECT album_id FROM tracks
                                WHERE file_path = ?
                            )
                        AND r.track_number = ?
                        AND r.disc_number = ?
                        AND r.file_path LIKE 'bandcamp://%'
                        AND r.favorite = 1
                  )
                """,
                (
                    str(t.file_path),
                    str(t.file_path),
                    t.track_number,
                    t.disc_number,
                ),
            )
        if new_tracks:
            self._conn.commit()

    def inherit_remote_play_counts(self, new_tracks: "list[Track]") -> None:
        """Copy play_count from a matching remote bandcamp:// row to each newly-added
        local track, taking the higher of the two values.

        Called by LibraryScanner after a pre-order album is downloaded so play
        counts accumulated during the streaming period are not lost.  Uses the
        same (album_id, track_number, disc_number) match as inherit_remote_favorites.
        Only updates when the remote play_count > the local play_count.
        """
        for t in new_tracks:
            self._conn.execute(
                """
                UPDATE tracks SET play_count = (
                    SELECT MAX(r.play_count, tracks.play_count)
                    FROM tracks r
                    WHERE r.album_id = (
                              SELECT album_id FROM tracks
                              WHERE file_path = ?
                          )
                      AND r.track_number = ?
                      AND r.disc_number = ?
                      AND r.file_path LIKE 'bandcamp://%'
                      AND r.play_count > 0
                    LIMIT 1
                )
                WHERE file_path = ?
                  AND EXISTS (
                      SELECT 1 FROM tracks r
                      WHERE r.album_id = (
                                SELECT album_id FROM tracks
                                WHERE file_path = ?
                            )
                        AND r.track_number = ?
                        AND r.disc_number = ?
                        AND r.file_path LIKE 'bandcamp://%'
                        AND r.play_count > tracks.play_count
                  )
                """,
                (
                    str(t.file_path),
                    t.track_number,
                    t.disc_number,
                    str(t.file_path),
                    str(t.file_path),
                    t.track_number,
                    t.disc_number,
                ),
            )
        if new_tracks:
            self._conn.commit()

    def update_album_meta(
        self,
        album_artist: str,
        album: str,
        *,
        genre: str | None = None,
        label: str | None = None,
        release_date: str | None = None,
        mb_release_id: str | None = None,
    ) -> list[Track]:
        """Write genre, label, release_date, and/or mb_release_id to every track in *album*.

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
        if release_date is not None:
            sets.append("release_date = ?")
            params.append(release_date)
        if mb_release_id is not None:
            sets.append("mb_release_id = ?")
            params.append(mb_release_id)
        if not sets:
            return self.tracks_for_album(album_artist, album)
        album_id = self._album_id(album_artist, album)
        if album_id is None:
            return []
        params.append(album_id)
        self._conn.execute(
            f"UPDATE tracks SET {', '.join(sets)} WHERE album_id = ?",
            params,
        )
        # Keep the albums row in sync with track-level metadata fields.
        album_sets: list[str] = []
        album_meta: list[object] = []
        for col, val in [
            ("genre", genre),
            ("label", label),
            ("release_date", release_date),
            ("mb_release_id", mb_release_id),
        ]:
            if val is not None:
                album_sets.append(f"{col} = ?")
                album_meta.append(val)
        if album_sets:
            album_meta.append(album_id)
            self._conn.execute(
                f"UPDATE albums SET {', '.join(album_sets)} WHERE id = ?",
                album_meta,
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
        album_id = self._album_id(album_artist, album)
        if album_id is None:
            return
        str_paths = [str(p) for p in file_paths]
        placeholders = ",".join("?" * len(str_paths))
        self._conn.execute(
            f"UPDATE tracks SET embedded_art = 1, file_mtime = ?"
            f" WHERE album_id = ?"
            f" AND file_path IN ({placeholders})",
            [now, album_id, *str_paths],
        )
        # Update the album row's aggregate art fields.
        self._conn.execute(
            "UPDATE albums SET embedded_art = 1, art_version = ? WHERE id = ?",
            (now, album_id),
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
            "SELECT * FROM tracks_with_stats WHERE id = ?", (track_id,)
        ).fetchone()
        return _row_to_track(row) if row else None

    def toggle_album_favorite(
        self, album_artist: str, album: str, favorite: bool
    ) -> None:
        """Set or clear the favorite flag on the album row."""
        self._conn.execute(
            "UPDATE albums SET favorite = ? WHERE album_artist = ? COLLATE NOCASE AND album = ? COLLATE NOCASE",
            (int(favorite), album_artist, album),
        )
        self._conn.commit()
        if self.on_fields_changed:
            self.on_fields_changed({"album.favorite"})

    def albums(
        self, sort: str = "album_artist", sort_dir: str | None = None
    ) -> list[AlbumInfo]:
        """Return one AlbumInfo per album (named albums from the albums table,
        plus one virtual entry per track that has no album tag).

        Named albums are read directly from the ``albums`` table with a minimal
        LEFT JOIN to ``tracks`` for per-track aggregates (track_count,
        has_favorite_track). Missing-album tracks each appear as their own entry
        via a UNION ALL branch, unchanged from the pre-KAMP-418 behaviour.

        *sort* must be one of: ``album_artist`` (default), ``album``,
        ``date_added``, ``last_played``, ``most_played``.  Unknown values fall
        back to ``album_artist``.

        *sort_dir* must be ``"asc"`` or ``"desc"``; ``None`` (default) uses the
        natural direction for the chosen sort key (ASC for text fields, DESC for
        date/play fields) so the historical ordering is preserved.
        """
        clause = _SORT_CLAUSES.get(sort, _SORT_CLAUSES["album_artist"])
        if sort_dir in ("asc", "desc"):
            dir_sql = sort_dir.upper()
        else:
            dir_sql = _DEFAULT_SORT_DIR.get(sort, "ASC")
        order_by = clause.format(dir=dir_sql)
        rows = self._conn.execute(f"""
            SELECT
                a.id                AS album_id,
                a.album_artist,
                a.album,
                a.release_date,
                a.source            AS album_source,
                a.sale_item_id,
                a.favorite          AS is_favorite,
                a.date_added        AS sort_date_added,
                a.last_played_at    AS sort_last_played,
                a.play_count_avg    AS sort_play_count_avg,
                NULLIF(a.release_date, '')  AS sort_release_date,
                a.art_version,
                -- has_art: remote-only albums always have CDN art; others use embedded_art.
                CASE WHEN a.source = 'bandcamp' THEN 1 ELSE a.embedded_art END AS has_art,
                0                   AS missing_album,
                ''                  AS file_path,
                -- track_count: for mixed or local albums count only local tracks
                -- so the dedup state (local+remote rows coexisting) shows real files.
                CASE WHEN a.source IN ('mixed', 'local')
                     THEN COUNT(CASE WHEN t.source = 'local' THEN 1 END)
                     ELSE COUNT(t.id)
                END                 AS track_count,
                MAX(t.favorite)     AS has_favorite_track,
                -- in_bandcamp_collection: True only when mode='local' (user downloaded it).
                CASE WHEN bc.mode = 'local' THEN 1 ELSE 0 END AS in_bc,
                -- is_preorder: True when the album is a Bandcamp pre-order (KAMP-423).
                CASE WHEN bc.mode = 'preorder' THEN 1 ELSE 0 END AS is_preorder,
                -- num_streamable_tracks: 0 => no streamable version (KAMP-527).
                COALESCE(bc.num_streamable_tracks, 0) AS num_streamable_tracks,
                -- album_url: Bandcamp page URL for sharing (KAMP-367).
                COALESCE(bc.album_url, '') AS album_url,
                -- display overrides for streaming albums (KAMP-467).
                a.display_album,
                a.display_album_artist,
                -- effective album title used for sort-by-album ordering.
                COALESCE(a.display_album, a.album) AS sort_album
            FROM albums a
            LEFT JOIN tracks_with_stats t ON t.album_id = a.id
            LEFT JOIN bandcamp_collection bc ON bc.sale_item_id = a.sale_item_id
            GROUP BY a.id
            UNION ALL
            -- Missing-album tracks: each track with album='' appears as its own
            -- virtual entry; file_path is the unique identifier for these entries.
            SELECT
                0                   AS album_id,
                t.album_artist,
                t.title             AS album,
                t.release_date,
                {_EFFECTIVE_SOURCE_EXPR} AS album_source,
                NULL                AS sale_item_id,
                0                   AS is_favorite,
                t.date_added        AS sort_date_added,
                t.last_played       AS sort_last_played,
                CAST(t.play_count AS REAL) AS sort_play_count_avg,
                NULLIF(t.release_date, '')  AS sort_release_date,
                t.file_mtime        AS art_version,
                t.embedded_art      AS has_art,
                1                   AS missing_album,
                t.file_path,
                1                   AS track_count,
                t.favorite          AS has_favorite_track,
                0                   AS in_bc,
                0                   AS is_preorder,
                0                   AS num_streamable_tracks,
                ''                  AS album_url,
                NULL                AS display_album,
                NULL                AS display_album_artist,
                t.title             AS sort_album
            FROM tracks_with_stats t
            WHERE t.album = ''
            ORDER BY {order_by}
            """).fetchall()  # noqa: S608 — order_by is from a whitelist, not user input
        return [
            AlbumInfo(
                album_id=r["album_id"],
                album_artist=r["album_artist"],
                album=r["album"],
                release_date=r["release_date"],
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
                has_remote_tracks=r["album_source"] != "local",
                in_bandcamp_collection=bool(r["in_bc"]),
                is_preorder=bool(r["is_preorder"]),
                num_streamable_tracks=r["num_streamable_tracks"],
                album_url=r["album_url"] or "",
                sale_item_id=r["sale_item_id"],
                display_album=r["display_album"],
                display_album_artist=r["display_album_artist"],
            )
            for r in rows
        ]

    def artists(self) -> list[str]:
        """Return a sorted, deduplicated list of album_artist values.

        Queries the albums table (~1k rows) rather than tracks (~10k rows).
        COLLATE NOCASE on the albums.UNIQUE constraint means no case duplicates exist.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT album_artist FROM albums ORDER BY album_artist COLLATE NOCASE"
        ).fetchall()
        return [r["album_artist"] for r in rows]

    def _album_id(self, album_artist: str, album: str) -> int | None:
        """Return the albums.id for the given (album_artist, album) pair, or None."""
        row = self._conn.execute(
            "SELECT id FROM albums WHERE album_artist = ? COLLATE NOCASE AND album = ? COLLATE NOCASE",
            (album_artist, album),
        ).fetchone()
        return row["id"] if row else None

    def album_name_exists(self, album_artist: str, album: str) -> bool:
        """Return True if any albums row matches (album_artist, album) case-insensitively."""
        return self._album_id(album_artist, album) is not None

    def tracks_for_album(self, album_artist: str, album: str) -> list[Track]:
        """Return tracks for a given album sorted by disc then track number.

        KAMP-541: after the collapse there is one canonical row per track (its
        local file and stream both live in track_sources), so the old local-wins
        de-dup filter is gone — every album row is returned.
        """
        album_id = self._album_id(album_artist, album)
        if album_id is None:
            return []
        rows = self._conn.execute(
            "SELECT * FROM tracks_with_stats WHERE album_id = ?"
            " ORDER BY disc_number, track_number",
            (album_id,),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    def tracks_for_playlist(self, playlist_id: int) -> list[Track]:
        """Return Track objects for a playlist in playlist order."""
        rows = self._conn.execute(
            """
            SELECT t.*
            FROM playlist_tracks pt
            JOIN tracks_with_stats t ON t.id = pt.track_id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position ASC
            """,
            (playlist_id,),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    def indexed_paths(self) -> set[Path]:
        """Return the set of local file paths currently in the index.

        KAMP-541: reads the `kind='file'` sources from track_sources, not
        `tracks WHERE source='local'`. After a track is collapsed its local file
        lives as a source of a canonical row whose `tracks.file_path` may be the
        streaming uri — enumerating file sources keeps that path "indexed" so the
        scanner does not re-fork a duplicate row for it. Stream sources are
        excluded (their uri is not a real filesystem path).
        """
        rows = self._conn.execute(
            "SELECT uri FROM track_sources WHERE kind = 'file'"
        ).fetchall()
        return {Path(r["uri"]) for r in rows}

    def indexed_paths_with_mtime(self) -> dict[Path, float | None]:
        """Return a mapping of local indexed file paths to their stored file_mtime.

        KAMP-541: reads `kind='file'` sources from track_sources (see
        indexed_paths). Stream sources are excluded — their URI is not a real
        filesystem path.
        """
        rows = self._conn.execute(
            "SELECT uri, file_mtime FROM track_sources WHERE kind = 'file'"
        ).fetchall()
        return {Path(r["uri"]): r["file_mtime"] for r in rows}

    def get_track_by_path(self, path: "str | Path") -> "Track | None":
        """Return the track for *path*, or None if not indexed.

        Accepts both Path objects and plain strings. Strings are used directly
        as the lookup key to avoid Path normalization corrupting remote URIs
        (e.g. Path("bandcamp://999/3") collapses to "bandcamp:/999/3" on POSIX).
        """
        key = path if isinstance(path, str) else str(path)
        row = self._conn.execute(
            "SELECT * FROM tracks_with_stats WHERE file_path = ?", (key,)
        ).fetchone()
        if row is not None:
            return _row_to_track(row)
        # Fall back to a stream delivery uri: after the KAMP-541 collapse a client
        # may hold a track's bandcamp:// stream uri while file_path has realigned
        # to its downloaded local file. Resolve through track_sources so the stale
        # uri still finds the surviving canonical row. Restricted to stream
        # sources: a moved/renamed local file's *old* path is a stale file source
        # and must still resolve to nothing (the file is genuinely gone).
        src = self._conn.execute(
            "SELECT t.* FROM track_sources s"
            " JOIN tracks_with_stats t ON t.id = s.track_id"
            " WHERE s.uri = ? AND s.kind = 'stream' LIMIT 1",
            (_canonical_track_uri(key),),
        ).fetchone()
        return _row_to_track(src) if src else None

    def get_track_by_id(self, track_id: int) -> "Track | None":
        """Return the track with *track_id*, or None if not indexed."""
        row = self._conn.execute(
            "SELECT * FROM tracks_with_stats WHERE id = ?", (track_id,)
        ).fetchone()
        return _row_to_track(row) if row else None

    def get_track_by_recording_id(self, mb_recording_id: str) -> "Track | None":
        """Return the first track with *mb_recording_id*, or None."""
        if not mb_recording_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM tracks_with_stats WHERE mb_recording_id = ?",
            (mb_recording_id,),
        ).fetchone()
        return _row_to_track(row) if row else None

    def download_overrides_for_sale_item(self, sale_item_id: str) -> DownloadOverrides:
        """Return the effective album names + user title edits for a download (KAMP-523).

        The ingest pipeline applies these to the downloaded files so a download
        carries the same (album_artist, album) as its streaming origin — using
        the metadata we already have instead of re-deriving it. album_artist and
        album are the *effective* names: the user's display_* override if set,
        else the synced album row's canonical value, else the bandcamp_collection
        band_name/item_title (which always exists for a download). This is what
        gives a standalone single its album name (Bandcamp singles ship with no
        album tag, so the file would otherwise land album-less and fork off a
        second card — KAMP-523 single case).

        titles carries per-track user renames keyed by track_number; a
        track_number appearing on more than one disc is dropped (ambiguous)
        rather than risk mis-applying a title across discs.

        Returns empty names only when the item is unknown (no album row and no
        collection row) — the caller then keeps the file's own tags.
        """
        album = self._conn.execute(
            "SELECT id, album, album_artist, display_album, display_album_artist"
            " FROM albums WHERE sale_item_id = ?",
            (sale_item_id,),
        ).fetchone()

        titles: dict[int, str] = {}
        if album is not None:
            ambiguous: set[int] = set()
            for r in self._conn.execute(
                "SELECT track_number, display_title FROM tracks"
                " WHERE album_id = ? AND file_path LIKE 'bandcamp://%'"
                "   AND display_title IS NOT NULL AND display_title != ''",
                (album["id"],),
            ).fetchall():
                tno = r["track_number"]
                if tno in titles:
                    ambiguous.add(tno)
                titles[tno] = r["display_title"]
            for tno in ambiguous:
                titles.pop(tno, None)
            return DownloadOverrides(
                album_artist=album["display_album_artist"] or album["album_artist"],
                album=album["display_album"] or album["album"],
                titles=titles,
            )

        # No synced album row (downloaded without syncing streaming rows): fall
        # back to the collection ledger, which the downloader always wrote.
        bc = self._conn.execute(
            "SELECT band_name, item_title FROM bandcamp_collection"
            " WHERE sale_item_id = ?",
            (sale_item_id,),
        ).fetchone()
        if bc is None:
            return DownloadOverrides(album_artist="", album="", titles={})
        return DownloadOverrides(
            album_artist=(bc["band_name"] or "").strip(),
            album=(bc["item_title"] or "").strip(),
            titles=titles,
        )

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
            JOIN tracks_with_stats t ON f.rowid = t.id
            LEFT JOIN albums al ON al.id = t.album_id
            WHERE tracks_fts MATCH ?
              -- KAMP-541: the collapse leaves one canonical row per track, so the
              -- KAMP-529 local-wins de-dup filter is no longer needed.
            ORDER BY (t.favorite OR COALESCE(al.favorite, 0)) DESC, f.rank
            """,
            (fts_expr,),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    # Source expression reused by both playlist search methods.  Matches the
    # album source computation: 'mixed' when both local and non-local tracks are
    # present; otherwise the sole source value, defaulting to 'local' if empty.
    # Reads track_sources via _EFFECTIVE_SOURCE_EXPR instead of tracks.source
    # (KAMP-542) — behavior-identical, since post-collapse tracks.source equals a
    # track's effective source. Correlated on the outer `t` (LEFT JOIN tracks t).
    _PLAYLIST_SOURCE_EXPR = f"""
        CASE
            WHEN COUNT(CASE WHEN {_EFFECTIVE_SOURCE_EXPR} = 'local' THEN 1 END) > 0
             AND COUNT(CASE WHEN {_EFFECTIVE_SOURCE_EXPR} != 'local' THEN 1 END) > 0
            THEN 'mixed'
            WHEN COUNT(CASE WHEN {_EFFECTIVE_SOURCE_EXPR} = 'local' THEN 1 END) > 0
            THEN 'local'
            WHEN COUNT(t.id) = 0 THEN 'local'
            ELSE MIN({_EFFECTIVE_SOURCE_EXPR})
        END
    """

    def search_playlists(self, query: str) -> list[dict[str, Any]]:
        """Full-text search over playlist titles.

        Returns playlists ranked by relevance, each with a computed ``source``
        field ('local', 'bandcamp', or 'mixed'). Returns an empty list when
        *query* is blank.
        """
        fts_expr = _make_fts_query(query)
        if not fts_expr:
            return []
        rows = self._conn.execute(
            f"""
            SELECT p.id, p.title, p.favorite, p.created_at, p.updated_at,
                   p.last_played_at,
                   COUNT(pt.id) AS track_count,
                   {self._PLAYLIST_SOURCE_EXPR} AS source
            FROM playlists_fts f
            JOIN playlists p ON f.rowid = p.id
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            LEFT JOIN tracks t ON t.id = pt.track_id
            WHERE playlists_fts MATCH ?
            GROUP BY p.id
            ORDER BY p.favorite DESC, f.rank
            """,
            (fts_expr,),
        ).fetchall()
        return [_playlist_row_to_dict(r) for r in rows]

    def playlists_for_tracks(self, track_ids: list[int]) -> list[dict[str, Any]]:
        """Return deduplicated playlists that contain any of the given track IDs.

        Each result includes a computed ``source`` field. Returns an empty list
        when *track_ids* is empty.
        """
        if not track_ids:
            return []
        placeholders = ",".join("?" * len(track_ids))
        rows = self._conn.execute(
            f"""
            SELECT p.id, p.title, p.favorite, p.created_at, p.updated_at,
                   p.last_played_at,
                   COUNT(DISTINCT pt_all.id) AS track_count,
                   {self._PLAYLIST_SOURCE_EXPR} AS source
            FROM playlist_tracks pt_match
            JOIN playlists p ON p.id = pt_match.playlist_id
            -- Count all tracks in the playlist (not just the matched ones).
            LEFT JOIN playlist_tracks pt_all ON pt_all.playlist_id = p.id
            LEFT JOIN tracks t ON t.id = pt_all.track_id
            WHERE pt_match.track_id IN ({placeholders})
            GROUP BY p.id
            ORDER BY p.favorite DESC, p.title COLLATE NOCASE
            """,
            track_ids,
        ).fetchall()
        return [_playlist_row_to_dict(r) for r in rows]

    def save_player_state(self, track_id: int, position: float) -> None:
        """Persist the current track id and playback position.

        The value is stored in the (legacy-named) track_path column as the
        stringified track id (KAMP-536 queue-by-id). load_player_state() reads
        it back and callers resolve it via get_track_by_id; a legacy path value
        written by an older build is still resolvable via get_track_by_path.
        """
        self._conn.execute(
            """
            INSERT INTO player_state (id, track_path, position) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                track_path = excluded.track_path,
                position   = excluded.position
            """,
            (str(track_id), position),
        )
        self._conn.commit()

    def clear_player_state(self) -> None:
        """Remove the persisted player state (e.g. after the queue is exhausted)."""
        self._conn.execute("DELETE FROM player_state WHERE id = 1")
        self._conn.commit()

    def load_player_state(self) -> "tuple[str, float] | None":
        """Return (track_ref, position) from the last session, or None.

        *track_ref* is the current track's id as a string (KAMP-536); a legacy
        build may have stored a path/URI instead. Returned raw so the caller can
        resolve it (digit string → get_track_by_id, otherwise get_track_by_path)
        without Path() normalizing a remote bandcamp:// URI.
        """
        row = self._conn.execute(
            "SELECT track_path, position FROM player_state WHERE id = 1"
        ).fetchone()
        return (row["track_path"], row["position"]) if row else None

    def save_queue_state(
        self,
        track_ids: "Sequence[int]",
        order: list[int],
        pos: int,
        shuffle: bool,
        repeat: str,
    ) -> None:
        """Persist the queue in original load order with playback permutation.

        *track_ids* are track ids in original load order (KAMP-536 queue-by-id);
        the later collapse migration repoints these with a plain id update.
        """
        import json

        payload = json.dumps([int(t) for t in track_ids])
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
            (payload, order_payload, pos, int(shuffle), repeat),
        )
        self._conn.commit()

    def load_queue_state(
        self,
    ) -> "tuple[list[int | str], list[int], int, bool, str] | None":
        """Return (entries_in_original_order, order, pos, shuffle, repeat_mode) or None.

        *entries* are track ids (KAMP-536 queue-by-id). A DB last written by an
        older build yields legacy path strings instead; entries are returned as
        parsed JSON (ints or strs) so the caller resolves each by type — int via
        get_track_by_id, str via get_track_by_path (protecting bandcamp:// URIs
        from Path normalization).
        """
        import json

        row = self._conn.execute(
            "SELECT tracks, order_json, pos, shuffle, repeat FROM queue_state WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        entries: list[int | str] = list(json.loads(row["tracks"]))
        raw_order = row["order_json"]
        order: list[int] = (
            json.loads(raw_order) if raw_order else list(range(len(entries)))
        )
        raw_repeat = row["repeat"]
        repeat: str = (
            raw_repeat if raw_repeat in ("off", "queue", "album", "single") else "off"
        )
        return entries, order, row["pos"], bool(row["shuffle"]), repeat

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

    # ------------------------------------------------------------------
    # Playlists (KAMP-441)
    # ------------------------------------------------------------------

    def create_playlist(self, title: str) -> dict[str, Any]:
        """Create a new empty playlist and return its row as a dict."""
        import time as _time

        now = _time.time()
        cur = self._conn.execute(
            "INSERT INTO playlists (title, favorite, created_at, updated_at) VALUES (?, 0, ?, ?)",
            (title, now, now),
        )
        new_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO playlists_fts(rowid, title) VALUES (?, ?)", (new_id, title)
        )
        self._conn.commit()
        return {
            "id": new_id,
            "title": title,
            "favorite": False,
            "track_count": 0,
            "created_at": now,
            "updated_at": now,
        }

    def get_playlists(self) -> list[dict[str, Any]]:
        """Return all playlists with their track counts, ordered by title."""
        rows = self._conn.execute("""
            SELECT p.id, p.title, p.favorite, p.created_at, p.updated_at,
                   p.last_played_at,
                   CASE
                     WHEN mpc.playlist_id IS NOT NULL
                       THEN COALESCE(mpc.cached_track_count, 0)
                     ELSE COUNT(pt.id)
                   END AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            LEFT JOIN magic_playlist_criteria mpc ON mpc.playlist_id = p.id
            GROUP BY p.id
            ORDER BY p.title COLLATE NOCASE
            """).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "favorite": bool(r["favorite"]),
                "track_count": r["track_count"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "last_played_at": r["last_played_at"],
            }
            for r in rows
        ]

    def get_playlist(self, playlist_id: int) -> dict[str, Any] | None:
        """Return a single playlist row plus track_count, or None if absent."""
        row = self._conn.execute(
            """
            SELECT p.id, p.title, p.favorite, p.created_at, p.updated_at,
                   p.last_played_at,
                   CASE
                     WHEN mpc.playlist_id IS NOT NULL
                       THEN COALESCE(mpc.cached_track_count, 0)
                     ELSE COUNT(pt.id)
                   END AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            LEFT JOIN magic_playlist_criteria mpc ON mpc.playlist_id = p.id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (playlist_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "favorite": bool(row["favorite"]),
            "track_count": row["track_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_played_at": row["last_played_at"],
        }

    def record_playlist_played(self, playlist_id: int) -> None:
        """Write the current time as last_played_at for the given playlist."""
        now = _time.time()
        self._conn.execute(
            "UPDATE playlists SET last_played_at = ? WHERE id = ?",
            (now, playlist_id),
        )
        self._conn.commit()

    def get_playlist_tracks(self, playlist_id: int) -> list[dict[str, Any]]:
        """Return tracks in a playlist ordered by position.

        Each dict contains all Track fields plus the playlist-specific
        ``playlist_track_id`` (playlist_tracks.id) and ``position``.
        """
        rows = self._conn.execute(
            """
            SELECT pt.id AS playlist_track_id, pt.position,
                   t.id, t.file_path, t.title, t.artist, t.album_artist, t.album,
                   t.release_date, t.track_number, t.disc_number, t.ext, t.embedded_art,
                   t.mb_release_id, t.mb_recording_id, t.genre, t.label,
                   t.favorite, t.play_count, t.last_played, t.date_added,
                   t.source, t.is_available, t.duration
            FROM playlist_tracks pt
            JOIN tracks_with_stats t ON t.id = pt.track_id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position ASC
            """,
            (playlist_id,),
        ).fetchall()
        return [
            {
                "playlist_track_id": r["playlist_track_id"],
                "position": r["position"],
                "id": r["id"],
                "file_path": r["file_path"],
                "title": r["title"],
                "artist": r["artist"],
                "album_artist": r["album_artist"],
                "album": r["album"],
                "release_date": r["release_date"],
                "track_number": r["track_number"],
                "disc_number": r["disc_number"],
                "ext": r["ext"],
                "embedded_art": bool(r["embedded_art"]),
                "mb_release_id": r["mb_release_id"],
                "mb_recording_id": r["mb_recording_id"],
                "genre": r["genre"],
                "label": r["label"],
                "favorite": bool(r["favorite"]),
                "play_count": r["play_count"],
                "last_played": r["last_played"],
                "date_added": r["date_added"],
                "source": r["source"],
                "is_available": bool(r["is_available"]),
                "duration": r["duration"],
            }
            for r in rows
        ]

    def add_track_to_playlist(self, playlist_id: int, file_path: str) -> None:
        """Append a track to the end of a playlist.

        No-op if the track's file_path does not exist in the tracks table.
        """
        import time as _time

        row = self._conn.execute(
            "SELECT id FROM tracks WHERE file_path = ?", (file_path,)
        ).fetchone()
        if not row:
            return
        track_id = row["id"]
        next_pos = self._conn.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()[0]
        self._conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
            (playlist_id, track_id, next_pos),
        )
        self._conn.execute(
            "UPDATE playlists SET updated_at = ? WHERE id = ?",
            (_time.time(), playlist_id),
        )
        self._conn.commit()

    def remove_track_from_playlist(
        self, playlist_id: int, playlist_track_id: int
    ) -> None:
        """Remove a single playlist_tracks row and compact positions."""
        import time as _time

        pos_row = self._conn.execute(
            "SELECT position FROM playlist_tracks WHERE id = ? AND playlist_id = ?",
            (playlist_track_id, playlist_id),
        ).fetchone()
        if not pos_row:
            return
        removed_pos = pos_row["position"]
        self._conn.execute(
            "DELETE FROM playlist_tracks WHERE id = ? AND playlist_id = ?",
            (playlist_track_id, playlist_id),
        )
        # Compact: shift every row after the removed position down by 1
        self._conn.execute(
            "UPDATE playlist_tracks SET position = position - 1"
            " WHERE playlist_id = ? AND position > ?",
            (playlist_id, removed_pos),
        )
        self._conn.execute(
            "UPDATE playlists SET updated_at = ? WHERE id = ?",
            (_time.time(), playlist_id),
        )
        self._conn.commit()

    def reorder_playlist_tracks(self, playlist_id: int, track_ids: list[int]) -> None:
        """Rewrite positions so playlist_tracks.id rows appear in *track_ids* order.

        *track_ids* must contain every playlist_tracks.id that belongs to
        *playlist_id*.  Raises ValueError if the set does not match.
        """
        import time as _time

        existing = {
            r["id"]
            for r in self._conn.execute(
                "SELECT id FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
            ).fetchall()
        }
        if set(track_ids) != existing:
            raise ValueError(
                f"track_ids {set(track_ids)} does not match playlist {playlist_id} rows {existing}"
            )
        for new_pos, pt_id in enumerate(track_ids):
            self._conn.execute(
                "UPDATE playlist_tracks SET position = ? WHERE id = ?", (new_pos, pt_id)
            )
        self._conn.execute(
            "UPDATE playlists SET updated_at = ? WHERE id = ?",
            (_time.time(), playlist_id),
        )
        self._conn.commit()

    def set_playlist_favorite(self, playlist_id: int, favorite: bool) -> None:
        """Set or clear the favorite flag for *playlist_id*."""
        import time as _time

        self._conn.execute(
            "UPDATE playlists SET favorite = ?, updated_at = ? WHERE id = ?",
            (int(favorite), _time.time(), playlist_id),
        )
        self._conn.commit()

    def rename_playlist(self, playlist_id: int, title: str) -> None:
        """Rename *playlist_id*."""
        import time as _time

        self._conn.execute(
            "UPDATE playlists SET title = ?, updated_at = ? WHERE id = ?",
            (title, _time.time(), playlist_id),
        )
        self._conn.execute("DELETE FROM playlists_fts WHERE rowid = ?", (playlist_id,))
        self._conn.execute(
            "INSERT INTO playlists_fts(rowid, title) VALUES (?, ?)",
            (playlist_id, title),
        )
        self._conn.commit()

    def delete_playlist(self, playlist_id: int) -> None:
        """Delete *playlist_id* and all its playlist_tracks rows (via CASCADE)."""
        self._conn.execute("DELETE FROM playlists_fts WHERE rowid = ?", (playlist_id,))
        self._conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self._conn.commit()

    def set_playlist_cover(
        self, playlist_id: int, data: bytes
    ) -> dict[str, Any] | None:
        """Write *data* as the cover image for *playlist_id* and bump updated_at.

        Stores the file at <db_dir>/playlist_art/<playlist_id>.jpg.
        Returns the updated playlist row dict, or None if the playlist does not exist.
        """
        pl = self.get_playlist(playlist_id)
        if pl is None:
            return None
        art_dir = self._db_path.parent / "playlist_art"
        art_dir.mkdir(exist_ok=True)
        (art_dir / f"{playlist_id}.jpg").write_bytes(data)
        now = _time.time()
        self._conn.execute(
            "UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id)
        )
        self._conn.commit()
        return self.get_playlist(playlist_id)

    def get_playlist_cover(self, playlist_id: int) -> bytes | None:
        """Return the raw bytes of the stored cover art, or None if not set."""
        path = self._db_path.parent / "playlist_art" / f"{playlist_id}.jpg"
        if path.exists():
            return path.read_bytes()
        return None

    # ------------------------------------------------------------------
    # Magic playlist criteria (KAMP-459)
    # ------------------------------------------------------------------

    def create_magic_playlist(self, title: str, criteria: MagicCriteria) -> int:
        """Create a new magic playlist and return its id.

        Writes to playlists, playlists_fts, and magic_playlist_criteria
        atomically so the playlist is always coherent on read.
        """
        now = _time.time()
        cur = self._conn.execute(
            "INSERT INTO playlists (title, favorite, created_at, updated_at) VALUES (?, 0, ?, ?)",
            (title, now, now),
        )
        new_id = cur.lastrowid
        self._conn.execute(
            "INSERT INTO playlists_fts(rowid, title) VALUES (?, ?)", (new_id, title)
        )
        self._conn.execute(
            "INSERT INTO magic_playlist_criteria (playlist_id, criteria_json) VALUES (?, ?)",
            (new_id, json.dumps(criteria.to_dict())),
        )
        self._conn.commit()
        return new_id  # type: ignore[return-value]

    def get_magic_playlist_criteria(self, playlist_id: int) -> MagicCriteria | None:
        """Return the MagicCriteria for a magic playlist, or None if it is not magic."""
        row = self._conn.execute(
            "SELECT criteria_json FROM magic_playlist_criteria WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        if row is None:
            return None
        return MagicCriteria.from_dict(json.loads(row["criteria_json"]))

    def update_magic_playlist_criteria(
        self, playlist_id: int, criteria: MagicCriteria
    ) -> None:
        """Replace the criteria for an existing magic playlist and clear evaluated_at.

        Raises ValueError if playlist_id has no magic_playlist_criteria row.
        """
        cur = self._conn.execute(
            "UPDATE magic_playlist_criteria"
            " SET criteria_json = ?, evaluated_at = NULL WHERE playlist_id = ?",
            (json.dumps(criteria.to_dict()), playlist_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"playlist {playlist_id} is not a magic playlist")
        self._conn.commit()

    def evaluate_magic_playlist(self, playlist_id: int) -> list[int]:
        """Return track IDs matching the magic playlist criteria.

        Returns an empty list when the playlist does not exist or has no
        criteria row.  A LEFT JOIN on albums is added only when the criteria
        reference album-level fields so the common case avoids the extra join.
        """
        # Lazy import avoids a circular dependency: criteria.py imports the
        # dataclasses (MagicCriteria etc.) defined in this module.
        from kamp_core.criteria import build_query

        criteria = self.get_magic_playlist_criteria(playlist_id)
        if criteria is None:
            return []
        where_fragment, params, needs_album_join = build_query(criteria)
        album_join = (
            "LEFT JOIN albums ON albums.id = tracks.album_id"
            if needs_album_join
            else ""
        )
        sql = (
            f"SELECT tracks.id FROM tracks_with_stats AS tracks {album_join}"
            f" WHERE {where_fragment} ORDER BY tracks.id"
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [r[0] for r in rows]

    def get_magic_playlist_tracks(self, playlist_id: int) -> list[dict[str, Any]]:
        """Return full track dicts for a magic playlist, evaluated on the fly.

        Returns the same shape as ``get_playlist_tracks`` but with
        ``playlist_track_id=None`` and ``position=0`` since magic tracks are
        not stored in ``playlist_tracks``.  Returns an empty list when the
        playlist has no criteria row.
        """
        from kamp_core.criteria import build_query

        criteria = self.get_magic_playlist_criteria(playlist_id)
        if criteria is None:
            return []
        where_fragment, params, needs_album_join = build_query(criteria)
        album_join = (
            "LEFT JOIN albums ON albums.id = tracks.album_id"
            if needs_album_join
            else ""
        )
        sql = f"""
            SELECT NULL AS playlist_track_id, 0 AS position,
                   tracks.id, tracks.file_path, tracks.title, tracks.artist,
                   tracks.album_artist, tracks.album, tracks.release_date,
                   tracks.track_number, tracks.disc_number, tracks.ext,
                   tracks.embedded_art, tracks.mb_release_id, tracks.mb_recording_id,
                   tracks.genre, tracks.label, tracks.favorite, tracks.play_count,
                   tracks.last_played, tracks.date_added,
                   tracks.source, tracks.is_available, tracks.duration
            FROM tracks_with_stats AS tracks {album_join}
            WHERE {where_fragment} AND tracks.is_available = 1
            ORDER BY tracks.id
        """
        rows = self._conn.execute(sql, params).fetchall()
        # Cache the evaluated count so get_playlists can show it immediately.
        import time as _t  # noqa: PLC0415

        self._conn.execute(
            "UPDATE magic_playlist_criteria"
            " SET cached_track_count = ?, evaluated_at = ? WHERE playlist_id = ?",
            (len(rows), _t.time(), playlist_id),
        )
        self._conn.commit()
        return [
            {
                "playlist_track_id": None,
                "position": 0,
                "id": r["id"],
                "file_path": r["file_path"],
                "title": r["title"],
                "artist": r["artist"],
                "album_artist": r["album_artist"],
                "album": r["album"],
                "release_date": r["release_date"],
                "track_number": r["track_number"],
                "disc_number": r["disc_number"],
                "ext": r["ext"],
                "embedded_art": bool(r["embedded_art"]),
                "mb_release_id": r["mb_release_id"],
                "mb_recording_id": r["mb_recording_id"],
                "genre": r["genre"],
                "label": r["label"],
                "favorite": bool(r["favorite"]),
                "play_count": r["play_count"],
                "last_played": r["last_played"],
                "date_added": r["date_added"],
                "source": r["source"],
                "is_available": bool(r["is_available"]),
                "duration": r["duration"],
            }
            for r in rows
        ]

    def get_playlist_module_content(
        self,
        playlist_id: int,
        contents: str,
        sort: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return albums, artists, or tracks for a playlist module.

        ``contents`` is one of 'albums', 'artists', 'tracks'.
        ``sort`` is one of 'random', 'last_played', 'recently_added', 'most_played'.
        Works for both static (playlist_tracks) and magic playlists.
        Returns an empty list when the playlist does not exist.
        """
        # Check existence.
        exists = self._conn.execute(
            "SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        if exists is None:
            return []

        # Resolve track ID set.
        is_magic = self._conn.execute(
            "SELECT 1 FROM magic_playlist_criteria WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        if is_magic:
            track_ids = self.evaluate_magic_playlist(playlist_id)
        else:
            rows = self._conn.execute(
                "SELECT track_id FROM playlist_tracks WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchall()
            track_ids = [r[0] for r in rows]

        if not track_ids:
            return []

        placeholders = ",".join("?" * len(track_ids))

        _ALBUM_SORT = {
            "random": "ORDER BY RANDOM()",
            "last_played": "ORDER BY a.last_played_at DESC NULLS LAST",
            "recently_added": "ORDER BY a.date_added DESC",
            "most_played": "ORDER BY a.play_count_avg DESC",
        }
        _TRACK_SORT = {
            "random": "ORDER BY RANDOM()",
            "last_played": "ORDER BY t.last_played DESC NULLS LAST",
            "recently_added": "ORDER BY t.date_added DESC",
            "most_played": "ORDER BY t.play_count DESC",
        }
        # Artist sort is derived from the playlist tracks in scope.
        _ARTIST_SORT = {
            "random": "ORDER BY RANDOM()",
            "last_played": "ORDER BY artist_last_played DESC NULLS LAST",
            "recently_added": "ORDER BY artist_recently_added DESC",
            "most_played": "ORDER BY ar.play_time DESC",
        }

        if contents == "albums":
            order = _ALBUM_SORT.get(sort, "ORDER BY RANDOM()")
            rows = self._conn.execute(
                f"""
                SELECT
                    a.album_artist, a.album, a.release_date, a.play_count_avg,
                    a.date_added, a.last_played_at, a.embedded_art, a.art_version,
                    a.display_album, a.display_album_artist, a.source, a.sale_item_id,
                    COUNT(t.id) AS track_count
                FROM albums a
                JOIN tracks t ON t.album_id = a.id
                WHERE a.id IN (
                    SELECT DISTINCT album_id FROM tracks WHERE id IN ({placeholders})
                )
                GROUP BY a.id
                {order}
                LIMIT ?
                """,
                (*track_ids, limit),
            ).fetchall()
            return [
                {
                    "album_artist": r["album_artist"],
                    "album": r["album"],
                    "release_date": r["release_date"] or "",
                    "track_count": r["track_count"],
                    "has_art": bool(r["embedded_art"]) or r["source"] == "bandcamp",
                    "missing_album": False,
                    "file_path": "",
                    "art_version": r["art_version"],
                    "added_at": r["date_added"],
                    "last_played_at": r["last_played_at"],
                    "play_count_avg": r["play_count_avg"] or 0.0,
                    "favorite": False,
                    "has_favorite_track": False,
                    "source": r["source"] or "local",
                    "has_remote_tracks": r["source"] != "local",
                    "sale_item_id": r["sale_item_id"],
                    "is_preorder": False,
                    "album_url": "",
                    "display_album": r["display_album"],
                    "display_album_artist": r["display_album_artist"],
                }
                for r in rows
            ]

        if contents == "artists":
            order = _ARTIST_SORT.get(sort, "ORDER BY RANDOM()")
            rows = self._conn.execute(
                f"""
                SELECT
                    ar.name, ar.play_time,
                    MAX(t.last_played) AS artist_last_played,
                    MAX(t.date_added) AS artist_recently_added,
                    (SELECT album FROM albums
                     WHERE artist_id = ar.id
                     ORDER BY play_count_avg DESC LIMIT 1) AS top_album
                FROM artists ar
                JOIN albums a ON a.artist_id = ar.id
                JOIN tracks_with_stats t ON t.album_id = a.id
                WHERE t.id IN ({placeholders}) AND a.artist_id IS NOT NULL
                GROUP BY ar.id
                {order}
                LIMIT ?
                """,
                (*track_ids, limit),
            ).fetchall()
            return [
                {
                    "name": r["name"],
                    "play_time": r["play_time"],
                    "top_album": r["top_album"],
                }
                for r in rows
            ]

        # Default: tracks.
        order = _TRACK_SORT.get(sort, "ORDER BY RANDOM()")
        rows = self._conn.execute(
            f"""
            SELECT
                t.id, t.file_path, t.title, t.artist, t.album_artist, t.album,
                t.release_date, t.track_number, t.disc_number, t.ext, t.embedded_art,
                t.mb_release_id, t.mb_recording_id, t.genre, t.label,
                t.favorite, t.play_count, t.last_played, t.source, t.is_available, t.duration
            FROM tracks_with_stats t
            WHERE t.id IN ({placeholders})
            {order}
            LIMIT ?
            """,
            (*track_ids, limit),
        ).fetchall()
        return [
            {
                "playlist_track_id": None,
                "position": 0,
                "id": r["id"],
                "file_path": r["file_path"],
                "title": r["title"],
                "artist": r["artist"],
                "album_artist": r["album_artist"],
                "album": r["album"],
                "release_date": r["release_date"],
                "track_number": r["track_number"],
                "disc_number": r["disc_number"],
                "ext": r["ext"],
                "embedded_art": bool(r["embedded_art"]),
                "mb_release_id": r["mb_release_id"],
                "mb_recording_id": r["mb_recording_id"],
                "genre": r["genre"],
                "label": r["label"],
                "favorite": bool(r["favorite"]),
                "play_count": r["play_count"],
                "last_played": r["last_played"],
                "source": r["source"],
                "is_available": bool(r["is_available"]),
                "duration": r["duration"],
            }
            for r in rows
        ]

    def count_magic_criteria(self, criteria: "MagicCriteria") -> int:
        """Return the number of tracks that match *criteria* without fetching them."""
        from kamp_core.criteria import build_query

        where_fragment, params, needs_album_join = build_query(criteria)
        album_join = (
            "LEFT JOIN albums ON albums.id = tracks.album_id"
            if needs_album_join
            else ""
        )
        sql = f"SELECT COUNT(*) FROM tracks_with_stats AS tracks {album_join} WHERE {where_fragment}"
        row = self._conn.execute(sql, params).fetchone()
        return int(row[0])

    def list_all_magic_criteria(self) -> list[tuple[int, "MagicCriteria"]]:
        """Return ``(playlist_id, MagicCriteria)`` pairs for every magic playlist.

        Used by the server to build the field_index at startup and after CRUD.
        """
        rows = self._conn.execute(
            "SELECT playlist_id, criteria_json FROM magic_playlist_criteria"
        ).fetchall()
        return [
            (r["playlist_id"], MagicCriteria.from_dict(json.loads(r["criteria_json"])))
            for r in rows
        ]


# Track fields that extensions are permitted to write via apply_metadata_update.
# Excludes internal columns (embedded_art, play_count, last_played, etc.) so
# extensions cannot corrupt playback state or override quality signals.
_WRITABLE_TRACK_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "artist",
        "album_artist",
        "album",
        "release_date",
        "track_number",
        "disc_number",
        "mb_release_id",
    }
)


def _canonical_track_uri(path: "Path | str") -> str:
    """Return the canonical file_path key for *path*.

    For bandcamp:// URIs, normalises the single-slash POSIX form (bandcamp:/)
    and Windows backslash form (bandcamp:\\) back to the canonical double-slash
    form (bandcamp://) used throughout the codebase.  Local paths pass through
    unchanged.

    Only paths whose string representation *starts* with "bandcamp:" are
    treated as remote URIs; a local path that merely contains "bandcamp:" in a
    subdirectory name is left as-is.

    This mirrors the identically-named function in kamp_core.playback; it is
    defined here separately to avoid a circular import (playback imports Track
    from this module).
    """
    s = str(path)
    if s.startswith("bandcamp:"):
        rest = s.split("bandcamp:", 1)[1].lstrip("/\\").replace("\\", "/")
        return "bandcamp://" + rest
    return s


def _derive_source_fields(
    file_path: "Path | str", source: str, sale_item_id: "str | None"
) -> "tuple[str, str, str, str | None]":
    """Map a legacy tracks row onto its canonical track_sources fields (KAMP-540).

    Returns (uri, kind, provider, provider_item_id) per design note §3:
    - uri: the canonical file_path (bandcamp:// slash forms normalised).
    - kind: 'file' for a local file, else 'stream'.
    - provider: 'bandcamp' for a streamed row or a download-provenanced local
      file (has a sale_item_id); '' for an unaffiliated rip.
    - provider_item_id: the sale_item_id; for a pure streaming row (which stores
      the id only in its bandcamp://<sid>/<n> URI, not tracks.sale_item_id) the
      id is parsed back out of the URI.

    Shared by the v45 backfill and upsert_many so the scan and the migration
    derive identical rows.
    """
    uri = _canonical_track_uri(file_path)
    kind = "file" if source == "local" else "stream"
    provider = "bandcamp" if (source == "bandcamp" or sale_item_id) else ""
    provider_item_id = sale_item_id or None
    if (
        provider == "bandcamp"
        and not provider_item_id
        and uri.startswith("bandcamp://")
    ):
        provider_item_id = uri[len("bandcamp://") :].split("/", 1)[0] or None
    return uri, kind, provider, provider_item_id


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
    int,
    float,
]:
    return (
        _canonical_track_uri(t.file_path),
        t.title,
        t.artist,
        t.album_artist,
        t.album,
        t.release_date,
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
        int(t.is_available),
        t.duration,
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


def _row_to_pending_ingest(row: sqlite3.Row) -> PendingIngest:
    return PendingIngest(
        id=row["id"],
        artifact_path=row["artifact_path"],
        sale_item_id=row["sale_item_id"],
        tralbum_id=row["tralbum_id"],
        created_at=row["created_at"],
    )


def _row_to_track(row: sqlite3.Row) -> Track:
    # DB rows store canonical bandcamp:// form (ensured by _track_to_params and
    # migration v22). Path() on POSIX still collapses the double-slash to single,
    # but all callers that need a stable string key use _canonical_track_uri() or
    # get_track_by_path(str) rather than str(track.file_path), so this is safe.
    return Track(
        id=row["id"],
        file_path=Path(row["file_path"]),
        # Apply display overrides (KAMP-467): if the user has set a display value,
        # use it in place of the Bandcamp-sourced canonical value.  The override is
        # NULL for local tracks and for streaming tracks the user has not edited.
        title=row["display_title"] or row["title"],
        artist=row["artist"],
        album_artist=row["display_album_artist"] or row["album_artist"],
        album=row["display_album"] or row["album"],
        release_date=row["release_date"],
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
        is_available=bool(row["is_available"]),
        duration=row["duration"],
        sale_item_id=row["sale_item_id"] or "",
    )


def _playlist_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "favorite": bool(row["favorite"]),
        "track_count": row["track_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_played_at": row["last_played_at"],
        "source": row["source"],
    }


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

    # mutagen.mp3.MP3 parses the MPEG stream (required for duration); kept
    # separate from the ID3 read above to avoid raising on fake test files.
    try:
        duration = mutagen.mp3.MP3(str(path)).info.length
    except Exception:
        duration = 0.0

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
        release_date=_str("TDRC"),
        title=_str("TIT2"),
        track_number=_parse_num(_str("TRCK")),
        disc_number=_parse_num(_str("TPOS")) or 1,
        embedded_art=any(k.startswith("APIC") for k in tags),
        mb_release_id=_str("TXXX:MusicBrainz Album Id")
        or _str("TXXX:MusicBrainz Release Id"),
        mb_recording_id=_str("TXXX:MusicBrainz Track Id"),
        genre=_str("TCON"),
        label=_str("TPUB"),
        duration=duration,
        sale_item_id=_str("TXXX:KAMP_SALE_ITEM_ID"),
    )


def _read_m4a_tags(path: Path) -> Track:
    audio = None
    try:
        audio = mutagen.mp4.MP4(str(path))
        tags = audio.tags or {}
    except Exception:
        tags = {}

    try:
        duration = float(audio.info.length) if audio is not None else 0.0
    except Exception:
        duration = 0.0

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
        release_date=_s("\xa9day"),
        title=_s("\xa9nam"),
        track_number=track_number,
        disc_number=disc_number or 1,
        embedded_art=bool(tags.get("covr")),
        mb_release_id=_s("----:com.apple.iTunes:MusicBrainz Album Id")
        or _s("----:com.apple.iTunes:MusicBrainz Release Id"),
        mb_recording_id=_s("----:com.apple.iTunes:MusicBrainz Track Id"),
        genre=_s("\xa9gen"),
        label=_s("----:com.apple.iTunes:LABEL"),
        duration=duration,
        sale_item_id=_s("----:com.apple.iTunes:KAMP_SALE_ITEM_ID"),
    )


def _read_vorbis_tags(path: Path, *, is_flac: bool) -> Track:
    """Read tags from a FLAC or OGG Vorbis file."""
    audio = None
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

    try:
        duration = float(audio.info.length) if audio is not None else 0.0
    except Exception:
        duration = 0.0

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
        release_date=_s("DATE"),
        title=_s("TITLE"),
        track_number=_parse_num(_s("TRACKNUMBER")),
        disc_number=_parse_num(_s("DISCNUMBER")) or 1,
        embedded_art=bool(pictures),
        mb_release_id=_s("MUSICBRAINZ_ALBUMID"),
        mb_recording_id=_s("MUSICBRAINZ_TRACKID"),
        genre=_s("GENRE"),
        label=_s("LABEL") or _s("ORGANIZATION"),
        duration=duration,
        sale_item_id=_s("KAMP_SALE_ITEM_ID"),
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
    release_date: str | None = None,
    mb_release_id: str | None = None,
) -> None:
    """Write genre, label, release_date, and/or mb_release_id to an audio file without moving it.

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
        if release_date is not None:
            tags["TDRC"] = id3.TDRC(encoding=3, text=release_date)
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
        if release_date is not None:
            audio.tags["\xa9day"] = [release_date]  # type: ignore[index]
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
        if release_date is not None:
            audio.tags["DATE"] = [release_date]  # type: ignore[index]
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
        if release_date is not None:
            audio.tags["DATE"] = [release_date]  # type: ignore[index]
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
        if newly_added:
            self._index.inherit_remote_favorites(newly_added)
            self._index.inherit_remote_play_counts(newly_added)
        added = len(newly_added) + len(reconciled_new_paths)
        updated = len([t for t in upsert_subset if t.file_path in to_update])

        removed = 0
        for path in removed_paths:
            if path not in reconciled_old_paths:
                self._index.remove_track(path)
                removed += 1

        # A removal may have emptied an album; prune the now-orphaned local
        # rows so a deleted album folder disappears entirely (KAMP-522) rather
        # than lingering as a zero-track ghost. Only when something was removed
        # — idle scans never touch the albums table.
        if removed:
            pruned = self._index.prune_empty_albums()
            if pruned:
                logger.info("Pruned %d empty album(s) after scan removals", pruned)

        unchanged = len(on_disk & in_index) - len(to_update)

        return ScanResult(
            added=added,
            removed=removed,
            unchanged=unchanged,
            updated=updated,
            new_tracks=newly_added,
        )
