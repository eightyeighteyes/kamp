# Backlog

> Estimates use the vinyl scale: Single (<0.5), Side (0.5–1), LP (2), 2xLP (4), Box Set (4–8), Discography (>8)
> ⚠️ = needs scoping before work can start

## Error Handling: when there's an error in the pipeline, send a system level notification
*Single* — hook points already exist (`ExtractionError`, `TaggingError`, `ArtworkError`, `MoveError` in `pipeline.py`, bare `except Exception` in `watcher.py`); wire `rumps.notification()` to each failure site.

Possible error states to inform the user about:
- Download failure
- Download timeout
- Tagging failure
- Unable to find image for release
- Library folder doesn't exist

## Bandcamp Logout: Remove or replace the active Bandcamp session
*Side* — two surfaces (CLI `tune-shifter sync logout` + menu bar Logout item); CLI deletes the session file and state file; menu bar item calls the same logic and refreshes Bandcamp item state
```
Bandcamp Sync
Sync Status
Logout
```

## Cross-platform service installation (Linux systemd, Windows Task Scheduler)
*Side* — Linux systemd unit file is straightforward; Windows Task Scheduler adds another side; can ship incrementally

# Needs Refinement
## Investigate: main process inflates ~50 MB when Bandcamp sync starts and never recovers
*⚠️ LP* — subprocess isolation is implemented (syncer and pipeline both spawn via `multiprocessing.get_context("spawn")`) but the main process grows from ~35 MB to ~83 MB when sync starts and stays there after sync ends. An additional ~8 MB subprocess also lingers after sync completes. The subprocess workers themselves are not the resident cost — something in the parent or in the IPC setup is loading heavy modules or retaining allocations. Requires profiling (e.g. `tracemalloc`, `psutil` RSS snapshots before/after sync, `sys.modules` diff) to identify what is inflating memory in the parent and why it is not released. Scoping question: is the 50 MB growth from the queues / pickling overhead of passing `Config` objects, from a remaining import triggered at IPC setup time, or from OS-level page retention after multiprocessing fork-related copy-on-write?

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

## Allow a user to verify tags before they're written
⚠️ Not scoped — needs UI design (CLI prompt? TUI? GUI?) before estimating

## bug: pyenv shim shadows Homebrew binary after dev/brew cycle
*Single* — formula is clean (isolated venv). Root cause: a past dev practice (pre-Poetry) wrote `tune-shifter` to pyenv's global site-packages; `pyenv rehash` registered the shim and it persisted. Fix: audit current dev paths for any global pip writes; add `.python-version` to the repo so pyenv doesn't pick up executables from Poetry's cache venv; document the canonical dev workflow.

# Needs Estimation
-- don't discard this section --

## Add Bandcamp Auto Sync Frequency Options to Menu Bar App
*Side* — config and syncer already support arbitrary intervals; this adds a submenu with radio-style checkmarks (Off / 5 min / 15 min / 30 min / hourly / daily) that writes the new value via `config set` and triggers a live reload so the running daemon picks it up without a restart.

Options:
- Off
- 5 minutes
- 15 minutes
- 30 minutes
- hourly
- daily

## Rebrand to "kamp-daemon"
*Side* — rename package, CLI entry point, config dir (`~/.config/kamp-daemon`), state dir, log prefixes, README, and pyproject. Main risk is the config/state migration path for existing installs; needs a decision on auto-migrate vs. deprecation warning before starting.

Replace EVERYTHING that says 'tune-shifter' with 'kamp-daemon'

## Full Windows Support
*Box Set* — prerequisite: Rebrand must ship first. Breakdown:

| Component | Estimate | Notes |
|---|---|---|
| Windows tray app (pystray, full parity) | LP | pystray is pull-based (menus rebuilt on open); status animation needs background icon-swap thread; no inline status text like rumps |
| Windows CI (GitHub Actions `windows-latest`) | Side | Subprocess spawn differences, path separator edge cases, likely several test fixes |
| Playwright on Windows | Side | Chromium download + DevTools Protocol over localhost; verify subprocess isolation pattern holds |
| Windows service install (NSSM) | Side | NSSM wraps the CLI; simpler to manage start/stop than Task Scheduler |
| Chocolatey packaging | Side | `.nuspec`, install/uninstall scripts, community repo submission (review queue can take weeks) |
| Path/config conventions (`%APPDATA%`) | Single | `pathlib` handles most of it; needs an audit pass |

Target: Windows 10/11 only. Distribution via Chocolatey.