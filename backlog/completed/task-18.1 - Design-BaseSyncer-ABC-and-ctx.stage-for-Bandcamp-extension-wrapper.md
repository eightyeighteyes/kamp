---
id: TASK-18.1
title: Design BaseSyncer ABC and ctx.stage() for Bandcamp extension wrapper
status: Done
assignee: []
created_date: '2026-04-06 14:29'
updated_date: '2026-04-06 19:25'
labels:
  - extensions
  - bandcamp
milestone: m-2
dependencies: []
parent_task_id: TASK-18
priority: medium
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-18 (refactor built-ins as extensions) intentionally excluded the Bandcamp syncer because wrapping it requires substantial new API surface that wasn't needed for the tagger/artwork pair.

## Problem

The Bandcamp syncer's lifecycle doesn't fit any existing extension ABC:
- It is a background process (poll interval, Playwright browser, cookie file)
- It needs to deposit downloaded files into the staging area — but extensions have no filesystem access
- It has progress callbacks, credential management, and persistent state

`BaseTagger` and `BaseArtworkSource` are stateless request/response ABCs. The syncer is stateful and long-running.

## Work required

1. **Design `BaseSyncer` ABC** in `kamp_daemon/ext/abc.py`. Key questions:
   - What is the minimal lifecycle interface? (`start()`, `stop()`, `on_error(callback)`)
   - How does the syncer deposit downloads? Options: `ctx.stage(filename, content)` vs a dedicated `StagingWriter` object vs out-of-band

2. **Add `ctx.stage()` to `KampGround`** (or a dedicated staging capability type) so extensions can write files to the staging directory without receiving a raw filesystem path.

3. **Wrap `kamp_daemon.syncer.Syncer`** as `KampBandcampSyncer(BaseSyncer)` in `kamp_daemon/ext/builtin/bandcamp.py`.

4. **Add entry point** in `pyproject.toml` under `[tool.poetry.plugins."kamp.extensions"]`.

## Prior art / constraints
- Syncer uses Playwright (browser automation) and a cookie file — both require filesystem access. `ctx.stage()` solves the output side; cookie file path is an input configuration concern.
- The syncer runs in a background thread managed by `DaemonCore`. The extension model currently assumes request/response; `BaseSyncer` needs a different lifecycle contract.
- TASK-18 left `kamp_daemon/syncer.py` and `kamp_daemon/daemon_core.py` untouched — start there for context.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 BaseSyncer ABC is defined in kamp_daemon/ext/abc.py with a clear lifecycle interface
- [ ] #2 KampGround (or a dedicated capability type) exposes a stage() method so extensions can deposit files to staging without a raw path
- [ ] #3 KampBandcampSyncer in kamp_daemon/ext/builtin/bandcamp.py uses only the public extension API
- [ ] #4 Entry point declared in pyproject.toml under [tool.poetry.plugins."kamp.extensions"]
- [ ] #5 Existing syncer tests continue to pass; new tests cover the wrapper
- [ ] #6 Full CI (pytest, mypy, black) passes
<!-- AC:END -->
