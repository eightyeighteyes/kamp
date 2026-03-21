# 1.0.0

## Rebrand to "kamp-daemon"
*Side* — rename package, CLI entry point, config dir (`~/.config/kamp-daemon`), state dir, log prefixes, README, and pyproject. Main risk is the config/state migration path for existing installs; needs a decision on auto-migrate vs. deprecation warning before starting.

Replace EVERYTHING that says 'tune-shifter' with 'kamp-daemon'

# Backlog

> Estimates use the vinyl scale: Single (<0.5), Side (0.5–1), LP (2), 2xLP (4), Box Set (4–8), Discography (>8)
> ⚠️ = needs scoping before work can start

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
## Cross-platform service installation (Linux systemd, Windows Task Scheduler)
*Side* — Linux systemd unit file is straightforward; Windows Task Scheduler adds another side; can ship incrementally

# Needs Refinement
## Bug: MusicBrainz Release Id tag casing
⚠️ Needs repro steps — a lowercase tag was observed in the wild but the tagger writes `MusicBrainz Release Id` (mixed case). May be a tag coming from MusicBrainz data rather than a write bug. Needs a concrete example file or log showing the bad tag before scoping a fix.

## Investigate: main process inflates ~50 MB when Bandcamp sync starts and never recovers
*⚠️ LP* — subprocess isolation is implemented (syncer and pipeline both spawn via `multiprocessing.get_context("spawn")`) but the main process grows from ~35 MB to ~83 MB when sync starts and stays there after sync ends. An additional ~8 MB subprocess also lingers after sync completes. The subprocess workers themselves are not the resident cost — something in the parent or in the IPC setup is loading heavy modules or retaining allocations. Requires profiling (e.g. `tracemalloc`, `psutil` RSS snapshots before/after sync, `sys.modules` diff) to identify what is inflating memory in the parent and why it is not released. Scoping question: is the 50 MB growth from the queues / pickling overhead of passing `Config` objects, from a remaining import triggered at IPC setup time, or from OS-level page retention after multiprocessing fork-related copy-on-write?

## Best Release
*Side* — when multiple MB results exist, prefer the release closest to the original physical format (LP/CD over digital/streaming)

⚠️ Needs scoping: what ranking heuristic? (release format field, country, date proximity?) and what's the fallback when no physical release exists? Note: date-based tie-breaking (earliest release wins) is already implemented; remaining work is format/country preference.

## AcoustID Support (heavy tagging approach)
*LP* — fingerprint audio with `fpcalc`/chromaprint, look up recording via AcoustID API, feed MBID into existing tagger. Prerequisite: per-track lookup (light approach) should ship first. Fingerprinting should be its own subprocess pipeline phase.

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
## bug: debug level log noise from asyncio (bandcamp) and PIL.TiffImagePlugin (artwork)

2026-03-21 14:03:19  INFO      tune_shifter.config_monitor  Watching config file for changes: /Users/theodore.terry/.config/tune-shifter/config.toml
2026-03-21 14:03:19  DEBUG     asyncio  Using selector: KqueueSelector
2026-03-21 14:03:21  INFO      tune_shifter.bandcamp  Fetched fan_id=4346318 for user 'tedd-e-terry'

2026-03-21 14:03:39  DEBUG     PIL.TiffImagePlugin  tag: XResolution (282) - type: rational (5) Tag Location: 22 - Data Location: 74 - value: b'\x00\x00\x00H\x00\x00\x00\x01'
2026-03-21 14:03:39  DEBUG     PIL.TiffImagePlugin  tag: YResolution (283) - type: rational (5) Tag Location: 34 - Data Location: 82 - value: b'\x00\x00\x00H\x00\x00\x00\x01'
2026-03-21 14:03:39  DEBUG     PIL.TiffImagePlugin  tag: ResolutionUnit (296) - type: short (3) - value: b'\x00\x01'
2026-03-21 14:03:39  DEBUG     PIL.TiffImagePlugin  tag: YCbCrPositioning (531) - type: short (3) - value: b'\x00\x01'
2026-03-21 14:03:39  DEBUG     PIL.TiffImagePlugin  tag: ExifIFD (34665) - type: long (4) - value: b'\x00\x00\x00Z'
2026-03-21 14:03:39  INFO      tune_shifter.artwork  All 18 file(s) have qualifying embedded art — skipping Cover Art Archive fetch