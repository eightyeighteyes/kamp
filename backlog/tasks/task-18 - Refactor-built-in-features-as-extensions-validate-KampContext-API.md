---
id: TASK-18
title: Refactor built-in features as extensions (validate KampContext API)
status: To Do
assignee: []
created_date: '2026-03-29 03:12'
labels:
  - feature
  - extensions
  - refactor
  - 'estimate: lp'
milestone: m-2
dependencies:
  - TASK-17
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Refactor the Bandcamp syncer, MusicBrainz tagger, and artwork fetcher to use the public `KampContext` API as if they were third-party extensions. This validates that the API is sufficient for real use.

Per the architecture: if this is painful, stop and fix the API first before proceeding. The goal is to confirm that all built-in features are buildable using only the public extension SDK.

Depends on the extension host and KampContext API task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Bandcamp syncer, MusicBrainz tagger, and artwork fetcher use only the public KampContext API
- [ ] #2 No built-in feature accesses daemon internals directly
- [ ] #3 All existing tests pass after refactor
- [ ] #4 Any API gaps discovered during refactor are fixed in the KampContext API before this task closes
<!-- AC:END -->
