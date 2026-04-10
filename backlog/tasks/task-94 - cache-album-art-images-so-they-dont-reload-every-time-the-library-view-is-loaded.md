---
id: TASK-94
title: >-
  cache album art images so they don't reload every time the library view is
  loaded
status: To Do
assignee: []
created_date: '2026-04-08 13:37'
updated_date: '2026-04-10 14:16'
labels:
  - performance
  - ui
  - 'estimate: side'
milestone: m-26
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
There are a lot of requests to get album art every time the Library view is shown. These should be cached: album art is unlikely to change between view loads.  Album file updates can be the semaphore for cache invalidation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Album art images are served with HTTP cache headers (ETag or Last-Modified) so the browser does not re-fetch on subsequent library views
- [ ] #2 Cache is invalidated when an album's audio files are updated (tag write or artwork update)
- [ ] #3 No visible art flash or reload when navigating away from and back to the library view
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Two-part fix:\n\n**Backend:** The `/albums/{id}/art` endpoint should set `ETag` (hash of the image bytes or the file's mtime) and honour `If-None-Match` to return 304. Alternatively, add `Cache-Control: max-age=3600` and invalidate by changing the URL (append `?v=<mtime>`).\n\n**Frontend:** The simpler approach is URL-based cache busting — include the album's `updated_at` timestamp in the image URL so the browser caches by URL and only re-fetches when the tag actually changes. No custom cache logic needed in React.\n\nPrefer the URL busting approach — it works with the browser's native cache, requires no service worker, and the `updated_at` field is already returned by the albums API.
<!-- SECTION:PLAN:END -->
