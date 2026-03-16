# tune-shifter

A background daemon that automates the full ingest pipeline for a digital audio library — from Bandcamp purchase to tagged, art-embedded, organized file in your library — with no manual steps.

Built with Python 3 by [Claude Sonnet 4.6](https://www.anthropic.com/claude).

---

## Features

- **Bandcamp auto-download** — polls your Bandcamp collection for new purchases and downloads them automatically; authenticates via a one-time interactive browser login (no credentials stored)
- **Automatic tagging** — looks up every release on [MusicBrainz](https://musicbrainz.org) and writes canonical tags (artist, album artist, album, year, track number, disc number, MusicBrainz IDs)
- **Cover art** — fetches front cover art from the [MusicBrainz Cover Art Archive](https://coverartarchive.org), validates minimum dimensions (≥ 1000 × 1000 px) and maximum file size (≤ 1 MB), and embeds it in every track
- **Filesystem watcher** — monitors your staging directory; drop a ZIP or folder in and it is processed automatically
- **Configurable library layout** — moves finished files into your library using a template you control (`{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}`)
- **Error quarantine** — failed items are moved to `staging/errors/` so nothing loops or blocks the queue
- **Background service** — one command registers the daemon as a system service that starts at login (macOS launchd)
- **Cross-platform** — macOS, Linux, and Windows (Python 3.11+)

---

## How it works

```
Bandcamp purchase
       │
       ▼
  [bandcamp.py] poll collection API → scrape download links → download ZIP → staging/
       │
       ▼
  [watcher.py] detects new ZIP or folder in staging/
       │
       ▼
  [extractor.py] unzip archive
       │
       ▼
  [tagger.py] MusicBrainz lookup → write tags
       │
       ▼
  [artwork.py] Cover Art Archive → embed cover
       │
       ▼
  [mover.py] render path template → move to library
```

---

## Requirements

- Python 3.11+
- A [MusicBrainz](https://musicbrainz.org) contact email (required by their API policy — used only in the `User-Agent` header)
- For Bandcamp auto-download: a Bandcamp account

### Python dependencies

| Package | Purpose |
|---|---|
| `watchdog` | Filesystem event monitoring |
| `musicbrainzngs` | MusicBrainz release lookup |
| `mutagen` | Reading and writing audio tags (MP3, M4A, FLAC) |
| `requests` | HTTP client for the Cover Art Archive and session validation |
| `Pillow` | Image dimension validation |
| `playwright` | Headless browser for Bandcamp authentication and download |

---

## Installation

### Homebrew (recommended)

```bash
brew tap eightyeighteyes/tune-shifter
brew install tune-shifter
```

After install, download the Playwright browser binaries required for Bandcamp auto-download:

```bash
/opt/homebrew/opt/tune-shifter/venv/bin/playwright install chromium
```

### From source

```bash
git clone https://github.com/eightyeighteyes/tune-shifter
cd tune-shifter
poetry install
playwright install chromium
```

---

## Configuration

On first run, tune-shifter creates a config file with defaults at:

| Platform | Path |
|---|---|
| macOS / Linux | `~/.config/tune-shifter/config.toml` |
| Windows | `%APPDATA%\tune-shifter\config.toml` |

Edit it before starting:

```toml
[paths]
staging = "~/Music/staging"   # drop ZIPs here; tune-shifter watches this directory
library = "~/Music"           # finished files land here

[musicbrainz]
app_name = "tune-shifter"
app_version = "0.1.0"
contact = "you@example.com"   # required by MusicBrainz API policy

[artwork]
min_dimension = 1000          # minimum cover art width and height in pixels
max_bytes = 2_000_000         # maximum cover art file size (1 MB)

[library]
# Variables: {artist} {album_artist} {album} {year} {track} {disc} {title} {ext}
path_template = "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"

# Optional: enable Bandcamp auto-download
[bandcamp]
username = "your-bandcamp-username"
format = "mp3-v0"             # mp3-v0 | mp3-320 | flac
poll_interval_minutes = 60    # 0 = polling disabled; use `tune-shifter sync` manually
# cookie_file = "~/.config/tune-shifter/cookies.txt"  # advanced: bypass interactive login
```

---

## Usage

### Run the daemon

```bash
tune-shifter
# or explicitly:
tune-shifter daemon
```

Watches the staging directory for new ZIPs and folders, and (if `[bandcamp]` is configured) polls Bandcamp for new purchases on the configured interval.

Override paths without editing the config:

```bash
tune-shifter daemon --staging ~/Downloads/staging --library ~/Music
```

### Run as a background service (macOS)

Register tune-shifter as a launchd user agent so it starts at login and runs silently in the background:

```bash
tune-shifter install-service
```

Logs are written to `~/.local/share/tune-shifter/daemon.log`.

```bash
launchctl unload ~/Library/LaunchAgents/com.tune-shifter.plist   # pause the service
launchctl load ~/Library/LaunchAgents/com.tune-shifter.plist   # restart the service
tune-shifter uninstall-service   # remove the service registration
```

### One-shot Bandcamp sync

```bash
tune-shifter sync
```

Downloads any purchases not yet in your local state, places them in staging, and exits. The watcher (if running) picks them up automatically.

### Bootstrap: mark your existing collection as synced

If you've already downloaded everything in your Bandcamp collection manually, run this once before your first sync to prevent re-downloading it all:

```bash
tune-shifter sync --mark-synced
```

This fetches your full collection and records every item ID in the state file without downloading any files. Subsequent `sync` runs (and the daemon) will only pick up purchases made after this point.

### Manual ingest

Drop any Bandcamp ZIP or already-extracted folder into your staging directory. The daemon processes it within a few seconds.

---

## Supported formats

- MP3
- AAC / M4A
- FLAC
- OGG Vorbis

---

## Bandcamp auto-download

> **Note:** Bandcamp has no public API. The collection endpoints used here are reverse-engineered and could change without notice. No passwords are stored.

On first sync, tune-shifter opens a browser window and prompts you to log in to Bandcamp. Once you complete login the window closes automatically and the session is saved. Subsequent syncs (and the background daemon) reuse the saved session without opening a browser — you only need to log in again if your Bandcamp session expires.

The session file and download state are stored alongside each other:

| Platform | Directory |
|---|---|
| macOS / Linux | `~/.local/share/tune-shifter/` |
| Windows | `%LOCALAPPDATA%\tune-shifter\` |

The session file (`bandcamp_session.json`) is written with owner-only permissions (`0600`). The state file (`bandcamp_state.json`) tracks which purchases have been downloaded so nothing is ever re-downloaded.

---

## Development

```bash
poetry install
playwright install chromium
poetry run pytest      # run tests
poetry run mypy tune_shifter/     # type check
poetry run black tune_shifter/ tests/  # format
```


---

*Built by [Claude Sonnet 4.6](https://www.anthropic.com/claude) (claude-sonnet-4-6) — Anthropic's AI assistant.*
