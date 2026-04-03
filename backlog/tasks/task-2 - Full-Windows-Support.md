---
id: TASK-2
title: Full Windows Support
status: To Do
assignee: []
created_date: '2026-03-29 02:57'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - windows
  - 'estimate: discography'
milestone: m-3
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Port kamp to Windows 10/11 with full feature parity: tray app, CI, Playwright tests, service install, packaging, and path conventions. Distribution via Chocolatey.

**Prerequisite:** Rebrand must ship before this work begins.

This is a parent task. Subtasks cover each component independently so they can be worked in sequence or parallel.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 kamp runs on Windows 10 and Windows 11 with full feature parity to macOS
- [ ] #2 Tray app works via pystray with status animation and menu
- [ ] #3 CI passes on GitHub Actions windows-latest
- [ ] #4 Playwright E2E tests pass on Windows
- [ ] #5 Service can be installed/uninstalled via NSSM
- [ ] #6 Package published to Chocolatey
- [ ] #7 All paths use %APPDATA% conventions correctly
<!-- AC:END -->
