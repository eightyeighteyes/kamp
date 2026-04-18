---
id: TASK-147
title: validate proxy-fetch URL against bandcamp.com domain allowlist
status: To Do
assignee: []
created_date: '2026-04-18 18:02'
labels:
  - security
  - chore
  - 'estimate: single'
milestone: m-29
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-09)
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-09 from the v1.11.0 database security audit.

`POST /api/v1/bandcamp/proxy-fetch` accepts an arbitrary URL string with no validation and broadcasts it to the Electron main process, which executes `net.fetch(url)` with Bandcamp session cookies attached. A compromised extension or any local process making a POST could cause the Electron process to make authenticated requests to arbitrary non-Bandcamp hosts.

**Fix:** validate the URL hostname against an allowlist of known Bandcamp domains before broadcasting:

```python
from urllib.parse import urlparse

ALLOWED_PROXY_HOSTS = frozenset({"bandcamp.com", "f4.bcbits.com", "t4.bcbits.com"})

def _validate_proxy_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not any(host == h or host.endswith(f".{h}") for h in ALLOWED_PROXY_HOSTS):
        raise HTTPException(status_code=422, detail=f"Proxy URL host not allowed: {host}")
    return url
```

Add additional Bandcamp CDN hostnames to `ALLOWED_PROXY_HOSTS` as needed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 proxy-fetch rejects URLs whose hostname is not in the bandcamp.com allowlist with HTTP 422
- [ ] #2 Legitimate Bandcamp API and CDN URLs (bandcamp.com, f4.bcbits.com, t4.bcbits.com) are accepted
- [ ] #3 Non-Bandcamp URLs return a clear error without making a network request
<!-- AC:END -->
