---
id: TASK-59
title: the currently playing item is static / read-only in the queue
status: Done
assignee: []
created_date: '2026-03-31 19:29'
updated_date: '2026-04-01 02:20'
labels:
  - feature
  - ui
  - 'estimate: single'
milestone: m-8
dependencies: []
priority: low
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
the currently playing item can't be dragged around in the queue
other items in the queue can be reordered around it, but it can't be moved by clicking and dragging
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Single: remove `draggable` from the current-track row in QueuePanel so the browser doesn't initiate a drag. Other rows can already be reordered around it. Add a visual cue (cursor or opacity) to signal the row is locked.
<!-- SECTION:NOTES:END -->
