---
id: TASK-14
title: New purchase highlight (watcher → library re-scan → surfaced in UI)
status: To Do
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-04-23 14:11'
labels:
  - feature
  - ui
  - bandcamp
  - 'estimate: side'
milestone: m-31
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When the Bandcamp watcher detects a new download and the library re-scan completes, surface the new album prominently in the UI so the user notices their new purchase.

This requires: watcher emits a library-changed event → library re-scan runs → new albums are flagged → UI highlights them (e.g. a "New" badge, a dedicated section, or a toast).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New albums from a Bandcamp sync are visually highlighted in the library
- [ ] #2 Highlight clears after the user visits/plays the album
- [ ] #3 No manual refresh required — driven by WebSocket event
<!-- AC:END -->
