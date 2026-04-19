---
id: TASK-162
title: >-
  security: upgrade vite to 7.3.2 (3 CVEs — file read, fs.deny bypass, map path
  traversal)
status: To Do
assignee: []
created_date: '2026-04-19 13:58'
labels:
  - security
  - dependabot
  - npm
milestone: m-29
dependencies: []
references:
  - kamp_ui/package.json
  - 'https://github.com/advisories/GHSA-p9ff-h696-f583'
  - 'https://github.com/advisories/GHSA-v2wj-q39q-566r'
  - 'https://github.com/advisories/GHSA-4w7w-66w2-5vf9'
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dependabot alerts #4, #5, #6 — all fixed by upgrading vite to 7.3.2.

**Current:** `vite@7.3.1` (direct dev dependency, declared as `^7.2.6` in `kamp_ui/package.json`)
**Fix:** Bump lower bound to `^7.3.2`.

**Vulnerabilities patched:**

| Alert | CVE | Severity | Summary |
|-------|-----|----------|---------|
| #4 | CVE-2026-39363 | **high** | Arbitrary file read via dev server WebSocket (`GHSA-p9ff-h696-f583`) |
| #5 | CVE-2026-39364 | **high** | `server.fs.deny` bypassed with query strings (`GHSA-v2wj-q39q-566r`) |
| #6 | CVE-2026-39365 | medium | Path traversal in optimised deps `.map` handling (`GHSA-4w7w-66w2-5vf9`) |

**Note:** These only affect the vite dev server. They have no impact in production builds, but should still be patched to keep the dev environment safe.

**Steps:**
1. In `kamp_ui/package.json`, change `"vite": "^7.2.6"` → `"vite": "^7.3.2"`.
2. Run `npm install` in `kamp_ui/`.
3. Verify `npm list vite` shows `7.3.2`.
4. Run `npm run build` to confirm no regressions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 npm list vite shows >= 7.3.2
- [ ] #2 npm run build completes without errors
- [ ] #3 Dependabot alerts #4, #5, #6 are resolved
<!-- AC:END -->
