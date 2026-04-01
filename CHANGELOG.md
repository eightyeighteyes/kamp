# Changelog

All notable changes to kamp-daemon will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.4.0](https://github.com/eightyeighteyes/kamp/compare/v1.3.0...v1.4.0) (2026-04-01)


### Features

* add album to queue and play next (TASK-42) ([#133](https://github.com/eightyeighteyes/kamp/issues/133)) ([de460a3](https://github.com/eightyeighteyes/kamp/commit/de460a3c09ac6fd80b13bf5c53f62095b9e886ca))
* add to queue, play next, and queue rearrangement (TASK-41, TASK-54) ([#132](https://github.com/eightyeighteyes/kamp/issues/132)) ([9b96ca9](https://github.com/eightyeighteyes/kamp/commit/9b96ca95ec7fc659f030dfd7e1156e0f35ec4953))
* clear queue / clear remaining via context menu (TASK-57) ([#136](https://github.com/eightyeighteyes/kamp/issues/136)) ([939e1f9](https://github.com/eightyeighteyes/kamp/commit/939e1f92940b072e7403ce2db8b29d857e5c96e5))
* collapsible queue panel (TASK-40) ([#131](https://github.com/eightyeighteyes/kamp/issues/131)) ([501962a](https://github.com/eightyeighteyes/kamp/commit/501962ac5ba5466a9d421b867e67439e1bf7f4af))
* double-click queue track to skip to it (TASK-56) ([#134](https://github.com/eightyeighteyes/kamp/issues/134)) ([198fe13](https://github.com/eightyeighteyes/kamp/commit/198fe133aa926dd8cd66a6df7e48cd7ffc1e6beb))
* lock current track in queue (not draggable) (TASK-59) ([#137](https://github.com/eightyeighteyes/kamp/issues/137)) ([12fd067](https://github.com/eightyeighteyes/kamp/commit/12fd067e8e82aa46d5d3c363af164950256f5be9))
* persist playback queue across restarts (TASK-47) ([#129](https://github.com/eightyeighteyes/kamp/issues/129)) ([71e2444](https://github.com/eightyeighteyes/kamp/commit/71e24447f7ee10e9cea8c113bbb8b280802eaa5d))


### Bug Fixes

* persist queue panel visibility ([#139](https://github.com/eightyeighteyes/kamp/issues/139)) ([19313af](https://github.com/eightyeighteyes/kamp/commit/19313affabafadfe9b63b5e56d22b135f5a8079f))
* persist sort order across app restarts (TASK-58) ([#135](https://github.com/eightyeighteyes/kamp/issues/135)) ([b03bf5a](https://github.com/eightyeighteyes/kamp/commit/b03bf5a778f540c4779dfd111cc84ee85c5e130a))

## [1.3.0](https://github.com/eightyeighteyes/kamp/compare/v1.2.0...v1.3.0) (2026-03-31)


### Features

* auto-scan library after path is selected in setup ([#112](https://github.com/eightyeighteyes/kamp/issues/112)) ([7720741](https://github.com/eightyeighteyes/kamp/commit/7720741a18b88075039701f12d25efd56335b14e))
* automatic library watching (TASK-5) ([#121](https://github.com/eightyeighteyes/kamp/issues/121)) ([199d5e0](https://github.com/eightyeighteyes/kamp/commit/199d5e05c57c0a454be217ef6d5b30f0779d96a2))
* full-text search (TASK-6) ([#124](https://github.com/eightyeighteyes/kamp/issues/124)) ([9effbc7](https://github.com/eightyeighteyes/kamp/commit/9effbc75a8794da113e90215c84e2475af469564))
* library sort by artist, album, date added, last played (TASK-43) ([#125](https://github.com/eightyeighteyes/kamp/issues/125)) ([31f7724](https://github.com/eightyeighteyes/kamp/commit/31f7724d172f458b3bf8fe100c6a184e40af9860))
* Now Playing view (TASK-7) ([#117](https://github.com/eightyeighteyes/kamp/issues/117)) ([a6d2ed1](https://github.com/eightyeighteyes/kamp/commit/a6d2ed12e7447815d0fe5c82cb745d8fac3f5a23))
* panel layout persistence and keyboard shortcuts (TASK-10) ([#120](https://github.com/eightyeighteyes/kamp/issues/120)) ([f28a2cb](https://github.com/eightyeighteyes/kamp/commit/f28a2cbfb77d2d8127c532ae640fa2d033a51376))
* Phase 2 onboarding — native library picker and scan progress bar ([#110](https://github.com/eightyeighteyes/kamp/issues/110)) ([a84b4f0](https://github.com/eightyeighteyes/kamp/commit/a84b4f0ab09d1e16203176b19311f93eb0a3755d))
* re-scan trigger in panel footer; style library picker ([#116](https://github.com/eightyeighteyes/kamp/issues/116)) ([aa19a82](https://github.com/eightyeighteyes/kamp/commit/aa19a82217ad68b785a937cb7781716d5883da0f))
* restore last track and position on daemon restart (TASK-8) ([#119](https://github.com/eightyeighteyes/kamp/issues/119)) ([f92f131](https://github.com/eightyeighteyes/kamp/commit/f92f131ab68fa6d04c3e90e8e7c5407074ac795d))
* sort order in search results (TASK-44) ([#127](https://github.com/eightyeighteyes/kamp/issues/127)) ([2a8ee9d](https://github.com/eightyeighteyes/kamp/commit/2a8ee9d30eb216add663d7ca86c4e805bb2ec150))
* UI polish, library picker, and Backlog.md setup ([#113](https://github.com/eightyeighteyes/kamp/issues/113)) ([6617187](https://github.com/eightyeighteyes/kamp/commit/66171877f23d222c0a4ba6c1ac6f94a441fcce6e))


### Bug Fixes

* reconnect WebSocket with backoff after sleep/wake ([#114](https://github.com/eightyeighteyes/kamp/issues/114)) ([920b8e4](https://github.com/eightyeighteyes/kamp/commit/920b8e482e4cd9db85705581381def4f7ccb5ec4))
* rename Tune-Shifter to Kamp in menu bar and notifications ([#115](https://github.com/eightyeighteyes/kamp/issues/115)) ([2879ae4](https://github.com/eightyeighteyes/kamp/commit/2879ae426996eecc96649f91fbd7cb8b99143cbf))
* stop pauses and seeks to start instead of unloading file (TASK-38) ([#118](https://github.com/eightyeighteyes/kamp/issues/118)) ([3593547](https://github.com/eightyeighteyes/kamp/commit/3593547af6e5d264a63e9cbdcdf5e3d0da97b7ac))
* window bounds persistence and white gutter on resize (TASK-29, TASK-37) ([#122](https://github.com/eightyeighteyes/kamp/issues/122)) ([b0a4c50](https://github.com/eightyeighteyes/kamp/commit/b0a4c50aafacbf3f1743b86b655d6305a257ec04))

## [1.2.0](https://github.com/eightyeighteyes/kamp/compare/v1.1.1...v1.2.0) (2026-03-28)


### Features

* album art ([#105](https://github.com/eightyeighteyes/kamp/issues/105)) ([f7945f8](https://github.com/eightyeighteyes/kamp/commit/f7945f8e67bd63e43f3876d914f43441a7ab0ae9))
* Electron + React UI (kamp Phase 1) ([#104](https://github.com/eightyeighteyes/kamp/issues/104)) ([4c68463](https://github.com/eightyeighteyes/kamp/commit/4c684634198aa8511ab37684ff19560369ff9fdd))
* error states for server offline and reconnecting ([#106](https://github.com/eightyeighteyes/kamp/issues/106)) ([5f84472](https://github.com/eightyeighteyes/kamp/commit/5f84472c2e28958afab1fe9c599947add5f28d31))
* FastAPI REST + WebSocket server (kamp_core Phase 1) ([#103](https://github.com/eightyeighteyes/kamp/issues/103)) ([84aca6b](https://github.com/eightyeighteyes/kamp/commit/84aca6b0ef19144ac4ce6ea87d6e1ca0847fe1c9))
* first-run setup screen with library scan ([#107](https://github.com/eightyeighteyes/kamp/issues/107)) ([be935a7](https://github.com/eightyeighteyes/kamp/commit/be935a7f64e4052acc4bf95a8958d35c0802a782))
* PlaybackEngine and PlaybackQueue (kamp_core Phase 1) ([#102](https://github.com/eightyeighteyes/kamp/issues/102)) ([64a262e](https://github.com/eightyeighteyes/kamp/commit/64a262e39c7c8aa52b2c8ad236fd73ac3ffa39f8))
* SQLite library index and scanner (kamp_core Phase 1) ([#100](https://github.com/eightyeighteyes/kamp/issues/100)) ([7e866d6](https://github.com/eightyeighteyes/kamp/commit/7e866d62f7a4253a43fbf047b3714276b73a4be7))


### Bug Fixes

* enable WAL journal mode to prevent database is locked errors ([#108](https://github.com/eightyeighteyes/kamp/issues/108)) ([ff4a30d](https://github.com/eightyeighteyes/kamp/commit/ff4a30d81edf85730661c168a10125b4a4ffc427))

## [1.1.1](https://github.com/eightyeighteyes/kamp/compare/v1.1.0...v1.1.1) (2026-03-27)


### Bug Fixes

* homebrew install sometimes points to pyenv shim ([#98](https://github.com/eightyeighteyes/kamp/issues/98)) ([c052a7d](https://github.com/eightyeighteyes/kamp/commit/c052a7d9b34b4b88772ef409bf0ff5223f0d1c1a))

## [1.1.0](https://github.com/eightyeighteyes/kamp/compare/v1.0.2...v1.1.0) (2026-03-23)


### Features

* AcoustID fingerprint-based release lookup (Tier 0) ([#96](https://github.com/eightyeighteyes/kamp/issues/96)) ([f460380](https://github.com/eightyeighteyes/kamp/commit/f460380a5ceb76dcf22dbc5795e719e1bf57712c))

## [1.0.2](https://github.com/eightyeighteyes/kamp/compare/v1.0.1...v1.0.2) (2026-03-23)


### Bug Fixes

* run first-run setup when config missing before install-service ([#94](https://github.com/eightyeighteyes/kamp/issues/94)) ([9ef9ed5](https://github.com/eightyeighteyes/kamp/commit/9ef9ed5acf36da14ebe39ea52fb946b60785cda5))

## [1.0.1](https://github.com/eightyeighteyes/kamp-daemon/compare/v1.0.0...v1.0.1) (2026-03-23)


### Bug Fixes

* use joinphrase for multi-artist releases ([#89](https://github.com/eightyeighteyes/kamp-daemon/issues/89)) ([7a524f7](https://github.com/eightyeighteyes/kamp-daemon/commit/7a524f76926e9dde1cc6934c13ea580357ffefd5))

## [1.0.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.18.0...v1.0.0) (2026-03-21)


### Bug Fixes

* suppress asyncio and PIL.TiffImagePlugin debug log noise ([#87](https://github.com/eightyeighteyes/kamp-daemon/issues/87)) ([0d20c61](https://github.com/eightyeighteyes/kamp-daemon/commit/0d20c6162cb666a89a397e44436caf8f85b01a2f))

## [0.18.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.17.0...v0.18.0) (2026-03-21)


### Features

* **artwork:** compress oversized CAA images instead of skipping ([#83](https://github.com/eightyeighteyes/kamp-daemon/issues/83)) ([56e6b14](https://github.com/eightyeighteyes/kamp-daemon/commit/56e6b146d0e683b8ea87e233b028b02dde192d74))
* **menu-bar:** add Sync Frequency submenu with radio-style checkmarks ([#81](https://github.com/eightyeighteyes/kamp-daemon/issues/81)) ([6ff797f](https://github.com/eightyeighteyes/kamp-daemon/commit/6ff797f344fc2fe45dadd65c4a7e8db2c5b0ee11))
* **tagger:** per-track MusicBrainz lookup to fix track-count edition mismatches ([#79](https://github.com/eightyeighteyes/kamp-daemon/issues/79)) ([29db197](https://github.com/eightyeighteyes/kamp-daemon/commit/29db197bb7defa8a68462d3c54ec680c77098274))

## [0.17.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.16.1...v0.17.0) (2026-03-21)


### Features

* **logout:** add kamp-daemon logout command and menu bar item ([#77](https://github.com/eightyeighteyes/kamp-daemon/issues/77)) ([ca18a37](https://github.com/eightyeighteyes/kamp-daemon/commit/ca18a379a940f16ffe772a6e75a1de064799a70e))
* **notifications:** macOS notifications for pipeline and sync failures ([#75](https://github.com/eightyeighteyes/kamp-daemon/issues/75)) ([194c0f3](https://github.com/eightyeighteyes/kamp-daemon/commit/194c0f3ca567c52a72b60680c88df2c00046d343))
* **syncer:** auto-mark collection on first sync; add --download-all ([#78](https://github.com/eightyeighteyes/kamp-daemon/issues/78)) ([be19a70](https://github.com/eightyeighteyes/kamp-daemon/commit/be19a705268375aa2a6270a660315cbae8c919b0))

## [0.16.1](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.16.0...v0.16.1) (2026-03-21)


### Bug Fixes

* **menu_bar,syncer:** wire status_callback at init; suppress library log noise ([#73](https://github.com/eightyeighteyes/kamp-daemon/issues/73)) ([722b202](https://github.com/eightyeighteyes/kamp-daemon/commit/722b202ee205b59afc96b31ff28dc2a728488ba7))

## [0.16.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.15.3...v0.16.0) (2026-03-19)


### Features

* **artwork:** skip CAA fetch when all files have qualifying embedded art ([#67](https://github.com/eightyeighteyes/kamp-daemon/issues/67)) ([e9dc2b8](https://github.com/eightyeighteyes/kamp-daemon/commit/e9dc2b84e72480117db3300a89058d5ecd1b68e6))
* **pipeline:** schedule individual audio files dropped into staging ([#69](https://github.com/eightyeighteyes/kamp-daemon/issues/69)) ([b11f432](https://github.com/eightyeighteyes/kamp-daemon/commit/b11f432be2c3923383799bbc7a1526be12a3ac63))


### Performance Improvements

* **pipeline:** isolate mutagen/musicbrainzngs/PIL/requests in subprocess ([#71](https://github.com/eightyeighteyes/kamp-daemon/issues/71)) ([cd4e146](https://github.com/eightyeighteyes/kamp-daemon/commit/cd4e146810972dbd09aa62a48d9e075d080bbc90))
* **syncer:** isolate playwright in subprocess for true memory release ([#70](https://github.com/eightyeighteyes/kamp-daemon/issues/70)) ([6ca6602](https://github.com/eightyeighteyes/kamp-daemon/commit/6ca6602f64df8a260e8efacd2de44f52e5979128))

## [0.15.3](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.15.2...v0.15.3) (2026-03-18)


### Bug Fixes

* compile launcher to buildpath then bin.install ([#64](https://github.com/eightyeighteyes/kamp-daemon/issues/64)) ([f8bb106](https://github.com/eightyeighteyes/kamp-daemon/commit/f8bb10679386db6ae8e62186366673420f1295ab))

## [0.15.2](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.15.1...v0.15.2) (2026-03-18)


### Bug Fixes

* remove single-quote wrapping from -DVENV_PYTHON compiler flag ([#62](https://github.com/eightyeighteyes/kamp-daemon/issues/62)) ([700b5b3](https://github.com/eightyeighteyes/kamp-daemon/commit/700b5b324f166e997aa8a12902f61c867ac29aed))

## [0.15.1](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.15.0...v0.15.1) (2026-03-18)


### Bug Fixes

* include launcher/main.c in sdist ([#60](https://github.com/eightyeighteyes/kamp-daemon/issues/60)) ([540ee13](https://github.com/eightyeighteyes/kamp-daemon/commit/540ee13959ef26839b510d14b2437ce8829404a5))

## [0.15.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.14.0...v0.15.0) (2026-03-18)


### Features

* config reload fix, download status callback, format selector ([#56](https://github.com/eightyeighteyes/kamp-daemon/issues/56)) ([ab9d9c8](https://github.com/eightyeighteyes/kamp-daemon/commit/ab9d9c8b652297b57353e8ab1ce1774da98bc393))
* rename process to kamp-daemon via setproctitle ([#59](https://github.com/eightyeighteyes/kamp-daemon/issues/59)) ([b62150d](https://github.com/eightyeighteyes/kamp-daemon/commit/b62150d7f625460fb2a40cf06424fcc6a0d5c7b1))
* show pipeline stage in menu bar status item ([#58](https://github.com/eightyeighteyes/kamp-daemon/issues/58)) ([a2d8fdd](https://github.com/eightyeighteyes/kamp-daemon/commit/a2d8fdd0e7d1f26817e1d939bb0d48b51a7c1352))

## [0.14.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.13.0...v0.14.0) (2026-03-18)


### Features

* add daemon pause/resume for internal pipeline control ([#51](https://github.com/eightyeighteyes/kamp-daemon/issues/51)) ([de57e27](https://github.com/eightyeighteyes/kamp-daemon/commit/de57e2769715a6b2880cfc59be3815605ee1e023))
* add MenuBarApp and --menu-bar flag for macOS menu bar daemon ([#54](https://github.com/eightyeighteyes/kamp-daemon/issues/54)) ([805359e](https://github.com/eightyeighteyes/kamp-daemon/commit/805359ec9309a5148dc1ae9c8281aca840cf968a))
* add rumps dep and extract DaemonCore for menu bar support ([#53](https://github.com/eightyeighteyes/kamp-daemon/issues/53)) ([13ceb19](https://github.com/eightyeighteyes/kamp-daemon/commit/13ceb1951a1ad269de38ba045763ee8a45d491ea))

## [0.13.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.12.1...v0.13.0) (2026-03-17)


### Features

* add stop, play, status service commands ([#49](https://github.com/eightyeighteyes/kamp-daemon/issues/49)) ([6244ed8](https://github.com/eightyeighteyes/kamp-daemon/commit/6244ed8736eb7377ef0ee8e08b0cecf48caaa9f0))

## [0.12.1](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.12.0...v0.12.1) (2026-03-17)


### Bug Fixes

* include completions/_kamp-daemon in sdist ([#46](https://github.com/eightyeighteyes/kamp-daemon/issues/46)) ([15dc783](https://github.com/eightyeighteyes/kamp-daemon/commit/15dc7833dbe85bfee017b8edeeb59690013b0c55))

## [0.12.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.11.0...v0.12.0) (2026-03-17)


### Features

* zsh tab completion ([#44](https://github.com/eightyeighteyes/kamp-daemon/issues/44)) ([eb9fb49](https://github.com/eightyeighteyes/kamp-daemon/commit/eb9fb49b8f91e1bbab86da8a435755f2a272c761))

## [0.11.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.10.0...v0.11.0) (2026-03-17)


### Features

* hardcode MusicBrainz app name, remove from config ([#42](https://github.com/eightyeighteyes/kamp-daemon/issues/42)) ([f709271](https://github.com/eightyeighteyes/kamp-daemon/commit/f709271eb3d59d2253a99c6c4c8f09b0d262c948))

## [0.10.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.9.0...v0.10.0) (2026-03-17)


### Features

* config show/set subcommands ([#38](https://github.com/eightyeighteyes/kamp-daemon/issues/38)) ([978a650](https://github.com/eightyeighteyes/kamp-daemon/commit/978a65061925db0485234c560544db7feb3b9a1a))
* derive MusicBrainz app version from package, not config ([#40](https://github.com/eightyeighteyes/kamp-daemon/issues/40)) ([28236e0](https://github.com/eightyeighteyes/kamp-daemon/commit/28236e0bc924b4acb0a62fb47fc0bb06bd297977))
* live-reload config without daemon restart ([#41](https://github.com/eightyeighteyes/kamp-daemon/issues/41)) ([283ac9e](https://github.com/eightyeighteyes/kamp-daemon/commit/283ac9e20b92b384c5050ad6dda9ddd7553423cb))

## [0.9.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.8.1...v0.9.0) (2026-03-16)


### Features

* FLAC support ([#36](https://github.com/eightyeighteyes/kamp-daemon/issues/36)) ([00bc8fc](https://github.com/eightyeighteyes/kamp-daemon/commit/00bc8fc19756e8ce7ca4466d0b704b865db9f165))
* OGG Vorbis support ([#37](https://github.com/eightyeighteyes/kamp-daemon/issues/37)) ([a4711b1](https://github.com/eightyeighteyes/kamp-daemon/commit/a4711b197f6ec796ee92a9ef0c11439b8521565f))
* skip MusicBrainz lookup for already-tagged files ([#34](https://github.com/eightyeighteyes/kamp-daemon/issues/34)) ([3dac571](https://github.com/eightyeighteyes/kamp-daemon/commit/3dac5712881a8b4110bc2ff859f7b6cd8971b18a))

## [0.8.1](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.8.0...v0.8.1) (2026-03-15)


### Bug Fixes

* include USAGE.md in Poetry sdist for Homebrew formula ([#29](https://github.com/eightyeighteyes/kamp-daemon/issues/29)) ([a6e1ef7](https://github.com/eightyeighteyes/kamp-daemon/commit/a6e1ef76742b0abc082e4b3d259452a24817f9b2))

## [0.8.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.7.0...v0.8.0) (2026-03-15)


### Features

* enforce 95% code coverage in CI ([#26](https://github.com/eightyeighteyes/kamp-daemon/issues/26)) ([fe78eb7](https://github.com/eightyeighteyes/kamp-daemon/commit/fe78eb720630c97cea03bdec64e3f591109b18e0))
* migrate from setuptools to Poetry ([#27](https://github.com/eightyeighteyes/kamp-daemon/issues/27)) ([7f58471](https://github.com/eightyeighteyes/kamp-daemon/commit/7f5847161c25493ad4d857fffc2a204e111ba85b))
* scan staging directory for existing items on daemon start ([#22](https://github.com/eightyeighteyes/kamp-daemon/issues/22)) ([cc4de25](https://github.com/eightyeighteyes/kamp-daemon/commit/cc4de255966473bca23f469ce3b332e2cb817ed4))


### Bug Fixes

* prevent concurrent pipeline runs on the same staging item ([#24](https://github.com/eightyeighteyes/kamp-daemon/issues/24)) ([3177906](https://github.com/eightyeighteyes/kamp-daemon/commit/31779064f7e95e887447f50b782e9ede53d264ac))
* suppress noisy musicbrainzngs INFO logs ([#25](https://github.com/eightyeighteyes/kamp-daemon/issues/25)) ([e27d444](https://github.com/eightyeighteyes/kamp-daemon/commit/e27d44470a3755f10fe03ad81a697966e428683c))

## [0.7.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.6.0...v0.7.0) (2026-03-15)


### Features

* ask mark-synced question on first Bandcamp setup ([#20](https://github.com/eightyeighteyes/kamp-daemon/issues/20)) ([a247acd](https://github.com/eightyeighteyes/kamp-daemon/commit/a247acd585fa9e3c4af7eed3f21b1c88a7f14662))
* interactive Bandcamp setup when sync has no config ([#19](https://github.com/eightyeighteyes/kamp-daemon/issues/19)) ([24ef672](https://github.com/eightyeighteyes/kamp-daemon/commit/24ef6725aa81d87b520cfe20625094c376ad6c7f))


### Bug Fixes

* use rmtree to fully remove staging dir after ingest ([#17](https://github.com/eightyeighteyes/kamp-daemon/issues/17)) ([c291518](https://github.com/eightyeighteyes/kamp-daemon/commit/c29151800ddaf1a90fbf48bf7415a5f91f8d44c0))

## [0.6.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.5.0...v0.6.0) (2026-03-15)


### Features

* log version and install path when daemon starts ([#16](https://github.com/eightyeighteyes/kamp-daemon/issues/16)) ([4f49ba4](https://github.com/eightyeighteyes/kamp-daemon/commit/4f49ba44abc9433418f28a6cfb22fc0deb88275a))
* write full MusicBrainz tag set with exponential backoff retry ([#13](https://github.com/eightyeighteyes/kamp-daemon/issues/13)) ([59af752](https://github.com/eightyeighteyes/kamp-daemon/commit/59af75248daf5db8c2b3ca1dea628c3fe822744f))


### Bug Fixes

* sanitize tag values before path template rendering ([#15](https://github.com/eightyeighteyes/kamp-daemon/issues/15)) ([1941bb6](https://github.com/eightyeighteyes/kamp-daemon/commit/1941bb6945e010775c7020fd3375e1ad4af9fec3))

## [0.5.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.4.0...v0.5.0) (2026-03-15)


### Features

* interactive first-run configuration setup ([91365a6](https://github.com/eightyeighteyes/kamp-daemon/commit/91365a69be44323cae646c878513877dd6425577))
* interactive first-run configuration setup ([a7f62ac](https://github.com/eightyeighteyes/kamp-daemon/commit/a7f62acfa883da8a8bfb086fff5347bd857a0c88))


### Documentation

* update USAGE.md for interactive first-run setup ([e318da9](https://github.com/eightyeighteyes/kamp-daemon/commit/e318da90095bece1e56ed18dda896e2852fab0ca))

## [0.4.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.3.0...v0.4.0) (2026-03-15)


### Features

* prefer bundled artwork from archive over Cover Art Archive ([88ad795](https://github.com/eightyeighteyes/kamp-daemon/commit/88ad7954fad1dd244c1f330b845b9745724f90b4))
* prefer bundled artwork from archive over Cover Art Archive ([ef33820](https://github.com/eightyeighteyes/kamp-daemon/commit/ef33820f93dcdcc4ac8fa76fd0d7a2829a4092d6))


### Bug Fixes

* add get_release_by_id mock to pipeline tests ([27169c9](https://github.com/eightyeighteyes/kamp-daemon/commit/27169c9183f1418d932440b93a299098ff939de6))
* fetch full release details and fall back to release-group artwork ([e37ca19](https://github.com/eightyeighteyes/kamp-daemon/commit/e37ca19442d8217175f2ddaa7fec5297b530c120))
* M4A tagging — full release details and release-group artwork fallback ([7879e74](https://github.com/eightyeighteyes/kamp-daemon/commit/7879e742c8a2a0c1147e595cb3968a7e7b15c3c1))

## [0.3.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.2.3...v0.3.0) (2026-03-15)


### Features

* handle macOS FSEvents coalescing for M4A folder ingest ([7fbfc7e](https://github.com/eightyeighteyes/kamp-daemon/commit/7fbfc7efeafe33fa5a2403224e1dfda3317108a6))
* handle macOS FSEvents coalescing for M4A folder ingest ([726b506](https://github.com/eightyeighteyes/kamp-daemon/commit/726b506ae39d47c8e2ef367e6ac441d2c7d455db))
* handle macOS FSEvents coalescing for M4A folder ingest  on_modified handler scans staging root on DirModifiedEvent so folders dragged in via Finder are processed even when FSEvents drops the DirCreatedEvent/DirMovedEvent. Also retries MusicBrainz searches with edition suffixes stripped (Deluxe Edition, Remastered, etc.). ([7fbfc7e](https://github.com/eightyeighteyes/kamp-daemon/commit/7fbfc7efeafe33fa5a2403224e1dfda3317108a6))

## [Unreleased]

### Added

- `on_moved` handler in watcher: items dragged into staging on the same filesystem (macOS Finder) are now scheduled for processing
- `on_modified` handler in watcher: fixes macOS FSEvents coalescing drag-and-drop renames into `DirModifiedEvent` on the staging parent instead of emitting `DirCreatedEvent`/`DirMovedEvent` for the new item — the root cause of M4A folders being silently ignored
- MusicBrainz edition-suffix retry in tagger: album names with iTunes suffixes like "(Deluxe Edition)" or "(Remastered)" are stripped and retried once before failing, so those releases are matched correctly

---

## [0.2.3](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.2.2...v0.2.3) (2026-03-15)


### Bug Fixes

* install dependencies so they are in virtualenv after brew install ([048298c](https://github.com/eightyeighteyes/kamp-daemon/commit/048298c146c7272147defa00a9b123cccd9da2f2))

## [0.2.2](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.2.1...v0.2.2) (2026-03-15)


### Bug Fixes

* homebrew symlink doesn't exist ([046c19d](https://github.com/eightyeighteyes/kamp-daemon/commit/046c19d62e3f51df2d11df3840716594d1163b58))

## [0.2.1](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.2.0...v0.2.1) (2026-03-15)


### Bug Fixes

* work around release please trigger limitation ([09f3f05](https://github.com/eightyeighteyes/kamp-daemon/commit/09f3f05a2740cb993e52f217b77773d138a47e34))

## [Unreleased]


---

## [0.2.0](https://github.com/eightyeighteyes/kamp-daemon/compare/v0.1.0...v0.2.0) (2026-03-15)

### Added

- **GitHub Actions CI** — runs `black`, `mypy`, and `pytest` on every push to `main` and on pull requests
- **Homebrew distribution** — `brew tap eightyeighteyes/kamp-daemon && brew install kamp-daemon` installs the app; USAGE.md is displayed inline after install via formula `caveats`
- **Automated releases via Release Please** — merging a Release PR (opened automatically from Conventional Commits) tags the release, builds and attaches the sdist, and syncs the Homebrew formula; `pyproject.toml` version is bumped automatically

### Features

* add release please ([75d8180](https://github.com/eightyeighteyes/kamp-daemon/commit/75d81801d702400ffdc76cf73136ce842c63dbde))

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
- **Background daemon** — `kamp-daemon daemon` runs the watcher and Bandcamp poller together; handles `SIGINT` and `SIGTERM` for clean shutdown
- **macOS service installation** — `kamp-daemon install-service` registers the daemon as a launchd user agent that starts at login and restarts on crash; `kamp-daemon uninstall-service` removes it
- **One-shot sync** — `kamp-daemon sync` downloads new purchases to staging and exits, for use without the daemon
- **TOML configuration** — config file written to `~/.config/kamp-daemon/config.toml` on first run with sensible defaults; all paths, MusicBrainz contact, artwork constraints, library template, and Bandcamp options are configurable
- **MP3 and AAC/M4A support**

[Unreleased]: https://github.com/eightyeighteyes/kamp-daemon/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eightyeighteyes/kamp-daemon/releases/tag/v0.1.0
