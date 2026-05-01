---
id: TASK-185
title: Build minimal mpv from source with audio-only feature set
status: Done
assignee: []
created_date: '2026-04-30'
updated_date: '2026-05-01 02:14'
labels:
  - build
  - packaging
milestone: m-32
dependencies: []
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The build currently copies Homebrew's maximal mpv binary, which links against every optional
plugin mpv supports: VapourSynth (video frame scripting), LuaJIT, JavaScript, libbluray,
libdvdnav, libcdio, and more. dylibbundler faithfully bundles all transitive dependencies,
pulling in ~50 dylibs we never exercise. This creates two problems:

1. **Unbundleable deps**: Optional plugins like VapourSynth embed their own full runtimes
   (e.g. a specific Python.framework version) that dylibbundler cannot flatten into a portable
   dylib set. Any Homebrew formula update can introduce a new unbundleable transitive dep.
2. **Unnecessary bundle size**: We're shipping ~50 dylibs for features a music player never uses.

kamp only needs mpv for audio playback. It passes `--no-video` on every invocation. The only
capabilities required are audio decoding (FFmpeg), audio output (CoreAudio, a system framework),
and network streaming (libavformat handles this via FFmpeg).

## Approach

Build mpv from source in CI with a minimal feature set rather than copying Homebrew's binary.

### Feature flags (disable everything kamp doesn't need)

```bash
meson setup build \
  -Dvideo-output-backends=none \
  -Dvapoursynth=disabled \
  -Djavascript=disabled \
  -Dlua=disabled \
  -Dlibbluray=disabled \
  -Ddvdnav=disabled \
  -Dcdda=disabled \
  -Ddrm=disabled \
  -Dwayland=disabled \
  -Dx11=disabled \
  -Dsdl2=disabled \
  -Dopenal=disabled \
  -Djack=disabled \
  -Dpulseaudio=disabled \
  -Dalsa=disabled \
  -Dbuildtype=release
```

Required deps (still needed):
- FFmpeg (libavcodec, libavformat, libavutil, libswresample, libswscale) — audio decoding
- CoreAudio — system framework, already present on every Mac
- libass — subtitle renderer; may be needed for some audio formats that embed cue text

### Build step replacement

Replace the current `brew install mpv && cp ...` step with a source build:

```yaml
- name: Build minimal mpv from source
  run: |
    brew install meson ninja ffmpeg libass
    git clone --depth=1 --branch v0.39.0 https://github.com/mpv-player/mpv.git /tmp/mpv-src
    cd /tmp/mpv-src
    meson setup build \
      -Dvapoursynth=disabled \
      -Djavascript=disabled \
      -Dlua=disabled \
      ... (full flag list)
    ninja -C build
    cp build/mpv kamp_ui/resources/mpv
    chmod +x kamp_ui/resources/mpv
    otool -L kamp_ui/resources/mpv
```

dylibbundler is still needed to bundle the FFmpeg + libass dylibs that aren't system libraries.
With a minimal build, the expected dylib count drops from ~50 to ~15–20.

## Verification

- `otool -L kamp_ui/resources/mpv` shows only `@executable_path/mpv-libs/...`, `/usr/lib/`, `/System/`
- dylib count in mpv-libs is substantially lower than the current ~47
- Audio playback works end-to-end in the packaged app on a clean machine (no Homebrew)
- No VapourSynth, LuaJIT, or other non-audio dylibs appear in mpv-libs/
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Built mpv v0.41.0 from source with audio-only meson flags. Replaced the Homebrew binary copy + VapourSynth post-hoc removal hack. Dylib count: 47 → 32. No VapourSynth, no LuaJIT. Both arm64 and x64 CI runners passed including full sign-and-package. Lessons captured in CLAUDE.md: libplacebo is a hard dep (no disable flag), sdl2 split into three options, pulse not pulseaudio, iterate meson flags locally not in CI.
<!-- SECTION:FINAL_SUMMARY:END -->
