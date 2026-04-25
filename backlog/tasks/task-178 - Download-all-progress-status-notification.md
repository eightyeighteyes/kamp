---
id: TASK-178
title: Download-all progress / status notification
status: To Do
assignee: []
created_date: '2026-04-24 19:29'
updated_date: '2026-04-25 22:42'
labels:
  - feature
  - bandcamp
  - ux
  - 'estimate: LP'
milestone: m-1
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The "re-download all purchases" operation can take many minutes for large collections (589 items = potentially hours). The user currently has no visibility into progress while it runs.

Surface meaningful feedback during the download-all process:
- Show current item being downloaded (artist / album) — already available via the existing `status_callback` mechanism
- Show a count: "Downloading 42 / 589"
- Surface this somewhere persistent in the UI (not just the ephemeral pipeline indicator) so the user can check back after switching windows

Possible locations:
- Extend the existing sync status in the Bandcamp section of Preferences
- A new notification / banner in the main library view
- Extend the `bandcamp.sync-status` WebSocket event payload to carry `{ current, total }` progress fields alongside the status string
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 User can see which item is currently downloading (artist + album)
- [ ] #2 User can see overall progress (N of M)
- [ ] #3 Progress is visible without opening Preferences
- [ ] #4 No regression to existing manual-sync status indicator
<!-- AC:END -->
