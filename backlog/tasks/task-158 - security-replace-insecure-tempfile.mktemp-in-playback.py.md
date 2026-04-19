---
id: TASK-158
title: 'security: replace insecure tempfile.mktemp in playback.py'
status: In Progress
assignee: []
created_date: '2026-04-19 13:48'
updated_date: '2026-04-19 19:36'
labels:
  - security
  - codeql
milestone: m-29
dependencies: []
references:
  - kamp_core/playback.py#L346
  - 'https://github.com/teddyterry/kamp/security/code-scanning/1'
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CodeQL alert #1 (error — `py/insecure-temporary-file`): `kamp_core/playback.py:346` calls the deprecated `tempfile.mktemp()` to obtain a socket path for mpv's IPC server. This is a TOCTOU race — another process could claim the path between the name being generated and mpv opening the socket.

**Fix:** Replace with `tempfile.mkdtemp(prefix="kamp-mpv-")` and place the socket inside the returned directory (e.g. `<tmpdir>/mpv.sock`). Record the directory path so `_stop_mpv` can clean it up with `shutil.rmtree`.

**Context:** The socket path is passed directly to mpv via `--input-ipc-server`. A temp directory gives us an exclusive, OS-managed container for the socket file.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 tempfile.mktemp is no longer called anywhere in the codebase
- [ ] #2 mpv IPC socket lives inside a temp directory created with mkdtemp
- [ ] #3 the temp directory is cleaned up when mpv stops
- [ ] #4 CodeQL alert #1 is resolved
<!-- AC:END -->
