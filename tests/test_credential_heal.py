"""Tests for the startup heal of plaintext credential rows (KAMP-755).

Every packaged macOS build since 2026-04-23 wrote session credentials as
plaintext into ``sessions.session_json``, because the entitled keychain helper
is SIGKILLed by AMFI (KAMP-753). KAMP-749 fixed the write path; nothing moved
the rows already on disk. ``heal_plaintext_sessions`` is that pass.

Three things about this file exist for reasons that cost a measurement:

* **A positive-control keychain is mandatory.** ``tests/conftest.py`` has an
  autouse fixture stubbing the four Security.framework entry points to return
  ``errSecItemNotFound``, so the real ``set_password`` *always* raises in the
  suite and ``set_session`` always falls back to plaintext. A test asserting
  "a failing store leaves the row intact" therefore passes with **zero**
  patching and proves nothing. ``fake_keychain`` below is the inverse: a store
  that actually round-trips, so the success path is reachable at all.
* **Patch ``kamp_core.library._mac_kc``, never ``kamp_core.macos_keychain.*``.**
  CI runs this suite on ubuntu, windows and macOS, and ``macos_keychain`` does
  not import off macOS. Substituting the module reference inside ``library``
  exercises the macOS branch identically on all three.
* **The keychain-wins rule is deliberate, not a shortcut.** ``get_session``
  already returns the keychain value whenever there is one and only reaches the
  DB row when the keychain answers ``None``, so where an item exists it is
  *already* the credential in use and the row is unreachable. The heal agrees
  with the runtime rather than inventing a precedence of its own.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import keyring.errors
import pytest
from pytest_mock import MockerFixture

from kamp_core.library import LibraryIndex

#: High-entropy marker, searched as raw bytes. Not a plausible album or artist
#: name -- matching on "cookies" or "identity" is useless in a music library.
SENTINEL = "kamp755-6b4f2e9d1c8a3057-heal-marker"

#: The real observed bandcamp payload size, and under the 4061-byte max in-page
#: payload at page_size 4096, so it allocates no overflow pages.
ROW_SIZE = 2381


def _payload(marker: str = SENTINEL, total: int = ROW_SIZE) -> dict[str, str]:
    return {"marker": marker, "pad": "x" * max(total - len(marker), 0)}


class FakeKeychain:
    """A keychain that works, and records what was asked of it.

    Mirrors the two ``macos_keychain`` entry points ``library`` actually calls:
    ``set_password(service, account, value)`` and ``get_password(service,
    account)`` -- note kamp passes ``"kamp"`` as the service and the kamp
    service name as the *account*.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.get_error: BaseException | None = None
        self.set_error: BaseException | None = None
        #: When set, the read-back after a write returns this instead of the
        #: written value, simulating a write that reports success but does not
        #: round-trip -- the failure mode KAMP-743 was opened to diagnose.
        self.readback_override: str | None = None

    def get_password(self, service: str, account: str) -> str | None:
        self.reads.append(account)
        if self.get_error is not None:
            raise self.get_error
        if self.readback_override is not None and (service, account) in self.store:
            return self.readback_override
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.writes.append(account)
        if self.set_error is not None:
            raise self.set_error
        self.store[(service, account)] = value

    def backend_state(self) -> dict[str, object]:
        """Needed because every warning names the backend via
        ``_credential_backend_id()``. Shaped like the real module's, with
        ``helper_path`` None so the id reads as the dev/ctypes case -- the
        point of the field is that a plaintext row found later can be
        attributed to the path that wrote it (KAMP-743).
        """
        return {
            "implementation": "ctypes",
            "helper_path": None,
            "helper_unusable": False,
            "dpc_unavailable": True,
        }


@pytest.fixture()
def fake_keychain(mocker: MockerFixture) -> FakeKeychain:
    """Install a working keychain in place of the real macOS module."""
    kc = FakeKeychain()
    mocker.patch("kamp_core.library._mac_kc", kc)
    mocker.patch("kamp_core.library._win_cred", None)
    return kc


def _seed_plaintext(index: LibraryIndex, service: str, payload: dict[str, str]) -> str:
    """Write a plaintext credential row directly, bypassing set_session.

    Deliberately direct: ``set_session`` is the thing whose fallback produced
    these rows in the field, and routing the fixture through it would make the
    seed depend on which branch the test environment happens to take.
    """
    raw = json.dumps(payload)
    index._conn.execute(
        "INSERT INTO sessions (service, session_json, updated_at)"
        " VALUES (?, ?, 0) ON CONFLICT(service) DO UPDATE SET session_json = ?",
        (service, raw, raw),
    )
    index._conn.commit()
    return raw


