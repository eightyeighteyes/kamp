---
id: TASK-128
title: don't distribute Groover with the application
status: Done
assignee: []
created_date: '2026-04-13 11:43'
updated_date: '2026-04-14 21:32'
labels: []
milestone: m-9
dependencies: []
priority: low
ordinal: 9500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
for now, 'Groover' is just an easter egg / dev helper. we shouldn't distribute it with the application bundle. we need to think of a different way of distributing it (npm?).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Stats panel (kamp-example-panel) is removed from the app bundle and first-party allowlist
- [ ] #2 kamp-example-panel is moved to extensions/ at repo root as a minimal boilerplate reference (Phase 2)
- [ ] #3 Groover is explicitly excluded from the production build in electron-builder.yml
- [ ] #4 kamp-groover/package.json has author, license, repository (with directory), and publishConfig fields
- [ ] #5 GitHub Actions workflow for manual npm publish of kamp-groover is in place
- [ ] #6 kamp-groover/index.js is annotated as a developer reference covering the full extension SDK
- [ ] #7 README includes an Extensions section with permissions table and links to both example extensions
<!-- AC:END -->
