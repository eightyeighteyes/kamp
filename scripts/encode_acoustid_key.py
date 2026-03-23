#!/usr/bin/env python3
"""Encode the AcoustID API key and salt for embedding in acoustid.py.

Usage:
    python scripts/encode_acoustid_key.py <plaintext_key> <plaintext_salt>

Prints two lines:
    Line 1: Python bytes literal for _KEY  (XOR of key with salt)
    Line 2: Python bytes literal for _SALT (salt as hex-escaped bytes)

The CI release workflow pipes these into sed to patch acoustid.py before
building the sdist. Both _KEY and _SALT are b"" placeholders in source so
neither the encoded key nor the XOR salt is visible in the public repo.
"""

import sys


def _bytes_literal(data: bytes) -> str:
    """Return a Python bytes literal with all bytes hex-escaped."""
    inner = "".join(f"\\x{b:02x}" for b in data)
    return f'b"{inner}"'


def encode(key: str, salt: str) -> tuple[str, str]:
    salt_bytes = salt.encode()
    key_bytes = key.encode()
    encoded = bytes(
        b ^ salt_bytes[i % len(salt_bytes)] for i, b in enumerate(key_bytes)
    )
    return _bytes_literal(encoded), _bytes_literal(salt_bytes)


if __name__ == "__main__":
    if len(sys.argv) != 3 or not sys.argv[1] or not sys.argv[2]:
        print("Usage: encode_acoustid_key.py <key> <salt>", file=sys.stderr)
        sys.exit(1)
    encoded_key, encoded_salt = encode(sys.argv[1], sys.argv[2])
    print(encoded_key)
    print(encoded_salt)
