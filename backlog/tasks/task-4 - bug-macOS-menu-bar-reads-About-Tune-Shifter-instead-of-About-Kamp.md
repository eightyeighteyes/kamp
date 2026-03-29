---
id: TASK-4
title: 'bug: macOS menu bar reads \"About Tune-Shifter\" instead of \"About Kamp\"'
status: To Do
assignee: []
created_date: '2026-03-29 02:57'
updated_date: '2026-03-29 03:14'
labels:
  - bug
  - macos
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: low
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The macOS menu bar app shows "About Tune-Shifter" in the menu — a leftover hardcoded string from the rebrand. Likely in `menu_bar.py` or `__main__.py`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 macOS menu bar shows "About Kamp" (not "About Tune-Shifter")
- [ ] #2 No other rebrand remnants found in the same area
<!-- AC:END -->
