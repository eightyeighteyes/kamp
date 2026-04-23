---
id: TASK-165
title: stabilize keychain access with retry/backoff for background daemon
status: Done
assignee: []
created_date: '2026-04-20 02:02'
updated_date: '2026-04-20 16:13'
labels:
  - security
  - reliability
  - 'estimate: side'
milestone: m-30
dependencies: []
priority: high
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
After TASK-150, Bandcamp and Last.fm sessions are stored in the macOS Keychain via `keyring`. The daemon reads sessions at startup and on each sync, but the macOS Keychain can be transiently unavailable for several reasons:

- **Locked keychain**: the Keychain auto-locks after sleep, screensaver, or inactivity. Items stored with `kSecAttrAccessibleWhenUnlocked` (the `keyring` library default) are inaccessible while the Mac is locked, even to the creating process. A background daemon may start before the first unlock after a reboot.
- **Access-control prompts**: if macOS shows a "Python wants to access your keychain" prompt and it times out or is dismissed, the read fails.
- **Transient Security framework errors**: occasional `KeyringError` subclasses not covered by the current `except keyring.errors.NoKeyringError` guard.

Currently only `NoKeyringError` is caught; any other `KeyringError` propagates and causes the session to appear missing, breaking Bandcamp sync and Last.fm scrobbling until the next restart.

**Approach:**

1. **Widen exception handling** — catch `keyring.errors.KeyringError` (base class) in `get_session`, `set_session`, and `clear_session`, logging a warning on unexpected errors rather than propagating.

2. **Retry with exponential backoff** — when `get_session` fails with a non-`NoKeyringError` `KeyringError`, retry up to N times (e.g. 3) with exponential backoff (e.g. 0.5s, 1s, 2s) before giving up and returning `None`. This handles the transient locked-keychain case at daemon startup.

3. **Accessibility attribute** — investigate whether the `keyring` library (or a direct `Security` framework call via `pyobjc`) can store items with `kSecAttrAccessibleAfterFirstUnlock` instead of `kSecAttrAccessibleWhenUnlocked`. This would allow the daemon to read sessions even when the screen is locked, without requiring user interaction.

4. **Log clearly** — if all retries fail, log a warning that names the keychain as the source of the failure, rather than silently returning `None`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Unexpected KeyringError in get_session/set_session/clear_session is caught and logged rather than propagated
- [ ] #2 get_session retries with exponential backoff on transient KeyringError before returning None
- [ ] #3 Session items are stored with kSecAttrAccessibleAfterFirstUnlock if achievable via keyring or pyobjc
- [ ] #4 Bandcamp sync and Last.fm scrobbling are stable across mac sleep/wake cycles
<!-- AC:END -->
