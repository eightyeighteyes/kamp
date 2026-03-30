---
id: TASK-37
title: window dimension states aren't serialized / deserialized on app reload
status: Done
assignee: []
created_date: '2026-03-29 17:54'
updated_date: '2026-03-30 01:33'
labels:
  - bug
  - electron
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: low
ordinal: 4750
---

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added `loadWindowBounds()`/`saveWindowBounds()` in `src/main/index.ts` using Node `fs` + `app.getPath('userData')/window-state.json`. Bounds saved on every `moved`/`resized` event and restored on next launch, falling back to 900×670 if the file is absent.
<!-- SECTION:FINAL_SUMMARY:END -->
