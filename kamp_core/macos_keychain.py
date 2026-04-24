"""Security.framework wrapper for the macOS Data Protection Keychain.

In the built .app, all keychain operations are delegated to the
kamp-keychain-helper binary, which has a bound Info.plist
(CFBundleIdentifier = com.kamp.app) and the keychain-access-groups
entitlement. Items are stored in the Data Protection Keychain using the
code-signing identity (team ID + bundle ID), which is stable across app
updates — no keychain dialog after upgrading.

In dev / unsigned builds the helper is absent. The module falls back to
direct Security.framework calls via ctypes. Those calls return
errSecMissingEntitlement (-34018) because unsigned binaries lack the
entitlement; the module detects this, sets _dpc_unavailable = True, and
falls back to the Login Keychain for the rest of the process lifetime.

This module is macOS-only. Callers must guard with sys.platform.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import byref, c_int32, c_int64, c_uint32, c_void_p
from ctypes.util import find_library
from pathlib import Path

import keyring.errors

if sys.platform != "darwin":
    raise ImportError("kamp_core.macos_keychain is macOS-only")

# ---------------------------------------------------------------------------
# Helper binary discovery
# ---------------------------------------------------------------------------

_OS_STATUS_SUCCESS = 0
_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
_ERR_SEC_AUTH_FAILED = -25293
_ERR_SEC_MISSING_ENTITLEMENT = -34018

# Helper exit codes (mirrors the Swift implementation).
_HELPER_EXIT_OK = 0
_HELPER_EXIT_NOT_FOUND = 1
_HELPER_EXIT_LOCKED = 2
_HELPER_EXIT_ERROR = 3


def _find_helper() -> str | None:
    """Return the path to kamp-keychain-helper, or None if not bundled.

    In the built .app, the helper sits at Contents/Resources/kamp-keychain-helper
    (one level above the kamp onedir bundle). sys.executable inside a frozen
    PyInstaller binary points to the kamp executable inside the onedir directory,
    so parent.parent resolves to Contents/Resources/.
    """
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).parent.parent / "kamp-keychain-helper"
    return str(candidate) if candidate.exists() else None


# Resolved once at import time. None → use ctypes fallback.
_helper_path: str | None = _find_helper()

# ---------------------------------------------------------------------------
# Helper-based implementation (built .app)
# ---------------------------------------------------------------------------


def _run_helper(
    op: str, service: str, username: str, password: str | None = None
) -> tuple[int, str]:
    """Invoke the helper binary and return (exit_code, stdout)."""
    result = subprocess.run(
        [_helper_path, op, service, username],
        input=(password + "\n").encode("utf-8") if password is not None else None,
        capture_output=True,
        timeout=10,
    )
    return result.returncode, result.stdout.decode("utf-8")


def _helper_get(service: str, username: str, *, login: bool) -> str | None:
    op = "get_login" if login else "get_dpc"
    code, output = _run_helper(op, service, username)
    if code == _HELPER_EXIT_OK:
        return output
    if code == _HELPER_EXIT_NOT_FOUND:
        return None
    if code == _HELPER_EXIT_LOCKED:
        raise keyring.errors.KeyringLocked(f"Keychain locked ({op})")
    raise keyring.errors.KeyringError(f"kamp-keychain-helper {op} failed (exit {code})")


def _helper_set(service: str, username: str, password: str) -> None:
    code, _ = _run_helper("set_dpc", service, username, password)
    if code == _HELPER_EXIT_OK:
        return
    if code == _HELPER_EXIT_LOCKED:
        raise keyring.errors.KeyringLocked("Keychain locked (set_dpc)")
    raise keyring.errors.KeyringError(
        f"kamp-keychain-helper set_dpc failed (exit {code})"
    )


def _helper_delete(service: str, username: str, *, login: bool) -> None:
    op = "delete_login" if login else "delete_dpc"
    code, _ = _run_helper(op, service, username)
    if code in (_HELPER_EXIT_OK, _HELPER_EXIT_NOT_FOUND):
        return
    if code == _HELPER_EXIT_LOCKED:
        raise keyring.errors.KeyringLocked(f"Keychain locked ({op})")
    raise keyring.errors.KeyringError(f"kamp-keychain-helper {op} failed (exit {code})")


# ---------------------------------------------------------------------------
# ctypes fallback (dev / unsigned builds)
# ---------------------------------------------------------------------------

_sec = ctypes.CDLL(find_library("Security"))
_found = ctypes.CDLL(find_library("Foundation"))

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

# Set to True when errSecMissingEntitlement is returned, meaning the binary
# lacks keychain-access-groups (dev / unsigned builds). All subsequent calls
# skip the DPC path and use the Login Keychain instead.
_dpc_unavailable: bool = False


def _sym(name: str) -> c_void_p:
    return c_void_p.in_dll(_sec, name)


def _cf_str(s: str) -> c_void_p:
    _kCFStringEncodingUTF8 = 0x08000100
    return c_void_p(
        _CFStringCreateWithCString(None, s.encode("utf-8"), _kCFStringEncodingUTF8)
    )


def _cf_bool(val: bool) -> c_void_p:
    return c_void_p.in_dll(_found, "kCFBooleanTrue" if val else "kCFBooleanFalse")


def _cf_data(s: str) -> c_void_p:
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_password(service: str, username: str) -> str | None:
    """Read a generic password, preferring the Data Protection Keychain.

    In the built .app delegates to kamp-keychain-helper (DPC).
    In dev falls back to ctypes and then Login Keychain.
    Returns ``None`` if the item is absent.
    Raises ``keyring.errors.KeyringLocked`` when the keychain is locked.
    """
    if _helper_path is not None:
        return _helper_get(service, username, login=False)

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

    return _read_raw(service, username, use_dpc=False)


def set_password(service: str, username: str, password: str) -> None:
    """Write a generic password to the Data Protection Keychain.

    In the built .app delegates to kamp-keychain-helper (DPC).
    In dev falls back to ctypes / Login Keychain.
    """
    if _helper_path is not None:
        return _helper_set(service, username, password)

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

    In the built .app delegates to kamp-keychain-helper (tries DPC then Login KC).
    In dev falls back to ctypes / Login Keychain.
    Silently ignores the case where the item is absent in either keychain.
    """
    if _helper_path is not None:
        _helper_delete(service, username, login=False)
        _helper_delete(service, username, login=True)
        return

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
            _delete_raw(service, username, use_dpc=False)
            return
        else:
            _raise_for_status(status)
            return

    _delete_raw(service, username, use_dpc=False)


def _get_login_keychain_password(service: str, username: str) -> str | None:
    """Read directly from the Login Keychain — used only for DPC migration."""
    if _helper_path is not None:
        return _helper_get(service, username, login=True)
    return _read_raw(service, username, use_dpc=False)


def _delete_login_keychain_password(service: str, username: str) -> None:
    """Delete directly from the Login Keychain — used only for DPC migration cleanup."""
    if _helper_path is not None:
        _helper_delete(service, username, login=True)
        return
    _delete_raw(service, username, use_dpc=False)
