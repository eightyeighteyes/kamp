---
id: TASK-79
title: check window boundaries for context menus
status: Done
assignee: []
created_date: '2026-04-03 13:01'
updated_date: '2026-04-03 17:52'
labels:
  - bug
  - ui
  - 'estimate: single'
milestone: m-20
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
when a context menu is opened close to the right border of the screen, it gets clipped by the edge of the window

if the context menu size will go off the edge of the window's x-axis boundary, it should open with an inverted x-axis (to the left of the cursor instead of the right).

if the context menu size will go off the edge of the window's y-axis boundary, it should open with an inverted y-axis (above the cursor instead of below it).
<!-- SECTION:DESCRIPTION:END -->
