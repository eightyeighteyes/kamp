---
id: TASK-17
title: Extension host and KampContext API
status: To Do
assignee: []
created_date: '2026-03-29 03:12'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - architecture
  - 'estimate: box set'
milestone: m-2
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Build the backend extension host and define the `KampContext` public API that extensions use to interact with the daemon. Backend extensions are Python packages declared via `[project.entry-points."kamp.extensions"]`, implementing abstract base classes (`BaseTagger`, `BaseArtworkSource`, etc.).

Per the architecture invariant: the SDK surface must be extracted from two real working extensions, not designed in the abstract. Do not design the API first — implement two real extensions with it, then extract the surface.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extension host discovers and loads backend extensions via entry points
- [ ] #2 KampContext API covers at minimum: library access, playback control, event subscription
- [ ] #3 API is documented with examples
- [ ] #4 A crash in an extension worker does not take down the daemon
<!-- AC:END -->
