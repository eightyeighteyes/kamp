"""Shared pytest fixtures.

Currently one job: keep the test suite away from the developer's real macOS
keychain.
"""

from __future__ import annotations

from typing import Iterator

import pytest

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
