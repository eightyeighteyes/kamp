---
id: DRAFT-2
title: >-
  Best Release: prefer physical format (LP/CD) over digital/streaming when
  multiple MB results exist
status: Draft
assignee: []
created_date: '2026-03-29 02:58'
updated_date: '2026-03-29 03:00'
labels:
  - feature
  - musicbrainz
  - 'estimate: side'
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When multiple MusicBrainz releases match a lookup, prefer the release closest to the original physical format (LP/CD over digital/streaming).

Date-based tie-breaking (earliest release wins) is already implemented. Remaining work is format/country preference on top of that.

**Open scoping questions before this can start:**
- What ranking heuristic? (release format field, country, date proximity?)
- Fallback when no physical release exists?
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 When multiple MB results exist, physical formats (LP, CD) are preferred over digital/streaming releases
- [ ] #2 Fallback behavior defined and implemented when no physical release exists
- [ ] #3 Existing date-based tie-breaking is preserved
<!-- AC:END -->
