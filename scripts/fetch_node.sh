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
# Usage: bash scripts/fetch_node.sh [--version 20.18.3] [--arch arm64|x64]

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

TARBALL="node-v${NODE_VERSION}-darwin-${ARCH}.tar.gz"
URL="https://nodejs.org/dist/v${NODE_VERSION}/${TARBALL}"
STRIP_PREFIX="node-v${NODE_VERSION}-darwin-${ARCH}"
TMPDIR=$(mktemp -d)

echo "→ Downloading Node.js ${NODE_VERSION} (${ARCH}) from nodejs.org..."
curl -fsSL --progress-bar "$URL" -o "$TMPDIR/$TARBALL"

echo "→ Extracting node binary..."
tar xzf "$TMPDIR/$TARBALL" -C "$TMPDIR" \
  "${STRIP_PREFIX}/bin/node" \
  "${STRIP_PREFIX}/lib/node_modules/npm"

mkdir -p kamp_ui/resources
cp "$TMPDIR/${STRIP_PREFIX}/bin/node" kamp_ui/resources/node
chmod +x kamp_ui/resources/node

rm -rf kamp_ui/resources/npm
cp -r "$TMPDIR/${STRIP_PREFIX}/lib/node_modules/npm" kamp_ui/resources/npm

rm -rf "$TMPDIR"

echo "→ node: $(file kamp_ui/resources/node)"
echo "→ npm-cli: kamp_ui/resources/npm/bin/npm-cli.js"
echo "→ Node.js dylibs (should be system-only):"
otool -L kamp_ui/resources/node | grep -v "kamp_ui/"
