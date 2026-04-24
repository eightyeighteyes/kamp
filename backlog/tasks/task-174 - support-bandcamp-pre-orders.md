---
id: TASK-174
title: support bandcamp pre-orders
status: To Do
assignee: []
created_date: '2026-04-24 01:45'
labels: []
milestone: m-1
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
a user can purchase albums on bandcamp before they are officially released. this is known as a "pre-order".

in our current behavior, it is likely that we parse them as purchases, see that there's nothing to download, then move on (keeping them in bandcamp state).

additionally, sometimes artists release single songs from the album ahead of the whole album's release date.

we need to consult with design about how to handle these. I can think of a few options:
1. parse the release date of pre-orders and wait until that date to download the whole album
2. check on every sync to see if there is anything to download. this may result in repeated downloads of the same song.
3. parse the tracklist to see what's available. only try to download if there are more tracks than there were last time.
4. wait until the pre-order badge disappears from the album to attempt to download.

The simplest option is probably option 4, with possible future expansion to option 2 or 3 (possibly with a user option to control behavior). Option 1 is unreliable for a number of reasons: the label actually has to release the album, so it might not get released on the actual release date.
<!-- SECTION:DESCRIPTION:END -->
