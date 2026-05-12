# Build the now-playing-helper.exe (Windows SMTC bridge).
#
# Windows analog of native/build-native.sh. Compiles the Rust crate at
# native/now-playing-helper-win/ and copies the release binary into
# resources/ so electron-builder packages it via extraResources.
#
# Usage (from the kamp_ui/ directory or from native/):
#   pwsh native/build-native.ps1
#
# Requires: a Rust toolchain on PATH with MSVC linker access (CI uses
# `dtolnay/rust-toolchain@stable` on windows-latest, which has VS Build
# Tools preinstalled).

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# Always run from kamp_ui/ so paths resolve regardless of invocation cwd.
$kampUi = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $kampUi

$crateDir = "native/now-playing-helper-win"
$exeName = "now-playing-helper.exe"
$outDir = "resources"

Write-Host "[now-playing] Building $crateDir (cargo release)..."
Push-Location $crateDir
try {
    cargo build --release
    if ($LASTEXITCODE -ne 0) {
        throw "cargo build failed (exit $LASTEXITCODE)"
    }
}
finally {
    Pop-Location
}

$built = Join-Path $crateDir "target/release/$exeName"
if (-not (Test-Path $built)) {
    throw "Expected build artifact not found: $built"
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$dest = Join-Path $outDir $exeName
Copy-Item -Force $built $dest

$size = "{0:N0}" -f (Get-Item $dest).Length
Write-Host "[now-playing] Binary: $dest ($size bytes)"

# No smoke test: SMTC's GetForWindow requires an interactive Windows session
# (the Shell host is per-user, not present in GitHub Actions Session 0), so
# any spawn-and-exit check would fail with HRESULT 0x80070057 (E_INVALIDARG)
# even on a well-formed binary. Bundle-layout verification downstream
# (.github/workflows/build-app.yml "Verify bundle layout" step) catches a
# missing or zero-byte artifact, which is the realistic CI failure mode.
Write-Host "[now-playing] Done"
