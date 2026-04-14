# Extension Developer Guide

kamp supports two independent extension layers: **backend extensions** (Python, run inside the daemon pipeline) and **frontend extensions** (JavaScript, run inside the Electron renderer). They are packaged and distributed separately and can be developed independently.

---

## Table of Contents

1. [Overview](#overview)
2. [Frontend Extensions](#frontend-extensions)
   - [package.json manifest](#packagejson-manifest)
   - [The `register(api)` entry point](#the-registerapi-entry-point)
   - [Panels API](#panels-api)
   - [SDK Reference](#sdk-reference)
   - [Settings](#settings)
   - [Sandbox constraints](#sandbox-constraints)
   - [Complete example](#complete-example-frontend)
   - [Publishing to npm](#publishing-to-npm)
3. [Backend Extensions](#backend-extensions)
   - [ABCs: BaseTagger, BaseArtworkSource, BaseSyncer](#abcs-basetagger-baseartworksource-basesyncer)
   - [KampGround API](#kampground-api)
   - [Permissions](#backend-permissions)
   - [Sandbox tiers](#sandbox-tiers)
   - [pyproject.toml entry points](#pyprojecttoml-entry-points)
   - [Complete example](#complete-example-backend)
   - [Publishing to PyPI](#publishing-to-pypi)
4. [What extensions can and cannot do](#what-extensions-can-and-cannot-do)

---

## Overview

| | Frontend | Backend |
|---|---|---|
| Language | JavaScript (ESM) | Python 3.11+ |
| Package format | npm | PyPI / local |
| Install trigger | `kamp-extension` keyword in `package.json` | `kamp.extensions` entry point group |
| Discovery | Electron main process scans `node_modules` | `importlib.metadata.entry_points` |
| Runs in | Sandboxed `<iframe>` | Isolated subprocess (spawn) |
| Access to kamp server | SDK methods (`api.player`, `api.library`) | `KampGround` context object |
| Writes to library | No | Via `KampGround.update_metadata()` / `set_artwork()` |

---

## Frontend Extensions

Frontend extensions add panels to the kamp UI. They are JavaScript (ESM) packages installed via npm.

### package.json manifest

A frontend extension is any npm package whose `package.json` contains `"kamp-extension"` in its `keywords` array. Additional metadata goes in the top-level `kamp` field:

```json
{
  "name": "kamp-my-extension",
  "version": "1.0.0",
  "description": "A kamp UI extension.",
  "keywords": ["kamp-extension"],
  "displayName": "My Extension",
  "main": "index.js",
  "type": "module",
  "kamp": {
    "permissions": ["player.read"],
    "settings": [
      {
        "key": "refreshInterval",
        "label": "Refresh interval (seconds)",
        "type": "number",
        "default": 5,
        "hint": "How often to poll the server"
      }
    ]
  }
}
```

**Required fields:**
- `keywords` must include `"kamp-extension"`
- `main` points to the extension's entry point (must export a `register` function)

**`kamp.permissions`** — declare which SDK namespaces you need. The host only passes namespaces the extension has declared; undeclared ones are simply absent from `api`. Known values:

| Permission | SDK namespace granted |
|---|---|
| `player.read` | `api.player` |
| `library.read` | `api.library` |
| `player.control` | `api.player` (control methods) |
| `network.fetch` | Arbitrary HTTP fetch (community extensions; sandbox CSP limits to kamp server origin) |
| `settings` | `api.settings` |

**`kamp.settings`** — declares a settings schema. kamp renders a form from this in the Extensions preferences panel. Each entry:

```ts
{
  key: string           // storage key
  label: string         // displayed label
  type: 'text' | 'number' | 'boolean' | 'select'
  options?: string[]    // required for type: 'select'
  default?: string | number | boolean
  hint?: string         // optional help text shown below the field
}
```

---

### The `register(api)` entry point

Your `main` file must export a named `register` function. kamp calls it once with an `api` object after the extension loads:

```js
// index.js
export function register(api) {
  // api.panels    — panel registration API (always present)
  // api.player    — player state (only when 'player.read' declared)
  // api.library   — library access (only when 'library.read' declared)
  // api.settings  — settings access (only when 'settings' declared)
}
```

Extensions never receive a raw server URL — all server communication goes through the SDK namespaces.

---

### Panels API

Use `api.panels.register()` to add a panel tab to the UI:

```js
api.panels.register({
  id: 'kamp-my-extension.stats',   // must be globally unique; prefix with your package name
  title: 'Stats',                   // label shown on the tab
  defaultSlot: 'main',              // 'main' | 'left' | 'right' | 'bottom'
  compatibleSlots: ['main', 'left'], // optional; omit to allow all slots

  render(container) {
    // container is an HTMLElement — write into it
    container.innerHTML = '<p>Hello from my extension!</p>'

    // Return a cleanup function called when the tab is hidden
    return () => {
      container.innerHTML = ''
    }
  }
})
```

**`render(container)`** is called each time the panel tab is shown. It receives a fresh `HTMLElement` (the panel body) and must return a zero-argument cleanup function that runs when the tab is hidden or the panel is unmounted.

> **Note:** kamp creates a fresh iframe for each panel mount — DOM state is not preserved across tab switches. Store any state you want to survive in variables captured by the `render` closure, not in the DOM.

---

### SDK Reference

The SDK namespaces passed to `register(api)` are scoped to the permissions declared in `kamp.permissions`. A namespace that wasn't declared is simply absent — accessing it will throw a TypeError at runtime.

---

#### `api.panels` — always present

##### `api.panels.register(manifest)`

Registers a panel tab in the kamp UI. Must be called inside `register()`.

| Parameter | Type | Description |
|---|---|---|
| `manifest.id` | `string` | Globally unique panel ID. Prefix with your package name, e.g. `"kamp-my-ext.view"`. |
| `manifest.title` | `string` | Label shown on the tab. |
| `manifest.defaultSlot` | `'main' \| 'left' \| 'right' \| 'bottom'` | Where the panel appears by default. |
| `manifest.compatibleSlots` | `SlotId[]` _(optional)_ | Slots the user can move this panel to. Omit to allow all slots. |
| `manifest.render` | `(container: HTMLElement) => () => void` | Called each time the panel tab is shown. Must return a cleanup function. |

**`render(container)`** — receives a fresh `HTMLElement` every time the tab is activated. Returns a zero-argument cleanup function called when the tab is hidden or the panel unmounts. kamp creates a new iframe on each mount; DOM state does not persist across tab switches. Keep any state you need in variables captured by the closure.

```js
api.panels.register({
  id: 'kamp-my-ext.view',
  title: 'My View',
  defaultSlot: 'main',
  render(container) {
    container.textContent = 'Hello'
    return () => { container.textContent = '' }
  }
})
```

---

#### `api.player` — requires `"player.read"`

##### `api.player.getState()`

Fetches the current playback state from the kamp server.

**Returns:** `Promise<PlayerState>`

```ts
type PlayerState = {
  playing: boolean       // true when audio is actively playing
  position: number       // playback position in seconds
  duration: number       // total track duration in seconds
  volume: number         // volume level, 0–100
  current_track: Track | null  // null when the queue is empty
}

type Track = {
  title: string
  artist: string
  album_artist: string
  album: string
  year: string
  track_number: number
  disc_number: number
  file_path: string
  ext: string            // file extension, e.g. "flac", "mp3"
  embedded_art: boolean  // true when the file has embedded cover art
  mb_release_id: string  // MusicBrainz release MBID (empty string if unknown)
  mb_recording_id: string
  favorite: boolean
  play_count: number
}
```

```js
const state = await api.player.getState()
if (state.current_track) {
  console.log(`${state.current_track.artist} — ${state.current_track.title}`)
  console.log(`${state.position.toFixed(0)}s / ${state.duration.toFixed(0)}s`)
}
```

Throws if the kamp server is unreachable. Wrap in `try/catch` and degrade gracefully.

---

##### `api.player.onTrackChange(callback)`

Subscribe to track-change events. The callback fires whenever the current track transitions: a new track starts playing, the queue is exhausted, or playback stops.

| Parameter | Type | Description |
|---|---|---|
| `callback` | `(state: PlayerState) => void` | Called with the full player state at the moment of the transition. |

**Returns:** `() => void` — an unsubscribe function. Call it from your `render` cleanup to avoid memory leaks.

```js
render(container) {
  // ...

  const unsub = api.player.onTrackChange((state) => {
    const track = state.current_track
    if (track) {
      container.querySelector('#now-playing').textContent =
        `${track.artist} — ${track.title}`
    }
  })

  // Seed with current state on first mount.
  api.player.getState().then((state) => { /* same update */ }).catch(() => {})

  return () => unsub()  // cancel subscription on unmount
}
```

> **Tip:** Always call `api.player.getState()` once on mount to initialize your UI with the current state, then use `onTrackChange` for subsequent updates. Push events are only sent on *transitions* — the initial state is not replayed when a new subscriber registers.

---

##### `api.player.onPlayStateChange(callback)`

Subscribe to play/pause state changes. The callback fires whenever playback starts or pauses.

| Parameter | Type | Description |
|---|---|---|
| `callback` | `(state: PlayerState) => void` | Called with the full player state at the moment of the change. |

**Returns:** `() => void` — an unsubscribe function.

```js
render(container) {
  const indicator = container.querySelector('#play-indicator')

  const unsub = api.player.onPlayStateChange((state) => {
    indicator.textContent = state.playing ? '▶' : '⏸'
  })

  return () => unsub()
}
```

---

#### `api.library` — requires `"library.read"`

##### `api.library.getAlbumArtUrl(albumArtist, album)`

Returns a URL for an album's embedded cover art. The URL can be used directly as an `<img src>` — no fetch required.

| Parameter | Type | Description |
|---|---|---|
| `albumArtist` | `string` | Album artist name, as returned by `api.player.getState()`. |
| `album` | `string` | Album title, as returned by `api.player.getState()`. |

**Returns:** `string` — an absolute URL to the cover art endpoint.

The URL resolves to a `404` response if the album has no embedded art (`track.embedded_art === false`). Handle `img.onerror` to show a placeholder in that case.

```js
const track = state.current_track
if (track) {
  const url = api.library.getAlbumArtUrl(track.album_artist, track.album)
  img.onload = () => { img.style.opacity = '1' }
  img.onerror = () => { img.style.opacity = '0' }  // no art available
  img.src = url
}
```

---

#### `api.settings` — requires `"settings"`

##### `api.settings.get(key)`

Reads a persisted setting value for this extension.

| Parameter | Type | Description |
|---|---|---|
| `key` | `string` | Setting key, matching a `key` in the `kamp.settings` schema. |

**Returns:** `unknown` — the stored value, or `undefined` if the user has not yet set it. Cast to the expected type or use a default:

```js
const interval = (api.settings.get('refreshInterval') ?? 5)
```

##### `api.settings.set(key, value)`

Persists a setting value for this extension across sessions.

| Parameter | Type | Description |
|---|---|---|
| `key` | `string` | Setting key. |
| `value` | `unknown` | Value to persist. Should match the type declared in the settings schema. |

```js
api.settings.set('refreshInterval', 10)
```

---

### Settings

Declare a settings schema in `kamp.settings` (see [package.json manifest](#packagejson-manifest)) and access values via `api.settings`. Settings are rendered as a form in the Extensions preferences panel and persisted by the host across sessions.

```js
export function register(api) {
  const interval = (api.settings.get('refreshInterval') ?? 5)

  api.panels.register({
    id: 'kamp-my-ext.view',
    title: 'My View',
    defaultSlot: 'main',
    render(container) {
      const timer = setInterval(() => refresh(), interval * 1000)
      return () => clearInterval(timer)
    }
  })
}
```

---

### Sandbox constraints

Community (non-bundled) extensions run inside a sandboxed `<iframe>` with `sandbox="allow-scripts"`. Constraints:

- **No `allow-same-origin`** — the iframe is cross-origin. You cannot access `window.parent`, `localStorage`, IndexedDB, or cookies.
- **No `contextBridge` access** — `window.KampAPI` is not available inside the iframe. Use `api.player`, `api.library`, etc. instead; the SDK proxies calls through the host via postMessage.
- **No external network** — the CSP limits `connect-src` to the kamp server origin (`http://127.0.0.1:8000`). Requests to other origins are blocked.
- **No subframes** — `allow-popups` and `allow-top-navigation` are not granted.
- **State resets on tab switch** — kamp creates a fresh iframe on each panel activation; the previous iframe is torn down.

---

### Complete example (frontend)

`extensions/kamp-my-extension/package.json`:
```json
{
  "name": "kamp-my-extension",
  "version": "1.0.0",
  "description": "Shows the current track title in a side panel.",
  "keywords": ["kamp-extension"],
  "displayName": "Now Playing Text",
  "main": "index.js",
  "type": "module",
  "kamp": {
    "permissions": ["player.read"]
  }
}
```

`extensions/kamp-my-extension/index.js`:
```js
export function register(api) {
  api.panels.register({
    id: 'kamp-my-extension.now-playing-text',
    title: 'Now Playing Text',
    defaultSlot: 'right',

    render(container) {
      container.style.cssText = 'padding: 16px; color: white; font-size: 14px;'

      const el = document.createElement('p')
      el.textContent = '...'
      container.appendChild(el)

      async function poll() {
        try {
          const state = await api.player.getState()
          const track = state.current_track
          el.textContent = track
            ? `${track.artist} — ${track.title}`
            : 'Nothing playing'
        } catch {
          el.textContent = 'Server unreachable'
        }
      }

      void poll()
      const timer = setInterval(() => void poll(), 2000)

      return () => clearInterval(timer)
    }
  })
}
```

---

### Publishing to npm

1. Ensure `keywords` includes `"kamp-extension"` and `main` points to your entry file.
2. `npm publish` — kamp discovers extensions by scanning installed `node_modules` for the keyword, so no registration step is needed.
3. Users install your extension from the kamp Extensions preferences panel or via `npm install kamp-my-extension` in the kamp app directory.

---

## Backend Extensions

Backend extensions hook into the kamp daemon pipeline. They can resolve track metadata, fetch album artwork, or sync new music from external sources.

### ABCs: BaseTagger, BaseArtworkSource, BaseSyncer

Import the ABCs from `kamp_daemon.ext.abc`:

```python
from kamp_daemon.ext.abc import BaseTagger, BaseArtworkSource, BaseSyncer
from kamp_daemon.ext.context import KampGround
from kamp_daemon.ext.types import TrackMetadata, ArtworkQuery, ArtworkResult
```

---

#### `BaseTagger`

Receives a `TrackMetadata`, enriches it, and returns an updated copy. The host writes results to disk — taggers never touch audio files.

```python
class MyTagger(BaseTagger):
    kampground_permissions = ["network.fetch"]
    kampground_network_domains = ["api.example.com"]

    def __init__(self, ctx: KampGround) -> None:
        self._ctx = ctx

    def tag(self, track: TrackMetadata) -> TrackMetadata:
        """Return an updated copy of track with resolved metadata."""
        ...
```

For album-level taggers that can resolve a whole release in one API round-trip, override `tag_release` instead:

```python
def tag_release(self, tracks: list[TrackMetadata]) -> list[TrackMetadata]:
    """Resolve all tracks in a release together. Default calls tag() per track."""
    ...
```

---

#### `BaseArtworkSource`

Fetches cover artwork for an album. Return `None` if no qualifying art is found.

```python
class MyArtSource(BaseArtworkSource):
    kampground_permissions = ["network.fetch"]
    kampground_network_domains = ["img.example.com"]

    def __init__(self, ctx: KampGround) -> None:
        self._ctx = ctx

    def fetch(self, query: ArtworkQuery) -> ArtworkResult | None:
        ...
```

`ArtworkQuery` fields:
- `mbid: str` — MusicBrainz release MBID
- `release_group_mbid: str`
- `album: str`, `artist: str` — human-readable fallback
- `min_dimension: int` — minimum pixel width/height (0 = unconstrained)
- `max_bytes: int` — maximum image size in bytes (0 = unconstrained)

Return `ArtworkResult(image_bytes=..., mime_type="image/jpeg")` on success, or `None`.

---

#### `BaseSyncer`

Polls an external source and deposits new downloads into the kamp watch folder. The host picks them up for ingest.

```python
class MySyncer(BaseSyncer):
    kampground_permissions = ["library.write", "network.fetch"]
    kampground_network_domains = ["api.example.com"]
    _sandbox_tier = "syncer"  # needs filesystem writes

    def __init__(self, ctx: KampGround) -> None:
        self._ctx = ctx
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(timeout=300):
            self._sync()

    def _sync(self) -> None:
        resp = self._ctx.fetch("https://api.example.com/new-releases")
        self._ctx.stage("artist-album.zip", resp.body)
```

`BaseSyncer` lifecycle (called by `DaemonCore`):
1. `start()` — begin background polling
2. `pause()` / `resume()` — suspend/restart polling (default: delegates to `stop`/`start`)
3. `stop()` — final shutdown; join background thread

Optional: override `sync_once()` to support manual triggers and `mark_synced()` to support "mark existing collection as synced without downloading".

---

### KampGround API

Every extension constructor receives a `KampGround` instance. It is a frozen snapshot — state reflects the moment the worker subprocess was spawned, not a live view.

#### Library

```python
ctx.library_tracks          # list[TrackMetadata] — full library snapshot
ctx.search("Madvillainy")   # list[TrackMetadata] — case-insensitive substring match
```

#### Network

```python
resp = ctx.fetch("https://api.example.com/lookup")
# resp.status_code: int
# resp.headers: dict[str, str]
# resp.body: bytes
```

Requires `"network.fetch"` in `kampground_permissions` and the hostname in `kampground_network_domains`. Requests to undeclared domains raise `PermissionError`.

> **Scope note:** `network.fetch` is an API gate, not a network sandbox. Extensions that call `requests`, `urllib`, or other libraries directly bypass this check. A subprocess-level network filter is tracked separately.

#### Library writes

Extensions never write to the database directly. Queue mutations through the context; the host applies them after the worker exits.

```python
# Update metadata fields
ctx.update_metadata(track.mbid, {"title": "Alright", "year": "2015"})

# Set artwork
ctx.set_artwork(track.mbid, ArtworkResult(image_bytes=b"...", mime_type="image/jpeg"))

# Deposit a file in the watch folder (syncers only)
ctx.stage("artist-album.zip", zip_bytes)
```

All three methods require `"library.write"` in `kampground_permissions`.

#### Events

```python
ctx.subscribe("track_start", lambda: print("Track started"))
ctx.subscribe("track_end", lambda: print("Track ended"))
ctx.subscribe("daemon_stop", lambda: print("Daemon shutting down"))
```

#### Playback snapshot

```python
ctx.playback.playing   # bool
ctx.playback.position  # float (seconds)
ctx.playback.duration  # float (seconds)
ctx.playback.volume    # int (0–100)
```

---

### Backend permissions

Declare permissions as class attributes on your extension class:

```python
class MyExtension(BaseTagger):
    kampground_permissions = ["network.fetch", "library.write"]
    kampground_network_domains = ["api.example.com", "img.example.com"]
```

The host reads these at discovery time — no extra config file required.

| Permission | Required for |
|---|---|
| `network.fetch` | `ctx.fetch()` |
| `library.write` | `ctx.update_metadata()`, `ctx.set_artwork()`, `ctx.stage()` |

> **Warning:** An extension that declares both `library.write` and `network.fetch` can read your library metadata and send it to an external server. kamp logs a warning when it discovers such an extension. Review carefully before granting access.

---

### Sandbox tiers

Backend extensions run in an OS-level sandboxed subprocess (macOS `sandbox_init`, Linux landlock/seccomp). Set `_sandbox_tier` on your class:

| Tier | Default for | Restrictions |
|---|---|---|
| `"minimal"` | Taggers, artwork sources | No filesystem writes outside `/dev`; no subprocess spawn; network allowed |
| `"syncer"` | Syncers | Writes restricted to watch folder and state directories; subprocess spawn permitted |

```python
class MyTagger(BaseTagger):
    _sandbox_tier = "minimal"   # this is the default; no need to set it explicitly
```

---

### pyproject.toml entry points

Declare your extension class under the `[tool.poetry.plugins."kamp.extensions"]` section (or the equivalent `[project.entry-points."kamp.extensions"]` for setuptools/Hatch):

```toml
[tool.poetry.plugins."kamp.extensions"]
my-tagger = "my_package.tagger:MyTagger"
my-artwork = "my_package.artwork:MyArtSource"
```

The key (e.g. `my-tagger`) is the entry point name used in logs. The value is a `module:ClassName` path.

The host validates the class at startup:
1. It must subclass `BaseTagger`, `BaseArtworkSource`, or `BaseSyncer`.
2. It must implement all abstract methods.
3. It is probed in a throwaway subprocess to confirm it can be imported safely.
4. Its installed files are hash-pinned on first encounter; any subsequent modification causes the extension to be rejected.

---

### Complete example (backend)

`my_tagger/tagger.py`:
```python
from kamp_daemon.ext.abc import BaseTagger
from kamp_daemon.ext.context import KampGround
from kamp_daemon.ext.types import TrackMetadata


class AcmeMetadataTagger(BaseTagger):
    """Resolves track metadata from the Acme Music API."""

    kampground_permissions = ["network.fetch"]
    kampground_network_domains = ["api.acme-music.example.com"]

    def __init__(self, ctx: KampGround) -> None:
        self._ctx = ctx

    def tag(self, track: TrackMetadata) -> TrackMetadata:
        resp = self._ctx.fetch(
            f"https://api.acme-music.example.com/lookup"
            f"?artist={track.artist}&title={track.title}"
        )
        if resp.status_code != 200:
            return track  # return unchanged on failure

        import json
        data = json.loads(resp.body)
        return TrackMetadata(
            title=data.get("title", track.title),
            artist=data.get("artist", track.artist),
            album=data.get("album", track.album),
            album_artist=data.get("album_artist", track.album_artist),
            year=data.get("year", track.year),
            track_number=data.get("track_number", track.track_number),
            mbid=data.get("mbid", track.mbid),
        )
```

`pyproject.toml`:
```toml
[tool.poetry.plugins."kamp.extensions"]
acme-metadata-tagger = "my_tagger.tagger:AcmeMetadataTagger"
```

---

### Publishing to PyPI

1. Declare the `kamp.extensions` entry point group as shown above.
2. `poetry publish` (or `python -m build && twine upload dist/*`).
3. Users install your package with `pip install kamp-acme-tagger` (or via the kamp preferences panel for frontend-installable backend extensions — not yet implemented).
4. The kamp daemon discovers the extension on next launch via `importlib.metadata.entry_points`.

---

## What extensions can and cannot do

### Frontend

| Can | Cannot |
|---|---|
| Add panels to any layout slot | Access `window.KampAPI` directly (community extensions only; sandboxed) |
| Call `api.player`, `api.library` etc. via the SDK | Contact external origins (blocked by iframe CSP) |
| Use any browser API (DOM, Canvas, WebGL, `requestAnimationFrame`, etc.) | Access `localStorage`, cookies, or IndexedDB |
| Render HTML/CSS/JS freely | Spawn popups or navigate the parent frame |
| Read/write declared settings | Write to the kamp library directly |

### Backend

| Can | Cannot |
|---|---|
| Resolve track metadata and return enriched `TrackMetadata` | Write audio files or the library database directly |
| Fetch album artwork and return image bytes | Access the filesystem outside the sandbox-permitted paths |
| Stage downloaded files for ingest | Spawn subprocesses (unless `_sandbox_tier = "syncer"`) |
| Make HTTP requests to declared domains via `ctx.fetch()` | Contact undeclared domains via the KampGround API |
| Subscribe to daemon lifecycle events | Read ambient host state (all state is a snapshot at spawn time) |
