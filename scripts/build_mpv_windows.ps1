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

Write-Host "==> Installing mingw-w64 toolchain + mpv build deps via pacman"
Invoke-Msys @'
set -euo pipefail
pacman -Syu --noconfirm
pacman -S --needed --noconfirm \
    git \
    mingw-w64-x86_64-toolchain \
    mingw-w64-x86_64-meson \
    mingw-w64-x86_64-ninja \
    mingw-w64-x86_64-pkgconf \
    mingw-w64-x86_64-ffmpeg \
    mingw-w64-x86_64-libass \
    mingw-w64-x86_64-libplacebo
'@

Write-Host "==> Cloning mpv $MpvTag"
$mpvSrcUnix = "/tmp/mpv-src"
Invoke-Msys "rm -rf $mpvSrcUnix && git clone --depth=1 --branch $MpvTag https://github.com/mpv-player/mpv.git $mpvSrcUnix"

# Audio-only meson configuration. Windows-only differences from the macOS
# build: no coreaudio, no avfoundation; WASAPI is built-in (auto-enabled when
# building on win32 -- no flag needed). All video/scripting/optical-disc
# features are disabled the same way as on macOS, plus the Windows-specific
# video output and hwaccel backends (d3d11, direct3d/D3D9, gl/gl-win32/
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
    -Dlua=disabled \
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

# Copy the built executable. mpv's meson build emits 'mpv.exe' on Windows.
$mpvExeWin = Join-Path $msys2 "tmp\mpv-src\build\mpv.exe"
if (-not (Test-Path $mpvExeWin)) {
    throw "Expected build artifact $mpvExeWin not found after ninja"
}
Copy-Item -Force $mpvExeWin (Join-Path $out "mpv.exe")

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

# Smoke test: a sibling-DLL layout problem would surface here as a
# 0xc0000135 ("DLL not found") exit code. This is the Windows analog of
# the macOS Homebrew-leak audit.
Write-Host "==> Smoke-testing mpv.exe --version from staged directory"
Push-Location $out
try {
    & .\mpv.exe --version | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "mpv.exe --version exited with code $LASTEXITCODE -- check that all transitive DLLs were copied"
    }
}
finally {
    Pop-Location
}

Write-Host "==> mpv build complete: $out"
