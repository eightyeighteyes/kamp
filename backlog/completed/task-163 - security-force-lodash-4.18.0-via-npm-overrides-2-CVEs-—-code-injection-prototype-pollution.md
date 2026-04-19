---
id: TASK-163
title: >-
  security: force lodash >= 4.18.0 via npm overrides (2 CVEs — code injection,
  prototype pollution)
status: Done
assignee: []
created_date: '2026-04-19 13:59'
updated_date: '2026-04-19 19:33'
labels:
  - security
  - dependabot
  - npm
milestone: m-29
dependencies: []
references:
  - kamp_ui/package.json
  - 'https://github.com/advisories/GHSA-r5fr-rjxr-66jc'
  - 'https://github.com/advisories/GHSA-f23m-r3pf-42rh'
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dependabot alerts #2, #3 — lodash is a transitive dependency via `electron-builder → app-builder-lib`, pinned at `4.17.23`. There is no upstream fix available yet in `electron-builder`; use npm `overrides` to force the patched version.

**Dependency chain:** `electron-builder@26.8.1 → app-builder-lib → @malept/flatpak-bundler → lodash@4.17.23`

**Vulnerabilities patched:**

| Alert | CVE | Severity | Summary |
|-------|-----|----------|---------|
| #3 | CVE-2026-4800 | **high** | Code injection via `_.template` imports key names (`GHSA-r5fr-rjxr-66jc`) |
| #2 | CVE-2026-2950 | medium | Prototype pollution via array path bypass in `_.unset` / `_.omit` (`GHSA-f23m-r3pf-42rh`) |

**Fix:** Add an `overrides` block to `kamp_ui/package.json`:
```json
"overrides": {
  "lodash": "^4.18.0"
}
```
Then run `npm install` and verify with `npm list lodash`.

**Risk:** Forcing a minor lodash bump is low risk — lodash 4.x has a stable API. Run the electron-builder smoke test (a local build) to confirm packaging still works.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 npm list lodash shows >= 4.18.0 for all instances
- [x] #2 npm run build completes without errors
- [x] #3 Dependabot alerts #2 and #3 are resolved
<!-- AC:END -->
