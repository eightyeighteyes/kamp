---
id: TASK-159
title: 'security: review path injection in library-path API endpoint'
status: In Progress
assignee: []
created_date: '2026-04-19 13:48'
updated_date: '2026-04-19 20:15'
labels:
  - security
  - codeql
milestone: m-29
dependencies: []
references:
  - kamp_core/server.py#L487
  - 'https://github.com/teddyterry/kamp/security/code-scanning/5'
priority: medium
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CodeQL alert #5 (error — `py/path-injection`): `kamp_core/server.py:487` builds a `Path` from user-supplied input (`req.path`) without restricting it to a safe root.

```python
candidate = Path(req.path).expanduser().resolve()
```

The endpoint validates that the path exists and is a directory, but a malicious or buggy client could supply any path on the filesystem (e.g. `/etc`, `/`), which kamp would then use as the library root and scan.

**Fix options to evaluate:**
1. Add a deny-list for obviously dangerous roots (`/`, `/etc`, `/System`, etc.).
2. Restrict to paths under `~` (expanduser result must start with `Path.home()`).
3. Accept the risk as intentional — this is a local-only API and the user controls their own machine — and suppress with a CodeQL annotation plus a comment explaining why.

**Recommendation:** Option 3 is likely correct given kamp is a local desktop app; document the rationale and suppress the alert rather than adding hollow validation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A decision is made and documented (fix or intentional suppression)
- [x] #2 If suppressed, a comment explains why the risk is acceptable for a local-only API
- [x] #3 CodeQL alert #5 is resolved or marked as dismissed with a reason
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Chose Option 3 (intentional suppression). Added a four-line rationale comment at kamp_core/server.py:487 explaining that kamp binds only to 127.0.0.1, is reachable only by the same-user Electron frontend, and legitimately needs to accept any user-chosen path (including external drives). Added `# nosec: py/path-injection` and `# noqa: S603` inline annotations for CodeQL/bandit. All 135 server tests pass; mypy clean.
<!-- SECTION:FINAL_SUMMARY:END -->
