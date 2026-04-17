---
id: TASK-46
title: Cover art in macOS Control Center
status: Done
assignee: []
created_date: '2026-03-30 23:14'
updated_date: '2026-04-17 19:17'
labels:
  - feature
  - macos
  - os-integration
  - 'estimate: side'
milestone: m-27
dependencies: []
priority: low
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Album art is not appearing in the macOS Control Center Now Playing widget, even though `MPMediaItemPropertyArtwork` is set via `MPMediaItemArtwork.alloc().initWithImage_()` in `CoreAudioMediaController.update()`.

Three iterations were attempted in TASK-9 with no change in behavior. Root cause is unknown — no exception is raised (the artwork block is wrapped in `try/except`), but the artwork key may be silently ignored by the system.

Possible directions to investigate:
- Verify that `extract_art()` is returning bytes (add debug logging)
- Verify that `NSData` and `NSImage` are constructed correctly from the bytes
- Check if `initWithImage_:` requires the image to have specific dimensions or format
- Try `initWithBoundsSize:requestHandler:` with a proper block via a different PyObjC approach
- Check if `MPNowPlayingInfoCenter` requires the app to be registered with a bundle identifier before displaying artwork
<!-- SECTION:DESCRIPTION:END -->
