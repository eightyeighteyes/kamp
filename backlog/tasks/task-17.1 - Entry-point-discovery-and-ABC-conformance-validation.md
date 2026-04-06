---
id: TASK-17.1
title: Entry point discovery and ABC conformance validation
status: Done
assignee:
  - Claude
created_date: '2026-04-05 16:36'
updated_date: '2026-04-06 11:26'
labels:
  - feature
  - architecture
  - 'estimate: side'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the extension discovery mechanism: scan `[project.entry-points."kamp.extensions"]` from installed packages, load each declared entry point, and validate that the loaded class implements the required ABC (`BaseTagger`, `BaseArtworkSource`, etc.). Reject and log any entry point that fails conformance before the daemon activates it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Extensions declared via [project.entry-points."kamp.extensions"] are discovered at daemon startup
- [x] #2 Each entry point is validated against its expected ABC; non-conforming classes are rejected with a clear error naming the package and missing method
- [x] #3 Valid extensions are registered in the host's extension registry
- [x] #4 An installed package with no entry points matching the kamp.extensions group is silently ignored
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Implementation Plan

1. Create `kamp_daemon/ext/` package:
   - `__init__.py` — exports `BaseTagger`, `BaseArtworkSource`, `ExtensionRegistry`, `discover_extensions`
   - `abc.py` — minimal ABCs (`BaseTagger`, `BaseArtworkSource`) with stub abstract methods; will be refined when real extensions are built
   - `registry.py` — `ExtensionRegistry` holding `list[type[BaseTagger]]` and `list[type[BaseArtworkSource]]`; `register(cls)` dispatches to correct bucket
   - `discovery.py` — `discover_extensions(registry)` using `importlib.metadata.entry_points(group="kamp.extensions")`; logs rejections naming package + missing methods; handles ImportError per entry point

2. Wire into `DaemonCore.start()` — call `discover_extensions(self._registry)` at startup; store registry on the core

3. Add `tests/test_extension_discovery.py` covering:
   - Conforming BaseTagger subclass → registered in `registry.taggers`
   - Conforming BaseArtworkSource subclass → registered in `registry.artwork_sources`
   - Non-conforming class → rejected log names package + missing abstract methods
   - No `kamp.extensions` entry points → empty registry, no error
   - ImportError on load → rejected with clear log, daemon continues
<!-- SECTION:PLAN:END -->
