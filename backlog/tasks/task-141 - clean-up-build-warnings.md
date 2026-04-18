---
id: TASK-141
title: clean up build warnings
status: Done
assignee: []
created_date: '2026-04-18 02:09'
updated_date: '2026-04-18 02:41'
labels: []
milestone: m-27
dependencies: []
priority: low
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Clean up build/lint warnings across the stack:
1. Python: unclosed SQLite connections in syncer worker functions (ResourceWarning in tests)
2. Python: unused mypy module overrides in pyproject.toml
3. Frontend: prettier formatting warnings
<!-- SECTION:DESCRIPTION:END -->
