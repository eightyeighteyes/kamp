---
id: TASK-64
title: Library scan progress UI
status: Done
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-04-04 22:49'
labels:
  - feature
  - ux
  - library
  - 'estimate: lp'
milestone: m-20
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The `LibraryScanner` runs synchronously and returns a `ScanResult`, but there is no UI feedback during a scan. Large libraries can stall the UI without any indication of progress.

**Not scoped — needs a design pass before estimating.** Open questions:
- Progress bar in the first-run setup flow?
- Status indicator during background re-scans?
- Does the scan need to move to a worker thread/subprocess to avoid blocking the UI on large libraries?
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 User sees progress feedback during a library scan (form TBD in design)
- [ ] #2 UI remains responsive during scan on large libraries
- [ ] #3 First-run setup flow includes scan progress indicator
<!-- AC:END -->
