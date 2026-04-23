---
id: TASK-139
title: >-
  onboarding flow — welcome, library setup, watch folder, bandcamp, and
  almost-done screens
status: Done
assignee:
  - 88eyes
created_date: '2026-04-17 22:28'
updated_date: '2026-04-22 14:18'
labels:
  - feature
  - ui
  - 'estimate: lp'
milestone: m-30
dependencies: []
priority: high
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Replace the current `SetupScreen.tsx` with a polished multi-step onboarding flow that lives inside the normal app shell. A vinyl record serves as both a visual anchor and a scan progress indicator throughout.

## Flow

| Step | Title bar text | Content | Advance condition |
|------|---------------|---------|-------------------|
| 1 | Welcome to Kamp | "Welcome to **kamp**!" + vinyl rising from bottom | Auto after 5s |
| 2 | Library Setup | "Let's set up your library" + Choose Library Folder button | User picks folder (native picker) |
| 3 | Watch Folder Setup | "While we're waiting…" card + Choose Watch Folder | User picks folder — required, no skip |
| 4 | Bandcamp Setup | "While we're waiting…" card + Log in to Bandcamp + Skip | User logs in or skips |
| 5 | Almost Done | Rotating text strings fading every 4s | Scan completes |

## Vinyl behavior
- Step 1: vinyl rises from bottom (CSS translateY animation)
- Step 2→5: vinyl spins continuously; SVG arc stroke traces the visible bottom semicircle from left (0%) to right (100%) as scan progresses (weight-5 accent stroke)
- Scan completes: vinyl sinks back down off screen, library fades in
- If scan completes during a card step: vinyl sinks and library loads behind it, but the card stays until the user acts

## Key decisions
- Watch folder is set via `PATCH /api/v1/config` with key `"paths.watch_folder"` (no dedicated endpoint)
- Bandcamp step reuses `window.api.bandcamp.beginLogin()` from PreferencesDialog
- Rotating strings are static only (dynamic follow-up: TASK-169)
- Title bar text is a custom rendered element (window uses `titleBarStyle: 'hidden'`)

## Static rotating strings (Almost Done step)
- "Almost done..."
- "You're gonna love this..."
- "What are you gonna listen to first?"

## Files
- NEW: `kamp_ui/src/renderer/src/components/OnboardingScreen.tsx`
- MOD: `kamp_ui/src/renderer/src/App.tsx` — swap SetupScreen, add title bar text
- MOD: `kamp_ui/src/renderer/src/store.ts` — add `setWatchFolderPath`
- MOD: `kamp_ui/src/renderer/src/api/client.ts` — add `patchConfig`
- MOD: `kamp_ui/src/renderer/src/assets/main.css` — vinyl animations, arc, card, rotating text
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Fresh launch with no library shows welcome screen, auto-advances to library setup after 5s
- [ ] #2 Choosing library folder starts the scan (vinyl spins + arc progresses) and advances to watch folder card
- [ ] #3 Watch folder card has no skip — user must pick a folder to proceed
- [ ] #4 Watch folder selection advances to Bandcamp card
- [ ] #5 Bandcamp Skip link advances past Bandcamp step without logging in
- [ ] #6 Bandcamp login triggers existing login flow and advances on success
- [ ] #7 Almost Done screen shows rotating static strings fading every 4s
- [ ] #8 Scan completion: vinyl sinks, library grid appears
- [ ] #9 Scan completes during a card step: vinyl sinks, library loads behind, card stays until user acts
- [ ] #10 Re-opening with an existing library skips onboarding entirely
<!-- AC:END -->
