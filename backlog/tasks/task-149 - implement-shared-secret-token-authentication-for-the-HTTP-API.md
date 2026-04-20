---
id: TASK-149
title: implement shared-secret token authentication for the HTTP API
status: To Do
assignee: []
created_date: '2026-04-18 18:02'
updated_date: '2026-04-19 23:52'
labels:
  - security
  - feature
  - 'estimate: lp'
milestone: m-30
dependencies: []
references:
  - 'doc-1 - Database Security Audit — v1.11.0 (FINDING-01, FINDING-02)'
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-01 and FINDING-02 (comprehensive fix) from the v1.11.0 database security audit.

The HTTP API has no authentication — any local process can call any endpoint, including those that expose Bandcamp session cookies and mutate library state. The CORS wildcard (addressed separately in v1.11.0) is a partial mitigation; full protection requires a shared secret.

**Approach (standard pattern for local-first desktop apps — used by VS Code, JupyterLab):**

1. At daemon startup, generate a cryptographically random token: `secrets.token_hex(32)`
2. Write it to `~/.local/share/kamp/.token` with `chmod 600`
3. Electron reads the token from this file at startup and attaches it to all API requests as `X-Kamp-Token: <token>`
4. FastAPI middleware validates the header on every request; missing or wrong token → HTTP 401
5. Token is regenerated on each daemon start (stateless — Electron re-reads on reconnect)

Sensitive endpoints (session cookies, config writes) should require the token in v1.11.0's partial fix; this task makes it universal.

This comprehensively closes the "any local process can reach the API" attack surface.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Daemon generates a random token at startup and writes it to .token (chmod 600)
- [ ] #2 All API endpoints reject requests missing a valid X-Kamp-Token header with HTTP 401
- [ ] #3 Electron reads the token file at startup and includes it on all requests
- [ ] #4 Token is regenerated on each daemon restart; Electron re-reads on reconnect
- [ ] #5 WebSocket upgrade also validates the token (query param or first message)
- [ ] #6 No existing Electron↔API flows break after the change
<!-- AC:END -->
