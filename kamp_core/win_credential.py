"""Windows DPAPI wrapper for credential storage.

Windows Credential Manager (the keyring ``WinVaultKeyring`` backend) caps
each credential at 2560 bytes (``CRED_MAX_CREDENTIAL_BLOB_SIZE`` =
``5 * 512``).  The Bandcamp session blob — full cookie jar plus username
and metadata — easily exceeds this, and ``CredWrite`` fails with
``OSError(1783, 'CredWrite', 'The stub received bad data.')`` (KAMP-282).

DPAPI (``CryptProtectData`` / ``CryptUnprotectData``) has no size limit
and ties the encryption key to the current Windows user account, so the
resulting ciphertext can sit safely in the SQLite ``sessions`` table
even though the column is on disk in plaintext-readable form (KAMP-280
AC #3).

Public API:
    protect(plaintext: bytes) -> bytes      # raw encrypt
    unprotect(ciphertext: bytes) -> bytes   # raw decrypt
    protect_str(plaintext: str) -> str      # UTF-8 + base64 + DPAPI prefix
    unprotect_str(text: str) -> str | None  # None when input is not DPAPI

The string variants prefix the ciphertext with ``DPAPI_PREFIX`` so
callers can distinguish encrypted blobs from legacy plaintext payloads
without out-of-band metadata.

This module is Windows-only.  Importers must guard with ``sys.platform``.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes
from typing import Final

if sys.platform != "win32":
    raise ImportError("kamp_core.win_credential is Windows-only")


DPAPI_PREFIX: Final[str] = "dpapi-v1:"


class _DataBlob(ctypes.Structure):
    _fields_ = (
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    )


class DPAPIError(OSError):
    """Raised when a DPAPI operation fails."""


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_CryptProtectData = _crypt32.CryptProtectData
_CryptProtectData.argtypes = (
    ctypes.POINTER(_DataBlob),  # pDataIn
    wintypes.LPCWSTR,  # szDataDescr
    ctypes.POINTER(_DataBlob),  # pOptionalEntropy
    ctypes.c_void_p,  # pvReserved
    ctypes.c_void_p,  # pPromptStruct
    wintypes.DWORD,  # dwFlags
    ctypes.POINTER(_DataBlob),  # pDataOut
)
_CryptProtectData.restype = wintypes.BOOL

_CryptUnprotectData = _crypt32.CryptUnprotectData
_CryptUnprotectData.argtypes = (
    ctypes.POINTER(_DataBlob),
    ctypes.POINTER(wintypes.LPWSTR),  # ppszDataDescr
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DataBlob),
)
_CryptUnprotectData.restype = wintypes.BOOL

_LocalFree = _kernel32.LocalFree
_LocalFree.argtypes = (ctypes.c_void_p,)
_LocalFree.restype = ctypes.c_void_p


def _to_blob(data: bytes) -> _DataBlob:
    buf = (ctypes.c_byte * len(data))(*data)
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def _from_blob(blob: _DataBlob) -> bytes:
    out = ctypes.string_at(blob.pbData, blob.cbData)
    # CryptProtectData / CryptUnprotectData allocate pbData with LocalAlloc;
    # the docs require LocalFree to release it.  Without this we leak the
    # ciphertext (or plaintext) buffer for the lifetime of the process.
    _LocalFree(blob.pbData)
    return out


def protect(plaintext: bytes) -> bytes:
    """Encrypt *plaintext* with DPAPI under the current Windows user.

    Raises :class:`DPAPIError` (an :class:`OSError` subclass) on failure.
    """
    in_blob = _to_blob(plaintext)
    out_blob = _DataBlob()
    if not _CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        err = ctypes.get_last_error()
        raise DPAPIError(err, f"CryptProtectData failed (Win32 {err})")
    return _from_blob(out_blob)


def unprotect(ciphertext: bytes) -> bytes:
    """Decrypt DPAPI ciphertext produced by :func:`protect`.

    Raises :class:`DPAPIError` on failure (e.g. tampered ciphertext or
    a different Windows user account).
    """
    in_blob = _to_blob(ciphertext)
    out_blob = _DataBlob()
    if not _CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        err = ctypes.get_last_error()
        raise DPAPIError(err, f"CryptUnprotectData failed (Win32 {err})")
    return _from_blob(out_blob)


def protect_str(plaintext: str) -> str:
    """UTF-8 encode + DPAPI-encrypt + base64; result is opaque ASCII text.

    The returned string starts with :data:`DPAPI_PREFIX` so callers can
    detect DPAPI-wrapped payloads without out-of-band metadata.
    """
    cipher = protect(plaintext.encode("utf-8"))
    return DPAPI_PREFIX + base64.b64encode(cipher).decode("ascii")


def unprotect_str(text: str) -> str | None:
    """Inverse of :func:`protect_str`.

    Returns ``None`` when *text* does not start with :data:`DPAPI_PREFIX`,
    so callers can transparently handle legacy plaintext rows that
    pre-date the DPAPI rollout.
    """
    if not text.startswith(DPAPI_PREFIX):
        return None
    cipher = base64.b64decode(text[len(DPAPI_PREFIX) :])
    return unprotect(cipher).decode("utf-8")


def is_dpapi_blob(text: str) -> bool:
    """Return True when *text* looks like a value produced by :func:`protect_str`."""
    return text.startswith(DPAPI_PREFIX)
