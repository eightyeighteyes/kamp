#!/usr/bin/env python3
"""Pre-flight check: will this provisioning profile authorise the helper? (KAMP-753)

kamp-keychain-helper carries ``keychain-access-groups``, a restricted
entitlement. AMFI honours it only when the embedded provisioning profile
authorises every restricted entitlement the binary claims *and* lists the
certificate that signed it. When either is wrong the helper is SIGKILLed at
exec, the daemon falls back to the Login Keychain, and the app looks fine —
which is how the original defect survived for months. So this fails the build
loudly, with the reason, before any signing time is spent.

Checks, against the entitlements file the helper is actually signed with
(single source of truth — no hard-coded IDs to drift):

* the profile's application-identifier and team-identifier equal the ones the
  helper claims;
* every keychain access group the helper claims is covered by the profile
  (which grants a wildcard such as ``TEAMID.*``);
* each signing certificate (SHA-1, as ``security find-identity`` prints it) is
  among the profile's DeveloperCertificates;
* neither the profile nor its certificates expire within ``--fail-days``
  (warning within ``--warn-days``). The certificate is the real deadline: a
  Developer ID profile says it lasts ~18 years, but the certificate it pins
  expires far sooner, and renewing it means regenerating the profile.

Standard library only: the signing job runs outside the Poetry environment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_APP_ID_KEYS = ("com.apple.application-identifier", "application-identifier")
_TEAM_ID_KEY = "com.apple.developer.team-identifier"
_KEYCHAIN_KEY = "keychain-access-groups"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Minimal DER reading — just enough to find a certificate's notAfter.
# ---------------------------------------------------------------------------


def _read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """Return (tag, value, next_pos) for the DER element starting at *pos*."""
    tag = data[pos]
    length = data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[pos : pos + n], "big")
        pos += n
    return tag, data[pos : pos + length], pos + length


def _children(value: bytes) -> list[tuple[int, bytes]]:
    out = []
    pos = 0
    while pos < len(value):
        tag, inner, pos = _read_tlv(value, pos)
        out.append((tag, inner))
    return out


def cert_not_after(der: bytes) -> dt.datetime:
    """Parse notAfter from an X.509 certificate's DER encoding (UTC)."""
    _, cert, _ = _read_tlv(der, 0)
    _, tbs = _children(cert)[0]
    fields = _children(tbs)
    # tbsCertificate: [0] version (optional), serial, signature, issuer, validity
    if fields[0][0] == 0xA0:
        fields = fields[1:]
    validity = fields[3][1]
    tag, raw = _children(validity)[1]
    text = raw.decode("ascii")
    if tag == 0x17:  # UTCTime, YYMMDDHHMMSSZ
        parsed = dt.datetime.strptime(text, "%y%m%d%H%M%SZ")
    elif tag == 0x18:  # GeneralizedTime, YYYYMMDDHHMMSSZ
        parsed = dt.datetime.strptime(text, "%Y%m%d%H%M%SZ")
    else:
        raise ValueError(f"unexpected validity time tag 0x{tag:02x}")
    return parsed.replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# The check itself — pure, so the tests need no keychain or openssl.
# ---------------------------------------------------------------------------


def _app_id(entitlements: dict[str, Any]) -> str | None:
    for key in _APP_ID_KEYS:
        if key in entitlements:
            return str(entitlements[key])
    return None


def _group_covered(group: str, granted: list[str]) -> bool:
    for entry in granted:
        if entry == group:
            return True
        if entry.endswith("*") and group.startswith(entry[:-1]):
            return True
    return False


def _check_expiry(
    label: str, when: dt.datetime, now: dt.datetime, warn: int, fail: int, r: Report
) -> None:
    days = (when - now).days
    if days < fail:
        r.errors.append(
            f"{label} expires {when:%Y-%m-%d} ({days} days) — regenerate it now"
            " (KAMP-753 renewal: new Developer ID cert → new profile → update"
            " CSC_LINK and MACOS_KEYCHAIN_HELPER_PROFILE)"
        )
    elif days < warn:
        r.warnings.append(f"{label} expires {when:%Y-%m-%d} ({days} days)")


