---
id: TASK-56
title: double-clicking on a track in the queue advances the queue to that track
status: Done
assignee: []
created_date: '2026-03-31 17:39'
updated_date: '2026-03-31 22:13'
labels:
  - feature
  - ui
  - 'estimate: single'
milestone: m-8
dependencies: []
priority: medium
ordinal: 6000
---

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Single: `onDoubleClick` on queue rows + a new `skip_to` endpoint that sets `_pos` directly; no new UI chrome needed.
<!-- SECTION:NOTES:END -->
