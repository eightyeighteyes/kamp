---
id: TASK-157
title: harden branch protection rules on main
status: Done
assignee: []
created_date: '2026-04-19 02:58'
updated_date: '2026-04-19 19:31'
labels:
  - security
  - chore
  - 'estimate: single'
milestone: m-29
dependencies: []
references:
  - backlog/docs/doc-2 - GitHub Repository Security Checklist — kamp.md
priority: medium
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Configure branch protection on `main` via `Settings → Branches → Add rule → Branch name pattern: main`.

| Setting | Value |
|---------|-------|
| Require a pull request before merging | On, required approvals: 0 |
| Require status checks to pass | On; jobs: `python`, `ui`, `sandbox-macos` |
| Restrict who can force push | On, no exceptions |
| Require linear history | On |

Hold off on "Require signed commits" until GPG signing is set up locally.
<!-- SECTION:DESCRIPTION:END -->