def check(
    profile: dict[str, Any],
    entitlements: dict[str, Any],
    signing_sha1s: list[str],
    now: dt.datetime,
    warn_days: int = 60,
    fail_days: int = 30,
) -> Report:
    r = Report()
    granted: dict[str, Any] = profile.get("Entitlements", {})

    wanted_app_id = _app_id(entitlements)
    if wanted_app_id is None:
        r.errors.append(
            "helper entitlements claim no com.apple.application-identifier;"
            " AMFI cannot match any profile without one"
        )
    elif _app_id(granted) != wanted_app_id:
        r.errors.append(
            f"profile is for {_app_id(granted)!r}, but the helper claims"
            f" {wanted_app_id!r} — wrong profile in the secret?"
        )

    wanted_team = entitlements.get(_TEAM_ID_KEY)
    if wanted_team is not None and granted.get(_TEAM_ID_KEY) != wanted_team:
        r.errors.append(
            f"profile team {granted.get(_TEAM_ID_KEY)!r} != helper team"
            f" {wanted_team!r}"
        )

    granted_groups = [str(g) for g in granted.get(_KEYCHAIN_KEY, [])]
    for group in entitlements.get(_KEYCHAIN_KEY, []):
        if not _group_covered(str(group), granted_groups):
            r.errors.append(
                f"keychain access group {group!r} is not authorised by the"
                f" profile (it grants {granted_groups})"
            )

    certs: list[bytes] = list(profile.get("DeveloperCertificates", []))
    profile_sha1s = {hashlib.sha1(c).hexdigest().upper() for c in certs}
    if not signing_sha1s:
        r.errors.append("no signing certificate given to compare against")
    for sha1 in signing_sha1s:
        if sha1.upper() not in profile_sha1s:
            r.errors.append(
                f"signing certificate {sha1.upper()} is not in the profile —"
                " the profile was generated for a different Developer ID"
                " certificate; regenerate it for the one in CSC_LINK"
            )

    expiry = profile.get("ExpirationDate")
    if isinstance(expiry, dt.datetime):
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=dt.timezone.utc)
        _check_expiry("provisioning profile", expiry, now, warn_days, fail_days, r)
    else:
        r.errors.append("profile has no ExpirationDate")
    for cert in certs:
        _check_expiry(
            f"profile certificate {hashlib.sha1(cert).hexdigest().upper()[:8]}…",
            cert_not_after(cert),
            now,
            warn_days,
            fail_days,
            r,
        )
    return r


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_profile(path: Path) -> dict[str, Any]:
    """Load a .provisionprofile (CMS-signed) or an already-decoded plist."""
    raw = path.read_bytes()
    if not raw.startswith((b"<?xml", b"bplist")):
        # The profile is a CMS envelope around an XML plist; `security`
        # verifies Apple's signature while unwrapping it.
        raw = subprocess.run(
            ["security", "cms", "-D", "-i", str(path)],
            check=True,
            capture_output=True,
        ).stdout
    return dict(plistlib.loads(raw))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--entitlements", type=Path, required=True)
    p.add_argument(
        "--signing-cert-sha1",
        action="append",
        default=[],
        help="SHA-1 of a signing identity (repeatable)",
    )
    p.add_argument("--warn-days", type=int, default=60)
    p.add_argument("--fail-days", type=int, default=30)
    args = p.parse_args(argv)

    report = check(
        load_profile(args.profile),
        dict(plistlib.loads(args.entitlements.read_bytes())),
        args.signing_cert_sha1,
        now=dt.datetime.now(dt.timezone.utc),
        warn_days=args.warn_days,
        fail_days=args.fail_days,
    )
    # GitHub Actions annotations, so the reason shows on the run summary.
    for w in report.warnings:
        print(f"::warning::{w}")
    for e in report.errors:
        print(f"::error::{e}")
    if not report.errors:
        print("→ provisioning profile authorises the helper's entitlements")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
