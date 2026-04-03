---
id: TASK-23
title: Extension developer documentation
status: To Do
assignee: []
created_date: '2026-03-29 03:12'
updated_date: '2026-04-03 04:37'
labels:
  - docs
  - 'estimate: side'
milestone: m-2
dependencies:
  - TASK-18
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Write developer documentation for the kamp extension system: how to build both backend (Python) and frontend (npm) extensions, the KampContext API reference, the panel manifest format, and how to publish to npm/PyPI.

Documentation should be written after the built-in refactor (TASK-18) confirms the API is stable, so examples reflect real usage.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Backend extension guide covers: entry points, BaseTagger/BaseArtworkSource ABCs, KampContext API
- [ ] #2 Frontend extension guide covers: package.json manifest, window.KampAPI, panel registration
- [ ] #3 At least one complete worked example (end-to-end) for each layer
- [ ] #4 Publishing guide covers npm and PyPI distribution
<!-- AC:END -->
