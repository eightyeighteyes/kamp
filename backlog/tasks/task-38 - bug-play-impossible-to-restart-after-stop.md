---
id: TASK-38
title: 'bug: play impossible to restart after stop'
status: To Do
assignee: []
created_date: '2026-03-29 17:59'
updated_date: '2026-03-29 18:00'
labels:
  - bug
  - playback
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: high
ordinal: 875
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
to repro:
start playing a track: play button changes to 'pause' state
press stop: play button remains 'pause'
press pause to get play button back
press play

expected:
cursor index returns to 0 of track
pause button becomes play button
pressing play again starts track at beginning

actual:
track can't be restarted
nothing can be played until a different track is selected
<!-- SECTION:DESCRIPTION:END -->
