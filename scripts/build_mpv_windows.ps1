# scripts/build_mpv_windows.ps1
#
# Builds a minimal audio-only mpv from source on Windows using the MSYS2
# UCRT64 toolchain that is pre-installed on `windows-latest` GitHub runners.
# Produces kamp_ui\resources\mpv\mpv.exe plus the runtime DLLs it needs.
#
# Mirrors the macOS source-build in build-app.yml: pinned to mpv v0.41.0 with
# the same audio-only meson feature set. Windows uses WASAPI for audio output;
# all video, optical-disc, and X11/Wayland features are disabled. Lua stays
# ENABLED: kamp_fade.lua implements pause/resume/stop (KAMP-519).
#
# Output layout (sibling DLL pattern is the Windows analog of macOS mpv-libs/):
#   kamp_ui\resources\mpv\mpv.exe
#   kamp_ui\resources\mpv\<dll>.dll       # UCRT64-supplied transitive deps
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

# windows-latest runners ship MSYS2 at C:\msys64.
$msys2 = "C:\msys64"
if (-not (Test-Path $msys2)) {
    throw "MSYS2 not found at $msys2. windows-latest runners are expected to have MSYS2 preinstalled."
}
$msysBash = Join-Path $msys2 "usr\bin\bash.exe"

# The MSYS2 environment is ONE fact, and every prefix, path and package name
# below is derived from it. UCRT64 (GCC + the modern UCRT C runtime) replaced
# MINGW64 (GCC + legacy msvcrt.dll) in KAMP-751: MSYS2 stopped publishing
# mingw-w64-x86_64-luajit, and mpv must link Lua. The whole dependency chain
# has to move together -- msvcrt and UCRT keep separate heaps and FILE*
# layouts, so a mixed bundle crashes at runtime instead of failing the build.
$msysEnv = "UCRT64"
$prefix = $msysEnv.ToLowerInvariant()           # /ucrt64 and C:\msys64\ucrt64
$pkg = "mingw-w64-ucrt-x86_64"                  # package-name prefix for UCRT64
$prefixBinUnix = "/$prefix/bin/"
$prefixBinWin = Join-Path $msys2 "$prefix\bin"

# Inherit the Windows PATH so curl/git/etc. resolve, but prepend the
# environment's bin dir so its toolchain takes precedence inside bash.
$env:MSYS2_PATH_TYPE = "inherit"
$env:CHERE_INVOKING = "1"
$env:MSYSTEM = $msysEnv

# All MSYS2 commands run through a single bash -lc invocation per logical step
# so that pacman/PATH state propagates within the step. Shell-quoting note:
# double-quoted PowerShell strings expand $prefix etc. before bash sees them;
# a bash variable that must survive is escaped with a backtick (`$PATH).

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

# git is an MSYS package (no environment prefix); everything else is built
# against the chosen environment's C runtime. The toolchain group also
# supplies objdump, used by the C-runtime guard below.
$packages = @("git") + (
    @("toolchain", "meson", "ninja", "pkgconf", "ffmpeg", "libass", "libplacebo", "luajit") |
        ForEach-Object { "$pkg-$_" }
)
Write-Host "==> Installing $msysEnv toolchain + mpv build deps via pacman"
Invoke-Msys "set -euo pipefail; pacman -S --needed --noconfirm $($packages -join ' ')"

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
export PATH=/$prefix/bin:`$PATH
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
Invoke-Msys "export PATH=/$prefix/bin:`$PATH && cd $mpvSrcUnix && ninja -C build"

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
# and copy every transitive dep that lives under /ucrt64/bin into the same
# directory as mpv.exe. Windows resolves bare DLL names from the executable's
# directory first, so siblings need no further rewriting (unlike the macOS
# @executable_path/mpv-libs/ rewrite handled by dylibbundler).
#
# The walk is an ALLOWLIST: every resolved DLL must come from the environment's
# bin dir (bundled) or from C:\Windows (the OS provides it). Anything else --
# a DLL found via the inherited runner PATH (Strawberry Perl, Git's mingw64
# and similar carry their own zlib1/libgcc), an unresolved "not found"/???,
# or a stray /mingw64 runtime -- fails the build. Silently skipping such a DLL
# is how a bundle builds green and then dies with 0xc0000135 on a clean machine.
Write-Host "==> Walking dependency tree and copying $msysEnv runtime DLLs"
$lddRaw = & $msysBash -lc "export PATH=/$prefix/bin:`$PATH && ldd $mpvSrcUnix/build/mpv.exe"
if ($LASTEXITCODE -ne 0) {
    throw "ldd on built mpv.exe failed"
}

