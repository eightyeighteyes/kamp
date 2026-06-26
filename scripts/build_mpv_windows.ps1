# scripts/build_mpv_windows.ps1
#
# Builds a minimal audio-only mpv from source on Windows using the MSYS2
# mingw-w64 toolchain that is pre-installed on `windows-latest` GitHub runners.
# Produces kamp_ui\resources\mpv\mpv.exe plus the runtime DLLs it needs.
#
# Mirrors the macOS source-build in build-app.yml: pinned to mpv v0.41.0 with
# the same audio-only meson feature set. Windows uses WASAPI for audio output;
# all video, scripting, optical-disc, and X11/Wayland features are disabled.
#
# Output layout (sibling DLL pattern is the Windows analog of macOS mpv-libs/):
#   kamp_ui\resources\mpv\mpv.exe
#   kamp_ui\resources\mpv\<dll>.dll       # mingw-supplied transitive deps
#
# Usage: pwsh scripts/build_mpv_windows.ps1

[CmdletBinding()]
param(
    [string]$MpvTag = "v0.41.0"
)

$ErrorActionPreference = "Stop"

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$out = Join-Path $repo "kamp_ui\resources\mpv"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force -Path $out | Out-Null

# windows-latest runners ship MSYS2 at C:\msys64. Use the mingw64 bash so
# pacman, meson, and ninja resolve the mingw-w64 toolchain consistently.
$msys2 = "C:\msys64"
if (-not (Test-Path $msys2)) {
    throw "MSYS2 not found at $msys2. windows-latest runners are expected to have MSYS2 preinstalled."
}
$msysBash = Join-Path $msys2 "usr\bin\bash.exe"

# Inherit the Windows PATH so curl/git/etc. resolve, but prepend mingw64 so
# the mingw toolchain takes precedence inside the bash session.
$env:MSYS2_PATH_TYPE = "inherit"
$env:CHERE_INVOKING = "1"
$env:MSYSTEM = "MINGW64"

# All MSYS2 commands run through a single bash -lc invocation per logical step
# so that pacman/PATH state propagates within the step. Shell-quoting note:
# we use single-quoted PowerShell here-strings (@'...'@) so $vars are passed
# through to bash literally rather than expanded by PowerShell.

function Invoke-Msys($script) {
    & $msysBash -lc $script
    if ($LASTEXITCODE -ne 0) {
        throw "MSYS2 step failed (exit $LASTEXITCODE)"
    }
}

Write-Host "==> Updating MSYS2 package database"
# pacman -Syu upgrades pacman itself on the first run, which terminates the
# MSYS2 process (expected; exit is non-zero). A second run completes the
# system update with the refreshed pacman binary.
& $msysBash -lc "pacman -Syu --noconfirm"
Invoke-Msys "pacman -Syu --noconfirm"

Write-Host "==> Installing mingw-w64 toolchain + mpv build deps via pacman"
Invoke-Msys @'
set -euo pipefail
pacman -S --needed --noconfirm \
    git \
    mingw-w64-x86_64-toolchain \
    mingw-w64-x86_64-meson \
    mingw-w64-x86_64-ninja \
    mingw-w64-x86_64-pkgconf \
    mingw-w64-x86_64-ffmpeg \
    mingw-w64-x86_64-libass \
    mingw-w64-x86_64-libplacebo \
    mingw-w64-x86_64-luajit
'@

Write-Host "==> Cloning mpv $MpvTag"
$mpvSrcUnix = "/tmp/mpv-src"
Invoke-Msys "rm -rf $mpvSrcUnix && git clone --depth=1 --branch $MpvTag https://github.com/mpv-player/mpv.git $mpvSrcUnix"

# Audio-only meson configuration. Windows-only differences from the macOS
# build: no coreaudio, no avfoundation; WASAPI is built-in (auto-enabled when
# building on win32 -- no flag needed). All video/optical-disc features are
# disabled the same way as on macOS, plus the Windows-specific video output
# and hwaccel backends (d3d11, direct3d/D3D9, gl/gl-win32/
# gl-dxinterop, vaapi/vaapi-win32, vdpau, egl-angle, caca, d3d-hwaccel/
# d3d9-hwaccel) which would otherwise auto-enable and pull video/vaapi.c
# via video/out/d3d11/context.h -- triggering a DXGI_DEBUG_D3D11 redefinition
# clash with newer mingw-w64 d3d11sdklayers.h. All flag names verified
# against mpv v0.41.0's meson.options.
Write-Host "==> Configuring mpv with audio-only feature set"
Invoke-Msys @"
set -euo pipefail
export PATH=/mingw64/bin:`$PATH
cd $mpvSrcUnix
meson setup build \
    -Dbuildtype=release \
    -Dvapoursynth=disabled \
    -Djavascript=disabled \
    -Dlua=enabled \
    -Dlibbluray=disabled \
    -Ddvdnav=disabled \
    -Dcdda=disabled \
    -Ddrm=disabled \
    -Dwayland=disabled \
    -Dx11=disabled \
    -Dsdl2-audio=disabled \
    -Dsdl2-video=disabled \
    -Dsdl2-gamepad=disabled \
    -Dopenal=disabled \
    -Djack=disabled \
    -Dpulse=disabled \
    -Dalsa=disabled \
    -Dvulkan=disabled \
    -Dshaderc=disabled \
    -Dpipewire=disabled \
    -Dgl=disabled \
    -Dgl-win32=disabled \
    -Dgl-dxinterop=disabled \
    -Dd3d11=disabled \
    -Ddirect3d=disabled \
    -Dd3d-hwaccel=disabled \
    -Dd3d9-hwaccel=disabled \
    -Dvaapi=disabled \
    -Dvaapi-win32=disabled \
    -Dvdpau=disabled \
    -Degl-angle=disabled \
    -Degl-angle-lib=disabled \
    -Degl-angle-win32=disabled \
    -Dcaca=disabled
"@

Write-Host "==> Building mpv (ninja)"
Invoke-Msys "export PATH=/mingw64/bin:`$PATH && cd $mpvSrcUnix && ninja -C build"

