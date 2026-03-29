"""SQLite-backed library index and filesystem scanner."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mutagen.flac
import mutagen.id3 as id3
import mutagen.mp4
import mutagen.oggvorbis

logger = logging.getLogger(__name__)

_AUDIO_SUFFIXES = frozenset({".mp3", ".m4a", ".flac", ".ogg"})

_SCHEMA_VERSION = 1

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
    mb_recording_id  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS player_state (
    id         INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table
    track_path TEXT    NOT NULL,
    position   REAL    NOT NULL DEFAULT 0
);
"""


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


@dataclass
class AlbumInfo:
    album_artist: str
    album: str
    year: str
    track_count: int
    has_art: bool = False

    # Allow dict-style access so callers can use a["album_artist"] etc.
    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


@dataclass
class ScanResult:
    added: int
    removed: int
    unchanged: int


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
        self._migrate()

    def _make_conn(self) -> sqlite3.Connection:
        """Open a new SQLite connection configured for use in this index."""
        # check_same_thread=False: each connection is only *used* by the thread
        # that created it (enforced by threading.local), but close() is called
        # from the main thread at shutdown and needs to reach all connections.
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
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
        """Create schema and stamp migration version if not already done."""
        self._conn.executescript(_DDL)
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,)
            )
            self._conn.commit()

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
                 mb_release_id, mb_recording_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                mb_recording_id = excluded.mb_recording_id
            """,
            [_track_to_params(t) for t in tracks],
        )
        self._conn.commit()

    def remove_track(self, file_path: Path) -> None:
        """Remove the track with the given file path from the index."""
        self._conn.execute("DELETE FROM tracks WHERE file_path = ?", (str(file_path),))
        self._conn.commit()

    def all_tracks(self) -> list[Track]:
        """Return all indexed tracks in insertion order."""
        rows = self._conn.execute("SELECT * FROM tracks").fetchall()
        return [_row_to_track(r) for r in rows]

    def albums(self) -> list[AlbumInfo]:
        """Return one AlbumInfo per (album_artist, album) pair, sorted."""
        rows = self._conn.execute("""
            SELECT album_artist, album, year, COUNT(*) AS track_count,
                   MAX(embedded_art) AS has_art
            FROM tracks
            GROUP BY album_artist, album
            ORDER BY album_artist COLLATE NOCASE, album COLLATE NOCASE
            """).fetchall()
        return [
            AlbumInfo(
                album_artist=r["album_artist"],
                album=r["album"],
                year=r["year"],
                track_count=r["track_count"],
                has_art=bool(r["has_art"]),
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

    def get_track_by_path(self, path: Path) -> "Track | None":
        """Return the track for *path*, or None if not indexed."""
        row = self._conn.execute(
            "SELECT * FROM tracks WHERE file_path = ?", (str(path),)
        ).fetchone()
        return _row_to_track(row) if row else None

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

    def close(self) -> None:
        with self._all_conns_lock:
            for conn in self._all_conns:
                conn.close()
            self._all_conns.clear()


def _track_to_params(
    t: Track,
) -> tuple[str, str, str, str, str, str, int, int, str, int, str, str]:
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

    return Track(
        file_path=path,
        ext="mp3",
        artist=_str("TPE1"),
        album_artist=_str("TPE2"),
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

    return Track(
        file_path=path,
        ext="m4a",
        artist=_s("\xa9ART"),
        album_artist=_s("aART"),
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
        tags: dict[str, list[str]] = dict(audio.tags or {})
        pictures = getattr(audio, "pictures", [])
    except Exception:
        tags = {}
        pictures = []

    def _s(key: str) -> str:
        vals = tags.get(key)
        return vals[0] if vals else ""

    return Track(
        file_path=path,
        ext="flac" if is_flac else "ogg",
        artist=_s("ARTIST"),
        album_artist=_s("ALBUMARTIST"),
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
    """Dispatch to the appropriate tag reader; return None on unrecognised format."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            return _read_mp3_tags(path)
        if suffix == ".m4a":
            return _read_m4a_tags(path)
        if suffix == ".flac":
            return _read_vorbis_tags(path, is_flac=True)
        if suffix == ".ogg":
            return _read_vorbis_tags(path, is_flac=False)
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

        Files already in the index are left untouched (unchanged).
        New files are read and added. Index entries whose files no longer
        exist on disk are removed.

        *on_progress*, if provided, is called after each new file's tags are
        read with (current, total) where total = number of new files to index.
        """
        if not library_path.exists():
            return ScanResult(added=0, removed=0, unchanged=0)

        on_disk: set[Path] = {
            p
            for p in library_path.rglob("*")
            if p.is_file() and p.suffix.lower() in _AUDIO_SUFFIXES
        }
        in_index = self._index.indexed_paths()

        # Read tags for all new files, then commit in one transaction.
        to_add = on_disk - in_index
        total = len(to_add)
        new_tracks: list[Track] = []
        for current, path in enumerate(to_add, start=1):
            track = _read_tags(path)
            if track is not None:
                new_tracks.append(track)
            else:
                logger.warning("Skipped unreadable file: %s", path)
            if on_progress is not None:
                on_progress(current, total)
        self._index.upsert_many(new_tracks)
        added = len(new_tracks)

        removed = 0
        for path in in_index - on_disk:
            self._index.remove_track(path)
            removed += 1

        unchanged = len(on_disk & in_index)

        return ScanResult(added=added, removed=removed, unchanged=unchanged)
