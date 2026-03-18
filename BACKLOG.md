# Backlog

> Estimates use the vinyl scale: Single (<0.5), Side (0.5–1), LP (2), 2xLP (4), Box Set (4–8), Discography (>8)
> ⚠️ = needs scoping before work can start

## Menu Bar: Show Pipeline Status (Idle, Tagging, Updating Artwork, Moving)
*Side* — requires threading a stage-change callback through `pipeline.py` (Watcher-owned) and the Syncer path; two separate status sources need to be reconciled in `MenuBarApp._refresh`

## ALAC Support
*Single* — add `"alac"` to `_FORMAT_LABELS` in `bandcamp.py`; the rest of the pipeline already handles `.m4a` containers (ALAC and AAC share the same container format and tag schema via `mutagen.mp4.MP4`)

## Tagging: Skip artwork fetch when existing art meets quality requirements
*Single* — add a guard in `artwork.py` (or `pipeline.py`) that checks embedded art dimensions before making a Cover Art Archive request; saves a few seconds per ingest when Bandcamp art already meets the threshold

## Tagging: Producer Support
*Side* — add recording-rels include to `get_release_by_id` call and traverse relationships to extract producer credits

## Executable / Process name should be tune-shifter, not Python
*Single* — add `setproctitle` dependency; call `setproctitle.setproctitle("tune-shifter")` near the top of `__main__.py`; covers Activity Monitor and OS permission dialogs

## Pipeline: One File At A Time
*Single* — watcher already handles ZIPs; extend to schedule individual audio files (`.mp3`, `.m4a`, etc.) dropped directly into staging

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
