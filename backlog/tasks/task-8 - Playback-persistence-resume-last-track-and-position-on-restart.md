---
id: TASK-8
title: Playback persistence (resume last track and position on restart)
status: To Do
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-03-29 03:14'
labels:
  - feature
  - playback
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: medium
ordinal: 1750
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When the app restarts, resume the last playing track at the last known position. The daemon owns all player state (per ADR-4), so last track/position should be persisted in the daemon (SQLite or config), not in Electron.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On restart, the last track and position are restored in the player state
- [ ] #2 Playback does not auto-resume on restart (user must press play)
- [ ] #3 State is stored in the daemon, not in Electron localStorage or electron-store
<!-- AC:END -->
