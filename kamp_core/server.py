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
import re
import sys
import threading as _threading
import uuid as _uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast
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

from kamp_core.library import LibraryIndex, LibraryScanner, Track, extract_art
from kamp_core.playback import MpvPlaybackEngine, PlaybackQueue

# ---------------------------------------------------------------------------
# Playback URI resolution
# ---------------------------------------------------------------------------


def resolve_playback_uri(
    track: Track,
    index: LibraryIndex,
    refresh_stream_url: Callable[[str, int], tuple[str, float] | None] | None,
) -> str:
    """Return the URL or path string to pass to mpv for *track*.

    For local tracks returns str(track.file_path). For remote tracks checks
    stream URL expiry and attempts a refresh via the refresh_stream_url
    callback before returning track.playback_uri.
    """
    if not track.is_remote:
        return track.playback_uri

    import time as _t

    needs_refresh = (
        track.stream_url_expires_at is None
        or track.stream_url_expires_at < _t.time() + 60
    )
    if needs_refresh and refresh_stream_url is not None:
        _fp = str(track.file_path)
        # Parse sale_item_id and track_number from bandcamp://sale_id/track_num.
        # Path() mutates the URI differently per platform:
        #   POSIX: bandcamp:// → bandcamp:/ (double-slash collapse)
        #   Windows: bandcamp:// → bandcamp:\\ (backslash normalisation)
        # Split on "bandcamp:" and strip all leading slashes/backslashes to
        # get the raw "sale_id/track_num" segment, then reconstruct canonical
        # "bandcamp://sale_id/track_num" for DB lookups.
        _after_scheme = _fp.split("bandcamp:", 1)
        _canonical_fp: str | None = None
        _rest: str | None = None
        if len(_after_scheme) == 2:
            _rest = _after_scheme[1].lstrip("/\\").replace("\\", "/")
            _canonical_fp = "bandcamp://" + _rest
        if _canonical_fp is not None and _rest is not None:
            parts = _rest.split("/", 1)
            if len(parts) == 2:
                sale_item_id, track_num_str = parts
                try:
                    track_num = int(track_num_str)
                except ValueError:
                    track_num = 0
                item = index.get_collection_item(sale_item_id)
                album_url = (item or {}).get("album_url", "")
                if album_url:
                    result = refresh_stream_url(album_url, track_num)
                    if result is not None:
                        new_url, expires_at = result
                        index.update_stream_url(_canonical_fp, new_url, expires_at)
                        return new_url
                    else:
                        logger.warning(
                            "Stream URL refresh failed for %s — using existing URI",
                            _canonical_fp,
                        )
                else:
                    logger.warning(
                        "No album_url for %s — cannot refresh stream URL; "
                        "run kamp sync to populate.",
                        _canonical_fp,
                    )
    return track.playback_uri


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TrackOut(BaseModel):
    id: int
    title: str
    artist: str
    album_artist: str
    album: str
    year: str
    track_number: int
    disc_number: int
    file_path: str
    ext: str
    embedded_art: bool
    mb_release_id: str
    mb_recording_id: str
    genre: str
    label: str
    favorite: bool
    play_count: int
    source: str

    @classmethod
    def from_track(cls, t: Track) -> "TrackOut":
        return cls(
            id=t.id,
            title=t.title,
            artist=t.artist,
            album_artist=t.album_artist,
            album=t.album,
            year=t.year,
            track_number=t.track_number,
            disc_number=t.disc_number,
            file_path=str(t.file_path),
            ext=t.ext,
            embedded_art=t.embedded_art,
            mb_release_id=t.mb_release_id,
            mb_recording_id=t.mb_recording_id,
            genre=t.genre,
            label=t.label,
            favorite=t.favorite,
            play_count=t.play_count,
            source=t.source,
        )


class AlbumOut(BaseModel):
    album_artist: str
    album: str
    year: str
    track_count: int
    has_art: bool
    missing_album: bool = False
    # Non-empty only when missing_album=True; used as the unique lookup key
    # instead of (album_artist, album) for tracks without an album tag.
    file_path: str = ""
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


