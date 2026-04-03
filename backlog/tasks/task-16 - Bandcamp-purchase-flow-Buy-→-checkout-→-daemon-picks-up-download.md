---
id: TASK-16
title: Bandcamp purchase flow (Buy → checkout → daemon picks up download)
status: To Do
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - bandcamp
  - 'estimate: 2xlp'
milestone: m-1
dependencies:
  - TASK-15
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Allow users to purchase music on Bandcamp from within kamp and have it automatically land in their library. The intended flow: user clicks "Buy" in the storefront browser → Bandcamp checkout completes → daemon detects the new download → library re-scan runs → album appears in kamp.

Depends on the storefront browser task being complete.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 User can complete a Bandcamp purchase without leaving kamp
- [ ] #2 After purchase, the new album appears in the library automatically (no manual sync)
- [ ] #3 Purchase confirmation is shown in the UI
- [ ] #4 Failure cases (payment failure, download delay) are handled gracefully
<!-- AC:END -->
