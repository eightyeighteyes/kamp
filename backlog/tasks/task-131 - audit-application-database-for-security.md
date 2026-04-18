---
id: TASK-131
title: audit application database for security
status: Done
assignee: []
created_date: '2026-04-15 02:47'
updated_date: '2026-04-18 17:54'
labels:
  - chore
  - security
  - 'estimate: side'
milestone: m-29
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
consult with the Security Engineer to provide a security report, including vulnerabilities, attack surfaces, and threat severity, of our application's database, along with recommendations for remediation.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Security audit completed by Security Engineer subagent. Full report saved as doc-1 (Database Security Audit — v1.11.0).

**9 findings across 4 severity levels:**

- **High (1):** FINDING-01 — Bandcamp session cookies exposed via unauthenticated HTTP endpoint and WebSocket broadcast
- **Medium (3):** FINDING-02 (no API auth, wildcard CORS), FINDING-03 (plaintext credential storage), FINDING-04 (DB file world-readable, 644 permissions)
- **Low (3):** FINDING-05 (no explicit path traversal check), FINDING-06 (noqa suppresses safety signal on dynamic SQL), FINDING-07 (Last.fm session key in plaintext config.toml)
- **Informational (2):** FINDING-08 (deleted credentials linger in WAL), FINDING-09 (proxy-fetch accepts arbitrary URLs)

**SQL injection posture is clean** — all queries parameterized, column names allowlist-filtered, FTS input sanitized. No injection vectors found.

**v1.11.0 hardening scope (all Single-effort):** umask fix for DB file permissions, remove cookies from WS broadcast, chmod 600 on config.toml, WAL checkpoint after clear_session, restrict CORS origins, validate proxy-fetch URL domain, replace noqa with explicit guard.

**Medium-term:** shared-secret API token and OS keychain integration for credentials.
<!-- SECTION:FINAL_SUMMARY:END -->
