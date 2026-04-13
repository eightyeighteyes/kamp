---
id: TASK-127
title: >-
  Route Bandcamp HTTP requests through Electron's net module to bypass
  Cloudflare TLS fingerprinting
status: To Do
assignee: []
created_date: '2026-04-13 11:17'
labels:
  - bandcamp
  - electron
  - networking
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Problem

The PyInstaller-bundled Python ships its own OpenSSL, which has a different TLS fingerprint (JA3/JA4) than a real browser. Cloudflare detects this and serves JS challenge pages (HTTP 200, ~3 KB HTML) instead of the expected JSON or profile HTML — even for authenticated API endpoints like `api/fan/2/collection_summary`. This blocks all Bandcamp sync in the built app.

`requests` in the dev environment uses the system Python's OpenSSL (or macOS SecureTransport), which has a fingerprint Cloudflare doesn't flag — hence sync works in dev but not in the built app.

## Solution

Route all `bandcamp.com` HTTP requests through Electron's `net` module (Chromium's network stack), which has a real browser TLS fingerprint and already holds the `cf_clearance` cookie in `session.defaultSession`.

### Design

**Daemon → server:** POST to a new `GET /api/v1/bandcamp/proxy-fetch` endpoint with `{"url": "...", "method": "GET|POST", "body": "..."}`.

**Server → Electron:** The server needs to forward this to Electron main. The existing pattern is one-directional push via WebSocket. Options:
1. Add a pending-request queue in `app.state`; Electron polls via a new `GET /api/v1/bandcamp/pending-fetch` endpoint, picks up the request, makes the `net.fetch` call, and POSTs the result back to `POST /api/v1/bandcamp/fetch-result`.
2. Use a future or asyncio Event in the server to block the proxy-fetch endpoint until Electron delivers the result (cleaner, but requires care around timeouts).

**Electron main:** Add an `ipcMain.handle` or a polling loop that calls the pending-fetch endpoint, executes `net.fetch(url, { session: session.defaultSession })`, and posts results back.

### Affected call sites in bandcamp.py
- `_get_fan_id` — GET `api/fan/2/collection_summary`
- `_get_download_links` — GET `bandcamp.com/{username}/`
- `_get_cdn_url` — GET individual download page
- `_download_file` — GET CDN URL (may not need proxying — CDN is not on bandcamp.com)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Bandcamp sync completes successfully in the built .app (not just dev)
- [ ] #2 fan_id fetch, download link scrape, and CDN URL fetch all go through Electron net
- [ ] #3 CDN download (popplers5.bandcamp.com) confirmed to work directly or proxied as needed
- [ ] #4 No regression in dev environment (daemon running outside .app falls back gracefully or uses same path)
<!-- AC:END -->
