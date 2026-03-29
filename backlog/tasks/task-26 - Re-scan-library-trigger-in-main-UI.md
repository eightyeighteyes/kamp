---
id: TASK-26
title: Re-scan library trigger in main UI
status: To Do
assignee: []
created_date: '2026-03-29 14:09'
updated_date: '2026-03-29 14:10'
labels:
  - feature
  - ui
  - library
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expose the existing `POST /api/v1/library/scan` endpoint in the main UI so users can trigger a re-scan outside of the first-run setup flow. The scan store action and progress polling already exist in `store.ts` — this is purely a UI affordance.

Place it in the main UI for now (e.g. a menu item or toolbar button). It should eventually move into the preferences panel (TASK-25/DRAFT-8) once automatic library watching (TASK-5) handles the common case, making manual re-scan an infrequent power-user action.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A re-scan action is accessible from the main UI without entering the setup flow
- [ ] #2 The scan progress bar is shown while the scan runs
- [ ] #3 On completion, the library view updates to reflect any changes
- [ ] #4 The UI affordance is clearly a temporary placement (comment in code) pending move to preferences
<!-- AC:END -->
