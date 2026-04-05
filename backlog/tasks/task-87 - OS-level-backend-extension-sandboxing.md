---
id: TASK-87
title: OS-level backend extension sandboxing
status: To Do
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-05 16:32'
labels:
  - feature
  - security
  - 'estimate: box set'
milestone: m-2
dependencies:
  - TASK-17
documentation:
  - project/kampground-ideation.md
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Structured data contracts and the import-time probe remove the incentive and catch obvious cases, but a determined malicious extension can still call open() or spawn subprocesses directly. OS-level sandboxing enforces the boundary at the kernel level.

**This is a marketplace gate: the public extension marketplace does not open until this task ships on macOS and Linux.**

Target platforms and mechanisms:
- **macOS:** `sandbox-exec` with a restrictive profile applied to backend extension worker subprocesses
- **Linux:** `landlock` + `seccomp` 
- **Windows:** AppContainer / restricted token (can trail macOS/Linux)

The subprocess isolation model already in place (workers are spawn-context subprocesses) makes this straightforward to layer in — the sandbox profile is applied to the worker process at spawn time.

Needs a scoping pass before work begins: define the restrictive profile for each platform and validate it against the three built-in extensions refactored in TASK-18.

Depends on: TASK-17 (extension host/subprocess model), TASK-18 (built-in extensions to validate against).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Backend extension worker subprocesses on macOS run under sandbox-exec with a profile that denies filesystem access outside defined paths
- [ ] #2 Backend extension worker subprocesses on Linux run under landlock + seccomp restrictions
- [ ] #3 The three built-in extensions (Bandcamp syncer, MusicBrainz tagger, artwork fetcher) operate correctly under the sandbox
- [ ] #4 A test extension that calls open() on an arbitrary path is blocked by the OS sandbox
- [ ] #5 Windows sandboxing is tracked as a follow-up; macOS + Linux are required to open the marketplace
<!-- AC:END -->
