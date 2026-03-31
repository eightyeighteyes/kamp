---
id: TASK-49
title: Self-contained macOS .app bundle (kamp server + mpv)
status: To Do
assignee: []
created_date: '2026-03-31 02:40'
updated_date: '2026-03-31 03:27'
labels:
  - packaging
  - distribution
  - macos
milestone: m-9
dependencies: []
priority: medium
ordinal: 500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Package `kamp server` and `mpv` into the Electron `.app` bundle so the app works on a fresh Mac with no manual install steps.

## Scope

Bundle only what is needed to run the music player:
- `kamp server` (FastAPI + kamp_core playback/library)
- `mpv` binary (required by MpvPlaybackEngine for audio playback)

The Bandcamp sync daemon (`kamp daemon`) and its Playwright dependency are **out of scope** for this task.

## What needs to change

### 1. Freeze `kamp server` with PyInstaller
- Create a PyInstaller spec that produces a single-file or single-dir executable for `kamp server`
- Entry point: `kamp_daemon/__main__.py` → `main()` with `server` subcommand, or a dedicated slim entry point
- Exclude Playwright, Bandcamp syncer, and other daemon-only dependencies to keep bundle size manageable
- Output goes into `kamp_ui/resources/` so electron-builder picks it up

### 2. Bundle `mpv`
- Source a static macOS `mpv` binary (e.g. from the mpv.io builds or Homebrew bottle extraction)
- Place it alongside the frozen `kamp` binary in `kamp_ui/resources/`
- Update `MpvPlaybackEngine` to accept a configurable binary path (env var or constructor arg), defaulting to the bundled location at runtime

### 3. Fix binary path discovery in Electron main
- `kamp_ui/src/main/index.ts` currently looks for `.venv/bin/kamp` relative to the dev tree
- Update `findKampBinary()` to check `process.resourcesPath` first (where electron-builder copies `resources/`), then fall back to Homebrew paths and PATH for development

### 4. Wire `mpv` path into the server
- When Electron spawns `kamp server`, pass the bundled `mpv` path via an env var (e.g. `KAMP_MPV_BIN`) so the playback engine uses the bundled binary without hardcoding paths in Python

### 5. Update electron-builder config
- Set a proper `appId` (`com.kamp.app`) and `productName` (`Kamp`)
- Add `extraResources` entry to copy the frozen `kamp` binary and `mpv` into `Contents/Resources/`
- Confirm entitlements are sufficient (hardened runtime, JIT if needed by mpv)

## Out of scope
- Code signing / notarization (separate task once bundling works)
- Windows / Linux packaging
- Bundling `kamp daemon` (Bandcamp sync) — requires Playwright, much larger effort
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A fresh Mac with no Python, no Homebrew, and no mpv can launch Kamp.app and play music after selecting a library folder
- [ ] #2 electron-builder produces a .dmg whose .app contains the kamp binary and mpv under Contents/Resources/
- [ ] #3 kamp_ui/src/main/index.ts resolves the kamp binary from process.resourcesPath when running packaged
- [ ] #4 MpvPlaybackEngine uses the bundled mpv binary when KAMP_MPV_BIN is set
- [ ] #5 The frozen kamp binary responds correctly to `kamp server` invocation inside the bundle
<!-- AC:END -->
