---
id: TASK-29
title: 'redraw issue: window has visible white gutters while shrinking'
status: Done
assignee: []
created_date: '2026-03-29 17:35'
updated_date: '2026-03-30 01:33'
labels:
  - bug
  - ui
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
to repro:
increase the size of the window
decrease the size of the window

expected:
no visual artifacts while changing window dimensions

actual:
white gutters appear on the right and bottom of the window while dimensions shrink
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added `backgroundColor: '#141414'` to `BrowserWindow` options. The native Chromium surface previously defaulted to white, which showed through as gutters at the trailing edges during shrink. Value sourced from `src/shared/theme.ts`.
<!-- SECTION:FINAL_SUMMARY:END -->
