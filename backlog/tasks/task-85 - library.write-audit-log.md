---
id: TASK-85
title: library.write audit log
status: To Do
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-05 16:32'
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
The `library.write` permission grants extensions the ability to modify library metadata. To bound the damage a malicious or buggy extension can cause, all writes must go through named atomic operations (`update_metadata`, `set_artwork`) and every write must be logged to an append-only audit table.

The audit table schema: `extension_id`, `operation`, `track_id`, `old_value` (JSON), `new_value` (JSON), `timestamp`. This enables rollback of all changes made by a given extension (e.g. if a user uninstalls a misbehaving tagger).

No bulk deletes and no raw SQL access are permitted through the `library.write` permission. Extensions cannot drop tables, truncate, or issue arbitrary queries.

Depends on: TASK-17 (KampContext API, which defines the library.write surface).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 library.write permission exposes only named mutations: update_metadata(track_id, fields) and set_artwork(track_id, bytes)
- [ ] #2 Every write operation is appended to an audit table in SQLite with extension_id, operation, track_id, old_value, new_value, timestamp
- [ ] #3 Audit table is append-only; extensions cannot delete or modify audit records
- [ ] #4 A rollback operation exists to undo all library.write changes made by a given extension_id
- [ ] #5 Attempts to issue raw SQL or call unlisted write operations via KampContext are rejected
<!-- AC:END -->
