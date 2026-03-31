---
id: TASK-47
title: media queue needs to survive app restart
status: To Do
assignee: []
created_date: '2026-03-30 23:18'
updated_date: '2026-03-31 03:44'
labels:
  - feature
  - backend
  - 'estimate: single'
milestone: m-8
dependencies: []
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
currently, if i am playing a song, we remember the track position of the current song, but we don't remember that it was in the middle of a queue: that tracks precede and follow it. thus, when the app restarts and the current song ends, the queue is empty and nothing is playing
<!-- SECTION:DESCRIPTION:END -->
