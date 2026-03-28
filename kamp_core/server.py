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

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kamp_core.library import LibraryIndex, LibraryScanner, Track
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


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    index: LibraryIndex,
    engine: MpvPlaybackEngine,
    queue: PlaybackQueue,
    library_path: Path | None = None,
) -> FastAPI:
    """Return a configured FastAPI application.

    All mutable state (index, engine, queue) is owned by the caller.  This
    makes the app easy to test: pass mock objects, use TestClient, done.
    """
    app = FastAPI(title="Kamp", version="1")

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
    def get_albums() -> list[AlbumOut]:
        return [
            AlbumOut(
                album_artist=a.album_artist,
                album=a.album,
                year=a.year,
                track_count=a.track_count,
            )
            for a in index.albums()
        ]

    @app.get("/api/v1/artists", response_model=list[str])
    def get_artists() -> list[str]:
        return index.artists()

    @app.get(
        "/api/v1/albums/{album_artist}/{album}/tracks",
        response_model=list[TrackOut],
    )
    def get_tracks(album_artist: str, album: str) -> list[TrackOut]:
        return [
            TrackOut.from_track(t) for t in index.tracks_for_album(album_artist, album)
        ]

    @app.post("/api/v1/library/scan", response_model=ScanResult)
    def scan_library() -> ScanResult:
        if library_path is None:
            raise HTTPException(status_code=503, detail="Library path not configured")
        result = LibraryScanner(index).scan(library_path)
        return ScanResult(
            added=result.added, removed=result.removed, unchanged=result.unchanged
        )

    # -----------------------------------------------------------------------
    # Player
    # -----------------------------------------------------------------------

    @app.get("/api/v1/player/state", response_model=PlayerStateOut)
    def get_player_state() -> PlayerStateOut:
        return _state_snapshot()

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
        try:
            while True:
                # Each "ping" from the client triggers a fresh snapshot.
                # This keeps the protocol simple for Phase 1; a push-based
                # approach (asyncio task + event) can replace it later.
                await ws.receive_text()
                await ws.send_json(
                    {"type": "player.state", **_state_snapshot().model_dump()}
                )
        except Exception:
            pass

    return app
