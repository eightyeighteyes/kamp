# Changelog

All notable changes to tune-shifter will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.15.2](https://github.com/eightyeighteyes/tune-shifter/compare/v0.15.1...v0.15.2) (2026-03-18)


### Bug Fixes

* remove single-quote wrapping from -DVENV_PYTHON compiler flag ([#62](https://github.com/eightyeighteyes/tune-shifter/issues/62)) ([700b5b3](https://github.com/eightyeighteyes/tune-shifter/commit/700b5b324f166e997aa8a12902f61c867ac29aed))

## [0.15.1](https://github.com/eightyeighteyes/tune-shifter/compare/v0.15.0...v0.15.1) (2026-03-18)


### Bug Fixes

* include launcher/main.c in sdist ([#60](https://github.com/eightyeighteyes/tune-shifter/issues/60)) ([540ee13](https://github.com/eightyeighteyes/tune-shifter/commit/540ee13959ef26839b510d14b2437ce8829404a5))

## [0.15.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.14.0...v0.15.0) (2026-03-18)


### Features

* config reload fix, download status callback, format selector ([#56](https://github.com/eightyeighteyes/tune-shifter/issues/56)) ([ab9d9c8](https://github.com/eightyeighteyes/tune-shifter/commit/ab9d9c8b652297b57353e8ab1ce1774da98bc393))
* rename process to tune-shifter via setproctitle ([#59](https://github.com/eightyeighteyes/tune-shifter/issues/59)) ([b62150d](https://github.com/eightyeighteyes/tune-shifter/commit/b62150d7f625460fb2a40cf06424fcc6a0d5c7b1))
* show pipeline stage in menu bar status item ([#58](https://github.com/eightyeighteyes/tune-shifter/issues/58)) ([a2d8fdd](https://github.com/eightyeighteyes/tune-shifter/commit/a2d8fdd0e7d1f26817e1d939bb0d48b51a7c1352))

## [0.14.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.13.0...v0.14.0) (2026-03-18)


### Features

* add daemon pause/resume for internal pipeline control ([#51](https://github.com/eightyeighteyes/tune-shifter/issues/51)) ([de57e27](https://github.com/eightyeighteyes/tune-shifter/commit/de57e2769715a6b2880cfc59be3815605ee1e023))
* add MenuBarApp and --menu-bar flag for macOS menu bar daemon ([#54](https://github.com/eightyeighteyes/tune-shifter/issues/54)) ([805359e](https://github.com/eightyeighteyes/tune-shifter/commit/805359ec9309a5148dc1ae9c8281aca840cf968a))
* add rumps dep and extract DaemonCore for menu bar support ([#53](https://github.com/eightyeighteyes/tune-shifter/issues/53)) ([13ceb19](https://github.com/eightyeighteyes/tune-shifter/commit/13ceb1951a1ad269de38ba045763ee8a45d491ea))

## [0.13.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.12.1...v0.13.0) (2026-03-17)


### Features

* add stop, play, status service commands ([#49](https://github.com/eightyeighteyes/tune-shifter/issues/49)) ([6244ed8](https://github.com/eightyeighteyes/tune-shifter/commit/6244ed8736eb7377ef0ee8e08b0cecf48caaa9f0))

## [0.12.1](https://github.com/eightyeighteyes/tune-shifter/compare/v0.12.0...v0.12.1) (2026-03-17)


### Bug Fixes

* include completions/_tune-shifter in sdist ([#46](https://github.com/eightyeighteyes/tune-shifter/issues/46)) ([15dc783](https://github.com/eightyeighteyes/tune-shifter/commit/15dc7833dbe85bfee017b8edeeb59690013b0c55))

## [0.12.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.11.0...v0.12.0) (2026-03-17)


### Features

* zsh tab completion ([#44](https://github.com/eightyeighteyes/tune-shifter/issues/44)) ([eb9fb49](https://github.com/eightyeighteyes/tune-shifter/commit/eb9fb49b8f91e1bbab86da8a435755f2a272c761))

## [0.11.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.10.0...v0.11.0) (2026-03-17)


### Features

* hardcode MusicBrainz app name, remove from config ([#42](https://github.com/eightyeighteyes/tune-shifter/issues/42)) ([f709271](https://github.com/eightyeighteyes/tune-shifter/commit/f709271eb3d59d2253a99c6c4c8f09b0d262c948))

## [0.10.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.9.0...v0.10.0) (2026-03-17)


### Features

* config show/set subcommands ([#38](https://github.com/eightyeighteyes/tune-shifter/issues/38)) ([978a650](https://github.com/eightyeighteyes/tune-shifter/commit/978a65061925db0485234c560544db7feb3b9a1a))
* derive MusicBrainz app version from package, not config ([#40](https://github.com/eightyeighteyes/tune-shifter/issues/40)) ([28236e0](https://github.com/eightyeighteyes/tune-shifter/commit/28236e0bc924b4acb0a62fb47fc0bb06bd297977))
* live-reload config without daemon restart ([#41](https://github.com/eightyeighteyes/tune-shifter/issues/41)) ([283ac9e](https://github.com/eightyeighteyes/tune-shifter/commit/283ac9e20b92b384c5050ad6dda9ddd7553423cb))

## [0.9.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.8.1...v0.9.0) (2026-03-16)


### Features

* FLAC support ([#36](https://github.com/eightyeighteyes/tune-shifter/issues/36)) ([00bc8fc](https://github.com/eightyeighteyes/tune-shifter/commit/00bc8fc19756e8ce7ca4466d0b704b865db9f165))
* OGG Vorbis support ([#37](https://github.com/eightyeighteyes/tune-shifter/issues/37)) ([a4711b1](https://github.com/eightyeighteyes/tune-shifter/commit/a4711b197f6ec796ee92a9ef0c11439b8521565f))
* skip MusicBrainz lookup for already-tagged files ([#34](https://github.com/eightyeighteyes/tune-shifter/issues/34)) ([3dac571](https://github.com/eightyeighteyes/tune-shifter/commit/3dac5712881a8b4110bc2ff859f7b6cd8971b18a))

## [0.8.1](https://github.com/eightyeighteyes/tune-shifter/compare/v0.8.0...v0.8.1) (2026-03-15)


### Bug Fixes

* include USAGE.md in Poetry sdist for Homebrew formula ([#29](https://github.com/eightyeighteyes/tune-shifter/issues/29)) ([a6e1ef7](https://github.com/eightyeighteyes/tune-shifter/commit/a6e1ef76742b0abc082e4b3d259452a24817f9b2))

## [0.8.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.7.0...v0.8.0) (2026-03-15)


### Features

* enforce 95% code coverage in CI ([#26](https://github.com/eightyeighteyes/tune-shifter/issues/26)) ([fe78eb7](https://github.com/eightyeighteyes/tune-shifter/commit/fe78eb720630c97cea03bdec64e3f591109b18e0))
* migrate from setuptools to Poetry ([#27](https://github.com/eightyeighteyes/tune-shifter/issues/27)) ([7f58471](https://github.com/eightyeighteyes/tune-shifter/commit/7f5847161c25493ad4d857fffc2a204e111ba85b))
* scan staging directory for existing items on daemon start ([#22](https://github.com/eightyeighteyes/tune-shifter/issues/22)) ([cc4de25](https://github.com/eightyeighteyes/tune-shifter/commit/cc4de255966473bca23f469ce3b332e2cb817ed4))


### Bug Fixes

* prevent concurrent pipeline runs on the same staging item ([#24](https://github.com/eightyeighteyes/tune-shifter/issues/24)) ([3177906](https://github.com/eightyeighteyes/tune-shifter/commit/31779064f7e95e887447f50b782e9ede53d264ac))
* suppress noisy musicbrainzngs INFO logs ([#25](https://github.com/eightyeighteyes/tune-shifter/issues/25)) ([e27d444](https://github.com/eightyeighteyes/tune-shifter/commit/e27d44470a3755f10fe03ad81a697966e428683c))

## [0.7.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.6.0...v0.7.0) (2026-03-15)


### Features

* ask mark-synced question on first Bandcamp setup ([#20](https://github.com/eightyeighteyes/tune-shifter/issues/20)) ([a247acd](https://github.com/eightyeighteyes/tune-shifter/commit/a247acd585fa9e3c4af7eed3f21b1c88a7f14662))
* interactive Bandcamp setup when sync has no config ([#19](https://github.com/eightyeighteyes/tune-shifter/issues/19)) ([24ef672](https://github.com/eightyeighteyes/tune-shifter/commit/24ef6725aa81d87b520cfe20625094c376ad6c7f))


### Bug Fixes

* use rmtree to fully remove staging dir after ingest ([#17](https://github.com/eightyeighteyes/tune-shifter/issues/17)) ([c291518](https://github.com/eightyeighteyes/tune-shifter/commit/c29151800ddaf1a90fbf48bf7415a5f91f8d44c0))

## [0.6.0](https://github.com/eightyeighteyes/tune-shifter/compare/v0.5.0...v0.6.0) (2026-03-15)


### Features

* log version and install path when daemon starts ([#16](https://github.com/eightyeighteyes/tune-shifter/issues/16)) ([4f49ba4](https://github.com/eightyeighteyes/tune-shifter/commit/4f49ba44abc9433418f28a6cfb22fc0deb88275a))
* write full MusicBrainz tag set with exponential backoff retry ([#13](https://github.com/eightyeighteyes/tune-shifter/issues/13)) ([59af752](https://github.com/eightyeighteyes/tune-shifter/commit/59af75248daf5db8c2b3ca1dea628c3fe822744f))


### Bug Fixes

* sanitize tag values before path template rendering ([#15](https://github.com/eightyeighteyes/tune-shifter/issues/15)) ([1941bb6](https://github.com/eightyeighteyes/tune-shifter/commit/1941bb6945e010775c7020fd3375e1ad4af9fec3))

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
