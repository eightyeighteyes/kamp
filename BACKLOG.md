# Backlog

> Estimates use the vinyl scale: Single (<0.5), Side (0.5–1), LP (2), 2xLP (4), Box Set (4–8), Discography (>8)
> ⚠️ = needs scoping before work can start

## OGG Support
*Side* — same as FLAC but via `mutagen.oggvorbis`; can be done alongside FLAC in one pass

## Producer Support
*Side* — add recording-rels include to `get_release_by_id` call and traverse relationships to extract producer credits

## One File At A Time
*Single* — watcher already handles ZIPs; extend to schedule individual audio files (`.mp3`, `.m4a`, etc.) dropped directly into staging

## Config Arguments
*Side* — add `tune-shifter config set <key> <value>` and `tune-shifter config show` subcommands; reads/writes existing TOML file

## Cross-platform service installation (Linux systemd, Windows Task Scheduler)
*Side* — Linux systemd unit file is straightforward; Windows Task Scheduler adds another side; can ship incrementally

## Does Bandcamp auto-download actually work? Test poll_interval_minutes.
*Single* — manual QA task; set a short poll interval and verify downloads trigger correctly

## Menu Bar Status Item
*LP* — when the daemon runs, show a menu bar icon with a "Sync Now" item; requires `rumps` dependency and threading integration with the daemon lifecycle

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

# Needs Estimation

Nothing to estimate.