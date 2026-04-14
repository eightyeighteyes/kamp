---
id: TASK-17.5
title: KampGround.fetch() — proxied network capability
status: Done
assignee: []
created_date: '2026-04-05 16:36'
updated_date: '2026-04-06 11:26'
labels:
  - feature
  - security
  - 'estimate: side'
milestone: m-2
dependencies: []
parent_task_id: TASK-17
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement `KampGround.fetch(url, method, body)` as the sole network interface for backend extensions declaring `network.external`. The host makes the HTTP request on behalf of the extension; the extension never calls the network directly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 KampGround.fetch(url, method, body) makes the HTTP request from the host process and returns the response to the extension
- [x] #2 fetch() enforces the network.domains allowlist declared in the extension manifest; requests to unlisted domains raise PermissionError
- [x] #3 Extensions cannot make direct outbound network calls; all network activity goes through KampGround.fetch()
<!-- AC:END -->
