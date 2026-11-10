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
import threading
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
        """Absent everywhere is None, and the helper is not latched unusable.

        The only further read allowed is the KAMP-753 migration probe of the
        Login Keychain — never a DPC re-read over ctypes, which would be the
        unentitled path second-guessing the entitled one.
        """
        mocker.patch.object(
            kc.subprocess, "run", return_value=_completed(kc._HELPER_EXIT_NOT_FOUND)
        )
        read = mocker.patch.object(kc, "_read_raw", return_value=None)

        assert kc.get_password("kamp", "bandcamp") is None
        for call in read.call_args_list:
            assert call.kwargs == {"use_dpc": False}, "re-read DPC over ctypes"
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


# Mirrors _HELPER_EXIT_* in macos_keychain (and the Swift helper's exit codes).
kc_exit_ok, kc_exit_not_found, kc_exit_error = 0, 1, 3


class _FakeHelper:
    """A working helper backed by a dict standing in for the DPC.

    Routes on the op argument so each test can see exactly which operations
    went through the helper, and can make one of them misbehave.
    """

    def __init__(self) -> None:
        self.dpc: dict[str, str] = {}
        self.ops: list[str] = []
        self.fail_set = False
        self.corrupt_set = False

    def run(self, argv: list[str], **kwargs: Any) -> MagicMock:
        _, op, service, user = argv
        key = f"{service}/{user}"
        self.ops.append(op)
        if op == "get_dpc":
            if key in self.dpc:
                return _completed(kc_exit_ok, self.dpc[key].encode())
            return _completed(kc_exit_not_found)
        if op == "set_dpc":
            if self.fail_set:
                return _completed(kc_exit_error)
            value = kwargs["input"].decode().removesuffix("\n")
            self.dpc[key] = value + ("-corrupt" if self.corrupt_set else "")
            return _completed(kc_exit_ok)
        if op == "delete_dpc":
            self.dpc.pop(key, None)
            return _completed(kc_exit_ok)
        raise AssertionError(f"Login Keychain op {op!r} went through the helper")


