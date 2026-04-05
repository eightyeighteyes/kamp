---
id: TASK-36
title: Preferences panel
status: To Do
assignee: []
created_date: '2026-03-29 14:01'
updated_date: '2026-04-05 16:45'
labels:
  - feature
  - ui
  - electron
  - 'estimate: lp'
milestone: m-21
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A centralized place to manage Kamp's config options, implemented as a modal dialog.

**Opening:** accessible from macOS menu bar (App Name → Preferences) and via Cmd/Ctrl+,

**Behaviour:** preferences take effect immediately on change — no Apply or OK button. An ephemeral confirmation indicates the preference was saved.

**Contents:** all user-facing config options with appropriate controls; library path options moved here from wherever they currently live.

**Visual:** dialog background matches the queue panel background. Dismissable via the X in the upper-right corner or Escape key.

Consult with UI Designer before implementation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GET /api/v1/config returns current config values
- [ ] #2 PATCH /api/v1/config persists changes to config.toml
- [ ] #3 All user-facing config options are represented with appropriate controls
- [ ] #4 Settings that require a restart are clearly indicated
- [ ] #5 Invalid values are rejected with a visible error before saving
- [ ] #6 Preferences dialog opens from macOS menu bar (App Name → Preferences) and via Cmd/Ctrl+,
- [ ] #7 Preferences take effect immediately on change; no Apply or OK button
- [ ] #8 An ephemeral confirmation is shown when a preference is saved
- [ ] #9 Library path options are available in the preferences dialog
- [ ] #10 Dialog background matches the queue panel background
- [ ] #11 Dialog can be dismissed with the X button or Escape key
<!-- AC:END -->
