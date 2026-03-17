# Backlog

> Estimates use the vinyl scale: Single (<0.5), Side (0.5–1), LP (2), 2xLP (4), Box Set (4–8), Discography (>8)
> ⚠️ = needs scoping before work can start

## Producer Support
*Side* — add recording-rels include to `get_release_by_id` call and traverse relationships to extract producer credits

## One File At A Time
*Single* — watcher already handles ZIPs; extend to schedule individual audio files (`.mp3`, `.m4a`, etc.) dropped directly into staging

## Cross-platform service installation (Linux systemd, Windows Task Scheduler)
*Side* — Linux systemd unit file is straightforward; Windows Task Scheduler adds another side; can ship incrementally

## Menu Bar Status Item
*LP* — when the daemon runs, show a menu bar icon with a "Sync Now" item.

Broken down into delivery units:

**Single: Add `rumps` dependency**
Add `rumps>=0.4` to `pyproject.toml` and `Formula/tune-shifter.rb`. Add a `platform.system() == "Darwin"` guard so the import is skipped on Linux/Windows and the rest of the codebase stays cross-platform. Update CI to skip rumps-dependent tests on non-macOS runners (the existing `ci.yml` runs on `ubuntu-latest`; add a conditional or skip marker).

**Side: Refactor daemon lifecycle into `DaemonCore`**
`_cmd_daemon()` currently blocks the main thread on `watcher.join()`. `rumps` requires the main thread for its AppKit run loop, so the blocking join must move off-main. Extract a `DaemonCore` class that starts/stops `Watcher`, `Syncer`, and `ConfigMonitor` and exposes a `shutdown()` method. The existing `_cmd_daemon` path calls this then blocks on `watcher.join()` as before; the menu bar path calls this then hands the main thread to `rumps.App.run()`. Signal handling (`SIGINT`/`SIGTERM`) moves into `DaemonCore`.

**Side: `MenuBarApp` class (`tune_shifter/menu_bar.py`)**
`rumps.App` subclass holding a reference to `DaemonCore`. Ships:
- A static menu bar icon (see ⚠️ icon design below)
- **"Sync Now"** menu item — calls `syncer.sync_once()` on a background thread; item is disabled while a sync is already in progress
- **"Quit"** menu item — calls `DaemonCore.shutdown()` then `rumps.quit_application()`
- Minimum viable dynamic state: menu bar title or tooltip reflects "Syncing…" vs "Idle" (see ⚠️ status display)

**Single: Wire menu bar into `_cmd_daemon`**
Add a `--menu-bar` flag to the `daemon` subcommand. When set (and on macOS), call `MenuBarApp.run()` instead of `watcher.join()`. No behavioural change on Linux/Windows or when flag is omitted.

---

### ⚠️ Open design questions — needs answers before work starts

**launchd + AppKit compatibility** — *Spiked. No bundle required; estimate stays LP.*

When launchd starts a bare CLI process (no `.app` bundle, no `Info.plist`), the process is spawned as a background process type and has no Window Server connection. Calling `NSApplication.sharedApplication()` at that point fails with `_RegisterApplication(), FAILED TO establish the default connection to the WindowServer`. This is the root failure mode.

**`LSUIElement` in the launchd plist does nothing.** `LSUIElement` is an `Info.plist` key consumed by the OS when a `.app` bundle launches; it has no effect in a `~/Library/LaunchAgents/` plist. Adding it to the existing launchd plist template would be a no-op.

**The fix is purely in Python, before NSApplication initialises.** macOS exposes `TransformProcessType` (Carbon ApplicationServices) and the modern Objective-C equivalent `NSApp.setActivationPolicy_()` to convert a background process into a UI-element process at runtime — no bundle required. The call must happen before `NSApplication.sharedApplication()` is invoked (i.e. before `rumps.App.__init__` runs).

Concrete approach in `MenuBarApp.__init__`:

```python
import platform
if platform.system() == "Darwin":
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
```

