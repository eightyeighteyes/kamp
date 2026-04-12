---
id: TASK-117
title: build intel & arm64 as separate binaries
status: Done
assignee: []
created_date: '2026-04-10 13:39'
updated_date: '2026-04-12 22:03'
labels:
  - chore
  - ci
  - 'estimate: lp'
milestone: m-9
dependencies: []
priority: medium
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 CI produces a separate arm64 DMG and a separate x64 DMG on each release
- [ ] #2 Each DMG contains a native binary for its target architecture (no Rosetta required)
- [ ] #3 Both DMGs are uploaded to the GitHub release
- [ ] #4 PyInstaller bundle is built natively for each architecture (no cross-compilation)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Split `build-app.yml` into two parallel matrix jobs: `arch: [arm64, x64]`.\n\n- `arm64` job: `runs-on: macos-latest` (Apple Silicon runner, current setup)\n- `x64` job: `runs-on: macos-13` (last Intel runner GitHub provides)\n\nEach job:\n1. Builds PyInstaller bundle natively on its runner\n2. Fetches mpv via Homebrew (arch-native bottle)\n3. Fetches Node via `fetch_node.sh --arch <arch>`\n4. Runs `electron-builder --arm64` or `--x64` respectively\n5. Signs and notarizes independently\n6. Uploads a DMG named `Kamp-<version>-arm64.dmg` / `Kamp-<version>-x64.dmg`\n\nThe `--universal` flag and `x64ArchFiles` workaround can both be removed. The `fetch_node.sh --arch universal` lipo step is also no longer needed.\n\n**Artifact naming:** update `dmg.artifactName` in `electron-builder.yml` to include `${arch}` so the two DMGs don't collide: `Kamp-${version}-${arch}.${ext}`.\n\n**Dependency:** requires a macOS 13 (Intel) runner to be available, which GitHub provides for free-tier accounts.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
## What was done

Split the monolithic build-app workflow into two jobs per arch (`build-bundle` → `sign-and-package`) to enable fast re-runs of the signing step without rebuilding PyInstaller. Then diagnosed and fixed a series of hardened runtime failures in the frozen binary revealed only at runtime in the signed .app:

### Fixes committed to `task-117-separate-arch-builds`

1. **`kamp.spec`** — wrong exclude path (`kamp_daemon.bandcamp` → `kamp_daemon.ext.builtin.bandcamp`)
2. **`kamp_daemon/daemon_core.py`** — unconditional top-level import of `KampBandcampSyncer` triggered lazy import of excluded `kamp_daemon.syncer`; fixed with try/except + no-op stub
3. **`_kamp_entry.py`** — added `multiprocessing.freeze_support()` before any imports so frozen binary re-invocations (resource_tracker) are intercepted correctly
4. **`kamp_daemon/ext/discovery.py`** — skip extension probe and pin when `sys.frozen`; frozen importer uses `open()` internally (triggers probe's restricted-symbol stub), and importlib.metadata only sees bundled packages anyway
5. **`.github/workflows/build-app.yml`** — embed `entitlements.mac.plist` when pre-signing `kamp`, `mpv`, and `node`:
   - `kamp`: needs `allow-unsigned-executable-memory` for PyObjC/libffi closures (rumps menu bar)
   - `mpv`: needs `disable-library-validation` to load unsigned Homebrew dylibs (libass, etc.)
   - `node`: needs `allow-jit` for V8 JIT executable memory allocation
6. **`.github/workflows/build-app.yml`** — cache `~/Library/Caches/electron-builder` to avoid transient `socket hang up` failures when downloading dmgbuild

### Root cause pattern
All runtime failures shared the same cause: pre-signing with `--options runtime` enables hardened runtime enforcement, but without `--entitlements` the binary has no entitlements — blocking libffi, dylib loading, and JIT. The entitlements were already correct in `entitlements.mac.plist`; they just weren't being passed to `codesign` for any of the three pre-signed binaries.
<!-- SECTION:FINAL_SUMMARY:END -->
