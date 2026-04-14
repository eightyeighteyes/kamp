---
id: TASK-19
title: contextBridge API and frontend panel registration system
status: Done
assignee: []
created_date: '2026-03-29 03:12'
updated_date: '2026-04-06 20:28'
labels:
  - feature
  - architecture
  - 'estimate: lp'
milestone: m-2
dependencies: []
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expose the player API to frontend extensions via Electron's `contextBridge` as `window.KampAPI`. Extensions are npm packages discovered via the `kamp-extension` keyword in `package.json` and export a manifest declaring contributed panels/components.

Extensions must never touch `ipcRenderer` or Node.js directly — only `window.KampAPI`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 window.KampAPI is injected via contextBridge in the preload script
- [x] #2 Frontend extensions are discovered via kamp-extension npm keyword
- [x] #3 An example first-party extension registers a panel successfully
- [x] #4 Extensions cannot access ipcRenderer or Node.js APIs directly
<!-- AC:END -->
