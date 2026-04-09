---
id: TASK-115
title: >-
  Server-push player events — proactive WebSocket notifications on track change
  and play state
status: To Do
assignee: []
created_date: '2026-04-09 13:01'
updated_date: '2026-04-09 13:02'
labels: []
milestone: m-22
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Currently the WebSocket at `/api/v1/ws` is pull-based: the client sends a "ping" and the server replies with a `player.state` snapshot. Extensions that need to react to track changes (groover, last.fm) are forced to poll.

Replace this with a push model: the server proactively emits events whenever player state transitions occur. The host renderer holds a single WebSocket connection and fans out events to all extension subscribers via the SDK.

## Server changes (`kamp_core/server.py`)

The server needs to detect state transitions internally and push them without waiting for a ping. Two new event types:

- `track.changed` — emitted when `current_track` changes (new track started, queue emptied, or playback stopped). Payload: the full `PlayerStateOut` snapshot at the moment of change.
- `play_state.changed` — emitted when `playing` flips (play → pause, pause → play). Payload: the full `PlayerStateOut` snapshot.

The existing `library.changed` push (already implemented) confirms the pattern works — extend it to cover player state transitions. The server will need a callback hook from `MpvPlaybackEngine` to know when these transitions happen.

`player.state` responses to pings should be retained — the renderer still uses them for regular UI updates.

## SDK changes (`preload/kampAPI.ts`, `shared/kampAPI.ts`)

Add subscription methods to `api.player`:

```ts
api.player.onTrackChange(callback: (state: PlayerState) => void): () => void
api.player.onPlayStateChange(callback: (state: PlayerState) => void): () => void
```

The preload holds the single WebSocket connection and dispatches incoming push events to registered callbacks. Returns an unsubscribe function.

## Sandbox shim (`SandboxedExtensionLoader.tsx`)

Fan out events to community (sandboxed) extensions via a new `kamp:sdk-event` postMessage, mirroring the existing `kamp:sdk-call` pattern. The shim registers the corresponding `api.player.onTrackChange` / `api.player.onPlayStateChange` methods that post `kamp:sdk-subscribe` / `kamp:sdk-unsubscribe` messages to the host, which manages the subscription on their behalf.

## Motivation

- **Groover** currently polls `api.player.getState()` every 2s to detect track changes — this can be replaced with `api.player.onTrackChange`.
- **Last.fm** needs accurate track-start and track-end signals to scrobble correctly. Polling introduces timing error; push events eliminate it.
- Multiple extensions polling independently creates unnecessary load and timing inconsistency — a single server-push fan-out is strictly better.

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 #1 Server emits `track.changed` WebSocket event when `current_track` transitions
- [ ] #2 #2 Server emits `play_state.changed` WebSocket event when `playing` flips
- [ ] #3 #3 `api.player.onTrackChange(cb)` and `api.player.onPlayStateChange(cb)` added to SDK; both return an unsubscribe function
- [ ] #4 #4 Sandboxed extensions receive events via `kamp:sdk-event` postMessage
- [ ] #5 #5 Groover updated to use `onTrackChange` instead of polling
- [ ] #6 #6 Extension Developer Guide updated with subscription API docs
<!-- SECTION:DESCRIPTION:END -->
<!-- AC:END -->
