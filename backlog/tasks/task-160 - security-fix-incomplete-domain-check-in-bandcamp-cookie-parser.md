---
id: TASK-160
title: 'security: fix incomplete domain check in bandcamp cookie parser'
status: To Do
assignee: []
created_date: '2026-04-19 13:48'
labels:
  - security
  - codeql
milestone: m-29
dependencies: []
references:
  - kamp_daemon/bandcamp.py#L401
  - tests/test_extension_permissions.py#L94
  - scripts/spike_bandcamp_http.py#L53
  - 'https://github.com/teddyterry/kamp/security/code-scanning/2'
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CodeQL alerts #2, #3, #4 (`py/incomplete-url-substring-sanitization`): three locations use `in` to check if a URL/domain string contains a trusted hostname, which allows prefix/suffix bypasses.

**Alert #2 (production — high priority):** `kamp_daemon/bandcamp.py:401`
```python
if len(parts) >= 7 and "bandcamp.com" in parts[0]:
```
A cookie domain like `evil-bandcamp.com` or `notbandcamp.com` would pass this check. Fix: `parts[0] == "bandcamp.com" or parts[0].endswith(".bandcamp.com")`.

**Alert #3 (spike script):** `scripts/spike_bandcamp_http.py:53` — throwaway script; dismiss the alert as "used in tests" or delete the file if unused.

**Alert #4 (test file):** `tests/test_extension_permissions.py:94` — test uses `"api.example.com" in url`; update to use `url == "https://api.example.com/..."` or `url.startswith(...)` with a full scheme+host prefix.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bandcamp.py cookie parser uses exact/suffix domain match, not substring
- [ ] #2 test_extension_permissions.py URL check cannot be bypassed by a substring match
- [ ] #3 spike script alert is dismissed or the file is deleted
- [ ] #4 CodeQL alerts #2, #3, #4 are resolved
<!-- AC:END -->
