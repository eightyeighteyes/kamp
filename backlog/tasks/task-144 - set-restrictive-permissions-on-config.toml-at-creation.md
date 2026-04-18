---
id: TASK-144
title: set restrictive permissions on config.toml at creation
status: To Do
assignee: []
created_date: '2026-04-18 18:02'
labels:
  - security
  - chore
  - 'estimate: single'
milestone: m-29
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-07)
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-07 from the v1.11.0 database security audit.

`config.toml` is created with world-readable permissions (default umask `644`) and contains the Last.fm `session_key` — a persistent OAuth-style token granting full scrobbling API access with no expiry. This is functionally equivalent to a credential and should not be world-readable.

**Fix:** set `chmod 600` on `config.toml` at creation in `Config.first_run_setup` and on load if the file already exists with too-permissive modes:

```python
path.parent.mkdir(parents=True, exist_ok=True)
path.touch(mode=0o600)
path.write_text(DEFAULT_CONFIG_CONTENT)
```

Also apply a corrective `chmod` in `Config.load()` for existing installs where the file was already created with loose permissions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 config.toml created with 600 permissions on fresh install
- [ ] #2 Existing config.toml with loose permissions corrected to 600 on next load
- [ ] #3 No change to config read/write behaviour
<!-- AC:END -->
