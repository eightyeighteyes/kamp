---
id: TASK-19
title: contextBridge API and frontend panel registration system
status: To Do
assignee: []
created_date: '2026-03-29 03:12'
labels:
  - feature
  - extensions
  - electron
  - 'estimate: lp'
milestone: m-2
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expose the player API to frontend extensions via Electron's `contextBridge` as `window.KampAPI`. Extensions are npm packages discovered via the `kamp-extension` keyword in `package.json` and export a manifest declaring contributed panels/components.

Extensions must never touch `ipcRenderer` or Node.js directly — only `window.KampAPI`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 window.KampAPI is injected via contextBridge in the preload script
- [ ] #2 Frontend extensions are discovered via kamp-extension npm keyword
- [ ] #3 An example first-party extension registers a panel successfully
- [ ] #4 Extensions cannot access ipcRenderer or Node.js APIs directly
<!-- AC:END -->
