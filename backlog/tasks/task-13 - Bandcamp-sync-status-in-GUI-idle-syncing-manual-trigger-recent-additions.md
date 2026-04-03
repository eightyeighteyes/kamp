---
id: TASK-13
title: 'Bandcamp sync status in GUI (idle/syncing, manual trigger, recent additions)'
status: To Do
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - ui
  - bandcamp
  - 'estimate: side'
milestone: m-1
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Surface the Bandcamp sync daemon state in the UI: show whether a sync is idle or in progress, allow the user to trigger a manual sync, and display recently added albums from the last sync.

The daemon already does the sync work — this task is purely about exposing that state through the existing WebSocket event stream and wiring up a UI component.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 UI shows sync status (idle / syncing) in real time via WebSocket
- [ ] #2 User can trigger a manual sync from the UI
- [ ] #3 Recently synced albums are surfaced after sync completes
- [ ] #4 UI handles sync errors gracefully with a visible error state
<!-- AC:END -->
