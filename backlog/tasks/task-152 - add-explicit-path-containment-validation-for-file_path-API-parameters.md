---
id: TASK-152
title: add explicit path containment validation for file_path API parameters
status: To Do
assignee: []
created_date: '2026-04-18 18:03'
updated_date: '2026-04-19 23:52'
labels:
  - security
  - chore
  - 'estimate: side'
milestone: m-30
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-05)
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-05 from the v1.11.0 database security audit.

Several API endpoints accept a `file_path` string from the HTTP request and pass it directly to `index.get_track_by_path(Path(file_path))`. The DB lookup currently acts as an implicit path allowlist — only indexed paths can be returned. However, this is not explicit, and a future change that uses the caller-supplied path directly (e.g. for performance in the `album-art` endpoint) would silently introduce a path traversal vulnerability.

**Fix:** add a shared validation helper and call it in each affected endpoint:

```python
def _validate_library_path(file_path: str, library_path: Path | None) -> Path:
    p = Path(file_path).resolve()
    if library_path is not None:
        if not str(p).startswith(str(library_path.resolve())):
            raise HTTPException(status_code=400, detail="Path outside library directory")
    return p
```

Affected endpoints: `GET /api/v1/tracks`, `POST /api/v1/tracks/favorite`, `GET /api/v1/album-art`, `POST /api/v1/player/play`, `POST /api/v1/player/queue/add`, and any other endpoint that accepts a raw `file_path`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All file_path-accepting endpoints validate that the resolved path lies within the configured library directory
- [ ] #2 Requests with paths outside the library directory return HTTP 400
- [ ] #3 No regression for valid library paths
- [ ] #4 Validation helper is shared (not duplicated per endpoint)
<!-- AC:END -->
