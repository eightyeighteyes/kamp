---
id: TASK-171
title: 'Onboarding exit polish: "All set!" string and delay until album art loads'
status: To Do
assignee: []
created_date: '2026-04-22 13:12'
updated_date: '2026-04-22 14:18'
labels: []
milestone: m-30
dependencies: []
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two polish items for the transition from the onboarding "Almost Done" screen to the library:

## 1. "All set!" completion string

When the scan finishes, replace the rotating strings on the "Almost Done" screen with a static "All set!" message before the vinyl sinks. Currently the scan-done effect immediately begins the sinking animation; instead it should:
- Stop the rotating string interval
- Snap to "All set!" (no fade needed, or a quick fade-in)
- Hold briefly (~500ms) so the user can read it
- Then begin the sinking animation

## 2. Delay library reveal until album art is ready

After the vinyl sinks, `onComplete` is called which flips the app to the library view. At that point, album art may not yet be in the browser's image cache, causing a flash of broken/missing art thumbnails. To avoid this:

- After `loadLibrary()` completes in the store, fire a speculative preload of the first N (e.g. 20) album art URLs using `new Image()` before calling `onComplete`
- Or: add a short fixed delay (e.g. 800–1000ms) after the vinyl sinks before calling `onComplete`, giving the browser time to begin fetching art in the background
- The simpler fixed-delay approach is acceptable if speculative preloading proves complex

## Notes

- The sinking animation is ~600ms (`onboarding-vinyl-sink`); the hold should come BEFORE the sink begins, not after
- `onCompleteRef.current()` is what triggers the library reveal — the delay should wrap that call
- Scan-done logic lives in the `useEffect` on `scanStatus` in `OnboardingScreen.tsx`
<!-- SECTION:DESCRIPTION:END -->
