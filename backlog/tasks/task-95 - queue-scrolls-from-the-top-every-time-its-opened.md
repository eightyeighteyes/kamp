---
id: TASK-95
title: queue scrolls from the top every time it's opened
status: To Do
assignee: []
created_date: '2026-04-08 16:58'
updated_date: '2026-04-10 14:16'
labels:
  - bug
  - ui
  - 'estimate: single'
milestone: m-26
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
this is visually distracting: it should just open at the position it wants to scroll to
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Queue panel opens at the currently playing track position, not the top of the list
- [ ] #2 Scroll position is stable — no visible jump or animation on open
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Preserve scroll position in React state (or a ref) so the queue panel mounts at the right offset. If the queue is virtualised, use the virtualiser's `scrollToIndex` on mount pointing at the active track rather than defaulting to 0. The key constraint: don't scroll at all if the active track is already visible.
<!-- SECTION:PLAN:END -->
