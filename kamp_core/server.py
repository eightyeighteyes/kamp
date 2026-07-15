"""FastAPI application for the Kamp music player.

create_app() wires together a LibraryIndex, MpvPlaybackEngine, and
PlaybackQueue into a REST + WebSocket API.  The caller is responsible for
constructing and owning those objects; the server holds references only.

REST base: /api/v1/
WebSocket:  /api/v1/ws   — client sends "ping", server replies with a
                           player.state snapshot; initial snapshot is pushed
                           on connect.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import queue as _queue
import re
import sys
import threading as _threading
import uuid as _uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from kamp_core.library import (
    ArtistInfo,
    LibraryIndex,
    LibraryScanner,
    LibraryStats,
    MagicCriteria,
    NoStreamableVersionError,
    Track,
    _canonical_track_uri,
    extract_art,
)
from kamp_core.playback import MpvPlaybackEngine, PlaybackQueue

# ---------------------------------------------------------------------------
# Playback URI resolution
# ---------------------------------------------------------------------------


def resolve_playback_uri(
    track: Track,
    index: LibraryIndex,
    refresh_stream_url: Callable[[str, int], tuple[str, float] | None] | None,
    check_stream_url: Callable[[str], int] | None = None,
) -> str:
    """Return the URL or path string to pass to mpv for *track*.

    For local tracks returns str(track.file_path).  For remote tracks:
    1. Proactively refreshes the cached CDN URL if it is near/past expiry.
    2. Makes a HEAD request to validate the URL before handing it to mpv.
       If the CDN returns a 4xx (e.g. 410 Gone for an invalidated session
       token), forces a fresh refresh so mpv always receives a live URL.

    A brief validation delay is far better than silently skipping a track.
    """
    # Resolve the preferred delivery via track_sources (KAMP-541): a collapsed
    # track has both a file and a stream source, and the preferred-source rule
    # picks the local file when present/available and falls through to the stream
    # otherwise. On the pre-collapse DB each track has exactly one source, so this
    # is behaviour-neutral. A track with no source row (pre-KAMP-540 / a synthetic
    # queue-restore stub) falls back to the legacy Track columns.
    _src = index.preferred_source(track.id) if track.id else None
    _cached_url: str | None
    _cached_expires: float | None
    _source_id: int | None
    if _src is not None:
        _kind = str(_src["kind"])
        _src_uri = str(_src["uri"])
        _cached_url = _src["stream_url"]
        _cached_expires = _src["stream_url_expires_at"]
        _source_id = int(_src["id"])
    else:
        _kind = "stream" if track.is_remote else "file"
        _src_uri = str(track.file_path)
        _cached_url = track.stream_url
        _cached_expires = track.stream_url_expires_at
        _source_id = None

    if _kind == "file":
        return _src_uri

    import time as _t

    _now = _t.time()

    # Parse bandcamp://sale_id/track_num from the source uri — needed by both the
    # proactive-expiry path and the HEAD-triggered forced-refresh path.
    # Path() mutates the URI differently per platform (POSIX collapses //,
    # Windows converts to \\), so split on the scheme literal instead.
    _after_scheme = _src_uri.split("bandcamp:", 1)
    _canonical_fp: str | None = None
    _album_url: str = ""
    _track_num: int = 0
    if len(_after_scheme) == 2:
        _rest = _after_scheme[1].lstrip("/\\").replace("\\", "/")
        _canonical_fp = "bandcamp://" + _rest
        _parts = _rest.split("/", 1)
        if len(_parts) == 2:
            _sale_item_id, _track_num_str = _parts
            try:
                _track_num = int(_track_num_str)
            except ValueError:
                pass
            _item = index.get_collection_item(_sale_item_id)
            _album_url = (_item or {}).get("album_url", "")

    def _do_refresh(reason: str) -> str | None:
        """Fetch a fresh CDN URL and persist it; return the URL or None."""
        if not _album_url or refresh_stream_url is None or _canonical_fp is None:
            if not _album_url and _canonical_fp:
                logger.warning(
                    "resolve_playback_uri: no album_url for %s — cannot "
                    "refresh stream URL; run kamp sync to populate.",
                    _canonical_fp,
                )
            return None
        logger.info(
            "resolve_playback_uri: refreshing stream URL for %s (%s)",
            _canonical_fp,
            reason,
        )
        result = refresh_stream_url(_album_url, _track_num)
        if result is not None:
            new_url, expires_at = result
            # Persist onto the source row when we resolved one, else the legacy
            # tracks column (pre-KAMP-540 fallback).
            if _source_id is not None:
                index.update_stream_url_for_source(_source_id, new_url, expires_at)
            else:
                index.update_stream_url(_canonical_fp, new_url, expires_at)
            logger.info(
                "resolve_playback_uri: stream URL refreshed for %s "
                "(new expires_at=%.0f)",
                _canonical_fp,
                expires_at,
            )
            return new_url
        logger.warning(
            "resolve_playback_uri: refresh failed for %s",
            _canonical_fp,
        )
        return None

    # Step 1 — proactive refresh when the cached URL is near or past expiry.
    url = _cached_url or _src_uri
    needs_refresh = _cached_expires is None or _cached_expires < _now + 60
    if needs_refresh:
        refreshed = _do_refresh(f"expires_at={_cached_expires}")
        if refreshed is not None:
            url = refreshed
    else:
        logger.debug(
            "resolve_playback_uri: cached stream URL for %s (expires in %.0fs)",
            _canonical_fp or _src_uri,
            (_cached_expires or 0) - _now,
        )

    # Step 2 — HEAD request to verify the URL is live before handing to mpv.
    # A 4xx from the CDN means the signed token was invalidated (e.g. 410 Gone
    # when Bandcamp's session key rotates); force a fresh refresh in that case.
    # This adds a round-trip but prevents silent track-skipping on stale tokens.
    if url.startswith("https://") and check_stream_url is not None:
        status = check_stream_url(url)
        if 400 <= status < 500:
            logger.warning(
                "resolve_playback_uri: HEAD returned %d for %s — "
                "forcing refresh before playback",
                status,
                _canonical_fp,
            )
            refreshed = _do_refresh(f"HEAD {status}")
            if refreshed is not None:
                url = refreshed

    return url


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SourceOut(BaseModel):
    """One delivery of a track (KAMP-537), for display only — local/stream badge,
    offline state, remove-download affordance. Ordered preferred-first. NOT the
    client's playability signal: playback is server-resolved and a legacy row can
    be playable with an empty sources list, so TrackOut.is_available/duration
    (the preferred source) remain authoritative."""

    kind: str  # 'file' | 'stream'
    provider: str
    uri: str
    is_available: bool
    duration: float

    @classmethod
    def from_row(cls, row: Any) -> "SourceOut":
        return cls(
            kind=row["kind"],
            provider=row["provider"] or "",
            uri=row["uri"],
            is_available=bool(row["is_available"]),
            duration=float(row["duration"] or 0.0),
        )


class TrackOut(BaseModel):
    id: int
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
    genre: str
    label: str
    favorite: bool
    play_count: int
    source: str
    reachable: bool = True
    is_available: bool = True
    duration: float = 0.0
    sources: list[SourceOut] = []

    @classmethod
    def from_track(cls, t: Track, sources: "list[Any] | None" = None) -> "TrackOut":
        return cls(
            id=t.id,
            title=t.title,
            artist=t.artist,
            album_artist=t.album_artist,
            album=t.album,
            release_date=t.release_date,
            track_number=t.track_number,
            disc_number=t.disc_number,
            ext=t.ext,
            embedded_art=t.embedded_art,
            mb_release_id=t.mb_release_id,
            mb_recording_id=t.mb_recording_id,
            genre=t.genre,
            label=t.label,
            favorite=t.favorite,
            play_count=t.play_count,
            source=t.source,
            reachable=t.reachable,
            is_available=t.is_available,
            duration=t.duration,
            sources=[SourceOut.from_row(r) for r in (sources or [])],
        )


class ArtistOut(BaseModel):
    name: str
    play_time: float  # total elapsed playback seconds
    top_album: str | None

    @classmethod
    def from_artist(cls, a: ArtistInfo) -> "ArtistOut":
        return cls(name=a.name, play_time=a.play_time, top_album=a.top_album)


class StatsOut(BaseModel):
    track_count: int
    album_count: int
    artist_count: int
    total_play_seconds: float
    total_track_plays: int
    albums_played: int
    top_artist_name: str | None
    top_artist_seconds: float | None
    top_tracks: list[TrackOut]

    @classmethod
    def from_stats(cls, s: LibraryStats, top_tracks_out: list[TrackOut]) -> "StatsOut":
        return cls(
            track_count=s.track_count,
            album_count=s.album_count,
            artist_count=s.artist_count,
            total_play_seconds=s.total_play_seconds,
            total_track_plays=s.total_track_plays,
            albums_played=s.albums_played,
            top_artist_name=s.top_artist_name,
            top_artist_seconds=s.top_artist_seconds,
            top_tracks=top_tracks_out,
        )


class AlbumOut(BaseModel):
    album_artist: str
    album: str
    release_date: str
    track_count: int
    has_art: bool
    missing_album: bool = False
    # KAMP-554: a missing-album card is always exactly one track (album tag empty);
    # its canonical id is the stable lookup key instead of (album_artist, album).
    # None for real albums.
    track_id: int | None = None
    # MAX(file_mtime) across the album's tracks — appended to art URLs as ?v=
    # so the browser caches images by URL and only re-fetches when files change.
    art_version: float | None = None
    # MIN(date_added) across the album's tracks — used by the New Arrivals module.
    added_at: float | None = None
    # MAX(last_played) across the album's tracks — used by the Last Played module.
    last_played_at: float | None = None
    # SUM(play_count) / COUNT(*) across tracks — used by the Top Albums module.
    play_count_avg: float = 0.0
    # True when the user has favorited this album (KAMP-293).
    favorite: bool = False
    # True when any track in this album is individually favorited (KAMP-294).
    has_favorite_track: bool = False
    # 'local' | 'bandcamp' | 'mixed' — derived from constituent track sources.
    source: str = "local"
    # True when any track in this album has source != 'local'.
    has_remote_tracks: bool = False
    # Bandcamp sale_item_id parsed from constituent track file paths; None for local albums.
    sale_item_id: str | None = None
    # True when the album is a Bandcamp pre-order (some tracks not yet released).
    is_preorder: bool = False
    # Streamable-track count Bandcamp reports; 0 => no streamable version, so the
    # UI hides "Remove download" (KAMP-527). Snapshot; the server re-verifies.
    num_streamable_tracks: int = 0
    # Bandcamp album page URL — non-empty for Bandcamp albums (KAMP-367).
    album_url: str = ""
    # User-set display overrides for streaming albums (KAMP-467). None means no override.
    display_album: str | None = None
    display_album_artist: str | None = None


class PlayerStateOut(BaseModel):
    playing: bool
    position: float
    duration: float
    volume: int
    current_track: TrackOut | None
    next_track: TrackOut | None = None
    buffering: bool = False


class PlayRequest(BaseModel):
    album_artist: str
    album: str
    track_index: int = 0
    # KAMP-554: a missing-album track is addressed by its canonical id; real albums
    # leave id=None and resolve by (album_artist, album).
    id: int | None = None


class PlayPlaylistRequest(BaseModel):
    playlist_id: int
    start_index: int = 0


class PlayFilesRequest(BaseModel):
    start_index: int = 0
    # KAMP-552: canonical ids only.
    ids: list[int] = []


class SeekRequest(BaseModel):
    position: float


class VolumeRequest(BaseModel):
    volume: int


class ShuffleRequest(BaseModel):
    shuffle: bool
    album_shuffle: bool = False


class RepeatRequest(BaseModel):
    mode: Literal["off", "queue", "album", "single"]


class ScanResult(BaseModel):
    added: int
    removed: int
    unchanged: int
    updated: int


class LibraryPathRequest(BaseModel):
    path: str


# Paths that must never be accepted as a library root, regardless of whether they
# exist and are directories. Entries are platform-specific: POSIX system roots on
# macOS/Linux, Windows system roots on Windows. Bare drive roots on Windows (e.g.
# C:\, D:\) are rejected separately in the validator via a len(parts) == 1 check
# so we don't have to enumerate every possible drive letter.
_FORBIDDEN_LIBRARY_ROOTS: frozenset[Path] = frozenset(
    Path(p).resolve()
    for p in (
        (
            r"C:\Windows",
            r"C:\Windows\System32",
            r"C:\Program Files",
            r"C:\Program Files (x86)",
            r"C:\ProgramData",
            r"C:\Users",
        )
        if sys.platform == "win32"
        else (
            "/",
            "/System",
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/etc",
            "/private/etc",
            "/var",
            "/private/var",
            "/Library",
            "/Applications",
            "/dev",
            "/proc",
            "/sys",
        )
    )
)


class FavoriteRequest(BaseModel):
    favorite: bool
    id: int  # KAMP-552: tracks are addressed by canonical id only


class AlbumFavoriteRequest(BaseModel):
    album_artist: str
    album: str
    favorite: bool


class ReorderDownloadsRequest(BaseModel):
    # KAMP-567: the desired order of the currently-'queued' items (a permutation);
    # the downloading item is fixed at the top and is not included.
    provider_item_ids: list[str]


class SearchOut(BaseModel):
    albums: list[AlbumOut]
    tracks: list[TrackOut]
    playlists: list[PlaylistSearchOut] = []


class QueueOut(BaseModel):
    tracks: list[TrackOut]
    position: int  # index of the currently playing track; -1 if empty
    shuffle: bool
    repeat: str


class AddToQueueRequest(BaseModel):
    id: int  # KAMP-552: tracks are addressed by canonical id only


class MoveQueueRequest(BaseModel):
    from_index: int
    to_index: int


class ReorderQueueRequest(BaseModel):
    order: list[int]


class InsertQueueRequest(BaseModel):
    index: int
    id: int  # KAMP-552: tracks are addressed by canonical id only


class AlbumQueueRequest(BaseModel):
    album_artist: str
    album: str
    # KAMP-554: missing-album track id; real albums leave id=None.
    id: int | None = None


class InsertAlbumQueueRequest(BaseModel):
    album_artist: str
    album: str
    index: int
    # KAMP-554: missing-album track id; real albums leave id=None.
    id: int | None = None


class RemoveFromQueueRequest(BaseModel):
    indices: list[int]


class SkipToRequest(BaseModel):
    position: int


class ConfigPatchRequest(BaseModel):
    key: str
    value: str


class LastfmConnectRequest(BaseModel):
    username: str
    password: str


class BandcampCookiePayload(BaseModel):
    cookies: list[dict[str, Any]]
    origins: list[dict[str, Any]] = []


class BandcampProxyFetchRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = {}
    body: str | None = None


class TrackTagsRequest(BaseModel):
    title: str
    overwrite: bool = False


class TrackMetaRequest(BaseModel):
    mb_recording_id: str


class AlbumTagsRequest(BaseModel):
    album: str | None = None
    album_artist: str | None = None
    # overwrite=True: replace any file that already exists at the target path.
    # skip_conflicts=True: leave colliding files at their old path, rename the rest.
    # Default (both False): stop on first collision and return 409.
    overwrite: bool = False
    skip_conflicts: bool = False


class AlbumTagsTrackResult(BaseModel):
    track_id: int
    old_path: str
    new_path: str
    error: str | None = None


class AlbumTagsDeferredResult(BaseModel):
    track_id: int
    op_id: int
    old_path: str
    new_path: str


class AlbumTagsOut(BaseModel):
    moved: list[TrackOut]
    # Tracks deferred because the file was playing when the PATCH arrived (KAMP-309).
    deferred: list[AlbumTagsDeferredResult] = []
    # Paths of files left at their original location due to skip_conflicts.
    skipped: list[str]
    failed: list[AlbumTagsTrackResult]


class TrackDisplayRequest(BaseModel):
    """Display-only title override for a streaming track (KAMP-467)."""

    display_title: str | None = None


class AlbumDisplayRequest(BaseModel):
    """Display-only overrides for a streaming album's title and artist (KAMP-467)."""

    album_artist: str
    album: str
    display_album: str | None = None
    display_album_artist: str | None = None


class AlbumMetaRequest(BaseModel):
    genre: str | None = None
    label: str | None = None
    release_date: str | None = None
    mb_release_id: str | None = None


class AlbumMetaOut(BaseModel):
    tracks: list[TrackOut]


class MusicBrainzTrackOut(BaseModel):
    track_number: int
    disc_number: int
    title: str
    recording_mbid: str


class MusicBrainzReleaseOut(BaseModel):
    mbid: str
    release_group_mbid: str
    title: str
    album_artist: str
    release_date: str
    label: str
    release_type: str
    tracks: list[MusicBrainzTrackOut]


