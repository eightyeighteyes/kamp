"""Windows-only tests for the DPAPI wrapper.

These hit the real ``CryptProtectData`` / ``CryptUnprotectData`` APIs.
On a non-Windows runner the module import fails and the entire file is
skipped via ``pytest.importorskip`` (mirrors ``tests/test_macos_keychain.py``).
"""

from __future__ import annotations

import pytest

# Skips the entire module on non-Windows before any ctypes calls happen.
win_credential = pytest.importorskip(
    "kamp_core.win_credential",
    reason="win_credential is Windows-only",
    exc_type=ImportError,
)


class TestProtectUnprotectRoundtrip:
    def test_short_payload_roundtrips(self) -> None:
        cipher = win_credential.protect(b"hello")
        assert cipher != b"hello"  # actually encrypted
        assert win_credential.unprotect(cipher) == b"hello"

    def test_large_payload_roundtrips(self) -> None:
        # The whole point of DPAPI: blobs much larger than CredWrite's
        # 2560-byte ceiling.  This mimics a real Bandcamp session JSON
        # (cookies + flags) which is the regression KAMP-280 / KAMP-282
        # exists to fix.
        payload = b"x" * 8192
        cipher = win_credential.protect(payload)
        assert win_credential.unprotect(cipher) == payload

    def test_unprotect_rejects_tampered_ciphertext(self) -> None:
        cipher = bytearray(win_credential.protect(b"hello"))
        cipher[-1] ^= 0xFF  # flip a bit in the trailing MAC
        with pytest.raises(win_credential.DPAPIError):
            win_credential.unprotect(bytes(cipher))


class TestStringHelpers:
    def test_protect_str_prefixes_with_marker(self) -> None:
        wrapped = win_credential.protect_str('{"cookies":[]}')
        assert wrapped.startswith(win_credential.DPAPI_PREFIX)
        # Body after the marker is base64 — only ASCII safe chars.
        body = wrapped[len(win_credential.DPAPI_PREFIX) :]
        assert body.isascii()

    def test_protect_str_unprotect_str_roundtrips_unicode(self) -> None:
        plaintext = '{"username":"tedd-é-terry","cookies":["✓"]}'
        wrapped = win_credential.protect_str(plaintext)
        assert win_credential.unprotect_str(wrapped) == plaintext

    def test_unprotect_str_returns_none_for_legacy_plaintext(self) -> None:
        # Pre-DPAPI rows in the sessions table look like JSON.  The helper
        # must report "not DPAPI" so callers can fall back to treating
        # the value as plaintext rather than raising.
        assert win_credential.unprotect_str('{"cookies": []}') is None

    def test_unprotect_str_returns_none_for_empty_string(self) -> None:
        assert win_credential.unprotect_str("") is None

    def test_is_dpapi_blob_true_for_protected(self) -> None:
        assert win_credential.is_dpapi_blob(win_credential.protect_str("x"))

    def test_is_dpapi_blob_false_for_plaintext(self) -> None:
        assert not win_credential.is_dpapi_blob('{"cookies": []}')
