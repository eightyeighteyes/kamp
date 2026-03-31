---
id: TASK-57
title: the queue can be cleared with a context menu action
status: To Do
assignee: []
created_date: '2026-03-31 17:44'
updated_date: '2026-03-31 19:44'
labels:
  - feature
  - ui
  - 'estimate: side'
milestone: m-8
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
right click on queue: "clear queue" removes all songs from queue except for currently playing song.

if no song is playing, "clear queue" removes all songs from queue.

right click on unplayed song in queue: "clear remaining" clears all unplayed songs from queue.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Side: two distinct backend operations ("clear queue" keeps current track, "clear remaining" drops all unplayed), right-click context menu on the queue panel itself (separate from the track row menu), plus backend methods for each case.
<!-- SECTION:NOTES:END -->
