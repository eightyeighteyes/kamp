---
id: TASK-50
title: 'Spike: Bandcamp download link scraping without full Playwright bundle'
status: Done
assignee: []
created_date: '2026-03-31 02:46'
updated_date: '2026-04-13 22:22'
labels:
  - spike
  - bandcamp
  - 'estimate: side'
milestone: m-9
dependencies: []
priority: low
ordinal: 1500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Playwright ships ~300 MB of browser binaries that can't be cleanly bundled in a macOS .app. This spike investigates lighter alternatives before committing to an approach.

## Context

The Bandcamp syncer needs JS rendering to obtain download links — the link appears after JS executes, so plain HTTP scraping won't work. Playwright currently handles this but is impractical to bundle for distribution.

## Options to evaluate

### A. Reverse-engineer the network request
Check DevTools → Network (XHR/Fetch) while the download link appears. If Bandcamp fires an API call to populate the link, we can replicate it with `httpx` — no browser needed at all. Best outcome if feasible.

### B. Use Electron's existing Chromium
Drive a hidden `BrowserWindow` or `webContents.loadURL()` + `executeJavaScript()` in the Electron main process. Expose the result to the Python daemon via a local HTTP endpoint or IPC. Zero additional binary size. Downside: sync requires the UI to be running.

### C. macOS system WebKit (WKWebView)
Drive `WKWebView` via PyObjC (already a dependency) or a small native helper. Built into every Mac, no download. macOS-only and more implementation work than A or B.

### D. Keep Playwright, make sync an optional install
Ship the player `.app` without Playwright. A separate Homebrew formula or `kamp install-sync` command pulls in the daemon + Playwright browsers. Cleanest separation of concerns; most work for the user.

## Deliverable

A short written recommendation with findings for each option tried, a clear recommendation, and any proof-of-concept code or network traces captured during the investigation.
<!-- SECTION:DESCRIPTION:END -->
