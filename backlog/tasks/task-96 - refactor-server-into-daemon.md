---
id: TASK-96
title: refactor server into daemon
status: In Progress
assignee: []
created_date: '2026-04-08 18:27'
updated_date: '2026-04-09 17:44'
labels: []
milestone: m-6
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Currently `kamp daemon` (background processing, managed by launchd) and `kamp server` (HTTP API, started by Electron) are two separate processes. There is no strong reason for the split — it introduces real costs with no offsetting benefit:

- Two processes competing over the library database
- Two different lifecycle models: Electron manages `kamp server`, launchd manages `kamp daemon`
- State that should be shared (playback, pipeline status) crosses an IPC boundary unnecessarily

The right shape is a single long-running process: `kamp daemon` starts uvicorn as a thread alongside its existing work, Electron starts `kamp daemon` instead of `kamp server`, and `kamp server` becomes an alias or is retired. This matches how every similar player (Plex, Jellyfin, Navidrome) works — the HTTP server is a component of the service, not a separate service.

The code inside both processes doesn't need to change; only the entry point and lifecycle wiring.

Note: the extension sandbox (TASK-87) is unaffected. The sandbox applies only to extension worker subprocesses spawned by `invoke_extension()`. The main process (daemon/server combined) remains fully unsandboxed and retains direct filesystem access."
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 kamp daemon starts the HTTP API server (uvicorn) as a thread on startup — no separate kamp server process needed
- [ ] #2 Electron main process starts kamp daemon instead of kamp server
- [ ] #3 kamp server subcommand is aliased to kamp daemon or removed with a clear deprecation message
- [ ] #4 launchd plist (if present) and the auto-start logic in Electron both reference a single entry point
- [ ] #5 All existing server and daemon tests continue to pass
<!-- AC:END -->
