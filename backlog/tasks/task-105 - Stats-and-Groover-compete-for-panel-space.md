---
id: TASK-105
title: Stats and Groover compete for panel space
status: To Do
assignee: []
created_date: '2026-04-09 11:56'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Stats and Groover are both dev tools, but they are meant to emulate possible future experiences. We don't want Main Panel extensions colliding or fighting: they all get their own space.

to repro:
Enable Stats and Groover in Main Panel slots
show Stats
show Groover

expected:
Groover renders in its own view that occupies the entire main slot div

actual:
Groover renders inside the Stats view
<!-- SECTION:DESCRIPTION:END -->
