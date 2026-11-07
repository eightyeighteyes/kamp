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
import logging
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

logger = logging.getLogger(__name__)

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

# Set when the helper cannot be executed at all, as opposed to running and
# returning an error. Latched for the process lifetime so we stop paying a
# subprocess spawn that is guaranteed to die, mirroring _dpc_unavailable below.
#
# KAMP-749: this is not hypothetical. `keychain-access-groups` is a *restricted*
# entitlement, and AMFI will not honour it without a matching provisioning
# profile; the app bundle has none, so the kernel SIGKILLs the helper at exec in
# every released build ("Disallowing com.kamp.app because no eligible
# provisioning profiles found", AMFI error -413). Before this latch the packaged
# path returned the helper's failure straight to the caller, and set_session
# then wrote the credential to SQLite in plaintext -- with no second attempt,
# because get_password/set_password delegated to the helper unconditionally.
_helper_unusable: bool = False


class _HelperUnusable(Exception):
    """The helper could not be run — fall through to the ctypes path.

    Distinct from a `keyring.errors.KeyringError`, which means the helper *ran*
    and reported something (not found, locked, internal error). That answer is
    authoritative and must be passed to the caller; this exception means we
    learned nothing and should ask a different mechanism.
    """


# ---------------------------------------------------------------------------
# Helper-based implementation (built .app)
# ---------------------------------------------------------------------------


def _run_helper(
    op: str, service: str, username: str, password: str | None = None
) -> tuple[int, str]:
    """Invoke the helper binary and return (exit_code, stdout).

    Raises :class:`_HelperUnusable` when the helper could not run at all, and
    :class:`keyring.errors.KeyringLocked` when it ran but was still waiting on
    the user. Neither ``subprocess.TimeoutExpired`` nor ``OSError`` is a
    ``keyring.errors`` subclass, so before KAMP-749 both escaped
    ``set_session``'s ``except keyring.errors.KeyringError`` and turned the
    bandcamp login-complete handler into an HTTP 422 — the same class of bug
    KAMP-282 fixed for the Windows branch.
    """
    global _helper_unusable
    assert _helper_path is not None
    try:
        result = subprocess.run(
            [_helper_path, op, service, username],
            input=(password + "\n").encode("utf-8") if password is not None else None,
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        # Reading an *existing* Login Keychain item from a binary the item's ACL
        # does not trust raises a modal keychain prompt, which blocks for the
        # full timeout when nobody answers it. The store is reachable and the
        # answer is simply pending, so this is Locked rather than unusable — and
        # deliberately does not latch, because the next call may well be
        # answered. get_session already retries KeyringLocked with backoff.
        raise keyring.errors.KeyringLocked(
            f"kamp-keychain-helper {op} timed out after 10s"
            " (a keychain prompt is probably waiting for the user)"
        ) from exc
    except OSError as exc:
        # Cannot exec at all: missing, wrong architecture (Errno 86 "Bad CPU
        # type" when a Rosetta process spawns a native arm64 child), or not
        # executable. Nothing about the keychain was learned.
        _helper_unusable = True
        raise _HelperUnusable(f"cannot exec kamp-keychain-helper: {exc}") from exc

    if result.returncode < 0:
        # Killed by a signal. -9 (SIGKILL) is what AMFI does when a restricted
        # entitlement is unauthorised; the process never ran, so this says
        # nothing about the credential.
        _helper_unusable = True
        raise _HelperUnusable(
            f"kamp-keychain-helper {op} killed by signal {-result.returncode}"
            " (restricted entitlement not authorised — see KAMP-753)"
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


def _use_helper() -> bool:
    """True when the helper is present and has not already proved unrunnable."""
    return _helper_path is not None and not _helper_unusable


def _note_fallback(op: str, exc: Exception) -> None:
    """Record that the helper could not run and the ctypes path is taking over.

    Logged at WARNING unconditionally rather than behind a diagnostics gate: the
    whole reason KAMP-749 went unnoticed for months is that a credential quietly
    took a different path than intended and nothing said so.
    """
    logger.warning(
        "macos_keychain: helper unusable for %s (%s); falling back to the"
        " Login Keychain via ctypes. The Data Protection Keychain is"
        " unavailable until a provisioning profile authorises it (KAMP-753).",
        op,
        exc,
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


def _make_dict(**kwargs: str | bool | c_void_p) -> c_void_p:
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
            cf_vals.append(v)

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
    q_kwargs: dict[str, str | bool | c_void_p] = dict(
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
    q_kwargs: dict[str, str | bool | c_void_p] = dict(
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
    if _use_helper():
        try:
            return _helper_get(service, username, login=False)
        except _HelperUnusable as exc:
            _note_fallback("get", exc)
            # Fall through to ctypes below rather than reporting absence: the
            # helper never ran, so it learned nothing about this credential.

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
            pass  # not in DPC; fall through to Login Keychain check below
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
    if _use_helper():
        try:
            return _helper_set(service, username, password)
        except _HelperUnusable as exc:
            _note_fallback("set", exc)
            # Fall through: writing via ctypes puts the credential in the Login
            # Keychain, which is the whole point — the alternative is the
            # caller storing it in SQLite as plaintext.

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
    if _use_helper():
        try:
            _helper_delete(service, username, login=False)
            _helper_delete(service, username, login=True)
            return
        except _HelperUnusable as exc:
            _note_fallback("delete", exc)
            # Fall through, so a disconnect still removes the Login Keychain
            # copy. Without this a credential the ctypes path had written
            # survived every attempt to delete it from the packaged app.

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
    if _use_helper():
        try:
            return _helper_get(service, username, login=True)
        except _HelperUnusable as exc:
            _note_fallback("get_login", exc)
    return _read_raw(service, username, use_dpc=False)


def _delete_login_keychain_password(service: str, username: str) -> None:
    """Delete directly from the Login Keychain — used only for DPC migration cleanup."""
    if _use_helper():
        try:
            _helper_delete(service, username, login=True)
            return
        except _HelperUnusable as exc:
            _note_fallback("delete_login", exc)
    _delete_raw(service, username, use_dpc=False)
