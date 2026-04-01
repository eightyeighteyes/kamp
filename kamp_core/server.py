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

from collections.abc import Callable
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
        )


class AlbumOut(BaseModel):
    album_artist: str
    album: str
    year: str
    track_count: int
    has_art: bool


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


class LibraryPathRequest(BaseModel):
    path: str


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


class InsertAlbumQueueRequest(BaseModel):
    album_artist: str
    album: str
    index: int


class SkipToRequest(BaseModel):
    position: int


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
    }

    def _notify_library_changed() -> None:
        """Increment the library version so connected WebSocket clients are notified."""
        _state["library_version"] += 1

    # Expose the notifier on app.state so the caller (__main__.py) can trigger
    # it after background (watcher-driven) scans complete.
    app.state.notify_library_changed = _notify_library_changed

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
            )
            for a in index.albums(sort=sort)
        ]

    @app.get("/api/v1/artists", response_model=list[str])
    def get_artists() -> list[str]:
        return index.artists()

    @app.get("/api/v1/tracks", response_model=list[TrackOut])
    def get_tracks(album_artist: str, album: str) -> list[TrackOut]:
        # Query parameters instead of path segments — artist/album names may
        # contain slashes (e.g. "AC/DC") which would break URL path routing.
        return [
            TrackOut.from_track(t) for t in index.tracks_for_album(album_artist, album)
        ]

    @app.get("/api/v1/album-art")
    def get_album_art(album_artist: str, album: str) -> Response:
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
        fts_keys = {(t.album_artist, t.album) for t in fts_tracks}
        albums = [
            AlbumOut(
                album_artist=a.album_artist,
                album=a.album,
                year=a.year,
                track_count=a.track_count,
                has_art=a.has_art,
            )
            for a in index.albums(sort=sort)
            if (a.album_artist, a.album) in fts_keys
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
            added=result.added, removed=result.removed, unchanged=result.unchanged
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
        tracks = index.tracks_for_album(req.album_artist, req.album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.load(tracks, start_index=req.track_index)
        current = queue.current()
        if current:
            engine.play(current.file_path)
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
        return {"ok": True}

    @app.post("/api/v1/player/prev")
    def prev_track() -> dict[str, Any]:
        track = queue.prev()
        if track:
            engine.play(track.file_path)
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
        tracks = index.tracks_for_album(req.album_artist, req.album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.add_album_to_queue(tracks)
        return {"ok": True}

    @app.post("/api/v1/player/queue/play-album-next")
    def queue_play_album_next(req: AlbumQueueRequest) -> dict[str, Any]:
        tracks = index.tracks_for_album(req.album_artist, req.album)
        if not tracks:
            raise HTTPException(status_code=404, detail="Album not found")
        queue.play_album_next(tracks)
        return {"ok": True}

    @app.post("/api/v1/player/queue/insert-album")
    def queue_insert_album(req: InsertAlbumQueueRequest) -> dict[str, Any]:
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
    # WebSocket: player state stream
    # -----------------------------------------------------------------------

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        # Push initial snapshot immediately on connect.
        await ws.send_json({"type": "player.state", **_state_snapshot().model_dump()})
        last_library_version: int = _state["library_version"]
        try:
            while True:
                # Each "ping" from the client triggers a fresh snapshot.
                await ws.receive_text()
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

    return app
