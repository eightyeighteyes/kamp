---
id: TASK-12
title: Linux MPRIS support
status: To Do
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-03-29 03:15'
labels:
  - feature
  - linux
  - os-integration
  - 'estimate: side'
milestone: m-4
dependencies: []
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the `MediaController` interface for Linux via MPRIS2 (D-Bus). This allows desktop environments (GNOME, KDE, etc.) to show and control kamp playback via their media widgets.

Per ADR-7, implement as an `MPRISController` in the platform dispatch table. Adding this should require touching zero files that contain Bandcamp, playback, or library logic.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 kamp exposes an MPRIS2 D-Bus interface
- [ ] #2 Desktop environment media widgets show current track and allow play/pause/next/prev
- [ ] #3 Implementation is isolated in MPRISController behind the dispatch table
- [ ] #4 No Bandcamp/playback/library files modified
<!-- AC:END -->
