---
id: TASK-149
title: implement shared-secret token authentication for the HTTP API
status: Done
assignee: []
created_date: '2026-04-18 18:02'
updated_date: '2026-04-20 02:55'
labels:
  - security
  - feature
  - 'estimate: lp'
milestone: m-30
dependencies: []
references:
  - 'doc-1 - Database Security Audit — v1.11.0 (FINDING-01, FINDING-02)'
priority: high
ordinal: 2000
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
- [x] #1 Daemon generates a random token at startup and writes it to .token (chmod 600)
- [x] #2 All API endpoints reject requests missing a valid X-Kamp-Token header with HTTP 401
- [x] #3 Electron reads the token file at startup and includes it on all requests
- [x] #4 Token is regenerated on each daemon restart; Electron re-reads on reconnect
- [x] #5 WebSocket upgrade also validates the token (query param or first message)
- [x] #6 No existing Electron↔API flows break after the change
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## Implementation

**Python (daemon + server):**
- `kamp_daemon/config.py`: Added `token_path()` → `~/.local/share/kamp/.token`
- `kamp_daemon/__main__.py`: Generates `secrets.token_hex(32)` at startup, writes to `.token` with `chmod 600`, passes `auth_token` to `create_app()`
- `kamp_core/server.py`: Added `auth_token` param; HTTP middleware returns 401 for missing/wrong token (header or `?token=` query param); OPTIONS bypasses for CORS preflight; WebSocket endpoint validates `?token=` before accepting (closes 1008 on rejection)

**TypeScript (Electron):**
- `main/index.ts`: Reads token after server starts; `authHeaders()` helper wires it into all `postToPlayer` and `net.fetch` calls
- `preload/index.ts`: Exposes `getApiToken()` via contextBridge (re-reads file on each call so daemon restarts are handled)
- `preload/kampAPI.ts`: Includes `?token=` in WS URLs and `X-Kamp-Token` in fetches; `getAlbumArtUrl()` appends `&token=` for `<img src>` use
- `renderer/src/api/client.ts`: All fetch helpers include `X-Kamp-Token`; `artUrl()` appends `&token=`; `connectStateStream` includes `?token=` in WS URL

**Note on `<img src>` requests:** Browser image fetches cannot carry custom headers, so the token is accepted as a `?token=` query param for both HTTP and WebSocket endpoints.

**Tests:** 9 new tests in `TestAuthToken` covering all acceptance criteria (no-auth passthrough, 401 without/with-wrong token, query-param acceptance, OPTIONS bypass, WS accept/reject).
<!-- SECTION:FINAL_SUMMARY:END -->
