from __future__ import annotations

import ctypes

import keyring.errors
import pytest
from pytest_mock import MockerFixture

# Skips the entire module on non-macOS before any ctypes calls are attempted.
macos_keychain = pytest.importorskip(
    "kamp_core.macos_keychain",
    reason="macos_keychain is macOS-only",
    exc_type=ImportError,
)

_ERR_SEC_AUTH_FAILED = macos_keychain._ERR_SEC_AUTH_FAILED
_ERR_SEC_INTERACTION_NOT_ALLOWED = macos_keychain._ERR_SEC_INTERACTION_NOT_ALLOWED
_ERR_SEC_ITEM_NOT_FOUND = macos_keychain._ERR_SEC_ITEM_NOT_FOUND
_ERR_SEC_MISSING_ENTITLEMENT = macos_keychain._ERR_SEC_MISSING_ENTITLEMENT
_raise_for_status = macos_keychain._raise_for_status


@pytest.fixture(autouse=True)
def reset_dpc_flag() -> None:
    """Reset the DPC availability flag before and after each test."""
    macos_keychain._dpc_unavailable = False
    yield
    macos_keychain._dpc_unavailable = False


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

    def test_missing_entitlement_raises_keyring_error(self) -> None:
        with pytest.raises(keyring.errors.KeyringError):
            _raise_for_status(_ERR_SEC_MISSING_ENTITLEMENT)


# ---------------------------------------------------------------------------
# get_password — Data Protection Keychain path
# ---------------------------------------------------------------------------


