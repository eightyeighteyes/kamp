#!/usr/bin/env bash
# scripts/make_icns.sh <source_png>
#
# Converts a high-resolution PNG (ideally 1024×1024) into a macOS .icns file
# at kamp_ui/build/icon.icns using macOS built-in tools (sips + iconutil).
# No Homebrew dependencies required.
#
# Usage:
#   bash scripts/make_icns.sh kamp_ui/resources/icon_1024.png

set -euo pipefail

SRC="${1:?Usage: make_icns.sh <source_png>}"
ICONSET="$(mktemp -d)/AppIcon.iconset"
OUT="kamp_ui/build/icon.icns"

mkdir -p "$ICONSET" kamp_ui/build

# Generate all required slots. sips downsamples from the source PNG.
sips -z 16   16   "$SRC" --out "$ICONSET/icon_16x16.png"    >/dev/null
sips -z 32   32   "$SRC" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32   32   "$SRC" --out "$ICONSET/icon_32x32.png"    >/dev/null
sips -z 64   64   "$SRC" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128  128  "$SRC" --out "$ICONSET/icon_128x128.png"  >/dev/null
sips -z 256  256  "$SRC" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256  256  "$SRC" --out "$ICONSET/icon_256x256.png"  >/dev/null
sips -z 512  512  "$SRC" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512  512  "$SRC" --out "$ICONSET/icon_512x512.png"  >/dev/null
sips -z 1024 1024 "$SRC" --out "$ICONSET/icon_512x512@2x.png" >/dev/null

iconutil -c icns "$ICONSET" -o "$OUT"
rm -rf "$(dirname "$ICONSET")"

echo "→ $OUT"
