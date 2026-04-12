---
id: TASK-118
title: sync doesn't run from built application
status: To Do
assignee: []
created_date: '2026-04-12 22:10'
labels: []
milestone: m-9
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
[kamp daemon] 2026-04-12 18:09:36  ERROR     kamp_daemon.menu_bar  Unhandled error during manual Bandcamp sync
Traceback (most recent call last):
  File "kamp_daemon/menu_bar.py", line 168, in _run
  File "kamp_daemon/ext/abc.py", line 87, in sync_once
NotImplementedError
<!-- SECTION:DESCRIPTION:END -->
