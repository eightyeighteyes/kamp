---
id: TASK-168
title: eliminate keychain dialog after app updates via Data Protection Keychain
status: Done
assignee: []
created_date: '2026-04-20 21:30'
updated_date: '2026-04-24 00:14'
labels:
  - feature
  - os-integration
  - keychain
  - 'estimate: lp'
milestone: m-31
dependencies:
  - TASK-167
priority: medium
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Background

TASK-167 fixed "dialog on every launch" by replacing keyring's delete+recreate with SecItemUpdate. However the Login Keychain uses binary-hash ACLs — each new PyInstaller build changes the daemon binary hash, so macOS prompts again after every release.

The fix is `kSecUseDataProtectionKeychain: True` in `macos_keychain.py`, which uses the app's code-signing identity (stable across releases) instead of binary hashes. Apple's documentation says two paths enable this:

> "Your app needs the `keychain-access-groups` entitlement. Alternatively, if your app is sandboxed, you don't need this entitlement because each app has one access group automatically."
> — [developer.apple.com/documentation/security/ksecusedataprotectionkeychain](https://developer.apple.com/documentation/security/ksecusedataprotectionkeychain)

## Option A — Xcode Keychain Sharing capability (narrower scope)

Apple's docs say to enable `keychain-access-groups` by using **Xcode's Signing & Capabilities tab**, not the Developer Portal directly. Xcode registers the capability with Apple and creates/updates a Developer ID Application provisioning profile that includes it. The portal Identifiers page does not expose this capability manually — Xcode is the entry point.

**Steps:**
1. Open (or create a minimal stub) Xcode project targeting `com.kamp.app` with the Developer ID certificate
2. In **Signing & Capabilities**, add the **Keychain Sharing** capability and add group `com.kamp.app`
3. Let Xcode generate and download the Developer ID Application provisioning profile with this capability
4. Place the `.provisionprofile` in the repo (e.g. `kamp_ui/build/KampDeveloperID.provisionprofile`) — profiles are not secret but expire annually
5. In `electron-builder.yml` (or `package.json` build config), set `mac.provisioningProfile` to point at the file
6. In `entitlements.mac.plist`, add:
   ```xml
   <key>keychain-access-groups</key>
   <array>
       <string>$(AppIdentifierPrefix)com.kamp.app</string>
   </array>
   ```
   `$(AppIdentifierPrefix)` is expanded by `codesign` to `TEAMID.` automatically.
7. Re-add `kSecUseDataProtectionKeychain=True` to all three functions in `kamp_core/macos_keychain.py`
8. Re-add `KeyringEntitlementError` / `_mac_kc_entitlement_missing` fallback in `library.py` for unsigned dev builds (reference: `task-167-data-protection-keychain` git history before the entitlement was removed in the second commit)
9. Re-add the Login→DPC one-time migration in `get_session` (same reference)

**Risk:** electron-builder's `@electron/osx-sign` must pass `--entitlements` correctly so `codesign` expands `$(AppIdentifierPrefix)`. Verify the signed binary's embedded entitlements with `codesign -d --entitlements :- /path/to/Kamp.app` before shipping.

## Option B — App Sandbox (larger scope)

Sandboxed apps get an implicit keychain access group (`TEAMID.com.kamp.app`) and don't need `keychain-access-groups` or a provisioning profile. All MAS apps and many Developer ID apps use this. It is production-safe but is a significant architectural change:

- The PyInstaller `kamp` daemon is a child process — sandboxed parents can only spawn signed children; the daemon needs its own entitlements (`com.apple.security.inherit` or explicit entitlements)
- File access is restricted to `~/Library/Application Support/<bundle-id>/`, `TMPDIR`, and user-selected paths — any hardcoded paths outside these will break
- All current entitlements in `entitlements.mac.plist` would still be needed plus `com.apple.security.app-sandbox`

Recommend Option A first. If the provisioning profile path proves unworkable with electron-builder's signing setup, fall back to Option B.

## Code changes (same for both options, after setup)

- `kamp_core/macos_keychain.py`: re-add `kSecUseDataProtectionKeychain=True`, `KeyringEntitlementError`, `_ERR_SEC_MISSING_ENTITLEMENT`
- `kamp_core/library.py`: re-add `_mac_kc_entitlement_missing` flag, `_KeyringEntitlementError` sentinel, and Login→DPC migration block in `get_session`
- `tests/test_macos_keychain.py`: re-add `KeyringEntitlementError` and `_ERR_SEC_MISSING_ENTITLEMENT` tests
- `tests/test_library.py`: re-add `TestLoginKeychainMigration` tests
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Signed build shows no Keychain dialog on first launch after an app update (Data Protection Keychain confirmed via `codesign -d --entitlements`)
- [ ] #2 Existing users' credentials migrate transparently from Login Keychain to Data Protection Keychain on first launch
- [ ] #3 Dev (unsigned) build falls back gracefully to Login Keychain with SecItemUpdate (TASK-167 behavior)
- [ ] #4 All tests pass
<!-- AC:END -->
