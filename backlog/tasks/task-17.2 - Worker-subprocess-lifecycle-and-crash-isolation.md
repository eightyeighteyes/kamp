---
id: TASK-17.2
title: Worker subprocess lifecycle and crash isolation
status: Done
assignee: []
created_date: '2026-04-05 16:36'
labels:
  - feature
  - architecture
  - 'estimate: side'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 1200
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the worker subprocess model for backend extensions. Each extension invocation runs inside a spawn-context subprocess. A crash in the worker quarantines the item being processed and logs the failure — it does not take down the daemon or affect other items in the queue.

Worker functions must clean up any handlers/state they add (logging, signals) so the parent process is unaffected when the worker runs inline in tests (per existing subprocess isolation pattern in the codebase).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extension methods are invoked inside spawn-context worker subprocesses
- [ ] #2 A worker crash (unhandled exception, segfault) quarantines the current item and logs the error; the daemon continues processing other items
- [ ] #3 Worker cleans up logging handlers and signal state before exit so the parent process is unaffected
- [ ] #4 Worker subprocess exits cleanly after completing its task; no zombie processes
<!-- AC:END -->
