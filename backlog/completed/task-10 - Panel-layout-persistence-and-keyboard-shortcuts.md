---
id: TASK-10
title: Panel layout persistence and keyboard shortcuts
status: Done
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-03-30 01:30'
labels:
  - feature
  - ui
  - 'estimate: side'
milestone: m-0
dependencies: []
priority: medium
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Persist the user's panel layout (panel positions, sizes, visibility) across restarts. Add keyboard shortcuts for common actions (play/pause, next, previous, search focus, view switching).

Layout state should be stored in `config.toml` on the daemon side, not in Electron (per ADR-4).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Panel layout is restored on app restart
- [ ] #2 Layout is stored in config.toml, not in electron-store or localStorage
- [ ] #3 Keyboard shortcuts cover: play/pause, next, previous, search focus, view switch
- [ ] #4 Shortcuts are documented in the UI (e.g. tooltip or help overlay)
<!-- AC:END -->