class PlayerStateOut(BaseModel):
    playing: bool
    position: float
    duration: float
    volume: int
    current_track: TrackOut | None
    next_track: TrackOut | None = None


class PlayRequest(BaseModel):
    album_artist: str
    album: str
    track_index: int = 0
    file_path: str = ""  # non-empty for missing-album tracks


class SeekRequest(BaseModel):
    position: float


class VolumeRequest(BaseModel):
    volume: int


class ShuffleRequest(BaseModel):
    shuffle: bool


class RepeatRequest(BaseModel):
    repeat: bool


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
    file_path: str
    favorite: bool


class AlbumFavoriteRequest(BaseModel):
    album_artist: str
    album: str
    favorite: bool


class SearchOut(BaseModel):
    albums: list[AlbumOut]
    tracks: list[TrackOut]


class QueueOut(BaseModel):
    tracks: list[TrackOut]
    position: int  # index of the currently playing track; -1 if empty
    shuffle: bool
    repeat: bool


class AddToQueueRequest(BaseModel):
    file_path: str


class MoveQueueRequest(BaseModel):
    from_index: int
    to_index: int


class ReorderQueueRequest(BaseModel):
    order: list[int]


class InsertQueueRequest(BaseModel):
    file_path: str
    index: int


class AlbumQueueRequest(BaseModel):
    album_artist: str
    album: str
    file_path: str = ""  # non-empty for missing-album tracks


class InsertAlbumQueueRequest(BaseModel):
    album_artist: str
    album: str
    index: int
    file_path: str = ""  # non-empty for missing-album tracks


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


class AlbumMetaRequest(BaseModel):
    genre: str | None = None
    label: str | None = None
    year: str | None = None
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
    year: str
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


# OS metadata filenames that macOS (and Windows) drop into every directory.
# These make rmdir() fail even on "empty" folders, so we remove them first.
_OS_METADATA_NAMES: frozenset[str] = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", ".Spotlight-V100", ".Trashes"}
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


def _validate_library_path(file_path: str, library_path: Path | None) -> Path:
    """Resolve *file_path* and verify it lies within *library_path*.

    Raises HTTP 400 when a library_path is configured and the resolved path
    falls outside it — preventing path-traversal attacks from reaching any
    future code that uses the caller-supplied path directly.

    Remote track URIs (bandcamp://) are rejected here; remote tracks are only
    reachable through the queue/album flow, not by direct file_path reference.
    """
    if file_path.startswith("bandcamp:"):
        raise HTTPException(
            status_code=400, detail="Remote tracks cannot be addressed by path"
        )
    p = Path(file_path).resolve()
    if library_path is not None and not p.is_relative_to(library_path.resolve()):
        raise HTTPException(status_code=400, detail="Path outside library directory")
    return p


def _is_remote_uri(s: str) -> bool:
    """True for scheme-prefixed remote URIs (bandcamp://, etc.) that must bypass
    library-path validation."""
    return "://" in s or s.startswith("bandcamp:")