class MusicBrainzLookupOut(BaseModel):
    # Ranked best-first. KAMP-230 always uses candidates[0]; KAMP-231 adds a picker.
    candidates: list[MusicBrainzReleaseOut]


class ItunesCandidateOut(BaseModel):
    title: str
    artist: str
    preview_url: str
    # mzstatic URL with "{size}" placeholder (e.g. "200x200bb") for the client
    # to substitute the desired resolution before calling the apply endpoint.
    artwork_url_template: str


class ItunesSearchOut(BaseModel):
    candidates: list[ItunesCandidateOut]


class ItunesApplyRequest(BaseModel):
    album_artist: str
    album: str
    # mzstatic URL template with "{size}" placeholder; server resolves to min_dimension.
    artwork_url_template: str


class BandcampProxyFetchResult(BaseModel):
    id: str
    status: int
    body: str
    content_type: str = "text/html"
    url: str | None = None


class PlaylistOut(BaseModel):
    id: int
    title: str
    favorite: bool
    track_count: int
    created_at: float
    updated_at: float
    last_played_at: float | None = None
    criteria: dict[str, Any] | None = None


class PlaylistSearchOut(PlaylistOut):
    source: str = "local"


class PlaylistTrackOut(BaseModel):
    playlist_track_id: int | None
    position: int
    id: int
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
    genre: str
    label: str
    favorite: bool
    play_count: int
    last_played: float | None = None
    date_added: float | None = None
    source: str
    is_available: bool
    duration: float
    sources: list[SourceOut] = []


class CreatePlaylistRequest(BaseModel):
    title: str
    criteria: dict[str, Any] | None = None


class PatchPlaylistRequest(BaseModel):
    title: str | None = None
    favorite: bool | None = None


class UpdateCriteriaRequest(BaseModel):
    criteria: dict[str, Any]


class CriteriaPreviewRequest(BaseModel):
    criteria: dict[str, Any]


class AddTrackToPlaylistRequest(BaseModel):
    album_artist: str | None = None
    album: str | None = None
    id: int | None = None  # KAMP-552: single track by canonical id


class ReorderPlaylistRequest(BaseModel):
    track_ids: list[int]


# ---------------------------------------------------------------------------
# Bandcamp proxy URL allowlist
# ---------------------------------------------------------------------------

# Only requests targeting these hostnames (or subdomains) may be forwarded to
# Electron's net.fetch, which carries Bandcamp session cookies.  This prevents
# a malicious extension or local process from exfiltrating credentials to an
# arbitrary host.
_ALLOWED_PROXY_HOSTS: frozenset[str] = frozenset(
    {"bandcamp.com", "f4.bcbits.com", "t4.bcbits.com"}
)

# SVG template for playlist placeholder art (KAMP-441).
# __TITLE__ is substituted with the (possibly-truncated) playlist title at request time.
_PLAYLIST_ART_TEMPLATE = """\
<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
  <rect width="200" height="200" fill="#141414"/>
  <circle cx="100" cy="100" r="86" fill="#1c1a16" stroke="#c4aa78" stroke-width="1.5"/>
  <circle cx="100" cy="100" r="78" fill="none" stroke="#2a2620" stroke-width="0.8"/>
  <circle cx="100" cy="100" r="70" fill="none" stroke="#2a2620" stroke-width="0.8"/>
  <circle cx="100" cy="100" r="62" fill="none" stroke="#2a2620" stroke-width="0.8"/>
  <circle cx="100" cy="100" r="54" fill="none" stroke="#2a2620" stroke-width="0.8"/>
  <circle cx="100" cy="100" r="46" fill="none" stroke="#2a2620" stroke-width="0.8"/>
  <circle cx="100" cy="100" r="38" fill="none" stroke="#2a2620" stroke-width="0.8"/>
  <circle cx="100" cy="100" r="32" fill="none" stroke="#2a2620" stroke-width="0.8"/>
  <circle cx="100" cy="100" r="26" fill="#bf7a20"/>
  <circle cx="100" cy="100" r="26" fill="none" stroke="#8a5515" stroke-width="1"/>
  <circle cx="100" cy="100" r="22.5" fill="none" stroke="#8a5515" stroke-width="0.6" stroke-dasharray="2.5 2"/>
  <!-- Title sits above the spindle hole (hole top y≈96.5); baseline at y=95 clears the hole -->
  <text x="100" y="95" text-anchor="middle" fill="#1c1a16"
        font-size="5.5" font-weight="700" letter-spacing="0.5"
        font-family="sans-serif">__TITLE__</text>
  <circle cx="100" cy="100" r="3.5" fill="#141414"/>
</svg>"""


# OS metadata filenames that macOS (and Windows) drop into every directory.
# These make rmdir() fail even on "empty" folders, so we remove them first.
_OS_METADATA_NAMES: frozenset[str] = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", ".Spotlight-V100", ".Trashes"}
)

_COVER_ART_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
)


def _scrub_os_metadata(directory: Path) -> None:
    """Remove known OS-generated metadata files from *directory*.

    Called before rmdir() so that Finder-created .DS_Store files don't prevent
    cleanup of otherwise-empty album/artist directories after a rename.
    """
    try:
        for entry in directory.iterdir():
            if entry.name in _OS_METADATA_NAMES:
                try:
                    entry.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _scrub_cover_art(directory: Path) -> None:
    """Remove image files and macOS resource forks from *directory*.

    Called before rmdir() on an album directory being vacated by
    remove_download, so cover.jpg and similar bundled artwork files don't
    prevent cleanup.  Only touches the top level — does not recurse.
    """
    try:
        for entry in directory.iterdir():
            if entry.is_file() and (
                entry.suffix.lower() in _COVER_ART_EXTENSIONS
                or entry.name.startswith("._")
            ):
                try:
                    entry.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _tracks_out(index: LibraryIndex, tracks: "list[Track]") -> list[TrackOut]:
    """Serialize a list of tracks to TrackOut with sources batch-fetched (KAMP-537).

    One sources_for_track_ids call for the whole list (no N+1). Synthetic queue
    restore stubs (id=0) are excluded from the batch and get an empty sources
    list. Use this instead of a bare `[TrackOut.from_track(t) for t in ...]`.
    """
    src_map = index.sources_for_track_ids([t.id for t in tracks if t.id])
    return [TrackOut.from_track(t, src_map.get(t.id, [])) for t in tracks]


def _track_out(index: LibraryIndex, track: Track) -> TrackOut:
    """Serialize a single track to TrackOut with its sources (KAMP-537)."""
    return TrackOut.from_track(
        track, index.sources_for_track_ids([track.id]).get(track.id, [])
    )


def _validate_proxy_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not any(host == h or host.endswith(f".{h}") for h in _ALLOWED_PROXY_HOSTS):
        raise HTTPException(
            status_code=422, detail=f"Proxy URL host not allowed: {host}"
        )
    return url


