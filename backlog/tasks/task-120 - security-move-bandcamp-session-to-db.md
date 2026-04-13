---
id: TASK-120
title: 'security: move bandcamp session to db'
status: To Do
assignee: []
created_date: '2026-04-13 01:47'
updated_date: '2026-04-13 02:08'
labels: []
milestone: m-1
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
We shouldn't keep cookies in a plaintext file on disk. The user's active sessions should be kept in a `sessions` table in `library.db`.

The session schema should support session information for multiple possible service integrations (Bandcamp, Last.fm, future services).

**Scope:**
- Add a `sessions` table to `kamp_core` (columns: `service TEXT`, `session_json TEXT`, `updated_at`); expose `get_session` / `set_session` / `clear_session` on `LibraryIndex`
- Update `bandcamp.py`: `_make_requests_session` and `_validate_session` read from DB instead of `bandcamp_session.json`
- Update `_on_bandcamp_login_complete` in `__main__.py` to write to DB instead of file
- Update `logout()` in `syncer.py` to call `index.clear_session("bandcamp")` instead of unlinking the file
- After extracting cookies in the Electron login flow (`openBandcampLogin`), clear them from `session.defaultSession` so they don't linger in Electron's cookie store
- Migrate existing `bandcamp_session.json` to DB on first startup (read file → write DB → delete file)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bandcamp_session.json is no longer written to disk after login
- [ ] #2 Existing bandcamp_session.json is migrated to DB on first daemon start and then deleted
- [ ] #3 sync_once() reads session from DB correctly
- [ ] #4 logout() removes the DB row and Electron session cookies are cleared on login
- [ ] #5 sessions table schema supports a 'service' key so Last.fm or other services can use the same table in future
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Estimate: LP (2 sides)**

Dependencies: none — do this before TASK-121 and TASK-122 since both depend on where the session lives.

Key files: `kamp_core/library.py` (schema + accessors), `kamp_daemon/bandcamp.py` (_make_requests_session, _validate_session), `kamp_daemon/__main__.py` (_on_bandcamp_login_complete), `kamp_daemon/syncer.py` (logout), `kamp_ui/src/main/index.ts` (clear defaultSession cookies after POST to login-complete).
<!-- SECTION:NOTES:END -->
