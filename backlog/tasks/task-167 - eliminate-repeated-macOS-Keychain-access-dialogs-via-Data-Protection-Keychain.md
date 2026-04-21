---
id: TASK-167
title: eliminate repeated macOS Keychain access dialogs via Data Protection Keychain
status: Done
assignee: []
created_date: '2026-04-20 20:54'
updated_date: '2026-04-21 13:34'
labels: []
milestone: m-30
dependencies: []
priority: high
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Users see a Keychain access dialog on every Kamp launch even after clicking "Always Allow". Root cause: two compounding bugs — (1) the `keyring` library uses delete+recreate for updates, wiping the item's ACL on every Bandcamp login; (2) the Login Keychain uses binary-hash ACLs, so every new PyInstaller build gets a new hash and macOS prompts again.\n\nFix: replace keyring calls with a custom ctypes Security.framework wrapper (`kamp_core/macos_keychain.py`) that uses `kSecUseDataProtectionKeychain: True` (code-signing identity, not binary hash) and `SecItemUpdate` (not delete+recreate). Add transparent one-time migration from Login Keychain to Data Protection Keychain. Add `keychain-access-groups` entitlement to the app bundle. Fallback to `keyring` in dev (unsigned builds lack the entitlement).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Signed build launches without any Keychain dialog after first unlock
- [ ] #2 Existing credentials migrate transparently on first launch
- [ ] #3 Dev (unsigned) builds fall back to keyring with no regression
- [ ] #4 All tests pass
<!-- AC:END -->
