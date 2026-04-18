---
id: doc-1
title: Database Security Audit — v1.11.0
type: other
created_date: '2026-04-18 17:54'
---
# Security Audit Report: kamp Application Database

**Date**: 2026-04-18
**Scope**: SQLite database, schema, query layer (`library.py`), HTTP API (`server.py`), configuration handling (`config.py`)
**Deployment model**: Single-user, local-first desktop app; HTTP server binds to 127.0.0.1 only
**Schema version audited**: 10

---

## 1. Threat Model

### 1.1 Architecture Summary

kamp runs a Python/FastAPI daemon on `127.0.0.1` with an Electron renderer as its primary client. The database lives at `~/.local/share/kamp/library.db` (or the Windows equivalent). There is no cloud backend, no remote authentication, and no multi-user access. The daemon spawns subprocess workers for library scanning and Bandcamp sync.

### 1.2 Realistic Threat Actors

**In scope:**

- **Malicious local processes** — any other process running as the same user (or root) can reach `127.0.0.1:PORT` without authentication. This is the primary attack surface.
- **Compromised community extensions** — extensions run with the extension_id trust boundary enforced only by audit log filtering in Python code, not by OS-level isolation.
- **Malicious audio files** — files placed in the watched library directory are parsed by mutagen; a crafted file could attempt to exploit tag parsing.
- **Ambient filesystem access** — on macOS and Linux, any process running as the user can directly open the database file if file permissions are permissive.

**Out of scope:**

- Remote attackers over the network (the server does not bind to a routable interface)
- Cross-user attacks (single-user system)
- Physical access attacks
- Electron renderer XSS leading to renderer-process compromise (separate surface)

### 1.3 Trust Boundary Map

| Boundary | From | To | Current Controls |
|---|---|---|---|
| Loopback → API | Any local process | FastAPI on 127.0.0.1 | None — no authentication |
| API → Database | FastAPI handlers | SQLite | Parameterized queries, whitelisted column names |
| Extension → Library | Extension code | `apply_metadata_update` | `_WRITABLE_TRACK_FIELDS` allowlist enforced in Python |
| Filesystem → DB | Library scanner | SQLite | Mutagen tag parsing; mtime change detection |
| Electron → API | Electron main/renderer | FastAPI | CORS wildcard; no token |

---

## 2. Findings

### FINDING-01: Bandcamp Session Cookies Exposed Over Unauthenticated Local HTTP

**Severity: High**

`GET /api/v1/bandcamp/session-cookies` returns the full raw Bandcamp cookie list — including `cf_clearance` and authentication cookies — to any caller on the loopback interface with no authentication, token, or origin check. Additionally, the `bandcamp.proxy-fetch` WebSocket push broadcasts the full cookie list to any connected WebSocket client:

```python
# server.py
proxy_event = {
    "type": "bandcamp.proxy-fetch",
    ...
    "cookies": broadcast_cookies,  # full cookie list in every WS push
}
```

**Attack scenario**: A malicious process opens a WebSocket to the kamp daemon and harvests Bandcamp auth cookies, enabling it to act as the authenticated user against Bandcamp's API.

**Remediation**:
- Remove `cookies` from the WebSocket broadcast payload immediately. Electron can call `/session-cookies` directly when needed.
- Long term: add a shared-secret token generated at daemon startup (written to a `chmod 600` file) required as a header on sensitive endpoints.

---

### FINDING-02: No Authentication on HTTP API — Any Local Process Can Control Playback and Modify Library State

**Severity: Medium**

All endpoints — including `POST /api/v1/library/scan`, `POST /api/v1/tracks/favorite`, Bandcamp/Last.fm connect/disconnect — are unauthenticated. CORS is configured with `allow_origins=["*"]`, meaning any page open in any browser can make cross-origin requests to the daemon.

**Attack scenario**: A malicious website calls `fetch("http://127.0.0.1:PORT/api/v1/bandcamp/session-cookies")` and receives the full Bandcamp cookie payload (restricted to `http://` pages by browser mixed-content policy, but still a real risk).

