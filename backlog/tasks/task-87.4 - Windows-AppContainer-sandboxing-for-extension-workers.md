---
id: TASK-87.4
title: Windows AppContainer sandboxing for extension workers
status: To Do
assignee: []
created_date: '2026-04-05 16:37'
labels:
  - feature
  - security
  - 'estimate: side'
milestone: m-2
dependencies:
  - TASK-87.1
parent_task_id: TASK-87
ordinal: 12400
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Apply AppContainer / restricted token to backend extension worker subprocesses on Windows. This trails macOS and Linux and is not required to open the marketplace, but should ship before the Windows extension marketplace is opened.

Capability requirements are documented in the scoping subtask (TASK-87.1).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extension worker subprocesses on Windows run under AppContainer or restricted token
- [ ] #2 All three built-in extensions operate correctly under the sandbox
- [ ] #3 A test extension that attempts filesystem access outside permitted paths is blocked
<!-- AC:END -->
