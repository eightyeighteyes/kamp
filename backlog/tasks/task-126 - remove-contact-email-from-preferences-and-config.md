---
id: TASK-126
title: remove 'contact email' from preferences and config
status: Done
assignee: []
created_date: '2026-04-13 02:06'
updated_date: '2026-04-13 22:22'
labels: []
milestone: m-9
dependencies: []
priority: medium
ordinal: 4500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Users shouldn't have to supply a contact email just to use MusicBrainz features.

Hardcode the MusicBrainz User-Agent contact to `tedd.e.terry+kamp@gmail.com` in the source. Remove `musicbrainz.contact` from the config schema, the Preferences UI, CLI help text, and the first-run setup wizard.

**Scope:**
- `kamp_daemon/__main__.py`: replace `config.musicbrainz.contact` with the hardcoded email in the `musicbrainzngs.set_useragent()` call
- `kamp_daemon/config.py`: remove `contact` field from `MusicBrainzConfig`; remove from default config template written on first run
- `kamp_core/server.py`: remove `musicbrainz.contact` from `_config_values` and `_INT_CONFIG_KEYS` / `_BOOL_CONFIG_KEYS`
- `PreferencesDialog.tsx`: remove the MusicBrainz contact email InputRow
- README and CLI `--help`: remove any mention of the contact email requirement
- Existing `config.toml` files with `[musicbrainz] contact = "..."` should load without error (ignore unknown keys on parse, or strip the key silently)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 musicbrainz.contact is not shown in Preferences
- [ ] #2 musicbrainz.contact is not required in config.toml
- [ ] #3 MusicBrainz tagging continues to work after the change
- [ ] #4 Existing config.toml files with a contact field load without error
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Estimate: Single**

Mechanical removal across ~5 files. The only wrinkle is ensuring existing config.toml files with the old key don't crash on load — check how tomllib/Config.load() handles unknown keys (likely fine since Pydantic ignores extras by default, but verify).
<!-- SECTION:NOTES:END -->
