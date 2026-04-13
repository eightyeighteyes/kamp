---
id: TASK-124
title: rebrand "staging folder" as "watch folder"
status: To Do
assignee: []
created_date: '2026-04-13 01:54'
updated_date: '2026-04-13 02:09'
labels: []
milestone: m-9
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Any user-visible mention of "staging folder" should be changed to "watch folder" because this is a more intuitive name for the feature.

**Scope:**
- Display labels: Preferences UI (`paths.staging` label → "Watch folder"), README, CLI `--help` text, menu bar status messages, log messages, comments
- Config key: keep `paths.staging` as the canonical TOML key to avoid breaking existing installs; accept `paths.watch_folder` as an alias (read both on load, warn if old key found, write new key)
- Internal Python variable names (`self._staging`, `staging_dir`, etc.) can stay as-is — this is a display rename only unless there is a good reason to rename internals too

**Note:** Do NOT rename the TOML key without a migration path — existing `config.toml` files use `paths.staging` and would silently break.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All user-visible text says 'watch folder' not 'staging folder'
- [ ] #2 Existing config.toml files with paths.staging continue to work without modification
- [ ] #3 README updated
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Estimate: Side**

Mostly mechanical find-and-replace in UI, docs, and help text, plus a small migration shim in `config.py` for the TOML key alias. Don't rename internal Python identifiers unless there's a clear benefit.
<!-- SECTION:NOTES:END -->
