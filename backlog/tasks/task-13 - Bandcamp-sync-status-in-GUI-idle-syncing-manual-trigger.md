---
id: TASK-13
title: 'Bandcamp sync status in GUI (idle/syncing, manual trigger)'
status: Done
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-04-24 16:01'
labels:
  - feature
  - ui
  - bandcamp
  - 'estimate: side'
milestone: m-31
dependencies: []
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Surface the Bandcamp sync daemon state in the UI: show whether a sync is idle or in progress and allow the user to trigger a manual sync.

The daemon already does the sync work — this task is purely about exposing that state through the existing WebSocket event stream and wiring up a UI component.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 UI shows sync status (idle / syncing) in real time via WebSocket
- [ ] #2 User can trigger a manual sync from the UI
- [ ] #3 UI handles sync errors gracefully with a visible error state
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Figma design spec (node 82:1830, Bandcamp logo vector in nav bar):
- Visibility: only render when user has an active Bandcamp session
- Left click: trigger manual sync; icon fades in/out (pulse animation) while sync is running
- Right click: context menu with single item 'Bandcamp options...' that opens Preferences panel on the Services tab
- Fill: #4DA9D2 (Bandcamp blue) when visible
<!-- SECTION:NOTES:END -->
