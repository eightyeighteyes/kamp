---
id: TASK-129
title: >-
  fix: reload Bandcamp cookies from DB into Electron session before each
  proxy-fetch sync
status: To Do
assignee: []
created_date: '2026-04-14 01:54'
labels: []
milestone: m-1
dependencies:
  - TASK-120
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After TASK-120, cookies are stored in `library.db` and cleared from `session.defaultSession` after login. In the PyInstaller bundle, `_ProxySession` routes all Bandcamp requests through the Electron `net` module via `session.defaultSession` — so the cookies must be present there at sync time.

Currently the cookies are never reloaded from the DB into `session.defaultSession`, which means the first sync after login (in the built `.app`) will fail with 401/403.

**Fix:** Before the Python daemon triggers a sync in the bundled app, the Electron main process must:
1. Read the `bandcamp` session row from `library.db` (via the `/api/v1/bandcamp/session-cookies` endpoint or a new IPC channel)
2. Set those cookies on `session.defaultSession` using `session.defaultSession.cookies.set(...)`
3. Proceed with the sync; clear cookies from `session.defaultSession` again after sync completes (optional but keeps the store clean)

Alternatively, inject cookies via a new server endpoint that Electron polls/subscribes to on `bandcamp.needs-login` or a new `bandcamp.sync-starting` WebSocket event.

**Background:** The `_ProxySession` in `kamp_daemon/bandcamp.py` uses `http://127.0.0.1:8000/api/v1/bandcamp/proxy-fetch` which is handled by `ipcMain.handle('bandcamp:proxy-fetch', ...)` in `kamp_ui/src/main/index.ts`. That handler calls `net.fetch(..., { session: session.defaultSession })`, relying on the cookie jar.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Sync in the built .app succeeds on first run after login (cookies reloaded from DB before proxy-fetch)
- [ ] #2 Cookies are not permanently resident in session.defaultSession between syncs
- [ ] #3 Dev path (non-frozen) is unaffected
<!-- AC:END -->
