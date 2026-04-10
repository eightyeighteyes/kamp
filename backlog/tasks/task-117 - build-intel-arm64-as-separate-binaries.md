---
id: TASK-117
title: build intel & arm64 as separate binaries
status: To Do
assignee: []
created_date: '2026-04-10 13:39'
updated_date: '2026-04-10 14:16'
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
