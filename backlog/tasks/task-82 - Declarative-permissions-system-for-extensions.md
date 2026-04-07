---
id: TASK-82
title: Declarative permissions system for extensions
status: In Progress
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-07 03:05'
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
Define and implement the declarative permissions system for kamp extensions. Every extension declares the capabilities it needs in its manifest; the host enforces them at load time and rejects any extension that uses an undeclared capability.

**Backend permissions (pyproject.toml `[tool.kampground]`):**
- `network.external` — HTTP/HTTPS via `KampGround.fetch()`; must also declare `network.domains` allowlist
- `audio.read` — receive raw audio bytes (host mediates; extension never gets a file path)
- `library.write` — named atomic mutations only (`update_metadata`, `set_artwork`)

**Frontend permissions (manifest):**
- `library.read`, `player.read`, `player.control`, `network.external`, `settings`

Depends on: TASK-17 (KampGround API) for backend enforcement, TASK-19 (contextBridge API) for frontend enforcement.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Frontend extensions declare permissions in their manifest; host rejects undeclared KampAPI capability access
- [ ] #2 Backend extensions declare permissions in [tool.kampground] in pyproject.toml; host rejects any KampGround capability not declared
- [ ] #3 network.external requires a network.domains allowlist; requests to unlisted domains are rejected
- [ ] #4 Elevated install-time language is shown when an extension combines library.read + network.external
- [ ] #5 User can review granted permissions for any installed extension at any time
- [ ] #6 An extension with no declared permissions cannot access any KampAPI or KampGround capability beyond its ABC contract
<!-- AC:END -->
