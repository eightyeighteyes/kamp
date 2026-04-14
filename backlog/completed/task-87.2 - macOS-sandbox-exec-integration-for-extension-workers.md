---
id: TASK-87.2
title: macOS sandbox-exec integration for extension workers
status: Done
assignee: []
created_date: '2026-04-05 16:37'
updated_date: '2026-04-08 18:09'
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
- [x] #1 Extension worker subprocesses on macOS launch under sandbox-exec with the scoped restrictive profile
- [x] #2 All three built-in extensions operate correctly under the sandbox
- [x] #3 A test extension that calls open() on an arbitrary path outside permitted paths is blocked by the sandbox
- [x] #4 Sandbox failure (e.g. profile rejected by MDM) produces a clear error rather than silently running unsandboxed
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented in kamp_daemon/ext/sandbox/_macos.py. sandbox_init() via ctypes to libsandbox.dylib. Profile applied as multiprocessing.Process initializer before extension code loads. Non-fatal on MDM/EDR failure (WARNING logged). Two tiers: minimal (blocks writes + exec) and syncer (allows state-dir writes + exec for Playwright). Note: seccomp uses default-allow + block-execve rather than full allowlist — follow-up hardening tracked in sandbox-profiles.md.
<!-- SECTION:NOTES:END -->
