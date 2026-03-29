---
id: TASK-25
title: Library scan progress UI
status: In Progress
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-03-29 14:14'
labels:
  - feature
  - ux
  - library
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: medium
ordinal: 500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Now that the library picker and scan progress bar both exist (landed in feat: native library picker, config endpoint, and scan progress bar), wire them together: automatically trigger a library scan immediately after the user selects their library path in the first-run setup flow.

The scan progress bar should show while the scan runs. On completion, the setup flow proceeds as normal.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 After the user selects a library path in setup, a scan starts automatically without requiring a separate button press
- [ ] #2 The scan progress bar is visible during the scan
- [ ] #3 On scan completion, the setup flow advances to the next step
- [ ] #4 If the scan fails, an error is shown with the option to retry or choose a different path
<!-- AC:END -->
