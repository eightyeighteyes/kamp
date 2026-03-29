---
id: DRAFT-3
title: >-
  Nested folders: recurse into subdirectories in staging and treat each leaf
  folder as an album
status: Draft
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-03-29 03:00'
labels:
  - feature
  - ingest
  - 'estimate: side'
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When a folder-of-folders is dropped into staging, recurse into subdirectories and treat each leaf folder as an album rather than failing or ignoring nested structure.

**Open scoping questions before this can start:**
- Does each subfolder get its own MusicBrainz lookup?
- How are mixed-album folders (multiple artists/albums in one tree) handled?
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Dropping a folder-of-folders into staging processes each leaf folder as an album
- [ ] #2 Each subfolder triggers its own MusicBrainz lookup (or decision documented if not)
- [ ] #3 Mixed-album folder behavior defined and tested
<!-- AC:END -->
