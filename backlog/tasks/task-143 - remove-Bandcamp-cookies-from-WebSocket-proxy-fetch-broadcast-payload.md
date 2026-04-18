---
id: TASK-143
title: remove Bandcamp cookies from WebSocket proxy-fetch broadcast payload
status: To Do
assignee: []
created_date: '2026-04-18 18:01'
updated_date: '2026-04-18 18:12'
labels:
  - security
  - chore
  - 'estimate: single'
milestone: m-29
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-01)
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-01 (partial) from the v1.11.0 database security audit.

The `bandcamp.proxy-fetch` WebSocket event broadcasts the full Bandcamp cookie list (including `cf_clearance` and auth cookies) to every connected WebSocket client. Any local process that opens a WebSocket connection to the kamp daemon receives these cookies as part of normal operation.

**This is a two-part change — both sides must ship together.**

### Part 1: Python (`kamp_core/server.py`)

Remove `cookies` from the broadcast payload in the `bandcamp_proxy_fetch` endpoint:

```python
proxy_event: dict[str, Any] = {
    "type": "bandcamp.proxy-fetch",
    "id": req_id,
    "url": req.url,
    "method": req.method,
    "headers": req.headers,
    "body": req.body,
    # cookies omitted — Electron fetches /session-cookies directly
}
```

Also remove the `broadcast_cookies` / `session_data` locals that are no longer needed.

### Part 2: Electron (`kamp_ui/src/main/index.ts`)

The `bandcamp:proxy-fetch` IPC handler currently reads cookies exclusively from `req.cookies` (the WS payload field). It does **not** fall back to the HTTP endpoint — `req.cookies ?? []` silently produces an empty list, which means `net.fetch` runs without auth cookies and gets 403s from Bandcamp.

`GET /api/v1/bandcamp/session-cookies` already exists and returns the right shape `{"cookies": [...]}`. It just needs to be called. Replace the `req.cookies` read with a fetch to that endpoint before injecting into `session.defaultSession`:

```typescript
// Fetch cookies from the daemon rather than reading from the WS payload,
// so auth cookies are never broadcast to all WS clients.
const cookieResp = await net.fetch('http://127.0.0.1:8000/api/v1/bandcamp/session-cookies')
const { cookies } = await cookieResp.json() as { cookies: BandcampCookie[] }
```

Then use `cookies` (from the endpoint) in place of `req.cookies` for the injection loop. Remove the `cookies` field from the `req` type annotation on the handler.

### Why the endpoint already exists

`GET /api/v1/bandcamp/session-cookies` was written in anticipation of this change but the Electron wiring was never completed. The endpoint is tested in `tests/test_server.py` but not called from any Electron code.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 WebSocket proxy-fetch broadcast no longer includes a cookies field
- [ ] #2 Electron main process fetches cookies from GET /api/v1/bandcamp/session-cookies before executing net.fetch
- [ ] #3 The cookies field is removed from the req type annotation on the bandcamp:proxy-fetch IPC handler
- [ ] #4 Bandcamp proxy flow (library sync, album fetch) continues to work end-to-end after the change
- [ ] #5 No cookies field appears in the pending proxy-fetch cache (_pending_proxy_fetches) stored in memory
<!-- AC:END -->
