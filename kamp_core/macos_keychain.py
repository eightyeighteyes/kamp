"""Security.framework wrapper for the macOS Data Protection Keychain.

Stores items in the Data Protection Keychain (kSecUseDataProtectionKeychain)
which uses the app's code-signing identity rather than binary-hash ACLs.
This means keychain items remain accessible after app updates with no dialog,
because the signing identity (team ID + bundle ID) is stable across builds.

Requires the ``keychain-access-groups`` entitlement in the signed binary.
In unsigned dev builds the entitlement is absent; the module detects
``errSecMissingEntitlement`` (-34018) on the first access and falls back to
the Login Keychain (TASK-167 SecItemUpdate behaviour) for the process lifetime.

``KeyringError`` from ``keyring.errors`` is raised on failure so existing
exception-handling in ``library.py`` is unchanged.

This module is macOS-only.  Callers must guard with ``sys.platform``.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import byref, c_int32, c_int64, c_uint32, c_void_p
from ctypes.util import find_library

import keyring.errors

if sys.platform != "darwin":
    raise ImportError("kamp_core.macos_keychain is macOS-only")

_OS_STATUS_SUCCESS = 0
_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
_ERR_SEC_AUTH_FAILED = -25293
_ERR_SEC_MISSING_ENTITLEMENT = -34018


_sec = ctypes.CDLL(find_library("Security"))
_found = ctypes.CDLL(find_library("Foundation"))

# ----- CoreFoundation helpers -----

_CFDictionaryCreate = _found.CFDictionaryCreate
_CFDictionaryCreate.restype = c_void_p
_CFDictionaryCreate.argtypes = (
    c_void_p,
    c_void_p,
    c_void_p,
    c_int32,
    c_void_p,
    c_void_p,
)

_CFStringCreateWithCString = _found.CFStringCreateWithCString
_CFStringCreateWithCString.restype = c_void_p
_CFStringCreateWithCString.argtypes = [c_void_p, c_void_p, c_uint32]

_CFDataCreate = _found.CFDataCreate
_CFDataCreate.restype = c_void_p
_CFDataCreate.argtypes = (c_void_p, c_void_p, c_int64)

_CFDataGetBytePtr = _found.CFDataGetBytePtr
_CFDataGetBytePtr.restype = c_void_p
_CFDataGetBytePtr.argtypes = (c_void_p,)

_CFDataGetLength = _found.CFDataGetLength
_CFDataGetLength.restype = c_int64
_CFDataGetLength.argtypes = (c_void_p,)

# ----- Security.framework functions -----

_SecItemAdd = _sec.SecItemAdd
_SecItemAdd.restype = c_int32
_SecItemAdd.argtypes = (c_void_p, c_void_p)

_SecItemCopyMatching = _sec.SecItemCopyMatching
_SecItemCopyMatching.restype = c_int32
_SecItemCopyMatching.argtypes = (c_void_p, c_void_p)

_SecItemUpdate = _sec.SecItemUpdate
_SecItemUpdate.restype = c_int32
_SecItemUpdate.argtypes = (c_void_p, c_void_p)

_SecItemDelete = _sec.SecItemDelete
_SecItemDelete.restype = c_int32
_SecItemDelete.argtypes = (c_void_p,)

# Set to True at runtime when errSecMissingEntitlement is returned, meaning the
# binary lacks the keychain-access-groups entitlement (unsigned dev builds).
# All subsequent calls skip the DPC path and use the Login Keychain instead.
_dpc_unavailable: bool = False


def _sym(name: str) -> c_void_p:
    """Return a pointer to a Security.framework constant by name."""
    return c_void_p.in_dll(_sec, name)


def _cf_str(s: str) -> c_void_p:
    _kCFStringEncodingUTF8 = 0x08000100
    return c_void_p(
        _CFStringCreateWithCString(None, s.encode("utf-8"), _kCFStringEncodingUTF8)
    )


def _cf_bool(val: bool) -> c_void_p:
    return c_void_p.in_dll(_found, "kCFBooleanTrue" if val else "kCFBooleanFalse")


def _cf_data(s: str) -> c_void_p:
    # kSecValueData requires CFData, not CFString.
    encoded = s.encode("utf-8")
    return c_void_p(_CFDataCreate(None, encoded, len(encoded)))


def _make_dict(**kwargs: object) -> c_void_p:
    """Build a CFDictionary from keyword args.

    Keys whose names start with ``kSec`` are resolved as Security.framework
    constants.  Values that are ``str`` become CFString; ``bool`` become
    CFBoolean; anything else is passed through as a raw ``c_void_p``.
    """
    keys = list(kwargs.keys())
    vals = list(kwargs.values())

    cf_keys: list[c_void_p] = []
    cf_vals: list[c_void_p] = []
    for k, v in zip(keys, vals):
        cf_keys.append(_sym(k))
        if isinstance(v, bool):
            cf_vals.append(_cf_bool(v))
        elif isinstance(v, str):
            if v.startswith("kSec"):
                cf_vals.append(_sym(v))
            else:
                cf_vals.append(_cf_str(v))
        else:
            # Already a ctypes value (e.g. c_void_p from _cf_str); pass through.
            cf_vals.append(v)  # type: ignore[arg-type]

    key_arr = (c_void_p * len(keys))(*cf_keys)
    val_arr = (c_void_p * len(vals))(*cf_vals)
    return c_void_p(
        _CFDictionaryCreate(
            None,
            key_arr,
            val_arr,
            len(keys),
            _found.kCFTypeDictionaryKeyCallBacks,
            _found.kCFTypeDictionaryValueCallBacks,
        )
    )


def _raise_for_status(status: int) -> None:
    if status == _OS_STATUS_SUCCESS:
        return
    if status == _ERR_SEC_INTERACTION_NOT_ALLOWED or status == _ERR_SEC_AUTH_FAILED:
        raise keyring.errors.KeyringLocked(f"Keychain locked (OSStatus {status})")
    raise keyring.errors.KeyringError(f"Security framework error (OSStatus {status})")


def _read_raw(service: str, username: str, use_dpc: bool) -> str | None:
    """Read a raw password string from either the DPC or Login Keychain."""
    q_kwargs: dict[str, object] = dict(
        kSecClass="kSecClassGenericPassword",
        kSecMatchLimit="kSecMatchLimitOne",
        kSecAttrService=service,
        kSecAttrAccount=username,
        kSecReturnData=True,
    )
    if use_dpc:
        q_kwargs["kSecUseDataProtectionKeychain"] = True
    q = _make_dict(**q_kwargs)
    data = c_void_p()
    status = _SecItemCopyMatching(q, byref(data))
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        return None
    _raise_for_status(status)
    return ctypes.string_at(_CFDataGetBytePtr(data), _CFDataGetLength(data)).decode(
        "utf-8"
    )


def _delete_raw(service: str, username: str, use_dpc: bool) -> None:
    """Delete from either the DPC or Login Keychain; silently ignores missing items."""
    q_kwargs: dict[str, object] = dict(
        kSecClass="kSecClassGenericPassword",
        kSecAttrService=service,
        kSecAttrAccount=username,
    )
    if use_dpc:
        q_kwargs["kSecUseDataProtectionKeychain"] = True
    q = _make_dict(**q_kwargs)
    status = _SecItemDelete(q)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        return
    _raise_for_status(status)


def get_password(service: str, username: str) -> str | None:
    """Read a generic password, preferring the Data Protection Keychain.

    Falls back to the Login Keychain when the binary lacks the
    ``keychain-access-groups`` entitlement (unsigned dev builds).
    Returns ``None`` if the item is absent.
    Raises ``keyring.errors.KeyringLocked`` when the keychain is locked.
    """
    global _dpc_unavailable
    if not _dpc_unavailable:
        q = _make_dict(
            kSecClass="kSecClassGenericPassword",
            kSecUseDataProtectionKeychain=True,
            kSecMatchLimit="kSecMatchLimitOne",
            kSecAttrService=service,
            kSecAttrAccount=username,
            kSecReturnData=True,
        )
        data = c_void_p()
        status = _SecItemCopyMatching(q, byref(data))
        if status == _ERR_SEC_MISSING_ENTITLEMENT:
            _dpc_unavailable = True
        elif status == _ERR_SEC_ITEM_NOT_FOUND:
            return None
        elif status == _OS_STATUS_SUCCESS:
            return ctypes.string_at(
                _CFDataGetBytePtr(data), _CFDataGetLength(data)
            ).decode("utf-8")
        else:
            _raise_for_status(status)

    # Login Keychain fallback (unsigned dev builds)
    return _read_raw(service, username, use_dpc=False)


def set_password(service: str, username: str, password: str) -> None:
    """Write a generic password to the Data Protection Keychain.

    Uses ``SecItemUpdate`` when the item already exists to preserve existing
    entries in-place.  Falls through to ``SecItemAdd`` for new items, setting
    ``kSecAttrAccessibleAfterFirstUnlock`` so the daemon can access credentials
    after login without requiring the screen to be unlocked.

    Falls back to the Login Keychain when the entitlement is absent.
    """
    global _dpc_unavailable
    if not _dpc_unavailable:
        find_q = _make_dict(
            kSecClass="kSecClassGenericPassword",
            kSecUseDataProtectionKeychain=True,
            kSecAttrService=service,
            kSecAttrAccount=username,
        )
        update_attrs = _make_dict(kSecValueData=_cf_data(password))
        status = _SecItemUpdate(find_q, update_attrs)

        if status == _ERR_SEC_MISSING_ENTITLEMENT:
            _dpc_unavailable = True
        elif status == _ERR_SEC_ITEM_NOT_FOUND:
            add_q = _make_dict(
                kSecClass="kSecClassGenericPassword",
                kSecUseDataProtectionKeychain=True,
                kSecAttrService=service,
                kSecAttrAccount=username,
                kSecValueData=_cf_data(password),
                kSecAttrAccessible="kSecAttrAccessibleAfterFirstUnlock",
            )
            add_status = _SecItemAdd(add_q, None)
            if add_status == _ERR_SEC_MISSING_ENTITLEMENT:
                _dpc_unavailable = True
            else:
                _raise_for_status(add_status)
                return
        else:
            _raise_for_status(status)
            return

    # Login Keychain fallback (unsigned dev builds)
    find_q = _make_dict(
        kSecClass="kSecClassGenericPassword",
        kSecAttrService=service,
        kSecAttrAccount=username,
    )
    update_attrs = _make_dict(kSecValueData=_cf_data(password))
    status = _SecItemUpdate(find_q, update_attrs)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        add_q = _make_dict(
            kSecClass="kSecClassGenericPassword",
            kSecAttrService=service,
            kSecAttrAccount=username,
            kSecValueData=_cf_data(password),
        )
        status = _SecItemAdd(add_q, None)
    _raise_for_status(status)


def delete_password(service: str, username: str) -> None:
    """Delete a generic password, preferring the Data Protection Keychain.

    Silently ignores the case where the item is absent in either keychain.
    Falls back to the Login Keychain when the entitlement is absent.
    """
    global _dpc_unavailable
    if not _dpc_unavailable:
        q = _make_dict(
            kSecClass="kSecClassGenericPassword",
            kSecUseDataProtectionKeychain=True,
            kSecAttrService=service,
            kSecAttrAccount=username,
        )
        status = _SecItemDelete(q)
        if status == _ERR_SEC_MISSING_ENTITLEMENT:
            _dpc_unavailable = True
        elif status == _ERR_SEC_ITEM_NOT_FOUND:
            # Not in DPC — also check Login Keychain (transition period)
            _delete_raw(service, username, use_dpc=False)
            return
        else:
            _raise_for_status(status)
            return

    # Login Keychain fallback (unsigned dev builds)
    _delete_raw(service, username, use_dpc=False)


def _get_login_keychain_password(service: str, username: str) -> str | None:
    """Read directly from the Login Keychain — used only for DPC migration."""
    return _read_raw(service, username, use_dpc=False)


def _delete_login_keychain_password(service: str, username: str) -> None:
    """Delete directly from the Login Keychain — used only for DPC migration cleanup."""
    _delete_raw(service, username, use_dpc=False)
