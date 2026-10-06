# Developer Documentation

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) — check "Add to PATH" on Windows |
| Poetry | latest | `pip install poetry` or see [python-poetry.org](https://python-poetry.org/docs/#installation) |
| Node.js | 18+ (LTS) | [nodejs.org](https://nodejs.org/) — includes npm |
| mpv | latest | [mpv.io](https://mpv.io/) — must be on `PATH` for the player server to run |
| Git | any | [git-scm.com](https://git-scm.com/) |

## Bootstrap

```bash
# 1. Clone
git clone https://github.com/eightyeighteyes/kamp
cd kamp

# 2. Enable the pre-commit hook (black + mypy)
git config core.hooksPath .githooks

# 3. Install Python dependencies (creates a .venv in the project root)
poetry install

# 4. Install UI dependencies
cd kamp_ui
npm install
```

## Run in development

```bash
cd kamp_ui
npm run dev
```

`npm run dev` launches Electron and the Vite dev server, as well as the kamp server. Frontend changes are hotloaded. Server changes often require a restart.

```bash
cd kamp_ui
npm start
```

`npm start` builds and launches Electron and the Vite server, as well as the kamp server. Frontend changes are not hotloaded, but visual performance for things like meters and animations matches what users see.

## Diagnostics sampling

Set `KAMP_DIAGNOSTICS=1` to record per-process memory (and, on the Electron
side, CPU) to disk. It is off by default and costs nothing when unset.

```bash
cd kamp_ui
KAMP_DIAGNOSTICS=1 npm start
```

Three JSONL logs accumulate, one line per sample tick, date-stamped so a
multi-day capture does not land in a single unbounded file:

| File | Written by | Covers |
| --- | --- | --- |
| `memory-<date>.jsonl` | Python daemon | daemon, mpv, preview mpv |
| `electron-<date>.jsonl` | Electron main | main, renderer, GPU, utility processes |
| `renderer-<date>.jsonl` | renderer probe | window focus vs. running animations |

The daemon writes to `<state dir>/diagnostics/` (`~/.local/share/kamp` on
macOS/Linux, `%LOCALAPPDATA%\kamp` on Windows); Electron writes to
`diagnostics/` under its `userData` directory.

Memory is sampled every 60s and the renderer probe every 10s.

### Attributing Python memory growth

`KAMP_DIAGNOSTICS=1` says *which process* grew. To find out *what* inside the
daemon is holding the memory, add `KAMP_DIAGNOSTICS_TRACEMALLOC=1`:

```bash
cd kamp_ui
KAMP_DIAGNOSTICS=1 KAMP_DIAGNOSTICS_TRACEMALLOC=1 npm start
```

It is a separate switch because tracing intercepts every allocation and is not
free — fine for a diagnostic run, not for leaving on for days.

Each `memory-*.jsonl` tick then also carries `traced_current_bytes` and
`traced_peak_bytes`, and a second log `alloc-<date>.jsonl` records the largest
live allocation sites (first tick, then every 10th).

**Reading it — the comparison is the diagnosis**, not either number alone:

| RSS | `traced_current_bytes` | Meaning | Fix |
|---|---|---|---|
| high | high | live objects are retained | find and drop the reference; `alloc-*.jsonl` names the line |
| high | low | Python freed it, the allocator kept the pages | subprocess isolation (see `kamp_daemon/syncer.py`) |

That second row is the trap worth knowing about: a mechanism test can pass while
RSS stays put, which is exactly what happened with an earlier `sys.modules`
eviction attempt. Only the RSS curve flattening counts as fixed.

```bash
jq -r '"\(.t|strftime("%H:%M")) rss=\([.procs[]|select(.role=="daemon").rss_bytes]|add/1048576|floor) traced=\((.traced_current_bytes//0)/1048576|floor)"' \
  ~/.local/share/kamp/diagnostics/memory-*.jsonl
``` Totals per tick:

```bash
jq -r '"\(.t) \([.procs[].rss_bytes] | add)"' ~/.local/share/kamp/diagnostics/memory-*.jsonl
```

Three notes on reading these.

The daemon reports *current* RSS, not peak — peak only ever rises, so it cannot
show a leak that has been fixed.

`runningAnimations` in the renderer log is the number to watch while the window
is unfocused: it is the measurement that confirmed kamp does *not* keep CSS
animations running when it is not the active app (KAMP-704).

`appRafRequests` counts animation frames application code asked for in the
interval — 0 means nothing is driving frames and Chromium can idle, ~600 means a
rAF loop being served at 60fps, ~100 means one throttled to 10fps. It replaced an
earlier `rafTicks` field that counted the probe's *own* rAF loop, which measured
Chromium's frame cadence rather than the app's demand and kept the compositor
awake while doing it. Captures predating the rename carry `rafTicks` and are not
comparable.

## Tests and linting

```bash
# Run all tests (from repo root)
poetry run pytest

# Run a single test file without the coverage threshold check
poetry run pytest tests/test_server.py -v --no-cov

# Type check
poetry run mypy kamp_daemon/ kamp_core/

# Format
poetry run black kamp_daemon/ kamp_core/ tests/
```


## Frontend extensions

The Electron UI supports frontend extensions — npm packages that contribute panels to the nav bar.

**How it works:**

1. The main process scans `kamp_ui/extensions/` (first-party) and `node_modules/` (installed) for packages with `"kamp-extension"` in their `keywords`.
2. Each extension's entry point is an ES module that exports a `register(api)` function.
3. Extensions are classified into two security phases:
   - **Phase 1 (first-party):** listed in `kamp_ui/src/main/first-party-allowlist.json`; runs directly in the renderer with full `KampAPI` access. Reserved for kamp team extensions.
   - **Phase 2 (community):** all extensions installed by users; runs in a sandboxed `<iframe sandbox="allow-scripts">` with no access to the host DOM, localStorage, or contextBridge. Network access is restricted to the local kamp server. On first load, the user is shown a permission prompt listing the extension's declared capabilities.

**Managing extensions:**

Open **Preferences → Extensions** (Cmd+,) to view all installed extensions, enable/disable them, and configure per-extension settings. Community extensions can be installed by npm package name or by choosing a local directory. Installed extensions persist across app restarts. Removing an extension unloads it immediately and clears its stored permissions and settings.

**Writing an extension:**

```js
// package.json
{
  "name": "my-kamp-extension",
  "version": "1.0.0",
  "keywords": ["kamp-extension"],
  "main": "index.js",
  "kamp": {
    "permissions": ["player.read", "network.fetch"],
    "settings": [
      { "key": "refresh_interval", "label": "Refresh interval (s)", "type": "number", "default": 30 }
    ]
  }
}

// index.js
export function register(api) {
  api.panels.register({
    id: 'my-extension.my-panel',   // must be globally unique
    title: 'My Panel',
    defaultSlot: 'main',
    render(container) {
      container.textContent = 'Hello from my panel'
      // Return a cleanup function called on unmount:
      return () => {}
    }
  })
}
```

**Declared permissions** (`kamp.permissions`):

| Permission | Grants |
|---|---|
| `library.read` | Read library metadata via the REST API |
| `player.read` | Read playback state |
| `player.control` | Control playback (play, pause, skip, seek) |
| `network.fetch` | Make requests to external servers |
| `settings` | Read and write per-extension settings via `api.settings` |

Two reference extensions live in `extensions/` at the repo root:
- **[kamp-example-panel](extensions/kamp-example-panel/)** — minimal boilerplate; start here when building your first extension.
- **[kamp-groover](extensions/kamp-groover/)** — a complete community extension with annotated SDK usage covering `player.read`, `library.read`, subscriptions, and cleanup. Also available on npm: `npm install kamp-groover`.

### Backend extensions

The daemon supports Python backend extensions for custom tagging and artwork sources. Extensions are Python packages that declare an entry point in the `kamp.extensions` group.

**How it works:**

1. On server startup, the daemon calls `discover_extensions()` which loads all installed packages that declare a `kamp.extensions` entry point.
2. Each class is validated against `BaseTagger` or `BaseArtworkSource` ABC conformance.
3. At ingest time, after a staging item is processed and new tracks are indexed by `LibraryScanner`, the host invokes registered extensions on each new track.
4. The host enforces a single-invocation guarantee: each extension is offered each track at most once, using the `extension_audit_log` table to detect prior runs. Re-scans never trigger re-invocation.

**Invocation policy:**
- Tagger extensions run first (in registration order), then artwork sources.
- Tracks without a resolved `mb_recording_id` are skipped.
- A failed extension (crash, exception, or invalid mutation) is logged and skipped; remaining extensions and tracks continue normally.
- Extensions cannot be re-processed automatically on version update; use `kamp rollback <extension_id>` to revert mutations and allow re-processing on the next ingest.

**Writing a backend tagger:**

```python
# pyproject.toml
[project.entry-points."kamp.extensions"]
my_tagger = "my_package:MyTagger"

# my_package/__init__.py
from kamp_daemon.ext import BaseTagger
from kamp_daemon.ext.types import TrackMetadata

class MyTagger(BaseTagger):
    kampground_permissions = ["network.fetch"]
    kampground_network_domains = ["api.example.com"]

    def tag(self, track: TrackMetadata) -> TrackMetadata:
        # Enrich track metadata, then queue the result via KampGround:
        self._ctx.library.update_metadata(track.mbid, {"title": track.title.strip()})
        return track
```

**Audit log and rollback:**

Every mutation is logged to `extension_audit_log`. To revert all changes by an extension:

```bash
kamp rollback <extension_id>   # e.g. kamp rollback my_package.MyTagger
```
