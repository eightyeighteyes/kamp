# scripts/fetch_node.ps1
#
# Windows analog of scripts/fetch_node.sh. Downloads the official Node.js LTS
# binary + npm from nodejs.org and places them at kamp_ui\resources\node.exe
# and kamp_ui\resources\npm\.
#
# Used by the windows-latest job in .github/workflows/build-app.yml so the
# packaged app can install community extensions on a machine with no Node.js
# preinstalled.
#
# Usage: pwsh scripts/fetch_node.ps1 [-Version 20.18.3]

[CmdletBinding()]
param(
    [string]$Version = "20.18.3"
)

$ErrorActionPreference = "Stop"

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$resources = Join-Path $repo "kamp_ui\resources"
New-Item -ItemType Directory -Force -Path $resources | Out-Null

$arch = "x64"  # KAMP-278 ships x64 only; ARM64 deferred to a follow-up.
$archive = "node-v$Version-win-$arch.zip"
$stripPrefix = "node-v$Version-win-$arch"
$url = "https://nodejs.org/dist/v$Version/$archive"

$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("kamp-node-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmpRoot | Out-Null
try {
    $zipPath = Join-Path $tmpRoot $archive
    Write-Host "-> Downloading Node.js $Version ($arch) from nodejs.org..."
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing

    Write-Host "-> Extracting..."
    Expand-Archive -Path $zipPath -DestinationPath $tmpRoot -Force

    $extracted = Join-Path $tmpRoot $stripPrefix
    if (-not (Test-Path $extracted)) {
        throw "Expected extracted directory $extracted not found"
    }

    # node.exe lives at the archive root on Windows (no bin/ subdir as on macOS).
    $nodeSrc = Join-Path $extracted "node.exe"
    $nodeDest = Join-Path $resources "node.exe"
    Copy-Item -Force $nodeSrc $nodeDest

    # npm CLI is identical across platforms (pure JS). On Windows the archive
    # ships it under node_modules\npm rather than lib\node_modules\npm.
    $npmDest = Join-Path $resources "npm"
    if (Test-Path $npmDest) { Remove-Item -Recurse -Force $npmDest }
    $npmSrc = Join-Path $extracted "node_modules\npm"
    Copy-Item -Recurse -Force $npmSrc $npmDest
}
finally {
    Remove-Item -Recurse -Force $tmpRoot -ErrorAction SilentlyContinue
}

Write-Host "-> node.exe: $(Get-Item (Join-Path $resources 'node.exe') | Select-Object -ExpandProperty Length) bytes"
Write-Host "-> npm-cli: kamp_ui\resources\npm\bin\npm-cli.js"

# Sanity-run node to confirm the binary launches without missing DLLs.
$nodeRun = Join-Path $resources "node.exe"
& $nodeRun --version
if ($LASTEXITCODE -ne 0) {
    throw "node.exe failed to run from staged location"
}
