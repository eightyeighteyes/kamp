---
id: DRAFT-8
title: preferences panel
status: Draft
assignee: []
created_date: '2026-03-29 14:01'
updated_date: '2026-03-29 14:02'
labels:
  - feature
  - ui
  - 'estimate: side'
milestone: m-0
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
I want a centralized place to manage the under-the-hood config options of Kamp
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GET /api/v1/config returns current config values
- [ ] #2 PATCH /api/v1/config persists changes to config.toml
- [ ] #3 Preferences panel is accessible from the UI (menu bar or settings icon)
- [ ] #4 All user-facing config options are represented with appropriate controls
- [ ] #5 Settings that require a restart are clearly indicated
- [ ] #6 Invalid values are rejected with a visible error before saving
<!-- AC:END -->
