---
id: TASK-1
title: 'bug: kamp UI can''t find server after computer sleeps'
status: To Do
assignee: []
created_date: '2026-03-29 02:44'
updated_date: '2026-03-29 14:03'
labels:
  - bug
  - ui
  - 'estimate: single'
milestone: m-0
dependencies: []
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
to repro:
start `poetry run kamp server`
load UI: `kamp_ui\npm run dev`
confirm that UI and server are connected
close or sleep laptop
wake laptop

expected: 
UI and server are still conneted

actual:
UI says "kamp server is not running"

server logs show:
```
INFO:     connection closed
INFO:     connection closed
INFO:     127.0.0.1:51295 - "WebSocket /api/v1/ws" [accepted]
INFO:     connection open
INFO:     127.0.0.1:51299 - "WebSocket /api/v1/ws" [accepted]
INFO:     connection open
INFO:     127.0.0.1:51297 - "GET /api/v1/albums HTTP/1.1" 200 OK
INFO:     127.0.0.1:51298 - "GET /api/v1/artists HTTP/1.1" 200 OK
INFO:     127.0.0.1:51297 - "GET /api/v1/artists HTTP/1.1" 200 OK
INFO:     127.0.0.1:51300 - "GET /api/v1/albums HTTP/1.1" 200 OK
```

UI logs show:
```
2026-03-28 23:02:05.106 Electron[73970:14927871] representedObject is not a WeakPtrToElectronMenuModelAsNSObject
2026-03-28 23:02:05.106 Electron[73970:14927871] representedObject is not a WeakPtrToElectronMenuModelAsNSObject
2026-03-28 23:02:05.106 Electron[73970:14927871] representedObject is not a WeakPtrToElectronMenuModelAsNSObject
2026-03-28 23:02:05.106 Electron[73970:14927871] representedObject is not a WeakPtrToElectronMenuModelAsNSObject
2026-03-28 23:02:05.106 Electron[73970:14927871] representedObject is not a WeakPtrToElectronMenuModelAsNSObject
2026-03-28 23:02:05.106 Electron[73970:14927871] representedObject is not a WeakPtrToElectronMenuModelAsNSObject
2026-03-28 23:02:05.106 Electron[73970:14927871] representedObject is not a WeakPtrToElectronMenuModelAsNSObject
```
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 After sleep/wake, UI automatically reconnects to the server without user intervention
- [ ] #2 UI shows the existing 'reconnecting' state while attempting to reconnect (not 'server not running')
- [ ] #3 Reconnection uses exponential backoff to avoid hammering the server
- [ ] #4 If the server is genuinely offline, the 'server not running' error state is still shown after retries are exhausted
<!-- AC:END -->
