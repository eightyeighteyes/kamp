---
id: TASK-17.5
title: KampContext.fetch() — proxied network capability
status: To Do
assignee: []
created_date: '2026-04-05 16:36'
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
Implement `KampContext.fetch(url, method, body)` as the sole network interface for backend extensions declaring `network.external`. The host makes the HTTP request on behalf of the extension; the extension never calls the network directly.

At call time, the host checks the requested URL against the extension's declared `network.domains` allowlist. Requests to unlisted domains are rejected. This prevents `network.external` from being used as an unconstrained exfiltration channel.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 KampContext.fetch(url, method, body) makes the HTTP request from the host process and returns the response to the extension
- [ ] #2 Requests to domains not in the extension's declared network.domains allowlist are rejected with a clear error
- [ ] #3 Extensions cannot make direct outbound network calls; all network activity goes through KampContext.fetch()
- [ ] #4 fetch() is only available to extensions that declared network.external in their manifest; calling it without the permission raises an error
<!-- AC:END -->
