---
id: TASK-17.4
title: 'KampContext library, playback, and event API'
status: Done
assignee:
  - Claude
created_date: '2026-04-05 16:36'
updated_date: '2026-04-06 11:26'
labels:
  - feature
  - architecture
  - 'estimate: lp'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Define and implement the main KampGround API surface that extensions use to interact with the daemon: library queries, playback control, and event subscription.

Per the architecture invariant: do not design this API in the abstract. Implement it incrementally as TASK-18 (refactoring built-in extensions) reveals what is actually needed. The surface should be extracted from two real working extensions, not specced upfront.

This is the largest subtask of TASK-17 and should be worked in parallel with TASK-18.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 KampGround exposes library query methods sufficient for the MusicBrainz tagger and artwork fetcher to do their work
- [x] #2 KampGround exposes playback control and state query methods
- [x] #3 KampGround exposes an event subscription mechanism for daemon lifecycle events
- [x] #4 All API methods are typed and documented with examples
- [x] #5 No method on KampGround returns a file path, database cursor, or internal daemon object
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Implementation Plan

1. `kamp_daemon/ext/context.py` — `KampGround` class:
   - `PlaybackSnapshot` frozen dataclass: playing, position, duration, volume (all primitives, picklable)
   - `KampGround` dataclass: `playback: PlaybackSnapshot`, `library_tracks: list[TrackMetadata]`
   - `search(query: str) -> list[TrackMetadata]` — client-side filter on library_tracks
   - `subscribe(event: str, callback: Callable) -> None` — stores callbacks; host fires them before invocations
   - All picklable so it can cross the worker subprocess boundary

2. Update `_extension_worker` in worker.py to accept optional `KampGround` and pass to extension constructor: `cls(ctx)`

3. Update `invoke_extension()` to accept optional `KampGround` kwarg; backwards-compatible (defaults to empty context)

4. Export `KampGround`, `PlaybackSnapshot` from `kamp_daemon/ext/__init__.py`

5. `tests/test_extension_context.py`:
   - KampGround pickles cleanly
   - search() filters library_tracks by query string
   - Context passed through to extension constructor in worker
   - Empty/default context works with no args passed to invoke_extension
<!-- SECTION:PLAN:END -->
