---
id: TASK-98
title: Now Playing view should auto-hide Artist pane (if currently visible)
status: To Do
assignee: []
created_date: '2026-04-09 02:12'
updated_date: '2026-04-10 14:16'
labels:
  - bug
  - ui
  - 'estimate: single'
milestone: m-19
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
in short, the Artist panel isn't functional in the Now Playing panel, so it should always be hidden when the Now Playing panel is showing.

If it was visible in the Library panel, it should be hidden when the user shows the Now Playing panel, and then become visible again if the user shows the Library panel again.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Artist pane is always hidden when Now Playing panel is active
- [ ] #2 If Artist pane was visible before switching to Now Playing, it becomes visible again when returning to Library
- [ ] #3 No Artist pane toggle button visible in Now Playing context
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Track Artist pane visibility in a piece of state that knows which panel is active. On switch to Now Playing: record prior artist-pane state, force it hidden. On switch back to Library: restore recorded state. The simplest approach is a `wasArtistPaneOpen` ref that is set at the moment Now Playing activates and read when Library reactivates.
<!-- SECTION:PLAN:END -->
