"""Security.framework wrapper for the macOS Login Keychain.

The Python ``keyring`` library stores items in the macOS Login Keychain but
uses ``SecItemDelete`` + ``SecItemAdd`` for every write.  This wipes the
item's ACL — including any "Always Allow" the user granted — so the dialog
reappears on the next keychain access.

This module replaces that with ``SecItemUpdate`` (update-in-place) for
existing items, which preserves ACL entries so "Always Allow" survives
session refreshes.  New items are created with ``SecItemAdd`` as usual.

This module is macOS-only.  Callers must guard with ``sys.platform``.
``KeyringError`` from ``keyring.errors`` is raised on failure so existing
exception-handling in ``library.py`` is unchanged.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import byref, c_int32, c_uint32, c_void_p
from ctypes.util import find_library

import keyring.errors

if sys.platform != "darwin":
    raise ImportError("kamp_core.macos_keychain is macOS-only")

_OS_STATUS_SUCCESS = 0
_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
_ERR_SEC_AUTH_FAILED = -25293


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

_CFNumberCreate = _found.CFNumberCreate
_CFNumberCreate.restype = c_void_p
_CFNumberCreate.argtypes = [c_void_p, c_uint32, c_void_p]

_CFDataGetBytePtr = _found.CFDataGetBytePtr
_CFDataGetBytePtr.restype = c_void_p
_CFDataGetBytePtr.argtypes = (c_void_p,)

_CFDataGetLength = _found.CFDataGetLength
_CFDataGetLength.restype = c_int32
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


def _sym(name: str) -> c_void_p:
    """Return a pointer to a Security.framework constant by name."""
    return c_void_p.in_dll(_sec, name)


def _cf_str(s: str) -> c_void_p:
    _kCFStringEncodingUTF8 = 0x08000100
    return c_void_p(
        _CFStringCreateWithCString(None, s.encode("utf-8"), _kCFStringEncodingUTF8)
    )


def _cf_bool(val: bool) -> c_void_p:
    # Boolean constants live in CoreFoundation
    return c_void_p.in_dll(_found, "kCFBooleanTrue" if val else "kCFBooleanFalse")


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


def get_password(service: str, username: str) -> str | None:
    """Read a generic password from the Login Keychain.

    Returns the stored string, or ``None`` if the item is absent.
    Raises ``keyring.errors.KeyringLocked`` when the keychain is locked and
    ``keyring.errors.KeyringError`` for other Security framework failures.
    """
    q = _make_dict(
        kSecClass="kSecClassGenericPassword",
        kSecMatchLimit="kSecMatchLimitOne",
        kSecAttrService=service,
        kSecAttrAccount=username,
        kSecReturnData=True,
    )
    data = c_void_p()
    status = _SecItemCopyMatching(q, byref(data))
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        return None
    _raise_for_status(status)
    return ctypes.string_at(_CFDataGetBytePtr(data), _CFDataGetLength(data)).decode(
        "utf-8"
    )


def set_password(service: str, username: str, password: str) -> None:
    """Write a generic password to the Login Keychain.

    Uses ``SecItemUpdate`` when the item already exists so that item ACL
    entries (including any "Always Allow" the user granted) are preserved.
    Falls through to ``SecItemAdd`` for new items.
    """
    find_q = _make_dict(
        kSecClass="kSecClassGenericPassword",
        kSecAttrService=service,
        kSecAttrAccount=username,
    )
    update_attrs = _make_dict(kSecValueData=_cf_str(password))
    status = _SecItemUpdate(find_q, update_attrs)

    if status == _ERR_SEC_ITEM_NOT_FOUND:
        add_q = _make_dict(
            kSecClass="kSecClassGenericPassword",
            kSecAttrService=service,
            kSecAttrAccount=username,
            kSecValueData=_cf_str(password),
            kSecAttrAccessible="kSecAttrAccessibleWhenUnlocked",
        )
        status = _SecItemAdd(add_q, None)

    _raise_for_status(status)


def delete_password(service: str, username: str) -> None:
    """Delete a generic password from the Login Keychain.

    Silently ignores the case where the item is absent.
    """
    q = _make_dict(
        kSecClass="kSecClassGenericPassword",
        kSecAttrService=service,
        kSecAttrAccount=username,
    )
    status = _SecItemDelete(q)
    if status == _ERR_SEC_ITEM_NOT_FOUND:
        return
    _raise_for_status(status)
