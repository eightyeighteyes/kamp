---
id: TASK-34
title: Allow user to verify tags before they are written
status: To Do
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-03-31 03:22'
labels:
  - feature
  - ux
  - 'estimate: lp'
milestone: m-7
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Before tags are written to disk, give the user an opportunity to review and confirm (or reject) the proposed changes.

**Not scoped — needs UI design before estimating.** Open questions:
- Interface: CLI prompt, TUI, or GUI modal?
- Granularity: per-file, per-album, or per-field?
- What happens if the user rejects: skip file, queue for manual edit, abort pipeline?
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 User sees proposed tag changes before they are written to disk
- [ ] #2 User can approve or reject changes (granularity TBD in design)
- [ ] #3 Rejected files are handled gracefully (behavior TBD in design)
<!-- AC:END -->
