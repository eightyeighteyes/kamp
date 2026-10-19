#!/usr/bin/env bash
# scripts/make_dmg_assets.sh
#
# Renders the macOS DMG installer assets (KAMP-675) from their SVG sources:
#
#   kamp_ui/resources/dmg_background.svg   -> dmg_background.png + @2x.png
#   kamp_ui/resources/dmg_target_icon.svg  -> dmg_target_icon.icns
#
# Usage:
#   bash scripts/make_dmg_assets.sh
#
# Requires librsvg (brew install librsvg), the same dependency the app-icon
# pipeline already uses. CI installs it in the build-bundle job and runs this
# there; the outputs are gitignored, so run this locally before a local DMG
# build. iconutil and sips are built into macOS.
#
# Why the background needs both sizes: electron-builder fuses <name>.png and
# <name>@2x.png into one multi-resolution TIFF via `tiffutil -cathidpicheck`
# (dmg-builder/out/dmgUtil.js, transformBackgroundFileIfNeed). It then measures
# that TIFF with `sips`, which reports the 1x page — so the 1x dimensions set
# the mount window size and the 2x page is what a Retina display draws.

set -euo pipefail

BG_SRC="kamp_ui/resources/dmg_background.svg"
BG_1X="kamp_ui/resources/dmg_background.png"
BG_2X="kamp_ui/resources/dmg_background@2x.png"

ICON_SRC="kamp_ui/resources/dmg_target_icon.svg"
ICON_OUT="kamp_ui/resources/dmg_target_icon.icns"

# Must match the SVG's own canvas, which is in turn the DMG window size.
WIDTH=540
HEIGHT=300

if ! command -v rsvg-convert >/dev/null 2>&1; then
  echo "error: rsvg-convert not found. Install it with: brew install librsvg" >&2
  exit 1
fi

for f in "$BG_SRC" "$ICON_SRC"; do
  if [ ! -f "$f" ]; then
    echo "error: $f not found (run from the repository root)" >&2
    exit 1
  fi
done

rsvg-convert -w "$WIDTH"         -h "$HEIGHT"          "$BG_SRC" -o "$BG_1X"
rsvg-convert -w "$((WIDTH * 2))" -h "$((HEIGHT * 2))"  "$BG_SRC" -o "$BG_2X"
echo "→ $BG_1X ($(sips -g pixelWidth -g pixelHeight "$BG_1X" | tail -2 | tr -d ' \n'))"
echo "→ $BG_2X ($(sips -g pixelWidth -g pixelHeight "$BG_2X" | tail -2 | tr -d ' \n'))"

# The target icon. Rendered at 1024 and downsampled into a full iconset so the
# disc stays clean at the 16/32px slots Finder uses in list and column views.
ICON_PNG="$(mktemp -d)/target_1024.png"
ICONSET="$(mktemp -d)/DmgTarget.iconset"
mkdir -p "$ICONSET"
rsvg-convert -w 1024 -h 1024 "$ICON_SRC" -o "$ICON_PNG"
for spec in "16 16x16" "32 16x16@2x" "32 32x32" "64 32x32@2x" \
            "128 128x128" "256 128x128@2x" "256 256x256" "512 256x256@2x" \
            "512 512x512" "1024 512x512@2x"; do
  # shellcheck disable=SC2086
  set -- $spec
  sips -z "$1" "$1" "$ICON_PNG" --out "$ICONSET/icon_$2.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ICON_OUT"
rm -rf "$(dirname "$ICONSET")" "$(dirname "$ICON_PNG")"
echo "→ $ICON_OUT ($(du -h "$ICON_OUT" | cut -f1))"