**Remediation**:
```python
ALLOWED_ORIGINS = ["http://localhost", "http://127.0.0.1", "file://"]
# Add "http://localhost:5173" in dev mode only
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, ...)
```

---

### FINDING-03: Plaintext Session Credential Storage in SQLite

**Severity: Medium**

The `sessions` table stores Bandcamp cookies and the Last.fm session key as plaintext JSON blobs. With world-readable file permissions (Finding-04), these are exposed to any process on the system. Even with correct permissions, backup/sync tools (Time Machine, iCloud Drive, Dropbox) would capture plaintext credentials.

**Remediation**:
- Short term: `chmod 600` on database file (see Finding-04).
- Medium term: use macOS Keychain via `keyring` library; fall back to DB storage on platforms without a keyring.

---

### FINDING-04: Database File Permissions — World-Readable by Default

**Severity: Medium**

`sqlite3.connect()` creates the database file using the process umask (typically `022` → `644`). This makes the database, WAL file, and SHM file world-readable, exposing all track metadata, play history, favorites, file paths, and session credentials to any user on the system.

**Remediation** — apply restrictive umask before first connect:
```python
def _make_conn(self) -> sqlite3.Connection:
    old_umask = os.umask(0o077)  # produces 600 for files
    try:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
    finally:
        os.umask(old_umask)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

---

### FINDING-05: `file_path` User Input Accepted Without Explicit Path Traversal Validation

**Severity: Low**

Several endpoints accept a `file_path` string and pass it to `index.get_track_by_path(Path(file_path))`. The DB lookup currently acts as an implicit allowlist, but this is not explicit and could break if a future change uses the caller-supplied path directly (e.g. in the `album-art` endpoint).

**Remediation**: Add explicit path containment validation asserting the resolved path lies within the configured library directory, as defense-in-depth.

---

### FINDING-06: Dynamic SQL Built From Audit Log Data — `noqa` Suppresses Safety Signal

**Severity: Low**

`rollback_extension` and `apply_metadata_update` interpolate column names from audit log JSON into a `SET` clause, filtered through `_WRITABLE_TRACK_FIELDS`. The filter is correct, but the `# noqa: S608` comments suppress the linter warning without documenting the safety argument, making the pattern invisible to future readers.

**Remediation**: Replace silent filtering with an explicit invariant guard:
```python
unknown = set(safe) - _WRITABLE_TRACK_FIELDS
if unknown:
    raise ValueError(f"Unexpected column names in audit log rollback: {unknown}")
```

---

### FINDING-07: Last.fm Session Key Stored in Plaintext `config.toml`

**Severity: Low**

`config.toml` is created with world-readable permissions (same root cause as Finding-04) and contains the Last.fm `session_key`, which is functionally equivalent to a credential granting full scrobbling API access with no expiry.

**Remediation**: Set `chmod 600` on `config.toml` at creation. Complete the migration of Last.fm session key from TOML into the `sessions` DB table (and then to keyring per Finding-03).

---

### FINDING-08: Deleted Credentials Persist in WAL File Until Checkpoint

**Severity: Informational**

After `clear_session()` deletes a row, the plaintext credential data remains in the WAL file until the next checkpoint. A forensic copy of the WAL file taken immediately after disconnecting Bandcamp would still contain the cookies.

**Remediation**:
```python
def clear_session(self, service: str) -> None:
    self._conn.execute("DELETE FROM sessions WHERE service = ?", (service,))
    self._conn.commit()
    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
```

---

### FINDING-09: `proxy-fetch` Accepts Arbitrary URLs — Potential Cookie Leakage to Non-Bandcamp Hosts

**Severity: Informational**

`POST /api/v1/bandcamp/proxy-fetch` accepts any URL without validation and broadcasts it to Electron, which executes `net.fetch(url)` with Bandcamp session cookies attached. A compromised extension or local process could cause authenticated requests to be sent to non-Bandcamp hosts.

