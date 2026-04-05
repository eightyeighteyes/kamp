---
id: TASK-17.1
title: Entry point discovery and ABC conformance validation
status: In Progress
assignee:
  - Claude
created_date: '2026-04-05 16:36'
updated_date: '2026-04-05 19:03'
labels:
  - feature
  - architecture
  - 'estimate: side'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 1100
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the extension discovery mechanism: scan `[project.entry-points."kamp.extensions"]` from installed packages, load each declared entry point, and validate that the loaded class implements the required ABC (`BaseTagger`, `BaseArtworkSource`, etc.). Reject and log any entry point that fails conformance before the daemon activates it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extensions declared via [project.entry-points."kamp.extensions"] are discovered at daemon startup
- [ ] #2 Each entry point is validated against its expected ABC; non-conforming classes are rejected with a clear error naming the package and missing method
- [ ] #3 Valid extensions are registered in the host's extension registry
- [ ] #4 An installed package with no entry points matching the kamp.extensions group is silently ignored
<!-- AC:END -->
