---
id: TASK-151
title: migrate Last.fm session key from config.toml to sessions table
status: To Do
assignee: []
created_date: '2026-04-18 18:03'
labels:
  - security
  - chore
  - 'estimate: side'
milestone: m-15
dependencies: []
references:
  - doc-1 - Database Security Audit — v1.11.0 (FINDING-07)
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FINDING-07 (completion) from the v1.11.0 database security audit.

The Last.fm `session_key` is currently stored in `config.toml` under `[lastfm]`. It is a persistent OAuth-style token with no expiry that grants full scrobbling API access. It belongs in the `sessions` table alongside the Bandcamp session (and ultimately in the OS keychain per the keyring migration task).

**Fix:**

During `Config.load()`, if `lastfm.session_key` is present in the TOML file:
1. Read the value
2. Write it to `index.set_session("lastfm", {"session_key": value, "username": lastfm_username})`
3. Remove `session_key` (and `username`) from the TOML file
4. Log a one-time info message: "Migrated Last.fm session key from config.toml → database"

After migration, `LastfmConfig` no longer has `session_key` or `username` fields — the Last.fm session is read via `index.get_session("lastfm")` the same way the Bandcamp session is.

This task depends on TASK-132 (config → DB migration) being complete first, or can be done independently if config.toml is still in use at the time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Last.fm session_key is no longer stored in config.toml after first load post-migration
- [ ] #2 Session key is written to the sessions table (and later the keychain, per the keyring task)
- [ ] #3 Last.fm scrobbling continues to work end-to-end after migration
- [ ] #4 LastfmConfig no longer exposes session_key as a config field
<!-- AC:END -->