class TestGetPassword:
    def test_returns_none_when_item_not_found_in_dpc(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemCopyMatching", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        assert macos_keychain.get_password("svc", "user") is None

    def test_returns_stored_string_on_dpc_hit(self, mocker: MockerFixture) -> None:
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
# get_password — missing entitlement / Login Keychain fallback
# ---------------------------------------------------------------------------


class TestGetPasswordEntitlementFallback:
    def test_missing_entitlement_sets_dpc_unavailable(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            macos_keychain,
            "_SecItemCopyMatching",
            side_effect=[_ERR_SEC_MISSING_ENTITLEMENT, _ERR_SEC_ITEM_NOT_FOUND],
        )
        macos_keychain.get_password("svc", "user")
        assert macos_keychain._dpc_unavailable is True

    def test_falls_back_to_login_keychain_on_missing_entitlement(
        self, mocker: MockerFixture
    ) -> None:
        # First call: DPC returns missing entitlement
        # Second call: Login Keychain returns success
        mocker.patch.object(
            macos_keychain,
            "_SecItemCopyMatching",
            side_effect=[_ERR_SEC_MISSING_ENTITLEMENT, 0],
        )
        mocker.patch.object(macos_keychain, "_CFDataGetBytePtr", return_value=0x1234)
        mocker.patch.object(macos_keychain, "_CFDataGetLength", return_value=5)
        mocker.patch.object(ctypes, "string_at", return_value=b"token")

        result = macos_keychain.get_password("svc", "user")
        assert result == "token"

    def test_skips_dpc_when_already_unavailable(self, mocker: MockerFixture) -> None:
        macos_keychain._dpc_unavailable = True
        copy_matching = mocker.patch.object(
            macos_keychain, "_SecItemCopyMatching", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        macos_keychain.get_password("svc", "user")
        # Only one call — Login Keychain directly, no DPC attempt
        assert copy_matching.call_count == 1


# ---------------------------------------------------------------------------
# set_password — Data Protection Keychain path
# ---------------------------------------------------------------------------


class TestSetPassword:
    def test_uses_update_when_item_exists_in_dpc(self, mocker: MockerFixture) -> None:
        update = mocker.patch.object(macos_keychain, "_SecItemUpdate", return_value=0)
        add = mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=0)

        macos_keychain.set_password("svc", "user", "pass")

        update.assert_called_once()
        add.assert_not_called()

    def test_falls_through_to_add_when_item_not_found_in_dpc(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemUpdate", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        add = mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=0)

        macos_keychain.set_password("svc", "user", "pass")

        add.assert_called_once()

    def test_add_includes_kSecAttrAccessible_for_dpc(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemUpdate", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=0)
        make_dict = mocker.patch.object(
            macos_keychain, "_make_dict", wraps=macos_keychain._make_dict
        )

        macos_keychain.set_password("svc", "user", "pass")

        all_kwargs = {k for call in make_dict.call_args_list for k in call.kwargs}
        assert "kSecAttrAccessible" in all_kwargs

    def test_value_data_uses_cfdata_not_cfstring(self, mocker: MockerFixture) -> None:
        """kSecValueData must be CFData. Passing CFString returns errSecParam (-50)."""
        mocker.patch.object(macos_keychain, "_SecItemUpdate", return_value=0)
        cf_data_spy = mocker.patch.object(
            macos_keychain, "_cf_data", wraps=macos_keychain._cf_data
        )
        cf_str_spy = mocker.patch.object(
            macos_keychain, "_cf_str", wraps=macos_keychain._cf_str
        )

        macos_keychain.set_password("svc", "user", "mypassword")

        cf_data_spy.assert_called_once_with("mypassword")
        cf_str_calls = [call.args[0] for call in cf_str_spy.call_args_list]
        assert "mypassword" not in cf_str_calls

    def test_raises_on_add_failure(self, mocker: MockerFixture) -> None:
        mocker.patch.object(
            macos_keychain, "_SecItemUpdate", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=-99999)

        with pytest.raises(keyring.errors.KeyringError):
            macos_keychain.set_password("svc", "user", "pass")


# ---------------------------------------------------------------------------
# set_password — missing entitlement / Login Keychain fallback
# ---------------------------------------------------------------------------


class TestSetPasswordEntitlementFallback:
    def test_missing_entitlement_on_update_falls_back_to_login_keychain(
        self, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(
            macos_keychain,
            "_SecItemUpdate",
            side_effect=[_ERR_SEC_MISSING_ENTITLEMENT, 0],
        )
        mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=0)

        macos_keychain.set_password("svc", "user", "pass")
        assert macos_keychain._dpc_unavailable is True

    def test_login_keychain_fallback_omits_kSecAttrAccessible(
        self, mocker: MockerFixture
    ) -> None:
        """Login Keychain SecItemAdd must not include kSecAttrAccessible."""
        macos_keychain._dpc_unavailable = True
        mocker.patch.object(
            macos_keychain, "_SecItemUpdate", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        mocker.patch.object(macos_keychain, "_SecItemAdd", return_value=0)
        make_dict = mocker.patch.object(
            macos_keychain, "_make_dict", wraps=macos_keychain._make_dict
        )

        macos_keychain.set_password("svc", "user", "pass")

        all_kwargs = {k for call in make_dict.call_args_list for k in call.kwargs}
        assert "kSecAttrAccessible" not in all_kwargs
        assert "kSecUseDataProtectionKeychain" not in all_kwargs


# ---------------------------------------------------------------------------
# delete_password
# ---------------------------------------------------------------------------


class TestDeletePassword:
    def test_deletes_existing_item_from_dpc(self, mocker: MockerFixture) -> None:
        delete = mocker.patch.object(macos_keychain, "_SecItemDelete", return_value=0)
        macos_keychain.delete_password("svc", "user")
        delete.assert_called_once()

    def test_not_in_dpc_also_checks_login_keychain(self, mocker: MockerFixture) -> None:
        # DPC: not found → fall through; Login Keychain: not found → silently ignored
        mocker.patch.object(
            macos_keychain,
            "_SecItemDelete",
            side_effect=[_ERR_SEC_ITEM_NOT_FOUND, _ERR_SEC_ITEM_NOT_FOUND],
        )
        macos_keychain.delete_password("svc", "user")  # must not raise

    def test_raises_on_dpc_error(self, mocker: MockerFixture) -> None:
        mocker.patch.object(macos_keychain, "_SecItemDelete", return_value=-99999)
        with pytest.raises(keyring.errors.KeyringError):
            macos_keychain.delete_password("svc", "user")

    def test_login_keychain_fallback_when_dpc_unavailable(
        self, mocker: MockerFixture
    ) -> None:
        macos_keychain._dpc_unavailable = True
        delete = mocker.patch.object(
            macos_keychain, "_SecItemDelete", return_value=_ERR_SEC_ITEM_NOT_FOUND
        )
        macos_keychain.delete_password("svc", "user")
        assert delete.call_count == 1  # only Login Keychain, no DPC attempt
