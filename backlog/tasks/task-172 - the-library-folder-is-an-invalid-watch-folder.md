---
id: TASK-172
title: the library folder is an invalid watch folder
status: To Do
assignee: []
created_date: '2026-04-23 01:03'
labels: []
milestone: m-30
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
If the user chooses the same folder for the library and watch folders, that's invalid (and the watcher will enter a loop trying to copy files to itself).

Tell the user this is an invalid selection ("Your watch folder can't be the same as your library folder") and have them re-select.
<!-- SECTION:DESCRIPTION:END -->
