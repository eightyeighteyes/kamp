from __future__ import annotations

import ctypes

import keyring.errors
import pytest
from pytest_mock import MockerFixture

# Skips the entire module on non-macOS before any ctypes calls are attempted.
macos_keychain = pytest.importorskip(
    "kamp_core.macos_keychain", reason="macos_keychain is macOS-only"
)

_ERR_SEC_AUTH_FAILED = macos_keychain._ERR_SEC_AUTH_FAILED
_ERR_SEC_INTERACTION_NOT_ALLOWED = macos_keychain._ERR_SEC_INTERACTION_NOT_ALLOWED
_ERR_SEC_ITEM_NOT_FOUND = macos_keychain._ERR_SEC_ITEM_NOT_FOUND
_raise_for_status = macos_keychain._raise_for_status

# ---------------------------------------------------------------------------
# _raise_for_status
# ---------------------------------------------------------------------------


class TestRaiseForStatus:
    def test_success_is_noop(self) -> None:
        _raise_for_status(0)  # must not raise

    def test_interaction_not_allowed_raises_keyring_locked(self) -> None:
        with pytest.raises(keyring.errors.KeyringLocked):
            _raise_for_status(_ERR_SEC_INTERACTION_NOT_ALLOWED)

    def test_auth_failed_raises_keyring_locked(self) -> None:
        with pytest.raises(keyring.errors.KeyringLocked):
            _raise_for_status(_ERR_SEC_AUTH_FAILED)

    def test_unknown_error_raises_keyring_error(self) -> None:
        with pytest.raises(keyring.errors.KeyringError):
            _raise_for_status(-99999)


# ---------------------------------------------------------------------------
# get_password
# ---------------------------------------------------------------------------


class TestGetPassword:
    def test_returns_none_when_item_not_found(self, mocker: MockerFixture) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemCopyMatching", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        assert macos_keychain.get_password("svc", "user") is None

    def test_returns_stored_string_on_success(self, mocker: MockerFixture) -> None:
        mocker.patch.object(macos_keychain, "_SecItemCopyMatching", return_value=0)
        mocker.patch.object(macos_keychain, "_CFDataGetBytePtr", return_value=0x1234)
        mocker.patch.object(macos_keychain, "_CFDataGetLength", return_value=6)
        mocker.patch.object(ctypes, "string_at", return_value=b"secret")

        result = macos_keychain.get_password("svc", "user")
        assert result == "secret"

    def test_raises_keyring_locked_on_interaction_not_allowed(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            macos_keychain,
            "_SecItemCopyMatching",
            return_value=_ERR_SEC_INTERACTION_NOT_ALLOWED,
        )
        with pytest.raises(keyring.errors.KeyringLocked):
            macos_keychain.get_password("svc", "user")

    def test_raises_keyring_locked_on_auth_failed(self, mocker: MockerFixture) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemCopyMatching", return_value=_ERR_SEC_AUTH_FAILED
        )
        with pytest.raises(keyring.errors.KeyringLocked):
            macos_keychain.get_password("svc", "user")

    def test_raises_keyring_error_on_generic_failure(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(macos_keychain, "_SecItemCopyMatching", return_value=-99999)
        with pytest.raises(keyring.errors.KeyringError):
            macos_keychain.get_password("svc", "user")


# ---------------------------------------------------------------------------
# set_password
# ---------------------------------------------------------------------------


class TestSetPassword:
    def test_uses_update_when_item_exists(self, mocker: MockerFixture) -> None:
        update = mocker.patch.object(macos_keychain, "_SecItemUpdate", return_value=0)
        add = mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=0)

        macos_keychain.set_password("svc", "user", "pass")

        update.assert_called_once()
        add.assert_not_called()

    def test_falls_through_to_add_when_item_not_found(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemUpdate", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        add = mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=0)

        macos_keychain.set_password("svc", "user", "pass")

        add.assert_called_once()

    def test_raises_on_add_failure(self, mocker: MockerFixture) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemUpdate", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=-99999)

        with pytest.raises(keyring.errors.KeyringError):
            macos_keychain.set_password("svc", "user", "pass")


# ---------------------------------------------------------------------------
# delete_password
# ---------------------------------------------------------------------------


class TestDeletePassword:
    def test_deletes_existing_item(self, mocker: MockerFixture) -> None:
        delete = mocker.patch.object(macos_keychain, "_SecItemDelete", return_value=0)
        macos_keychain.delete_password("svc", "user")
        delete.assert_called_once()

    def test_silently_ignores_not_found(self, mocker: MockerFixture) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemDelete", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        macos_keychain.delete_password("svc", "user")  # must not raise

    def test_raises_on_other_errors(self, mocker: MockerFixture) -> None:
        mocker.patch.object(macos_keychain, "_SecItemDelete", return_value=-99999)
        with pytest.raises(keyring.errors.KeyringError):
            macos_keychain.delete_password("svc", "user")
