---
id: TASK-33
title: 'Configurable album art search (Bandcamp, Apple, Spotify, Qobuz)'
status: To Do
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - backend
  - 'estimate: lp'
milestone: m-7
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Allow users to configure additional album art sources beyond the current default. Each source (Bandcamp, Apple, Spotify, Qobuz) requires its own API integration and auth flow; estimate per source is ~Side to LP.

**Not scoped enough to start.** Needs a design pass on:
- Config schema for source list and fallback order
- Auth flow per source
- How to handle sources that require API keys
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 User can configure ordered list of art sources in config
- [ ] #2 At least one additional source (TBD in design) implemented end-to-end
- [ ] #3 Fallback order respected when preferred source returns no result
<!-- AC:END -->
