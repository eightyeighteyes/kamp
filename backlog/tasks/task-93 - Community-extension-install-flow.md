---
id: TASK-93
title: Community extension install flow
status: To Do
assignee: []
created_date: '2026-04-07 12:52'
labels:
  - feature
  - ui
milestone: m-2
dependencies:
  - TASK-22
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Provide a way for users to install community (Phase 2) extensions. Currently there is no install surface — community extensions can only appear if they happen to be in node_modules already. Users need a supported path to discover and install extensions by npm package name or local path.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 User can install a community extension by npm package name from the extension settings UI
- [ ] #2 User can install a community extension from a local directory path
- [ ] #3 Installed extensions are persisted across app restarts
- [ ] #4 User can uninstall a community extension from the UI
- [ ] #5 Install/uninstall does not require an app restart to take effect
<!-- AC:END -->
