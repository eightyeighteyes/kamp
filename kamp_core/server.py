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
import threading as _threading
import uuid as _uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kamp_core.library import LibraryIndex, LibraryScanner, Track, extract_art
from kamp_core.playback import MpvPlaybackEngine, PlaybackQueue

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TrackOut(BaseModel):
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
    favorite: bool
    play_count: int

    @classmethod
    def from_track(cls, t: Track) -> "TrackOut":
        return cls(
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
            favorite=t.favorite,
            play_count=t.play_count,
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


class PlayerStateOut(BaseModel):
    playing: bool
    position: float
    duration: float
    volume: int
    current_track: TrackOut | None


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


class FavoriteRequest(BaseModel):
    file_path: str
    favorite: bool


class SearchOut(BaseModel):
    albums: list[AlbumOut]
    tracks: list[TrackOut]


class QueueOut(BaseModel):
    tracks: list[TrackOut]
    position: int  # index of the currently playing track; -1 if empty


class AddToQueueRequest(BaseModel):
    file_path: str


class MoveQueueRequest(BaseModel):
    from_index: int
    to_index: int


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


class BandcampProxyFetchResult(BaseModel):
    id: str
    status: int
    body: str
    content_type: str = "text/html"


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
        "scan_progress": {"active": False, "current": 0, "total": 0},
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
        """Increment the library version so connected WebSocket clients are notified."""
        _state["library_version"] += 1

    def _notify_track_changed() -> None:
        """Broadcast a track.changed push event to all connected WebSocket clients."""
        _broadcast({"type": "track.changed", **_state_snapshot().model_dump()})

    def _notify_play_state_changed() -> None:
        """Broadcast a play_state.changed push event to all connected WebSocket clients."""
        _broadcast({"type": "play_state.changed", **_state_snapshot().model_dump()})

    # Expose notifiers on app.state so the daemon can wire them into engine
    # callbacks (e.g. on_track_end, on_play_state_changed).
    app.state.notify_library_changed = _notify_library_changed
    app.state.notify_track_changed = _notify_track_changed
    app.state.notify_play_state_changed = _notify_play_state_changed

    # Wire play-state change callback directly — the engine fires it from its
    # background reader thread whenever mpv's pause property flips.
    engine.on_play_state_changed = _notify_play_state_changed

    # Allow requests from the Electron renderer (Vite dev server and file://).
    # This server only binds to 127.0.0.1, so wildcard origins are safe.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _state_snapshot() -> PlayerStateOut:
        current = queue.current()
        return PlayerStateOut(
            playing=engine.state.playing,
            position=engine.state.position,
            duration=engine.state.duration,
            volume=engine.state.volume,
            current_track=TrackOut.from_track(current) if current else None,
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
            track = index.get_track_by_path(Path(file_path))
            return [TrackOut.from_track(track)] if track else []
        return [
            TrackOut.from_track(t) for t in index.tracks_for_album(album_artist, album)
        ]

    @app.post("/api/v1/tracks/favorite")
    def set_track_favorite(req: FavoriteRequest) -> dict[str, Any]:
        track = index.get_track_by_path(Path(req.file_path))
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        index.set_favorite(Path(req.file_path), req.favorite)
        # Keep the in-memory queue in sync so the next player-state snapshot
        # reflects the new favorite value without requiring a queue reload.
        queue.update_favorite(Path(req.file_path), req.favorite)
        return {"ok": True}

    @app.get("/api/v1/album-art")
    def get_album_art(album_artist: str, album: str, file_path: str = "") -> Response:
        # file_path overrides (album_artist, album) for missing-album tracks.
        if file_path:
            track = index.get_track_by_path(Path(file_path))
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(album_artist, album)
        for track in tracks:
            if track.embedded_art:
                result = extract_art(track.file_path)
                if result:
                    data, mime = result
                    return Response(content=data, media_type=mime)
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
            )
            for a in index.albums(sort=sort)
            if (a.album_artist, a.album) in fts_keys
            or (a.missing_album and a.file_path in fts_paths)
        ]
        return SearchOut(
            albums=albums, tracks=[TrackOut.from_track(t) for t in fts_tracks]
        )

    @app.post("/api/v1/library/scan", response_model=ScanResult)
    def scan_library() -> ScanResult:
        if _state["library_path"] is None:
            raise HTTPException(status_code=503, detail="Library path not configured")

        def _on_progress(current: int, total: int) -> None:
            _state["scan_progress"] = {
                "active": True,
                "current": current,
                "total": total,
            }

        _state["scan_progress"] = {"active": True, "current": 0, "total": 0}
        try:
            result = LibraryScanner(index).scan(
                _state["library_path"], on_progress=_on_progress
            )
        finally:
            _state["scan_progress"] = {"active": False, "current": 0, "total": 0}

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
        candidate = Path(req.path).expanduser().resolve()
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
        if view not in ("library", "now-playing"):
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
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Read username back from session (populated by the callback when
        # the API call succeeds) and surface it in the config state.
        if get_bandcamp_session is not None:
            session = get_bandcamp_session()
            if session:
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
        _state["config"]["bandcamp.username"] = None
        _broadcast({"type": "bandcamp.disconnected"})
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
        return QueueOut(tracks=[TrackOut.from_track(t) for t in tracks], position=pos)

    @app.post("/api/v1/player/play")
    def play(req: PlayRequest) -> dict[str, Any]:
        if req.file_path:
            track = index.get_track_by_path(Path(req.file_path))
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(req.album_artist, req.album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.load(tracks, start_index=req.track_index)
        current = queue.current()
        if current:
            engine.play(current.file_path)
        _notify_track_changed()
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
        track = queue.next()
        if track:
            engine.play(track.file_path)
        else:
            engine.stop()
        _notify_track_changed()
        return {"ok": True}

    @app.post("/api/v1/player/prev")
    def prev_track() -> dict[str, Any]:
        track = queue.prev()
        if track:
            engine.play(track.file_path)
        _notify_track_changed()
        return {"ok": True}

    @app.post("/api/v1/player/queue/clear")
    def queue_clear() -> dict[str, Any]:
        queue.clear()
        return {"ok": True}

    @app.post("/api/v1/player/queue/clear-remaining")
    def queue_clear_remaining(req: SkipToRequest) -> dict[str, Any]:
        queue.clear_remaining(req.position)
        return {"ok": True}

    @app.post("/api/v1/player/queue/skip-to")
    def skip_to_position(req: SkipToRequest) -> dict[str, Any]:
        track = queue.skip_to(req.position)
        if track:
            engine.play(track.file_path)
        _notify_track_changed()
        return {"ok": True}

    @app.post("/api/v1/player/queue/add")
    def queue_add(req: AddToQueueRequest) -> dict[str, Any]:
        track = index.get_track_by_path(Path(req.file_path))
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        queue.add_to_queue(track)
        return {"ok": True}

    @app.post("/api/v1/player/queue/play-next")
    def queue_play_next(req: AddToQueueRequest) -> dict[str, Any]:
        track = index.get_track_by_path(Path(req.file_path))
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        queue.play_next(track)
        return {"ok": True}

    @app.post("/api/v1/player/queue/insert")
    def queue_insert(req: InsertQueueRequest) -> dict[str, Any]:
        track = index.get_track_by_path(Path(req.file_path))
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        queue.insert_at(track, req.index)
        return {"ok": True}

    @app.post("/api/v1/player/queue/add-album")
    def queue_add_album(req: AlbumQueueRequest) -> dict[str, Any]:
        if req.file_path:
            track = index.get_track_by_path(Path(req.file_path))
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(req.album_artist, req.album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.add_album_to_queue(tracks)
        return {"ok": True}

    @app.post("/api/v1/player/queue/play-album-next")
    def queue_play_album_next(req: AlbumQueueRequest) -> dict[str, Any]:
        if req.file_path:
            track = index.get_track_by_path(Path(req.file_path))
            tracks = [track] if track else []
        else:
            tracks = index.tracks_for_album(req.album_artist, req.album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.play_album_next(tracks)
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
        return {"ok": True}

    @app.post("/api/v1/player/queue/move")
    def queue_move(req: MoveQueueRequest) -> dict[str, Any]:
        try:
            queue.move(req.from_index, req.to_index)
        except IndexError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/v1/player/shuffle")
    def set_shuffle(req: ShuffleRequest) -> dict[str, Any]:
        queue.set_shuffle(req.shuffle)
        return {"ok": True}

    @app.post("/api/v1/player/repeat")
    def set_repeat(req: RepeatRequest) -> dict[str, Any]:
        queue.set_repeat(req.repeat)
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
        # Include session cookies in the broadcast so the Electron main
        # process can inject them into session.defaultSession before calling
        # net.fetch — no extra HTTP round-trip required.
        session_data = (
            get_bandcamp_session() if get_bandcamp_session is not None else None
        )
        broadcast_cookies: list[Any] = (
            session_data.get("cookies", []) if session_data else []
        )
        # Build the push event.  Save it in _pending_proxy_fetches *before*
        # broadcasting so that a WS client connecting after _broadcast() (but
        # before the request is answered) still receives it on connect.  The
        # entry is removed when /fetch-result arrives.
        proxy_event: dict[str, Any] = {
            "type": "bandcamp.proxy-fetch",
            "id": req_id,
            "url": req.url,
            "method": req.method,
            "headers": req.headers,
            "body": req.body,
            "cookies": broadcast_cookies,
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
        }
        # Remove from pending — this request has been answered.
        _pending_proxy_fetches.pop(req.id, None)
        entry["event"].set()
        return {"ok": True}

    # -----------------------------------------------------------------------
    # WebSocket: player state stream
    # -----------------------------------------------------------------------

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
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
