---
id: TASK-182
title: Bundle mpv dylibs so mpv works on machines without Homebrew
status: Done
assignee: []
created_date: '2026-04-26'
updated_date: '2026-05-01 12:34'
labels:
  - build
  - packaging
milestone: m-32
dependencies: []
priority: medium
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The build copies mpv directly from Homebrew (`brew install mpv && cp $(brew --prefix)/bin/mpv ...`). Homebrew's mpv links against a chain of Homebrew-specific dylibs (`libavcodec`, `libavformat`, `libass`, `libfreetype`, `libharfbuzz`, etc.) that are absent on machines without Homebrew. On a clean machine, `dyld` cannot resolve these libs and mpv fails to launch.

mpv is only spawned on playback (not server startup), so this does not prevent the server from running, but it means audio playback silently breaks for users who don't have Homebrew installed.

## Approach

Use `dylibbundler` to walk mpv's full dependency tree, copy every non-system dylib into `kamp_ui/resources/mpv-libs/`, and rewrite install names to `@loader_path/mpv-libs/<lib>`. This makes the bundled mpv fully relocatable.

### Build step (replace current Fetch mpv step)

```yaml
- name: Fetch mpv binary (with bundled dylibs)
  run: |
    brew install dylibbundler mpv
    cp "$(brew --prefix)/bin/mpv" kamp_ui/resources/mpv
    chmod +x kamp_ui/resources/mpv
    dylibbundler \
      --bundle-deps \
      --fix-file kamp_ui/resources/mpv \
      --dest-dir kamp_ui/resources/mpv-libs \
      --install-path @loader_path/mpv-libs/ \
      --overwrite-dir
    echo "→ $(file kamp_ui/resources/mpv)"
    echo "→ dylibs bundled: $(ls kamp_ui/resources/mpv-libs/ | wc -l)"
```

### Signing step (add before electron-builder)

```yaml
echo "→ Signing mpv-libs..."
find kamp_ui/resources/mpv-libs -type f -name "*.dylib" \
  | xargs -P8 -I{} codesign \
      --force --sign "$IDENTITY" \
      --options runtime --timestamp \
      {}
```

### Restore execute permissions step

Add to the existing chmod block:
```yaml
find kamp_ui/resources/mpv-libs -name "*.dylib" -exec chmod +x {} \;
```

## Verification

After the first build, confirm:
```bash
otool -L kamp_ui/resources/mpv
```
Should show only `@loader_path/mpv-libs/...`, `/usr/lib/`, and `/System/` — nothing from `/opt/homebrew/` or `/usr/local/`.

Test on a clean VM (no Homebrew) that audio playback works end-to-end.
<!-- SECTION:DESCRIPTION:END -->
