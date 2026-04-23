---
id: TASK-135
title: 'Media keys: next/prev/play/pause on Windows'
status: To Do
assignee: []
created_date: '2026-04-17 00:05'
updated_date: '2026-04-23 14:16'
labels:
  - feature
  - windows
  - os-integration
  - 'estimate: lp'
milestone: m-3
dependencies:
  - TASK-48
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-48 implements next/prev/play/pause media keys on macOS using a Swift CLI helper that owns `MPNowPlayingInfoCenter` + `MPRemoteCommandCenter`. That approach is macOS-only.

## Windows equivalent

On Windows, media key integration uses the **System Media Transport Controls (SMTC)** API (`Windows.Media.SystemMediaTransportControls`). This is the Windows equivalent of macOS `MPNowPlayingInfoCenter` + `MPRemoteCommandCenter`.

## Approach options to evaluate

**Option A: Native Node.js addon (node-gyp + C++/WinRT)**
- Wrap SMTC via WinRT C++ in a `.node` addon
- Load from Electron main process
- Updates Now Playing widget in Action Center; receives play/pause/next/prev callbacks

**Option B: PowerShell or C# helper process (stdio JSON)**
- A small compiled C# CLI tool using `Windows.Media.SystemMediaTransportControls`
- Communicates over stdin/stdout JSON (same protocol as the macOS Swift helper)
- Pre-compiled `.exe`, bundled in `resources/`

**Option C: Electron built-in `globalShortcut`**
- On Windows, `globalShortcut.register('MediaNextTrack', ...)` may work as a fallback since SMTC ownership is less strictly enforced than macOS
- No Action Center Now Playing widget integration, but simpler

## Reference
- TASK-48 — macOS implementation (Swift helper approach)
- SMTC docs: https://docs.microsoft.com/en-us/uwp/api/windows.media.systemmediatransportcontrols

## Acceptance criteria
- Next/prev/play/pause media keys work on Windows
- Now Playing widget in Windows Action Center shows current track metadata (title, artist, album)
<!-- SECTION:DESCRIPTION:END -->
