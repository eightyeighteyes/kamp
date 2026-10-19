#!/usr/bin/env bash
# scripts/brand_dmg_target.sh <dmg-path> [icns-path]
#
# Replaces a built DMG's /Applications symlink with a Finder alias carrying the
# platter icon, so the install target seats into the turntable drawn on the
# background instead of showing a blue folder (KAMP-675).
#
# Usage (run from the repository root, after electron-builder):
#   bash scripts/brand_dmg_target.sh kamp_ui/dist/kamp-1.2.3-arm64.dmg
#
# == Why this runs after the build rather than as config ==
# electron-builder's DmgContent exposes no per-item icon, and a symlink cannot
# carry one at all: macOS refuses com.apple.ResourceFork on a symlink (errno 1,
# on HFS+ as well as APFS), and Finder draws a symlink using its TARGET's icon
# anyway. An alias file can carry an icon and is still a working drop target,
# so the swap has to happen on the finished image. See kamp_ui/native/
# DmgTargetIcon.swift for the full set of routes that were measured.
#
# == Why this is safe with respect to signing ==
# The .app inside is what gets signed and notarized — notarizeIfProvided()
# targets the app bundle, and dmg.sign is unset so electron-builder never signs
# the image itself. Repacking the image does not touch the app's bytes. That is
# not taken on trust: the app's signature is verified before and after, and a
# regression fails the script.

set -euo pipefail

DMG="${1:?usage: brand_dmg_target.sh <dmg-path> [icns-path]}"
ICNS="${2:-kamp_ui/resources/dmg_target_icon.icns}"
HELPER_SRC="kamp_ui/native/DmgTargetIcon.swift"

for f in "$DMG" "$ICNS" "$HELPER_SRC"; do
  [ -f "$f" ] || { echo "error: $f not found" >&2; exit 1; }
done

WORK="$(mktemp -d)"
DEV=""
cleanup() {
  [ -n "$DEV" ] && { hdiutil detach -quiet "$DEV" >/dev/null 2>&1 \
    || hdiutil detach -force "$DEV" >/dev/null 2>&1 || true; }
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "→ branding install target in $(basename "$DMG")"
swiftc -O -o "$WORK/dmg-target-icon" "$HELPER_SRC"

# Record the app's signing state before we touch anything, so the comparison
# afterwards is against reality rather than an assumption about whether this
# build was signed at all.
probe_signature() {
  local vol="$1"
  codesign --verify --strict "$vol/Kamp.app" >/dev/null 2>&1 && echo "valid" || echo "unsigned-or-invalid"
}

# Writable only for the UDRW working copy: a UDZO image is a read-only format
# and hdiutil refuses `-readwrite` on one with a bare "Operation not permitted".
mount_image() {
  local img="$1" mode="${2:-ro}"
  local attach args=(attach -noverify -noautoopen)
  [ "$mode" = "rw" ] && args+=(-readwrite)
  attach=$(hdiutil "${args[@]}" "$img")
  DEV=$(echo "$attach" | grep -o '^/dev/disk[0-9]*' | head -1)
  VOL=$(echo "$attach" | sed -n 's/.*\(\/Volumes\/.*\)$/\1/p' | tail -1 | xargs)
  [ -n "$DEV" ] && [ -n "$VOL" ] || { echo "error: could not mount $img" >&2; exit 1; }
}

hdiutil convert -quiet "$DMG" -format UDRW -o "$WORK/rw.dmg"
mount_image "$WORK/rw.dmg" rw

SIG_BEFORE="$(probe_signature "$VOL")"
echo "  app signature before: $SIG_BEFORE"

[ -e "$VOL/Applications" ] || { echo "error: no Applications entry in the image" >&2; exit 1; }
# Same filename, so the icon position recorded in .DS_Store still applies.
rm "$VOL/Applications"
"$WORK/dmg-target-icon" /Applications "$VOL/Applications" "$ICNS" | sed 's/^/  /'

hdiutil detach -quiet "$DEV"; DEV=""
rm -f "$WORK/out.dmg"
hdiutil convert -quiet "$WORK/rw.dmg" -format UDZO -o "$WORK/out.dmg"

# Verify on the repacked image before overwriting the original, so a failure
# here leaves the known-good DMG in place.
mount_image "$WORK/out.dmg"
rc=0
if ! xattr "$VOL/Applications" 2>/dev/null | grep -q ResourceFork; then
  echo "error: custom icon missing after repack" >&2; rc=1
fi
if [ ! -d "$VOL/Kamp.app" ]; then
  echo "error: Kamp.app missing after repack" >&2; rc=1
fi
SIG_AFTER="$(probe_signature "$VOL")"
echo "  app signature after:  $SIG_AFTER"
if [ "$SIG_BEFORE" = "valid" ] && [ "$SIG_AFTER" != "valid" ]; then
  echo "error: repacking invalidated the app signature" >&2; rc=1
fi
hdiutil detach -quiet "$DEV"; DEV=""
[ "$rc" -eq 0 ] || exit "$rc"

mv "$WORK/out.dmg" "$DMG"
echo "→ done: $(basename "$DMG") ($(du -sh "$DMG" | cut -f1))"
