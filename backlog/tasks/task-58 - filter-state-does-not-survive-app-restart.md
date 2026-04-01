---
id: TASK-58
title: filter state does not survive app restart
status: Done
assignee: []
created_date: '2026-03-31 17:51'
updated_date: '2026-03-31 23:50'
labels:
  - bug
  - 'estimate: single'
milestone: m-8
dependencies: []
priority: low
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
to repro:
open kamp ui: app opens in Sort By Artist
sort by Last Played
close kamp ui
open kamp ui

expected:
app opens in Sort Last Played

actual:
app opens in Sort By Artist
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Single: sort order already flows through the store but is never persisted. Needs the same DB treatment as `ui_active_view` — save on change, restore in `loadUiState`.
<!-- SECTION:NOTES:END -->
