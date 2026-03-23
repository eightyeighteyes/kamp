"""AcoustID fingerprinting and lookup.

Provides two functions used by the tagger as Tier 0 identification:
  - fingerprint_file: runs fpcalc to produce a Chromaprint fingerprint
  - lookup_recording_mbids: queries the AcoustID API for recording MBIDs

Both _KEY and _SALT are b"" placeholders in source. CI substitutes them
at build time using secrets/encode_acoustid_key.py so neither the encoded
key nor the XOR salt appears in the public repository.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import requests

# Both placeholders are replaced by CI (scripts/encode_acoustid_key.py)
# before the sdist is built. In dev builds both remain b"", causing
# _api_key() to return "" and lookup_recording_mbids() to return [].
_KEY: bytes = b""
_SALT: bytes = b""


def _api_key() -> str:
    """Return the decoded AcoustID API key, or '' if no key is embedded."""
    if not _KEY or not _SALT:
        return ""
    return bytes(b ^ _SALT[i % len(_SALT)] for i, b in enumerate(_KEY)).decode()


def fingerprint_file(path: Path) -> tuple[float, str] | None:
    """Run fpcalc -json on *path* and return (duration, fingerprint).

    Returns None if fpcalc is not installed or if it fails on the file.
    fpcalc ships as a Homebrew dependency (chromaprint); for dev installs,
    run: brew install chromaprint
    """
    if not shutil.which("fpcalc"):
        return None
    result = subprocess.run(
        ["fpcalc", "-json", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    return float(data["duration"]), data["fingerprint"]


def lookup_recording_mbids(duration: float, fingerprint: str) -> list[str]:
    """Query the AcoustID API and return recording MBIDs.

    Returns an empty list if no key is embedded (dev build) or if the API
    returns no results for this fingerprint.
    """
    key = _api_key()
    if not key:
        return []
    params: dict[str, str | int] = {
        "client": key,
        "meta": "recordingids",
        "duration": int(duration),
        "fingerprint": fingerprint,
    }
    resp = requests.get(
        "https://api.acoustid.org/v2/lookup",
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return [
        rec["id"]
        for result in resp.json().get("results", [])
        for rec in result.get("recordings", [])
    ]
