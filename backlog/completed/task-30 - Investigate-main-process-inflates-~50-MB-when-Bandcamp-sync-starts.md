---
id: TASK-30
title: 'Investigate: main process inflates ~50 MB when Bandcamp sync starts'
status: Done
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-04-09 19:54'
labels:
  - bug
  - performance
  - 'estimate: side'
milestone: m-6
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**won't do**

Subprocess isolation is implemented (syncer and pipeline both spawn via `multiprocessing.get_context("spawn")`) but the main process grows from ~35 MB to ~83 MB when sync starts and stays there after sync ends. An additional ~8 MB subprocess also lingers after sync completes.

The subprocess workers themselves are not the resident cost — something in the parent or IPC setup is loading heavy modules or retaining allocations.

**Needs profiling before scoping.** Candidate causes:
- Queues / pickling overhead of passing `Config` objects
- A remaining import triggered at IPC setup time in the parent
- OS-level page retention after multiprocessing fork-related copy-on-write

**Profiling approach:** `tracemalloc`, `psutil` RSS snapshots before/after sync, `sys.modules` diff to identify what inflates memory in the parent and why it isn't released.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Root cause of ~50 MB parent process growth identified via profiling
- [ ] #2 Main process RSS after sync ends is within 5 MB of pre-sync baseline
- [ ] #3 Lingering subprocess no longer present after sync completes
<!-- AC:END -->
