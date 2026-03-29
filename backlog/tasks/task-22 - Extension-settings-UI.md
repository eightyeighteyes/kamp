---
id: TASK-22
title: Extension settings UI
status: To Do
assignee: []
created_date: '2026-03-29 03:12'
labels:
  - feature
  - extensions
  - ui
  - 'estimate: side'
milestone: m-2
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a settings UI where users can view installed extensions, enable/disable them, and configure per-extension settings. Extensions declare their settings schema in their manifest; the host renders the settings form.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Settings screen lists all installed extensions with name, version, and enabled state
- [ ] #2 User can enable/disable extensions from the UI
- [ ] #3 Per-extension settings are rendered from the extension's declared schema
- [ ] #4 Settings changes take effect without requiring an app restart
<!-- AC:END -->
