---
id: TASK-138
title: the area under the queue is an active drop target for Add to Queue
status: To Do
assignee: []
created_date: '2026-04-17 20:43'
labels: []
milestone: m-27
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Albums and tracks can be dragged to the queue for insert operations, but the space at the bottom of the queue is currently an invalid drag target.

Given a user wants to add an album to the end of the queue
When they click and drag the album to the space below the last song in the queue
Then the album is added to the end of the queue

Same applies for a single track.
<!-- SECTION:DESCRIPTION:END -->
