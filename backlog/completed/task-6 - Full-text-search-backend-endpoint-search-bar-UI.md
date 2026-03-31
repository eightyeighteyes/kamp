---
id: TASK-6
title: Full-text search (backend endpoint + search bar UI)
status: Done
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-03-30 13:10'
labels:
  - feature
  - search
  - 'estimate: side'
milestone: m-0
dependencies: []
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add full-text search across the library: a `GET /api/v1/search?q=` endpoint on the backend and a search bar in the UI that filters albums and tracks in real time.

SQLite FTS5 is the natural fit for the backend index.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GET /api/v1/search?q= returns matching albums and tracks
- [ ] #2 Search bar in UI filters results as the user types
- [ ] #3 Results include album art thumbnails and are clickable to play
- [ ] #4 Empty query returns no results (not the full library)
- [ ] #5 Cmd K (mac) / Ctrl K (win/linux) brings user to search bar
<!-- AC:END -->
