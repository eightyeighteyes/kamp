---
id: TASK-145
title: checkpoint WAL after clear_session to promptly remove deleted credentials
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
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-08)
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-08 from the v1.11.0 database security audit.

After `clear_session()` deletes a row from the `sessions` table, the plaintext credential data (Bandcamp cookies, Last.fm session key) remains in the WAL file (`library.db-wal`) until the next full checkpoint. A forensic copy of the WAL file taken immediately after a user disconnects their Bandcamp account would still contain their cookies.

**Fix:** issue a `PRAGMA wal_checkpoint(TRUNCATE)` immediately after the delete commits:

```python
def clear_session(self, service: str) -> None:
    self._conn.execute("DELETE FROM sessions WHERE service = ?", (service,))
    self._conn.commit()
    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

`TRUNCATE` mode resets the WAL file to zero bytes after checkpointing all frames, minimising the window in which deleted credential data is recoverable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 clear_session() issues PRAGMA wal_checkpoint(TRUNCATE) after commit
- [ ] #2 WAL file is truncated (zero or near-zero bytes) immediately after a session is cleared
- [ ] #3 No regression in normal read/write performance
<!-- AC:END -->