def _row(index: LibraryIndex, service: str) -> str | None:
    found = index._conn.execute(
        "SELECT session_json FROM sessions WHERE service = ?", (service,)
    ).fetchone()
    return None if found is None else found["session_json"]


def _file_hits(db_path: Path, needle: str = SENTINEL) -> int:
    raw = needle.encode("utf-8")
    total = 0
    for path in (db_path, Path(str(db_path) + "-wal")):
        if path.exists():
            total += path.read_bytes().count(raw)
    return total


class TestKeychainAlreadyHoldsTheCredential:
    """The common case on an install that has logged in since KAMP-749."""

    def test_row_is_cleared_without_a_keychain_write(
        self, tmp_path: Path, fake_keychain: FakeKeychain
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        try:
            _seed_plaintext(index, "bandcamp", _payload())
            fake_keychain.store[("kamp", "bandcamp")] = json.dumps(_payload())

            assert index.heal_plaintext_sessions() == 1

            assert _row(index, "bandcamp") is None
            assert fake_keychain.writes == [], "must not rewrite an existing item"
        finally:
            index.close()

    def test_divergent_length_is_warned_about_before_discarding(
        self,
        tmp_path: Path,
        fake_keychain: FakeKeychain,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Discarding a *different* value must not be silent."""
        index = LibraryIndex(tmp_path / "library.db")
        try:
            _seed_plaintext(index, "bandcamp", _payload())
            fake_keychain.store[("kamp", "bandcamp")] = json.dumps(_payload(total=600))

            with caplog.at_level("WARNING"):
                assert index.heal_plaintext_sessions() == 1

            assert "differ" in caplog.text.lower()
            # Lengths are reportable; the values are not.
            assert SENTINEL not in caplog.text
        finally:
            index.close()


class TestKeychainIsEmpty:
    """The migrating case: move the credential, verify, then clear."""

    def test_credential_is_written_verified_and_row_cleared(
        self, tmp_path: Path, fake_keychain: FakeKeychain
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        try:
            raw = _seed_plaintext(index, "bandcamp", _payload())

            assert index.heal_plaintext_sessions() == 1

            assert fake_keychain.store[("kamp", "bandcamp")] == raw
            assert _row(index, "bandcamp") is None
            # The credential must still be reachable the normal way.
            assert index.get_session("bandcamp") == _payload()
        finally:
            index.close()

    def test_unverified_write_leaves_the_row_intact(
        self, tmp_path: Path, fake_keychain: FakeKeychain
    ) -> None:
        """A write that does not round-trip must never clear the row."""
        index = LibraryIndex(tmp_path / "library.db")
        try:
            raw = _seed_plaintext(index, "bandcamp", _payload())
            fake_keychain.readback_override = "something else entirely"

            assert index.heal_plaintext_sessions() == 0
            assert _row(index, "bandcamp") == raw
        finally:
            index.close()

    def test_failing_store_leaves_the_row_intact(
        self,
        tmp_path: Path,
        fake_keychain: FakeKeychain,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        try:
            raw = _seed_plaintext(index, "bandcamp", _payload())
            fake_keychain.set_error = keyring.errors.PasswordSetError("nope")

            with caplog.at_level("WARNING"):
                assert index.heal_plaintext_sessions() == 0

            assert _row(index, "bandcamp") == raw, "credential must not be destroyed"
            assert "backend=" in caplog.text, "warning must name the backend"
        finally:
            index.close()


class TestAbortAndSkipBehaviour:
    def test_keyring_locked_aborts_the_whole_pass(
        self, tmp_path: Path, fake_keychain: FakeKeychain
    ) -> None:
        """One locked read must not burn the pending-call budget on the rest.

        macos_keychain allows only _MAX_PENDING_CTYPES_CALLS (4) outstanding
        calls, and a call that timed out on an unanswered prompt keeps its slot
        until the user answers. Pressing on would exhaust the budget and make
        later legitimate get_session calls fail instantly -- bringing the app up
        logged out of everything, which is worse than the plaintext row.
        """
        index = LibraryIndex(tmp_path / "library.db")
        try:
            _seed_plaintext(index, "bandcamp", _payload())
            _seed_plaintext(index, "lastfm", _payload(total=73))
            fake_keychain.get_error = keyring.errors.KeyringLocked("prompt pending")

            assert index.heal_plaintext_sessions() == 0

            assert len(fake_keychain.reads) == 1, "stopped after the first lock"
            assert _row(index, "bandcamp") is not None
            assert _row(index, "lastfm") is not None
        finally:
            index.close()

    def test_non_json_row_is_skipped_without_aborting(
        self, tmp_path: Path, fake_keychain: FakeKeychain
    ) -> None:
        """A corrupt row must not take the rest of the heal -- or startup -- down."""
        index = LibraryIndex(tmp_path / "library.db")
        try:
            index._conn.execute(
                "INSERT INTO sessions (service, session_json, updated_at)"
                " VALUES ('bandcamp', 'not json at all', 0)"
            )
            index._conn.commit()
            _seed_plaintext(index, "lastfm", _payload(total=73))

            assert index.heal_plaintext_sessions() == 1

            assert _row(index, "bandcamp") == "not json at all", "left for inspection"
            assert _row(index, "lastfm") is None
        finally:
            index.close()

    def test_value_guard_spares_a_row_rewritten_concurrently(
        self, tmp_path: Path, fake_keychain: FakeKeychain, mocker: MockerFixture
    ) -> None:
        """A newer credential written during the heal must survive it.

        The window is real: a keychain call can block for 10s, and nothing stops
        a second daemon being started by hand against the same production DB
        (KAMP-737). An unguarded NULL would destroy a credential the heal never
        migrated.
        """
        index = LibraryIndex(tmp_path / "library.db")
        try:
            _seed_plaintext(index, "bandcamp", _payload())
            newer = json.dumps(_payload(marker="written-while-healing"))

            def _write_newer_row(service: str, account: str) -> str | None:
                # Fires while the heal holds its pre-read copy of the row, which
                # is exactly the interleaving the value guard exists for.
                index._conn.execute(
                    "UPDATE sessions SET session_json = ? WHERE service = ?",
                    (newer, account),
                )
                index._conn.commit()
                return None

            mocker.patch.object(
                fake_keychain, "get_password", side_effect=_write_newer_row
            )

            assert index.heal_plaintext_sessions() == 0
            assert _row(index, "bandcamp") == newer, "newer credential was destroyed"
        finally:
            index.close()


class TestPlatformGate:
    def test_no_op_without_a_macos_keychain(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """On Windows the DPAPI-wrapped row IS the store, not damage to repair.

        There, ``_mac_kc is None`` and ``_win_cred is not None``, so neither of
        ``set_session``'s keychain branches runs and a non-NULL session_json is
        the intended steady state (KAMP-280). A heal keyed on that predicate
        would retry forever and rewrite the row on every boot.
        """
        mocker.patch("kamp_core.library._mac_kc", None)
        index = LibraryIndex(tmp_path / "library.db")
        try:
            raw = _seed_plaintext(index, "bandcamp", _payload())
            assert index.heal_plaintext_sessions() == 0
            assert _row(index, "bandcamp") == raw
        finally:
            index.close()


class TestIdempotence:
    def test_second_run_touches_no_keychain(
        self, tmp_path: Path, fake_keychain: FakeKeychain
    ) -> None:
        """An already-healed install must make zero keychain calls.

        This is what keeps the heal off the startup critical path for everyone
        who does not need it: the DB SELECT is the gate, so there is no prompt
        risk and no pending-call cost on a healthy machine.
        """
        index = LibraryIndex(tmp_path / "library.db")
        try:
            _seed_plaintext(index, "bandcamp", _payload())
            assert index.heal_plaintext_sessions() == 1

            fake_keychain.reads.clear()
            fake_keychain.writes.clear()

            assert index.heal_plaintext_sessions() == 0
            assert fake_keychain.reads == []
            assert fake_keychain.writes == []
        finally:
            index.close()


class TestByteLevelErasure:
    """The row must leave the FILES, not just the query results.

    No VACUUM is needed here and that is worth pinning: ``secure_delete=ON``
    (set on every connection by KAMP-748) plus the TRUNCATE checkpoint make an
    ``UPDATE ... SET session_json = NULL`` clean, and at 2381 bytes there are no
    overflow pages. If either of those facts ever changes, this test is the
    thing that notices.
    """

    def test_assumptions_behind_this_test_hold(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        try:
            page_size = index._conn.execute("PRAGMA page_size").fetchone()[0]
            secure_delete = index._conn.execute("PRAGMA secure_delete").fetchone()[0]
        finally:
            index.close()
        assert page_size == 4096, "the 4061-byte in-page limit assumes this"
        assert secure_delete == 1, "ON, not the FAST default (KAMP-748)"
        assert (
            ROW_SIZE < 4061
        ), "payload must be in-page for this test to mean what it says"

    def test_credential_bytes_are_gone_from_the_db_and_wal(
        self, tmp_path: Path, fake_keychain: FakeKeychain
    ) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            _seed_plaintext(index, "bandcamp", _payload())
            index._checkpoint_wal("test seed")
            # Positive control: without this the assertion below passes on any
            # build where nothing was ever written.
            assert _file_hits(db_path) >= 1, "nothing planted; test would be vacuous"

            assert index.heal_plaintext_sessions() == 1

            assert _file_hits(db_path) == 0
        finally:
            index.close()


class TestSchemaAssumptions:
    def test_sessions_table_shape_is_what_the_heal_expects(
        self, tmp_path: Path
    ) -> None:
        """Guards against a future migration renaming out from under the heal."""
        index = LibraryIndex(tmp_path / "library.db")
        try:
            cols = {
                row[1]
                for row in index._conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
        finally:
            index.close()
        assert {"service", "session_json", "updated_at"} <= cols

    def test_no_view_shadows_sessions(self, tmp_path: Path) -> None:
        """A view over sessions would change what the heal's UPDATE touches."""
        index = LibraryIndex(tmp_path / "library.db")
        try:
            views = index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
                " AND sql LIKE '%sessions%'"
            ).fetchall()
        finally:
            index.close()
        assert views == []


class TestConftestStubIsStillInPlace:
    """If the autouse keychain stub ever stops working, say so here.

    The whole point of ``fake_keychain`` is that the real path cannot succeed in
    the suite. Should that change -- a conftest edit, a keyring upgrade -- the
    failure would show up as mysteriously passing tests elsewhere rather than
    as a clear signal, and on a dev machine it would write to the real Login
    Keychain.
    """

    def test_security_framework_entry_points_are_stubbed(self) -> None:
        """Asserted structurally, never by attempting a real write.

        A write probe would be the more direct test, but its failure mode is to
        leave a junk item in the developer's real Login Keychain -- doing the
        dangerous thing in order to detect that the guard against it is gone.
        Checking that the entry points are no longer ctypes function pointers
        detects a broken stub just as well and cannot touch the keychain.
        """
        import ctypes
        import sys

        if sys.platform != "darwin":
            pytest.skip("macos_keychain is macOS-only")
        from kamp_core import macos_keychain

        for name in ("_SecItemAdd", "_SecItemCopyMatching", "_SecItemUpdate"):
            entry = getattr(macos_keychain, name, None)
            if entry is None:
                continue
            assert not isinstance(entry, ctypes._CFuncPtr), (  # type: ignore[attr-defined]
                f"{name} is the live Security.framework call -- conftest's"
                " no_real_keychain fixture is not in effect, so this suite can"
                " read and write the real Login Keychain"
            )


def test_heal_is_not_called_from_the_index_constructor(tmp_path: Path) -> None:
    """Constructing a LibraryIndex must not reach the keychain.

    LibraryIndex is built inside the spawn-mode sync and pipeline subprocess
    workers (syncer.py:69,138,182,910 and pipeline_impl.py:121). A heal in the
    constructor would attempt a keychain write on every sync -- walking the
    SIGKILLed helper each time on a packaged build. Same reasoning that kept
    KAMP-748's VACUUM out of __init__.
    """
    probe = FakeKeychain()
    import kamp_core.library as lib

    original = lib._mac_kc
    lib._mac_kc = probe  # type: ignore[assignment]
    try:
        db_path = tmp_path / "library.db"
        conn = sqlite3.connect(db_path)
        conn.close()
        index = LibraryIndex(db_path)
        index.close()
    finally:
        lib._mac_kc = original  # type: ignore[assignment]

    assert probe.reads == []
    assert probe.writes == []
