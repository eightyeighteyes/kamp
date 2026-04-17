#!/usr/bin/env bash
# Build (and optionally sign) the now-playing-helper universal binary.
#
# Usage (from the kamp_ui/ directory or from native/):
#   ./native/build-native.sh
#
# To sign for distribution (required before notarization):
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#     ./native/build-native.sh

set -euo pipefail
cd "$(dirname "$0")/.."    # always run from kamp_ui/

SRC="native/NowPlayingHelper.swift"
OUT="resources/now-playing-helper"
TMP=$(mktemp -d)

cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

PLIST="native/NowPlayingHelper.plist"

echo "[now-playing] Compiling arm64…"
swiftc -O -target arm64-apple-macosx11.0 -framework MediaPlayer \
    -Xlinker -sectcreate -Xlinker __TEXT \
    -Xlinker __info_plist -Xlinker "${PLIST}" \
    -o "${TMP}/np-arm64" "${SRC}"

echo "[now-playing] Compiling x86_64…"
swiftc -O -target x86_64-apple-macosx11.0 -framework MediaPlayer \
    -Xlinker -sectcreate -Xlinker __TEXT \
    -Xlinker __info_plist -Xlinker "${PLIST}" \
    -o "${TMP}/np-x86" "${SRC}"

echo "[now-playing] Creating universal binary…"
lipo -create "${TMP}/np-arm64" "${TMP}/np-x86" -output "${OUT}"
chmod +x "${OUT}"

echo "[now-playing] Binary: ${OUT} ($(du -sh "${OUT}" | cut -f1))"

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    echo "[now-playing] Signing with: ${CODESIGN_IDENTITY}"
    # Hardened runtime is required for notarization.
    # No special entitlements are needed: MPNowPlayingInfoCenter and
    # MPRemoteCommandCenter are unprivileged MediaPlayer APIs.
    codesign --sign "${CODESIGN_IDENTITY}" --options runtime "${OUT}"
    echo "[now-playing] Verifying signature…"
    codesign --verify --verbose "${OUT}"
    echo "[now-playing] Signed OK"
else
    echo "[now-playing] CODESIGN_IDENTITY not set — skipping signing (dev build only)"
fi

echo "[now-playing] Done"
