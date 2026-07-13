# Language
- Python 3
- Poetry for dependency management & packaging

# Code Style
- Follow PEP8 and use black for formatting
- Use typing declarations
- Write meaningful behavioral tests
- The API should be expressive

# Workflow
- After cloning, run `git config core.hooksPath .githooks` to enable the pre-commit hook (black + mypy).
- Start work with a new branch created from a clean, updated main branch. Do not create files before creating a new branch.
- One fix or feature per branch / PR.
- Use red/green TDD
- Before opening a PR, run all CI steps (testing, linting, type checks, etc) locally
- Before opening a PR, scan through README.md to make sure it's still valid (nothing it says has drifted from what the application does)
- When merging a branch, squash commits.
- Task lifecycle: task management is done through Jira. When work begins on a task, move it to In Progress. When the user confirms it is tested and complete, squash merge the PR and move the task to Done.
- Prefer running single tests, and not the whole test suite, for performance; use `--no-cov` when running a single file to skip the coverage threshold check (e.g. `poetry run pytest tests/test_foo.py -v --no-cov`)
- Update documentation (README.md) after new features are validated
- Document rationale in comments: succinctly explain *why* decisions are made
- After completing a feature, before closing a pull request, retrospect about the development experience and update claude.md with lessons learned.

## Coverage
`fail_under` in `pyproject.toml` is set to **93%**. Keep actual coverage at or above **94%** — the gap exists to accommodate OS-specific branches (case-only renames, format-specific tag writes) that are legitimately untestable in CI. Do not let coverage slip below 94% in practice; do not chase the last few platform-only lines.

# Agents
- When designing a plan, query your available agents and interrogate all relevant agents for enhancements to your plan.

