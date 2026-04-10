#!/usr/bin/env bash
# scripts/fetch_node.sh
#
# Downloads the official Node.js LTS binary + npm from nodejs.org and places
# them in kamp_ui/resources/node and kamp_ui/resources/npm/.
#
# The official binaries only link against macOS system libraries (CoreFoundation,
# libSystem, libc++) — safe to bundle in Kamp.app without additional dylibs.
# Homebrew node links against Homebrew-specific dylibs and must NOT be used.
#
# Usage: bash scripts/fetch_node.sh [--version 20.18.3] [--arch arm64|x64|universal]
#
# --arch universal downloads both arm64 and x64 and lipo-merges the node binary
# so the bundled node runs on both Apple Silicon and Intel Macs.

set -euo pipefail

NODE_VERSION="20.18.3"

# Auto-detect architecture: uname -m returns arm64 on Apple Silicon, x86_64 on Intel.
MACHINE=$(uname -m)
case "$MACHINE" in
  arm64)  ARCH="arm64" ;;
  x86_64) ARCH="x64" ;;
  *)      echo "Unknown arch: $MACHINE" >&2; exit 1 ;;
esac

# Allow overrides via args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) NODE_VERSION="$2"; shift 2 ;;
    --arch)    ARCH="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

fetch_arch() {
  local arch="$1" tmpdir="$2"
  local tarball="node-v${NODE_VERSION}-darwin-${arch}.tar.gz"
  local strip_prefix="node-v${NODE_VERSION}-darwin-${arch}"

  echo "→ Downloading Node.js ${NODE_VERSION} (${arch}) from nodejs.org..."
  curl -fsSL --progress-bar "https://nodejs.org/dist/v${NODE_VERSION}/${tarball}" \
    -o "$tmpdir/$tarball"

  echo "→ Extracting (${arch})..."
  tar xzf "$tmpdir/$tarball" -C "$tmpdir" \
    "${strip_prefix}/bin/node" \
    "${strip_prefix}/lib/node_modules/npm"
}

mkdir -p kamp_ui/resources
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

if [[ "$ARCH" == "universal" ]]; then
  fetch_arch "arm64" "$TMPDIR"
  fetch_arch "x64" "$TMPDIR"

  echo "→ Creating universal node binary with lipo..."
  lipo -create \
    "$TMPDIR/node-v${NODE_VERSION}-darwin-arm64/bin/node" \
    "$TMPDIR/node-v${NODE_VERSION}-darwin-x64/bin/node" \
    -output kamp_ui/resources/node
  chmod +x kamp_ui/resources/node

  # npm is pure JS — both arch tarballs ship identical npm; use arm64's copy
  rm -rf kamp_ui/resources/npm
  cp -r "$TMPDIR/node-v${NODE_VERSION}-darwin-arm64/lib/node_modules/npm" \
    kamp_ui/resources/npm
else
  fetch_arch "$ARCH" "$TMPDIR"

  cp "$TMPDIR/node-v${NODE_VERSION}-darwin-${ARCH}/bin/node" kamp_ui/resources/node
  chmod +x kamp_ui/resources/node

  rm -rf kamp_ui/resources/npm
  cp -r "$TMPDIR/node-v${NODE_VERSION}-darwin-${ARCH}/lib/node_modules/npm" \
    kamp_ui/resources/npm
fi

echo "→ node: $(file kamp_ui/resources/node)"
echo "→ npm-cli: kamp_ui/resources/npm/bin/npm-cli.js"
echo "→ Node.js dylibs (should be system-only):"
otool -L kamp_ui/resources/node | grep -v "kamp_ui/"
