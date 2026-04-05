# kamp

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
- **Error notifications** — macOS system notifications for pipeline failures (extraction, tagging, artwork, download) and Bandcamp sync failures
- **macOS menu bar** — optional status-bar icon with pipeline Play/Stop toggle, on-demand Bandcamp sync, and a live sync status indicator with pulse animation
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
| `rumps` | macOS menu bar integration (macOS only) |

---

## Installation

### Homebrew (recommended)

```bash
brew tap eightyeighteyes/kamp
brew install kamp
```

After install, download the Playwright browser binaries required for Bandcamp auto-download:

```bash
/opt/homebrew/opt/kamp/venv/bin/playwright install chromium
```

zsh tab completion is included and activated automatically via Homebrew's shell integration.

### From source

```bash
git clone https://github.com/eightyeighteyes/kamp
cd kamp
poetry install
playwright install chromium
```

---

## Configuration

On first run, kamp creates a config file with defaults at:

| Platform | Path |
|---|---|
| macOS / Linux | `~/.config/kamp/config.toml` |
| Windows | `%APPDATA%\kamp\config.toml` |

Edit it before starting:

```toml
[paths]
staging = "~/Music/staging"   # drop ZIPs here; kamp watches this directory
library = "~/Music"           # finished files land here

[musicbrainz]
app_name = "kamp"
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
poll_interval_minutes = 60    # 0 = polling disabled; use `kamp sync` manually
# cookie_file = "~/.config/kamp/cookies.txt"  # advanced: bypass interactive login
```

---

## Usage

### Run the daemon

```bash
kamp
# or explicitly:
kamp daemon
```

Watches the staging directory for new ZIPs and folders, and (if `[bandcamp]` is configured) polls Bandcamp for new purchases on the configured interval.

Override paths without editing the config:

```bash
kamp daemon --staging ~/Downloads/staging --library ~/Music
```

### macOS menu bar

On macOS the daemon shows a `music.note.list` status-bar icon by default. The menu provides:

| Item | Action |
|---|---|
| **Stop / Play** | Pause or resume the ingest pipeline without stopping the process |
| **Bandcamp Sync** | Trigger an immediate sync; grayed out if `[bandcamp]` is not configured |
| **Sync Status** | Read-only: "Status: Idle" or "Status: Syncing…" (icon pulses during sync) |
| **Bandcamp Logout** | Delete the saved session and sync state; the next sync will re-authenticate |
| **About Tune-Shifter** | Opens the project GitHub page |
| **Quit** | Shuts down the daemon and removes the menu bar icon |

To run without the menu bar icon:

```bash
kamp daemon --no-menu-bar
kamp install-service --no-menu-bar  # persists the preference in the launchd plist
```

---

### Run as a background service (macOS)

Register kamp as a launchd user agent so it starts at login and runs silently in the background:

```bash
kamp install-service
```

Logs are written to `~/.local/share/kamp/daemon.log`.

```bash
kamp stop              # pause the service
kamp play              # resume the service
kamp status            # check if it's running
kamp uninstall-service # remove it permanently
```

### Preferences dialog (UI)

Open the Preferences dialog from **kamp → Preferences** in the macOS menu bar, or with **Cmd+,** (macOS) / **Ctrl+,** (Linux/Windows). Changes take effect immediately — no Apply or OK button. Settings marked **↺ restart** require restarting the kamp server to take effect.

### View or update config from the command line

```bash
kamp config show
kamp config set paths.staging ~/Downloads/staging
kamp config set musicbrainz.contact me@example.com
kamp config set artwork.min_dimension 500
```

Keys use dot notation (`section.field`). Run `kamp config set --help` to see all valid keys.

### Test notifications (macOS)

Verify that macOS notification permissions are granted and the full notification path works by simulating a failure at a specific pipeline stage:

```bash
kamp test-notify --type extraction  # simulate extraction failure
kamp test-notify --type tagging     # simulate MusicBrainz tagging failure
kamp test-notify --type artwork     # simulate cover art warning
kamp test-notify --type move        # simulate library move failure
kamp test-notify --type download    # simulate Bandcamp sync failure
```

Each command runs the real pipeline (or syncer) up to the named stage, injects a failure, and fires a notification via the same code path the daemon uses — so if the notification appears, the full chain is working.

---

### One-shot Bandcamp sync

```bash
kamp sync
```

Downloads any purchases not yet in your local state, places them in staging, and exits. The watcher (if running) picks them up automatically.

### First sync behaviour

On the first sync (no state file yet), kamp assumes you already have your Bandcamp purchases in your local library and marks your entire collection as synced before downloading. Only purchases made after that point will be downloaded.

To override this and re-download your entire collection from scratch:

```bash
kamp sync --download-all
```

This clears the local sync state and downloads everything.

### Log out of Bandcamp

To delete the saved session and force re-authentication on the next sync:

```bash
kamp logout
```

This also clears the sync state file, so the next sync will re-examine your full collection. Use this if your session expires or you want to switch accounts.

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

On first sync, kamp opens a browser window and prompts you to log in to Bandcamp. Once you complete login the window closes automatically and the session is saved. Subsequent syncs (and the background daemon) reuse the saved session without opening a browser — you only need to log in again if your Bandcamp session expires.

The session file and download state are stored alongside each other:

| Platform | Directory |
|---|---|
| macOS / Linux | `~/.local/share/kamp/` |
| Windows | `%LOCALAPPDATA%\kamp\` |

The session file (`bandcamp_session.json`) is written with owner-only permissions (`0600`). The state file (`bandcamp_state.json`) tracks which purchases have been downloaded so nothing is ever re-downloaded.

---

## Development

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) — check "Add to PATH" on Windows |
| Poetry | latest | `pip install poetry` or see [python-poetry.org](https://python-poetry.org/docs/#installation) |
| Node.js | 18+ (LTS) | [nodejs.org](https://nodejs.org/) — includes npm |
| mpv | latest | [mpv.io](https://mpv.io/) — must be on `PATH` for the player server to run |
| Git | any | [git-scm.com](https://git-scm.com/) |

### Bootstrap

```bash
# 1. Clone
git clone https://github.com/eightyeighteyes/kamp
cd kamp

# 2. Enable the pre-commit hook (black + mypy)
git config core.hooksPath .githooks

# 3. Install Python dependencies (creates a .venv in the project root)
poetry install

# 4. Install Playwright browser binaries (required for Bandcamp auto-download)
poetry run playwright install chromium

# 5. Install UI dependencies
cd kamp_ui
npm install
```

### Run in development

```bash
# Start the Python server (from repo root)
poetry run kamp server

# In a second terminal, start the Electron UI with hot-reload (from kamp_ui/)
cd kamp_ui
npm run dev
```

`npm run dev` launches Electron and the Vite dev server. The renderer reconnects automatically whenever the Python server starts or restarts.

### Tests and linting

```bash
# Run all tests (from repo root)
poetry run pytest

# Run a single test file without the coverage threshold check
poetry run pytest tests/test_server.py -v --no-cov

# Type check
poetry run mypy kamp_daemon/ kamp_core/

# Format
poetry run black kamp_daemon/ kamp_core/ tests/
```


---

*Built by [Claude Sonnet 4.6](https://www.anthropic.com/claude) (claude-sonnet-4-6) — Anthropic's AI assistant.*
