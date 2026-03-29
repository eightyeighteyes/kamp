---
id: TASK-5
title: Automatic library watching
status: To Do
assignee: []
created_date: '2026-03-29 02:57'
updated_date: '2026-03-29 03:14'
labels:
  - feature
  - library
  - 'estimate: side'
milestone: m-0
dependencies: []
priority: medium
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The LibraryScanner is incremental but must be triggered manually. Extend the existing watchdog infrastructure in `watcher.py` to also watch the library directory (in addition to staging) and call `LibraryScanner.scan()` when changes are detected.

Open scoping questions to resolve during implementation:
- Debounce strategy (avoid rapid re-scans on bulk file moves)
- Full rescan vs path-targeted upsert
- How to avoid re-scanning during an active ingest pipeline run
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Adding or removing files from the library directory triggers an automatic re-scan without manual intervention
- [ ] #2 Re-scan is debounced to avoid thrashing on bulk changes
- [ ] #3 No re-scan fires while an ingest pipeline run is active
- [ ] #4 Existing watchdog tests remain green
<!-- AC:END -->
