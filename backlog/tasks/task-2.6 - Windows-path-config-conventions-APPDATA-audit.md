---
id: TASK-2.6
title: Windows path/config conventions (%APPDATA% audit)
status: To Do
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-03-29 03:05'
labels:
  - platform
  - windows
  - 'estimate: single'
milestone: m-3
dependencies: []
parent_task_id: TASK-2
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Audit all path and config file handling to ensure Windows conventions are followed. `pathlib` handles most separators already; focus on ensuring config/data directories resolve to `%APPDATA%` (or `%LOCALAPPDATA%`) rather than Unix paths.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Config and data files resolve to %APPDATA% on Windows
- [ ] #2 No hardcoded Unix paths remain
- [ ] #3 Audit documented in a code comment or PR description
<!-- AC:END -->
