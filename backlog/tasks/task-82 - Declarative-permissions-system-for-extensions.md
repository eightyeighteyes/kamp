---
id: TASK-82
title: Declarative permissions system for extensions
status: To Do
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-05 16:32'
labels:
  - feature
  - security
  - 'estimate: lp'
milestone: m-2
dependencies:
  - TASK-17
  - TASK-19
documentation:
  - project/kampground-ideation.md
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every extension — frontend and backend — must declare the permissions it needs in its manifest. The host enforces declared permissions at load time; an extension that exercises an undeclared capability is rejected.

**Frontend** extensions declare permissions in `package.json` alongside the `kamp-extension` keyword. **Backend** extensions declare permissions in a `[tool.kampground]` table in `pyproject.toml`:

```toml
[tool.kampground]
permissions = ["network.external"]
network.domains = ["api.discogs.com"]
```

**Defined permissions:**
- Frontend: `library.read`, `player.read`, `player.control`, `network.external`, `settings`
- Backend: `network.external` (proxied via `KampContext.fetch()` — see TASK-17), `audio.read`, `library.write`

The install-time UI must show declared permissions to the user. When both `library.read` and `network.external` are declared together (frontend or backend), the UI must display elevated language: *"This extension can read your music library and send data to external servers."* This applies to legitimate scrobblers too — users should understand what they're approving.

Depends on: TASK-17 (KampContext API) for backend enforcement, TASK-19 (contextBridge API) for frontend enforcement.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Frontend extensions declare permissions in package.json manifest; host rejects any KampAPI call not covered by declared permissions
- [ ] #2 Backend extensions declare permissions in [tool.kampground] in pyproject.toml; host rejects any KampContext capability not declared
- [ ] #3 Install-time UI lists all declared permissions for user review
- [ ] #4 Combined library.read + network.external triggers elevated warning at install time
- [ ] #5 Permissions are reviewable after install
- [ ] #6 An extension with no declared permissions cannot access any KampAPI or KampContext capability beyond its ABC contract
<!-- AC:END -->
