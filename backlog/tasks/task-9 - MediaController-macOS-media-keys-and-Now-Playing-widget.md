---
id: TASK-9
title: 'MediaController: macOS media keys and Now Playing widget'
status: In Progress
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-03-30 22:21'
labels:
  - feature
  - macos
  - os-integration
  - 'estimate: side'
milestone: m-0
dependencies: []
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the `MediaController` interface for macOS: respond to media keys (play/pause, next, previous) and populate the macOS Now Playing widget (Control Center / lock screen) with current track metadata and artwork.

Per ADR-7, implement as a `CoreAudioMediaController` registered in the platform dispatch table — no `if platform == "darwin"` conditionals outside the dispatch table.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Play/pause, next, previous media keys control playback
- [ ] #2 macOS Now Playing widget shows current track title, artist, album, and artwork
- [ ] #3 Seek position updates in the Now Playing widget during playback
- [ ] #4 Implementation is isolated behind the MediaController dispatch table
<!-- AC:END -->