**Remediation**:
```python
ALLOWED_PROXY_HOSTS = frozenset({"bandcamp.com", "f4.bcbits.com", "t4.bcbits.com"})

def _validate_proxy_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not any(host == h or host.endswith(f".{h}") for h in ALLOWED_PROXY_HOSTS):
        raise HTTPException(status_code=422, detail=f"Proxy URL host not allowed: {host}")
    return url
```

---

## 3. Positive Findings

These practices are done well and should be preserved:

- **Parameterized queries throughout** — zero injection vectors found across all query paths.
- **Whitelisted sort columns** — `_SORT_CLAUSES` dict ensures dynamic ORDER BY comes from a controlled set, not user input.
- **FTS input sanitization** — `_make_fts_query` strips FTS5 special chars before the parameterized MATCH call.
- **`_WRITABLE_TRACK_FIELDS` allowlist** — extensions cannot write to internal columns (`play_count`, `last_played`, `file_path`). Allowlist is a `frozenset` (immutable at runtime).
- **Append-only audit log with DB-level triggers** — `_audit_log_no_delete` and `_audit_log_no_update` enforce the invariant at the database level, not just in Python.
- **No password storage** — Last.fm session key is an OAuth-style token obtained post-auth; Bandcamp password is never persisted.
- **Schema versioning with incremental migrations** — each migration step is idempotent and version-gated.
- **Per-thread connection model** — `threading.local` avoids cursor-state races without a global mutex.

---

## 4. Prioritized Remediation Roadmap

### v1.11.0 — Hardening Release (all Single-effort changes)

| Priority | Finding | Action |
|---|---|---|
| 1 | FINDING-04 | Restrictive umask in `_make_conn` before `sqlite3.connect()` |
| 2 | FINDING-01 | Remove `cookies` from WebSocket `proxy-fetch` broadcast payload |
| 3 | FINDING-07 | `chmod 600` on `config.toml` at creation in `first_run_setup` / `load` |
| 4 | FINDING-08 | `PRAGMA wal_checkpoint(TRUNCATE)` after `clear_session()` |
| 5 | FINDING-02 | Restrict CORS origins — remove wildcard `"*"` |
| 6 | FINDING-09 | Validate `proxy-fetch` URL against `bandcamp.com` domain allowlist |
| 7 | FINDING-06 | Replace `noqa: S608` with explicit `ValueError` guard on unexpected column names |

**Total estimate: Side**

### Medium Term (v1.12.0 or dedicated security sprint)

| Priority | Finding | Action | Effort |
|---|---|---|---|
| 1 | FINDING-01 | Shared-secret token for HTTP API; required on sensitive endpoints | LP |
| 2 | FINDING-03 | Migrate session credentials to OS keychain via `keyring`; DB as fallback | LP |
| 3 | FINDING-07 | Complete Last.fm session key migration from config.toml → sessions table | Side |
| 4 | FINDING-05 | Explicit path containment validation for `file_path` parameters | Side |

### Longer Term

- **Full API authentication**: daemon-startup token written to `~/.local/share/kamp/.token` (`chmod 600`), required as `X-Kamp-Token` header. This is the standard pattern used by VS Code, JupyterLab, and similar local-first tools and closes Findings 01 and 02 comprehensively.
- **Encrypted credential store**: once `keyring` is integrated, the `sessions` table stores only non-sensitive metadata; actual credentials live in the OS keychain, making database backups and sync-service copies safe.

---

## Summary

The most consequential issue is the combination of **no API authentication** and **Bandcamp session cookies accessible to any local process** (Findings 01 and 02). The SQL layer is clean — parameterized queries, column allowlists, and FTS sanitization are all correctly implemented. Security debt is concentrated in credential storage and API access control, both of which have well-established remediation patterns for local-first desktop apps. All v1.11.0 hardening items are single-file changes landable in one session without touching the API surface or requiring migration logic.
