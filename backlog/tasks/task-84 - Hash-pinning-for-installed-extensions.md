---
id: TASK-84
title: Hash-pinning for installed extensions
status: Done
assignee: []
created_date: '2026-04-05 16:27'
updated_date: '2026-04-06 11:51'
labels:
  - feature
  - security
  - 'estimate: side'
milestone: m-2
dependencies:
  - TASK-17
documentation:
  - project/kampground-ideation.md
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
At install time, kampground records a SHA-256 hash of each extension's installed files. On every subsequent load — including hot reloads triggered by the file watcher — the hash is verified before execution. A mismatch blocks the load and alerts the user.

This closes the post-install file-swap window: a malicious npm postinstall script or pip wheel hook that writes modified files to the extensions directory after install-time review will be caught on the next load.

Hash records are stored in a kampground-managed file (e.g. `~/.config/kamp/extension-pins.json`) outside the extensions directory itself so they cannot be tampered with by an extension.

Depends on: TASK-17 (extension host, which manages the load lifecycle).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 SHA-256 hashes of all installed extension files are recorded at install time
- [ ] #2 Hash is verified on every load, including hot reloads
- [ ] #3 A hash mismatch blocks the load and shows a clear error to the user identifying which extension failed verification
- [ ] #4 Hash records are stored outside the extensions directory and are not modifiable by extension code
- [ ] #5 Re-installing or explicitly updating an extension updates the stored hash
<!-- AC:END -->
