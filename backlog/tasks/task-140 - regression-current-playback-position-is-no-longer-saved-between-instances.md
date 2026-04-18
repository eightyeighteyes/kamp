---
id: TASK-140
title: 'regression: current playback position is no longer saved between instances'
status: To Do
assignee: []
created_date: '2026-04-18 01:52'
labels: []
milestone: m-27
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
to repro:
start playback of a song
seek to the middle of the track
let it play for at least 5 seconds
close Kamp
reopen Kamp

expected:
the track position is within 5 seconds of where it was when Kamp was closed

actual:
the track position is at the beginning of the last played track
<!-- SECTION:DESCRIPTION:END -->