$copiedDlls = New-Object System.Collections.Generic.HashSet[string]
$rejected = New-Object System.Collections.Generic.List[string]
foreach ($line in $lddRaw) {
    $text = "$line".Trim()
    if (-not $text) { continue }
    if ($text -notmatch '^(\S+)\s+=>\s+(\S+)') {
        $rejected.Add($text)
        continue
    }
    $target = $matches[2]
    if ($target.StartsWith($prefixBinUnix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $rel = $target.Substring($prefixBinUnix.Length)
        $winPath = Join-Path $prefixBinWin $rel
        if (-not (Test-Path $winPath)) {
            $rejected.Add("$text (resolved but missing at $winPath)")
            continue
        }
        Copy-Item -Force $winPath (Join-Path $out $rel)
        [void]$copiedDlls.Add($rel)
    }
    elseif ($target -match '^/c/Windows/') {
        # OS-provided (ntdll, kernel32, ucrtbase, api-ms-win-crt-*); not bundled.
        continue
    }
    else {
        $rejected.Add($text)
    }
}

Write-Host "-> Copied $($copiedDlls.Count) $msysEnv DLLs alongside mpv.exe"
$copiedDlls | Sort-Object | ForEach-Object { Write-Host "    $_" }

if ($rejected.Count -gt 0) {
    $rejected | ForEach-Object { Write-Host "    REJECTED: $_" }
    throw "$($rejected.Count) mpv.exe dependencies resolved outside $prefixBinUnix and C:\Windows -- they would be missing from the bundle (see REJECTED lines above)."
}

# luajit (mingw-w64-ucrt-x86_64-luajit) ships as lua51.dll; mpv links it
# dynamically so the ldd walk above should have carried it. Fail loud if it
# didn't: without it the kamp_fade.lua script never loads and pause/stop/resume
# become silent no-ops (KAMP-519) -- a regression that is invisible until you
# press a transport button in the packaged app.
if (-not ($copiedDlls -contains "lua51.dll")) {
    throw "lua51.dll was not bundled -- luajit missing from mpv.exe import table. Lua scripting (kamp_fade.lua) would not load; pause/stop/resume would be no-ops (KAMP-519)."
}

# C-runtime guard: no staged binary may import msvcrt.dll directly. A mixed
# msvcrt/UCRT process corrupts heaps at runtime rather than failing to load,
# so neither the build nor the smoke test would notice. Only DIRECT imports
# (objdump -p) are meaningful -- Windows' own system DLLs legitimately import
# msvcrt.dll, and ldd's recursive walk would report those too.
# Positive control: mpv.exe must import the UCRT, so a change in objdump's
# output format cannot make this check pass vacuously.
Write-Host "==> Checking staged binaries for a mixed C runtime"
$objdump = Join-Path $prefixBinWin "objdump.exe"
if (-not (Test-Path $objdump)) {
    throw "objdump not found at $objdump (expected from the $pkg-toolchain group)"
}
$msvcrtUsers = New-Object System.Collections.Generic.List[string]
$staged = Get-ChildItem -Path $out -File | Where-Object { $_.Extension -in ".exe", ".com", ".dll" }
foreach ($bin in $staged) {
    $imports = & $objdump -p $bin.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "objdump -p failed on $($bin.Name)"
    }
    if ($imports -match 'DLL Name:\s*msvcrt\.dll') {
        $msvcrtUsers.Add($bin.Name)
    }
    if ($bin.Name -eq "mpv.exe" -and -not ($imports -match 'DLL Name:\s*(api-ms-win-crt-|ucrtbase)')) {
        throw "mpv.exe does not import the UCRT -- either the build is not $msysEnv or objdump output changed; the msvcrt guard cannot be trusted."
    }
}
if ($msvcrtUsers.Count -gt 0) {
    throw "Mixed C runtime: these staged binaries import msvcrt.dll directly: $($msvcrtUsers -join ', ')"
}
Write-Host "-> $($staged.Count) staged binaries checked; none import msvcrt.dll"

# Smoke test: a sibling-DLL layout problem would surface here as a
# 0xc0000135 ("DLL not found") exit code. This is the Windows analog of
# the macOS Homebrew-leak audit. Run mpv.com (console subsystem) so the
# version banner is captured into the CI log for diagnostics; mpv.exe (GUI
# subsystem) emits nothing. Both front-ends load the identical sibling-DLL
# set, so either validates the staged layout via its exit code.
#
# PATH is cut down to the Windows directories for the duration, so a DLL
# that only resolves via the runner's toolchains cannot mask a missing
# sibling -- the test sees what a clean machine sees.
#
# NOTE: do NOT try to assert Lua here by grepping --version. `mpv --version`
# only prints mpv/libplacebo/FFmpeg version banners -- it does not enumerate
# scripting backends (lua/javascript), so there is no "lua" line to match on
# any platform. The authoritative Lua-enabled assertion is the lua51.dll
# import-table check above: mpv links lua51.dll only when built -Dlua=enabled,
# and nothing else in the bundle pulls it in, so its presence is proof and a
# -Dlua regression fails that check (not the user).
Write-Host "==> Smoke-testing mpv.com --version from staged directory (clean PATH)"
$savedPath = $env:PATH
Push-Location $out
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $versionOut = & .\mpv.com --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "mpv.com --version exited with code $LASTEXITCODE -- check that all transitive DLLs were copied"
    }
    $versionOut | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" }
    Write-Host "-> mpv.com runs from the staged directory (all sibling DLLs resolved)"
}
finally {
    $env:PATH = $savedPath
    Pop-Location
}

Write-Host "==> mpv build complete: $out"
