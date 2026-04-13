#!/usr/bin/env python3
"""Phase-0 spike: validate that Bandcamp sync can work without Playwright.

Run this with a valid bandcamp_session.json (or a cookie_file) to confirm:
  1. Fan ID can be extracted from the profile page via plain HTTP.
  2. Collection items can be fetched via the fancollection API.
  3. Download links can be scraped from the collection page HTML.
  4. The CDN download URL is available in the download-page pagedata JSON blob.

Usage:
    poetry run python scripts/spike_bandcamp_http.py <bandcamp_username>
    poetry run python scripts/spike_bandcamp_http.py <bandcamp_username> --cookie-file ~/Downloads/cookies.txt

The script prints what it finds at each step and exits non-zero if any
critical check fails.  No files are downloaded or modified.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("Install requests: poetry add requests")


# ---------------------------------------------------------------------------
# Session loading (mirrors kamp_daemon/bandcamp.py helpers)
# ---------------------------------------------------------------------------


def _load_session_cookies(session_file: Path) -> dict[str, str]:
    """Return a flat name→value cookie dict from a Playwright storage_state file."""
    state = json.loads(session_file.read_text())
    return {c["name"]: c["value"] for c in state.get("cookies", [])}


def _load_netscape_cookies(cookie_file: Path) -> dict[str, str]:
    """Parse a Netscape cookies.txt file and return Bandcamp cookies."""
    cookies: dict[str, str] = {}
    for line in cookie_file.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and "bandcamp.com" in parts[0]:
            cookies[parts[5]] = parts[6]
    return cookies


def _make_session(cookie_dict: dict[str, str]) -> requests.Session:
    s = requests.Session()
    s.cookies.update(cookie_dict)
    # Match a realistic browser user-agent so Bandcamp doesn't 403 us.
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    return s


# ---------------------------------------------------------------------------
# Extraction helpers (mirrors what the new bandcamp.py will do)
# ---------------------------------------------------------------------------


def _extract_pagedata(html: str, url: str) -> dict[str, Any]:
    match = re.search(r'id="pagedata"[^>]*data-blob="([^"]+)"', html)
    if not match:
        return {}
    return json.loads(html_lib.unescape(match.group(1)))


def _get_fan_id(username: str, session: requests.Session) -> int | None:
    url = f"https://bandcamp.com/{username}"
    print(f"\n[1] GET {url}")
    resp = session.get(url, timeout=20, allow_redirects=True)
    print(f"    status={resp.status_code}  content-length={len(resp.text)}")
    blob = _extract_pagedata(resp.text, url)
    if not blob:
        print("    ✗ pagedata blob not found in profile page HTML")
        return None
    fan_id = blob.get("fan_data", {}).get("fan_id")
    if fan_id:
        print(f"    ✓ fan_id={fan_id}")
    else:
        print("    ✗ fan_id not found in pagedata blob")
        print("      blob keys:", list(blob.keys()))
    return fan_id


def _fetch_collection(
    fan_id: int, session: requests.Session, limit: int = 5
) -> list[dict[str, Any]]:
    endpoint = "https://bandcamp.com/api/fancollection/1/collection_items"
    print(f"\n[2] POST {endpoint}  (first {limit} items)")
    payload = {
        "fan_id": fan_id,
        "count": limit,
        "older_than_token": f"{int(time.time())}:0:a::",
    }
    resp = session.post(
        endpoint,
        json=payload,
        timeout=20,
        headers={"Content-Type": "application/json"},
    )
    print(f"    status={resp.status_code}")
    if resp.status_code != 200:
        print(f"    ✗ unexpected status: {resp.text[:200]}")
        return []
    data = resp.json()
    items = data.get("items", [])
    print(f"    ✓ {len(items)} item(s) returned")
    for item in items[:3]:
        print(
            f"      sale_item_id={item.get('sale_item_id')}  "
            f"{item.get('band_name')!r} — {item.get('item_title')!r}"
        )
    return items


def _get_download_links(username: str, session: requests.Session) -> dict[int, str]:
    url = f"https://bandcamp.com/{username}/"
    print(f"\n[3] GET {url}  (collection page for download links)")
    resp = session.get(url, timeout=20)
    print(f"    status={resp.status_code}  content-length={len(resp.text)}")
    # Same CSS selector as the Playwright code: a[href*="bandcamp.com/download?"][href*="sitem_id="]
    pattern = re.compile(
        r'href="(https://[^"]*bandcamp\.com/download\?[^"]*sitem_id=(\d+)[^"]*)"'
    )
    links: dict[int, str] = {}
    for match in pattern.finditer(resp.text):
        links[int(match.group(2))] = html_lib.unescape(match.group(1))
    print(f"    ✓ found {len(links)} download link(s) in page HTML")
    for sid, href in list(links.items())[:3]:
        print(f"      sale_item_id={sid}  {href[:80]}…")
    return links


def _check_download_page(redownload_url: str, session: requests.Session) -> bool:
    """The critical spike check: does the download page pagedata contain CDN URLs?"""
    print(f"\n[4] GET {redownload_url}")
    print("    (checking whether CDN URL is in pagedata JSON — the spike question)")
    resp = session.get(redownload_url, timeout=20, allow_redirects=True)
    print(f"    status={resp.status_code}  content-length={len(resp.text)}")

    blob = _extract_pagedata(resp.text, redownload_url)
    if not blob:
        print("    ✗ no pagedata blob on download page")
        print("      first 500 chars:", resp.text[:500])
        return False

    print(f"    pagedata top-level keys: {list(blob.keys())}")

    # Dump the whole blob so the user can inspect it for CDN URLs.
    blob_path = Path("/tmp/kamp_spike_download_pagedata.json")
    blob_path.write_text(json.dumps(blob, indent=2))
    print(f"    pagedata JSON written to {blob_path} for inspection")

    # Look for anything that smells like a CDN download URL.
    blob_str = json.dumps(blob)
    cdn_hits = re.findall(r'"(https://[^"]*bcbits\.com/download[^"]*)"', blob_str)
    if cdn_hits:
        print(f"    ✓ FOUND {len(cdn_hits)} bcbits.com/download URL(s) in pagedata!")
        for u in cdn_hits[:3]:
            print(f"      {u[:100]}")
        return True
    else:
        print("    ✗ no bcbits.com/download URLs found in pagedata")
        # Look for format-related keys to understand the structure.
        for key in ("download_url", "url", "formats", "digital_formats", "tralbum_id"):
            if key in blob_str:
                print(
                    f"      hint: key {key!r} appears in blob — inspect the JSON file"
                )
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Spike: Bandcamp HTTP-only sync validation"
    )
    parser.add_argument("username", help="Bandcamp username")
    parser.add_argument("--cookie-file", type=Path, help="Netscape cookies.txt path")
    parser.add_argument(
        "--session-file",
        type=Path,
        default=None,
        help="Playwright storage_state JSON (default: ~/.local/share/kamp/bandcamp_session.json)",
    )
    args = parser.parse_args()

    # --- Load cookies ---
    if args.cookie_file:
        cookie_dict = _load_netscape_cookies(args.cookie_file)
        print(f"Using cookie file: {args.cookie_file}  ({len(cookie_dict)} cookies)")
    else:
        if args.session_file:
            sf = args.session_file
        else:
            # Try the default kamp state dir.
            from kamp_daemon.config import _state_dir

            sf = _state_dir() / "bandcamp_session.json"
        if not sf.exists():
            sys.exit(
                f"Session file not found: {sf}\n"
                "Provide --cookie-file or --session-file, or log in first with `kamp sync`."
            )
        cookie_dict = _load_session_cookies(sf)
        print(f"Using session file: {sf}  ({len(cookie_dict)} cookies)")

    session = _make_session(cookie_dict)

    # --- Step 1: fan_id ---
    fan_id = _get_fan_id(args.username, session)
    if fan_id is None:
        print("\n✗ SPIKE FAILED at step 1 (fan_id)")
        return 1

    # --- Step 2: collection ---
    items = _fetch_collection(fan_id, session)
    if not items:
        print("\n✗ SPIKE FAILED at step 2 (collection fetch)")
        return 1

    # --- Step 3: download links ---
    links = _get_download_links(args.username, session)
    if not links:
        print(
            "\n  (no download links found — collection page may be empty or session expired)"
        )

    # --- Step 4: CDN URL check (the critical unknown) ---
    # Use the first item that has a matching download link.
    redownload_url: str | None = None
    for item in items:
        sid = item.get("sale_item_id")
        if sid and sid in links:
            redownload_url = links[sid]
            break

    if redownload_url is None and links:
        # Fallback: use any link even if it's not in our small sample.
        redownload_url = next(iter(links.values()))

    if redownload_url:
        cdn_found = _check_download_page(redownload_url, session)
    else:
        print("\n[4] SKIPPED — no download links available to test")
        cdn_found = False

    # --- Summary ---
    print("\n" + "=" * 60)
    print("SPIKE SUMMARY")
    print("=" * 60)
    print(f"  fan_id extraction (requests GET + pagedata):  ✓")
    print(f"  collection fetch (requests POST):             ✓")
    print(
        f"  download links (HTML regex on collection pg): {'✓' if links else '✗ (no items?)'}"
    )
    print(
        f"  CDN URL in download-page pagedata:            {'✓ PROCEED' if cdn_found else '✗ NEEDS INVESTIGATION'}"
    )
    if not cdn_found and redownload_url:
        print(f"\n  Inspect /tmp/kamp_spike_download_pagedata.json to understand")
        print(f"  the download page structure and find an alternative CDN URL path.")
    print()
    return 0 if cdn_found else 2


if __name__ == "__main__":
    sys.exit(main())
