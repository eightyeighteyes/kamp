---
id: TASK-148
title: >-
  replace noqa S608 with explicit ValueError guard in dynamic SET clause
  construction
status: To Do
assignee: []
created_date: '2026-04-18 18:02'
labels:
  - security
  - chore
  - 'estimate: single'
milestone: m-29
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-06)
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-06 from the v1.11.0 database security audit.

`rollback_extension` and `apply_metadata_update` in `library.py` interpolate column names from audit log JSON into a dynamic `SET` clause, filtered through `_WRITABLE_TRACK_FIELDS`. The filter is correct, but `# noqa: S608` comments suppress the linter's safety warning without documenting the reasoning — making the pattern invisible to future readers who might weaken or remove the guard.

**Fix:** replace the silent filter with an explicit invariant check that raises on unexpected column names:

```python
# Validate against the allowlist — load-bearing; do not remove.
# Column names are interpolated directly into SQL; any name outside this
# set must raise, not be silently skipped.
unknown = set(fields) - _WRITABLE_TRACK_FIELDS
if unknown:
    raise ValueError(f"Unexpected column names in metadata update: {unknown}")

safe = {k: v for k, v in fields.items() if k in _WRITABLE_TRACK_FIELDS}
set_clause = ", ".join(f"{k} = ?" for k in safe)
```

Remove the `# noqa: S608` suppressions once the intent is self-documenting.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 noqa: S608 suppressions removed from apply_metadata_update and rollback_extension
- [ ] #2 Passing unexpected column names raises ValueError rather than silently dropping them
- [ ] #3 Existing tests for metadata update and rollback continue to pass
<!-- AC:END -->
