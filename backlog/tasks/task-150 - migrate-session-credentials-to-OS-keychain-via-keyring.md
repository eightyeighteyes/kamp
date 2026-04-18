---
id: TASK-150
title: migrate session credentials to OS keychain via keyring
status: To Do
assignee: []
created_date: '2026-04-18 18:02'
labels:
  - security
  - feature
  - 'estimate: lp'
milestone: m-15
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-03)
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-03 from the v1.11.0 database security audit.

Bandcamp cookies and the Last.fm session key are stored as plaintext JSON in the `sessions` table. Even with correct file permissions (600), backup/sync tools (Time Machine, iCloud Drive, Dropbox) capture plaintext credentials. The correct platform mechanism for credential storage is the OS keychain.

**Approach:**

Use the `keyring` library to store credentials in the macOS Keychain, Windows Credential Manager, or Linux Secret Service. Fall back to the existing `sessions` table on platforms where no keyring is available:

```python
import keyring
import keyring.errors

def set_session(self, service: str, data: dict[str, Any]) -> None:
    try:
        keyring.set_password("kamp", service, json.dumps(data))
    except keyring.errors.NoKeyringError:
        self._store_session_in_db(service, data)

def get_session(self, service: str) -> dict[str, Any] | None:
    try:
        raw = keyring.get_password("kamp", service)
        if raw is not None:
            return json.loads(raw)
    except keyring.errors.NoKeyringError:
        pass
    return self._get_session_from_db(service)
```

Once keyring is integrated, the `sessions` table retains only non-sensitive metadata (service name, `updated_at`) and the actual credential payload lives in the OS keychain — making database file backups and forensic copies safe.

One-time migration: on first load after upgrade, move any existing `sessions` rows into the keychain and clear the `session_json` column in the DB.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 keyring added as a dependency
- [ ] #2 set_session writes to OS keychain when available; falls back to DB otherwise
- [ ] #3 get_session reads from OS keychain first, then DB fallback
- [ ] #4 clear_session removes from both keychain and DB
- [ ] #5 One-time migration moves existing DB session data into the keychain on upgrade
- [ ] #6 After migration, session_json in DB is cleared (no plaintext credentials at rest)
- [ ] #7 Bandcamp and Last.fm auth flows work end-to-end on macOS, with graceful degradation on platforms without a keyring
<!-- AC:END -->
