---
id: TASK-72
title: Last Played module
status: To Do
assignee: []
created_date: '2026-04-03 04:24'
updated_date: '2026-04-10 14:17'
labels:
  - feature
  - ui
  - 'estimate: side'
milestone: m-24
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A Base Kamp module that shows the albums the user has played most recently. A quick way to resume listening without searching.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Module displays the N most recently played albums (N configurable, default 10), sorted by last play time descending
- [ ] #2 Empty state shown when no play history exists yet
- [ ] #3 Clicking an album navigates to it in the library
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Requires a `last_played_at` field on albums (either tracked in the existing database or derived from a play history table). Check whether Last.fm scrobble data (TASK-27, now done) already records this — if so, derive `last_played_at` from the scrobble log rather than adding a new field. Render in the module container from TASK-70.
<!-- SECTION:PLAN:END -->
