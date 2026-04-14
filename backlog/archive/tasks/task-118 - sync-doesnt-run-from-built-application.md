---
id: TASK-118
title: sync doesn't run from built application
status: Done
assignee: []
created_date: '2026-04-12 22:10'
updated_date: '2026-04-13 22:42'
labels: []
milestone: m-9
dependencies: []
priority: high
ordinal: 7500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
[kamp daemon] 2026-04-12 18:09:36  ERROR     kamp_daemon.menu_bar  Unhandled error during manual Bandcamp sync
Traceback (most recent call last):
  File "kamp_daemon/menu_bar.py", line 168, in _run
  File "kamp_daemon/ext/abc.py", line 87, in sync_once
NotImplementedError
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Clicking 'Sync now' in the built .app does not raise NotImplementedError or log an unhandled error
- [ ] #2 The frozen-app stub's sync_once() and mark_synced() return silently (no-op)
- [ ] #3 Tests cover the stub behaviour in isolation
<!-- AC:END -->
