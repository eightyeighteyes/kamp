---
id: TASK-21
title: iframe sandboxing and CSP for community extensions
status: In Progress
assignee: []
created_date: '2026-03-29 03:12'
updated_date: '2026-04-07 12:30'
labels:
  - feature
  - architecture
  - 'estimate: side'
milestone: m-2
dependencies: []
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Render community (third-party) extensions in `<iframe sandbox="allow-scripts">` communicating via `postMessage`, with a strict Content Security Policy on the renderer window. First-party extensions use contextBridge directly; this sandboxing is only for untrusted community extensions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Community extensions render in sandboxed iframes
- [ ] #2 Extensions communicate with the host only via postMessage (no direct DOM or API access)
- [ ] #3 Strict CSP is enforced on the renderer window
- [ ] #4 First-party extensions continue to work via contextBridge unaffected
- [ ] #5 CSP connect-src is restricted to the kamp server origin only; frontend extensions that need external network access must proxy requests through KampAPI, not fetch directly
<!-- AC:END -->
