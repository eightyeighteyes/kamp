---
id: TASK-21
title: iframe sandboxing and CSP for community extensions
status: To Do
assignee: []
created_date: '2026-03-29 03:12'
updated_date: '2026-04-03 04:37'
labels:
  - feature
  - architecture
  - 'estimate: side'
milestone: m-2
dependencies: []
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
<!-- AC:END -->
