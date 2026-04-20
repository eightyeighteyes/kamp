---
id: TASK-156
title: add permissions block to ci.yml and pin action SHAs
status: Done
assignee: []
created_date: '2026-04-19 02:58'
updated_date: '2026-04-19 03:17'
labels:
  - security
  - chore
  - 'estimate: side'
milestone: m-29
dependencies: []
references:
  - backlog/docs/doc-2 - GitHub Repository Security Checklist — kamp.md
priority: medium
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two related Actions hardening changes.

**1. Add `permissions: {}` to `ci.yml`**

`ci.yml` has no `permissions:` block — add it at the top level to default to read-only, then grant only what each job needs.

**2. Pin all action `uses:` to full commit SHAs**

All workflow files use floating tags (`@v6`, `@v4`, etc.). Get SHA for each:
```sh
gh api repos/actions/checkout/git/refs/tags/v6 --jq '.object.sha'
```

Pin format:
```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v6
```

Apply to all `uses:` lines across: `ci.yml`, `release.yml`, `build-app.yml`, `publish-kamp-groover.yml`.

Note: once `dependabot.yml` includes `github-actions` ecosystem, Dependabot keeps SHAs current automatically.
<!-- SECTION:DESCRIPTION:END -->
