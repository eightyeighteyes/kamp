---
id: TASK-2.1
title: 'Windows tray app (pystray, full parity with rumps)'
status: To Do
assignee: []
created_date: '2026-03-29 02:57'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - windows
  - 'estimate: lp'
milestone: m-3
dependencies: []
parent_task_id: TASK-2
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the Windows system tray app using pystray, matching the macOS rumps menu bar app in features and behavior.

Key differences from rumps:
- pystray is pull-based: menus are rebuilt on each open (not held in memory)
- Status animation requires a background icon-swap thread (no inline status text like rumps)
- No built-in notification support — wire up Windows notifications separately
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Tray icon appears in Windows system tray on startup
- [ ] #2 Menu items match macOS menu bar app (open UI, sync, quit, etc.)
- [ ] #3 Status animation works via background icon-swap thread
- [ ] #4 Notifications work on Windows 10/11
<!-- AC:END -->