def _validate_proxy_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not any(host == h or host.endswith(f".{h}") for h in _ALLOWED_PROXY_HOSTS):
        raise HTTPException(
            status_code=422, detail=f"Proxy URL host not allowed: {host}"
        )
    return url


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
    refresh_stream_url: Callable[[str, int], tuple[str, float] | None] | None = None,
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
        "ui_queue_panel_open": ui_queue_panel_open,
        "library_version": 0,
        "config": dict(config_values) if config_values is not None else {},
        # Pending proxy-fetch requests from the Python daemon subprocess.
        # id → {"id", "url", "method", "headers", "body", "event", "result"}
        "bandcamp_proxy_requests": {},
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
        _broadcast({"type": "play_state.changed", **_state_snapshot().model_dump()})

    def _notify_bandcamp_sync_status(status_msg: str) -> None:
        """Broadcast sync state derived from the syncer's status_callback string.

        The syncer passes "" on idle and a non-empty string while syncing.
        Called from the syncer's background thread — _broadcast is thread-safe.
        """
        state = "idle" if not status_msg else "syncing"
        _broadcast({"type": "bandcamp.sync-status", "state": state})

    def _notify_pipeline_stage(stage: str) -> None:
        """Broadcast the current pipeline stage to all connected WebSocket clients.

        stage is "" when idle, or a human-readable label ("Extracting", "Tagging",
        "Updating artwork", "Moving") while work is in progress.
        Called from the watcher thread — _broadcast is thread-safe.
        """
        _broadcast({"type": "pipeline.stage", "stage": stage})

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
        return resolve_playback_uri(track, index, refresh_stream_url)

    # Wire play-state change callback directly — the engine fires it from its
    # background reader thread whenever mpv's pause property flips.
    engine.on_play_state_changed = _notify_play_state_changed
    engine.on_audio_level = _notify_audio_level

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
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        # X-Kamp-Token must be listed so CORS preflight allows it.
        allow_headers=["Content-Type", "X-Kamp-Token"],
    )

    def _state_snapshot() -> PlayerStateOut:
        current = queue.current()
        nxt = queue.peek_next()
        return PlayerStateOut(
            playing=engine.state.playing,
            position=engine.state.position,
            duration=engine.state.duration,
            volume=engine.state.volume,
            current_track=TrackOut.from_track(current) if current else None,
            next_track=TrackOut.from_track(nxt) if nxt else None,
        )

    # -----------------------------------------------------------------------
    # Library
    # -----------------------------------------------------------------------

    @app.get("/api/v1/albums", response_model=list[AlbumOut])
    def get_albums(sort: str = "album_artist") -> list[AlbumOut]:
        return [
            AlbumOut(
                album_artist=a.album_artist,
                album=a.album,
                year=a.year,
                track_count=a.track_count,
                has_art=a.has_art,
                missing_album=a.missing_album,
                file_path=a.file_path,
                art_version=a.art_version,
                added_at=a.added_at,
                last_played_at=a.last_played_at,
                play_count_avg=a.play_count_avg,
                favorite=a.favorite,
                has_favorite_track=a.has_favorite_track,
                source=a.source,
                has_remote_tracks=a.has_remote_tracks,
            )
            for a in index.albums(sort=sort)
        ]

    @app.get("/api/v1/artists", response_model=list[str])
    def get_artists() -> list[str]:
        return index.artists()

    @app.get("/api/v1/tracks", response_model=list[TrackOut])
    def get_tracks(
        album_artist: str, album: str, file_path: str = ""
    ) -> list[TrackOut]:
        # Query parameters instead of path segments — artist/album names may
        # contain slashes (e.g. "AC/DC") which would break URL path routing.
        # file_path is used for missing-album tracks where (album_artist, album)
        # is not a unique key; when present it takes precedence.
        if file_path:
            p = _validate_library_path(file_path, _state["library_path"])
            track = index.get_track_by_path(p)
            return [TrackOut.from_track(track)] if track else []
        return [
            TrackOut.from_track(t) for t in index.tracks_for_album(album_artist, album)
        ]

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
            year=track.year,
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
            return TrackOut.from_track(updated)  # type: ignore[arg-type]

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
        return TrackOut.from_track(updated)  # type: ignore[arg-type]

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
        return TrackOut.from_track(updated)  # type: ignore[arg-type]

    @app.get("/api/v1/deferred-ops")
    def get_deferred_ops() -> list[dict[str, Any]]:
        """Return pending deferred ops for frontend reconciliation on WS reconnect."""
        return index.list_pending_deferred_ops_summary()

    @app.post("/api/v1/tracks/favorite")
    def set_track_favorite(req: FavoriteRequest) -> dict[str, Any]:
        if _is_remote_uri(req.file_path):
            track = index.get_track_by_path(req.file_path)
            if track is None:
                raise HTTPException(status_code=404, detail="Track not found")
            index.set_favorite(req.file_path, req.favorite)
            queue.update_favorite(req.file_path, req.favorite)
        else:
            p = _validate_library_path(req.file_path, _state["library_path"])
            track = index.get_track_by_path(p)
            if track is None:
                raise HTTPException(status_code=404, detail="Track not found")
            index.set_favorite(p, req.favorite)
            # Keep the in-memory queue in sync so the next player-state snapshot
            # reflects the new favorite value without requiring a queue reload.
            queue.update_favorite(p, req.favorite)
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
                year=track.year,
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
                        moved.append(TrackOut.from_track(updated))
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
                index.rename_album_tracks_bulk(
                    db_pairs,
                    new_album,
                    new_album_artist,
                    new_mtime,
                    old_album_artist=album_artist,
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
                            moved.append(TrackOut.from_track(updated))
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
                    index.rename_album_tracks_bulk(
                        db_pairs,
                        new_album,
                        new_album_artist,
                        new_mtime,
                        old_album_artist=album_artist,
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
                            moved.append(TrackOut.from_track(updated))

                index.rename_album_tracks_bulk(
                    db_pairs,
                    new_album,
                    new_album_artist,
                    new_mtime,
                    old_album_artist=album_artist,
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
                        moved.append(TrackOut.from_track(updated))
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
                index.rename_album_tracks_bulk(
                    db_pairs,
                    new_album,
                    new_album_artist,
                    new_mtime,
                    old_album_artist=album_artist,
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

    @app.patch("/api/v1/albums/meta")
    def patch_album_meta(
        album_artist: str, album: str, req: "AlbumMetaRequest"
    ) -> "AlbumMetaOut":
        """Write genre, label, and/or year to every track in an album.

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
            and req.year is None
            and req.mb_release_id is None
        ):
            raise HTTPException(status_code=400, detail="No changes requested")

        for track in tracks:
            try:
                write_meta_tags_to_file(
                    track.file_path,
                    genre=req.genre,
                    label=req.label,
                    year=req.year,
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
            year=req.year,
            mb_release_id=req.mb_release_id,
        )
        _notify_library_changed()
        return AlbumMetaOut(tracks=[TrackOut.from_track(t) for t in updated])

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
                year=r.year,
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
                    year=a.year,
                    track_count=a.track_count,
                    has_art=a.has_art,
                    missing_album=a.missing_album,
                    file_path=a.file_path,
                    art_version=a.art_version,
                    added_at=a.added_at,
                    last_played_at=a.last_played_at,
                    play_count_avg=a.play_count_avg,
                    favorite=a.favorite,
                    has_favorite_track=a.has_favorite_track,
                    source=a.source,
                    has_remote_tracks=a.has_remote_tracks,
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
                    year=a.year,
                    track_count=a.track_count,
                    has_art=a.has_art,
                    missing_album=a.missing_album,
                    file_path=a.file_path,
                    art_version=a.art_version,
                    added_at=a.added_at,
                    last_played_at=a.last_played_at,
                    play_count_avg=a.play_count_avg,
                    favorite=a.favorite,
                    has_favorite_track=a.has_favorite_track,
                    source=a.source,
                    has_remote_tracks=a.has_remote_tracks,
                )
        raise HTTPException(status_code=404, detail="Album not found after apply")

    @app.get("/api/v1/album-art")
    def get_album_art(
        album_artist: str, album: str, file_path: str = "", v: str = ""
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
            sale_item_id = fp.split("bandcamp:", 1)[1].lstrip("/").split("/")[0]
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

        # Remote albums (bandcamp: URIs) are served via the art proxy cache.
        # Path() on POSIX collapses bandcamp:// → bandcamp:/ so match on the
        # scheme prefix without assuming a specific number of slashes.
        if file_path.startswith("bandcamp:"):
            return _remote_art_response(file_path, cache_control)

        # file_path overrides (album_artist, album) for missing-album tracks.
        if file_path:
            p = _validate_library_path(file_path, _state["library_path"])
            track = index.get_track_by_path(p)
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

        # Local art not found — if any track is remote, try the proxy cache.
        # album.file_path is empty for regular albums, so the bandcamp: check
        # above never fires; use the first remote track's file_path instead.
        remote_tracks = [t for t in tracks if t.is_remote]
        if remote_tracks:
            return _remote_art_response(str(remote_tracks[0].file_path), cache_control)

        raise HTTPException(status_code=404, detail="No art found")

    @app.get("/api/v1/search", response_model=SearchOut)
    def search_library(q: str = "", sort: str = "album_artist") -> SearchOut:
        fts_tracks = index.search(q)
        # Collect the set of (album_artist, album) keys that appear in FTS results,
        # then filter the pre-sorted album list so the response respects sort order.
        # Missing-album tracks have album="" in the DB, so also match them by
        # file_path since their AlbumInfo.album is the display title, not "".
        fts_keys = {(t.album_artist, t.album) for t in fts_tracks}
        fts_paths = {str(t.file_path) for t in fts_tracks if not t.album}
        albums = [
            AlbumOut(
                album_artist=a.album_artist,
                album=a.album,
                year=a.year,
                track_count=a.track_count,
                has_art=a.has_art,
                missing_album=a.missing_album,
                file_path=a.file_path,
                art_version=a.art_version,
                added_at=a.added_at,
                play_count_avg=a.play_count_avg,
                favorite=a.favorite,
                has_favorite_track=a.has_favorite_track,
                source=a.source,
                has_remote_tracks=a.has_remote_tracks,
            )
            for a in index.albums(sort=sort)
            if (a.album_artist, a.album) in fts_keys
            or (a.missing_album and a.file_path in fts_paths)
        ]
        albums.sort(key=lambda a: not a.favorite)
        return SearchOut(
            albums=albums, tracks=[TrackOut.from_track(t) for t in fts_tracks]
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
        {"album_artist", "album", "date_added", "last_played"}
    )

    @app.post("/api/v1/ui/sort-order")
    def set_sort_order(req: dict[str, Any]) -> dict[str, Any]:
        sort = req.get("sort_order", "album_artist")
        if sort not in _VALID_SORT_ORDERS:
            raise HTTPException(status_code=422, detail="Invalid sort order")
        _state["ui_sort_order"] = sort
        if on_ui_state_set is not None:
            on_ui_state_set("ui.sort_order", sort)
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
            tracks=[TrackOut.from_track(t) for t in tracks],
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
        if req.file_path and _is_remote_uri(req.file_path):
            track = index.get_track_by_path(req.file_path)
            tracks = [track] if track else []
        elif req.file_path:
            p = _validate_library_path(req.file_path, _state["library_path"])
            track = index.get_track_by_path(p)
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(req.album_artist, req.album)
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
        if _is_remote_uri(req.file_path):
            track = index.get_track_by_path(req.file_path)
        else:
            p = _validate_library_path(req.file_path, _state["library_path"])
            track = index.get_track_by_path(p)
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
        if _is_remote_uri(req.file_path):
            track = index.get_track_by_path(req.file_path)
        else:
            p = _validate_library_path(req.file_path, _state["library_path"])
            track = index.get_track_by_path(p)
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
        if _is_remote_uri(req.file_path):
            track = index.get_track_by_path(req.file_path)
        else:
            p = _validate_library_path(req.file_path, _state["library_path"])
            track = index.get_track_by_path(p)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        queue.insert_at(track, req.index)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/queue/add-album")
    def queue_add_album(req: AlbumQueueRequest) -> dict[str, Any]:
        if req.file_path:
            p = _validate_library_path(req.file_path, _state["library_path"])
            track = index.get_track_by_path(p)
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(req.album_artist, req.album)
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
        if req.file_path:
            p = _validate_library_path(req.file_path, _state["library_path"])
            track = index.get_track_by_path(p)
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(req.album_artist, req.album)
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
        if req.file_path:
            track = index.get_track_by_path(Path(req.file_path))
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(req.album_artist, req.album)
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
        queue.set_shuffle(req.shuffle)
        engine.preload_next(queue.peek_next())
        return {"ok": True}

    @app.post("/api/v1/player/repeat")
    def set_repeat(req: RepeatRequest) -> dict[str, Any]:
        queue.set_repeat(req.repeat)
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
