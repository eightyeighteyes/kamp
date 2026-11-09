"""Probe which stored Bandcamp cookies authentication actually needs (KAMP-746).

Reads the stored session straight from the keychain -- deliberately NOT via
LibraryIndex, whose constructor runs migrations against the production DB
(KAMP-737) -- and replays four legs with different cookie subsets:

  summary   GET  collection_summary         (the fan_id/username probe)
  items     POST collection_items, count=1  (the collection walk)
  page      GET  the item's redownload page (yields the signed CDN URL)
  cdn       GET  popplers5, first bytes only (the album download)

A leg passes when it returns what the daemon would accept: a fan_id, a
non-empty items page carrying a redownload_url, pagedata with download URLs,
and a ZIP/audio magic number respectively.  Only cookie *names* and statuses
are printed, never values.

collection_items is the endpoint Bandcamp rate-limits hardest (KAMP-637), so
calls are spaced and the run stops at the first 429.

Usage:  poetry run python scripts/probe_bandcamp_cookies.py
macOS dev only (reads the Login Keychain; may show an access prompt).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from kamp_core import macos_keychain
from kamp_daemon.bandcamp import (
    _COLLECTION_SUMMARY_URL,
    _COLLECTION_URL,
    _cookie_session,
    _extract_pagedata,
)

# Spacing between collection_items POSTs.  Three walks a minute earns a 429;
# a count=1 POST is far cheaper than a walk, but the limiter counts requests.
_ITEMS_SPACING_S = 20.0
_FORMAT = "mp3-320"

_TRACKERS = {"_ga", "_fbp", "_rdt_uuid", "cookie_preferences", "cart_client_id"}


class RateLimited(Exception):
    pass


def _is_tracker(name: str) -> bool:
    return name in _TRACKERS or name.startswith("_ga_")


def _subset(session: dict[str, Any], names: set[str]) -> dict[str, Any]:
    return {**session, "cookies": [c for c in session["cookies"] if c["name"] in names]}


def _run_legs(
    session_data: dict[str, Any], last_items_call: list[float]
) -> dict[str, str]:
    """Run the four legs; return leg -> verdict.  A failed leg skips the rest."""
    s = _cookie_session(session_data)
    out: dict[str, str] = {}

    r = s.get(_COLLECTION_SUMMARY_URL, timeout=30, allow_redirects=False)
    fan_id = None
    if r.status_code == 200:
        try:
            fan_id = r.json().get("fan_id")
        except ValueError:
            pass
    out["summary"] = f"ok" if fan_id else f"FAIL http={r.status_code}"
    if r.status_code == 429:
        raise RateLimited("collection_summary")
    if not fan_id:
        return out

    wait = _ITEMS_SPACING_S - (time.monotonic() - last_items_call[0])
    if wait > 0:
        time.sleep(wait)
    r = s.post(
        _COLLECTION_URL,
        json={
            "fan_id": fan_id,
            "count": 1,
            "older_than_token": f"{int(time.time())}:0:a::",
        },
        timeout=30,
        allow_redirects=False,
    )
    last_items_call[0] = time.monotonic()
    if r.status_code == 429:
        raise RateLimited("collection_items")
    redownload_url = None
    if r.status_code == 200:
        body = r.json()
        items = body.get("items") or []
        urls = body.get("redownload_urls") or {}
        if items:
            it = items[0]
            redownload_url = urls.get(
                f"{it.get('sale_item_type', 'p')}{it.get('sale_item_id')}"
            )
        out["items"] = (
            "ok" if redownload_url else f"FAIL items={len(items)} urls={len(urls)}"
        )
    else:
        out["items"] = f"FAIL http={r.status_code}"
    if not redownload_url:
        return out

    r = s.get(redownload_url, timeout=30, allow_redirects=False)
    cdn_url = None
    if r.status_code == 200:
        try:
            blob = _extract_pagedata(r.text, redownload_url)
            dl = (blob.get("download_items") or blob.get("digital_items") or [{}])[0]
            cdn_url = ((dl.get("downloads") or {}).get(_FORMAT) or {}).get("url")
        except Exception as exc:  # noqa: BLE001 -- report, don't crash the probe
            out["page"] = f"FAIL {type(exc).__name__}"
            return out
    out["page"] = "ok" if cdn_url else f"FAIL http={r.status_code}"
    if not cdn_url:
        return out

    with s.get(cdn_url, stream=True, timeout=60, allow_redirects=True) as r:
        magic = r.raw.read(4) if r.status_code == 200 else b""
        ct = r.headers.get("Content-Type", "")
        final = r.url.split("?")[0]
    good = magic.startswith(b"PK") or magic[:3] == b"ID3" or magic[:4] == b"fLaC"
    out["cdn"] = "ok" if good else f"FAIL http={r.status_code} ct={ct!r} final={final}"
    return out


def main() -> int:
    raw = macos_keychain.get_password("kamp", "bandcamp")
    if raw is None:
        print("No bandcamp session in the keychain.")
        return 1
    session = json.loads(raw)
    names = [c["name"] for c in session.get("cookies", [])]
    print(f"stored cookies ({len(names)}): {sorted(names)}")
    print(f"non-cookie keys: {sorted(k for k in session if k != 'cookies')}")

    candidates = {n for n in names if not _is_tracker(n)}
    sets: list[tuple[str, set[str]]] = [
        ("full jar (control)", set(names)),
        ("candidates (no trackers)", candidates),
    ]
    for n in sorted(candidates):
        sets.append((f"candidates - {n}", candidates - {n}))
    sets.append(("identity only", {"identity"} & candidates))

    last_items_call = [0.0]
    for label, subset in sets:
        try:
            verdict = _run_legs(_subset(session, subset), last_items_call)
        except RateLimited as exc:
            print(f"429 on {exc} -- stopping.")
            return 2
        legs = "  ".join(f"{k}={v}" for k, v in verdict.items())
        print(f"{label:<32} {legs}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
