---
id: TASK-142
title: fix database file permissions — apply restrictive umask before sqlite3.connect
status: Done
assignee: []
created_date: '2026-04-18 18:01'
updated_date: '2026-04-18 18:05'
labels:
  - security
  - chore
  - 'estimate: single'
milestone: m-29
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-04)
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-04 from the v1.11.0 database security audit.

`sqlite3.connect()` creates the database file using the process umask (typically `022` → `644`). This makes `library.db`, `library.db-wal`, and `library.db-shm` world-readable, exposing all track metadata, play history, favorites, file paths, and session credentials to any user on the system.

**Fix:** apply a restrictive umask before the first `sqlite3.connect()` call in `_make_conn`, then restore it:

```python
def _make_conn(self) -> sqlite3.Connection:
    old_umask = os.umask(0o077)  # produces 600 for files, 700 for dirs
    try:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
    finally:
        os.umask(old_umask)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

The umask approach is preferred over a post-hoc `chmod` because there is no race window between SQLite creating the file and the permission change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Database file is created with 600 permissions (owner read/write only)
- [ ] #2 WAL and SHM sidecar files also created with 600 permissions
- [ ] #3 Existing installs: permissions corrected on next startup (apply chmod on open if current mode is too permissive)
- [ ] #4 No regression in multi-threaded connection behaviour
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Applied in `kamp_core/library.py`:

1. **New files** — `_make_conn` now sets `os.umask(0o077)` before `sqlite3.connect()` and restores the original umask in a `finally` block. This ensures the DB file and all WAL/SHM sidecar files are created with 600 permissions.

2. **Existing installs** — `__init__` calls `db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)` on the existing file before opening it, correcting any pre-existing 644 permissions on upgrade.

Added two tests in `TestDatabaseFilePermissions`: one verifying fresh DB is created 600, one verifying an existing 644 DB is corrected to 600 on re-open. All 106 tests pass.
<!-- SECTION:FINAL_SUMMARY:END -->
