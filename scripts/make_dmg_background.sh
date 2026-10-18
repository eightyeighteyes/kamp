#!/usr/bin/env bash
# scripts/make_dmg_background.sh
#
# Renders the macOS DMG installer background (KAMP-675) from its SVG source to
# the 1x/2x PNG pair electron-builder expects.
#
# Usage:
#   bash scripts/make_dmg_background.sh
#
# Requires librsvg (brew install librsvg), the same dependency the icon pipeline
# already uses. CI installs it in the build-bundle job and runs this there; the
# outputs are gitignored, so run this locally before any local DMG build.
#
# Why both sizes: electron-builder fuses <name>.png and <name>@2x.png into one
# multi-resolution TIFF via `tiffutil -cathidpicheck` (dmg-builder/out/dmgUtil.js,
# transformBackgroundFileIfNeed). It then measures that TIFF with `sips`, which
# reports the 1x page — so the 1x dimensions set the mount window size and the 2x
# page is what a Retina display actually draws.

set -euo pipefail

SRC="kamp_ui/resources/dmg_background.svg"
OUT_1X="kamp_ui/resources/dmg_background.png"
OUT_2X="kamp_ui/resources/dmg_background@2x.png"

# Must match the SVG's own canvas, which is in turn the DMG window size.
WIDTH=540
HEIGHT=320

if ! command -v rsvg-convert >/dev/null 2>&1; then
  echo "error: rsvg-convert not found. Install it with: brew install librsvg" >&2
  exit 1
fi

if [ ! -f "$SRC" ]; then
  echo "error: $SRC not found (run from the repository root)" >&2
  exit 1
fi

rsvg-convert -w "$WIDTH"             -h "$HEIGHT"             "$SRC" -o "$OUT_1X"
rsvg-convert -w "$((WIDTH * 2))"     -h "$((HEIGHT * 2))"     "$SRC" -o "$OUT_2X"

echo "→ $OUT_1X ($(sips -g pixelWidth -g pixelHeight "$OUT_1X" | tail -2 | tr -d ' \n'))"
echo "→ $OUT_2X ($(sips -g pixelWidth -g pixelHeight "$OUT_2X" | tail -2 | tr -d ' \n'))"