# Copy the built executable. mpv's meson build emits TWO front-ends on Windows:
#   mpv.exe -- GUI subsystem (no console); what the daemon spawns headlessly.
#   mpv.com -- console subsystem; the only variant that writes --version etc.
#              to a stream the parent process can capture.
# We ship/spawn mpv.exe, but stage mpv.com too so the smoke test below can read
# the version banner -- mpv.exe (GUI subsystem) emits nothing to stdout/stderr,
# so `mpv.exe --version` always captures empty, which silently defeated the
# Lua-feature assertion even after a 2>&1 merge.
$mpvExeWin = Join-Path $msys2 "tmp\mpv-src\build\mpv.exe"
if (-not (Test-Path $mpvExeWin)) {
    throw "Expected build artifact $mpvExeWin not found after ninja"
}
Copy-Item -Force $mpvExeWin (Join-Path $out "mpv.exe")

$mpvComWin = Join-Path $msys2 "tmp\mpv-src\build\mpv.com"
if (-not (Test-Path $mpvComWin)) {
    throw "Expected build artifact $mpvComWin not found after ninja"
}
Copy-Item -Force $mpvComWin (Join-Path $out "mpv.com")

# Walk mpv.exe's import table via ldd (the MSYS2 analog of macOS otool -L)
# and copy every transitive dep that lives under /mingw64/bin into the same
# directory as mpv.exe. Windows resolves bare DLL names from the executable's
# directory first, so siblings need no further rewriting (unlike the macOS
# @executable_path/mpv-libs/ rewrite handled by dylibbundler).
Write-Host "==> Walking dependency tree and copying mingw runtime DLLs"
$lddRaw = & $msysBash -lc "ldd $mpvSrcUnix/build/mpv.exe"
if ($LASTEXITCODE -ne 0) {
    throw "ldd on built mpv.exe failed"
}

$copiedDlls = New-Object System.Collections.Generic.HashSet[string]
foreach ($line in $lddRaw) {
    if ($line -match '=>\s+(/mingw64/bin/[^\s]+\.dll)') {
        $unixPath = $matches[1]
        $rel = $unixPath.Substring("/mingw64/bin/".Length)
        $winPath = Join-Path $msys2 "mingw64\bin\$rel"
        if (Test-Path $winPath) {
            Copy-Item -Force $winPath (Join-Path $out $rel)
            [void]$copiedDlls.Add($rel)
        }
    }
}

Write-Host "-> Copied $($copiedDlls.Count) mingw DLLs alongside mpv.exe"
$copiedDlls | Sort-Object | ForEach-Object { Write-Host "    $_" }

# luajit (mingw-w64-x86_64-luajit) ships as lua51.dll; mpv links it dynamically
# so the generic ldd walk above should have carried it. Fail loud if it didn't:
# without it the kamp_fade.lua script never loads and pause/stop/resume become
# silent no-ops (KAMP-519) -- a regression that is invisible until you press a
# transport button in the packaged app.
if (-not ($copiedDlls -contains "lua51.dll")) {
    throw "lua51.dll was not bundled -- luajit missing from mpv.exe import table. Lua scripting (kamp_fade.lua) would not load; pause/stop/resume would be no-ops (KAMP-519)."
}

# Smoke test: a sibling-DLL layout problem would surface here as a
# 0xc0000135 ("DLL not found") exit code. This is the Windows analog of
# the macOS Homebrew-leak audit. Also assert luajit is among the compiled-in
# features so a future -Dlua regression fails the build, not the user.
#
# Run mpv.com (console subsystem), NOT mpv.exe: only mpv.com writes the version
# banner to a stream PowerShell can capture. Both front-ends load the identical
# sibling-DLL set, so mpv.com validates the staged layout just as well, while
# also making the Lua-feature grep meaningful (mpv.exe emits nothing).
Write-Host "==> Smoke-testing mpv.com --version from staged directory"
Push-Location $out
try {
    $versionOut = & .\mpv.com --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "mpv.com --version exited with code $LASTEXITCODE -- check that all transitive DLLs were copied"
    }
    $versionOut | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
    # LuaJIT ships as lua51.dll on Windows (Lua 5.1 API-compatible); mpv may
    # report the feature as "luajit" or "lua" depending on pkg-config detection.
    # The lua51.dll presence check above is the authoritative assertion; here
    # we just confirm some Lua variant compiled in (guards against -Dlua=disabled).
    if (-not ($versionOut | Select-String -Pattern "lua" -Quiet)) {
        throw "Built mpv does not list any Lua in its enabled features -- Lua scripting is off (KAMP-519). Check -Dlua=enabled and that mingw-w64-x86_64-luajit was installed."
    }
    Write-Host "-> Verified Lua (luajit/lua51) is in mpv's enabled features"
}
finally {
    Pop-Location
}

Write-Host "==> mpv build complete: $out"
