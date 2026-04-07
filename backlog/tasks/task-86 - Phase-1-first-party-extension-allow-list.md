---
id: TASK-86
title: Phase 1 first-party extension allow-list
status: Done
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-07 02:53'
labels:
  - feature
  - security
  - 'estimate: side'
milestone: m-2
dependencies:
  - TASK-19
documentation:
  - project/kampground-ideation.md
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The `kamp-extension` npm keyword used for frontend extension discovery is uncontrolled — any published npm package can claim it and be loaded as a Phase 1 (contextBridge-access) extension with no iframe isolation. Until the community marketplace ships, this is a privilege escalation risk.

Phase 1 first-party extensions must be declared in a kamp-controlled allow-list (e.g. a signed manifest or a local config file) in addition to having the npm keyword. Extensions not on the allow-list are treated as Phase 2 (community) extensions and rendered in sandboxed iframes, not given contextBridge access.

The allow-list mechanism must be in place before the extension directory or keyword is publicly documented.

Depends on: TASK-19 (contextBridge API, which is the surface being protected).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 First-party (contextBridge) access requires both the kamp-extension keyword and presence on the allow-list
- [ ] #2 An npm package with the keyword but not on the allow-list is loaded as a Phase 2 community extension (sandboxed iframe), not a Phase 1 extension
- [ ] #3 The allow-list format is defined and documented
- [ ] #4 The allow-list mechanism is implemented before the kamp-extension keyword or extension directory is publicly documented
<!-- AC:END -->
