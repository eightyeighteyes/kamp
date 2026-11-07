"""KAMP-749: the packaged keychain path must fall back instead of writing plaintext.

`keychain-access-groups` is a restricted entitlement that AMFI will not honour
without a provisioning profile, so in every released build the kernel SIGKILLs
kamp-keychain-helper at exec. The packaged path used to delegate to the helper
unconditionally, so that failure reached `set_session`, which stored the
credential in SQLite as plaintext — with no second attempt.

The distinction these tests pin down: a helper that *ran* and reported
something is authoritative, while a helper that could not run at all has
learned nothing and must not be mistaken for an answer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import keyring.errors
import pytest
from pytest_mock import MockerFixture

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macos_keychain is macOS-only"
)

SENTINEL = "kamp-test-secret-7f3a9c"
FAKE_HELPER = "/fake/Contents/Resources/kamp-keychain-helper"


def _completed(returncode: int, stdout: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    return proc


@pytest.fixture()
def kc(mocker: MockerFixture) -> ModuleType:
    """macos_keychain with the helper 'present' and module latches reset."""
    from kamp_core import macos_keychain as _kc

    mocker.patch.object(_kc, "_helper_path", FAKE_HELPER)
    mocker.patch.object(_kc, "_helper_unusable", False)
    # Skip the DPC probe so the ctypes path goes straight to the Login Keychain,
    # which is the branch the fallback is supposed to reach.
    mocker.patch.object(_kc, "_dpc_unavailable", True)
    return _kc


class TestHelperCannotRun:
    """SIGKILL and exec failures must fall through, not surface as answers."""

    def test_sigkill_falls_back_to_ctypes_read(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """The exact production failure: AMFI kills the helper with SIGKILL."""
        mocker.patch.object(kc.subprocess, "run", return_value=_completed(-9))
        read = mocker.patch.object(kc, "_read_raw", return_value=SENTINEL)

        assert kc.get_password("kamp", "bandcamp") == SENTINEL
        read.assert_called_once_with("kamp", "bandcamp", use_dpc=False)

    def test_sigkill_falls_back_to_ctypes_write(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """The credential must reach the Login Keychain, not the caller's DB."""
        mocker.patch.object(kc.subprocess, "run", return_value=_completed(-9))
        update = mocker.patch.object(kc, "_SecItemUpdate", return_value=0)

        kc.set_password("kamp", "bandcamp", SENTINEL)
        assert update.called, "ctypes Login Keychain write was never attempted"

    def test_exec_failure_falls_back(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """Errno 86 'Bad CPU type' — a Rosetta process cannot exec an arm64 child."""
        mocker.patch.object(
            kc.subprocess, "run", side_effect=OSError(86, "Bad CPU type in executable")
        )
        mocker.patch.object(kc, "_read_raw", return_value=SENTINEL)

        assert kc.get_password("kamp", "bandcamp") == SENTINEL

    def test_unusable_helper_is_not_respawned(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """Latched, so we stop paying a spawn that is guaranteed to die."""
        run = mocker.patch.object(kc.subprocess, "run", return_value=_completed(-9))
        mocker.patch.object(kc, "_read_raw", return_value=SENTINEL)

        for _ in range(3):
            kc.get_password("kamp", "bandcamp")

        assert run.call_count == 1, "helper re-spawned after proving unrunnable"
        assert kc._helper_unusable is True

    def test_delete_falls_back_so_disconnect_still_works(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """Otherwise a ctypes-written credential survives every disconnect."""
        mocker.patch.object(kc.subprocess, "run", return_value=_completed(-9))
        delete = mocker.patch.object(kc, "_delete_raw")

        kc.delete_password("kamp", "bandcamp")
        delete.assert_called_once_with("kamp", "bandcamp", use_dpc=False)

    def test_fallback_is_logged(
        self, kc: ModuleType, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A silent fallback is how this went unnoticed for months."""
        import logging

        mocker.patch.object(kc.subprocess, "run", return_value=_completed(-9))
        mocker.patch.object(kc, "_read_raw", return_value=None)

        with caplog.at_level(logging.WARNING, logger="kamp_core.macos_keychain"):
            kc.get_password("kamp", "bandcamp")

        assert "helper unusable" in caplog.text
        assert SENTINEL not in caplog.text


class TestHelperRanAndAnswered:
    """A helper that ran is authoritative — do not second-guess it."""

    def test_not_found_is_an_answer_not_a_fallback(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            kc.subprocess, "run", return_value=_completed(kc._HELPER_EXIT_NOT_FOUND)
        )
        read = mocker.patch.object(kc, "_read_raw", return_value=SENTINEL)

        assert kc.get_password("kamp", "bandcamp") is None
        assert not read.called, "fell back despite an authoritative answer"
        assert kc._helper_unusable is False

    def test_locked_still_raises_keyring_locked(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            kc.subprocess, "run", return_value=_completed(kc._HELPER_EXIT_LOCKED)
        )
        with pytest.raises(keyring.errors.KeyringLocked):
            kc.get_password("kamp", "bandcamp")
        assert kc._helper_unusable is False

    def test_internal_error_still_raises(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            kc.subprocess, "run", return_value=_completed(kc._HELPER_EXIT_ERROR)
        )
        with pytest.raises(keyring.errors.KeyringError):
            kc.get_password("kamp", "bandcamp")


class TestTimeout:
    """A pending keychain dialog is Locked, not unusable."""

    def test_timeout_becomes_keyring_locked(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """Was an uncaught TimeoutExpired that 422'd the login handler."""
        mocker.patch.object(
            kc.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="helper", timeout=10),
        )
        with pytest.raises(keyring.errors.KeyringLocked):
            kc.get_password("kamp", "bandcamp")

    def test_timeout_does_not_latch(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """The next call may be answered, and ctypes would prompt identically."""
        mocker.patch.object(
            kc.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="helper", timeout=10),
        )
        with pytest.raises(keyring.errors.KeyringLocked):
            kc.get_password("kamp", "bandcamp")

        assert kc._helper_unusable is False


class TestSetSessionNoLongerWritesPlaintext:
    """The acceptance criterion, stated as a property of the stored row."""

    def test_sigkilled_helper_does_not_produce_a_plaintext_row(
        self, kc: ModuleType, mocker: MockerFixture, tmp_path: Path
    ) -> None:
        """End to end: this is what reproduced against the signed app.

        Asserts the *property* (no plaintext on disk), not the mechanism, so it
        would still fail if some future refactor reintroduced the fallback-to-DB
        behaviour by another route.
        """
        from kamp_core.library import LibraryIndex

        store: dict[str, str] = {}
        mocker.patch.object(kc.subprocess, "run", return_value=_completed(-9))
        mocker.patch.object(
            kc, "_read_raw", side_effect=lambda s, u, use_dpc: store.get(f"{s}/{u}")
        )

        # set_session verifies its write by reading back through get_password, so
        # the fake Login Keychain has to round-trip the real payload. _cf_data is
        # the one place the plaintext is still a str before becoming CFData, so
        # capture it there and leave the production call path intact.
        captured: dict[str, str] = {}
        real_cf_data = kc._cf_data

        def _capturing_cf_data(value: str) -> Any:
            captured["payload"] = value
            return real_cf_data(value)

        def _fake_update(find_q: Any, attrs: Any) -> int:
            store["kamp/bandcamp"] = captured["payload"]
            return 0

        mocker.patch.object(kc, "_cf_data", side_effect=_capturing_cf_data)
        mocker.patch.object(kc, "_SecItemUpdate", side_effect=_fake_update)

        index = LibraryIndex(tmp_path / "library.db")
        index.set_session("bandcamp", {"cookies": [{"value": SENTINEL}]})

        row = index._conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
        assert row is not None
        assert row["session_json"] is None, (
            "credential was stored in the DB despite the keychain fallback"
            " succeeding — this is the KAMP-749 defect"
        )

        raw = SENTINEL.encode()
        assert raw not in (tmp_path / "library.db").read_bytes()
        index.close()
