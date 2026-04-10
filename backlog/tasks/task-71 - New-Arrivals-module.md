---
id: TASK-71
title: New Arrivals module
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
A Base Kamp module that surfaces albums added to the library recently (last 30 days by default). Gives users a quick \"what's new\" view without navigating the full library.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Module displays albums added within the last 30 days, sorted newest first
- [ ] #2 Empty state shown when no albums have been added recently
- [ ] #3 Clicking an album navigates to that album in the library
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Query the existing albums API filtered by `added_at` descending with a cutoff of 30 days. Render using whichever display variant the module slot supports (carousel, grid, or list — determined by TASK-73/74/75). This task only needs a working data fetch and a default display; the display variant toggle is handled by the module container from TASK-70.
<!-- SECTION:PLAN:END -->
