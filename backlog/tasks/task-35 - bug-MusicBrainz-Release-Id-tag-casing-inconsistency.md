---
id: TASK-35
title: 'bug: MusicBrainz Release Id tag casing inconsistency'
status: To Do
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-03-31 03:22'
labels:
  - bug
  - musicbrainz
  - tagging
  - 'estimate: single'
milestone: m-7
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A lowercase `musicbrainz release id` tag was observed in the wild, but the tagger writes `MusicBrainz Release Id` (mixed case). May be a tag coming from MusicBrainz data rather than a write bug.

**Needs a concrete repro before scoping a fix.** Required before this can move to To Do:
- A concrete example file or log showing the bad tag
- Confirmation of whether it's a write bug (our tagger) or a read issue (tag written by another tool)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Root cause confirmed (write bug vs. foreign tag)
- [ ] #2 If write bug: tagger consistently writes mixed-case `MusicBrainz Release Id`
- [ ] #3 If foreign tag: reader normalises casing before comparison
<!-- AC:END -->
