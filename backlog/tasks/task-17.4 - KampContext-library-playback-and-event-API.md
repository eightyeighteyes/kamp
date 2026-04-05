---
id: TASK-17.4
title: 'KampContext library, playback, and event API'
status: To Do
assignee: []
created_date: '2026-04-05 16:36'
labels:
  - feature
  - architecture
  - 'estimate: lp'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 1400
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Define and implement the main KampContext API surface that extensions use to interact with the daemon: library queries, playback control, and event subscription.

Per the architecture invariant: do not design this API in the abstract. Implement it incrementally as TASK-18 (refactoring built-in extensions) reveals what is actually needed. The surface should be extracted from two real working extensions, not specced upfront.

This is the largest subtask of TASK-17 and should be worked in parallel with TASK-18.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 KampContext exposes library query methods sufficient for the MusicBrainz tagger and artwork fetcher to do their work
- [ ] #2 KampContext exposes playback control and state query methods
- [ ] #3 KampContext exposes an event subscription mechanism for daemon lifecycle events
- [ ] #4 All API methods are typed and documented with examples
- [ ] #5 No method on KampContext returns a file path, database cursor, or internal daemon object
<!-- AC:END -->
