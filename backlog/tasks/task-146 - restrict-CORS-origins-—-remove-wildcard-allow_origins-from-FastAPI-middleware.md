---
id: TASK-146
title: restrict CORS origins — remove wildcard allow_origins from FastAPI middleware
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
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-02)
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-02 from the v1.11.0 database security audit.

The FastAPI server uses `allow_origins=["*"]` in CORSMiddleware. Combined with no API authentication, this means any page open in any browser can make cross-origin requests to the daemon — including calling `GET /api/v1/bandcamp/session-cookies`.

**Fix:** restrict origins to those kamp actually serves:

```python
ALLOWED_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "file://",  # Electron renderer in production
]
# Vite dev server — only in dev mode
if dev_mode:
    ALLOWED_ORIGINS.append("http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type"],
)
```

Verify the Electron renderer (production `file://` origin) and Vite dev server continue to work after the change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 CORS no longer uses wildcard allow_origins
- [ ] #2 Electron renderer (file:// origin) can still reach all API endpoints
- [ ] #3 Vite dev server (localhost:5173) works in dev mode
- [ ] #4 Requests from arbitrary browser origins are rejected with CORS error
<!-- AC:END -->
