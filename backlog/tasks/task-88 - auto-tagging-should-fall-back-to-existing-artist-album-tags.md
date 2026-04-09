---
id: TASK-88
title: auto-tagging should fall back to existing artist/album tags
status: Done
assignee: []
created_date: '2026-04-06 02:33'
updated_date: '2026-04-09 19:44'
labels: []
milestone: m-6
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
occasionally, a musicbrainz lookup will mis-tag files, especially newer releases from indies that may not be in the database yet.

If the MusicBrainz lookup doesn't agree with the existing Artist and Album Title tags, log a warning and don't trust the MusicBrainz tag: skip ID3 tags, and move to the album art tagging so the file ends up in the library with the correct tags.

Make this behavior configurable:
[musicbrainz]
trust-musicbrainz-when-tags-conflict = false

Expose this behavior in Preferences.
<!-- SECTION:DESCRIPTION:END -->
