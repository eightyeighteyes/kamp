---
id: TASK-87.3
title: Linux landlock + seccomp integration for extension workers
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
ordinal: 12300
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Apply `landlock` filesystem access control and `seccomp` syscall filtering to backend extension worker subprocesses on Linux. Rules are derived from the scoping pass. Both mechanisms are applied at subprocess startup before the extension module is loaded.

Landlock requires kernel 5.13+; document the minimum kernel version requirement. Seccomp filter should use allowlist mode (deny all except permitted syscalls).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extension worker subprocesses on Linux apply landlock path restrictions and seccomp syscall filter at startup
- [ ] #2 All three built-in extensions operate correctly under the sandbox
- [ ] #3 A test extension that calls open() on an arbitrary path is blocked by landlock
- [ ] #4 Minimum supported kernel version is documented; graceful degradation (or clear error) on older kernels
- [ ] #5 Sandbox is applied before the extension module is imported
<!-- AC:END -->
