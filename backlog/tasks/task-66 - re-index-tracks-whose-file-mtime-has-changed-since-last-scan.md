---
id: TASK-66
title: re-index tracks whose file mtime has changed since last scan
status: Done
assignee: []
created_date: '2026-04-02 21:45'
updated_date: '2026-04-02 22:51'
labels:
  - feature
  - library
  - 'estimate: side'
milestone: m-5
dependencies: []
priority: medium
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The scanner currently only reads tags for files not yet in the index (`to_add = on_disk - in_index`). Files already indexed are never re-read, so tag changes made outside of kamp (e.g. adding cover art, fixing metadata with an external editor) are invisible until the database is deleted and a full rescan is done.

## Root cause

`LibraryScanner.scan()` in `kamp_core/library.py` computes `to_add = on_disk - in_index` and only reads tags for new files. There is no mechanism to detect that an existing file's tags have changed.

## Solution

Add a `file_mtime` column (REAL, Unix timestamp) to the `tracks` table. On each scan:

1. Record `path.stat().st_mtime` when a track is first indexed.
2. For files already in the index, compare the current `st_mtime` against the stored value.
3. Re-read tags (and update `file_mtime`) for any file whose mtime has changed.

This catches cover art additions, tag corrections, and any other metadata edits made by external tools without requiring a full re-index.

## Known trigger

Adding cover art to an M4A file with an external editor leaves `embedded_art=0` in the DB and the art never appears in the UI, even after repeated rescans.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 After adding or changing embedded art in an audio file outside of kamp, a library rescan detects the changed mtime and updates the track's tags (including embedded_art) in the index
- [ ] #2 Unchanged files (same mtime) are still skipped — scan performance is not degraded for unmodified libraries
- [ ] #3 file_mtime column is added with a v→v+1 migration; existing tracks get their mtime backfilled from the filesystem on first migration
<!-- AC:END -->
