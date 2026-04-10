---
id: TASK-116
title: install kamp service on first launch
status: To Do
assignee: []
created_date: '2026-04-10 13:14'
updated_date: '2026-04-10 14:17'
labels:
  - feature
  - os-integration
  - 'estimate: side'
milestone: m-9
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Install a macOS LaunchAgent on first launch of the .app so the kamp daemon starts at login and runs in the background even when the Electron UI is not open. This allows background operations (Bandcamp sync, library watching, scrobbling) to continue without the UI running.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On first launch, a LaunchAgent plist is installed to ~/Library/LaunchAgents/
- [ ] #2 The daemon starts automatically at login without the UI running
- [ ] #3 The Electron UI connects to the already-running daemon rather than spawning a new one
- [ ] #4 Uninstalling the app removes the LaunchAgent (or documents how to do so)
- [ ] #5 No duplicate daemon processes if the UI is also open
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
**LaunchAgent plist:** Write `com.kamp.daemon.plist` to `~/Library/LaunchAgents/` pointing at the bundled `kamp` executable (`Contents/Resources/kamp/kamp daemon`). Set `RunAtLoad: true` and `KeepAlive: true`.\n\n**First-launch detection:** In the Electron main process, check whether the plist exists before trying to load it. If absent, write it and call `launchctl load` via `execFile`.\n\n**Deduplication:** The existing `startServer()` flow in `index.ts` already checks whether the server is reachable before spawning. If the LaunchAgent has already started the daemon, the health check will pass and `spawn` is skipped. No additional logic needed.\n\n**Uninstall:** Document `launchctl unload ~/Library/LaunchAgents/com.kamp.daemon.plist && rm ~/Library/LaunchAgents/com.kamp.daemon.plist` in the README. A future preferences panel can offer a \"Remove from login items\" button.\n\n**macOS constraint:** `launchctl load` from a sandboxed app may require entitlements. Since we're not App Store sandboxed, this should work from the Electron main process directly. Test on a signed build — unsigned builds on macOS 14+ may have restrictions.\n\n**Plist template:**\n```xml\n<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict>\n  <key>Label</key><string>com.kamp.daemon</string>\n  <key>ProgramArguments</key><array>\n    <string>/path/to/kamp/kamp</string>\n    <string>daemon</string>\n  </array>\n  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n  <key>StandardOutPath</key><string>~/Library/Logs/kamp/daemon.log</string>\n  <key>StandardErrorPath</key><string>~/Library/Logs/kamp/daemon-error.log</string>\n</dict></plist>\n```\nThe path must be the absolute path to the bundled executable — use `process.resourcesPath` to construct it at install time.
<!-- SECTION:PLAN:END -->
