---
id: TASK-169
title: dynamic rotating strings in onboarding Almost Done screen
status: Done
assignee: []
created_date: '2026-04-22 02:11'
updated_date: '2026-04-23 01:48'
labels:
  - feature
  - ui
  - 'estimate: side'
milestone: m-30
dependencies:
  - TASK-139
priority: low
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Extend the "Almost Done" onboarding screen (TASK-139) with dynamic rotating strings that reference live scan data, replacing the static-only strings shipped in TASK-139.

## Proposed dynamic strings
- "I personally recommend {{song currently being scanned}}..."
- "Or perhaps {{artist there's a lot of}}..."
- "Maybe some {{completely made-up genre}}..."
- "Ooooh, I haven't heard {{artist currently being scanned}} in ages!"

## What's needed
1. Extend `GET /api/v1/library/scan/progress` to expose: current file being scanned, running artist frequency map, genre list
2. Update the rotating string cycle in OnboardingScreen to interpolate live data into the dynamic strings when available
3. Fall back gracefully to static strings if the data fields are absent
<!-- SECTION:DESCRIPTION:END -->
