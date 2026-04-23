---
id: TASK-170
title: Add Last.fm login step to onboarding flow
status: Done
assignee: []
created_date: '2026-04-22 13:12'
updated_date: '2026-04-23 01:22'
labels: []
milestone: m-30
dependencies: []
priority: medium
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add a Last.fm login card to the onboarding flow, between the Bandcamp step and "Almost Done".

## Implementation

- Add `'lastfm'` to the `OnboardingStep` union type and `STEP_TITLES` in `OnboardingScreen.tsx`
- Add a `handleLastfmLogin` function (reuse the existing Last.fm connect API: `api.connectLastfm(username, password)`)
- Add a JSX card block for the `lastfm` step (same `.onboarding-card` pattern as the Bandcamp step); include username + password fields, a "Connect" button, and a Skip link
- Change `advancePastBandcamp` to call `changeStep('lastfm')` instead of `advancePastLastCard`
- Extract the shared "advance past the last card" logic into a generic helper (e.g. `advancePastCards`) so both Bandcamp and Last.fm handlers call the same terminal step logic

## Notes

- Skip should be low-friction (same pattern as Bandcamp skip)
- The Last.fm password field should be type="password"
- Reuse `api.connectLastfm` and `api.disconnectLastfm` from `api/client.ts`
<!-- SECTION:DESCRIPTION:END -->
