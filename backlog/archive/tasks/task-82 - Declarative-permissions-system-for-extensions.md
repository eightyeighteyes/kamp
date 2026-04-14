---
id: TASK-82
title: Declarative permissions system for extensions
status: Done
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-07 03:59'
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
ordinal: 16000
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
- [x] #1 Frontend extensions declare permissions in their manifest; host rejects undeclared KampAPI capability access
- [x] #2 Backend extensions declare permissions in [tool.kampground] in pyproject.toml; host rejects any KampGround capability not declared
- [x] #3 network.external requires a network.domains allowlist; requests to unlisted domains are rejected
- [x] #4 Elevated install-time language is shown when an extension combines library.read + network.external
- [x] #5 User can review granted permissions for any installed extension at any time
- [x] #6 An extension with no declared permissions cannot access any KampAPI or KampGround capability beyond its ABC contract
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## Implementation

### Backend
- **`kamp_daemon/ext/permissions.py`** (new): `ExtensionPermissions` dataclass + `parse_permissions()` that reads `[tool.kampground.<ep_name>]` (multi-ext packages) or flat `[tool.kampground]` (single-ext packages) from the distribution's pyproject.toml.
- **`kamp_daemon/ext/context.py`**: Added `permissions: frozenset[str]` field to `KampGround`. Gated `fetch()` on `"network.external"` and `update_metadata()`/`set_artwork()`/`stage()` on `"library.write"` — each raises `PermissionError` with a clear message pointing to pyproject.toml if the permission is absent.
- **`kamp_daemon/ext/registry.py`**: Stores `ExtensionPermissions` per class; `register()` now accepts optional `permissions`; new `permissions_for(cls)` accessor.
- **`kamp_daemon/ext/discovery.py`**: Parses permissions at discovery time, passes to registry, and logs a warning when `library.write + network.external` are both declared.
- **`pyproject.toml`**: Added `[tool.kampground.kamp-*]` sections for each built-in extension. `kamp-bandcamp-syncer` declares `["library.write"]`; the two taggers declare `[]`.

### Frontend
- **`kamp_ui/src/shared/kampAPI.ts`**: Added `permissions: string[]` to `ExtensionInfo`.
- **`kamp_ui/src/main/extensions.ts`**: Parses `kamp.permissions` from package.json, attaches to `ExtensionInfo`, and logs an elevated warning when `library.read + network.external` are both declared.
- **`kamp_ui/src/renderer/src/App.tsx`**: Builds a scoped API object per extension (filtered by declared permissions) and passes it to `mod.register()` instead of the full `window.KampAPI`.
- **`kamp_ui/extensions/kamp-example-panel/package.json`**: Added `"kamp": {"permissions": []}`.

### Tests
- **`tests/test_extension_permissions.py`** (new, 22 tests): parsing edge cases — missing toml, invalid toml, flat vs. named subsection, fallback logic, domain parsing.
- **`tests/test_extension_context.py`**: Added 4 new tests for PermissionError gates; updated all mutation/fetch tests to pass required permissions.
- **`tests/test_extension_worker.py`**: Updated worker mutation test to include `library.write` permission on its context.

### Key design decisions
- Named subsection (`[tool.kampground.<name>]`) takes precedence over flat (`[tool.kampground]`), enabling multi-extension packages to give each extension different permissions. Third-party single-extension packages use the flat form.
- `fetch()` has two levels of gating: permission check first (`network.external` declared?), then domain allowlist check (`hostname in allowed_domains?`). An empty allowlist with the permission declared is a valid state that blocks all requests until domains are listed.
- The scoped API passed to frontend extensions is a plain object (not the `window.KampAPI` reference), ensuring undeclared capabilities cannot be accessed through the parameter. `panels.*` and `serverUrl` are always included as the base contract.
<!-- SECTION:FINAL_SUMMARY:END -->
