---
id: TASK-125
title: >-
  move musicbrainz, last.fm and bandcamp session features to 'Services' section
  of preferences
status: Done
assignee: []
created_date: '2026-04-13 01:59'
updated_date: '2026-04-13 22:41'
labels: []
milestone: m-9
dependencies: []
priority: medium
ordinal: 6500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The preferences page is getting long. Move MusicBrainz, Last.fm, and Bandcamp settings into a new "Services" tab between "General" and "Extensions".

**Scope:**
- Add a "Services" tab to the tab bar in `PreferencesDialog.tsx`
- Move the MusicBrainz section (contact email — or remove it per TASK-126), Last.fm section, and Bandcamp section out of the General tab body and into Services
- General tab retains: Paths, Artwork settings, Library path template
- Services tab contains: Bandcamp, Last.fm, MusicBrainz (in that order — most commonly used first)
- No changes to the underlying config keys or server endpoints
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A 'Services' tab exists between General and Extensions in Preferences
- [x] #2 Bandcamp, Last.fm, and MusicBrainz settings appear only under Services, not under General
- [x] #3 General tab is uncluttered (Paths + library settings only)
- [x] #4 No regression in save/load behaviour for any moved setting
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
**Estimate: Single**

Self-contained to `PreferencesDialog.tsx` — move JSX blocks between tab panels, add tab button. No backend changes. Coordinate with TASK-126 (contact email may be gone before this lands).
<!-- SECTION:NOTES:END -->
