---
id: TASK-21
title: iframe sandboxing and CSP for community extensions
status: In Progress
assignee:
  - Claude
created_date: '2026-03-29 03:12'
updated_date: '2026-04-07 12:36'
labels:
  - feature
  - architecture
  - 'estimate: side'
milestone: m-2
dependencies: []
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Render community (third-party) extensions in `<iframe sandbox="allow-scripts">` communicating via `postMessage`, with a strict Content Security Policy on the renderer window. First-party extensions use contextBridge directly; this sandboxing is only for untrusted community extensions.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Community extensions render in sandboxed iframes
- [x] #2 Extensions communicate with the host only via postMessage (no direct DOM or API access)
- [x] #3 Strict CSP is enforced on the renderer window
- [x] #4 First-party extensions continue to work via contextBridge unaffected
- [x] #5 CSP connect-src is restricted to the kamp server origin only; frontend extensions that need external network access must proxy requests through KampAPI, not fetch directly
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## Implementation Plan

**Estimate: Side**

### Approach
Phase 2 (community) extensions run inside `<iframe sandbox="allow-scripts">` iframes. No `allow-same-origin` — zero access to host DOM or storage. The iframe's srcdoc contains a strict CSP, a postMessage shim that emulates the subset of KampAPI extensions need, and the extension code imported via blob URL.

**Communication protocol:**
- Iframe → host: `{ type: 'kamp:register-panel', extensionId, manifest: { id, title, defaultSlot, compatibleSlots } }`
- Host → iframe: `{ type: 'kamp:panel-mount', panelId }` / `{ type: 'kamp:panel-unmount', panelId }`

When a panel tab is activated, the iframe is moved from a hidden holding div into the active container. Moving an iframe within the same document does NOT reload it, so extension state persists across tab switches.

**Iframe CSP (in srcdoc):**
`default-src 'none'; script-src 'unsafe-inline' blob:; connect-src http://127.0.0.1:8000; style-src 'unsafe-inline'`
connect-src is pinned to the exact kamp server origin (no wildcard port).

### Files
1. **New: `kamp_ui/src/renderer/src/components/SandboxedExtensionLoader.tsx`**
   - Renders all Phase 2 iframes in a hidden holding div
   - Each iframe: `sandbox="allow-scripts"`, `srcdoc` = CSP + postMessage shim + blob-imported code
   - `window.message` listener validates `event.source` against known iframes
   - On `kamp:register-panel`: calls `window.KampAPI.panels.register()` with render fn that moves iframe to container + sends `kamp:panel-mount`; cleanup moves it back

2. **Modified: `kamp_ui/src/renderer/src/App.tsx`**
   - Collect Phase 2 extensions into state instead of skipping with a log warning
   - Render `<SandboxedExtensionLoader extensions={phase2Exts} />`

Phase 1 code path, preload, extensions.ts, and index.html CSP are untouched.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementation complete. New file: SandboxedExtensionLoader.tsx. Modified: App.tsx (phase2Extensions state, SandboxedExtensionLoader render). Type-check and lint pass clean. Key decisions: iframe-move pattern (no reload on tab switch), safeJson escaping for code injection safety, </script> split with string concatenation to satisfy no-useless-escape.
<!-- SECTION:NOTES:END -->
