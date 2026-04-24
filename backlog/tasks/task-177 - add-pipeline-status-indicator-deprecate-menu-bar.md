---
id: TASK-177
title: 'add pipeline status indicator, deprecate menu bar'
status: Done
assignee:
  - Tedd Terry
created_date: '2026-04-24 16:04'
updated_date: '2026-04-24 18:36'
labels:
  - feature
  - ui
  - 'estimate: lp'
milestone: m-31
dependencies: []
priority: medium
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
now that we have an area of the UI for Library Actions (top right of the library pane), it makes sense for us to fully deprecate the macOS menu bar app and move the pipeline status indicator into the application.

If we can keep the same glyph (lines and music note) and behavior (pulsing while tagging/moving, hover shows tooltip that shows what's happening), that would be ideal.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Pipeline status indicator renders in the library toolbar (top right, next to Bandcamp button)
- [ ] #2 Icon pulses while any pipeline stage is active (Extracting, Tagging, Updating artwork, Moving)
- [ ] #3 Tooltip shows the current stage name
- [ ] #4 Indicator is hidden/absent when pipeline is idle
- [ ] #5 macOS menu bar app is fully removed — no menu_bar.py, no --no-menu-bar CLI flag
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Approach

Wire `core.watcher.stage_callback` through the existing WebSocket broadcast mechanism (same pattern as Bandcamp sync status), add a `PipelineIndicator` React component in the Library Actions toolbar, and delete the rumps-based menu bar app entirely.

## Steps

1. `kamp_core/server.py` — add `_notify_pipeline_stage(stage)` + expose on `app.state`
2. `kamp_daemon/__main__.py` — wire `core.watcher.stage_callback`, remove menu bar branch + `--no-menu-bar` CLI args + plist arg
3. Delete `kamp_daemon/menu_bar.py`
4. `kamp_ui/src/preload/kampAPI.ts` — handle `pipeline.stage` WS message, export `onPipelineStage`
5. `kamp_ui/src/preload/index.ts` + `index.d.ts` — expose `api.pipeline.onStage`
6. New `PipelineIndicator.tsx` + update `AlbumGrid.tsx` toolbar + CSS pulse styles
7. `tests/test_server.py` — add `test_notify_pipeline_stage_exposed_on_app_state`

## Acceptance criteria

- Pipeline status indicator renders in the toolbar (top right of library pane)
- Icon pulses when any stage is active (Extracting, Tagging, Updating artwork, Moving)
- Tooltip shows the current stage name
- Indicator is hidden when pipeline is idle
- macOS menu bar app is fully removed (no `--no-menu-bar` flag, no menu_bar.py)
<!-- SECTION:PLAN:END -->
