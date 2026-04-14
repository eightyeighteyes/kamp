---
id: TASK-17.6
title: library.write — named atomic mutations on KampGround
status: Done
assignee: []
created_date: '2026-04-06 01:26'
updated_date: '2026-04-06 11:26'
labels:
  - feature
  - 'estimate: side'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expose `library.write` capability on KampGround as named atomic mutations (`update_metadata`, `set_artwork`). Extensions must never have raw database access — all writes go through these methods, which the host executes on behalf of the extension after the worker returns.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 KampGround.update_metadata(mbid, fields) queues a metadata update to be applied by the host after the worker completes
- [x] #2 KampGround.set_artwork(mbid, artwork_result) queues an artwork write to be applied by the host after the worker completes
- [x] #3 Mutations are collected as a list on KampGround and returned to the host via the result queue; the host applies them — no direct DB access from the worker subprocess
- [x] #4 Extensions cannot access the database directly through KampGround
<!-- AC:END -->
