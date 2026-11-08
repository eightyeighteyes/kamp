"""Tests for the credential-location diagnostics added by KAMP-743.

Two concerns:

* ``kamp_core.credential_probe`` / ``macos_keychain.credential_provenance`` —
  report *where* a credential lives without revealing it, and never claim
  "absent" for the Data Protection Keychain when the process cannot read it.
* ``LibraryIndex.set_session``'s fallback warnings — every path that writes a
  credential to the SQLite column must say which backend produced it, so a
  plaintext row found later can be attributed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

import keyring.backends.fail
import keyring.errors
import pytest
from pytest_mock import MockerFixture

from kamp_core import credential_probe, library
from kamp_core.library import LibraryIndex

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="macos_keychain is macOS-only"
)

SENTINEL = "kamp-test-secret-7f3a9c"


class TestCredentialDigest:
    def test_digest_is_deterministic(self) -> None:
        assert credential_probe.credential_digest("abc") == (
            credential_probe.credential_digest("abc")
        )

    def test_digest_differs_for_different_values(self) -> None:
        assert credential_probe.credential_digest("abc") != (
            credential_probe.credential_digest("abd")
        )

    def test_digest_does_not_leak_the_value(self) -> None:
        """The whole point: safe to paste into a ticket or a log."""
        digest = credential_probe.credential_digest(SENTINEL)
        assert SENTINEL not in digest
        assert len(digest) == 12

    def test_equal_values_in_two_stores_produce_equal_digests(self) -> None:
        """How the report shows a keychain item and a DB row agree."""
        blob = json.dumps({"cookies": [{"name": "identity", "value": SENTINEL}]})
        assert credential_probe.credential_digest(blob) == (
            credential_probe.credential_digest(blob)
        )

    def test_digest_is_not_a_bare_sha256_of_the_credential(self) -> None:
        """A plain digest of a secret trips CodeQL py/weak-sensitive-data-hashing.

        Pinned so that "simplifying" this back to hashlib.sha256 fails here
        rather than re-breaking the CodeQL check on a later PR. The property is
        what matters, not the exact KDF, so this asserts only that the output
        is *not* the bare digest.
        """
        bare = hashlib.sha256(SENTINEL.encode("utf-8")).hexdigest()[:12]
        assert credential_probe.credential_digest(SENTINEL) != bare

    def test_describe_carries_status_and_extras(self) -> None:
        report = credential_probe.describe(credential_probe.PRESENT, length=7)
        assert report == {"status": credential_probe.PRESENT, "length": 7}


@darwin_only
class TestCredentialProvenance:
    """``credential_provenance`` must never overstate what it knows."""

    def test_ctypes_mode_reports_dpc_unknown_not_absent(
        self, mocker: MockerFixture
    ) -> None:
        """The specific error that misdirected the first pass at KAMP-743.

        An unentitled process cannot enumerate the Data Protection Keychain, so
        a "not found" from it is indistinguishable from "not allowed to look".
        Reporting that as absence is what made a credential look like it had
        vanished.
        """
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", None)
        mocker.patch.object(kc, "_read_raw", return_value=None)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=None)

        result = kc.credential_provenance("kamp", "bandcamp")

        assert result["implementation"] == "ctypes"
        dpc = result["dpc"]
        assert isinstance(dpc, dict)
        assert dpc["status"] == credential_probe.UNKNOWN_NO_ENTITLEMENT
        assert dpc["status"] != credential_probe.ABSENT

    def test_ctypes_mode_trusts_a_positive_dpc_hit(self, mocker: MockerFixture) -> None:
        """A value actually returned is trustworthy even unentitled."""
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", None)
        mocker.patch.object(kc, "_read_raw", return_value=SENTINEL)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=None)

        dpc = kc.credential_provenance("kamp", "bandcamp")["dpc"]
        assert isinstance(dpc, dict)
        assert dpc["status"] == credential_probe.PRESENT
        assert dpc["length"] == len(SENTINEL)
        assert dpc["digest"] == credential_probe.credential_digest(SENTINEL)

    def test_helper_mode_answer_is_authoritative(self, mocker: MockerFixture) -> None:
        """With the entitled helper, absent really means absent."""
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", "/fake/kamp-keychain-helper")
        mocker.patch.object(kc, "_helper_get", return_value=None)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=None)

        result = kc.credential_provenance("kamp", "bandcamp")
        assert result["implementation"] == "helper"
        dpc = result["dpc"]
        assert isinstance(dpc, dict)
        assert dpc["status"] == credential_probe.ABSENT

    def test_login_keychain_present_reports_digest(self, mocker: MockerFixture) -> None:
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", None)
        mocker.patch.object(kc, "_read_raw", return_value=None)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=SENTINEL)

        login = kc.credential_provenance("kamp", "lastfm")["login_keychain"]
        assert isinstance(login, dict)
        assert login["status"] == credential_probe.PRESENT
        assert login["digest"] == credential_probe.credential_digest(SENTINEL)

    def test_locked_keychain_is_distinguished_from_absent(
        self, mocker: MockerFixture
    ) -> None:
        """A locked store is reachable-but-unknown, not empty."""
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", None)
        mocker.patch.object(kc, "_read_raw", return_value=None)
        mocker.patch.object(
            kc,
            "_get_login_keychain_password",
            side_effect=keyring.errors.KeyringLocked(
                "Keychain locked (OSStatus -25308)"
            ),
        )

        login = kc.credential_provenance("kamp", "lastfm")["login_keychain"]
        assert isinstance(login, dict)
        assert login["status"] == credential_probe.LOCKED
        assert "-25308" in str(login["detail"])

    def test_unexpected_error_is_reported_with_type(
        self, mocker: MockerFixture
    ) -> None:
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", None)
        mocker.patch.object(kc, "_read_raw", return_value=None)
        mocker.patch.object(
            kc, "_get_login_keychain_password", side_effect=OSError("agent gone")
        )

        login = kc.credential_provenance("kamp", "lastfm")["login_keychain"]
        assert isinstance(login, dict)
        assert login["status"] == credential_probe.ERROR
        assert "OSError" in str(login["detail"])

    def test_provenance_never_returns_the_credential(
        self, mocker: MockerFixture
    ) -> None:
        """Property test: no field anywhere in the report holds the secret."""
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", None)
        mocker.patch.object(kc, "_read_raw", return_value=SENTINEL)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=SENTINEL)

        result = kc.credential_provenance("kamp", "bandcamp")
        assert SENTINEL not in json.dumps(result, default=str)

    def test_an_unusable_helper_reports_ctypes_not_helper(
        self, mocker: MockerFixture
    ) -> None:
        """The packaged-build case: the binary exists and AMFI killed it.

        KAMP-749 latches ``_helper_unusable`` the first time the helper cannot
        run, and in every released build it does latch, because the restricted
        ``keychain-access-groups`` entitlement is unauthorised (KAMP-753).
        Deriving the implementation from ``_helper_path`` alone would name the
        Data Protection Keychain while the credential actually went to the Login
        Keychain — the exact misattribution this report exists to prevent.
        """
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", "/fake/kamp-keychain-helper")
        mocker.patch.object(kc, "_helper_unusable", True)
        mocker.patch.object(kc, "_read_raw", return_value=None)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=None)

        result = kc.credential_provenance("kamp", "bandcamp")

        assert result["implementation"] == "ctypes"
        assert result["helper_unusable"] is True
        assert result["helper_path"] == "/fake/kamp-keychain-helper"

    def test_a_helper_that_cannot_run_is_unknown_not_an_error(
        self, mocker: MockerFixture
    ) -> None:
        """``_HelperUnusable`` is not an answer about the credential.

        Left to ``_probe``'s broad ``except Exception`` it would be described as
        ERROR, which reads as "the store reported a problem" when in fact the
        store was never asked. It has to come out as UNKNOWN, with the signal
        or errno kept as the diagnostic.
        """
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", "/fake/kamp-keychain-helper")
        mocker.patch.object(kc, "_helper_unusable", False)
        mocker.patch.object(
            kc,
            "_helper_get",
            side_effect=kc._HelperUnusable("killed by signal 9"),
        )
        mocker.patch.object(kc, "_read_raw", return_value=None)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=None)

        dpc = kc.credential_provenance("kamp", "bandcamp")["dpc"]
        assert isinstance(dpc, dict)
        assert dpc["status"] == credential_probe.UNKNOWN_NO_ENTITLEMENT
        assert dpc["status"] != credential_probe.ERROR
        assert "signal 9" in str(dpc["helper_error"])

    def test_a_dead_helper_still_trusts_a_positive_unentitled_hit(
        self, mocker: MockerFixture
    ) -> None:
        """Falling through to the ctypes read is worth doing, not just polite.

        An unentitled "not found" proves nothing, but an unentitled *hit* is a
        real credential — so a dead helper must not short-circuit the report
        into UNKNOWN when the value is sitting right there.
        """
        from kamp_core import macos_keychain as kc

        mocker.patch.object(kc, "_helper_path", "/fake/kamp-keychain-helper")
        mocker.patch.object(kc, "_helper_unusable", True)
        mocker.patch.object(kc, "_read_raw", return_value=SENTINEL)
        mocker.patch.object(kc, "_get_login_keychain_password", return_value=None)

        dpc = kc.credential_provenance("kamp", "bandcamp")["dpc"]
        assert isinstance(dpc, dict)
        assert dpc["status"] == credential_probe.PRESENT
        assert dpc["digest"] == credential_probe.credential_digest(SENTINEL)


class TestCredentialBackendId:
    """The id has to describe the store that will actually service the call.

    Review feedback on PR #835: the generic arm returned a bare ``"keyring"``
    whenever neither native module was importable, which can be wrong three
    ways — and worst inside ``set_session``'s ``NoKeyringError`` handler, where
    the message says "no keyring backend" while the field claimed one.
    """

    def test_names_the_failing_backend_rather_than_claiming_keyring(
        self, mocker: MockerFixture
    ) -> None:
        """The self-contradiction the review caught."""
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch(
            "kamp_core.library.keyring.get_keyring",
            return_value=keyring.backends.fail.Keyring(),
        )

        backend_id = library._credential_backend_id()
        assert backend_id != "keyring"
        assert "fail.Keyring" in backend_id

    def test_reports_the_helper_latch_so_ctypes_is_not_read_as_dev(
        self, mocker: MockerFixture
    ) -> None:
        """``impl=ctypes`` means two different things after KAMP-749.

        In dev there is no helper; in a packaged build there is one and AMFI
        killed it. The warning has to distinguish them, or a plaintext row from
        a released build looks like it came from a developer's machine.
        """

        class StubKc:
            @staticmethod
            def backend_state() -> dict[str, object]:
                return {
                    "implementation": "ctypes",
                    "helper_path": "/fake/kamp-keychain-helper",
                    "helper_unusable": True,
                    "dpc_unavailable": True,
                }

        mocker.patch("kamp_core.library._mac_kc", StubKc)

        backend_id = library._credential_backend_id()
        assert "impl=ctypes" in backend_id
        assert "helper_unusable=True" in backend_id

    def test_omits_the_helper_latch_when_there_is_no_helper(
        self, mocker: MockerFixture
    ) -> None:
        """Dev: the latch describes nothing, so it is noise in every log line."""

        class StubKc:
            @staticmethod
            def backend_state() -> dict[str, object]:
                return {
                    "implementation": "ctypes",
                    "helper_path": None,
                    "helper_unusable": False,
                    "dpc_unavailable": True,
                }

        mocker.patch("kamp_core.library._mac_kc", StubKc)

        backend_id = library._credential_backend_id()
        assert "impl=ctypes" in backend_id
        assert "helper_unusable" not in backend_id

    def test_names_the_real_backend_when_one_exists(
        self, mocker: MockerFixture
    ) -> None:
        class StubBackend:
            pass

        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch(
            "kamp_core.library.keyring.get_keyring", return_value=StubBackend()
        )

        assert "StubBackend" in library._credential_backend_id()

    @pytest.mark.parametrize("platform", ["darwin", "win32"])
    def test_flags_a_missing_native_module(
        self, platform: str, mocker: MockerFixture
    ) -> None:
        """Reaching the generic arm on darwin/win32 means an import failed.

        The native imports are wrapped in `except Exception`, so that is
        otherwise invisible — and a plaintext row from a macOS machine would
        look like an ordinary Linux-style fallback.
        """
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch("kamp_core.library.sys.platform", platform)
        mocker.patch("kamp_core.library.keyring.get_keyring", return_value=object())

        assert f"native-module-missing={platform}" in library._credential_backend_id()

    def test_no_native_flag_on_other_platforms(self, mocker: MockerFixture) -> None:
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch("kamp_core.library.sys.platform", "linux")
        mocker.patch("kamp_core.library.keyring.get_keyring", return_value=object())

        assert "native-module-missing" not in library._credential_backend_id()


class TestFallbackWarningsNameTheBackend:
    """Every path that writes a plaintext credential must be attributable.

    Before KAMP-743 these warnings did not say which of the three platform
    backends had run, and the no-keyring path logged nothing at all — so a
    plaintext row on disk could not be traced to the code that wrote it.
    """

    def _index(self, tmp_path: Path) -> LibraryIndex:
        return LibraryIndex(tmp_path / "library.db")

    def test_no_keyring_backend_now_warns(
        self, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        """This arm used to be a bare ``pass`` — silent plaintext."""
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.NoKeyringError(),
        )
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)
        # Whatever raises NoKeyringError is a fail.Keyring, so the warning must
        # name it instead of asserting a backend the same line calls absent.
        mocker.patch(
            "kamp_core.library.keyring.get_keyring",
            return_value=keyring.backends.fail.Keyring(),
        )

        index = self._index(tmp_path)
        with caplog.at_level(logging.WARNING, logger="kamp_core.library"):
            index.set_session("bandcamp", {"cookies": [{"value": SENTINEL}]})

        assert "no keyring backend" in caplog.text
        assert "PLAINTEXT" in caplog.text
        assert "payload_len=" in caplog.text
        # Not "backend=keyring": that contradicted "no keyring backend" in the
        # same sentence (PR #835 review).
        assert "backend=keyring(" in caplog.text
        assert "fail.Keyring" in caplog.text
        assert SENTINEL not in caplog.text, "warning must not echo the credential"
        index.close()

    def test_keyring_error_warning_names_backend(
        self, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.KeyringError("OSStatus -25308"),
        )
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)

        index = self._index(tmp_path)
        with caplog.at_level(logging.WARNING, logger="kamp_core.library"):
            index.set_session("bandcamp", {"cookies": []})

        assert "keychain write failed" in caplog.text
        assert "backend=keyring" in caplog.text
        # The existing exception text already carries the status code; the
        # diagnostic is only useful if that survives alongside the backend id.
        assert "-25308" in caplog.text
        index.close()

    def test_unexpected_exception_warning_names_backend(
        self, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch(
            "kamp_core.library.keyring.set_password", side_effect=OSError("stub data")
        )
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)

        index = self._index(tmp_path)
        with caplog.at_level(logging.WARNING, logger="kamp_core.library"):
            index.set_session("bandcamp", {"cookies": []})

        assert "raised unexpected OSError" in caplog.text
        assert "backend=keyring" in caplog.text
        index.close()

    def test_verify_mismatch_warning_reports_both_lengths(
        self, tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A read-back that returns nothing is the packaged-app signature."""
        mocker.patch("kamp_core.library._mac_kc", None)
        mocker.patch("kamp_core.library._win_cred", None)
        mocker.patch("kamp_core.library.keyring.set_password")
        mocker.patch("kamp_core.library.keyring.get_password", return_value=None)

        index = self._index(tmp_path)
        with caplog.at_level(logging.WARNING, logger="kamp_core.library"):
            index.set_session("bandcamp", {"cookies": []})

        assert "did not verify" in caplog.text
        assert "readback_len=n/a" in caplog.text
        assert "payload_len=" in caplog.text
        index.close()
