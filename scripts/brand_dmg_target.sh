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
# == Why a shadow file rather than a UDRW round trip ==
# A DMG is read-only, so the obvious way to edit one is to convert it to UDRW,
# mount that, and convert back. Measured in CI, that cost 17s on the arm64
# runner and 159s-then-failure on the Intel one, because it materialises an
# uncompressed copy of the whole image — several hundred megabytes — on a
# runner that does not have much room to spare.
#
# `-shadow` makes a read-only image writable by diverting writes to a sidecar
# holding only the changed blocks. For this edit that sidecar is about 5MB
# instead of ~700MB, and the whole operation drops to a few seconds. The big
# intermediate never exists.
#
# == Why this is safe with respect to signing ==
# The .app inside is what gets signed and notarized — notarizeIfProvided()
# targets the app bundle, and dmg.sign is unset so electron-builder never signs
# the image itself. Repacking does not touch the app's bytes. That is not taken
# on trust: the app's signature is verified before and after, and a regression
# fails the script.

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

# Never let hdiutil fail silently. `-quiet` plus `set -e` is how the first CI
# run of this script died with no diagnostics at all: the step logged the icon
# being written and then simply exited 1. Every hdiutil call goes through here
# so its output is reported on failure, along with free space.
run_hdiutil() {
  local desc="$1"; shift
  local out
  if ! out=$(hdiutil "$@" 2>&1); then
    echo "error: hdiutil $desc failed" >&2
    echo "  command: hdiutil $*" >&2
    echo "$out" | sed 's/^/  /' >&2
    echo "  free space:" >&2
    df -h "$WORK" / 2>&1 | sed 's/^/    /' >&2
    exit 1
  fi
  printf '%s' "$out"
}

# Detaching is the flaky step in CI: Spotlight and fseventsd can still hold the
# volume for a moment after the last write, which surfaces as "resource busy".
# Retry before escalating to -force rather than failing the build over it.
detach_image() {
  local dev="$1" i
  for i in 1 2 3 4 5; do
    if hdiutil detach -quiet "$dev" >/dev/null 2>&1; then
      DEV=""
      return 0
    fi
    [ "$i" -lt 5 ] && sleep 2
  done
  echo "  detach of $dev needed -force" >&2
  if hdiutil detach -force "$dev" >/dev/null 2>&1; then
    DEV=""
    return 0
  fi
  echo "error: could not detach $dev" >&2
  exit 1
}

mount_image() {
  local attach
  attach=$(run_hdiutil "attach" "$@")
  DEV=$(echo "$attach" | grep -o '^/dev/disk[0-9]*' | head -1)
  VOL=$(echo "$attach" | sed -n 's/.*\(\/Volumes\/.*\)$/\1/p' | tail -1 | xargs)
  if [ -z "$DEV" ] || [ -z "$VOL" ]; then
    echo "error: could not parse mount point" >&2
    echo "$attach" | sed 's/^/  /' >&2
    exit 1
  fi
}

probe_signature() {
  codesign --verify --strict "$1/Kamp.app" >/dev/null 2>&1 \
    && echo "valid" || echo "unsigned-or-invalid"
}

echo "→ branding install target in $(basename "$DMG")"
echo "  free space before: $(df -h "$WORK" | awk 'NR==2{print $4}')"
swiftc -O -o "$WORK/dmg-target-icon" "$HELPER_SRC"

SHADOW="$WORK/brand.shadow"
mount_image attach -noverify -noautoopen -readwrite -shadow "$SHADOW" "$DMG"

SIG_BEFORE="$(probe_signature "$VOL")"
echo "  app signature before: $SIG_BEFORE"

[ -e "$VOL/Applications" ] || { echo "error: no Applications entry in the image" >&2; exit 1; }
# Same filename, so the icon position recorded in .DS_Store still applies.
rm "$VOL/Applications"
"$WORK/dmg-target-icon" /Applications "$VOL/Applications" "$ICNS" | sed 's/^/  /'

detach_image "$DEV"
echo "  shadow: $(du -h "$SHADOW" | cut -f1) of changed blocks"

run_hdiutil "convert with shadow" convert "$DMG" -format UDZO \
  -shadow "$SHADOW" -o "$WORK/out.dmg" -quiet >/dev/null

# Verify the repacked image before overwriting the original, so a failure here
# leaves the known-good DMG in place.
mount_image attach -noverify -noautoopen "$WORK/out.dmg"
rc=0
if [ -L "$VOL/Applications" ]; then
  echo "error: install target is still a symlink — the swap did not take" >&2; rc=1
elif ! xattr "$VOL/Applications" 2>/dev/null | grep -q ResourceFork; then
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
detach_image "$DEV"
[ "$rc" -eq 0 ] || exit "$rc"

mv "$WORK/out.dmg" "$DMG"
echo "→ done: $(basename "$DMG") ($(du -sh "$DMG" | cut -f1))"
