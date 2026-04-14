---
id: TASK-121
title: extract bandcamp username after login
status: In Progress
assignee: []
created_date: '2026-04-13 01:49'
updated_date: '2026-04-14 02:11'
labels: []
milestone: m-1
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Bandcamp username shouldn't be a required config field — it should be extracted from the authenticated session automatically.

After login, `GET https://bandcamp.com/api/fan/2` with the authenticated session returns `{ fan_id, username, ... }` — no profile-page scrape needed. Store username alongside the session in the DB.

**Scope:**
- In `bandcamp.py`: replace `_get_fan_id(username, session)` profile-page scrape with a direct `GET /api/fan/2` call that returns both `fan_id` and `username`; store username in the DB session row (or a separate field) on first successful call
- Make `bandcamp.username` in config optional; if absent, use the username from the DB session
- Preferences UI: show "Connected as {username}" with a Disconnect button when a session exists; show "Connect to Bandcamp" button when not connected — mirrors the Last.fm section pattern
- Remove the Username InputRow from the Bandcamp prefs section
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 sync works without bandcamp.username in config.toml when a valid session exists in the DB
- [ ] #2 Preferences Bandcamp section shows 'Connected as {username}' when logged in
- [ ] #3 Preferences Bandcamp section shows 'Connect to Bandcamp' button when not logged in
- [ ] #4 Username is populated automatically after the login flow completes without any user input
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Estimate: Side**

Depends on TASK-120 (session in DB, so username can be stored there).

Key files: `kamp_daemon/bandcamp.py` (_get_fan_id replacement), `kamp_daemon/config.py` (make username optional), `kamp_core/server.py` (expose session status endpoint or return username in config), `kamp_ui/.../PreferencesDialog.tsx` (connected/disconnected state in BandcampLoginRow).
<!-- SECTION:NOTES:END -->
