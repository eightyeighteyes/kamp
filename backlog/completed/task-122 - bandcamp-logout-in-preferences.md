---
id: TASK-122
title: bandcamp logout in preferences
status: Done
assignee: []
created_date: '2026-04-13 01:51'
updated_date: '2026-04-14 02:40'
labels: []
milestone: m-1
dependencies: []
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When a user has logged in to Bandcamp, the Preferences Bandcamp section should show a "Disconnect" button that removes the session from the DB and clears any Electron session cookies.

The Bandcamp Login and Bandcamp Logout items should be removed from the menu bar app entirely — login/logout lives in Preferences only.

**Scope:**
- Add `POST /api/v1/bandcamp/logout` server endpoint that calls `logout()` (clears DB session row)
- Preferences BandcampLoginRow: show Disconnect button when connected (mirrors LastfmSection pattern); on click, call logout endpoint then reload config
- Remove `_login_item` and `_logout_item` (and related `_on_login`, `_on_logout` callbacks) from `menu_bar.py`
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Disconnect button appears in Preferences Bandcamp section when a session exists
- [ ] #2 Clicking Disconnect clears the session from the DB
- [ ] #3 Menu bar no longer shows Bandcamp Login or Bandcamp Logout items
- [ ] #4 After disconnect, Preferences shows 'Connect to Bandcamp' button again
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Estimate: Side**

Depends on TASK-120 and TASK-121 (session in DB, connected/disconnected UI state already built).

Key files: `kamp_core/server.py` (logout endpoint), `kamp_daemon/__main__.py` (wire logout callback), `kamp_daemon/menu_bar.py` (remove login/logout items), `PreferencesDialog.tsx` (Disconnect button in BandcampLoginRow).
<!-- SECTION:NOTES:END -->
