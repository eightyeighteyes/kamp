---
id: TASK-73
title: Carousel Base Kamp module view
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
A horizontal scrolling carousel display variant for Base Kamp modules. Shows album art cards in a single row with left/right scroll controls.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Module content renders as a horizontally scrollable row of album art cards
- [ ] #2 Left/right arrow controls are visible and functional
- [ ] #3 Keyboard arrow keys scroll the carousel when it has focus
- [ ] #4 Works with any Base Kamp module that opts into the carousel variant
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Implement as a display wrapper component `<CarouselView items={...} />` that the module container from TASK-70 can select. Use CSS `overflow-x: auto` with `scroll-snap-type` for smooth snapping — no external carousel library needed. Arrow buttons call `scrollBy` on the container ref. This is purely a presentational component; data fetching stays in the module.
<!-- SECTION:PLAN:END -->
