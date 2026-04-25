---
id: TASK-179
title: 'Fix: non-ASCII header value crashes Electron with ByteString TypeError'
status: To Do
assignee: []
created_date: '2026-04-25 14:32'
updated_date: '2026-04-25 14:37'
labels:
  - bug
  - electron
  - bandcamp
milestone: m-31
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Observed error

An alert window shows the following uncaught exception during sync (application continues to function):

```
Uncaught Exception:
TypeError: Cannot convert argument to a ByteString because the character at index 22
has a value of 21561 which is greater than 255.
at webidl.converters.ByteString (node:internal/deps/undici/undici:5154:17)
at Headers.set (node:internal/deps/undici/undici:11194:35)
at ClientRequest.<anonymous> (node:electron/js2c/browser_init:2:55096)
at ClientRequest.emit (node:events:508:28)
at SimpleURLLoaderWrapper.<anonymous> (node:electron/js2c/browser_init:2:134069)
at SimpleURLLoaderWrapper.emit (node:events:508:28)
```

Value 21561 (0x5439) is a CJK character, suggesting a non-ASCII artist name or album title is being set as an HTTP header value somewhere in Electron's main process networking code.

## Likely cause

A string containing non-Latin characters (artist name, album title, or status message) is being passed directly to `Headers.set()` in Electron's main process. HTTP headers must be Latin-1 (ByteString); values outside 0–255 are invalid. Candidate sites: the `bandcamp.sync-status` broadcast payload, the proxy-fetch request headers, or any other place where user-supplied metadata flows into an outgoing HTTP header.

## Fix direction

Identify where the non-ASCII string enters a header and either percent-encode it, strip non-ASCII characters, or move the value into the request body / a JSON payload instead.
<!-- SECTION:DESCRIPTION:END -->
