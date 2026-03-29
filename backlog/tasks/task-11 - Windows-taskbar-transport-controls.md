---
id: TASK-11
title: Windows taskbar transport controls
status: To Do
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-03-29 03:15'
labels:
  - feature
  - windows
  - os-integration
  - 'estimate: side'
milestone: m-3
dependencies: []
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the `MediaController` interface for Windows: populate the Windows System Media Transport Controls (SMTC) with current track info and respond to play/pause, next, previous from the taskbar and lock screen.

Per ADR-7, implement as a `WinMediaController` in the platform dispatch table.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Windows taskbar shows play/pause, next, previous controls for kamp
- [ ] #2 SMTC displays current track title, artist, album, and artwork
- [ ] #3 Media key presses are handled correctly
- [ ] #4 Implementation is isolated in WinMediaController behind the dispatch table
<!-- AC:END -->
