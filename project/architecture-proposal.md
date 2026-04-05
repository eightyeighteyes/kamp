# Kamp: Architecture Proposal

**Estimate:** Discography (20–26 sessions across 4 phases)

## Context

The vision describes a cross-platform, extensible, panel-based music player that supports joyful listening and artist support (Bandcamp-first). The existing `kamp` project is a production-quality **backend ingest daemon** — it handles Bandcamp downloads, MusicBrainz tagging, cover art embedding, and library organization, but has no GUI, no playback engine, and no library index.

The existing daemon is the "back of house." The dining room does not exist yet. This proposal is about building it.

---

## Architectural Decisions

### ADR-1: GUI Stack — Electron + React (TypeScript throughout)

Electron is the right call:
- VS Code is built on this exact stack — the most direct reference implementation for panel-based, extensible desktop apps
- No Rust required (Tauri's build chain adds a steep learning curve alongside React and Electron)
- Massive community, well-documented extension patterns, mature tooling

Trade-offs accepted: ~150–200 MB bundle, ~80–120 MB RAM at idle — acceptable for a foreground desktop app.

### ADR-2: Playback Engine — mpv subprocess via JSON IPC socket

mpv runs as a standalone subprocess, controlled via `--input-ipc-server`. The Python daemon sends commands and reads events over the socket.

**Why not Python audio bindings (python-vlc, python-mpv)?**
- Python's GIL causes audio glitches under load
- A libmpv crash takes down the daemon process that also manages the ingest pipeline
- Subprocess isolation: a crashing mpv affects playback only

**Why mpv for audiophile fidelity?**
- CoreAudio exclusive mode (macOS), WASAPI exclusive (Windows), ALSA/PipeWire (Linux)
- No forced resampling — bit-perfect passthrough configurable
- mpv is used in audiophile contexts specifically for this

mpv bundled with the app (~30 MB) to eliminate runtime dependency. `PlaybackEngine` interface defined in Python; mpv implementation is swappable.

### ADR-3: Backend — Python; API is the contract

The Python daemon remains the backend. The REST + WebSocket interface is the contract — not the language. If the daemon were ever rewritten in Go, the Electron frontend changes nothing.

- FastAPI for REST (`/api/v1/...`) + WebSocket for event streaming
- API documented in OpenAPI spec from day one
- All Electron-side API calls live in a single `src/api/` module; no component calls `fetch()` directly

### ADR-4: IPC Design is Mobile-Ready by Construction

Design the API as if the client may not be on the same device:

- Configurable base URL (never hardcoded `localhost`) — stubbed from Phase 1
- WebSocket events are **state streams**: `{ type: "player.state", track: {...}, position: 42.1, playing: true }` — not UI events
- All player state lives in the daemon; the renderer is a stateless view layer
- User preferences live in `config.toml`, not `electron-store` or `localStorage`

A future React Native mobile client calls the same API over the local network. No architectural changes needed in the daemon.

### ADR-5: Library Index — SQLite

`sqlite3` (stdlib). Schema: artist, album_artist, album, year, track_number, title, file_path, embedded_art, musicbrainz IDs. Migration version table from day one.

### ADR-6: Extension System — Dual-Layer

**Frontend extensions (npm packages):**
- Discovered via `kamp-extension` keyword in `package.json`
- Export a manifest declaring contributed panels/components
- Access the player via `window.KampAPI` — injected via Electron's `contextBridge` in the preload script. Extensions never touch `ipcRenderer` or Node.js directly.
- Phase 1: first-party extensions, `contextBridge` isolation is sufficient
- Phase 2: community extensions rendered in `<iframe sandbox="allow-scripts">` communicating via `postMessage`; strict CSP on the renderer window

**Backend extensions (Python packages):**
- Declared via `[project.entry-points."kamp.extensions"]`
- Implement abstract base classes (`BaseTagger`, `BaseArtworkSource`, etc.)
- Run inside existing spawn-context worker subprocesses — a crash quarantines the item, not the daemon

**Invariant:** All built-in features are extensions. Bandcamp sync, MusicBrainz tagger, and artwork fetcher must be buildable using the public `KampGround` API. The SDK surface should be extracted from two real working extensions, not designed in the abstract first — otherwise the API will be wrong.

**Hot reload:** watch extensions directory and reload on file change.

### ADR-7: OS Integrations — `MediaController` interface with platform dispatch table

Define `MediaController(ABC)` in the daemon. Platform implementations (`CoreAudioMediaController`, `WinMediaController`, `MPRISController`, `NullMediaController`) are registered at startup via a dispatch table. No `if platform == "darwin"` conditionals outside the dispatch table.

Same pattern in Electron's main process for taskbar progress, tray behavior, and global shortcuts.

Adding Linux MPRIS support should require touching zero files that also contain Bandcamp, playback, or library logic.

---

## Build Phases

### Phase 1 — Library Player MVP (Box Set: ~6–8 sessions)
Goal: a working music player over the library the daemon already manages. No new ingest features.

| Component | Effort | Notes |
|-----------|--------|-------|
| SQLite library index + `LibraryIndex` class | LP | Schema, migrations, CRUD queries |
| Library scanner | LP | Walks library dir; hooks existing watchdog watcher for incremental re-scans |
| `PlaybackEngine` protocol + `MpvPlaybackEngine` + `PlaybackQueue` | LP | mpv IPC commands + events; next/prev/shuffle/repeat |
| FastAPI backend (REST `/api/v1/` + WebSocket state stream) | LP | OpenAPI spec; configurable base URL |
| Electron + React GUI: album art grid + artist panel + transport bar | 2xLP | Largest unknown; no extensibility hooks yet |
| First-run setup | Side | Point at existing library; trigger scan; show progress |

**Excludes:** Bandcamp UI, extension system, multi-view layout, panel customization, search UI.

### Phase 2 — Search, Now Playing, Polish (~3–4 sessions)

| Component | Effort |
|-----------|--------|
| Full-text search (backend endpoint + search bar in UI) | Side |
| Now Playing view (artwork-centered; view switching) | LP |
| Playback persistence (resume last track/position on restart) | Single |
| `MediaController` + macOS media keys / Now Playing widget | Side |
| Panel layout persistence + keyboard shortcuts | Side |
| Windows taskbar controls + Linux MPRIS | Side each |

### Phase 3 — Bandcamp Integration in GUI (~4 sessions)
The daemon already does the work. Phase 3 surfaces it.

| Component | Effort |
|-----------|--------|
| Sync status in GUI (idle/syncing, manual sync trigger, recent additions) | Side |
| New purchase highlight (watcher → library re-scan → surfaced in UI) | Side |
| Bandcamp storefront browser (collection, wishlist, followed artists in-app) | LP |
| Purchase flow ("Buy" → Bandcamp checkout → daemon picks up download) | LP |

### Phase 4 — Extension Platform (~5–8 sessions)

| Component | Effort |
|-----------|--------|
| Extension host + `KampGround` API | LP |
| Refactor built-ins as extensions (validates API; if painful, stop and fix the API first) | LP |
| `contextBridge` API + frontend panel registration system | LP |
| UI slot API: declarative panel manifests rendered by Electron host | LP |
| iframe sandboxing + CSP for community extensions | Side |
| Extension settings UI + developer docs | Side each |

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Seek bar jitter (WebSocket latency for position at ~10 Hz) | High | mpv pushes `time-pos` events directly via socket; validate timing before IPC design is finalized |
| mpv bundling complexity on 3 platforms | Medium | Validate packaging on macOS, Windows, Linux before committing; pin minimum mpv version |
| "All features are extensions" invariant erodes under deadline | Medium | Design extension seams in Phase 1; build a real frontend extension before Phase 4 |
| SQLite schema migration causes data loss | Low-Medium | Hand-rolled version table + rescan fallback; decide before Phase 1 ships |
| Renderer accumulates state (undermines mobile-ready design) | Low-Medium | Code review rule: no `useState` for data that lives in the daemon |
| Memory regression from existing ~50 MB backlog item | Low | Maintain subprocess isolation pattern; mpv is already isolated |

---

## Open Questions (resolved)

| Question | Decision |
|----------|----------|
| Tauri vs Electron? | **Electron** |
| Python audio bindings vs standalone engine? | **mpv subprocess via IPC socket** |
| Extension system: Python only? | **Dual-layer**: npm (frontend) + Python (backend) |
| Mobile in scope? | **Desktop first; mobile-ready by API design** |
| Extension sandboxing? | **contextBridge (Phase 1) → iframe sandbox (Phase 2)** |
| Audio fidelity? | **High fidelity default**: mpv exclusive mode per platform; no resampling |
| macOS-first vs cross-platform? | **macOS first; `MediaController` dispatch table for everything OS-specific** |
| Backend language rewrite risk? | **Mitigated**: wire contract is REST/WebSocket; frontend changes nothing if backend is rewritten |

---

## Critical Files

**Existing — to extend:**
- `kamp_daemon/watcher.py` — hook library scanner into watchdog events
- `kamp_daemon/config.py` — extend schema for library index path, mpv path, server port

**New — to create:**
- `kamp_core/library.py` — `LibraryIndex` (SQLite), `LibraryScanner`
- `kamp_core/playback.py` — `PlaybackEngine` protocol + `MpvPlaybackEngine` + `PlaybackQueue`
- `kamp_core/server.py` — FastAPI app (REST + WebSocket)
- `kamp_core/media_controller.py` — `MediaController(ABC)` + platform dispatch table
- `kamp_ui/` — Electron + React project (`src/api/` module, panel registry, transport)

---

## Phase 1 Exit Criteria

- `kamp library scan` indexes an existing library; `GET /api/v1/albums` returns JSON with artwork URLs
- `kamp server` starts; WebSocket connection receives `player.state` events at ~10 Hz during playback
- Electron app opens; album art grid renders; clicking a track plays audio via mpv
- Artist panel filters album browser; transport play/pause/seek/skip work
- Playback state persists across app restarts
- Existing `kamp daemon` (ingest pipeline) operates independently, unaffected
- Base URL is configurable; no hardcoded `localhost` in frontend code
