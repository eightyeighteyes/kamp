---
id: TASK-85
title: library.write audit log
status: To Do
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-05 21:21'
labels:
  - feature
  - security
  - 'estimate: side'
milestone: m-2
dependencies:
  - TASK-17
documentation:
  - project/kampground-ideation.md
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement an append-only audit log for all `library.write` operations performed by extensions. Every mutation (`update_metadata`, `set_artwork`) is logged to a dedicated SQLite table with extension ID, operation name, old value, new value, and timestamp. The log enables rollback of any extension's changes.

Depends on: TASK-17 (KampGround API, which defines the library.write surface).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 An audit_log table exists with columns: extension_id, operation, old_value, new_value, timestamp
- [ ] #2 Every update_metadata and set_artwork call via KampGround is logged before the write is applied
- [ ] #3 Audit log is append-only; no extension or host code may delete or update rows
- [ ] #4 A rollback command exists that reverts all writes by a given extension_id
- [ ] #5 Attempts to issue raw SQL or call unlisted write operations via KampGround are rejected
<!-- AC:END -->
