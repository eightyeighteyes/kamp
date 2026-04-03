---
id: TASK-15
title: 'Bandcamp storefront browser (collection, wishlist, followed artists)'
status: To Do
assignee: []
created_date: '2026-03-29 03:11'
updated_date: '2026-04-03 04:36'
labels:
  - feature
  - ui
  - bandcamp
  - 'estimate: 2xlp'
milestone: m-1
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Embed a Bandcamp storefront browser in kamp: let users browse their Bandcamp collection, wishlist, and followed artists without leaving the app. This is the "discovery" surface described in the vision statement.

Design question to resolve: webview embedding vs API-driven UI (Bandcamp has no public API — a webview may be the only option).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 User can browse their Bandcamp collection inside kamp
- [ ] #2 User can browse their wishlist and followed artists
- [ ] #3 Navigation between storefront and library is fluid
- [ ] #4 Bandcamp session/auth is handled without requiring re-login on each launch
<!-- AC:END -->
