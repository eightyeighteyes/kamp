---
id: TASK-17.3
title: KampContext structured data types
status: In Progress
assignee:
  - Claude
created_date: '2026-04-05 16:36'
updated_date: '2026-04-05 20:12'
labels:
  - feature
  - architecture
  - 'estimate: single'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 1300
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Define the typed data objects that flow between the extension host and extension code. Extensions receive and return these types exclusively — no file paths, no database handles, no raw dicts.

Initial types needed: `TrackMetadata`, `ArtworkQuery`, `ArtworkResult`. Additional types are added as the real extension implementations in TASK-18 reveal what's needed — do not over-design upfront.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 TrackMetadata, ArtworkQuery, and ArtworkResult are defined as typed dataclasses or similar
- [ ] #2 All fields use Python primitive types or other KampContext types; no pathlib.Path, no SQLite connections, no internal daemon types
- [ ] #3 Types are importable from a public kamp.extensions module
- [ ] #4 Types are serialisable (can round-trip through the worker subprocess IPC boundary)
<!-- AC:END -->
