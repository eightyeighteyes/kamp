---
id: TASK-137
title: Files without an Album tag are organized using their Song Title
status: Done
assignee: []
created_date: '2026-04-17 20:23'
updated_date: '2026-04-17 22:10'
labels: []
milestone: m-27
dependencies: []
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
If there is no Album tag in a file, we currently organize it as though there is no artist or album information.

With the referenced file, there are two issues:
* The Artist is present in the file tags ("Mndsgn.") - It looks like Album Artist isn't present, so we have no fallback.
* The Album isn't present in file tags, but the file is its own standalone release and shouldn't be grouped with other files that don't have album tags

In the Library view, we should show this file as its own "album" with the song title in *Italics* to indicate the metadata issue.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each track without an Album tag appears as its own album entry in the Library view
- [ ] #2 The song title is displayed in italics for missing-album entries to indicate the metadata issue
- [ ] #3 Clicking a missing-album card opens the track list and plays correctly
- [ ] #4 Context menu Play Next / Add to Queue work for missing-album entries
- [ ] #5 Search results correctly include missing-album tracks
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## Implementation

**Backend (`kamp_core/library.py`, `kamp_core/server.py`)**

- Added `missing_album: bool = False` and `file_path: str = ""` fields to `AlbumInfo` dataclass
- Rewrote `albums()` to use a UNION ALL query: tracks with albums group as before; tracks with no album tag each get their own entry with `title` as the display album name, `missing_album=True`, and the track's `file_path` as a unique key
- Updated `_SORT_CLAUSES` to use `sort_date_added`/`sort_last_played` aliases produced by the UNION subquery (previously used `MIN()`/`MAX()` aggregate aliases that don't work across a UNION)
- Added `missing_album` and `file_path` fields to `AlbumOut` response model
- All endpoints that look up tracks by `(album_artist, album)` now accept an optional `file_path` parameter and use `get_track_by_path()` when it's provided: `GET /api/v1/tracks`, `GET /api/v1/album-art`, `POST /api/v1/player/play`, and all album queue endpoints
- Fixed `search_library` to match missing-album entries by `file_path` (since their DB album is `""` but AlbumInfo.album is the display title)

**Frontend (`kamp_ui/src/renderer/src/`)**

- Extended `Album` type with `missing_album: boolean` and `file_path: string`
- Updated `artUrl`, `getTracksForAlbum`, `playAlbum`, `addAlbumToQueue`, `playAlbumNext`, `insertAlbumAt` in `client.ts` to accept optional `filePath` and include it in requests
- `AlbumGrid.tsx`: italicize album title when `missing_album`; use `file_path` as React key; `isActive` check uses `file_path` comparison for missing-album cards; drag data includes `file_path`; context menu passes `file_path`
- `store.ts`: all album actions updated to thread `filePath` through to the API; `loadTracks` uses `file_path` as the cache key for missing-album entries; `selectAlbum` and `refreshOpenAlbum` pass `album.file_path`
- `TrackList.tsx`, `SearchView.tsx`, `QueuePanel.tsx`: all `playTrack`/`addAlbumToQueue`/`playAlbumNext`/`insertAlbumAt` calls pass `file_path` where applicable

**Tests**

- 3 new `TestLibraryIndex` tests: single missing-album track gets its own entry, two missing-album tracks each get separate entries, missing-album and normal albums coexist correctly
- 3 new `TestMissingAlbumEndpoints` server tests: `AlbumOut` includes new fields, tracks endpoint uses `file_path` when provided, album-art endpoint uses `file_path` when provided
<!-- SECTION:FINAL_SUMMARY:END -->
