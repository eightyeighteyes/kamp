---
id: TASK-17
title: Extension host and KampGround API
status: In Progress
assignee: []
created_date: '2026-03-29 03:12'
updated_date: '2026-04-05 21:21'
labels:
  - feature
  - architecture
  - 'estimate: box set'
milestone: m-2
dependencies: []
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Build the backend extension host and define the `KampGround` public API that extensions use to interact with the daemon. Backend extensions are Python packages declared via `[project.entry-points."kamp.extensions"]`, implementing abstract base classes (`BaseTagger`, `BaseArtworkSource`, etc.).

Per the architecture invariant: the SDK surface must be extracted from two real working extensions, not designed in the abstract. Do not design the API first — implement two real extensions with it, then extract the surface.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Extension host discovers and loads backend extensions via entry points
- [ ] #2 KampGround API covers at minimum: library access, playback control, event subscription
- [ ] #3 API is documented with examples
- [ ] #4 A crash in an extension worker does not take down the daemon
- [ ] #5 Backend extensions receive and return structured data objects (TrackMetadata, ArtworkResult, etc.); no file paths are ever passed to extension code
- [ ] #6 network.external capability is exposed only as KampGround.fetch(url, method, body) — the host makes the request; the extension never calls the network directly; declared network.domains allowlist is enforced per-request
- [ ] #7 library.write capability is exposed only as named atomic mutations (update_metadata, set_artwork); no raw database access is possible through KampGround
<!-- AC:END -->
