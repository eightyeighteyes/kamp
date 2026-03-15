# Changelog

All notable changes to tune-shifter will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.5.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.4.0...v0.5.0) (2026-03-15)


### Features

* interactive first-run configuration setup ([91365a6](https://github.com/eightyeighteyes/tune-shifter/commit/91365a69be44323cae646c878513877dd6425577))
* interactive first-run configuration setup ([a7f62ac](https://github.com/eightyeighteyes/tune-shifter/commit/a7f62acfa883da8a8bfb086fff5347bd857a0c88))


### Documentation

* update USAGE.md for interactive first-run setup ([e318da9](https://github.com/eightyeighteyes/tune-shifter/commit/e318da90095bece1e56ed18dda896e2852fab0ca))

## [0.4.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.3.0...v0.4.0) (2026-03-15)


### Features

* prefer bundled artwork from archive over Cover Art Archive ([88ad795](https://github.com/eightyeighteyes/tune-shifter/commit/88ad7954fad1dd244c1f330b845b9745724f90b4))
* prefer bundled artwork from archive over Cover Art Archive ([ef33820](https://github.com/eightyeighteyes/tune-shifter/commit/ef33820f93dcdcc4ac8fa76fd0d7a2829a4092d6))


### Bug Fixes

* add get_release_by_id mock to pipeline tests ([27169c9](https://github.com/eightyeighteyes/tune-shifter/commit/27169c9183f1418d932440b93a299098ff939de6))
* fetch full release details and fall back to release-group artwork ([e37ca19](https://github.com/eightyeighteyes/tune-shifter/commit/e37ca19442d8217175f2ddaa7fec5297b530c120))
* M4A tagging — full release details and release-group artwork fallback ([7879e74](https://github.com/eightyeighteyes/tune-shifter/commit/7879e742c8a2a0c1147e595cb3968a7e7b15c3c1))

## [0.3.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.2.3...v0.3.0) (2026-03-15)


### Features

* handle macOS FSEvents coalescing for M4A folder ingest ([7fbfc7e](https://github.com/eightyeighteyes/tune-shifter/commit/7fbfc7efeafe33fa5a2403224e1dfda3317108a6))
* handle macOS FSEvents coalescing for M4A folder ingest ([726b506](https://github.com/eightyeighteyes/tune-shifter/commit/726b506ae39d47c8e2ef367e6ac441d2c7d455db))
* handle macOS FSEvents coalescing for M4A folder ingest  on_modified handler scans staging root on DirModifiedEvent so folders dragged in via Finder are processed even when FSEvents drops the DirCreatedEvent/DirMovedEvent. Also retries MusicBrainz searches with edition suffixes stripped (Deluxe Edition, Remastered, etc.). ([7fbfc7e](https://github.com/eightyeighteyes/tune-shifter/commit/7fbfc7efeafe33fa5a2403224e1dfda3317108a6))

## [Unreleased]

### Added

- `on_moved` handler in watcher: items dragged into staging on the same filesystem (macOS Finder) are now scheduled for processing
- `on_modified` handler in watcher: fixes macOS FSEvents coalescing drag-and-drop renames into `DirModifiedEvent` on the staging parent instead of emitting `DirCreatedEvent`/`DirMovedEvent` for the new item — the root cause of M4A folders being silently ignored
- MusicBrainz edition-suffix retry in tagger: album names with iTunes suffixes like "(Deluxe Edition)" or "(Remastered)" are stripped and retried once before failing, so those releases are matched correctly

---

## [0.2.3](https://github.com/eightyeighteyes/tune-shifter/compare/v0.2.2...v0.2.3) (2026-03-15)


### Bug Fixes

* install dependencies so they are in virtualenv after brew install ([048298c](https://github.com/eightyeighteyes/tune-shifter/commit/048298c146c7272147defa00a9b123cccd9da2f2))

## [0.2.2](https://github.com/eightyeighteyes/tune-shifter/compare/v0.2.1...v0.2.2) (2026-03-15)


### Bug Fixes

* homebrew symlink doesn't exist ([046c19d](https://github.com/eightyeighteyes/tune-shifter/commit/046c19d62e3f51df2d11df3840716594d1163b58))

## [0.2.1](https://github.com/eightyeighteyes/tune-shifter/compare/v0.2.0...v0.2.1) (2026-03-15)


### Bug Fixes

* work around release please trigger limitation ([09f3f05](https://github.com/eightyeighteyes/tune-shifter/commit/09f3f05a2740cb993e52f217b77773d138a47e34))

## [Unreleased]


---

## [0.2.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.1.0...v0.2.0) (2026-03-15)

### Added

- **GitHub Actions CI** — runs `black`, `mypy`, and `pytest` on every push to `main` and on pull requests
- **Homebrew distribution** — `brew tap eightyeighteyes/tune-shifter && brew install tune-shifter` installs the app; USAGE.md is displayed inline after install via formula `caveats`
- **Automated releases via Release Please** — merging a Release PR (opened automatically from Conventional Commits) tags the release, builds and attaches the sdist, and syncs the Homebrew formula; `pyproject.toml` version is bumped automatically

### Features

* add release please ([75d8180](https://github.com/eightyeighteyes/tune-shifter/commit/75d81801d702400ffdc76cf73136ce842c63dbde))

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

[Unreleased]: https://github.com/eightyeighteyes/tune-shifter/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eightyeighteyes/tune-shifter/releases/tag/v0.1.0
