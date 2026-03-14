# Changelog

All notable changes to tune-shifter will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-03-14

### Added

- **Filesystem watcher** — monitors a configurable staging directory using `watchdog`; automatically processes any ZIP archive or extracted folder dropped into it
- **ZIP extraction** — unpacks Bandcamp download archives and discovers audio files within
- **MusicBrainz tagging** — looks up each release by artist and album title; writes canonical tags (artist, album artist, album, year, track number, disc number, MusicBrainz release ID) using `mutagen`; auto-selects the highest-scoring match when multiple results are returned
- **Cover art embedding** — fetches front cover art from the MusicBrainz Cover Art Archive; validates minimum dimensions (≥ 1000 × 1000 px) and maximum file size (≤ 1 MB) before embedding into every track
- **Library organiser** — moves finished files into the user's library using a configurable path template (`{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}`)
- **Error quarantine** — failed items are moved to `staging/errors/` so nothing loops or blocks the queue
- **Bandcamp auto-download** — polls a Bandcamp collection for new purchases and downloads them automatically; authenticates via a one-time interactive Playwright browser login (no credentials stored); session is serialised to disk with owner-only permissions (`0600`) and reused on subsequent runs
- **Persistent session validation** — on each run, the saved Playwright session is validated against Bandcamp's authenticated API before use; re-prompts for interactive login only when the session has expired
- **Format selection** — supports `mp3-v0`, `mp3-320`, and `flac` download formats; format is selected via DOM interaction with Bandcamp's Knockout.js-driven download page
- **State tracking** — records downloaded purchase IDs in a local JSON state file so nothing is ever re-downloaded; `sync --mark-synced` bootstraps the state from an existing collection without downloading any files
- **Background daemon** — `tune-shifter daemon` runs the watcher and Bandcamp poller together; handles `SIGINT` and `SIGTERM` for clean shutdown
- **macOS service installation** — `tune-shifter install-service` registers the daemon as a launchd user agent that starts at login and restarts on crash; `tune-shifter uninstall-service` removes it
- **One-shot sync** — `tune-shifter sync` downloads new purchases to staging and exits, for use without the daemon
- **TOML configuration** — config file written to `~/.config/tune-shifter/config.toml` on first run with sensible defaults; all paths, MusicBrainz contact, artwork constraints, library template, and Bandcamp options are configurable
- **MP3 and AAC/M4A support**

[0.1.0]: https://github.com/yourname/tune-shifter/releases/tag/v0.1.0
