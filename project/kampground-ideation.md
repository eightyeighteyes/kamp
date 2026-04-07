# kampground - The kamp extension system

## What is possible with kampground?

Ideally, anything that's supported by the database and the renderer — and that includes everything the built-in features do. Bandcamp sync, MusicBrainz tagging, and artwork fetching must all be buildable using the public `KampGround` API. This invariant keeps the SDK honest: the API surface is extracted from two real working extensions, not designed in the abstract first.

The extensions directory is watched; extensions reload on file change.

---

## Security Model

### Declarative permissions

Every extension — frontend and backend — declares the permissions it needs in its manifest. The host enforces them at load time; an extension that requests an undeclared capability is rejected.

**Frontend permissions:**
- `library.read` — query albums, tracks, and metadata
- `player.read` — observe playback state (current track, position, queue)
- `player.control` — issue playback commands (play, pause, seek, skip)
- `network.fetch` — fetch from external URLs (proxied through `KampAPI` in Phase 2 sandbox)
- `settings` — read and write the extension's own settings namespace

**Backend permissions:**
- `network.fetch` — make HTTP/HTTPS requests via `KampGround.fetch(url, method, body)`. The host makes the request; the extension never calls the network directly. The manifest must also declare an allowlist of permitted domains (`network.domains = ["api.discogs.com"]`); requests to unlisted domains are rejected.
- `audio.read` — receive raw audio data for the track being processed (e.g. for fingerprinting); the host mediates the read, the extension never receives a file path
- `library.write` — modify library metadata via a set of named atomic operations: `update_metadata(track_id, fields)`, `set_artwork(track_id, bytes)`. No bulk deletes, no raw SQL. Every write is logged to an append-only audit table (`extension_id`, `operation`, `old_value`, `new_value`, `timestamp`) enabling rollback of any extension's changes.

Permissions are shown to the user on install and can be reviewed at any time. The combination of `library.read` + `network.fetch` triggers elevated install-time language: *"This extension can read your music library and send data to external servers."* This applies to both frontend and backend extensions — scrobblers are a legitimate use case, but users should understand what they're approving.

### Backend: structured data contracts as the primary defense

Backend extension ABCs are designed so that extensions have no *need* for filesystem access. The host is responsible for reading and writing files; extensions receive and return structured data objects.

```python
class BaseTagger(ABC):
    def tag(self, track: TrackMetadata) -> TrackMetadata: ...

class BaseArtworkSource(ABC):
    def fetch(self, query: ArtworkQuery) -> ArtworkResult | None: ...
```

A `BaseTagger` that only transforms a `TrackMetadata` object never needs to open a file. If an extension legitimately needs audio data (e.g. acoustic fingerprinting), it declares the `audio.read` permission and receives the bytes through a controlled host method — not a path.

This is the first line of defense: *removing the need* for filesystem access, rather than trying to block it after the fact.

### Backend: import-time execution probe

Python entry points execute module-level code at import time — before any ABC conformance check, before any permission gate. A malicious `__init__.py` can exfiltrate data before `tag()` is ever called.

Before a backend extension is activated, kampground loads it once in a restricted subprocess where `socket`, `subprocess`, `os.system`, and `open` are stubbed to loggers. Any call to a stubbed symbol during import raises a load-time error and the extension is rejected. This is a heuristic (not a sandbox), but it catches the obvious pattern and costs nothing for legitimate extensions that only do initialization work.

### Backend: hash-pinning

At install time, kampground records a SHA-256 hash of each extension's installed files. On every subsequent load — including hot reloads triggered by the file watcher — the hash is verified before execution. A mismatch (e.g. from a post-install hook that swapped files after review) blocks the load and alerts the user.

### Backend: OS-level sandboxing (marketplace gate)

Structured data contracts and the import-time probe remove the incentive and catch the obvious cases, but don't prevent a determined malicious extension from calling `open()` directly. OS-level sandboxing enforces the boundary at the kernel level:

- **macOS:** `sandbox-exec` with a restrictive profile
- **Linux:** `landlock` + `seccomp`
- **Windows:** AppContainer / restricted token

**This is a release gate, not a future nice-to-have.** The public extension marketplace does not open until OS-level sandboxing ships on at least macOS and Linux. The subprocess isolation model already in place makes this straightforward to layer in.

A WASM runtime (wasmtime/wasmer) would provide the strongest cross-platform isolation, but requires extension authors to compile to WASM — too high a bar for v1.

### Frontend: Phase 1 / Phase 2 enforcement gap

"Phase 1 is first-party extensions" is currently a process gate, not a technical one. The `kamp-extension` npm keyword is uncontrolled — any published npm package can claim it and be discovered as a Phase 1 extension, with `contextBridge` access and no iframe isolation. Until the community marketplace opens, first-party extensions must be declared in a signed manifest or kamp-controlled allow-list, not just the npm keyword alone.

---

### Frontend Extensions

Frontend extensions can contribute anything the host renders: panels, alternative layouts, visualizers, themes, web views, and non-visual integrations like scrobblers or queue automation tools. The "view layer only" framing is a useful intuition pump but not a hard constraint.

Extensions access the player exclusively via `window.KampAPI`, injected by Electron's `contextBridge` in the preload script. Extensions never touch `ipcRenderer` or Node.js directly — `KampAPI` is the only surface.

**Phase 1 (first-party extensions):** `contextBridge` isolation is sufficient. Extensions ship as npm packages, discovered via the `kamp-extension` keyword in `package.json`, and declare contributed panels/components and required permissions in a manifest.

**Phase 2 (community extensions):** Rendered in `<iframe sandbox="allow-scripts">` communicating via `postMessage`. Strict CSP on the renderer window. Extensions that need to fetch from external URLs (e.g. a Bandcamp discovery feed) may need to proxy those requests through `KampAPI` rather than fetching directly, depending on CSP policy.

#### Examples

**creamy** — a hardware-accelerated music visualization plugin with beat detection and customizable presets

**crate** — a virtual record shelf that renders a user's collection as a crate to dig through

**bandcamp discover queue** — a new way to find your next favorite record: pulls recommendations from the store pages of albums you recently listened to and gives you a chance to preview them. Phase 2 extension; external fetches may need to go through `KampAPI`.

**glowup** — adds glow effects to the accents. Pure CSS theme; likely needs no `KampAPI` access, though it still declares a manifest entry so kampground can register and unload it cleanly.

---

### Backend Extensions

Backend extensions are anything that change the underlying data or engine features.

Each extension implements one or more abstract base classes (`BaseTagger`, `BaseArtworkSource`, etc.) that define the capability contract. The ABCs are designed so that extensions work entirely with structured data — they receive and return data objects; the host handles all file I/O. See the Security Model section for how this eliminates the need for filesystem access.

Extensions declare their required permissions in a `[tool.kampground]` table in `pyproject.toml`:

```toml
[tool.kampground]
permissions = ["network.fetch"]
```

Extensions are discovered via Python entry points:

```toml
[project.entry-points."kamp.extensions"]
my-tagger = "my_package:MyTagger"
```

Backend extensions run inside the existing spawn-context worker subprocesses. A crash quarantines the item being processed — not the daemon. Extension authors should treat their code as running in an isolated worker: don't assume shared state with the parent process, and don't assume global configuration (e.g. `musicbrainzngs.set_useragent`) has been applied.

#### Examples

**bandcamp-tagger** — resolves Bandcamp release metadata for purchases not in MusicBrainz

**discogs-artwork** — pulls high-resolution artwork from the Discogs API as a fallback source

**beets-compat** — maps kamp's `BaseTagger` interface to beets plugins so existing beets taggers work without modification

