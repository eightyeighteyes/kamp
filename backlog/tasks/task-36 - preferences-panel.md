---
id: TASK-36
title: Preferences panel
status: In Progress
assignee: []
created_date: '2026-03-29 14:01'
updated_date: '2026-04-05 16:56'
labels:
  - feature
  - ui
  - electron
  - 'estimate: lp'
milestone: m-21
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A centralized place to manage Kamp's config options, implemented as a modal dialog.

**Opening:** accessible from macOS menu bar (App Name → Preferences) and via Cmd/Ctrl+,

**Behaviour:** preferences take effect immediately on change — no Apply or OK button. An ephemeral confirmation indicates the preference was saved.

**Contents:** all user-facing config options with appropriate controls; library path options moved here from wherever they currently live.

**Visual:** dialog background matches the queue panel background. Dismissable via the X in the upper-right corner or Escape key.

Consult with UI Designer before implementation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GET /api/v1/config returns current config values
- [ ] #2 PATCH /api/v1/config persists changes to config.toml
- [ ] #3 All user-facing config options are represented with appropriate controls
- [ ] #4 Settings that require a restart are clearly indicated
- [ ] #5 Invalid values are rejected with a visible error before saving
- [ ] #6 Preferences dialog opens from macOS menu bar (App Name → Preferences) and via Cmd/Ctrl+,
- [ ] #7 Preferences take effect immediately on change; no Apply or OK button
- [ ] #8 An ephemeral confirmation is shown when a preference is saved
- [ ] #9 Library path options are available in the preferences dialog
- [ ] #10 Dialog background matches the queue panel background
- [ ] #11 Dialog can be dismissed with the X button or Escape key
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Implementation Plan

Estimate: LP (2 sides)

### Backend (Python)

1. **`kamp_core/server.py`** — Add `GET /api/v1/config` and `PATCH /api/v1/config`
   - Add `config_values: dict[str, Any] | None` parameter (flat pref values, keyed by dot-notation)
   - Add `on_config_set: Callable[[str, str], None] | None` parameter (raises KeyError/ValueError on bad input)
   - Track config state in `_state["config"]`
   - `GET /api/v1/config` → returns current values (paths/library/artwork/musicbrainz/bandcamp; not ui)
   - `PATCH /api/v1/config` body `{key, value}` → validates via callback, updates `_state["config"]`, returns `{ok: true}` or 422
   - Add `ConfigPatchRequest` Pydantic model

2. **`kamp_daemon/__main__.py`** — Wire config values and callback
   - Build `config_values` dict from Config object before calling `create_app()`
   - Add `_on_config_set(key, value)` callback → calls `config_set(config_path, key, value)`, lets exceptions propagate

3. **`tests/test_server.py`** — Add tests for new endpoints (red/green TDD)

### Frontend (TypeScript/React)

4. **`kamp_ui/src/renderer/src/api/client.ts`**
   - Add `ConfigValues` type (flat dict)
   - Add `getConfig()` → `GET /api/v1/config`
   - Add `patchConfig(key, value)` → `PATCH /api/v1/config`

5. **`kamp_ui/src/renderer/src/store.ts`**
   - Add `configValues: ConfigValues | null`, `prefsOpen: boolean`
   - Add `loadConfig()`, `setConfigValue(key, value)`, `openPrefs()`, `closePrefs()` actions

6. **`kamp_ui/src/renderer/src/components/PreferencesDialog.tsx`** (new)
   - Full dialog following UI Designer design spec
   - Sections: Paths, Library, Artwork, MusicBrainz, Bandcamp (only if configured)
   - Folder pickers via `window.api.openDirectory()` IPC
   - Ephemeral ✓ confirmation per row (1500ms fade)
   - Restart badges on: paths.staging, paths.library, musicbrainz.contact, bandcamp.poll_interval_minutes
   - Dismiss with X button or Escape key

7. **`kamp_ui/src/renderer/src/App.tsx`**
   - Mount `<PreferencesDialog>` (when `prefsOpen`)
   - Add `Cmd/Ctrl+,` keyboard shortcut → `openPrefs()`
   - Listen for `open-preferences` IPC from main process

8. **`kamp_ui/src/main/index.ts`**
   - Set `applicationMenu` with App Name → Preferences (Cmd+,)
   - On click: send `open-preferences` to focused window via IPC

9. **`kamp_ui/src/preload/index.ts` + `index.d.ts`**
   - Expose `onOpenPreferences(callback)` using `ipcRenderer.on`

10. **`kamp_ui/src/renderer/src/assets/main.css`**
    - Add prefs-* CSS from UI Designer

### UI Designer design brief (key decisions)
- Dialog: 520px wide, `background: var(--surface)`, `border-radius: 8px`
- Overlay backdrop: `rgba(0,0,0,0.6)`
- Controls: text/email inputs, number inputs (90px + unit label), textarea (monospace) for path_template, select for bandcamp.format, path-picker rows for folder paths
- Save confirmation: inline `✓` in `--accent` that fades out after 1500ms
- Restart badge: `↺ restart` pill next to label for paths.staging, paths.library, musicbrainz.contact, bandcamp.poll_interval_minutes
<!-- SECTION:PLAN:END -->
