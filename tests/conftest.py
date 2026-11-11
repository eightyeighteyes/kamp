"""Shared pytest fixtures.

Two jobs: keep the test suite away from the developer's real macOS keychain,
and give every test a credential store that actually works.
"""

from __future__ import annotations

from typing import Iterator

import keyring
import keyring.errors
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    """A keyring backend that round-trips, held in a dict for one test.

    keyring ships no in-memory backend, so this is the minimal one: the three
    calls ``LibraryIndex`` makes, with keyring's own "missing item" semantics
    (``None`` on read, ``PasswordDeleteError`` on delete).
    """

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.store[(service, username)]
        except KeyError:
            raise keyring.errors.PasswordDeleteError(username) from None


@pytest.fixture(autouse=True)
def working_secret_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryKeyring]:
    """Route every incidental ``set_session`` through a store that works.

    Before KAMP-750 the suite ran entirely on the plaintext fallback: the
    fixture below makes the real macOS keychain answer not-found, Linux CI has
    only keyring's ``fail`` backend, so ``set_session`` "succeeded" by writing
    plaintext into the DB. Now that it fails closed, that environment would
    make every login, sync and config test raise ``CredentialStorageError``.

    Both platform references in ``library`` are nulled so the keyring branch
    serves all three CI OSes identically. ``_mac_kc`` is deliberately NOT a
    fake by default: ``heal_plaintext_sessions`` switches on whenever it is
    non-None. Tests exercising the macOS or Windows branch patch
    ``kamp_core.library._mac_kc`` / ``_win_cred`` themselves, which takes
    precedence (mocker undoes its patches before this fixture tears down).

    Do NOT set ``KAMP_ALLOW_PLAINTEXT_CREDENTIALS`` here: that would put the
    default path out of reach of every test, recreating what KAMP-750 removed.
    """
    from kamp_core import library

    monkeypatch.setattr(library, "_mac_kc", None)
    monkeypatch.setattr(library, "_win_cred", None)
    monkeypatch.delenv("KAMP_ALLOW_PLAINTEXT_CREDENTIALS", raising=False)
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


# Security.framework entry points in kamp_core.macos_keychain. Patching at this
# level — rather than at get_password/set_password — leaves the module's own
# branch logic (DPC vs Login Keychain, status-code handling, the timeout
# wrapper) under test; only the syscall at the bottom is replaced.
_SECURITY_ENTRY_POINTS = (
    "_SecItemAdd",
    "_SecItemCopyMatching",
    "_SecItemUpdate",
    "_SecItemDelete",
)


@pytest.fixture(autouse=True)
def no_real_keychain(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make the real macOS keychain answer "no such item" to every test.

    Before KAMP-749, nothing in a released build ever reached the Login
    Keychain, so these calls hit an *empty* store and returned
    errSecItemNotFound silently. Now that credentials actually land there, any
    test that reads one gets a modal SecurityAgent prompt instead: Login
    Keychain ACLs are binary-hash based (TASK-167), and the item the packaged
    app writes trusts that app's binary, not the dev interpreter running
    pytest. 17 tests reach this code via `get_session` -> `get_password`
    (test_main, test_syncer, test_diagnostics), none of them deliberately —
    they just build a Config or a Syncer.

    Unguarded that is three separate problems: the suite blocks on a dialog
    (bounded to 10s per call since KAMP-749, so it stalls rather than hangs),
    it asks the developer to hand their real Bandcamp credential to a test, and
    the result depends on whether they clicked Allow at some point in the past.

    errSecItemNotFound is the honest stand-in: it is what dev genuinely saw
    before, and it keeps every caller on the DB-fallback path the tests were
    written against. A test that wants keychain behaviour patches a higher-level
    seam (`_read_raw`, `get_password`) in its own body, which takes precedence
    over this fixture.
    """
    try:
        from kamp_core import macos_keychain
    except Exception:
        # Not macOS, or Security.framework unavailable — nothing to guard.
        yield
        return

    not_found = macos_keychain._ERR_SEC_ITEM_NOT_FOUND

    def _absent(*_args: object, **_kwargs: object) -> int:
        return not_found

    for name in _SECURITY_ENTRY_POINTS:
        if hasattr(macos_keychain, name):
            monkeypatch.setattr(macos_keychain, name, _absent)

    yield
