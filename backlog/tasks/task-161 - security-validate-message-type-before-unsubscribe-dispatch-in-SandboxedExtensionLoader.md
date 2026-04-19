---
id: TASK-161
title: >-
  security: validate message type before unsubscribe dispatch in
  SandboxedExtensionLoader
status: To Do
assignee: []
created_date: '2026-04-19 13:48'
labels:
  - security
  - codeql
milestone: m-29
dependencies: []
references:
  - kamp_ui/src/renderer/src/components/SandboxedExtensionLoader.tsx#L122
  - 'https://github.com/teddyterry/kamp/security/code-scanning/6'
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
CodeQL alert #6 (warning — `js/unvalidated-dynamic-method-call`): `kamp_ui/src/renderer/src/components/SandboxedExtensionLoader.tsx:122` calls `unsub()` where `unsub` is retrieved from `_activeSubscriptions` using a key derived from `msg.subId` — a value arriving from a sandboxed iframe.

```tsx
const unsub = _activeSubscriptions.get(key)
if (unsub) {
  unsub()  // ← flagged: method call with user-controlled key
```

The value stored in `_activeSubscriptions` is always an internal unsubscribe function (set by the renderer), not user-supplied, so this is likely a false positive. However, the `key` is derived from untrusted `msg.subId`, meaning a crafted message could invoke an unsubscribe for a subscription it doesn't own.

**Fix:** Verify that the message `source` matches the iframe that originally registered the subscription before calling `unsub()`. This ensures a sandboxed extension can only unsubscribe its own subscriptions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Unsubscribe only succeeds if the requesting iframe owns the subscription
- [ ] #2 Or: alert is dismissed with a documented rationale if the ownership model already prevents cross-extension unsubscription
- [ ] #3 CodeQL alert #6 is resolved
<!-- AC:END -->