# Lessons Learned
## Dropping a constrained column from `tracks` (FK-aware SQLite rebuild) — KAMP-552
Dropping `file_path`/`sale_item_id` (UNIQUE / FK / indexed) can't use `ALTER TABLE DROP COLUMN` (that only works for unconstrained columns, as KAMP-539 did). Use a full table rebuild, and reuse this recipe for any future constrained-column drop on a table with children:
- **`DROP TABLE tracks` cascade-deletes children under `foreign_keys=ON`** (`track_sources`/`track_stats`/`playlist_tracks` are `ON DELETE CASCADE`). `PRAGMA foreign_keys` is a **no-op inside a transaction**, so: `commit()` to autocommit → `PRAGMA foreign_keys=OFF` → **read it back and assert it's 0** → `BEGIN`. Toggle back ON in `finally` after COMMIT/ROLLBACK. Assert child-row COUNTs are unchanged across the rebuild.
- **Copy `id` verbatim** in `INSERT … SELECT` so inbound child FKs (and FTS rowids = tracks.id) stay valid. Build the copied-column list from the **intersection** of the new schema and the old table's `PRAGMA table_info` so a partial/synthetic schema survives (missing cols take their DEFAULTs).
- **Bump `schema_version` INSIDE the rebuild transaction** (differs from the file's usual separate-`commit()` style) so a crash rolls back atomically to the prior version and retries; gate on `if version == N` and short-circuit with a `if "<col>" not in PRAGMA table_info` presence check.
- `PRAGMA foreign_key_check` **returns rows, it does not raise** — inspect and repair (e.g. null a dangling FK) or `ROLLBACK`. Backup-first via `_backup_db` **before** the FK-off/BEGIN.
- **Keep dropped columns readable by re-deriving them in the `tracks_with_stats` view** (`file_path` = preferred `track_sources.uri`, `sale_item_id` = `provider_item_id`, COALESCE to a safe default). This keeps `_row_to_track` and every `SELECT *` consumer unchanged. But the preferred-source pick is availability-ordered and thus **read-time-unstable** — never use the derived value as a lookup/join key; resolve identity on `track_sources.uri` (any kind) directly, and have stat/lookup helpers return `track_id: int`, not a path string.
- **Test-migration gotcha:** synthetic old-version DBs built from the *current* fresh schema lack the historical columns. For `LibraryIndex`-based tests, `_readd_legacy_track_columns` re-adds them (as plain nullable `TEXT` — `ALTER` can't re-add `NOT NULL UNIQUE`); for raw-`sqlite3` migration tests, `ALTER TABLE … ADD COLUMN` the columns the target migration keys on (and the pre-v49 per-source columns the v45 backfill reads). Direct `INSERT INTO tracks (file_path,…)` fixtures must switch to `INSERT … DEFAULT VALUES` + a `track_sources` row, or resolve ids via `lastrowid`. Stat-seeding writes (`UPDATE tracks SET favorite/play_count`) must target `track_stats` by resolved `track_id` (the view reads stats from there, shadowing the legacy tracks columns).

## Removing the album-granularity `file_path` (missing-album key) — KAMP-554
The second, independent role of `file_path`: the stable key for `missing_album` virtual cards (tracks with an empty `album` tag, so `(album_artist, album)` isn't unique). Lessons:
- **Read the SQL before treating a "design question" as open.** The estimation flagged "does a multi-track missing album need a distinct album key?" — but the query builds missing-album entries as `SELECT … WHERE t.album = ''` with `track_count = 1` and `missing_track_id = t.id`. A missing album is **always exactly one track**, so `track_id` is a complete, uniform drop-in and there is no multi-track case. The open question dissolved on reading `LibraryIndex.albums`.
- **`get_album_art`'s `file_path` param was dual-purpose, not bandcamp-only.** Now-playing/queue art callers passed `trackUri(track)` — a **local file path** for local tracks (not just a `bandcamp:` uri) — which resolved via `get_track_by_path`. Both those callers already hold a full track with an `id`, so routing them through a new `track_id` param let the endpoint resolve the track and fall through to the existing remote-proxy tail (`remote_tracks[0].file_path`) for bandcamp. That eliminated the path param entirely and made `_validate_library_path` dead code — delete the function and its path-traversal tests **together** so coverage doesn't strand. Removing the client-supplied path param is also a net security win (no more traversal surface).
- **`queue_play_album_next` never got the `id` branch KAMP-537 added to its siblings.** When migrating a family of handlers (play / add-album / play-album-next / insert-album), diff them against each other — one had only the `file_path` path and would have silently lost missing-album support.
- **UI: `artUrl` had two caller shapes** (missing-album `album.file_path` vs bandcamp `trackUri`). An options object `artUrl(artist, album, { trackId?, version? })` disambiguates cleanly; enumerate the ~15 call sites and every `album.file_path` read by grep, not memory (kamp_ui has no unit-test runner, so a stale key silently becomes `undefined`). The `text/kamp-album` drag payload is a wire contract — its one producer and three QueuePanel consumers must change atomically.
- **Never `git stash` to get a baseline mid-change.** A `git stash && pytest && git stash pop` one-liner had the 2-min pytest time out (SIGTERM 143) **before** the `pop`, stranding all 37 modified files in a stash. Use `git worktree add <tmp> main` for an isolated baseline run instead — it can't touch the working tree. (This branch improved coverage 93.12%→93.27%; main was already sub-94% post-552, so don't chase it here.)

## Mutagen
- **Never use `mutagen.File(path)` for MP3s** — use `mutagen.id3.ID3(str(path))` directly. `mutagen.File` parses the full MPEG audio stream and raises on files with minimal/fake audio data (common in tests).
- `ID3NoHeaderError` and frame classes (`TPE1`, `TALB`, etc.) exist at runtime but not in type stubs — use `except Exception:` not `except id3.ID3NoHeaderError:`.
- APIC frames are keyed `"APIC:Cover"`, not `"APIC:"` — check with `any(k.startswith("APIC") for k in tags)`.
- `mp4.tags.get(key)` returns `None | value` — always check for None before indexing.

## Testing
- **Fake MP3 files:** write `b"\xff\xfb" * 64` then `id3.ID3().save(str(path))` — valid ID3 header without valid MPEG frames.
- **Fake M4A files:** write `b"\x00" * 32` and patch `mutagen.mp4.MP4` — real MP4 containers aren't needed.
- **Patching `Path.stat` in Python 3.12:** `patch.object(path_instance, "stat", ...)` fails (C-implemented method). Patch at class level: `patch("pathlib.Path.stat", fn)` where `fn` takes `self` as first arg.
- **QueueHandler re-emission loop:** When a subprocess worker adds a `QueueHandler` to the root logger and an inline test helper runs the worker in-process, the handler stays attached. If `_replay_log_queue` re-emits records via the logger hierarchy, those records loop back through the root's QueueHandler indefinitely. Fix: remove the QueueHandler in the worker's `finally` block so it's gone before replay runs.

## Subprocess isolation
- Any global state set in the parent process (e.g. `musicbrainzngs.set_useragent`) is NOT inherited by `spawn`-mode subprocesses — re-apply it inside the worker function.
- Worker functions must clean up any handlers/state they add to shared objects (logging, signals) so the parent process is unaffected when the worker runs inline in tests.

## Skip/optimization logic
Before implementing "skip if already done," define precisely what *correct* means for the skip condition. "Present ≠ best available" — skipping based on presence alone can degrade quality (e.g., skipping artwork fetch because art is embedded, when embedded art is lower quality than what the Archive would return). Validate skip conditions against the regression case explicitly.

## Memory optimization
When the goal is a runtime property (memory released, latency reduced), write a test that measures that property *before* implementing the mechanism. Mechanism tests (e.g. "modules removed from sys.modules") can pass while the property test fails — this is exactly what happened with the `sys.modules` eviction approach, which passed all tests but left pymalloc-held pages resident. A property test (measuring process RSS before and after) would have caught this immediately and driven the correct approach (subprocess isolation) from the start.

## macOS system integration
Budget at least a Side for any feature touching osacompile, Spotlight registration, or macOS app bundles. Corporate MDM/EDR (Falcon, Jamf) can silently block registration in ways that are hard to diagnose.

## Bundling binaries in the .app
**Never copy the Homebrew node binary into the app bundle.** Homebrew node links against Homebrew-specific dylibs (`libnode.dylib`, `llhttp`, `libuv`, `ada-url`, `simdjson`, `brotli`, `c-ares`) that are absent on clean machines. Always download from nodejs.org — the official tarball's `bin/node` only links against macOS system libraries (`CoreFoundation`, `libSystem`, `libc++`). Same caution applies to any Homebrew binary: run `otool -L <binary>` and verify all deps are under `/usr/lib/` or `/System/`.

## Sandboxed iframes (community extensions)

- **srcdoc iframes inherit the parent document's CSP** — even if the srcdoc has its own `<meta http-equiv=Content-Security-Policy>`, the parent's CSP overrides script-src. To allow an inline script, hash-whitelist it in the parent CSP (`'sha256-...'` in `src/renderer/index.html`). Recompute the hash every time the shim changes: `node -e "const s='...';console.log('sha256-'+require('crypto').createHash('sha256').update(s,'utf8').digest('base64'))"`.
- **Any change to `SANDBOX_SHIM` in `SandboxedExtensionLoader.tsx` requires updating the sha256 hash in `src/renderer/index.html`** — including seemingly unrelated changes like port numbers embedded in the shim string. A stale hash causes the shim to be silently blocked: extensions install but panels never appear.
- **Sandboxed iframes without allow-same-origin reload on DOM move** — Chromium treats them as cross-origin and navigates on reparent. The holding-area/move strategy doesn't work. Create a fresh iframe on each panel mount instead.
- **Race condition: send init and mount in the same onLoad callback** — `kamp:init` triggers an async `import()`; `kamp:panel-mount` arrives before the import resolves, so `r[panelId]` is empty and mount silently no-ops. Fix: buffer the pending mount id and fire it inside `panels.register()` if it arrived early.
- **iframe CSP needs explicit img-src** — `default-src 'none'` blocks image loads from the kamp server. Add `img-src http://127.0.0.1:47483` to the srcdoc CSP.

## macOS notifications
`NSUserNotificationCenter` (used by `rumps.notification()`) is a no-op on macOS 14+. Use `UNUserNotificationCenter` instead. It requires `CFBundleIdentifier` — embed it in `launcher/main.c` via `__TEXT,__info_plist`. Without the compiled launcher (e.g. dev venv), `UNUserNotificationCenter.currentNotificationCenter()` crashes; wrap it in `try/except` and fall back to `rumps.notification()`.

## Scope discipline
If the same sub-problem fails twice in a row, stop and check in before attempting a third approach. Two failures signal a wrong level of abstraction or an environment constraint — not a fixable bug. This applies especially to test fixtures and dev-environment workarounds, which have no user value on their own.

**Concrete example (TASK-9 media keys):** next/prev media keys failed six times across multiple sessions because each attempt looked like a fixable bug. The real constraint — `MPRemoteCommandCenter` requires a CFRunLoop on the main thread; asyncio/uvicorn does not run one — was architectural and unfixable by implementation tweaks. Stopping after the second failure, diagnosing the root cause, and creating a task would have saved a full week of token spend. When a sub-problem fails twice: write up what was tried, name the constraint, create a backlog task, and move on.

## Cloudflare TLS fingerprinting
PyInstaller bundles its own OpenSSL, which has a different JA3/JA4 TLS fingerprint than a real browser. Cloudflare detects this and serves JS challenge pages (HTTP 200, ~3 KB HTML body starting with `<!DOCTYPE html>` and a path under `/_fs-ch-...`) instead of the expected response — **even for authenticated JSON API endpoints**, not just HTML page loads.

The set of environments that trigger the challenge is broader than originally believed:

- **PyInstaller bundle on any OS** — always affected (KAMP-127).
- **Windows dev** — also affected (KAMP-290). The Python 3.11 `.venv` on Windows links against a Python-bundled OpenSSL whose fingerprint Cloudflare flags, just like the PyInstaller version.
- **macOS dev** — not affected. The system Python on macOS uses SecureTransport / LibreSSL, which Cloudflare accepts.
- **Linux dev** — not affected (at the time of writing; system OpenSSL is generally OK).

The only reliable fix for any `bandcamp.com` request in an affected environment is to route through Electron's `net` module (Chromium's network stack), which has a real browser TLS fingerprint and pulls fresh cookies from the daemon on every call. The gate in `kamp_daemon/bandcamp.py::_needs_proxy_session` is `_is_frozen() or sys.platform == "win32"`. Do not attempt to fix this by changing User-Agent, tweaking cipher suites, or using a different requests library — the check is at the TLS layer before HTTP headers are read.

## Data Protection Keychain and PyInstaller binaries
`keychain-access-groups` in a hardened-runtime entitlements file causes macOS to SIGKILL the binary at exec time (before dyld runs) when the binary has no bound `Info.plist` (`Info.plist=not bound` in `codesign -d`). PyInstaller onedir executables are standalone Mach-O files — they have no bundle identity, so macOS cannot validate the entitlement. The binary dies with `EXC_CRASH (SIGKILL - Code Signature Invalid)` and `codeSigningID: ""` in the crash report, even though `codesign --verify` says "valid on disk". Do not add `keychain-access-groups` to standalone (non-bundle) binary entitlements. DPC requires either a compiled launcher with a bound `Info.plist`, or an `--identifier` and `--entitlements` pair where the identifier matches a real bundle ID registered with the team. The DPC code path in `macos_keychain.py` is correct and will activate automatically once the binary has a proper bundle identity; for now it falls back to Login Keychain on `errSecMissingEntitlement`.

## macOS CFRunLoop constraint
Any macOS API that dispatches callbacks on the main GCD queue (`dispatch_get_main_queue()`) will not work in the kamp Python server process. The main thread runs asyncio/uvicorn, which does not pump a CFRunLoop. Affected APIs include: `MPRemoteCommandCenter`, `NSDistributedNotificationCenter`, `NSTimer`, and any delegate/target-action pattern that assumes an AppKit main loop. Features requiring these APIs must live in the Electron main process (which has a real CFRunLoop) or in a dedicated helper subprocess.

## Bandcamp CDN downloads (popplers5)
`popplers5.bandcamp.com` requires valid Bandcamp session cookies to serve a ZIP. Without cookies it returns HTTP 200 with an HTML error page.

- **Dev mode:** pass the authenticated `requests.Session` directly to `_download_file`. The session carries cookies; `requests` follows any redirect automatically. Do not attempt an "activate then download cookieless" pattern — it is intermittent and unreliable.
- **Frozen mode:** the `requests.Session` has a PyInstaller OpenSSL fingerprint Cloudflare blocks (see above). Route through Electron's proxy: call `_resolve_cdn_redirect(cdn_url, _ProxySession)` to follow the popplers5 → bcbits.com redirect via `net.fetch`, then download from the bcbits.com pre-signed URL with a plain cookieless `requests.Session` (bcbits.com URLs are time-limited tokens that do not need cookies).

## Diagnosis discipline: ask before assuming
Before proposing or implementing a fix, verify the actual failure mode from logs or a direct question. In TASK-173 multiple sessions were spent fixing things that were not broken (downloads, onboarding completion, the watch-folder/library wiring) because the diagnosis was assumed rather than confirmed. The cost: rewrites of working components, regressions introduced and then reverted, and a much longer path to the real two-line fix.

**Rule:** if you cannot point to a specific log line, error message, or user-confirmed observation that proves X is broken, do not fix X. Ask instead.

## Daemon runtime config and closure variables
Variables captured at daemon startup (e.g. `lib_path`, `lib_watcher`) are NOT automatically updated when the user changes config at runtime (e.g. during onboarding). The callbacks wired to `on_library_path_set` etc. only write to the DB by default. To propagate a runtime config change through the daemon:
- Use `nonlocal` in the callback to reassign the closure variable.
- Stop and restart any dependent objects (e.g. `LibraryWatcher`) that were initialized with the old value.
- Also call `core.reload(Config.load(index))` if the change should reach `DaemonCore` (see `_on_config_set` for the existing pattern).

## UI config refresh after config-changing events
The server does NOT push config changes over WebSocket. After any event that changes server-side config (Bandcamp login, library path set, etc.), the UI store must explicitly call `loadConfig()` to pick up the new state. Without this, fields like `bandcamp.connected` stay stale from the initial mount, hiding UI elements that depend on them (e.g. the sync button).

## Building mpv from source in CI

When building mpv from source, use the latest release tag (v0.41.0 as of 2026-04) — older tags are incompatible with Homebrew's current FFmpeg (8.x removed `FF_PROFILE_*` constants and `av_format_inject_global_side_data`).

- **libplacebo is a hard required dep** in v0.41.0 — there is no meson `-Dlibplacebo=disabled` flag. Install it via `brew install libplacebo`; it pulls in shaderc and vulkan-loader transitively.
- **sdl2 split into three options** in v0.41.0: `-Dsdl2-audio=disabled -Dsdl2-video=disabled -Dsdl2-gamepad=disabled` (the old `-Dsdl2=disabled` errors out).
- **PulseAudio option is `pulse`**, not `pulseaudio`.
- **Iterate meson flag errors locally**, not in CI: clone mpv, run `meson setup`, check `meson.options` for valid option names, build with ninja, then run dylibbundler to verify final dylib count before pushing.
- With the minimal flag set (audio-only), expect ~32 bundled dylibs (down from ~47 with Homebrew mpv). The video encoder libs (x264/x265/SVT-AV1/vmaf) come from Homebrew FFmpeg's transitive deps and cannot be avoided without building FFmpeg from source.
- **Lua must stay ENABLED (`-Dlua=enabled`), do not "disable all scripting".** This reverses the original TASK-185 audio-only stance. Pause/stop/resume are not issued from Python anymore — `engine.pause()/resume()/stop()` only send `script-message`s that are handled entirely inside `kamp_core/kamp_fade.lua`, loaded via `--script=`. A Lua-less mpv loads no script, so the script-messages are silent no-ops and the transport controls are dead in the packaged app (dev still works because system/scoop/Homebrew mpv ship luajit). This shipped broken in KAMP-508 → fixed in KAMP-519. Build deps: `brew install luajit` (macOS) / `mingw-w64-x86_64-luajit` (Windows). Bundling: macOS dylibbundler carries `libluajit-*.dylib` automatically (the build now *asserts* it is present and that vapoursynth is *not*); on Windows the `ldd` walk carries `lua51.dll` (the build asserts it landed in mpv.exe's import table — that presence is the authoritative "built with Lua" proof). VapourSynth must still be disabled — it embeds an unbundleable Python.framework, which was the real reason for the audio-only source build, not Lua.

### Verifying mpv was built with Lua — what does NOT work (KAMP-519 follow-up)
Three separate dead ends cost three CI rounds; do not repeat them:
- **`mpv --version` does NOT enumerate scripting backends.** Its output is only the mpv / libplacebo / FFmpeg version banners — there is no `lua`/`luajit` line to grep for, on any platform. Any `mpv --version | grep lua` assertion is doomed by construction. The authoritative check is the linked-DLL/dylib: `lua51.dll` in mpv.exe's import table (Windows) or `libluajit-*.dylib` among dylibbundler's output (macOS). mpv links these only when `-Dlua=enabled` and nothing else in the bundle pulls them in, so presence ⟺ Lua enabled.
- **`mpv.exe` on Windows is the GUI-subsystem front-end — it writes NOTHING to stdout/stderr** (no console attached), so `mpv.exe --version` captures empty no matter how you merge streams (`2>&1` is futile). meson also builds `mpv.com`, the console-subsystem twin, which prints normally. The app spawns `mpv.exe` headlessly (correct), but any CI step that needs to read mpv's stdout must invoke `mpv.com`. Both load the same sibling-DLL set, so `mpv.com --version`'s exit code still validates the staged DLL layout.
- **The Lua script file itself must be bundled into the PyInstaller app.** `playback.py` loads `kamp_core/kamp_fade.lua` via `--script=Path(__file__).parent/"kamp_fade.lua"`. `collect_submodules("kamp_core")` gathers only `.py` modules, so the `.lua` sibling was missing from the frozen bundle — `--script=<nonexistent>` makes mpv silently load no script, the `kamp-pause/resume/stop` handlers never register, and transport controls are no-ops in the packaged app while working in dev. Staged explicitly in `kamp.spec` `datas` as `("kamp_core/kamp_fade.lua", "kamp_core")`. mpv silently ignores a missing `--script` path, so `_start_mpv` now logs a warning if `_FADE_SCRIPT` is absent at launch.
- **lavfi filter syntax uses `%N%` escaping for `=` in graph values.** `--af=lavfi=ebur128=metadata=1` crashes mpv because the parser treats `ebur128` as a lavfi sub-option name. The correct form is `--af=lavfi=graph=%N%<filtergraph>` where `%N%` is mpv's percent-encoding (N = byte length of the value). Example: `ebur128=metadata=1` is 18 chars → `--af=lavfi=graph=%18%ebur128=metadata=1`. Without this, the daemon crashes on startup with "Error parsing option ebur128 (option not found)".

### Windows (MSYS2 + mingw-w64) extras
The macOS audio-only meson flag set is necessary but not sufficient on Windows. The Windows-specific video pipeline (Direct3D 9, D3D11, OpenGL, VAAPI, Caca, EGL/ANGLE) is auto-enabled by default and `video/vaapi.c` indirectly includes `video/out/gpu/d3d11_helpers.h`, which redefines `DXGI_DEBUG_D3D11` against newer mingw-w64 (`d3d11sdklayers.h`) — clean compile failure even though we don't want any video output. Add **all of these** to the Windows meson invocation: `-Dgl=disabled -Dgl-win32=disabled -Dgl-dxinterop=disabled -Dd3d11=disabled -Ddirect3d=disabled -Dd3d-hwaccel=disabled -Dd3d9-hwaccel=disabled -Dvaapi=disabled -Dvaapi-win32=disabled -Dvdpau=disabled -Degl-angle=disabled -Degl-angle-lib=disabled -Degl-angle-win32=disabled -Dcaca=disabled`. `dxva2` is **not** a valid option (do not add); `gl-dxinterop-d3d9` and `zimg-st428` are sub-features that cascade. After the build, the Windows analog of `otool -L` is MSYS2's `ldd` — copy every transitive `/mingw64/bin/*.dll` into the same directory as `mpv.exe` (Windows resolves DLLs from the executable's directory, no `@executable_path` rewriting needed).

## GitHub Actions: upload-artifact LCA stripping

`actions/upload-artifact@v4+` (v7 same generation) strips the **least common ancestor** of all `path:` entries from archive paths. A single-path upload preserves the path-relative-to-LCA layout fine, but multi-path uploads collapse to the deepest shared parent and the `kamp_ui/` (or other) prefix disappears from the archive.

- Symptom: `download-artifact` (with no `path:`) extracts to `$GITHUB_WORKSPACE`, files land in the "wrong" directory, and downstream tools (`electron-builder`, scripts assuming a known layout) silently miss them.
- Especially dangerous with `electron-builder`: missing `extraResources` sources log `file source doesn't exist from=...` but the build **still exits 0**, producing a silently-broken installer with a green CI conclusion.
- Fix: either upload a **single path** (matches the macOS `bundle-${arch}` pattern in this repo), or set the download step's `path:` to the LCA so the archive layout restores correctly.
- Defense in depth: any `package`-style job that consumes a bundle artifact should run a **pre-flight `Test-Path`/`test -f` check** for the required source files and fail loudly if any are missing. Don't rely on the packager to surface the problem.

## PowerShell file encoding (Windows codepage trap)

PowerShell on Windows reads `.ps1` and inline-script files via the system codepage (CP1252) by default, **not** UTF-8. Em-dashes (`—`, U+2014), curly quotes, and other non-ASCII text in `.ps1` sources or in YAML inline scripts that target `pwsh` get reinterpreted byte-by-byte and break the parser ("Unexpected token", "string is missing the terminator"). Use ASCII `--` and straight quotes in PowerShell sources and in any `run:` block on `windows-latest`. AST-parse new `.ps1` files locally before pushing: `[System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errs)`.

## Runner-image fragility (windows-latest)

Don't assume tools are preinstalled on `windows-latest` without checking the [runner-images](https://github.com/actions/runner-images) repo for the current image. Inkscape was on the image historically and is **not** at the time of writing — assuming `C:\Program Files\Inkscape\bin\inkscape.exe` works is a CI-only failure that won't surface in any local check. Prefer tools that are installable via MSYS2 pacman (`mingw-w64-x86_64-librsvg` for SVG → PNG) or that ship with the Python/Node toolchain we already require, both because they're version-pinnable and because they survive runner-image churn.

## Cross-platform bundled-asset filename divergence

When bundled binaries land at different on-disk names per platform (e.g. `<resourcesPath>/node` on macOS vs `<resourcesPath>\node.exe` on Windows, per `electron-builder.yml` `extraResources` and `scripts/fetch_node.*`), every runtime consumer that constructs a path to those assets needs a platform branch. `child_process.execFile` with an absolute path on Windows does **not** auto-resolve `.exe` via PATHEXT — that only applies when the executable is on PATH and resolved via shell. With `shell: false` (the `execFile` default) and an absolute path, the explicit `.exe` suffix is required.

KAMP-287 shipped a Windows installer where `bundledNodeAndNpm()` in `kamp_ui/src/main/extensions.ts` looked for `<resourcesPath>\node`, returned null, and silently fell back to a bare `npm` on PATH — breaking community-extension install on any machine without Node preinstalled. The bug went unnoticed because `kamp_ui` has no unit-test runner; nothing exercised the bundled path during build. When adding any new platform to the packaging matrix, audit every `process.resourcesPath` / `path.join` site that names a bundled binary.

## Verifying packaged Windows behavior without a clean machine

Windows Sandbox is the ideal clean-Windows sim but requires Win11 Pro/Enterprise/Education — **not available on Win11 Home**. On Home, the cheapest realistic substitute for testing `process.resourcesPath`-style code paths is to strip Node (or whatever bundled binary is being tested) from `$env:PATH` in the shell you launch the app from, then run the installed `.exe` from that same shell:

```powershell
$env:PATH = ($env:PATH -split ';' | Where-Object { $_ -notmatch 'nodejs|npm' }) -join ';'
Get-Command npm  # must error before launching
& "$env:LOCALAPPDATA\Programs\Kamp\Kamp.exe"
```

This forces the bundled-path branch because the system fallback is unreachable. Equivalent to a clean machine for the code under test; differs only in environmental cruft (registry/user keys) that doesn't reach `execFile`. Pair with the CI-built artifact from `workflow_dispatch` on `build-app.yml` if local Electron builds fail.

## Windows SMTC (System Media Transport Controls)

Lessons from KAMP-56 (Rust helper that owns SMTC so media keys route to Kamp and the Action Center Now Playing widget shows the current track):

- **`GetForWindow` rejects `HWND_MESSAGE`.** `ISystemMediaTransportControlsInterop::GetForWindow` returns `HRESULT 0x80070057` (`E_INVALIDARG`) when handed a message-only window — Microsoft's docs say "pass the HWND of your application's main window," and that's literal. Use a real top-level window (parent `None`); it can be invisible without ever calling `ShowWindow`. Style combo for an out-of-the-way utility window: `WS_POPUP` (no border / title bar) + `WS_EX_TOOLWINDOW` (excluded from Alt+Tab and taskbar) + `WS_EX_NOACTIVATE` (never steals focus).
- **AUMID is per-process, not inherited.** Subprocesses do *not* inherit the parent's `AppUserModelID`. Without an AUMID, SMTC's Now Playing widget shows "unknown app" instead of the bundled name + icon. Every helper that touches Shell-integrated APIs (SMTC, jump lists, toast notifications) must call `SetCurrentProcessExplicitAppUserModelID(L"<appid>")` early — before any WinRT init — with the same value as `electron-builder.yml`'s `appId` and Electron main's `setAppUserModelId(...)`. The NSIS installer's Start menu shortcut registers that AUMID with the Shell, which is what makes the lookup resolve.
- **SMTC cannot be smoke-tested in CI.** GitHub Actions `windows-latest` runners execute in non-interactive Session 0, which has no Shell. `GetForWindow` returns `E_INVALIDARG` even on a well-formed binary, indistinguishable from a real bug. Don't try to wire a spawn-and-exit smoke test into `build-native.ps1` — rely on the bundle-layout pre-flight to confirm the artifact exists, and verify end-to-end on a real desktop session.
- **Packaged Electron has no visible stderr — capture it.** A packaged installer doesn't ship a console, so the helper's `eprintln!`/`console.error` lines vanish unless the user relaunches from PowerShell (`& "$env:LOCALAPPDATA\Programs\Kamp\Kamp.exe"`). For any subprocess helper, spawn with `stdio: ['pipe','pipe','pipe']` and tee `child.stderr` via `createWriteStream` to a per-launch log at `%LOCALAPPDATA%\kamp\<name>.log` (truncate-on-spawn so the file always reflects the most recent run). Pair with stage markers in the helper itself (`eprintln!("[helper] init: GetForWindow")`) so a stray HRESULT lands on a known phase rather than a bare "parameter is incorrect."

## Python regex pitfall: backslash escapes in re.subn replacement

`re.sub`/`re.subn` interpret backslash escapes in the **replacement string** as regex backreferences. When rewriting code that contains `\xNN` byte literals (e.g. `b"\x00\x18..."` for AcoustID encoded keys), passing the literal as the replacement raises `re.error: bad escape \x at position N`. Use the callback form: `pattern.subn(lambda _m: replacement, src, count=1)` — callbacks bypass backreference interpretation.

## CSS height animations in Electron

`grid-template-rows: 0fr → 1fr`, `max-height`, JS-measured `scrollHeight`, and `scaleY` + `max-height` combos all produce inconsistent or jerky results in the Electron renderer. Do not attempt to animate `height` or `max-height` for show/hide transitions. Use `display: none` / `display: block` (instant toggle) instead. If a smooth reveal is ever required, reach for a JS animation library (e.g. Framer Motion) rather than CSS property animation.

## Shared debounce and high-volume FSEvents
`_LibraryHandler._schedule()` (in `watcher.py`) is shared between FSEvents from the library directory and explicit `trigger_scan()` calls. During a large batch sync, continuous FSEvents from files being moved in reset the debounce timer faster than the `_MAX_SETTLE_SECONDS` cap fires. To guarantee a scan fires after each pipeline completion, bypass the debounce entirely: call `_on_library_change` directly in a `threading.Thread` from `on_pipeline_complete` instead of routing through `lib_watcher.trigger_scan()`.

## Windows credential storage
The `keyring` package's `WinVaultKeyring` backend (Windows Credential Manager) caps each credential at **2560 bytes** (`CRED_MAX_CREDENTIAL_BLOB_SIZE = 5 * 512`). The Bandcamp session blob — full cookie jar plus username and metadata — exceeds this, and `CredWrite` fails with `OSError(1783, 'CredWrite', 'The stub received bad data.')` (Win32 error 1783 = `RPC_S_INVALID_TAG`, which for `CredWrite` almost always means the blob exceeds the limit). The error type sits **outside** the keyring exception hierarchy, so `except keyring.errors.KeyringError` does not catch it — without a broader `except Exception` clause it propagates up and turns the FastAPI handler's `try/except` into a 422 (KAMP-282).

Workaround in `kamp_core/win_credential.py`: wrap the JSON blob with **DPAPI** (`CryptProtectData` / `CryptUnprotectData`) and store the resulting ciphertext in the SQLite `sessions.session_json` column. DPAPI has no size limit, the encryption key is tied to the current Windows user account, and the ctypes wiring matches the `macos_keychain.py` pattern (no extra dependency). The wrapped value is detected on read via the `dpapi-v1:` prefix; legacy plaintext rows still work via the same code path. Schema migration v12 → v13 wraps any plaintext rows already on disk on first launch.

Two further notes for future Windows work on this code path:
- The non-mac branches of `set_session`/`get_session`/`clear_session` in `library.py` should always have a final `except Exception` arm in addition to the typed keyring catches — the keyring exception hierarchy does not cover backend bugs.
- Don't add `pywin32` for Credential Manager work — `keyring` 25.x's WinVault and our DPAPI wrapper both use ctypes directly, so the dependency is unnecessary.

## HTML5 drag and Escape-cancel in Electron (KAMP-456)

**Never attempt to cancel an HTML5 drag ghost via JavaScript.** The OS drag session (AppKit on macOS, OLE on Windows) owns the ghost. Nothing in JS can terminate it early — not synthetic `MouseEvent('mouseup')`, not synthetic `DragEvent('dragend')`, not `sendInputEvent({type:'mouseUp'})`, not `sendInputEvent({type:'keyDown', keyCode:'Escape'})`. The ~1s delay on Escape is OS drag session teardown time, not JS latency.

Four approaches were tried on KAMP-456; all failed. The correct fix (KAMP-458) is replacing HTML5 drag with pointer-events drag (`onPointerDown` + `document pointermove/pointerup`) for any drag that needs Escape-cancel. Pointer-events drag has no OS modal session — the ghost is a DOM element, keyboard events fire normally, Escape cancels immediately. Keep HTML5 drag only for cross-component drops where `dataTransfer` is essential and Escape-cancel is not required.

## Click-free pause/stop/resume fades in mpv (KAMP-508)

A smooth fade on pause/stop/resume turned out to depend on a stack of non-obvious mpv/ffmpeg constraints. The working design (`_FADE_LUA` in `kamp_core/playback.py`) is an embedded Lua script that re-arms a persistent `afade` filter via `af-command`; `pause()`/`resume()`/`stop()` only send `script-message`s. Lessons, each verified empirically (drive mpv over the JSON IPC socket and measure RMS via a second `astats,ametadata=print` lavfi filter on stdout):

- **The `volume` property and ffmpeg `volume` filter apply ONE constant gain per audio frame — no per-sample interpolation.** Any stepped volume ramp is zipper noise regardless of step count (confirmed in FFmpeg `af_volume.c`). Use `afade`, which interpolates the gain *per sample* (`curve=hsin` raised cosine). Don't waste time tuning step counts on a volume ramp.
- **`af-command` routing is a trap.** Address the filter by its **bare** mpv label (`kampfade`, NOT `@kampfade` — the `@` is only the *labelling* syntax in `--af-append`) and route to the **inner** ffmpeg instance name (`afade`, NOT `all`). The wrong form returns `"error running command"` and the gain silently never moves (looks like "a timer, not a fade").
- **The fade filter runs ~`audio-buffer` (default 0.2s) AHEAD of the speakers.** mpv hands that much audio to the output device before it's heard, and it's already past the filter. Anchor every fade at the *filter head* (`audio-pts + audio-buffer + margin`), not the audible `audio-pts`; otherwise a short fade lands entirely inside already-buffered audio and is inaudible (a 3s fade was audible only because it overflowed the buffer). Delay the actual `pause` by the same lead so it falls on silence.
- **Seek and load RESET `afade` to its command-line definition (unity).** So a seek-while-paused, or the `load_paused` that restores a mid-track position on app start, refills the output buffer at full volume — and the filter (before the buffer) cannot retroactively fade it. Gating the filter "silent" loses the race against the refill. Fix: gate the **output** with the `mute` property (it sits *after* the filter and survives seeks) while the buffer drains on resume, then unmute as the fade-in reaches the speakers. kamp has no mute feature of its own, so the property is free to use.
- **`afade` rejects a negative `start_time`** (the af-command fails); `audio-pts` reads slightly negative right after a seek-while-paused — clamp to 0.
- **Test-harness gotcha:** `--ao=null` has no output buffer, so it hides every buffer-timing bug (fades that "work" in the harness are silent on a real device). The user recording output with Audio Hijack is what surfaced the buffer issues. For anything buffer/latency-related, real-device verification is mandatory.
