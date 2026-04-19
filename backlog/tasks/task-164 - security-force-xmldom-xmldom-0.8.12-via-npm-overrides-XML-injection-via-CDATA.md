---
id: TASK-164
title: >-
  security: force @xmldom/xmldom >= 0.8.12 via npm overrides (XML injection via
  CDATA)
status: To Do
assignee: []
created_date: '2026-04-19 13:59'
labels:
  - security
  - dependabot
  - npm
milestone: m-29
dependencies:
  - TASK-163
references:
  - kamp_ui/package.json
  - 'https://github.com/advisories/GHSA-wh4c-j3r5-mjhp'
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dependabot alert #1 (high — `GHSA-wh4c-j3r5-mjhp`, CVE-2026-34601): `@xmldom/xmldom@0.8.11` is vulnerable to XML injection via unsafe CDATA serialisation, allowing attacker-controlled markup insertion.

**Dependency chain:** `electron-builder@26.8.1 → app-builder-lib → plist@3.1.0 → @xmldom/xmldom@0.8.11`

**Fix:** Add to the `overrides` block in `kamp_ui/package.json` (alongside the lodash override from TASK-163):
```json
"overrides": {
  "lodash": "^4.18.0",
  "@xmldom/xmldom": "^0.8.12"
}
```
Run `npm install` and verify with `npm list @xmldom/xmldom`.

**Risk:** `plist` uses xmldom to parse `.plist` files during macOS packaging. The 0.8.x API is stable; a patch bump is low risk. Verify with a macOS build (`npm run build:mac` or equivalent).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 npm list @xmldom/xmldom shows >= 0.8.12
- [ ] #2 npm run build completes without errors
- [ ] #3 Dependabot alert #1 is resolved
<!-- AC:END -->
