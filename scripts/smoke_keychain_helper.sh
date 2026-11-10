#!/usr/bin/env bash
# Prove a signed KampKeychainHelper.app can reach the Data Protection
# Keychain (KAMP-753). Usage: smoke_keychain_helper.sh <path/to/KampKeychainHelper.app>
#
# A helper whose restricted entitlement is not honoured looks fine to every
# other check, so this distinguishes three failure modes by how the helper exits:
#   killed by a signal (exit > 128) — AMFI refused the profile; the original defect
#   exit 3 on set                   — it ran, but without the entitlement
#                                     (e.g. something re-signed it with other entitlements)
#   a value that does not round-trip — anything else
# Only a full set → get → delete round-trip on a throwaway item passes. The
# item is deleted again on the way out, whatever happens.
set -uo pipefail

APP=${1:?usage: $0 <KampKeychainHelper.app>}
HELPER="$APP/Contents/MacOS/kamp-keychain-helper"
SERVICE=kamp-ci-probe
ACCOUNT="probe-$$"
VALUE="kamp-753-probe-$RANDOM-$$"

fail() { echo "::error::keychain helper smoke test: $*"; exit 1; }

describe() {  # <op> <exit code>
  local rc=$2
  if [ "$rc" -gt 128 ]; then
    fail "$1 killed by signal $((rc - 128)): the provisioning profile did not authorise the keychain entitlement (KAMP-753)"
  fi
}

cleanup() { "$HELPER" delete_dpc "$SERVICE" "$ACCOUNT" >/dev/null 2>&1 || true; }
trap cleanup EXIT

[ -x "$HELPER" ] || fail "no executable at $HELPER"

echo "→ entitlements on the helper:"
codesign -d --entitlements - "$APP" 2>/dev/null | tee /dev/stderr \
  | grep -q "keychain-access-groups" \
  || fail "the signed helper carries no keychain-access-groups (re-signed with other entitlements?)"

printf '%s\n' "$VALUE" | "$HELPER" set_dpc "$SERVICE" "$ACCOUNT"
rc=$?
describe set_dpc "$rc"
[ "$rc" -eq 0 ] || fail "set_dpc exited $rc (3 = errSecMissingEntitlement or another OSStatus; see stderr)"

got=$("$HELPER" get_dpc "$SERVICE" "$ACCOUNT")
rc=$?
describe get_dpc "$rc"
[ "$rc" -eq 0 ] || fail "get_dpc exited $rc after a successful set"
[ "$got" = "$VALUE" ] || fail "get_dpc returned a different value than was set"

"$HELPER" delete_dpc "$SERVICE" "$ACCOUNT"
rc=$?
describe delete_dpc "$rc"
[ "$rc" -eq 0 ] || fail "delete_dpc exited $rc"

"$HELPER" get_dpc "$SERVICE" "$ACCOUNT" >/dev/null
rc=$?
describe get_dpc "$rc"
[ "$rc" -eq 1 ] || fail "item still readable after delete (get_dpc exited $rc)"

echo "→ keychain helper round-tripped through the Data Protection Keychain"
