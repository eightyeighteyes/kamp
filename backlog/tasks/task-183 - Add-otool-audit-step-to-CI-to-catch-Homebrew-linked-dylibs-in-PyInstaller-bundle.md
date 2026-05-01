---
id: TASK-183
title: >-
  Add otool audit step to CI to catch Homebrew-linked dylibs in PyInstaller
  bundle
status: To Do
assignee: []
created_date: '2026-04-26'
updated_date: '2026-05-01 12:51'
labels:
  - build
  - ci
  - 'estimate: single'
milestone: m-33
dependencies: []
priority: medium
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The PyInstaller `.so`/`.dylib` files bundled under `kamp_ui/resources/kamp/_internal/` come from PyPI wheels, which are generally self-contained. However, if any package is compiled from source on the CI runner (no matching wheel available), the compiled extension could link against whatever Homebrew libraries that runner has installed. These would be absent on a clean user machine and silently break the feature that depends on them.

There is currently no build-time check that catches this class of issue.

## Approach

Add a verification step to `.github/workflows/build-app.yml` immediately after the **Build PyInstaller bundle** step:

```yaml
- name: Audit bundle for Homebrew-linked dylibs
  run: |
    HOMEBREW_LEAKS=$(find kamp_ui/resources/kamp -type f \( -name "*.so" -o -name "*.dylib" \) \
      -exec otool -L {} \; 2>/dev/null \
      | grep -E '/opt/homebrew|/usr/local/Cellar' | wc -l)
    echo "→ Homebrew-linked deps found: $HOMEBREW_LEAKS"
    if [ "$HOMEBREW_LEAKS" -gt 0 ]; then
      find kamp_ui/resources/kamp -type f \( -name "*.so" -o -name "*.dylib" \) \
        -exec sh -c 'otool -L "$1" | grep -E "/opt/homebrew|/usr/local/Cellar" && echo "  ↑ in $1"' _ {} \;
      exit 1
    fi
```

This step:
- Scans every `.so` and `.dylib` in the PyInstaller bundle
- Fails the build with an actionable diff if any Homebrew path is found
- Prints the offending file and library path so the fix is immediately obvious

## Verification

CI should pass on a clean run. Manually break it by adding a `binaries` entry in `kamp.spec` pointing to a Homebrew-installed `.dylib` and confirm the step catches it.
<!-- SECTION:DESCRIPTION:END -->
