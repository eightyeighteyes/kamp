---
id: TASK-166
title: bandcamp polling option updates don't apply immediately
status: Done
assignee: []
created_date: '2026-04-20 16:15'
updated_date: '2026-04-20 18:45'
labels: []
milestone: m-30
dependencies: []
priority: medium
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
regression

to repro:
open preferences
set bandcamp polling from 0 to 1: sync should run immediately then every minute afterward
set bandcamp polling from 1 to 0: sync should only run on user request

actual:
no change in polling behavior until application restart

this behavior may have changed when the configuration moved into the database.
<!-- SECTION:DESCRIPTION:END -->
