---
id: TASK-2.2
title: Windows CI (GitHub Actions windows-latest)
status: To Do
assignee: []
created_date: '2026-03-29 02:57'
updated_date: '2026-03-29 03:04'
labels:
  - platform
  - windows
  - ci
  - 'estimate: side'
milestone: m-3
dependencies: []
parent_task_id: TASK-2
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a GitHub Actions CI job running on `windows-latest` so the test suite is verified on Windows. Expected work: subprocess spawn differences, path separator edge cases, and likely several test fixes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 CI workflow includes a windows-latest job
- [ ] #2 All existing tests pass on Windows
- [ ] #3 Path separator issues resolved
<!-- AC:END -->
