# Backlog

> Estimates use the vinyl scale: Single (<0.5), Side (0.5–1), LP (2), 2xLP (4), Box Set (4–8), Discography (>8)
> ⚠️ = needs scoping before work can start

## Menu Bar Status Item - Epic
*LP* — when the daemon runs, show a menu bar icon with pipeline start/stop and Bandcamp sync controls.

Icon: SF Symbol `music.note.square.stack`; pulse animation while a Bandcamp sync is in progress.

Menu:
- **Play / Stop** — toggles the internal pipeline (watcher + syncer) on/off within the running process. Becomes Stop when pipeline is running, Play when paused.
- *(separator)*
- **Bandcamp Sync** — triggers an immediate Bandcamp sync on a background thread; grayed out if `[bandcamp]` config is absent or a sync is already in progress
- **Sync Status** — read-only label: "Status: Syncing…" or "Status: Idle"
- *(separator)*
- **About Tune-Shifter** — opens project GitHub page

Broken down into delivery units:

**Single: Add `rumps` dependency**
Add `rumps>=0.4` to `pyproject.toml` and `Formula/tune-shifter.rb`. Add a `platform.system() == "Darwin"` guard so the import is skipped on Linux/Windows and the rest of the codebase stays cross-platform. Update CI to skip rumps-dependent tests on non-macOS runners (the existing `ci.yml` runs on `ubuntu-latest`; add a conditional or skip marker).

**Side: Refactor daemon lifecycle into `DaemonCore`**
`_cmd_daemon()` currently blocks the main thread on `watcher.join()`. `rumps` requires the main thread for its AppKit run loop, so the blocking join must move off-main. Extract a `DaemonCore` class that starts/stops `Watcher`, `Syncer`, and `ConfigMonitor` and exposes `start()`, `stop()`, and `shutdown()` methods, plus a `state` property (`"running"` / `"paused"` / `"stopped"`). The existing `_cmd_daemon` path calls this then blocks on `watcher.join()` as before; the menu bar path calls this then hands the main thread to `rumps.App.run()`. Signal handling (`SIGINT`/`SIGTERM`) moves into `DaemonCore`.

**Side: `MenuBarApp` class (`tune_shifter/menu_bar.py`)**
`rumps.App` subclass holding a reference to `DaemonCore`. Before `rumps.App.__init__` runs, sets `NSApplicationActivationPolicyAccessory` so the process gets Window Server access from launchd without a bundle:

```python
if platform.system() == "Darwin":
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
```

Ships the menu described above. Play/Stop calls `DaemonCore.start()` / `DaemonCore.stop()` and updates the item title. Bandcamp Sync calls `syncer.sync_once()` on a background thread. A `@rumps.timer(5)` callback refreshes Sync Status and the icon pulse state. Quit calls `DaemonCore.shutdown()` then `rumps.quit_application()`.

**Single: Wire menu bar into `_cmd_daemon`**
Add a `--menu-bar` flag to the `daemon` subcommand. When set (and on macOS), call `MenuBarApp.run()` instead of `watcher.join()`. When `--menu-bar` is used, `_cmd_install_service` appends `daemon --menu-bar` to `ProgramArguments`. No behavioural change on Linux/Windows or when flag is omitted.

*launchd + AppKit compatibility spiked — no bundle required, estimate unchanged at LP.*

## Producer Support
*Side* — add recording-rels include to `get_release_by_id` call and traverse relationships to extract producer credits

## One File At A Time
*Single* — watcher already handles ZIPs; extend to schedule individual audio files (`.mp3`, `.m4a`, etc.) dropped directly into staging

## Cross-platform service installation (Linux systemd, Windows Task Scheduler)
*Side* — Linux systemd unit file is straightforward; Windows Task Scheduler adds another side; can ship incrementally

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

# Needs Estimation
-- don't discard this section --