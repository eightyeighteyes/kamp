---
id: TASK-154
title: rotate and remove committed AcoustID key from source
status: Done
assignee: []
created_date: '2026-04-19 02:58'
updated_date: '2026-04-19 03:17'
labels:
  - security
  - chore
  - 'estimate: single'
milestone: m-29
dependencies: []
references:
  - backlog/docs/doc-2 - GitHub Repository Security Checklist — kamp.md
priority: high
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The XOR-obfuscated AcoustID key bytes in `kamp_daemon/acoustid.py` lines 24–25 decode to a real API key. XOR with a 4-byte salt is trivially reversible — any reader of the source has the key.

## Steps

1. Rotate the key at acoustid.org → My Applications (invalidate the current key)
2. Update `ACOUSTID_KEY` and `ACOUSTID_SALT` in GitHub Secrets with the new key/salt
3. Restore the source file to placeholders: `_KEY: bytes = b""` and `_SALT: bytes = b""`
4. Verify CI still substitutes the new key correctly at build time
5. Enable GitHub secret scanning + push protection to prevent recurrence

## Acceptance Criteria

- `kamp_daemon/acoustid.py` committed to `main` contains `_KEY: bytes = b""` (no real key material)
- The built `.app` still fingerprints tracks correctly (AcoustID lookup works)
<!-- SECTION:DESCRIPTION:END -->
