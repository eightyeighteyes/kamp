---
id: TASK-184
title: Enable optional file logging from the application
status: To Do
assignee: []
created_date: '2026-04-30'
updated_date: '2026-04-30 21:06'
labels:
  - dx
  - diagnostics
milestone: m-32
dependencies: []
priority: low
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The kamp daemon logs to stderr only. Electron pipes this output and echoes it via
`console.log`/`console.error` in the main process. When the app is launched from
Finder (or any non-terminal context), that output has nowhere visible to go, making
startup crashes impossible to diagnose without `log stream` or Console.app.

Add an opt-in flag (e.g. `--log-file`) to the daemon that tees log output to
`~/Library/Logs/kamp/main.log` in addition to stderr. Electron should pass this
flag when spawning the daemon in a packaged app so that a log file is always
present after a Finder launch.

## Approach

### Daemon side

Add a `--log-file <path>` argument to the daemon CLI. When set, attach a
`logging.FileHandler` alongside the existing stderr handler in `logging.basicConfig`
(or after it via `logging.getLogger().addHandler`). Create the parent directory if
it does not exist.

Default path (used when Electron spawns the daemon):
```
~/Library/Logs/kamp/main.log
```

### Electron side

In `startServer()` (`kamp_ui/src/main/index.ts`), pass the flag when spawning:

```typescript
const logFile = join(app.getPath('logs'), 'main.log')
// ...
serverProcess = spawn(binary, ['daemon', '--log-file', logFile], { ... })
```

`app.getPath('logs')` on macOS returns `~/Library/Logs/<appName>/`, so no
hardcoded paths needed.

## Verification

- Launch Kamp from Finder and confirm `~/Library/Logs/Kamp/main.log` exists and
  contains daemon startup output.
- Confirm that a daemon crash (e.g. bad config) is captured in the log file and
  readable without `log stream` or Console.app.
<!-- SECTION:DESCRIPTION:END -->
