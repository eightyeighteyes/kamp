---
id: TASK-17.5
title: KampContext.fetch() — proxied network capability
status: To Do
assignee: []
created_date: '2026-04-05 16:36'
updated_date: '2026-04-05 21:20'
labels:
  - feature
  - security
  - 'estimate: side'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 1500
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement `KampGround.fetch(url, method, body)` as the sole network interface for backend extensions declaring `network.external`. The host makes the HTTP request on behalf of the extension; the extension never calls the network directly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 KampGround.fetch(url, method, body) makes the HTTP request from the host process and returns the response to the extension
- [ ] #2 fetch() enforces the network.domains allowlist declared in the extension manifest; requests to unlisted domains raise PermissionError
- [ ] #3 Extensions cannot make direct outbound network calls; all network activity goes through KampGround.fetch()
<!-- AC:END -->
