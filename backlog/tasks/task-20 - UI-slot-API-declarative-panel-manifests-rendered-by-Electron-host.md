---
id: TASK-20
title: 'UI slot API: declarative panel manifests rendered by Electron host'
status: To Do
assignee: []
created_date: '2026-03-29 03:12'
labels:
  - feature
  - extensions
  - ui
  - 'estimate: lp'
milestone: m-2
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the panel slot system so extensions can declaratively register panels that the Electron host renders in the layout. Extensions declare their panels in a manifest; the host places them in the panel grid.

This is the mechanism described in the vision statement: transport panel, artist panel, album browser, etc. are all panels that can be added, removed, and repositioned.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extensions declare panels in a manifest (title, component, default slot)
- [ ] #2 Host renders extension panels in the panel layout
- [ ] #3 User can add/remove/reposition panels including those from extensions
- [ ] #4 Built-in panels (transport, artist list, album grid) are registered through the same slot API
<!-- AC:END -->
