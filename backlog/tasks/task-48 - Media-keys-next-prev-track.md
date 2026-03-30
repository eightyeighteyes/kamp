---
id: TASK-48
title: 'Media keys: next/prev track'
status: To Do
assignee: []
created_date: '2026-03-30 23:47'
labels:
  - macos
  - media-keys
  - architecture
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Next/prev track media keys do not work. Play/pause works (handled by mpv's default input bindings). This has been attempted many times without success — the root cause is architectural, not a simple bug.

## What was tried

1. **`addTargetWithHandler:` (blocks)** — PyObjC block-signature error. Tried `__block_signature__` workaround, still failed.
2. **`addTarget:action:` (NSObject selectors, commit 2ec5927)** — Attempted but quickly reverted; exact failure not captured.
3. **Electron `globalShortcut`** — `globalShortcut.register('MediaNextTrack', ...)` posts HTTP to `/api/v1/player/next`. Never fires because macOS routes media key events to the process that owns `MPNowPlayingInfoCenter` (the Python server), not to HID listeners.
4. **`NSObject` subclass + `addTarget:action:` (this session)** — Registration appears to succeed (no exceptions), but callbacks never fire. Root cause: `MPRemoteCommandCenter` dispatches handlers on the main GCD dispatch queue. The kamp server's main thread runs asyncio/uvicorn, not a CFRunLoop. GCD blocks queued for the main thread never execute without a running CFRunLoop or explicit queue drain.
5. **`--no-input-default-bindings` on mpv** — Correctly identified that mpv was intercepting next/prev keys and mapping them to seek. But this also killed play/pause (which was working via mpv). Reverted.

## Root cause

macOS routes media key events to the process registered as Now Playing via `MPNowPlayingInfoCenter`. That process (the kamp Python server) must handle them via `MPRemoteCommandCenter`. But `MPRemoteCommandCenter` always dispatches on the main GCD queue, which requires a CFRunLoop on the main thread. asyncio/uvicorn does not run a CFRunLoop. Without draining the main GCD queue, the Python handlers are dead code even if registered correctly.

## Proposed technical direction

Move `MPNowPlayingInfoCenter` + `MPRemoteCommandCenter` entirely out of the Python server and into the **Electron main process**, which runs a proper CFRunLoop (it's an AppKit application). This requires:

- A Node.js native module (compiled Objective-C/Swift) that wraps `MPNowPlayingInfoCenter` and `MPRemoteCommandCenter`.
- The Electron process updates Now Playing info by calling this module when it receives player state updates from the server (already flowing via WebSocket).
- The module registers `nextTrackCommand` / `previousTrackCommand` handlers that call the kamp server's existing `/api/v1/player/next` and `/api/v1/player/prev` HTTP endpoints.
- Remove `CoreAudioMediaController` from the Python server (or leave as a no-op / future fallback).

**Before implementing:** consult with the Software Architect and Frontend Developer agents to validate the approach and identify the right native module strategy (e.g. `node-gyp` + ObjC, Swift Package Manager, `electron-rebuild`, pre-built binary).

## Files involved

- `kamp_core/media_controller.py` — Python Now Playing widget (remove or keep for non-Electron use)
- `kamp_ui/src/main/index.ts` — Electron main process (add native module call here)
- `kamp_daemon/__main__.py` — server startup (remove `_mc` wiring if moving to Electron)
- New: native Node.js module in `kamp_ui/native/` or similar
<!-- SECTION:DESCRIPTION:END -->
