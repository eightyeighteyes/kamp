---
id: TASK-87.1
title: Sandbox profile scoping — define allow/deny rules for each platform
status: To Do
assignee: []
created_date: '2026-04-05 16:36'
labels:
  - security
  - 'estimate: side'
milestone: m-2
dependencies:
  - TASK-18
parent_task_id: TASK-87
ordinal: 12100
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Before writing any sandboxing code, define exactly what the restrictive profile needs to allow and deny on each platform. This scoping pass prevents wasted implementation effort and is especially important given that macOS MDM/EDR tools (Falcon, Jamf) can silently interfere with sandbox profiles in ways that are hard to diagnose.

Instrument the three built-in extensions (Bandcamp syncer, MusicBrainz tagger, artwork fetcher) running under strace/dtruss to enumerate the syscalls and filesystem paths they legitimately access. Use this as the basis for the allow rules. Document the final profiles before any platform implementation begins.

Depends on TASK-18 (built-ins must be refactored as extensions before they can be profiled in this context).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Syscall and filesystem access profiles for all three built-in extensions are documented
- [ ] #2 macOS sandbox-exec profile (allow rules) is written and reviewed
- [ ] #3 Linux landlock path rules and seccomp syscall allowlist are written and reviewed
- [ ] #4 Windows AppContainer capability requirements are documented
- [ ] #5 Any MDM/EDR interference risks on macOS are identified and noted before implementation begins
<!-- AC:END -->
