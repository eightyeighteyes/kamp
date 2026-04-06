---
id: TASK-17.3
title: KampContext structured data types
status: Done
assignee:
  - Claude
created_date: '2026-04-05 16:36'
updated_date: '2026-04-06 11:26'
labels:
  - feature
  - architecture
  - 'estimate: single'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Define the typed data objects that flow between the extension host and extension code. Extensions receive and return these types exclusively — no file paths, no database handles, no raw dicts.

Initial types needed: `TrackMetadata`, `ArtworkQuery`, `ArtworkResult`. Additional types are added as the real extension implementations in TASK-18 reveal what's needed — do not over-design upfront.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 TrackMetadata, ArtworkQuery, and ArtworkResult are defined as typed dataclasses or similar
- [x] #2 All fields use Python primitive types or other KampContext types; no pathlib.Path, no SQLite connections, no internal daemon types
- [x] #3 Types are importable from a public kamp.extensions module
- [x] #4 Types are serialisable (can round-trip through the worker subprocess IPC boundary)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Implementation Plan

1. `kamp_daemon/ext/types.py` — three @dataclass definitions:
   - TrackMetadata: title, artist, album, album_artist, year (str), track_number (int), mbid (str)
   - ArtworkQuery: mbid, release_group_mbid, album, artist (all str)
   - ArtworkResult: image_bytes (bytes), mime_type (str)

2. Update ABC signatures in `kamp_daemon/ext/abc.py` to match ideation doc:
   - BaseTagger.tag(self, track: TrackMetadata) -> TrackMetadata
   - BaseArtworkSource.fetch(self, query: ArtworkQuery) -> ArtworkResult | None
   (previous stubs used wrong method names and list[str] args)

3. Export types from `kamp_daemon/ext/__init__.py`

4. `tests/test_extension_types.py` — field/type checks + pickle round-trip for all three types
<!-- SECTION:PLAN:END -->