def _materialize_stream_tracks_or_422(
    index: LibraryIndex,
    item: dict[str, Any],
    get_bandcamp_session: "Callable[[], dict[str, Any] | None] | None",
) -> None:
    """Fetch + upsert bandcamp:// stream rows for a download-mode album (KAMP-527).

    Called from the DELETE-download endpoint before any deletion so a downloaded
    album gains the streamable representation it never had. Any failure — no
    session, no album_url, network/parse error, or an empty result (Bandcamp has
    no streamable version) — raises HTTP 422 so the caller deletes nothing. The
    underlying upsert carries its own rollback discipline.
    """
    album_url = item.get("album_url") or ""
    sale_item_id = str(item.get("sale_item_id") or "")
    session_data = get_bandcamp_session() if get_bandcamp_session else None
    if not session_data or not album_url:
        raise HTTPException(
            status_code=422,
            detail=(
                "Log in to Bandcamp to revert this download — a streamable "
                "version must be fetched before the local files are removed."
            ),
        )
    from kamp_daemon.bandcamp import (  # noqa: PLC0415
        _make_requests_session,
        fetch_album_tracks,
    )

    try:
        session = _make_requests_session(session_data)
        tracks = fetch_album_tracks(
            album_url,
            int(sale_item_id),
            item.get("band_name") or "",
            item.get("item_title") or "",
            session,
        )
    except Exception as exc:  # network, parse, or auth failure
        raise HTTPException(
            status_code=422,
            detail="Could not fetch the streamable version from Bandcamp.",
        ) from exc

    if not tracks:
        raise HTTPException(
            status_code=422,
            detail=(
                "No streamable version available for this album. Removing the "
                "download would remove it from your library, so it was kept."
            ),
        )
    index.materialize_stream_tracks(sale_item_id, tracks)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    index: LibraryIndex,
    engine: MpvPlaybackEngine,
    queue: PlaybackQueue,
    library_path: Path | None = None,
    on_library_path_set: Callable[[Path], None] | None = None,
    ui_active_view: str = "library",
    ui_sort_order: str = "album_artist",
    ui_sort_dir: str = "asc",
    ui_queue_panel_open: int = 0,
    on_ui_state_set: Callable[[str, str], None] | None = None,
    config_values: dict[str, Any] | None = None,
    on_config_set: Callable[[str, str], None] | None = None,
    on_lastfm_connect: Callable[[str, str], None] | None = None,
    on_lastfm_disconnect: Callable[[], None] | None = None,
    on_bandcamp_login_complete: Callable[[dict[str, Any]], None] | None = None,
    get_bandcamp_session: Callable[[], dict[str, Any] | None] | None = None,
    on_bandcamp_disconnect: Callable[[], None] | None = None,
    on_bandcamp_sync_trigger: Callable[[], None] | None = None,
    on_bandcamp_sync_all_trigger: Callable[[], None] | None = None,
    dl_queue: _queue.Queue[str] | None = None,
    refresh_stream_url: Callable[[str, int], tuple[str, float] | None] | None = None,
    check_stream_url: Callable[[str], int] | None = None,
    art_cache_dir: Path | None = None,
    dev_mode: bool = False,
    auth_token: str | None = None,
    mb_lookup_fn: Callable[..., Any] | None = None,
) -> FastAPI:
    """Return a configured FastAPI application.

    All mutable state (index, engine, queue) is owned by the caller.  This
    makes the app easy to test: pass mock objects, use TestClient, done.
    """
    app = FastAPI(title="Kamp", version="1")

    # Mutable containers for runtime-updatable state.
    # library_path can be changed via POST /api/v1/config/library-path.
    # scan_progress is written by the scan thread and read by GET /api/v1/library/scan/progress.
    # library_version is incremented by background scans; WebSocket connections
    # detect the bump on the next ping and push a "library.changed" notification.
    _state: dict[str, Any] = {
        "library_path": library_path,
        "scan_progress": {
            "active": False,
            "current": 0,
            "total": 0,
            "current_file": None,
            "current_artist": None,
            "top_artist": None,
            "num_albums": 0,
            "num_artists": 0,
        },
        "ui_active_view": ui_active_view,
        "ui_sort_order": ui_sort_order,
        "ui_sort_dir": ui_sort_dir,
        "ui_queue_panel_open": ui_queue_panel_open,
        "library_version": 0,
        "config": dict(config_values) if config_values is not None else {},
        # Pending proxy-fetch requests from the Python daemon subprocess.
        # id → {"id", "url", "method", "headers", "body", "event", "result"}
        "bandcamp_proxy_requests": {},
        "buffering": False,
    }

    # Proxy-fetch events that were broadcast but had no WS client connected to
    # receive them.  Keyed by request ID so they can be removed when answered.
    # A newly-connected WS client receives these immediately so that requests
    # made before the client connected are not silently dropped.
    _pending_proxy_fetches: dict[str, dict[str, Any]] = {}

    # Active WebSocket queues — one asyncio.Queue per connected client.
    # Events are broadcast to all queues so push notifications wake every client.
    _ws_queues: set[asyncio.Queue[dict[str, Any]]] = set()
    # The running event loop, captured on first WS connection (thread-safe puts
    # need call_soon_threadsafe, which requires the loop reference).
    _event_loop: asyncio.AbstractEventLoop | None = None

    def _broadcast(event: dict[str, Any]) -> None:
        """Thread-safe: enqueue *event* for every connected WebSocket client."""
        if _event_loop is None:
            return
        for q in list(_ws_queues):
            _event_loop.call_soon_threadsafe(q.put_nowait, event)

    def _notify_library_changed() -> None:
        """Push library.changed to all connected WebSocket clients immediately."""
        _state["library_version"] += 1
        _broadcast({"type": "library.changed"})

    def _notify_track_changed() -> None:
        """Broadcast a track.changed push event to all connected WebSocket clients."""
        _broadcast({"type": "track.changed", **_state_snapshot().model_dump()})

    def _notify_play_state_changed() -> None:
        """Broadcast a play_state.changed push event to all connected WebSocket clients."""
        # Only clear buffering when mpv reports it is actively playing. During a
        # playing→playing track switch, mpv briefly sets pause=True as the old
        # file ends; clearing on that transition would kill the indicator before
        # the new file loads. Clearing on playing=True is redundant (file-loaded
        # already cleared it) but harmless.
        if engine.state.playing:
            _state["buffering"] = False
        _broadcast({"type": "play_state.changed", **_state_snapshot().model_dump()})

    def _notify_album_download_status(sale_item_id: str, state: str) -> None:
        _broadcast(
            {
                "type": "bandcamp.album-download",
                "sale_item_id": sale_item_id,
                "state": state,
            }
        )

    app.state.notify_album_download_status = _notify_album_download_status

    def _notify_album_download_progress(
        sale_item_id: str, downloaded_bytes: int, total_bytes: int
    ) -> None:
        """Broadcast byte-level download progress for a single album (KAMP-436/566).

        Rides the same ``bandcamp.album-download`` event as the coarse state
        transitions. Carries a ``progress`` percentage (0–100) — kept for the
        KAMP-436 bottom-up album-art reveal — plus the raw ``downloaded_bytes`` /
        ``total_bytes`` for the Downloads view. Called from the syncer's download
        thread — ``_broadcast`` is thread-safe.
        """
        pct = min(100, downloaded_bytes * 100 // total_bytes) if total_bytes else 0
        _broadcast(
            {
                "type": "bandcamp.album-download",
                "sale_item_id": sale_item_id,
                "state": "downloading",
                "progress": pct,
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
            }
        )

    app.state.notify_album_download_progress = _notify_album_download_progress

    def _notify_download_queue() -> None:
        """Broadcast a structured snapshot of the whole download queue (KAMP-566).

        Emitted on every queue transition (enqueue / downloading / done / failed /
        retry) so the Downloads view can render the Now Downloading / Queued /
        Failed sections. Each item carries status, position, size, error text and
        the album snapshot (see ``download_queue_items``); failed items carry their
        ``error_text``, which is how download errors reach the UI. Called from the
        worker/endpoint threads — ``_broadcast`` is thread-safe.
        """
        _broadcast({"type": "download.queue", "items": index.download_queue_items()})

    app.state.notify_download_queue = _notify_download_queue

    def _notify_bandcamp_sync_status(status_msg: str) -> None:
        """Broadcast sync state derived from the syncer's status_callback string.

        The syncer passes "" on idle and a non-empty string while syncing.
        Called from the syncer's background thread — _broadcast is thread-safe.
        """
        state = "idle" if not status_msg else "syncing"
        _broadcast({"type": "bandcamp.sync-status", "state": state})

    def _notify_pipeline_stage(
        stage: str,
        sale_item_id: str | None = None,
        committed: bool = False,
        album: str = "",
    ) -> None:
        """Broadcast the current pipeline stage to all connected WebSocket clients.

        stage is "" when idle, or a human-readable label ("Extracting", "Tagging",
        "Updating artwork", "Moving") while work is in progress.

        KAMP-562: *sale_item_id* identifies the album being processed (None for
        non-download drops) so a per-album card can show a tagging badge; the
        global pipeline indicator ignores it and reads only ``stage``.
        *committed* is True on the terminal "" reset only when the item reached
        the library, letting the UI tell success (rescan coming) from quarantine.
        KAMP-558: *album* is a human-readable album label ("" before extraction)
        so the pipeline indicator can show "Copying {album}…" / "Tagging {album}…".
        Called from the watcher thread — _broadcast is thread-safe.
        """
        _broadcast(
            {
                "type": "pipeline.stage",
                "stage": stage,
                "sale_item_id": sale_item_id,
                "committed": committed,
                "album": album,
            }
        )

    def _notify_audio_level(
        left_db: float, right_db: float, crest_db: float, peak_db: float
    ) -> None:
        """Broadcast real-time per-channel audio levels to WebSocket clients.

        Called from the engine's stdout reader thread at ~20 Hz.
        _broadcast is thread-safe (call_soon_threadsafe).
        """
        _broadcast(
            {
                "type": "audio.level",
                "left_db": left_db,
                "right_db": right_db,
                "crest_db": crest_db,
                "peak_db": peak_db,
            }
        )

    # Expose notifiers on app.state so the daemon can wire them into engine
    # callbacks (e.g. on_track_end, on_play_state_changed).
    app.state.notify_library_changed = _notify_library_changed
    app.state.notify_track_changed = _notify_track_changed
    app.state.notify_play_state_changed = _notify_play_state_changed
    app.state.notify_bandcamp_sync_status = _notify_bandcamp_sync_status
    app.state.notify_pipeline_stage = _notify_pipeline_stage

    def _notify_deferred_op_completed(track_id: int, op_id: int) -> None:
        # Refresh the in-memory queue so the renamed track shows the new path/title
        # immediately; loadQueue() called by the frontend on library.changed will
        # then see consistent data rather than the stale pre-rename Track object.
        updated = index.get_track_by_id(track_id)
        if updated is not None:
            queue.update_track_by_id(track_id, updated)
        # Broadcast deferred_op.completed BEFORE library.changed (done in execute_op)
        # so the frontend clears the pip before the library reload re-renders.
        _broadcast(
            {"type": "deferred_op.completed", "track_id": track_id, "op_id": op_id}
        )

    app.state.notify_deferred_op_completed = _notify_deferred_op_completed

    # Wired by the daemon after create_app() to suppress watcher events and
    # trigger a direct scan following a tag-edit file move.
    app.state.on_track_file_moved = None
    # Batch variant for album rename: suppress all moved pairs, then one scan.
    # Signature: (pairs: list[tuple[Path, Path]]) -> None
    app.state.on_album_tracks_moved = None

    # Pending debuff timer for last_played writes on skip endpoints (next/prev/skip-to).
    # Stored as a mutable cell so endpoint closures can replace it without nonlocal.
    _last_played_timer: list[_threading.Timer | None] = [None]

    def _record_track_started_immediate(fp: Path) -> None:
        """Write last_played now and cancel any pending debuff timer."""
        if _last_played_timer[0] is not None:
            _last_played_timer[0].cancel()
            _last_played_timer[0] = None
        index.record_track_started(fp)

    def _record_track_started_debounced(fp: Path) -> None:
        """Start a 5-second debuff timer; cancel any previous one first.

        Used by next/prev/skip-to so rapidly-skipped tracks don't appear in
        Last Played.  The timer fires _notify_track_changed() so the UI
        re-fetches albums and picks up the newly-written last_played value.
        """
        if _last_played_timer[0] is not None:
            _last_played_timer[0].cancel()

        def _fire(file_path: Path = fp) -> None:
            index.record_track_started(file_path)
            _notify_track_changed()
            _last_played_timer[0] = None

        t = _threading.Timer(5.0, _fire)
        _last_played_timer[0] = t
        t.start()

    def _resolve_playback(track: "Track") -> str:
        if track.is_remote:
            _state["buffering"] = True
            _broadcast({"type": "player.state", **_state_snapshot().model_dump()})
        try:
            return resolve_playback_uri(
                track, index, refresh_stream_url, check_stream_url
            )
        except Exception:
            _state["buffering"] = False
            raise

    # Wire play-state change callback directly — the engine fires it from its
    # background reader thread whenever mpv's pause property flips.
    engine.on_play_state_changed = _notify_play_state_changed
    engine.on_audio_level = _notify_audio_level

    # Outermost on_file_loaded wrapper: clear buffering the moment mpv opens
    # the new file. Wraps the __main__.py chain (gapless preload + scrobble)
    # which is already assigned before create_app is called.
    _outer_on_file_loaded = engine.on_file_loaded

    def _on_file_loaded_clear_buffering() -> None:
        _state["buffering"] = False
        _broadcast({"type": "player.state", **_state_snapshot().model_dump()})
        if _outer_on_file_loaded is not None:
            _outer_on_file_loaded()

    engine.on_file_loaded = _on_file_loaded_clear_buffering

    # Magic playlist field_index: maps field name → set of playlist IDs whose
    # criteria reference that field.  Rebuilt at startup and after CRUD ops.
    field_index: dict[str, set[int]] = {}
    # Per-playlist timestamp of last broadcast (for ≤1 event/second debounce).
    _last_magic_broadcast: dict[int, float] = {}

    def _rebuild_field_index() -> None:
        new_index: dict[str, set[int]] = {}
        for playlist_id, mc in index.list_all_magic_criteria():
            for group in mc.groups:
                for cond in group.conditions:
                    new_index.setdefault(cond.field, set()).add(playlist_id)
        field_index.clear()
        field_index.update(new_index)

    def _on_fields_changed(fields: set[str]) -> None:
        """Broadcast magic_playlist.updated for each playlist affected by *fields*.

        Called from LibraryIndex mutation methods (which run on various threads).
        _broadcast is thread-safe. Debounce suppresses duplicate events within 1s.
        """
        affected: set[int] = set()
        for f in fields:
            affected |= field_index.get(f, set())
        if not affected:
            return
        import time as _t  # noqa: PLC0415

        now = _t.time()
        for pid in affected:
            if now - _last_magic_broadcast.get(pid, 0.0) < 1.0:
                continue
            _last_magic_broadcast[pid] = now
            _broadcast({"type": "magic_playlist.updated", "id": pid})

    index.on_fields_changed = _on_fields_changed
    _rebuild_field_index()
    app.state.field_index = field_index
    app.state.on_fields_changed = _on_fields_changed

    # Auth middleware must be defined before add_middleware(CORSMiddleware) so
    # CORS ends up as the outermost wrapper (handles OPTIONS preflight first).
    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next: Any) -> Any:
        if auth_token is None or request.method == "OPTIONS":
            return await call_next(request)
        # Accept token via header (fetch/XHR) or query param (<img src> URLs).
        token = request.headers.get("X-Kamp-Token") or request.query_params.get("token")
        if token != auth_token:
            return Response(status_code=401)
        return await call_next(request)

    # Restrict to origins kamp actually serves; wildcard would allow any page
    # open in any browser to read session cookies via cross-origin requests.
    _allowed_origins = [
        "http://localhost",
        "http://127.0.0.1",
        # Electron renderer in production loads from file://; Chromium serializes
        # that as the opaque origin "null" in the Origin request header.
        "null",
    ]
    # In dev mode, Vite picks the first free port from 5173 upward (5174, 5175,
    # …) when an earlier dev session left a stale listener behind. Match any
    # localhost port via regex so the renderer keeps working across restarts.
    _allowed_origin_regex: str | None = (
        r"^http://(localhost|127\.0\.0\.1):\d+$" if dev_mode else None
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_origin_regex=_allowed_origin_regex,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        # X-Kamp-Token must be listed so CORS preflight allows it.
        allow_headers=["Content-Type", "X-Kamp-Token"],
    )

    def _state_snapshot() -> PlayerStateOut:
        import time as _t

        current = queue.current()
        nxt = queue.peek_next()
        pos = engine.state.position
        # When mpv stops emitting time-pos events (e.g. after seeking near EOF
        # of an HTTP stream where the demuxer is at EOF but audio drains from
        # the hardware buffer), extrapolate position from wall-clock time so the
        # progress bar advances smoothly instead of freezing.
        if (
            engine.state.playing
            and engine.state.duration > 0
            and _t.time() - engine.state.position_updated_at > 0.3
        ):
            pos = min(
                pos + (_t.time() - engine.state.position_updated_at),
                engine.state.duration,
            )
        return PlayerStateOut(
            playing=engine.state.playing,
            position=pos,
            duration=engine.state.duration,
            volume=engine.state.volume,
            current_track=_track_out(index, current) if current else None,
            next_track=_track_out(index, nxt) if nxt else None,
            buffering=_state["buffering"],
        )

    # -----------------------------------------------------------------------
    # Library
    # -----------------------------------------------------------------------

    @app.get("/api/v1/albums", response_model=list[AlbumOut])
    def get_albums(sort: str = "album_artist", direction: str = "") -> list[AlbumOut]:
        # direction="" means use the natural per-key default (historical behaviour).
        sort_dir = direction if direction in ("asc", "desc") else None
        return [
            AlbumOut(
                album_artist=a.album_artist,
                album=a.album,
                release_date=a.release_date,
                track_count=a.track_count,
                has_art=a.has_art,
                missing_album=a.missing_album,
                track_id=a.missing_track_id,
                art_version=a.art_version,
                added_at=a.added_at,
                last_played_at=a.last_played_at,
                play_count_avg=a.play_count_avg,
                favorite=a.favorite,
                has_favorite_track=a.has_favorite_track,
                source=a.source,
                has_remote_tracks=a.has_remote_tracks,
                sale_item_id=a.sale_item_id,
                is_preorder=a.is_preorder,
                num_streamable_tracks=a.num_streamable_tracks,
                album_url=a.album_url,
                display_album=a.display_album,
                display_album_artist=a.display_album_artist,
            )
            for a in index.albums(sort=sort, sort_dir=sort_dir)
        ]

    @app.get("/api/v1/stats", response_model=StatsOut)
    def get_stats(top_tracks: int = 3) -> StatsOut:
        s = index.get_stats(top_tracks_limit=top_tracks)
        return StatsOut.from_stats(s, _tracks_out(index, s.top_tracks))

    @app.get("/api/v1/artists/top", response_model=list[ArtistOut])
    def get_top_artists(limit: int = 10) -> list[ArtistOut]:
        return [ArtistOut.from_artist(a) for a in index.top_artists(limit)]

    @app.get("/api/v1/artists", response_model=list[str])
    def get_artists() -> list[str]:
        return index.artists()

    @app.get("/api/v1/tracks/top", response_model=list[TrackOut])
    def get_top_tracks(limit: int = 10) -> list[TrackOut]:
        return _tracks_out(index, index.top_tracks(limit))

    @app.get("/api/v1/tracks", response_model=list[TrackOut])
    def get_tracks(
        album_artist: str, album: str, track_id: int | None = None
    ) -> list[TrackOut]:
        # Query parameters instead of path segments — artist/album names may
        # contain slashes (e.g. "AC/DC") which would break URL path routing.
        # track_id addresses a missing-album track (album tag empty) where
        # (album_artist, album) is not a unique key; when present it takes precedence.
        if track_id is not None:
            track = index.get_track_by_id(track_id)
            return [_track_out(index, track)] if track else []
        return _tracks_out(index, index.tracks_for_album(album_artist, album))

    @app.patch("/api/v1/tracks/{track_id}/tags")
    def patch_track_tags(track_id: int, req: "TrackTagsRequest") -> Any:
        """Edit a track's title tag and rename the file on disk to match.

        Returns the updated track on success (200).  Returns 202 with
        ``{"deferred": true, "op_id": N}`` if the track is currently playing or
        queued as the gapless lookahead — the op runs after playback ends.
        Returns 404 if the track is not in the library.  Returns 409 with
        collision details if the computed target path already exists on disk;
        send the request again with overwrite=true to replace it.
        """
        import json as _json
        import shutil
        import time as _t

        from fastapi.responses import JSONResponse

        from kamp_core.library import write_title_to_file
        from kamp_core.path_utils import make_path_vars, render_destination

        track = index.get_track_by_id(track_id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")

        lib_path: Path | None = _state["library_path"]
        if lib_path is None:
            raise HTTPException(status_code=503, detail="Library path not configured")

        path_template: str = (
            _state["config"].get("library.path_template")
            or "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        )

        tags = make_path_vars(
            artist=track.artist,
            album_artist=track.album_artist,
            album=track.album,
            release_date=track.release_date,
            track=track.track_number,
            disc=track.disc_number,
            title=req.title,
            ext=track.ext,
        )
        old_path = track.file_path
        try:
            new_path = render_destination(tags, lib_path, path_template)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        # Detect case-only rename before the lock check so the payload is ready.
        is_case_only = str(old_path).lower() == str(new_path).lower() and str(
            old_path
        ) != str(new_path)

        # Defer if the track is currently playing or in the gapless-lookahead slot.
        # On Windows, open files cannot be renamed; on macOS/Linux the inode stays
        # valid but we defer everywhere for consistency (KAMP-309).
        current = queue.current()
        lookahead = queue.peek_next()
        if (current and current.id == track_id) or (
            lookahead and lookahead.id == track_id
        ):
            payload = _json.dumps(
                {
                    "old_path": str(old_path),
                    "new_path": str(new_path),
                    "title": req.title,
                    "is_case_only": is_case_only,
                }
            )
            op_id = index.queue_deferred_op("track_retag", track_id, payload)
            return JSONResponse(
                status_code=202, content={"deferred": True, "op_id": op_id}
            )

        if str(old_path) == str(new_path):
            # Path unchanged — just update the title tag and DB.
            write_title_to_file(old_path, req.title)
            index.move_track(old_path, old_path, req.title, _t.time())
            queue.update_track_path(old_path, old_path, req.title)
            _notify_library_changed()
            updated = index.get_track_by_id(track_id)
            return _track_out(index, updated)  # type: ignore[arg-type]

        # is_case_only was computed before the lock check so it is available
        # for both the deferred-op payload and the immediate rename path.
        # On case-insensitive filesystems (HFS+, APFS, NTFS) new_path.exists()
        # returns True for the same inode, which would incorrectly trigger a 409.

        if not is_case_only and new_path.exists():
            if not req.overwrite:
                existing = index.get_track_by_path(new_path)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "target_path": str(new_path),
                        "existing_track_id": existing.id if existing else None,
                    },
                )
            # Overwrite requested: remove the conflicting DB entry.
            # (new_path != old_path is guaranteed here: old_path == new_path returns early above)
            index.remove_track(new_path)

        # Order: write tags → move file → update DB.
        write_title_to_file(old_path, req.title)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        if is_case_only:
            # Two-step via a temp name so the OS doesn't silently treat it as a no-op.
            tmp_path = old_path.with_suffix(f".kamp_rename{old_path.suffix}")
            shutil.move(str(old_path), tmp_path)
            shutil.move(str(tmp_path), new_path)
        else:
            shutil.move(str(old_path), new_path)

        new_mtime = _t.time()
        index.move_track(old_path, new_path, req.title, new_mtime)

        # Patch the in-memory queue so mpv's next file reference and the
        # player-state snapshot both use the new path immediately.
        queue.update_track_path(old_path, new_path, req.title)

        # Suppress FSEvents from this move and fire a reconciliation scan.
        notify_track_moved = getattr(app.state, "on_track_file_moved", None)
        if notify_track_moved is not None:
            try:
                notify_track_moved(old_path, new_path)
            except Exception:
                logger.exception("on_track_file_moved callback raised")

        _notify_library_changed()
        updated = index.get_track_by_id(track_id)
        return _track_out(index, updated)  # type: ignore[arg-type]

    @app.patch("/api/v1/tracks/{track_id}/meta")
    def patch_track_meta(track_id: int, req: "TrackMetaRequest") -> "TrackOut":
        """Write a MusicBrainz recording ID to a track without renaming the file.

        Tag-only operation — no file move occurs.  Returns 404 if the track is
        not in the library.
        """
        from kamp_core.library import write_track_mbid_to_file

        track = index.get_track_by_id(track_id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")

        try:
            write_track_mbid_to_file(
                track.file_path, mb_recording_id=req.mb_recording_id
            )
        except Exception as exc:
            logger.exception(
                "MBID tag write failed for track %d (%s)", track.id, track.file_path
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to write MBID to {track.file_path}: {exc}",
            ) from exc

        updated = index.update_track_mb_recording_id(track_id, req.mb_recording_id)
        _notify_library_changed()
        return _track_out(index, updated)  # type: ignore[arg-type]

    @app.get("/api/v1/deferred-ops")
    def get_deferred_ops() -> list[dict[str, Any]]:
        """Return pending deferred ops for frontend reconciliation on WS reconnect."""
        return index.list_pending_deferred_ops_summary()

    @app.post("/api/v1/tracks/favorite")
    def set_track_favorite(req: FavoriteRequest) -> dict[str, Any]:
        track = index.get_track_by_id(req.id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        # Feed the resolved track's canonical uri to the (still file_path-keyed)
        # stat/queue writers; KAMP-539 makes these id-native (KAMP-537).
        key = _canonical_track_uri(track.file_path)
        index.set_favorite(key, req.favorite)
        # Keep the in-memory queue in sync so the next player-state snapshot
        # reflects the new favorite value without requiring a queue reload. Keyed
        # on the canonical id (KAMP-538/532): the queued track's delivery uri may
        # have diverged from `key` (post-download preferred-source flip), which a
        # uri match would miss, reverting the UI on the next 4 Hz state poll.
        queue.update_favorite(track.id, req.favorite)
        return {"ok": True}

    @app.post("/api/v1/albums/favorite")
    def set_album_favorite(req: AlbumFavoriteRequest) -> dict[str, Any]:
        index.toggle_album_favorite(req.album_artist, req.album, req.favorite)
        return {"ok": True}

    @app.patch("/api/v1/albums/tags")
    def patch_album_tags(
        album_artist: str, album: str, req: "AlbumTagsRequest"
    ) -> "AlbumTagsOut":
        """Rename album title and/or album artist across every track in the album.

        The album directory is renamed atomically with os.rename() — no per-file
        moves.  Tag writes and DB updates happen after the rename.

        Collision: if the target directory already exists, returns 409.
        - overwrite=True: moves each file from the old dir into the existing target,
          overwriting any same-name files (merge).
        - skip_conflicts=True: moves only files whose names don't already exist in
          the target (partial merge).
        """
        import os
        import shutil
        import sqlite3
        import time as _t

        from kamp_core.library import write_album_tags_to_file
        from kamp_core.path_utils import make_path_vars, render_destination

        tracks = index.tracks_for_album(album_artist, album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")

        new_album = req.album if req.album is not None else album
        new_album_artist = (
            req.album_artist if req.album_artist is not None else album_artist
        )

        if new_album == album and new_album_artist == album_artist:
            raise HTTPException(status_code=400, detail="No changes requested")

        # Pre-flight: reject before any file or DB mutation if the target name
        # already exists as a different album (e.g. a streaming-only entry).
        # This prevents file operations from running when the DB write would
        # fail anyway, and ensures the check fires before any transaction opens.
        current_id = index._album_id(album_artist, album)
        target_id = index._album_id(new_album_artist, new_album)
        if target_id is not None and target_id != current_id:
            raise HTTPException(
                status_code=409, detail="Album name already exists in library"
            )

        lib_path: Path | None = _state["library_path"]
        if lib_path is None:
            raise HTTPException(status_code=503, detail="Library path not configured")

        path_template: str = (
            _state["config"].get("library.path_template")
            or "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        )

        # Compute the target path for each track and derive the album directories.
        track_dest: list[tuple[Path, Path]] = []  # (old_path, new_path)
        for track in tracks:
            tags = make_path_vars(
                artist=track.artist,
                album_artist=new_album_artist,
                album=new_album,
                release_date=track.release_date,
                track=track.track_number,
                disc=track.disc_number,
                title=track.title,
                ext=track.ext,
            )
            try:
                new_path = render_destination(tags, lib_path, path_template)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            track_dest.append((track.file_path, new_path))

        old_paths = [op for op, _ in track_dest]
        new_paths = [np for _, np in track_dest]

        # Album directory = common ancestor of the directories containing the tracks.
        # Using .parent before commonpath avoids returning a file path for single-track albums
        # (commonpath(['/a/b/f.mp3']) == '/a/b/f.mp3', not '/a/b').
        old_album_dir = Path(os.path.commonpath([str(p.parent) for p in old_paths]))
        new_album_dir = Path(os.path.commonpath([str(p.parent) for p in new_paths]))

        total = len(tracks)
        moved: list[TrackOut] = []
        deferred: list[AlbumTagsDeferredResult] = []
        skipped: list[str] = []
        failed: list[AlbumTagsTrackResult] = []
        notify_album_tracks_moved = getattr(app.state, "on_album_tracks_moved", None)
        moved_path_pairs: list[tuple[Path, Path]] = []

        # Check whether any track in the album is locked (currently playing or in
        # the gapless-lookahead slot).  When any track is locked the atomic
        # directory rename is skipped in favour of per-file moves so the locked
        # track's file is never touched until its deferred op drains (KAMP-309).
        def _is_track_locked(tid: int) -> bool:
            c = queue.current()
            la = queue.peek_next()
            return (c is not None and c.id == tid) or (la is not None and la.id == tid)

        any_locked = any(_is_track_locked(t.id) for t in tracks)

        if old_album_dir == new_album_dir:
            # Tags changed but the path template produces the same directory.
            # Write tags in-place and update the DB — no filesystem move needed.
            _broadcast({"type": "album.rename.progress", "done": 0, "total": total})
            new_mtime = _t.time()
            db_pairs: list[tuple[Path, Path]] = []
            for i, (track, (old_path, new_path)) in enumerate(zip(tracks, track_dest)):
                _broadcast({"type": "album.rename.progress", "done": i, "total": total})
                # When the per-track artist matches the old album_artist, update it
                # too — keeps TPE1/artist in sync with TPE2/album_artist.
                new_artist = new_album_artist if track.artist == album_artist else None
                if _is_track_locked(track.id):
                    op_id = index.queue_deferred_op(
                        "album_retag",
                        track.id,
                        _json.dumps(
                            {
                                "old_path": str(old_path),
                                "new_path": str(new_path),
                                "new_album": new_album,
                                "new_album_artist": new_album_artist,
                                "new_artist": new_artist,
                                "is_case_only": False,
                            }
                        ),
                    )
                    deferred.append(
                        AlbumTagsDeferredResult(
                            track_id=track.id,
                            op_id=op_id,
                            old_path=str(old_path),
                            new_path=str(new_path),
                        )
                    )
                    # Pre-update DB album metadata so the library shows all tracks
                    # under the new album name immediately; file stays at old_path
                    # until the deferred op drains (idempotent when drain runs).
                    db_pairs.append((old_path, old_path))
                    queue.update_track_album_tags(
                        old_path,
                        old_path,
                        new_album,
                        new_album_artist,
                        new_artist=new_artist,
                    )
                    continue
                try:
                    write_album_tags_to_file(
                        old_path, new_album, new_album_artist, artist=new_artist
                    )
                    db_pairs.append((old_path, new_path))
                    queue.update_track_album_tags(
                        old_path,
                        new_path,
                        new_album,
                        new_album_artist,
                        new_artist=new_artist,
                    )
                    updated = index.get_track_by_id(track.id)
                    if updated is not None:
                        moved.append(_track_out(index, updated))
                except Exception as exc:
                    logger.exception("tag write failed for %s", old_path)
                    failed.append(
                        AlbumTagsTrackResult(
                            track_id=track.id,
                            old_path=str(old_path),
                            new_path=str(new_path),
                            error=str(exc),
                        )
                    )
            if db_pairs:
                try:
                    index.rename_album_tracks_bulk(
                        db_pairs,
                        new_album,
                        new_album_artist,
                        new_mtime,
                        old_album_artist=album_artist,
                    )
                except sqlite3.IntegrityError:
                    raise HTTPException(
                        status_code=409,
                        detail="Album name already exists in library",
                    )

        elif not new_album_dir.exists():
            # Happy path: target directory does not exist — atomic directory rename.
            _broadcast({"type": "album.rename.progress", "done": 0, "total": total})

            if any_locked:
                # Cannot do atomic rename while any track is playing.  Fall back to
                # per-file moves so locked files stay in place until deferred ops drain.
                new_album_dir.parent.mkdir(parents=True, exist_ok=True)
                new_album_dir.mkdir(exist_ok=True)
                new_mtime = _t.time()
                db_pairs = []
                for i, (track, (old_path, new_path)) in enumerate(
                    zip(tracks, track_dest)
                ):
                    _broadcast(
                        {"type": "album.rename.progress", "done": i, "total": total}
                    )
                    new_artist = (
                        new_album_artist if track.artist == album_artist else None
                    )
                    if _is_track_locked(track.id):
                        op_id = index.queue_deferred_op(
                            "album_retag",
                            track.id,
                            _json.dumps(
                                {
                                    "old_path": str(old_path),
                                    "new_path": str(new_path),
                                    "new_album": new_album,
                                    "new_album_artist": new_album_artist,
                                    "new_artist": new_artist,
                                    "is_case_only": False,
                                }
                            ),
                        )
                        deferred.append(
                            AlbumTagsDeferredResult(
                                track_id=track.id,
                                op_id=op_id,
                                old_path=str(old_path),
                                new_path=str(new_path),
                            )
                        )
                        # Pre-update DB album metadata so the library rescan sees
                        # all tracks under the new album name immediately.  The
                        # file stays at old_path; drain moves it and updates file_path.
                        db_pairs.append((old_path, old_path))
                        queue.update_track_album_tags(
                            old_path,
                            old_path,
                            new_album,
                            new_album_artist,
                            new_artist=new_artist,
                        )
                        continue
                    try:
                        write_album_tags_to_file(
                            old_path, new_album, new_album_artist, artist=new_artist
                        )
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(old_path), str(new_path))
                        index.rewrite_deferred_op_old_path(
                            track.id, str(old_path), str(new_path)
                        )
                        db_pairs.append((old_path, new_path))
                        moved_path_pairs.append((old_path, new_path))
                        queue.update_track_album_tags(
                            old_path,
                            new_path,
                            new_album,
                            new_album_artist,
                            new_artist=new_artist,
                        )
                        updated = index.get_track_by_id(track.id)
                        if updated is not None:
                            moved.append(_track_out(index, updated))
                    except Exception as exc:
                        logger.exception("album per-file move failed for %s", old_path)
                        failed.append(
                            AlbumTagsTrackResult(
                                track_id=track.id,
                                old_path=str(old_path),
                                new_path=str(new_path),
                                error=str(exc),
                            )
                        )
                if db_pairs:
                    try:
                        index.rename_album_tracks_bulk(
                            db_pairs,
                            new_album,
                            new_album_artist,
                            new_mtime,
                            old_album_artist=album_artist,
                        )
                    except sqlite3.IntegrityError:
                        raise HTTPException(
                            status_code=409,
                            detail="Album name already exists in library",
                        )
            else:
                old_artist_dir = old_album_dir.parent
                new_artist_dir = new_album_dir.parent

                # When only the artist component changes and the old artist directory
                # contains nothing else, rename at the artist level in one syscall.
                # This matches the user's expectation: "Artist A/" → "Artist B/" directly,
                # rather than mkdir("Artist B"), rename album dir, rmdir("Artist A").
                try:
                    exclusive = not any(
                        e.name not in _OS_METADATA_NAMES
                        and e.name != old_album_dir.name
                        for e in old_artist_dir.iterdir()
                    )
                except OSError:  # pragma: no cover
                    exclusive = False

                rename_at_artist_level = (
                    old_album_dir.name == new_album_dir.name  # only artist dir changed
                    and old_artist_dir != new_artist_dir
                    and not new_artist_dir.exists()
                    and old_artist_dir != lib_path
                    and lib_path in old_artist_dir.parents
                    and exclusive
                )

                if rename_at_artist_level:
                    src, dst = old_artist_dir, new_artist_dir
                else:
                    new_album_dir.parent.mkdir(parents=True, exist_ok=True)
                    src, dst = old_album_dir, new_album_dir

                is_case_only = str(src).lower() == str(dst).lower() and str(src) != str(
                    dst
                )
                if (
                    is_case_only
                ):  # pragma: no cover — macOS HFS+ routes this to collision path
                    tmp = src.with_name(f"kamp_tmp_{src.name}")
                    os.rename(str(src), str(tmp))
                    os.rename(str(tmp), str(dst))
                else:
                    os.rename(str(src), str(dst))

                # Files are now under new_album_dir; write tags and bulk-update DB.
                # The directory rename already succeeded, so every file is at its new
                # path regardless of whether tag-writing succeeds.  Always include the
                # pair in db_pairs (DB must reflect the new path) and update the queue,
                # but report tag-write failures in failed[] rather than moved[].
                new_mtime = _t.time()
                db_pairs = []
                for i, (track, (old_path, _)) in enumerate(zip(tracks, track_dest)):
                    _broadcast(
                        {"type": "album.rename.progress", "done": i, "total": total}
                    )
                    new_path = new_album_dir / old_path.relative_to(old_album_dir)
                    new_artist = (
                        new_album_artist if track.artist == album_artist else None
                    )
                    tag_write_ok = True
                    try:
                        write_album_tags_to_file(
                            new_path, new_album, new_album_artist, artist=new_artist
                        )
                    except Exception as exc:
                        logger.exception("tag write failed for %s", new_path)
                        tag_write_ok = False
                        failed.append(
                            AlbumTagsTrackResult(
                                track_id=track.id,
                                old_path=str(old_path),
                                new_path=str(new_path),
                                error=str(exc),
                            )
                        )
                    db_pairs.append((old_path, new_path))
                    moved_path_pairs.append((old_path, new_path))
                    queue.update_track_album_tags(
                        old_path,
                        new_path,
                        new_album,
                        new_album_artist,
                        new_artist=new_artist,
                    )
                    if tag_write_ok:
                        updated = index.get_track_by_id(track.id)
                        if updated is not None:
                            moved.append(_track_out(index, updated))

                try:
                    index.rename_album_tracks_bulk(
                        db_pairs,
                        new_album,
                        new_album_artist,
                        new_mtime,
                        old_album_artist=album_artist,
                    )
                except sqlite3.IntegrityError:
                    raise HTTPException(
                        status_code=409,
                        detail="Album name already exists in library",
                    )

                # Album-level rename: clean up old artist dir if now empty.
                # (Artist-level rename already removed it by renaming the dir itself.)
                if not rename_at_artist_level:
                    _scrub_os_metadata(old_artist_dir)
                    try:
                        old_artist_dir.rmdir()
                    except OSError:
                        pass

        else:
            # Target directory already exists — collision.
            if not req.overwrite and not req.skip_conflicts:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "collision_count": sum(1 for _ in new_album_dir.iterdir()),
                        "first_path": str(new_album_dir),
                    },
                )
            # Merge: move individual files into the existing target directory.
            new_mtime = _t.time()
            db_pairs = []
            for i, (track, (old_path, new_path)) in enumerate(zip(tracks, track_dest)):
                _broadcast({"type": "album.rename.progress", "done": i, "total": total})
                # new_path here is the per-file destination inside new_album_dir.
                if new_path.exists() and req.skip_conflicts:
                    skipped.append(str(old_path))
                    continue
                new_artist = new_album_artist if track.artist == album_artist else None
                if _is_track_locked(track.id):
                    op_id = index.queue_deferred_op(
                        "album_retag",
                        track.id,
                        _json.dumps(
                            {
                                "old_path": str(old_path),
                                "new_path": str(new_path),
                                "new_album": new_album,
                                "new_album_artist": new_album_artist,
                                "new_artist": new_artist,
                                "is_case_only": False,
                            }
                        ),
                    )
                    deferred.append(
                        AlbumTagsDeferredResult(
                            track_id=track.id,
                            op_id=op_id,
                            old_path=str(old_path),
                            new_path=str(new_path),
                        )
                    )
                    db_pairs.append((old_path, old_path))
                    queue.update_track_album_tags(
                        old_path,
                        old_path,
                        new_album,
                        new_album_artist,
                        new_artist=new_artist,
                    )
                    continue
                try:
                    write_album_tags_to_file(
                        old_path, new_album, new_album_artist, artist=new_artist
                    )
                    if old_path != new_path:
                        if new_path.exists() and req.overwrite:
                            index.remove_track(new_path)
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(old_path), str(new_path))
                        index.rewrite_deferred_op_old_path(
                            track.id, str(old_path), str(new_path)
                        )
                    db_pairs.append((old_path, new_path))
                    moved_path_pairs.append((old_path, new_path))
                    queue.update_track_album_tags(
                        old_path,
                        new_path,
                        new_album,
                        new_album_artist,
                        new_artist=new_artist,
                    )
                    updated = index.get_track_by_id(track.id)
                    if updated is not None:
                        moved.append(_track_out(index, updated))
                except Exception as exc:
                    logger.exception(
                        "album merge failed for track %d (%s)", track.id, old_path
                    )
                    failed.append(
                        AlbumTagsTrackResult(
                            track_id=track.id,
                            old_path=str(old_path),
                            new_path=str(new_path),
                            error=str(exc),
                        )
                    )
            if db_pairs:
                try:
                    index.rename_album_tracks_bulk(
                        db_pairs,
                        new_album,
                        new_album_artist,
                        new_mtime,
                        old_album_artist=album_artist,
                    )
                except sqlite3.IntegrityError:
                    raise HTTPException(
                        status_code=409,
                        detail="Album name already exists in library",
                    )
            # Remove old album dir if all files were moved out.
            _scrub_os_metadata(old_album_dir)
            try:
                old_album_dir.rmdir()
                old_parent = old_album_dir.parent
                if old_parent != lib_path and lib_path in old_parent.parents:
                    _scrub_os_metadata(old_parent)
                    old_parent.rmdir()
            except OSError:
                pass

        _broadcast({"type": "album.rename.progress", "done": total, "total": total})

        if notify_album_tracks_moved is not None and moved_path_pairs:
            try:
                notify_album_tracks_moved(moved_path_pairs)
            except Exception:
                logger.exception("on_album_tracks_moved callback raised")

        _notify_library_changed()
        return AlbumTagsOut(
            moved=moved, deferred=deferred, skipped=skipped, failed=failed
        )

    @app.patch("/api/v1/tracks/{track_id}/display", response_model=TrackOut)
    def patch_track_display(track_id: int, req: "TrackDisplayRequest") -> TrackOut:
        """Set (or clear) the display title for a streaming track (KAMP-467).

        Writes only to the DB — no file operations are performed.
        Passing null or empty string for display_title clears the override and
        restores the Bandcamp canonical title.
        Returns 404 if the track is not found.
        """
        track = index.get_track_by_id(track_id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        updated = index.update_track_display_title(track_id, req.display_title)
        _notify_library_changed()
        return _track_out(index, updated)  # type: ignore[arg-type]

    @app.patch("/api/v1/albums/display", response_model=AlbumOut)
    def patch_album_display(req: "AlbumDisplayRequest") -> AlbumOut:
        """Set (or clear) display overrides for a streaming album (KAMP-467).

        Writes only to the DB — no file operations are performed.
        Passing null or empty string for a field clears that override.
        Returns 404 if the album is not found.
        """
        result = index.update_album_display(
            req.album_artist,
            req.album,
            req.display_album,
            req.display_album_artist,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Album not found")
        _notify_library_changed()
        return AlbumOut(
            album_artist=result.album_artist,
            album=result.album,
            release_date=result.release_date,
            track_count=result.track_count,
            has_art=result.has_art,
            missing_album=result.missing_album,
            track_id=result.missing_track_id,
            art_version=result.art_version,
            added_at=result.added_at,
            last_played_at=result.last_played_at,
            play_count_avg=result.play_count_avg,
            favorite=result.favorite,
            has_favorite_track=result.has_favorite_track,
            source=result.source,
            has_remote_tracks=result.has_remote_tracks,
            sale_item_id=result.sale_item_id,
            is_preorder=result.is_preorder,
            num_streamable_tracks=result.num_streamable_tracks,
            album_url=result.album_url,
            display_album=result.display_album,
            display_album_artist=result.display_album_artist,
        )

    @app.patch("/api/v1/albums/meta")
    def patch_album_meta(
        album_artist: str, album: str, req: "AlbumMetaRequest"
    ) -> "AlbumMetaOut":
        """Write genre, label, and/or release_date to every track in an album.

        Tag-only: no files are moved or renamed.  Only the fields present in
        the request body are written; omitted fields are left unchanged.
        """
        from kamp_core.library import write_meta_tags_to_file

        tracks = index.tracks_for_album(album_artist, album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")

        if (
            req.genre is None
            and req.label is None
            and req.release_date is None
            and req.mb_release_id is None
        ):
            raise HTTPException(status_code=400, detail="No changes requested")

        for track in tracks:
            if track.is_remote:
                continue
            try:
                write_meta_tags_to_file(
                    track.file_path,
                    genre=req.genre,
                    label=req.label,
                    release_date=req.release_date,
                    mb_release_id=req.mb_release_id,
                )
            except Exception as exc:
                logger.exception(
                    "meta tag write failed for track %d (%s)", track.id, track.file_path
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to write tags to {track.file_path}: {exc}",
                ) from exc

        updated = index.update_album_meta(
            album_artist,
            album,
            genre=req.genre,
            label=req.label,
            release_date=req.release_date,
            mb_release_id=req.mb_release_id,
        )
        _notify_library_changed()
        return AlbumMetaOut(tracks=_tracks_out(index, updated))

    @app.get("/api/v1/albums/musicbrainz", response_model=MusicBrainzLookupOut)
    async def get_album_musicbrainz(
        album_artist: str, album: str
    ) -> MusicBrainzLookupOut:
        """Fetch ranked MusicBrainz release candidates for an album.

        Uses the same tier-1 (per-track recording votes) + tier-2 (album-level
        search) strategy as the import pipeline.  Returns up to 5 candidates
        sorted best-first.  The frontend uses candidates[0] for KAMP-230;
        KAMP-231 will add a picker that steps through the full list.

        Returns 404 if no tracks are found or if MusicBrainz has no match.
        Returns 503 if mb_lookup_fn was not wired up at server construction.
        """
        if mb_lookup_fn is None:
            raise HTTPException(
                status_code=503, detail="MusicBrainz lookup not available"
            )

        tracks = index.tracks_for_album(album_artist, album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")

        tuples = [(t.artist, t.title, t.album) for t in tracks]
        try:
            from kamp_daemon.tagger import TaggingError

            releases = await asyncio.get_event_loop().run_in_executor(
                None, mb_lookup_fn, tuples
            )
        except Exception as exc:
            # TaggingError and network failures both surface as 404 with the
            # human-readable message so the frontend can show "No match found".
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        def _to_release_out(r: Any) -> MusicBrainzReleaseOut:
            sorted_tracks = sorted(r.tracks.values(), key=lambda t: (t.disc, t.number))
            return MusicBrainzReleaseOut(
                mbid=r.mbid,
                release_group_mbid=r.release_group_mbid,
                title=r.title,
                album_artist=r.album_artist,
                release_date=r.release_date,
                label=r.label,
                release_type=r.release_type,
                tracks=[
                    MusicBrainzTrackOut(
                        track_number=t.number,
                        disc_number=t.disc,
                        title=t.title,
                        recording_mbid=t.recording_mbid,
                    )
                    for t in sorted_tracks
                ],
            )

        return MusicBrainzLookupOut(candidates=[_to_release_out(r) for r in releases])

    @app.get("/api/v1/albums/art/search", response_model=ItunesSearchOut)
    async def search_album_art(album_artist: str, album: str) -> ItunesSearchOut:
        """Search iTunes for album art candidates matching *album_artist* and *album*.

        Returns up to 10 candidates with 200×200 preview URLs and artwork URL
        templates that the frontend resolves to a full-resolution image before
        calling the apply endpoint.  An empty candidate list (200 OK) means
        iTunes returned no results — this is not an error.
        Returns 404 if the album is not in the library.
        Returns 502 if the iTunes API is unreachable.
        """
        from kamp_daemon.artwork import (  # noqa: PLC0415 — lazy to avoid circular import
            ArtworkError,
            search_itunes,
        )

        tracks = index.tracks_for_album(album_artist, album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")

        try:
            candidates = await asyncio.get_event_loop().run_in_executor(
                None, search_itunes, album_artist, album
            )
        except ArtworkError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return ItunesSearchOut(
            candidates=[
                ItunesCandidateOut(
                    title=c.title,
                    artist=c.artist,
                    preview_url=c.preview_url,
                    artwork_url_template=c.artwork_url_template,
                )
                for c in candidates
            ]
        )

    # Regex for validating that an artwork_url targets Apple's CDN — prevents SSRF.
    _MZSTATIC_HOST_RE = re.compile(r"^[a-z0-9-]+\.mzstatic\.com$")

    @app.post("/api/v1/albums/art/apply", response_model=AlbumOut)
    async def apply_album_art(body: ItunesApplyRequest) -> AlbumOut:
        """Download *artwork_url* and embed it into every track of the album.

        The URL must target mzstatic.com (Apple's iTunes CDN).  If any track is
        currently playing or in the gapless-lookahead slot, returns 409 — try
        again after playback moves on.

        Returns 400 if the URL is not an mzstatic.com URL.
        Returns 404 if the album is not in the library.
        Returns 409 if any track in the album is currently locked by playback.
        Returns 422 if the downloaded image is below the configured minimum dimension.
        Returns 502 if the image download fails.
        Returns the updated AlbumOut (with new has_art and art_version) on success.
        """
        from kamp_daemon.artwork import (  # noqa: PLC0415
            ArtworkError,
            _embed,
            fetch_itunes_image,
            write_cover_file,
        )

        # SSRF guard — only mzstatic.com URLs are permitted.
        parsed = urlparse(body.artwork_url_template)
        if not (
            parsed.scheme in ("http", "https")
            and parsed.netloc
            and _MZSTATIC_HOST_RE.match(parsed.netloc)
        ):
            raise HTTPException(
                status_code=400,
                detail="artwork_url_template must be an https://….mzstatic.com URL",
            )

        tracks = index.tracks_for_album(body.album_artist, body.album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")

        def _is_track_locked(tid: int) -> bool:
            c = queue.current()
            la = queue.peek_next()
            return (c is not None and c.id == tid) or (la is not None and la.id == tid)

        if any(_is_track_locked(t.id) for t in tracks):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A track in this album is currently playing. "
                    "Art cannot be embedded while a file is open. Try again after playback moves on."
                ),
            )

        config: dict[str, Any] = _state.get("config") or {}
        min_dim: int = int(config.get("artwork.min_dimension", 500))
        max_b: int = int(config.get("artwork.max_bytes", 5_000_000))
        save_format: str = config.get("artwork.save_format", "embedded")

        # Resolve template to the user's configured minimum dimension so we always
        # request exactly the quality they require (Apple CDN resizes on demand).
        artwork_url = body.artwork_url_template.replace(
            "{size}", f"{min_dim}x{min_dim}bb"
        )

        try:
            image_bytes = await asyncio.get_event_loop().run_in_executor(
                None, fetch_itunes_image, artwork_url, min_dim, max_b
            )
        except ArtworkError as exc:
            msg = str(exc)
            status = 422 if "below minimum" in msg else 502
            raise HTTPException(status_code=status, detail=msg) from exc

        local_tracks = [t for t in tracks if not t.is_remote]
        if not local_tracks:
            raise HTTPException(
                status_code=400, detail="Cannot embed art in remote-only album"
            )

        if save_format == "cover-file":
            album_dir = local_tracks[0].file_path.parent
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, write_cover_file, image_bytes, "image/jpeg", album_dir
                )
                index.mark_album_art_embedded(
                    body.album_artist, body.album, [t.file_path for t in local_tracks]
                )
            except Exception:
                logger.exception("Failed to write cover file to %s", album_dir)
        else:
            successful_paths: list[Path] = []
            for track in local_tracks:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, _embed, track.file_path, image_bytes
                    )
                    successful_paths.append(track.file_path)
                except Exception:
                    logger.exception("Failed to embed art in %s", track.file_path)

            if successful_paths:
                index.mark_album_art_embedded(
                    body.album_artist, body.album, successful_paths
                )

        _notify_library_changed()

        albums = index.albums()
        for a in albums:
            if a.album_artist == body.album_artist and a.album == body.album:
                return AlbumOut(
                    album_artist=a.album_artist,
                    album=a.album,
                    release_date=a.release_date,
                    track_count=a.track_count,
                    has_art=a.has_art,
                    missing_album=a.missing_album,
                    track_id=a.missing_track_id,
                    art_version=a.art_version,
                    added_at=a.added_at,
                    last_played_at=a.last_played_at,
                    play_count_avg=a.play_count_avg,
                    favorite=a.favorite,
                    has_favorite_track=a.has_favorite_track,
                    source=a.source,
                    has_remote_tracks=a.has_remote_tracks,
                    sale_item_id=a.sale_item_id,
                    is_preorder=a.is_preorder,
                    num_streamable_tracks=a.num_streamable_tracks,
                    album_url=a.album_url,
                    display_album=a.display_album,
                    display_album_artist=a.display_album_artist,
                )
        raise HTTPException(status_code=404, detail="Album not found after apply")

    @app.post("/api/v1/albums/art/apply-local", response_model=AlbumOut)
    async def apply_album_art_local(
        album_artist: str = Form(...),
        album: str = Form(...),
        file: UploadFile = File(...),
    ) -> AlbumOut:
        """Embed a user-supplied image file into every track of the album.

        Accepts multipart/form-data with album_artist, album, and file fields.
        Returns 404 if the album is not in the library.
        Returns 409 if any track is currently playing.
        Returns 422 if the uploaded file is not a valid image.
        Returns the updated AlbumOut on success.
        """
        from kamp_daemon.artwork import (  # noqa: PLC0415
            ArtworkError,
            _compress_to_max_bytes,
            _embed,
            validate_image_bytes,
            write_cover_file,
        )

        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=422, detail="Uploaded file must be an image"
            )

        image_data = await file.read()

        try:
            validate_image_bytes(image_data)
        except ArtworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        tracks = index.tracks_for_album(album_artist, album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")

        def _is_track_locked(tid: int) -> bool:
            c = queue.current()
            la = queue.peek_next()
            return (c is not None and c.id == tid) or (la is not None and la.id == tid)

        if any(_is_track_locked(t.id) for t in tracks):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A track in this album is currently playing. "
                    "Art cannot be embedded while a file is open. Try again after playback moves on."
                ),
            )

        config: dict[str, Any] = _state.get("config") or {}
        max_b: int = int(config.get("artwork.max_bytes", 5_000_000))
        min_dim: int = int(config.get("artwork.min_dimension", 500))
        save_format: str = config.get("artwork.save_format", "embedded")

        cover_mime = content_type
        image_bytes = image_data
        if len(image_bytes) > max_b:
            import io as _io  # noqa: PLC0415

            from PIL import Image as _Image  # noqa: PLC0415

            img = _Image.open(_io.BytesIO(image_bytes))
            image_bytes = _compress_to_max_bytes(img, min_dim, max_b)
            cover_mime = "image/jpeg"  # _compress_to_max_bytes always outputs JPEG

        local_tracks = [t for t in tracks if not t.is_remote]
        if not local_tracks:
            raise HTTPException(
                status_code=400, detail="Cannot embed art in remote-only album"
            )

        if save_format == "cover-file":
            album_dir = local_tracks[0].file_path.parent
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, write_cover_file, image_bytes, cover_mime, album_dir
                )
                index.mark_album_art_embedded(
                    album_artist, album, [t.file_path for t in local_tracks]
                )
            except Exception:
                logger.exception("Failed to write cover file to %s", album_dir)
        else:
            successful_paths: list[Path] = []
            for track in local_tracks:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, _embed, track.file_path, image_bytes
                    )
                    successful_paths.append(track.file_path)
                except Exception:
                    logger.exception("Failed to embed art in %s", track.file_path)

            if successful_paths:
                index.mark_album_art_embedded(album_artist, album, successful_paths)

        _notify_library_changed()

        albums = index.albums()
        for a in albums:
            if a.album_artist == album_artist and a.album == album:
                return AlbumOut(
                    album_artist=a.album_artist,
                    album=a.album,
                    release_date=a.release_date,
                    track_count=a.track_count,
                    has_art=a.has_art,
                    missing_album=a.missing_album,
                    track_id=a.missing_track_id,
                    art_version=a.art_version,
                    added_at=a.added_at,
                    last_played_at=a.last_played_at,
                    play_count_avg=a.play_count_avg,
                    favorite=a.favorite,
                    has_favorite_track=a.has_favorite_track,
                    source=a.source,
                    has_remote_tracks=a.has_remote_tracks,
                    sale_item_id=a.sale_item_id,
                    is_preorder=a.is_preorder,
                    num_streamable_tracks=a.num_streamable_tracks,
                    album_url=a.album_url,
                    display_album=a.display_album,
                    display_album_artist=a.display_album_artist,
                )
        raise HTTPException(status_code=404, detail="Album not found after apply")

    @app.get("/api/v1/album-art")
    def get_album_art(
        album_artist: str, album: str, track_id: int | None = None, v: str = ""
    ) -> Response:
        from kamp_daemon.artwork import read_cover_file  # noqa: PLC0415

        config: dict[str, Any] = _state.get("config") or {}
        save_format: str = config.get("artwork.save_format", "embedded")

        # When a version stamp is present the URL encodes content identity,
        # so the response is safe to cache indefinitely.
        cache_control = "public, max-age=31536000, immutable" if v else "no-store"

        def _remote_art_response(fp: str, _cache_ctrl: str) -> Response:
            if art_cache_dir is None or get_bandcamp_session is None:
                raise HTTPException(status_code=404, detail="No art found")
            # Strip scheme; handle both 'bandcamp://...' and 'bandcamp:/...'
            # (Path() on POSIX normalises double-slash to single-slash).
            sale_item_id = (
                fp.split("bandcamp:", 1)[1]
                .lstrip("/\\")
                .replace("\\", "/")
                .split("/")[0]
            )
            item = index.get_collection_item(sale_item_id)
            if not item or not item.get("album_url"):
                raise HTTPException(status_code=404, detail="No art found")
            # Use tralbum_id as the cache key when available (stable content
            # identity); fall back to sale_item_id so multiple remote albums
            # never share a single ".jpg" file when tralbum_id is empty.
            cache_key: str = item.get("tralbum_id") or f"sid_{sale_item_id}"
            remote_cache_ctrl = "public, max-age=31536000, immutable"
            cache_path = art_cache_dir / f"{cache_key}.jpg"
            if cache_path.exists():
                return Response(
                    content=cache_path.read_bytes(),
                    media_type="image/jpeg",
                    headers={"Cache-Control": remote_cache_ctrl},
                )
            session_data = get_bandcamp_session()
            if not session_data:
                raise HTTPException(status_code=404, detail="No art found")
            from kamp_daemon.bandcamp import fetch_album_art_bytes  # noqa: PLC0415

            data = fetch_album_art_bytes(item["album_url"], session_data)
            if not data:
                raise HTTPException(status_code=404, detail="No art found")
            art_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
            return Response(
                content=data,
                media_type="image/jpeg",
                headers={"Cache-Control": remote_cache_ctrl},
            )

        # track_id addresses a single missing-album track (album tag empty), where
        # (album_artist, album) is not unique. Remote (bandcamp:) tracks resolved
        # this way fall through to the remote-proxy tail below via their derived uri.
        if track_id is not None:
            track = index.get_track_by_id(track_id)
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(album_artist, album)

        def _embedded_response() -> Response | None:
            for track in tracks:
                if track.is_remote:
                    continue  # remote tracks have no local file to extract art from
                if track.embedded_art:
                    result = extract_art(track.file_path)
                    if result:
                        data, mime = result
                        return Response(
                            content=data,
                            media_type=mime,
                            headers={"Cache-Control": cache_control},
                        )
            return None

        def _cover_file_response() -> Response | None:
            local_tracks = [t for t in tracks if not t.is_remote]
            if not local_tracks:
                return None
            result = read_cover_file(local_tracks[0].file_path.parent)
            if result:
                data, mime = result
                return Response(
                    content=data,
                    media_type=mime,
                    headers={"Cache-Control": cache_control},
                )
            return None

        if save_format == "cover-file":
            resp = _cover_file_response() or _embedded_response()
        else:
            resp = _embedded_response() or _cover_file_response()

        if resp is not None:
            return resp

        # Local art not found — if any resolved track is remote, try the proxy
        # cache, parsing the sale_item_id from that track's derived bandcamp: uri.
        # This covers both a remote missing-album card (resolved by track_id above)
        # and a normal remote album (resolved by album key).
        remote_tracks = [t for t in tracks if t.is_remote]
        if remote_tracks:
            return _remote_art_response(str(remote_tracks[0].file_path), cache_control)

        # Final fallback: if this album is (or was) in the Bandcamp collection,
        # serve cached CDN art. Covers two cases:
        #   1. Post-download, pre-art-embed: local tracks present but no embedded art yet.
        #   2. Permanently: local album from Bandcamp that was never art-embedded.
        bc_item = index.get_collection_item_by_album(album_artist, album)
        if bc_item:
            return _remote_art_response(
                f"bandcamp://{bc_item['sale_item_id']}/0", cache_control
            )

        raise HTTPException(status_code=404, detail="No art found")

    @app.get("/api/v1/search", response_model=SearchOut)
    def search_library(q: str = "", sort: str = "album_artist") -> SearchOut:
        fts_tracks = index.search(q)
        # Collect the set of (album_artist, album) keys that appear in FTS results,
        # then filter the pre-sorted album list so the response respects sort order.
        # Missing-album tracks have album="" in the DB, so also match them by
        # canonical id since their AlbumInfo.album is the display title, not "".
        # Keys are lower-cased so the match honours the NOCASE collation on
        # albums.album_artist/album (KAMP-545): an album row whose casing diverges
        # from its tracks' casing (e.g. row "SUNN O)))" vs tracks "Sunn O)))") would
        # otherwise be dropped from the album cards even though its tracks matched.
        fts_keys = {(t.album_artist.lower(), t.album.lower()) for t in fts_tracks}
        fts_ids = {t.id for t in fts_tracks if not t.album}
        albums = [
            AlbumOut(
                album_artist=a.album_artist,
                album=a.album,
                release_date=a.release_date,
                track_count=a.track_count,
                has_art=a.has_art,
                missing_album=a.missing_album,
                track_id=a.missing_track_id,
                art_version=a.art_version,
                added_at=a.added_at,
                last_played_at=a.last_played_at,
                play_count_avg=a.play_count_avg,
                favorite=a.favorite,
                has_favorite_track=a.has_favorite_track,
                source=a.source,
                has_remote_tracks=a.has_remote_tracks,
                sale_item_id=a.sale_item_id,
                is_preorder=a.is_preorder,
                num_streamable_tracks=a.num_streamable_tracks,
                album_url=a.album_url,
                display_album=a.display_album,
                display_album_artist=a.display_album_artist,
            )
            for a in index.albums(sort=sort)
            if (a.album_artist.lower(), a.album.lower()) in fts_keys
            or (a.missing_album and a.missing_track_id in fts_ids)
        ]
        albums.sort(key=lambda a: not a.favorite)

        # Merge playlist-name matches with playlists containing matched tracks,
        # deduplicating by id so a playlist that matches both facets appears once.
        name_playlists = index.search_playlists(q)
        track_playlists = index.playlists_for_tracks([t.id for t in fts_tracks])
        seen_ids: set[int] = set()
        playlists: list[PlaylistSearchOut] = []
        for raw in name_playlists + track_playlists:
            if raw["id"] not in seen_ids:
                seen_ids.add(raw["id"])
                playlists.append(PlaylistSearchOut(**raw))

        return SearchOut(
            albums=albums,
            tracks=_tracks_out(index, fts_tracks),
            playlists=playlists,
        )

    @app.post("/api/v1/library/scan", response_model=ScanResult)
    def scan_library() -> ScanResult:
        if _state["library_path"] is None:
            raise HTTPException(status_code=503, detail="Library path not configured")

        # Running artist frequency map and unique album set, accumulated across all on_progress calls.
        artist_counts: dict[str, int] = {}
        albums_seen: set[tuple[str, str]] = set()

        def _on_progress(current: int, total: int, track: Track | None) -> None:
            current_file: str | None = None
            current_artist: str | None = None
            if track is not None:
                current_file = track.title.strip() or track.file_path.stem
                if track.artist.strip():
                    current_artist = track.artist.strip()
                    artist_counts[current_artist] = (
                        artist_counts.get(current_artist, 0) + 1
                    )
                if track.album.strip():
                    albums_seen.add((track.album_artist, track.album))
            top_artist = (
                max(artist_counts, key=lambda a: artist_counts[a])
                if artist_counts
                else None
            )
            _state["scan_progress"] = {
                "active": True,
                "current": current,
                "total": total,
                "current_file": current_file,
                "current_artist": current_artist,
                "top_artist": top_artist,
                "num_albums": len(albums_seen),
                "num_artists": len(artist_counts),
            }

        _state["scan_progress"] = {
            "active": True,
            "current": 0,
            "total": 0,
            "current_file": None,
            "current_artist": None,
            "top_artist": None,
            "num_albums": 0,
            "num_artists": 0,
        }
        try:
            result = LibraryScanner(index).scan(
                _state["library_path"], on_progress=_on_progress
            )
        finally:
            _state["scan_progress"] = {
                "active": False,
                "current": 0,
                "total": 0,
                "current_file": None,
                "current_artist": None,
                "top_artist": None,
                "num_albums": 0,
                "num_artists": 0,
            }

        return ScanResult(
            added=result.added,
            removed=result.removed,
            unchanged=result.unchanged,
            updated=result.updated,
        )

    @app.get("/api/v1/library/scan/progress")
    def get_scan_progress() -> dict[str, Any]:
        return cast(dict[str, Any], _state["scan_progress"])

    @app.post("/api/v1/config/library-path")
    def set_library_path(req: LibraryPathRequest) -> dict[str, Any]:
        raw = req.path
        # Path.is_absolute is platform-aware: matches "/..." on POSIX and
        # "C:\\..." on Windows. Allow a leading ~ as a special case since it
        # becomes absolute only after expanduser() below.
        if not raw.startswith("~") and not Path(raw).is_absolute():
            raise HTTPException(status_code=422, detail="Path must be absolute")
        # nosec: py/path-injection — absolute-path requirement above rejects traversal;
        # deny-list below blocks system roots and their subtrees. Restricting to Path.home()
        # would break legitimate use cases (external drives, network mounts).
        candidate = Path(raw).expanduser().resolve()  # noqa: S603
        if candidate in _FORBIDDEN_LIBRARY_ROOTS:
            raise HTTPException(
                status_code=422, detail="Path is not allowed as a library root"
            )
        # Bare drive root on Windows (C:\, D:\, ...). Can't enumerate every drive
        # letter, so reject by structure: a resolved absolute path with a single
        # part is an anchor with no further component.
        if sys.platform == "win32" and len(candidate.parts) == 1:
            raise HTTPException(
                status_code=422, detail="Path is not allowed as a library root"
            )
        if not candidate.exists():
            raise HTTPException(status_code=422, detail="Path does not exist")
        if not candidate.is_dir():
            raise HTTPException(status_code=422, detail="Path is not a directory")
        _state["library_path"] = candidate
        if on_library_path_set is not None:
            on_library_path_set(candidate)
        return {"ok": True}

    # -----------------------------------------------------------------------
    # UI state
    # -----------------------------------------------------------------------

    @app.get("/api/v1/ui")
    def get_ui_state() -> dict[str, Any]:
        return {
            "active_view": _state["ui_active_view"],
            "sort_order": _state["ui_sort_order"],
            "sort_dir": _state["ui_sort_dir"],
            "queue_panel_open": bool(_state["ui_queue_panel_open"]),
        }

    @app.post("/api/v1/ui/active-view")
    def set_active_view(req: dict[str, Any]) -> dict[str, Any]:
        view = req.get("view", "library")
        if view not in ("library", "now-playing", "home"):
            raise HTTPException(status_code=422, detail="Invalid view")
        _state["ui_active_view"] = view
        if on_ui_state_set is not None:
            on_ui_state_set("ui.active_view", view)
        return {"ok": True}

    _VALID_SORT_ORDERS = frozenset(
        {"album_artist", "album", "date_added", "last_played", "release_date"}
    )

    @app.post("/api/v1/ui/sort-order")
    def set_sort_order(req: dict[str, Any]) -> dict[str, Any]:
        sort = req.get("sort_order", "album_artist")
        if sort not in _VALID_SORT_ORDERS:
            raise HTTPException(status_code=422, detail="Invalid sort order")
        _state["ui_sort_order"] = sort
        if on_ui_state_set is not None:
            on_ui_state_set("ui.sort_order", sort)
        sort_dir = req.get("sort_dir", "")
        if sort_dir in ("asc", "desc"):
            _state["ui_sort_dir"] = sort_dir
            if on_ui_state_set is not None:
                on_ui_state_set("ui.sort_dir", sort_dir)
        return {"ok": True}

    @app.post("/api/v1/ui/queue-panel")
    def set_queue_panel(req: dict[str, Any]) -> dict[str, Any]:
        open_ = req.get("open", False)
        value = 1 if open_ else 0
        _state["ui_queue_panel_open"] = value
        if on_ui_state_set is not None:
            on_ui_state_set("ui.queue_panel_open", str(value))
        return {"ok": True}

    # -----------------------------------------------------------------------
    # Config (preferences)
    # -----------------------------------------------------------------------

    @app.get("/api/v1/config")
    def get_config() -> dict[str, Any]:
        return cast(dict[str, Any], _state["config"])

    # Integer config keys — values are coerced to int when stored so that
    # GET /api/v1/config returns the correct JSON type (number, not string).
    _INT_CONFIG_KEYS = frozenset(
        {"artwork.min_dimension", "artwork.max_bytes", "bandcamp.poll_interval_minutes"}
    )
    # Boolean config keys — stored as Python bool so JSON serialises as true/false.
    _BOOL_CONFIG_KEYS = frozenset({"musicbrainz.trust-musicbrainz-when-tags-conflict"})

    @app.patch("/api/v1/config")
    def patch_config(req: ConfigPatchRequest) -> dict[str, Any]:
        if on_config_set is not None:
            try:
                on_config_set(req.key, req.value)
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Coerce to the correct Python type before caching in memory.
        if req.key in _INT_CONFIG_KEYS:
            coerced: Any = int(req.value)
        elif req.key in _BOOL_CONFIG_KEYS:
            coerced = req.value.lower() == "true"
        else:
            coerced = req.value
        _state["config"][req.key] = coerced
        return {"ok": True}

    # -----------------------------------------------------------------------
    # Last.fm connect / disconnect
    # -----------------------------------------------------------------------

    @app.post("/api/v1/lastfm/connect")
    def post_lastfm_connect(req: LastfmConnectRequest) -> dict[str, Any]:
        if on_lastfm_connect is None:
            raise HTTPException(status_code=503, detail="Last.fm not configured")
        try:
            on_lastfm_connect(req.username, req.password)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _state["config"]["lastfm.username"] = req.username
        return {"ok": True, "username": req.username}

    @app.delete("/api/v1/lastfm/connect")
    def delete_lastfm_connect() -> dict[str, Any]:
        if on_lastfm_disconnect is None:
            raise HTTPException(status_code=503, detail="Last.fm not configured")
        on_lastfm_disconnect()
        _state["config"]["lastfm.username"] = None
        return {"ok": True}

    # -----------------------------------------------------------------------
    # Bandcamp login
    # -----------------------------------------------------------------------

    @app.post("/api/v1/bandcamp/begin-login")
    def bandcamp_begin_login() -> dict[str, Any]:
        """Signal the Electron renderer to open the Bandcamp login BrowserWindow.

        Broadcasts a ``bandcamp.needs-login`` WebSocket push so the Electron
        renderer (which is subscribed to the push stream) can invoke the
        ``bandcamp:begin-login`` IPC handler in the Electron main process.
        Called by the macOS menu bar Login item and (eventually) the renderer's
        "Connect" button directly via the IPC path without hitting this endpoint.
        """
        _broadcast({"type": "bandcamp.needs-login"})
        return {"ok": True}

    @app.post("/api/v1/bandcamp/login-complete")
    def bandcamp_login_complete(req: BandcampCookiePayload) -> dict[str, Any]:
        """Receive cookies collected by the Electron BrowserWindow and persist them.

        Called by the Electron main process after the user successfully logs in.
        The callback also attempts to fetch the Bandcamp username and store it
        in the session; if successful, _state["config"]["bandcamp.username"] is
        updated so GET /config immediately reflects the connected account.
        """
        if on_bandcamp_login_complete is None:
            raise HTTPException(status_code=503, detail="Bandcamp login not configured")
        try:
            on_bandcamp_login_complete({"cookies": req.cookies, "origins": req.origins})
        except Exception as exc:
            # Redacted payload summary — names only, never cookie values, so we
            # can diagnose Windows-vs-macOS shape divergence without leaking the
            # session.  See KAMP-282.
            cookie_names = [str(c.get("name", "<noname>")) for c in req.cookies]
            logger.exception(
                "bandcamp login-complete callback failed: cookie_names=%s origins_count=%d",
                cookie_names,
                len(req.origins),
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Read username back from session (populated by the callback when
        # the API call succeeds) and surface it in the config state.
        if get_bandcamp_session is not None:
            session = get_bandcamp_session()
            if session:
                _state["config"]["bandcamp.connected"] = True
                _state["config"]["bandcamp.username"] = session.get("username")
                _state["config"]["bandcamp.ever_connected"] = True
        _broadcast({"type": "bandcamp.login-complete"})
        return {"ok": True}

    @app.get("/api/v1/bandcamp/status")
    def get_bandcamp_status() -> dict[str, Any]:
        """Return the current Bandcamp session status.

        ``connected`` is True when a session exists in the DB.
        ``username`` is the Bandcamp username extracted after login, or None.
        """
        if get_bandcamp_session is None:
            return {"connected": False, "username": None}
        session = get_bandcamp_session()
        if session is None:
            return {"connected": False, "username": None}
        return {"connected": True, "username": session.get("username")}

    @app.get("/api/v1/bandcamp/session-cookies")
    def get_bandcamp_session_cookies() -> dict[str, Any]:
        """Return the raw cookie list from the stored Bandcamp session.

        Used by the Electron main process to reload cookies into
        ``session.defaultSession`` before each proxy-fetch request so that
        ``net.fetch`` carries valid credentials in the PyInstaller bundle.
        Returns an empty list when no session is stored.
        """
        if get_bandcamp_session is None:
            return {"cookies": []}
        session_data = get_bandcamp_session()
        if session_data is None:
            return {"cookies": []}
        return {"cookies": session_data.get("cookies", [])}

    @app.delete("/api/v1/bandcamp/connect")
    def delete_bandcamp_connect() -> dict[str, Any]:
        """Disconnect the Bandcamp session (clear session from DB)."""
        if on_bandcamp_disconnect is None:
            raise HTTPException(
                status_code=503, detail="Bandcamp disconnect not configured"
            )
        on_bandcamp_disconnect()
        _state["config"]["bandcamp.connected"] = False
        _state["config"]["bandcamp.username"] = None
        _broadcast({"type": "bandcamp.disconnected"})
        return {"ok": True}

    @app.post("/api/v1/bandcamp/sync")
    def trigger_bandcamp_sync() -> dict[str, Any]:
        """Trigger a manual Bandcamp sync in the background.

        Returns immediately; the sync runs in a daemon thread.  Sync progress
        is pushed to clients via ``bandcamp.sync-status`` WebSocket events.
        """
        import threading

        if on_bandcamp_sync_trigger is None:
            raise HTTPException(status_code=503, detail="Bandcamp sync not configured")
        threading.Thread(
            target=on_bandcamp_sync_trigger, daemon=True, name="manual-sync"
        ).start()
        return {"ok": True}

    @app.post("/api/v1/bandcamp/sync-all")
    def trigger_bandcamp_sync_all() -> dict[str, Any]:
        """Re-download the entire Bandcamp collection from scratch.

        Clears the local sync-state file then downloads all purchases.  Returns
        immediately; progress arrives via ``bandcamp.sync-status`` WebSocket events.
        """
        import threading

        if on_bandcamp_sync_all_trigger is None:
            raise HTTPException(
                status_code=503, detail="Bandcamp sync-all not configured"
            )
        threading.Thread(
            target=on_bandcamp_sync_all_trigger, daemon=True, name="sync-all"
        ).start()
        return {"ok": True}

    @app.post("/api/v1/bandcamp/collection/{sale_item_id}/download")
    def download_collection_item(sale_item_id: str) -> dict[str, Any]:
        """Enqueue a Bandcamp album for serialized download.

        Adds the item to the persistent download_queue table and the in-memory
        dl_queue so the single-consumer worker thread in __main__ processes it.
        Broadcasts 'queued' immediately; the worker broadcasts 'downloading' when
        it begins and 'done'/'error' on completion.

        Returns 404 if the item is not in the collection.
        Returns 503 if the download queue is not configured.
        """
        item = index.get_collection_item(sale_item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Collection item not found")
        if dl_queue is None:
            raise HTTPException(status_code=503, detail="Album download not configured")

        # Mark the collection item as targeted for local download so in_bandcamp_collection
        # becomes true immediately (used by the art endpoint fallback and album display).
        # set_track_source_for_item is NOT called here — changing source prematurely breaks
        # the has_art=true shortcut for purely-remote albums and causes art to disappear.
        index.set_collection_item_mode(sale_item_id, "local")

        # Persist first so a restart can replay the queue even if the process
        # dies before the worker picks up the in-memory item. The album snapshot
        # lets the Downloads-view card render without a join (KAMP-564).
        index.enqueue_download(
            sale_item_id,
            album_name=item.get("item_title") or None,
            album_artist=item.get("band_name") or None,
        )
        dl_queue.put(sale_item_id)  # wake the worker

        _broadcast(
            {
                "type": "bandcamp.album-download",
                "sale_item_id": sale_item_id,
                "state": "queued",
            }
        )
        _notify_download_queue()  # structured snapshot for the Downloads view
        return {"ok": True}

    # ------------------------------------------------------------------
    # Download-queue management (KAMP-567) — provider-neutral REST surface
    # over the KAMP-564 state machine. Every mutation broadcasts the KAMP-566
    # download.queue snapshot so connected clients converge.
    # ------------------------------------------------------------------

    @app.get("/api/v1/downloads")
    def list_downloads() -> dict[str, Any]:
        """Return the full download queue in display order (KAMP-567).

        Items are ordered downloading → queued (by position) → failed, each
        carrying the card fields (status, position, size, error_text, album
        snapshot). Same shape as the ``download.queue`` WebSocket snapshot.
        """
        return {"items": index.download_queue_items()}

    @app.post("/api/v1/downloads/reorder")
    def reorder_downloads(req: ReorderDownloadsRequest) -> dict[str, Any]:
        """Reorder the queued items (KAMP-567).

        The body must list exactly the currently-'queued' provider_item_ids in the
        desired order; the downloading item is fixed at the top and excluded.
        Returns 400 if the list is not that exact set (e.g. a stale UI reorder).
        """
        try:
            index.reorder_download_queue(req.provider_item_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _notify_download_queue()
        return {"ok": True}

    @app.post("/api/v1/downloads/{provider_item_id}/retry")
    def retry_download_item(provider_item_id: str) -> dict[str, Any]:
        """Retry a failed download: re-queue it at the END of the queue (KAMP-567).

        Returns 503 if the download queue is not configured.
        """
        if dl_queue is None:
            raise HTTPException(status_code=503, detail="Album download not configured")

        index.retry_download(provider_item_id)
        dl_queue.put(provider_item_id)  # wake the worker

        # Per-item event keeps the library-grid card in sync (queued decoration).
        _broadcast(
            {
                "type": "bandcamp.album-download",
                "sale_item_id": provider_item_id,
                "state": "queued",
            }
        )
        _notify_download_queue()
        return {"ok": True}

    @app.delete("/api/v1/downloads/{provider_item_id}")
    def cancel_download_item(provider_item_id: str) -> dict[str, Any]:
        """Cancel a queued or failed item — remove it from the queue (KAMP-567).

        Distinct from ``DELETE /api/v1/bandcamp/collection/{id}/download``, which
        reverts a completed download back to streaming.
        """
        index.cancel_download(provider_item_id)
        # 'removed' clears the library-grid card's queued/failed decoration.
        _broadcast(
            {
                "type": "bandcamp.album-download",
                "sale_item_id": provider_item_id,
                "state": "removed",
            }
        )
        _notify_download_queue()
        return {"ok": True}

    @app.delete("/api/v1/bandcamp/collection/{sale_item_id}/download")
    def remove_downloaded_item(sale_item_id: str) -> dict[str, Any]:
        """Revert a downloaded Bandcamp album back to streaming state.

        Deletes the local files, removes local track rows from the DB (streaming
        rows are preserved and become the primary tracks again), and sets
        bandcamp_collection.mode back to 'remote'.

        Returns 404 if the item is not in the collection.
        Returns 409 if a track from this album is currently playing.
        Returns 422 if no streamable version can be produced (logged out,
        network/parse failure, or no streamable tracks) — in which case nothing
        is deleted.
        """
        item = index.get_collection_item(sale_item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Collection item not found")

        local_tracks = index.local_tracks_for_sale_item_id(sale_item_id)

        def _is_track_locked(tid: int) -> bool:
            c = queue.current()
            la = queue.peek_next()
            return (c is not None and c.id == tid) or (la is not None and la.id == tid)

        locked = [t for t in local_tracks if _is_track_locked(t.id)]
        if locked and engine.state.playing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A track in this album is currently playing. "
                    "Stop playback before removing the download."
                ),
            )

        # KAMP-527: download-mode albums have no bandcamp:// stream rows to fall
        # back to. Materialize them on demand (a network call) BEFORE the queue
        # swap and delete — this is why the daemon-side endpoint owns it rather
        # than the pure LibraryIndex method. If we cannot (logged out, fetch
        # fails, or Bandcamp has no streamable version), abort with 422 and
        # delete nothing; remove_download's own per-track guard is the final
        # safety net that still refuses to strand any local track.
        # KAMP-541: a downloaded track keeps its stream as a track_sources row, so
        # "has a stream to fall back to" is a source check. When some track lacks
        # one (download-mode album never streamed), materialize the stream sources
        # on demand (a network call) before removing; if that fails, abort with 422.
        if not index.all_downloads_streamable(sale_item_id):
            _materialize_stream_tracks_or_422(index, item, get_bandcamp_session)

        # No queue swap needed (KAMP-541 + queue-by-id): the canonical track
        # survives the removal — its file source is dropped and it reverts to its
        # stream source with the SAME track id — so the id-keyed queue entries
        # stay valid across a restart.

        if locked:
            # Current track's file is loaded in mpv — unload it so the handle
            # is released before we delete the file from disk.
            engine.unload()

        try:
            file_paths = index.remove_download(sale_item_id)
        except NoStreamableVersionError as exc:
            # The per-track guard refused: at least one local track has no stream
            # counterpart (e.g. a multi-disc download). Nothing was deleted.
            raise HTTPException(
                status_code=422,
                detail=(
                    "No streamable version available for this album. Removing the "
                    "download would remove it from your library, so it was kept."
                ),
            ) from exc

        for fp in file_paths:
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass

        lib_path: Path | None = _state["library_path"]
        # Album-level dirs (directly contained the track files): scrub OS
        # metadata and cover-art images before attempting rmdir.
        album_dirs: set[Path] = {fp.parent for fp in file_paths}
        for d in album_dirs:
            if lib_path is not None and d == lib_path:
                continue
            _scrub_os_metadata(d)
            _scrub_cover_art(d)
            try:
                d.rmdir()
            except OSError:
                pass
        # Parent (artist-level) dirs: only scrub OS metadata — cover art
        # doesn't live here, and rmdir fails safely if other albums remain.
        parent_dirs: set[Path] = {
            fp.parent.parent for fp in file_paths if fp.parent.parent != fp.parent
        }
        for d in parent_dirs:
            if lib_path is not None and d == lib_path:
                continue
            _scrub_os_metadata(d)
            try:
                d.rmdir()
            except OSError:
                pass

        _broadcast(
            {
                "type": "bandcamp.album-download",
                "sale_item_id": sale_item_id,
                "state": "removed",
            }
        )
        return {"ok": True}

    # -----------------------------------------------------------------------
    # Player
    # -----------------------------------------------------------------------

    @app.get("/api/v1/player/state", response_model=PlayerStateOut)
    def get_player_state() -> PlayerStateOut:
        return _state_snapshot()

    @app.get("/api/v1/player/queue", response_model=QueueOut)
    def get_queue() -> QueueOut:
        tracks, pos = queue.queue_tracks()
        return QueueOut(
            tracks=_tracks_out(index, tracks),
            position=pos,
            shuffle=queue.shuffle,
            repeat=queue.repeat,
        )

    def _drain_unlocked(old_current: Any, old_lookahead: Any) -> None:
        """Fire async drains for tracks that are no longer locked after a skip."""
        drain = getattr(app.state, "drain_for_track_async", None)
        if drain is None:
            return
        new_current = queue.current()
        new_lookahead = queue.peek_next()
        new_ids = {t.id for t in (new_current, new_lookahead) if t is not None}
        for t in (old_current, old_lookahead):
            if t is not None and t.id not in new_ids:
                drain(t.id)

    @app.post("/api/v1/player/play")
    def play(req: PlayRequest) -> dict[str, Any]:
        old_current = queue.current()
        old_lookahead = queue.peek_next()
        if req.id is not None:  # KAMP-554: missing-album track addressed by id
            track = index.get_track_by_id(req.id)
            tracks = [track] if track else []
        else:
            all_tracks = index.tracks_for_album(req.album_artist, req.album)
            # Map start_index from the full list to the available subset before filtering.
            requested = (
                all_tracks[req.track_index]
                if req.track_index < len(all_tracks)
                else None
            )
            tracks = [t for t in all_tracks if t.is_available]
            if requested and requested.is_available:
                req = PlayRequest(
                    album_artist=req.album_artist,
                    album=req.album,
                    track_index=next(
                        (i for i, t in enumerate(tracks) if t.id == requested.id),
                        0,
                    ),
                )
            else:
                req = PlayRequest(
                    album_artist=req.album_artist,
                    album=req.album,
                    track_index=0,
                )
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.load(tracks, start_index=req.track_index)
        current = queue.current()
        if current:
            engine.play(_resolve_playback(current))
            _record_track_started_immediate(current.file_path)
        _notify_track_changed()
        _drain_unlocked(old_current, old_lookahead)
        return {"ok": True}

    @app.post("/api/v1/player/play-playlist")
    def play_playlist(req: PlayPlaylistRequest) -> dict[str, Any]:
        if index.get_playlist(req.playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        tracks = index.tracks_for_playlist(req.playlist_id)
        if not tracks:
            return {"ok": True}
        old_current = queue.current()
        old_lookahead = queue.peek_next()
        start = max(0, min(req.start_index, len(tracks) - 1))
        queue.load(tracks, start_index=start)
        index.record_playlist_played(req.playlist_id)
        current = queue.current()
        if current:
            engine.play(_resolve_playback(current))
            _record_track_started_immediate(current.file_path)
        _notify_track_changed()
        _drain_unlocked(old_current, old_lookahead)
        return {"ok": True}

    @app.post("/api/v1/player/play-files")
    def play_files(req: PlayFilesRequest) -> dict[str, Any]:
        """Replace the queue with an explicit ordered list of track ids.

        Used when the client holds an ordered list (e.g. a sorted playlist
        view) that differs from the stored playlist order. KAMP-552: id-native.
        """
        tracks = [t for i in req.ids if (t := index.get_track_by_id(i)) is not None]
        if not tracks:
            return {"ok": True}
        old_current = queue.current()
        old_lookahead = queue.peek_next()
        start = max(0, min(req.start_index, len(tracks) - 1))
        queue.load(tracks, start_index=start)
        current = queue.current()
        if current:
            engine.play(_resolve_playback(current))
            _record_track_started_immediate(current.file_path)
        _notify_track_changed()
        _drain_unlocked(old_current, old_lookahead)
        return {"ok": True}

    @app.post("/api/v1/player/pause")
    def pause() -> dict[str, Any]:
        engine.pause()
        return {"ok": True}

    @app.post("/api/v1/player/resume")
    def resume() -> dict[str, Any]:
        engine.resume()
        return {"ok": True}

    @app.post("/api/v1/player/stop")
    def stop() -> dict[str, Any]:
        _state["buffering"] = False
        engine.stop()
        _notify_track_changed()
        return {"ok": True}

    @app.post("/api/v1/player/seek")
    def seek(req: SeekRequest) -> dict[str, Any]:
        engine.seek(req.position)
        return {"ok": True}

    @app.post("/api/v1/player/volume")
    def set_volume(req: VolumeRequest) -> dict[str, Any]:
        engine.volume = req.volume
        return {"ok": True}

    @app.post("/api/v1/player/next")
    def next_track() -> dict[str, Any]:
        old_current = queue.current()
        old_lookahead = queue.peek_next()
        track = queue.next()
        if track:
            engine.play(_resolve_playback(track))
            _record_track_started_debounced(track.file_path)
        else:
            engine.stop()
        _notify_track_changed()
        _drain_unlocked(old_current, old_lookahead)
        return {"ok": True}

    @app.post("/api/v1/player/prev")
    def prev_track() -> dict[str, Any]:
        old_current = queue.current()
        old_lookahead = queue.peek_next()
        track = queue.prev()
        if track:
            engine.play(_resolve_playback(track))
            _record_track_started_debounced(track.file_path)
        _notify_track_changed()
        _drain_unlocked(old_current, old_lookahead)
        return {"ok": True}

    @app.post("/api/v1/player/queue/clear")
    def queue_clear() -> dict[str, Any]:
        queue.clear()
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/clear-remaining")
    def queue_clear_remaining(req: SkipToRequest) -> dict[str, Any]:
        queue.clear_remaining(req.position)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/skip-to")
    def skip_to_position(req: SkipToRequest) -> dict[str, Any]:
        old_current = queue.current()
        old_lookahead = queue.peek_next()
        track = queue.skip_to(req.position)
        if track:
            engine.play(
                _resolve_playback(track)
            )  # resets lookahead; file-loaded re-primes it
            _record_track_started_debounced(track.file_path)
        _notify_track_changed()
        _drain_unlocked(old_current, old_lookahead)
        return {"ok": True}

    @app.post("/api/v1/player/queue/add")
    def queue_add(req: AddToQueueRequest) -> dict[str, Any]:
        track = index.get_track_by_id(req.id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        was_stopped = queue.current() is None
        queue.add_to_queue(track)
        current = queue.current()
        if was_stopped and current is not None:
            engine.play(_resolve_playback(current))
            _record_track_started_immediate(current.file_path)
            _notify_track_changed()
        else:
            engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/play-next")
    def queue_play_next(req: AddToQueueRequest) -> dict[str, Any]:
        track = index.get_track_by_id(req.id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        was_stopped = queue.current() is None
        queue.play_next(track)
        current = queue.current()
        if was_stopped and current is not None:
            engine.play(_resolve_playback(current))
            _record_track_started_immediate(current.file_path)
            _notify_track_changed()
        else:
            engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/insert")
    def queue_insert(req: InsertQueueRequest) -> dict[str, Any]:
        track = index.get_track_by_id(req.id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        queue.insert_at(track, req.index)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/add-album")
    def queue_add_album(req: AlbumQueueRequest) -> dict[str, Any]:
        if req.id is not None:  # KAMP-554: missing-album track addressed by id
            track = index.get_track_by_id(req.id)
            tracks = [track] if track else []
        else:
            tracks = [
                t
                for t in index.tracks_for_album(req.album_artist, req.album)
                if t.is_available
            ]
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        was_stopped = queue.current() is None
        queue.add_album_to_queue(tracks)
        current = queue.current()
        if was_stopped and current is not None:
            engine.play(_resolve_playback(current))
            _record_track_started_immediate(current.file_path)
            _notify_track_changed()
        else:
            engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/play-album-next")
    def queue_play_album_next(req: AlbumQueueRequest) -> dict[str, Any]:
        if req.id is not None:  # KAMP-554: missing-album track addressed by id
            track = index.get_track_by_id(req.id)
            tracks = [track] if track else []
        else:
            tracks = [
                t
                for t in index.tracks_for_album(req.album_artist, req.album)
                if t.is_available
            ]
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        was_stopped = queue.current() is None
        queue.play_album_next(tracks)
        current = queue.current()
        if was_stopped and current is not None:
            engine.play(_resolve_playback(current))
            _record_track_started_immediate(current.file_path)
            _notify_track_changed()
        else:
            engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/insert-album")
    def queue_insert_album(req: InsertAlbumQueueRequest) -> dict[str, Any]:
        if req.id is not None:  # KAMP-554: missing-album track addressed by id
            track = index.get_track_by_id(req.id)
            tracks = [track] if track else []
        else:
            tracks = [
                t
                for t in index.tracks_for_album(req.album_artist, req.album)
                if t.is_available
            ]
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.insert_album_at(tracks, req.index)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/move")
    def queue_move(req: MoveQueueRequest) -> dict[str, Any]:
        try:
            queue.move(req.from_index, req.to_index)
        except IndexError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/reorder")
    def queue_reorder(req: ReorderQueueRequest) -> dict[str, Any]:
        try:
            queue.reorder(req.order)
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/remove")
    def queue_remove(req: RemoveFromQueueRequest) -> dict[str, Any]:
        queue.remove_at(req.indices)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/shuffle")
    def set_shuffle(req: ShuffleRequest) -> dict[str, Any]:
        queue.set_shuffle(req.shuffle, album_mode=req.album_shuffle)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/repeat")
    def set_repeat(req: RepeatRequest) -> dict[str, Any]:
        queue.set_repeat_mode(req.mode)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    # -----------------------------------------------------------------------
    # Bandcamp HTTP proxy (bypasses PyInstaller OpenSSL TLS fingerprinting)
    # -----------------------------------------------------------------------
    # The Python daemon subprocess cannot reach bandcamp.com directly in the
    # built .app because PyInstaller's OpenSSL has a different TLS fingerprint
    # (JA3/JA4) that Cloudflare flags.  These two endpoints implement a
    # request/response relay via Electron's net module (Chromium network stack),
    # which has a real browser fingerprint and holds the cf_clearance cookie.
    #
    # Flow:
    #   1. daemon → POST /proxy-fetch (registers request, broadcasts over WS, blocks)
    #   2. preload WS handler receives "bandcamp.proxy-fetch" → ipcRenderer.invoke
    #   3. Electron main ipcMain.handle executes net.fetch with session.defaultSession
    #   4. Electron main POSTs result to /fetch-result
    #   5. /proxy-fetch unblocks and returns the result to the daemon

    @app.post("/api/v1/bandcamp/proxy-fetch")
    async def bandcamp_proxy_fetch(req: BandcampProxyFetchRequest) -> dict[str, Any]:
        _validate_proxy_url(req.url)
        nonlocal _event_loop
        # Capture the running loop here so _broadcast (which uses
        # call_soon_threadsafe) works even before any WS client has connected.
        if _event_loop is None:
            _event_loop = asyncio.get_running_loop()

        req_id = str(_uuid.uuid4())
        # threading.Event (not asyncio.Event) so fetch-result can unblock
        # proxy-fetch regardless of which event loop each request runs in.
        # run_in_executor keeps the server's event loop free while waiting.
        event: _threading.Event = _threading.Event()
        _state["bandcamp_proxy_requests"][req_id] = {
            "id": req_id,
            "url": req.url,
            "method": req.method,
            "headers": req.headers,
            "body": req.body,
            "event": event,
            "result": None,
        }
        # Build the push event.  Save it in _pending_proxy_fetches *before*
        # broadcasting so that a WS client connecting after _broadcast() (but
        # before the request is answered) still receives it on connect.  The
        # entry is removed when /fetch-result arrives.
        # Cookies are omitted — Electron fetches /api/v1/bandcamp/session-cookies
        # directly so they are never broadcast to all WS clients.
        proxy_event: dict[str, Any] = {
            "type": "bandcamp.proxy-fetch",
            "id": req_id,
            "url": req.url,
            "method": req.method,
            "headers": req.headers,
            "body": req.body,
        }
        _pending_proxy_fetches[req_id] = proxy_event
        # Notify the Electron preload via the existing WebSocket push channel.
        # The preload forwards to ipcMain which executes net.fetch and posts
        # the result back to /fetch-result.
        _broadcast(proxy_event)
        loop = asyncio.get_running_loop()
        # Allow up to 60s for Electron to complete net.fetch and post the
        # result.  Real Bandcamp API calls can take 20–30s; the subprocess
        # proxy_timeout is now 2×inner + 10s, so 60s covers the worst case.
        signalled = await loop.run_in_executor(None, event.wait, 60.0)
        if not signalled:
            _state["bandcamp_proxy_requests"].pop(req_id, None)
            # Also remove from pending so the event is not replayed to the next
            # WS client.  Without this, a timed-out request (e.g. because Electron
            # crashed) persists in _pending_proxy_fetches forever, causing a crash
            # loop: every new Electron launch replays the stale event and crashes again.
            _pending_proxy_fetches.pop(req_id, None)
            raise HTTPException(
                status_code=504,
                detail="Proxy fetch timed out — Electron did not respond",
            )
        entry = _state["bandcamp_proxy_requests"].pop(req_id, None)
        if entry is None or entry["result"] is None:
            raise HTTPException(
                status_code=502, detail="Proxy fetch returned no result"
            )
        return cast(dict[str, Any], entry["result"])

    @app.post("/api/v1/bandcamp/fetch-result")
    async def bandcamp_fetch_result(req: BandcampProxyFetchResult) -> dict[str, Any]:
        """Receive the net.fetch result from Electron and unblock the waiting proxy-fetch."""
        entry = _state["bandcamp_proxy_requests"].get(req.id)
        if entry is None:
            raise HTTPException(status_code=404, detail="No pending fetch with that ID")
        entry["result"] = {
            "status": req.status,
            "body": req.body,
            "content_type": req.content_type,
            "url": req.url,
        }
        # Remove from pending — this request has been answered.
        _pending_proxy_fetches.pop(req.id, None)
        entry["event"].set()
        return {"ok": True}

    # -----------------------------------------------------------------------
    # Playlists (KAMP-441, KAMP-461)
    # -----------------------------------------------------------------------

    def _enrich_playlist(pl: dict[str, Any]) -> dict[str, Any]:
        """Add 'criteria' key to a playlist dict (None for static playlists)."""
        mc = index.get_magic_playlist_criteria(pl["id"])
        pl["criteria"] = mc.to_dict() if mc is not None else None
        return pl

    @app.post("/api/v1/playlists", response_model=PlaylistOut, status_code=201)
    def create_playlist(req: CreatePlaylistRequest) -> dict[str, Any]:
        if req.criteria is not None:
            try:
                mc = MagicCriteria.from_dict(req.criteria)
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            new_id = index.create_magic_playlist(req.title, mc)
            pl = index.get_playlist(new_id)
            assert pl is not None
            _rebuild_field_index()
        else:
            pl = index.create_playlist(req.title)
        return _enrich_playlist(pl)

    @app.get("/api/v1/playlists", response_model=list[PlaylistOut])
    def list_playlists(type: str | None = None) -> list[dict[str, Any]]:
        playlists = index.get_playlists()
        result: list[dict[str, Any]] = []
        for pl in playlists:
            _enrich_playlist(pl)
            if type == "simple" and pl["criteria"] is not None:
                continue
            result.append(pl)
        return result

    @app.get("/api/v1/playlists/{playlist_id}", response_model=PlaylistOut)
    def get_playlist(playlist_id: int) -> dict[str, Any]:
        pl = index.get_playlist(playlist_id)
        if pl is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        return _enrich_playlist(pl)

    @app.patch("/api/v1/playlists/{playlist_id}", response_model=PlaylistOut)
    def patch_playlist(playlist_id: int, req: PatchPlaylistRequest) -> dict[str, Any]:
        pl = index.get_playlist(playlist_id)
        if pl is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if req.title is not None:
            index.rename_playlist(playlist_id, req.title)
        if req.favorite is not None:
            index.set_playlist_favorite(playlist_id, req.favorite)
        updated = index.get_playlist(playlist_id)
        assert updated is not None
        return _enrich_playlist(updated)

    @app.delete("/api/v1/playlists/{playlist_id}", status_code=204)
    def delete_playlist(playlist_id: int) -> Response:
        pl = index.get_playlist(playlist_id)
        if pl is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        index.delete_playlist(playlist_id)
        _rebuild_field_index()
        return Response(status_code=204)

    @app.post("/api/v1/playlists/{playlist_id}/played", status_code=204)
    def record_playlist_played(playlist_id: int) -> Response:
        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        index.record_playlist_played(playlist_id)
        return Response(status_code=204)

    @app.get("/api/v1/playlists/{playlist_id}/module-content")
    def get_playlist_module_content(
        playlist_id: int,
        contents: str = "albums",
        sort: str = "random",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        return index.get_playlist_module_content(playlist_id, contents, sort, limit)

    @app.get(
        "/api/v1/playlists/{playlist_id}/tracks", response_model=list[PlaylistTrackOut]
    )
    def get_playlist_tracks(playlist_id: int) -> list[dict[str, Any]]:
        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if index.get_magic_playlist_criteria(playlist_id) is not None:
            rows = index.get_magic_playlist_tracks(playlist_id)
        else:
            rows = index.get_playlist_tracks(playlist_id)
        # These rows are hand-rolled dicts (not via TrackOut.from_track), so attach
        # sources here in the response layer (KAMP-537).
        src_map = index.sources_for_track_ids([r["id"] for r in rows if r.get("id")])
        for r in rows:
            r["sources"] = [
                SourceOut.from_row(s).model_dump() for s in src_map.get(r["id"], [])
            ]
        return rows

    @app.put("/api/v1/playlists/{playlist_id}/criteria", response_model=PlaylistOut)
    def update_playlist_criteria(
        playlist_id: int, req: UpdateCriteriaRequest
    ) -> dict[str, Any]:
        pl = index.get_playlist(playlist_id)
        if pl is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        try:
            mc = MagicCriteria.from_dict(req.criteria)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            index.update_magic_playlist_criteria(playlist_id, mc)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _rebuild_field_index()
        updated = index.get_playlist(playlist_id)
        assert updated is not None
        return _enrich_playlist(updated)

    @app.get("/api/v1/playlists/{playlist_id}/criteria")
    def get_playlist_criteria(playlist_id: int) -> dict[str, Any]:
        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        mc = index.get_magic_playlist_criteria(playlist_id)
        return {"criteria": mc.to_dict() if mc is not None else None}

    @app.post("/api/v1/criteria/preview")
    def preview_criteria(req: CriteriaPreviewRequest) -> dict[str, Any]:
        try:
            mc = MagicCriteria.from_dict(req.criteria)
            return {"count": index.count_magic_criteria(mc)}
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/playlists/{playlist_id}/tracks")
    def add_to_playlist(
        playlist_id: int, req: AddTrackToPlaylistRequest
    ) -> dict[str, Any]:
        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        if req.id is not None:  # KAMP-552: single track by canonical id
            track = index.get_track_by_id(req.id)
            if track is None:
                raise HTTPException(status_code=404, detail="Track not found")
            index.add_track_to_playlist(
                playlist_id, _canonical_track_uri(track.file_path)
            )
        elif req.album_artist is not None and req.album is not None:
            tracks = index.tracks_for_album(req.album_artist, req.album)
            for t in tracks:
                index.add_track_to_playlist(playlist_id, str(t.file_path))
        else:
            raise HTTPException(
                status_code=400, detail="Provide id or album_artist+album"
            )
        return {"ok": True}

    @app.delete(
        "/api/v1/playlists/{playlist_id}/tracks/{playlist_track_id}", status_code=204
    )
    def remove_from_playlist(playlist_id: int, playlist_track_id: int) -> Response:
        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        index.remove_track_from_playlist(playlist_id, playlist_track_id)
        return Response(status_code=204)

    @app.put("/api/v1/playlists/{playlist_id}/order")
    def reorder_playlist(
        playlist_id: int, req: ReorderPlaylistRequest
    ) -> dict[str, Any]:
        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        try:
            index.reorder_playlist_tracks(playlist_id, req.track_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/api/v1/playlists/{playlist_id}/art")
    def playlist_art(playlist_id: int) -> Response:
        pl = index.get_playlist(playlist_id)
        if pl is None:
            raise HTTPException(status_code=404, detail="Playlist not found")
        cover = index.get_playlist_cover(playlist_id)
        if cover is not None:
            return Response(content=cover, media_type="image/jpeg")
        title = pl["title"]
        display = title if len(title) <= 12 else title[:11] + "…"
        svg = _PLAYLIST_ART_TEMPLATE.replace("__TITLE__", display)
        return Response(content=svg, media_type="image/svg+xml")

    @app.post("/api/v1/playlists/{playlist_id}/art", response_model=PlaylistOut)
    async def set_playlist_art(
        playlist_id: int,
        file: UploadFile = File(...),
    ) -> PlaylistOut:
        """Upload a local image file and store it as cover art for the playlist.

        Returns 404 if the playlist does not exist.
        Returns 422 if the uploaded file is not a valid image.
        Returns the updated PlaylistOut on success.
        """
        from kamp_daemon.artwork import (
            ArtworkError,
            validate_image_bytes,
        )  # noqa: PLC0415

        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=422, detail="Uploaded file must be an image"
            )

        image_data = await file.read()

        try:
            validate_image_bytes(image_data)
        except ArtworkError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if index.get_playlist(playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist not found")

        updated = index.set_playlist_cover(playlist_id, image_data)
        return PlaylistOut(**_enrich_playlist(updated))  # type: ignore[arg-type]

    # -----------------------------------------------------------------------
    # WebSocket: player state stream
    # -----------------------------------------------------------------------

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(ws: WebSocket, token: str = "") -> None:
        # Accept token via query param (legacy / non-Electron clients) or the
        # X-Kamp-Token header injected by Electron's webRequest interceptor.
        received = token or ws.headers.get("x-kamp-token", "")
        if auth_token is not None and received != auth_token:
            await ws.close(code=1008)  # Policy Violation
            return
        nonlocal _event_loop
        _event_loop = asyncio.get_running_loop()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        _ws_queues.add(q)

        await ws.accept()
        # Push initial snapshot immediately on connect.
        await ws.send_json({"type": "player.state", **_state_snapshot().model_dump()})
        # Replay any proxy-fetch events that fired before this client connected.
        # This closes the startup-race window: the daemon may have posted a
        # proxy request before the Electron preload established its WS connection.
        for pending_event in list(_pending_proxy_fetches.values()):
            await ws.send_json(pending_event)
        last_library_version: int = _state["library_version"]
        try:
            while True:
                # Await either a client ping or a server-push event — whichever
                # arrives first.  Both paths may fire in the same iteration if a
                # push event arrives while a ping is also pending.
                recv_task = asyncio.create_task(ws.receive_text())
                push_task = asyncio.create_task(q.get())
                done, pending = await asyncio.wait(
                    {recv_task, push_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                    # asyncio.CancelledError is BaseException in Python 3.8+, so
                    # suppress it explicitly alongside any other exception a
                    # cancelled task may carry (e.g. WebSocketDisconnect).
                    with suppress(asyncio.CancelledError, Exception):
                        await t

                if push_task in done:
                    await ws.send_json(push_task.result())

                if recv_task in done:
                    # Retrieve the exception (if any) before branching so asyncio
                    # never sees an un-retrieved exception on the task object,
                    # which would emit a "Task exception was never retrieved" warning.
                    # Guard against cancelled tasks: .exception() raises CancelledError
                    # if the task was cancelled (it shouldn't be here since it's done,
                    # but be defensive).
                    _recv_exc = None if recv_task.cancelled() else recv_task.exception()
                    if _recv_exc is not None:
                        raise _recv_exc
                    # Each "ping" from the client triggers a fresh snapshot.
                    await ws.send_json(
                        {"type": "player.state", **_state_snapshot().model_dump()}
                    )
                    # Notify the client if a background scan updated the library
                    # since the last ping so it can refresh the album list.
                    current_version = _state["library_version"]
                    if current_version != last_library_version:
                        last_library_version = current_version
                        await ws.send_json({"type": "library.changed"})
        except Exception:
            pass
        finally:
            _ws_queues.discard(q)

    return app
