---
id: TASK-83
title: Import-time execution probe for backend extensions
status: Done
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-06 11:26'
labels:
  - feature
  - security
  - 'estimate: side'
milestone: m-2
dependencies:
  - TASK-17
documentation:
  - project/kampground-ideation.md
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Python entry points execute module-level code at import time — before any ABC conformance check or permission gate. A malicious `__init__.py` can exfiltrate data, open files, or spawn subprocesses before `tag()` is ever called.

Before activating a backend extension, kampground loads it once in a restricted subprocess where `socket`, `subprocess`, `os.system`, and `open` are stubbed to loggers. Any call to a stubbed symbol during import raises a load-time error and the extension is rejected before it is added to the active set.

This is a heuristic, not a sandbox — it catches the obvious module-level exfiltration pattern. Legitimate extensions that only do initialization work (registering classes, reading package metadata) will pass without issue. OS-level sandboxing (a separate task) is the complete solution.

Depends on: TASK-17 (extension host, which is where this probe hooks in at load time).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A backend extension whose module-level code calls open(), socket, subprocess, or os.system is rejected at load time with a clear error
- [x] #2 A legitimate extension that only defines classes and reads package metadata loads without error
- [x] #3 The probe runs in an isolated subprocess — the daemon process is unaffected by any module-level side effects during probing
- [x] #4 Rejection error includes the extension package name and the stubbed symbol that was called
<!-- AC:END -->
