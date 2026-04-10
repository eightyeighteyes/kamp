---
id: TASK-70
title: >-
  a Base Kamp view exists to serve as the first open experience for the
  application
status: To Do
assignee: []
created_date: '2026-04-03 04:23'
updated_date: '2026-04-10 14:17'
labels:
  - feature
  - ui
  - 'estimate: 2xlp'
milestone: m-24
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Base Kamp is the center of Kamp: it offers enticing glimpses into the user's collection, with nudges about what is available for them to listen to without putting their whole library in front of them.

Base Kamp a page with configurable, rearrangeable modules.

This task is just to create the landing page and provide an architecture for the extensible pieces.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A Base Kamp route/view exists and is the default view on app open
- [ ] #2 The page renders a configurable list of modules in a defined layout slot
- [ ] #3 Module order can be rearranged (drag-and-drop or settings)
- [ ] #4 Adding/removing a module is supported without a code change (registration pattern)
- [ ] #5 Empty state shown when no modules are configured
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
**Architecture:** Base Kamp is a page that owns an ordered list of module registrations. Each module is a React component that receives a standard `ModuleProps` interface (nothing domain-specific — modules fetch their own data).\n\nModule registry: a static array (or config-driven object) mapping module IDs to components. Order is persisted in user preferences (existing preferences API).\n\n**Routing:** add a `/` or `/home` route that renders `<BaseKamp />`. Make it the default redirect from the app root.\n\n**Layout:** start with a simple vertical stack. TASK-73/74/75 add carousel/grid/list display variants per module — this task just needs the slot architecture in place.\n\n**Initial modules registered but empty:** New Arrivals (TASK-71) and Last Played (TASK-72) can be stubbed as skeleton cards so the layout is visible before those tasks ship.\n\n**Do not:** build drag-and-drop in this task — just the data model and a settings panel with up/down arrows. Drag-and-drop can be added later without architectural change."]
<!-- SECTION:PLAN:END -->
