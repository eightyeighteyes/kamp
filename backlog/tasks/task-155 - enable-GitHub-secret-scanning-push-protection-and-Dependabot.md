---
id: TASK-155
title: 'enable GitHub secret scanning, push protection, and Dependabot'
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
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three free GitHub security features that take under 10 minutes total.

**Secret scanning & push protection** (`Settings → Security → Code security`):
1. Enable "Secret scanning"
2. Enable "Push protection"

**Dependabot** (`Settings → Security → Code security`):
3. Verify "Dependabot alerts" is active
4. Enable "Dependabot security updates"
5. Create `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    labels: ["dependencies"]
  - package-ecosystem: "npm"
    directory: "/kamp_ui"
    schedule:
      interval: "weekly"
    labels: ["dependencies"]
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    labels: ["dependencies"]
```

Also add `*.p12` and `*.keychain-db` to `.gitignore`.
<!-- SECTION:DESCRIPTION:END -->
