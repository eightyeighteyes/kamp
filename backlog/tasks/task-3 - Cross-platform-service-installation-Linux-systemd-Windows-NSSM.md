---
id: TASK-3
title: 'Cross-platform service installation (Linux systemd, Windows NSSM)'
status: To Do
assignee: []
created_date: '2026-03-29 02:57'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - linux
  - windows
  - 'estimate: lp'
milestone: m-4
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add service installation support so kamp can run as a background service on Linux (systemd unit file) and Windows (NSSM wrapper). Can ship incrementally — Linux first, then Windows.

Linux systemd unit file is straightforward. Windows NSSM wraps the CLI and is simpler to manage than Task Scheduler.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Linux: systemd unit file ships and kamp can be enabled/started/stopped via systemctl
- [ ] #2 Windows: NSSM install/uninstall scripts provided
- [ ] #3 README documents service install steps for both platforms
<!-- AC:END -->
