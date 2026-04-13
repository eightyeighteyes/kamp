---
id: TASK-119
title: Replace Playwright with plain HTTP in Bandcamp sync
status: Done
assignee: []
created_date: '2026-04-12 22:35'
updated_date: '2026-04-13 22:22'
labels: []
milestone: m-9
dependencies: []
priority: high
ordinal: 2500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Playwright is excluded from the .app bundle (adds ~200 MB Chromium, complicates notarisation), leaving Bandcamp sync silently broken for all built-app users. Replace every Playwright call in `kamp_daemon/bandcamp.py` with plain `requests` + HTML/JSON parsing, move the interactive login window to an Electron BrowserWindow (Chromium is already in the bundle), and re-include the syncer modules in `kamp.spec`.

### Playwright usage to replace
| Operation | Current | Plan |
|-----------|---------|------|
| Fan ID extraction | Playwright page HTML parse | `requests` GET + regex on pagedata blob |
| Collection items fetch | `page.evaluate(fetch(...))` | plain `requests.Session` POST |
| Download links (collection page) | Playwright scroll + JS DOM query | `requests` GET + HTML parse |
| Download CDN URL (download page) | Playwright + Knockout.js | parse pagedata JSON blob (⚠️ spike needed) |
| Interactive login | Playwright headed browser | Electron BrowserWindow |

### Key risk
The CDN download URL is rendered by Knockout.js when the user selects a format. Phase 0 is a spike to confirm that URL is pre-embedded in the download page's `pagedata` JSON blob before committing to the rewrite.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Playwright is removed from pyproject.toml dependencies
- [ ] #2 kamp_daemon/syncer.py and kamp_daemon/ext/builtin/bandcamp.py are included in the .app bundle (removed from kamp.spec excludes)
- [ ] #3 Bandcamp sync (collection fetch + download) works end-to-end using plain requests
- [ ] #4 Interactive login uses an Electron BrowserWindow; cookies are written to bandcamp_session.json
- [ ] #5 All existing tests pass; test_bandcamp.py mocks use requests, not Playwright
- [ ] #6 The ModuleNotFoundError stub in daemon_core.py is removed
<!-- AC:END -->
