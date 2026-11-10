"""KAMP-753: the CI pre-flight that refuses a profile the helper cannot use.

Each test breaks exactly one thing the profile must get right, because each of
those, in production, is a SIGKILLed helper and a silent Login Keychain
fallback rather than a visible failure.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import plistlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_provision_profile.py"


@pytest.fixture(scope="module")
def cpp() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_provision_profile", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module by name
    spec.loader.exec_module(module)
    return module


NOW = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc)
APP_ID = "X6K4L8ZMLS.com.kamp.app.keychain-helper"
TEAM = "X6K4L8ZMLS"
GROUP = "X6K4L8ZMLS.com.kamp.app"


def _tlv(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 0x80:
        length = bytes([n])
    else:
        body = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(body)]) + body
    return bytes([tag]) + length + content


def _cert(not_after: dt.datetime, *, generalized: bool = False, pad: int = 0) -> bytes:
    """A DER certificate carrying only what cert_not_after walks past.

    *pad* inflates the issuer so the outer lengths need the long form, as every
    real certificate's do.
    """
    seq = 0x30
    if generalized:
        na = _tlv(0x18, not_after.strftime("%Y%m%d%H%M%SZ").encode())
    else:
        na = _tlv(0x17, not_after.strftime("%y%m%d%H%M%SZ").encode())
    nb = _tlv(0x17, b"260410021909Z")
    tbs = _tlv(
        seq,
        _tlv(0xA0, _tlv(0x02, b"\x02"))  # version v3
        + _tlv(0x02, b"\x75\x94\x36\x06")  # serial
        + _tlv(seq, _tlv(0x06, b"\x2a\x86"))  # signature algorithm
        + _tlv(seq, _tlv(0x0C, b"x" * pad))  # issuer
        + _tlv(seq, nb + na),  # validity
    )
    return _tlv(seq, tbs + _tlv(seq, b"") + _tlv(0x03, b"\x00"))


CERT = _cert(dt.datetime(2027, 2, 1, 22, 12, 15), pad=300)
CERT_SHA1 = hashlib.sha1(CERT).hexdigest().upper()


def _profile(**overrides: Any) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "ExpirationDate": dt.datetime(2044, 11, 5, 17, 34, 47),
        "DeveloperCertificates": [CERT],
        "Entitlements": {
            "com.apple.application-identifier": APP_ID,
            "keychain-access-groups": ["X6K4L8ZMLS.*"],
            "com.apple.developer.team-identifier": TEAM,
        },
    }
    profile.update(overrides)
    return profile


ENTITLEMENTS = {
    "com.apple.application-identifier": APP_ID,
    "com.apple.developer.team-identifier": TEAM,
    "keychain-access-groups": [GROUP],
}


class TestCertNotAfter:
    def test_utctime(self, cpp: ModuleType) -> None:
        assert cpp.cert_not_after(CERT) == dt.datetime(
            2027, 2, 1, 22, 12, 15, tzinfo=dt.timezone.utc
        )

    def test_generalized_time(self, cpp: ModuleType) -> None:
        """RFC 5280 switches to GeneralizedTime for dates from 2050."""
        when = dt.datetime(2051, 1, 1)
        der = _cert(when, generalized=True)
        assert cpp.cert_not_after(der) == when.replace(tzinfo=dt.timezone.utc)


class TestCheck:
    def test_matching_profile_passes(self, cpp: ModuleType) -> None:
        r = cpp.check(_profile(), ENTITLEMENTS, [CERT_SHA1], NOW)
        assert r.errors == []
        assert r.warnings == []

    def test_sha1_comparison_ignores_case(self, cpp: ModuleType) -> None:
        r = cpp.check(_profile(), ENTITLEMENTS, [CERT_SHA1.lower()], NOW)
        assert r.errors == []

    def test_profile_for_the_wrong_app_id_fails(self, cpp: ModuleType) -> None:
        """The easy mistake: storing the com.kamp.app profile in the secret."""
        profile = _profile()
        profile["Entitlements"][
            "com.apple.application-identifier"
        ] = "X6K4L8ZMLS.com.kamp.app"
        r = cpp.check(profile, ENTITLEMENTS, [CERT_SHA1], NOW)
        assert any("wrong profile" in e for e in r.errors)

    def test_helper_without_application_identifier_fails(self, cpp: ModuleType) -> None:
        """The pre-KAMP-753 entitlements file: only keychain-access-groups."""
        r = cpp.check(_profile(), {"keychain-access-groups": [GROUP]}, [CERT_SHA1], NOW)
        assert any("application-identifier" in e for e in r.errors)

    def test_team_mismatch_fails(self, cpp: ModuleType) -> None:
        ents = {**ENTITLEMENTS, "com.apple.developer.team-identifier": "OTHERTEAM1"}
        r = cpp.check(_profile(), ents, [CERT_SHA1], NOW)
        assert any("team" in e for e in r.errors)

    def test_uncovered_keychain_group_fails(self, cpp: ModuleType) -> None:
        profile = _profile()
        profile["Entitlements"]["keychain-access-groups"] = [
            "X6K4L8ZMLS.com.kamp.app.keychain-helper"
        ]
        r = cpp.check(profile, ENTITLEMENTS, [CERT_SHA1], NOW)
        assert any(GROUP in e for e in r.errors)

    def test_exact_keychain_group_is_covered(self, cpp: ModuleType) -> None:
        profile = _profile()
        profile["Entitlements"]["keychain-access-groups"] = [GROUP]
        assert cpp.check(profile, ENTITLEMENTS, [CERT_SHA1], NOW).errors == []

    def test_other_teams_wildcard_does_not_cover(self, cpp: ModuleType) -> None:
        profile = _profile()
        profile["Entitlements"]["keychain-access-groups"] = ["OTHERTEAM1.*"]
        assert cpp.check(profile, ENTITLEMENTS, [CERT_SHA1], NOW).errors

    def test_signing_cert_missing_from_profile_fails(self, cpp: ModuleType) -> None:
        """The renewal trap: a new certificate in CSC_LINK, the old profile kept."""
        r = cpp.check(_profile(), ENTITLEMENTS, ["AB" * 20], NOW)
        assert any("not in the profile" in e for e in r.errors)

    def test_no_signing_cert_given_fails(self, cpp: ModuleType) -> None:
        """An empty find-identity result must not read as "nothing to check"."""
        r = cpp.check(_profile(), ENTITLEMENTS, [], NOW)
        assert r.errors

    def test_certificate_inside_fail_window_fails(self, cpp: ModuleType) -> None:
        """The profile says 2044; the certificate it pins is the real deadline."""
        now = dt.datetime(2027, 1, 10, tzinfo=dt.timezone.utc)
        r = cpp.check(_profile(), ENTITLEMENTS, [CERT_SHA1], now)
        assert any("certificate" in e and "2027-02-01" in e for e in r.errors)

    def test_certificate_inside_warn_window_warns(self, cpp: ModuleType) -> None:
        now = dt.datetime(2026, 12, 15, tzinfo=dt.timezone.utc)
        r = cpp.check(_profile(), ENTITLEMENTS, [CERT_SHA1], now)
        assert r.errors == []
        assert any("2027-02-01" in w for w in r.warnings)

    def test_expired_profile_fails(self, cpp: ModuleType) -> None:
        profile = _profile(ExpirationDate=dt.datetime(2026, 11, 1))
        r = cpp.check(profile, ENTITLEMENTS, [CERT_SHA1], NOW)
        assert any("provisioning profile expires" in e for e in r.errors)


class TestMain:
    def test_decoded_plist_round_trip(
        self,
        cpp: ModuleType,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A decoded plist is read directly, so this runs on any CI platform.

        main() uses the real clock, so the certificate here is far from expiry.
        """
        far_cert = _cert(dt.datetime(2049, 1, 1), pad=300)
        profile = tmp_path / "profile.plist"
        profile.write_bytes(plistlib.dumps(_profile(DeveloperCertificates=[far_cert])))
        ents = tmp_path / "entitlements.plist"
        ents.write_bytes(plistlib.dumps(ENTITLEMENTS))
        far_sha1 = hashlib.sha1(far_cert).hexdigest()

        args = ["--profile", str(profile), "--entitlements", str(ents)]
        assert cpp.main([*args, "--signing-cert-sha1", far_sha1]) == 0
        assert "::error::" not in capsys.readouterr().out

        assert cpp.main([*args, "--signing-cert-sha1", "AB" * 20]) == 1
        assert "::error::" in capsys.readouterr().out
