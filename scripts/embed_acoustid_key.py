#!/usr/bin/env python3
"""Patch kamp_daemon/acoustid.py with the encoded AcoustID key/salt at build time.

Reads ACOUSTID_KEY and ACOUSTID_SALT from the environment, encodes them via
the same XOR scheme as scripts/encode_acoustid_key.py, and rewrites the
two ``b""`` placeholder lines in-place.

Used by both the macOS and Windows release jobs in build-app.yml so the
embedding logic is identical across platforms (replacing the previous
sed/PowerShell-specific shell snippets).

Usage:
    ACOUSTID_KEY=... ACOUSTID_SALT=... python scripts/embed_acoustid_key.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Reuse the existing encoder so the byte-literal format matches exactly.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from encode_acoustid_key import encode  # noqa: E402

_TARGET = _HERE.parent / "kamp_daemon" / "acoustid.py"
_KEY_LINE = re.compile(r'^_KEY:\s*bytes\s*=\s*b""\s*$', re.MULTILINE)
_SALT_LINE = re.compile(r'^_SALT:\s*bytes\s*=\s*b""\s*$', re.MULTILINE)


def main() -> int:
    key = os.environ.get("ACOUSTID_KEY", "")
    salt = os.environ.get("ACOUSTID_SALT", "")
    if not key or not salt:
        print(
            "ACOUSTID_KEY and ACOUSTID_SALT must be set in the environment",
            file=sys.stderr,
        )
        return 1

    encoded_key, encoded_salt = encode(key, salt)
    src = _TARGET.read_text(encoding="utf-8")

    # Use callback-form subn so backslash escapes in the replacement string
    # (the encoded byte literals contain \xNN sequences) are passed through
    # literally instead of being interpreted as regex backreferences.
    key_repl = f"_KEY: bytes = {encoded_key}"
    salt_repl = f"_SALT: bytes = {encoded_salt}"
    new_src, key_count = _KEY_LINE.subn(lambda _m: key_repl, src, count=1)
    new_src, salt_count = _SALT_LINE.subn(lambda _m: salt_repl, new_src, count=1)

    # Fail loudly if the placeholder lines moved — silent no-op patches in
    # CI would ship a build with no API key and only surface as runtime
    # AcoustID lookup failures.
    if key_count != 1 or salt_count != 1:
        print(
            f"Failed to locate placeholder lines in {_TARGET} "
            f"(_KEY matches={key_count}, _SALT matches={salt_count})",
            file=sys.stderr,
        )
        return 2

    _TARGET.write_text(new_src, encoding="utf-8")
    print(f"Embedded AcoustID key/salt into {_TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