class TestLoginKeychainMigration:
    """KAMP-753: a working helper must not strand a Login Keychain credential.

    Until now the helper never ran, so every packaged-build credential the
    KAMP-749 fallback wrote is in the Login Keychain. The helper's own read
    covers only the DPC, so without a migration the first build where the
    helper works would report every such user as logged out.

    Login Keychain items were created over ctypes by ``kamp`` and their ACL
    trusts ``kamp``, so the migration reads and deletes them in-process. The
    helper reading them would raise a prompt naming the helper and block for
    the full timeout on every retry.
    """

    @pytest.fixture()
    def helper(self, kc: ModuleType, mocker: MockerFixture) -> _FakeHelper:
        fake = _FakeHelper()
        mocker.patch.object(kc.subprocess, "run", side_effect=fake.run)
        return fake

    @pytest.fixture()
    def login(self, kc: ModuleType, mocker: MockerFixture) -> dict[str, str]:
        store: dict[str, str] = {"kamp/bandcamp": SENTINEL}

        def _read(service: str, user: str, use_dpc: bool) -> str | None:
            assert use_dpc is False
            return store.get(f"{service}/{user}")

        def _delete(service: str, user: str, use_dpc: bool) -> None:
            assert use_dpc is False
            store.pop(f"{service}/{user}", None)

        mocker.patch.object(kc, "_read_raw", side_effect=_read)
        mocker.patch.object(kc, "_delete_raw", side_effect=_delete)
        return store

    def test_login_credential_moves_to_dpc(
        self, kc: ModuleType, helper: _FakeHelper, login: dict[str, str]
    ) -> None:
        assert kc.get_password("kamp", "bandcamp") == SENTINEL
        assert helper.dpc == {"kamp/bandcamp": SENTINEL}
        assert login == {}, "Login Keychain copy was not removed after migrating"

    def test_second_read_is_served_from_dpc(
        self, kc: ModuleType, helper: _FakeHelper, login: dict[str, str]
    ) -> None:
        kc.get_password("kamp", "bandcamp")
        helper.ops.clear()

        assert kc.get_password("kamp", "bandcamp") == SENTINEL
        assert helper.ops == ["get_dpc"]

    def test_failed_dpc_write_keeps_the_login_copy(
        self, kc: ModuleType, helper: _FakeHelper, login: dict[str, str]
    ) -> None:
        """Deleting before a verified write would lose the credential."""
        helper.fail_set = True

        assert kc.get_password("kamp", "bandcamp") == SENTINEL
        assert login == {"kamp/bandcamp": SENTINEL}

    def test_unverified_dpc_write_keeps_the_login_copy(
        self, kc: ModuleType, helper: _FakeHelper, login: dict[str, str]
    ) -> None:
        helper.corrupt_set = True

        assert kc.get_password("kamp", "bandcamp") == SENTINEL
        assert login == {"kamp/bandcamp": SENTINEL}

    def test_failed_login_delete_still_returns_the_value(
        self,
        kc: ModuleType,
        helper: _FakeHelper,
        login: dict[str, str],
        mocker: MockerFixture,
    ) -> None:
        """The DPC copy is authoritative from here on, so a stale duplicate is harmless."""
        mocker.patch.object(
            kc, "_delete_raw", side_effect=keyring.errors.KeyringError("-25244")
        )
        assert kc.get_password("kamp", "bandcamp") == SENTINEL
        assert helper.dpc == {"kamp/bandcamp": SENTINEL}

    def test_migration_is_logged_without_the_value(
        self,
        kc: ModuleType,
        helper: _FakeHelper,
        login: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        with caplog.at_level(logging.INFO, logger="kamp_core.macos_keychain"):
            kc.get_password("kamp", "bandcamp")

        assert "migrated" in caplog.text
        assert SENTINEL not in caplog.text

    def test_absent_everywhere_writes_nothing(
        self, kc: ModuleType, helper: _FakeHelper, login: dict[str, str]
    ) -> None:
        login.clear()
        assert kc.get_password("kamp", "bandcamp") is None
        assert "set_dpc" not in helper.ops

    def test_doctor_reads_login_keychain_in_process(
        self, kc: ModuleType, helper: _FakeHelper, login: dict[str, str]
    ) -> None:
        """_FakeHelper raises if a Login op reaches it."""
        assert kc._get_login_keychain_password("kamp", "bandcamp") == SENTINEL

    def test_delete_password_clears_login_keychain_in_process(
        self, kc: ModuleType, helper: _FakeHelper, login: dict[str, str]
    ) -> None:
        helper.dpc["kamp/bandcamp"] = SENTINEL

        kc.delete_password("kamp", "bandcamp")

        assert helper.dpc == {}
        assert login == {}

    def test_backend_state_reports_helper_only_while_usable(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """KAMP-743: never claim the DPC on the strength of the path alone."""
        assert kc.backend_state()["implementation"] == "helper"
        mocker.patch.object(kc, "_helper_unusable", True)
        assert kc.backend_state()["implementation"] == "ctypes"


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


class TestCtypesCallsAreBounded:
    """Security.framework blocks until the user answers; we must not.

    Verified against a signed build: before this guard, a ctypes Login Keychain
    read sat for over two minutes on an unanswered prompt and only returned
    when the dialog was clicked. In the daemon that is a thread hung forever on
    a login or a playback lookahead.
    """

    def test_blocked_call_raises_keyring_locked(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(kc, "_CTYPES_TIMEOUT_SECONDS", 0.05)
        started = threading.Event()

        def _blocks_forever() -> str:
            started.set()
            threading.Event().wait()  # never returns
            return "unreachable"

        with pytest.raises(keyring.errors.KeyringLocked, match="timed out"):
            kc._call_with_timeout("test", _blocks_forever)

        assert started.is_set(), "the call never actually ran"

    def test_fast_call_is_unaffected(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        assert kc._call_with_timeout("test", lambda: "value") == "value"

    def test_a_none_result_is_returned_not_mistaken_for_a_failure(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """`None` is a real answer here: "no such credential".

        The runner records results in a list precisely so an appended None is
        distinguishable from "the call never got that far". A truthiness check
        would turn every absent credential into a spurious error.
        """
        assert kc._call_with_timeout("test", lambda: None) is None

    def test_exception_propagates_to_the_caller(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """A real OSStatus error must not be masked by the threading."""

        def _raises() -> str:
            raise keyring.errors.KeyringError("Security framework error (OSStatus -42)")

        with pytest.raises(keyring.errors.KeyringError, match="-42"):
            kc._call_with_timeout("test", _raises)

    def test_pending_calls_are_capped(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """A timed-out thread keeps waiting, so they must not accumulate."""
        mocker.patch.object(kc, "_pending_ctypes_calls", kc._MAX_PENDING_CTYPES_CALLS)

        with pytest.raises(keyring.errors.KeyringLocked, match="already waiting"):
            kc._call_with_timeout("test", lambda: "value")

    def test_counter_is_released_after_a_normal_call(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """Otherwise ordinary use would walk into the cap after four reads."""
        mocker.patch.object(kc, "_pending_ctypes_calls", 0)
        for _ in range(kc._MAX_PENDING_CTYPES_CALLS + 2):
            kc._call_with_timeout("test", lambda: "value")
        assert kc._pending_ctypes_calls == 0

    def test_read_raw_goes_through_the_bound(
        self, kc: ModuleType, mocker: MockerFixture
    ) -> None:
        """The wrapper must be on the path callers actually use."""
        blocking = mocker.patch.object(kc, "_read_raw_blocking", return_value=SENTINEL)
        assert kc._read_raw("kamp", "lastfm", use_dpc=False) == SENTINEL
        blocking.assert_called_once_with("kamp", "lastfm", False)


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
