---
id: TASK-87.2
title: macOS sandbox-exec integration for extension workers
status: To Do
assignee: []
created_date: '2026-04-05 16:37'
labels:
  - feature
  - security
  - 'estimate: lp'
milestone: m-2
dependencies:
  - TASK-87.1
parent_task_id: TASK-87
ordinal: 12200
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Apply the `sandbox-exec` profile (defined in the scoping subtask) to backend extension worker subprocesses on macOS. The profile is applied at subprocess spawn time using the profile defined in the scoping pass.

Per CLAUDE.md: budget at least a Side for anything touching macOS system sandboxing, and if the same approach fails twice, stop and check in rather than trying a third approach.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extension worker subprocesses on macOS launch under sandbox-exec with the scoped restrictive profile
- [ ] #2 All three built-in extensions operate correctly under the sandbox
- [ ] #3 A test extension that calls open() on an arbitrary path outside permitted paths is blocked by the sandbox
- [ ] #4 Sandbox failure (e.g. profile rejected by MDM) produces a clear error rather than silently running unsandboxed
<!-- AC:END -->
