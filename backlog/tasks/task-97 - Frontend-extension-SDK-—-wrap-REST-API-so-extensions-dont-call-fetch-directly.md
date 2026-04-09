---
id: TASK-97
title: >-
  Frontend extension SDK — wrap REST API so extensions don't call fetch()
  directly
status: To Do
assignee: []
created_date: '2026-04-09 01:18'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Frontend extensions currently communicate with the kamp server by calling `fetch(api.serverUrl + "/api/v1/...")` directly. This leaks REST implementation details (paths, HTTP verbs, JSON shapes) into every extension.

Replace this with a typed SDK object passed to `register(api)` that exposes the server's capabilities as named, documented methods. Extensions should never need to know that there's a REST server underneath.

## Scope

- Define a `KampSDK` type (or expand `api`) with methods mirroring the available REST endpoints, e.g.:
  - `api.player.getState()` → current playback state
  - `api.library.search(query)` → track list
  - `api.library.getAlbumArt(albumArtist, album)` → image URL or blob
- The SDK is built in the preload / host shim and passed into the `register()` call; extensions never hold a raw `serverUrl`
- `api.serverUrl` can remain for now as an escape hatch but should be marked deprecated in the types
- Update `SandboxedExtensionLoader` (and the sandbox shim) to pass the SDK into the iframe instead of just `serverUrl`
- Update the groover example extension to use the SDK
- Update the Extension Developer Guide wiki page to document the SDK instead of raw fetch

## Acceptance Criteria

- [ ] `KampSDK` type defined in `kamp_ui/src/shared/kampAPI.ts` with at minimum `player.getState()` and `library.getAlbumArt()`
- [ ] SDK implementation wired into the preload and passed to first-party extensions via `register(api)`
- [ ] Sandbox shim updated to pass the SDK into community extensions (serialised or proxied via postMessage)
- [ ] `kamp-groover` example updated to use SDK methods instead of raw fetch
- [ ] `api.serverUrl` deprecated (type annotation + console.warn on access) but not yet removed
- [ ] Developer Guide updated to show SDK usage; raw fetch removed from examples
<!-- SECTION:DESCRIPTION:END -->