`NSApplicationActivationPolicyAccessory` is the programmatic equivalent of `LSUIElement = true`: the process gets a menu bar presence and Window Server access, no Dock icon appears, and the app is excluded from Cmd-Tab cycling. `pyobjc-framework-Cocoa` (a transitive dependency of `rumps`) already provides `AppKit`, so no new dependency is needed.

**The launchd plist does not need changes.** LaunchAgents run in the user's login session and inherit Window Server access once the process type is transformed. The existing plist template is sufficient; `LSUIElement` stays out of it.

**Implication for `install-service`:** when the `--menu-bar` flag is used, `_cmd_install_service` should append `daemon --menu-bar` to `ProgramArguments`. That's a one-line change scoped inside the wire-up Single.

**Estimate unchanged: LP.** A `.app` bundle is not required; the Homebrew formula distribution model is unaffected.

**Icon design** — Single static icon or state-based set (idle / syncing / error)? SF Symbols (no asset files needed, macOS 11+) via `rumps`'s `template_image` vs a custom PNG. Minimum viable: one SF Symbol (`music.note` or `arrow.triangle.2.circlepath`). The icon name/asset must be decided before `MenuBarApp` work starts.

**Menu contents** — Beyond "Sync Now" and "Quit": should the menu include shortcuts like "Open Staging Folder", "Open Log File", or "Open Config"? Is a "Last synced: X ago" separator line in scope? Define the minimum viable menu before the Side begins.

**Status display** — Does the menu bar title or a `@rumps.timer` callback show the current state ("Syncing…", "Idle", "Error: see log")? If so, what cadence and what wording? This drives the internal state model that `DaemonCore` needs to expose.

**First-run / Bandcamp setup in GUI context** — When launched via launchd before the user has run `tune-shifter sync` interactively, the `[bandcamp]` config section is absent and Bandcamp polling silently does nothing. Does the menu bar feature need a "Set up Bandcamp…" item that opens a Terminal or a browser-based flow? Or is the existing CLI-first setup contract sufficient (i.e. document that users must run `tune-shifter sync` once before installing the service)?

## ALAC Support
*Single* — add `"alac"` to `_FORMAT_LABELS` in `bandcamp.py`; the rest of the pipeline already handles `.m4a` containers (ALAC and AAC share the same container format and tag schema via `mutagen.mp4.MP4`)

# Needs Refinement
## Best Release
*Side* — when multiple MB results exist, prefer the release closest to the original physical format (LP/CD over digital/streaming)

⚠️ Needs scoping: what ranking heuristic? (release format field, country, date proximity?) and what's the fallback when no physical release exists? Note: date-based tie-breaking (earliest release wins) is already implemented; remaining work is format/country preference.

## AcoustID Support
*LP* — fingerprint audio with `fpcalc`/chromaprint, look up recording via AcoustID API, feed MBID into existing tagger

⚠️ Needs scoping: how to handle mismatches between AcoustID result and existing MusicBrainz search? Which takes precedence?

## Nested Folders
*Side* — when a folder-of-folders is dropped into staging, recurse into subdirectories and treat each leaf folder as an album

⚠️ Needs scoping: does each subfolder get its own MusicBrainz lookup? How are mixed-album folders handled?

## Configurable Album Art Search
⚠️ Not scoped enough to start — each source (Bandcamp, Apple, Spotify, Qobuz) requires its own API integration and auth flow; estimate per source is ~Side to LP. Needs a design pass on the config schema and fallback order before any source is implemented.

## GUI / menu bar app for sync status
*Box Set* — new surface area; needs technology choice (SwiftUI, Tauri, rumps, etc.) and design before scoping

## Allow a user to verify tags before they're written
⚠️ Not scoped — needs UI design (CLI prompt? TUI? GUI?) before estimating

## bug: pyenv shim shadows Homebrew binary after dev/brew cycle
*Single* — formula is clean (isolated venv). Root cause: a past dev practice (pre-Poetry) wrote `tune-shifter` to pyenv's global site-packages; `pyenv rehash` registered the shim and it persisted. Fix: audit current dev paths for any global pip writes; add `.python-version` to the repo so pyenv doesn't pick up executables from Poetry's cache venv; document the canonical dev workflow.
