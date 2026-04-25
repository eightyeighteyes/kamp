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
- Task lifecycle: when work begins on a task, move it to In Progress. When the user confirms it is tested and complete, squash merge the PR and move the task to Done.
- Prefer running single tests, and not the whole test suite, for performance; use `--no-cov` when running a single file to skip the coverage threshold check (e.g. `poetry run pytest tests/test_foo.py -v --no-cov`)
- Update documentation (README.md) after new features are validated
- Document rationale in comments: succinctly explain *why* decisions are made
- After completing a feature, before closing a pull request, retrospect about the development experience and update claude.md with lessons learned.

# Lessons Learned
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

## Cloudflare TLS fingerprinting in the built app
PyInstaller bundles its own OpenSSL, which has a different JA3/JA4 TLS fingerprint than a real browser. Cloudflare detects this and serves JS challenge pages (HTTP 200, ~3 KB HTML) instead of the expected response — **even for authenticated JSON API endpoints**, not just HTML page loads. This only manifests in the built `.app`; in dev the system Python uses macOS SecureTransport or a different OpenSSL version that Cloudflare doesn't flag.

The only reliable fix for any `bandcamp.com` request in the built app is to route it through Electron's `net` module (Chromium's network stack), which has a real browser TLS fingerprint and already holds the `cf_clearance` cookie. See TASK-127. Do not attempt to fix this by changing User-Agent, tweaking cipher suites, or using a different requests library — the check is at the TLS layer before HTTP headers are read.

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

## Shared debounce and high-volume FSEvents
`_LibraryHandler._schedule()` (in `watcher.py`) is shared between FSEvents from the library directory and explicit `trigger_scan()` calls. During a large batch sync, continuous FSEvents from files being moved in reset the debounce timer faster than the `_MAX_SETTLE_SECONDS` cap fires. To guarantee a scan fires after each pipeline completion, bypass the debounce entirely: call `_on_library_change` directly in a `threading.Thread` from `on_pipeline_complete` instead of routing through `lib_watcher.trigger_scan()`.

<!-- BACKLOG.MD MCP GUIDELINES START -->

<CRITICAL_INSTRUCTION>

## BACKLOG WORKFLOW INSTRUCTIONS

This project uses Backlog.md MCP for all task and project management activities.

**CRITICAL GUIDANCE**

- If your client supports MCP resources, read `backlog://workflow/overview` to understand when and how to use Backlog for this project.
- If your client only supports tools or the above request fails, call `backlog.get_backlog_instructions()` to load the tool-oriented overview. Use the `instruction` selector when you need `task-creation`, `task-execution`, or `task-finalization`.

- **First time working here?** Read the overview resource IMMEDIATELY to learn the workflow
- **Already familiar?** You should have the overview cached ("## Backlog.md Overview (MCP)")
- **When to read it**: BEFORE creating tasks, or when you're unsure whether to track work

These guides cover:
- Decision framework for when to create tasks
- Search-first workflow to avoid duplicates
- Links to detailed guides for task creation, execution, and finalization
- MCP tools reference

You MUST read the overview resource to understand the complete workflow. The information is NOT summarized here.

</CRITICAL_INSTRUCTION>

<!-- BACKLOG.MD MCP GUIDELINES END -->
