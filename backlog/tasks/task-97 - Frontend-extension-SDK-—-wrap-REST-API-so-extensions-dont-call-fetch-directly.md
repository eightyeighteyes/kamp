---
id: TASK-97
title: >-
  Frontend extension SDK — wrap REST API so extensions don't call fetch()
  directly
status: To Do
assignee: []
created_date: '2026-04-09 01:18'
updated_date: '2026-04-09 01:37'
labels: []
milestone: m-2
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

## CSP / external network note

The SDK approach is the correct solution for extensions (e.g. a Bandcamp extension) that need to contact external origins. Rather than relaxing the iframe CSP to allow external `connect-src`, extension-specific API methods (`api.bandcamp.*`) should proxy through the kamp server. The CSP stays locked to `127.0.0.1:8000`; the server handles the outbound request.

This mirrors the backend pattern: `KampGround.fetch()` already proxies all network access through the daemon rather than letting the worker subprocess call out directly. The same principle applies here — the kamp server is the single network egress point for all extension traffic.

Implication: as extension-specific namespaces are added to the SDK, corresponding proxy routes will need to be added to the kamp HTTP server.

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `KampSDK` type defined in `kamp_ui/src/shared/kampAPI.ts` with at minimum `player.getState()` and `library.getAlbumArt()`
- [ ] #2 SDK implementation wired into the preload and passed to first-party extensions via `register(api)`
- [ ] #3 Sandbox shim updated to pass the SDK into community extensions (serialised or proxied via postMessage)
- [ ] #4 `kamp-groover` example updated to use SDK methods instead of raw fetch
- [ ] #5 `api.serverUrl` deprecated (type annotation + console.warn on access) but not yet removed
- [ ] #6 Developer Guide updated to show SDK usage; raw fetch removed from examples
<!-- SECTION:DESCRIPTION:END -->
<!-- AC:END -->
