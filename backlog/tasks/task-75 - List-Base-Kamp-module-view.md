---
id: TASK-75
title: List Base Kamp module view
status: To Do
assignee: []
created_date: '2026-04-03 04:25'
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
A compact list display variant for Base Kamp modules. Shows albums as rows with art thumbnail, title, and artist — more information density than carousel or grid.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Module content renders as a vertical list of rows with thumbnail, title, and artist
- [ ] #2 Works with any Base Kamp module that opts into the list variant
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
A `<ListView items={...} />` wrapper. Each row: small square art thumbnail (48px), album title, artist name. No external dependencies. Purely presentational — pairs with the module container from TASK-70.
<!-- SECTION:PLAN:END -->
