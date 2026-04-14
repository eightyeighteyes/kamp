---
id: TASK-123
title: remove Download Format and Sync Frequency from menu bar app
status: Done
assignee: []
created_date: '2026-04-13 01:53'
updated_date: '2026-04-14 01:25'
labels: []
milestone: m-1
dependencies: []
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Download Format and Sync Frequency submenus should be removed from the macOS menu bar app. These settings are accessible in Preferences and don't need to be in the tray menu.

**Scope:**
- Remove `_format_menu`, `_interval_menu`, `_format_items`, `_interval_items` and all related setup/callback code from `menu_bar.py`
- Remove `_on_format` and `_on_sync_interval` callbacks
- Update `_refresh_bandcamp_items` to drop the format/interval enabled-state logic
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Menu bar no longer shows Download Format submenu
- [ ] #2 Menu bar no longer shows Sync Frequency submenu
- [ ] #3 Both settings remain fully functional via Preferences
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Estimate: Single**

Purely additive removal — no other files touched.
<!-- SECTION:NOTES:END -->
