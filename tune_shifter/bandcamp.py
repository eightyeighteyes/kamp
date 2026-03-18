"""Bandcamp collection sync and purchase downloader.

Uses the unofficial fancollection API (reverse-engineered from Bandcamp's web app)
and a Playwright-managed browser session for authentication. All endpoints are
undocumented and may change without notice.

Excluded from coverage (see pyproject.toml [tool.coverage.run] omit list) because
all meaningful code paths require a live Playwright-managed Chromium instance and
real Bandcamp credentials. There is no practical way to stub the browser session
at a granularity that would produce reliable unit tests; correctness is verified
through manual QA against a real account.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from .config import BandcampConfig, _state_dir

logger = logging.getLogger(__name__)

_COLLECTION_URL = "https://bandcamp.com/api/fancollection/1/collection_items"
_HIDDEN_URL = "https://bandcamp.com/api/fancollection/1/hidden_items"
_COLLECTION_PAGE_BATCH = 20

# Maps our config format strings to the text shown on Bandcamp's download page.
_FORMAT_LABELS: dict[str, str] = {
    "mp3-v0": "MP3 V0",
    "mp3-320": "MP3 320",
    "flac": "FLAC",
    "aac-hi": "AAC",
    "vorbis": "Ogg Vorbis",
    "alac": "ALAC",
    "wav": "WAV",
    "aiff-lossless": "AIFF",
}


class CookieError(Exception):
    pass


class BandcampAPIError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def mark_collection_synced(
    bc_config: BandcampConfig,
    state_file: Path,
) -> int:
    """Record every item in the Bandcamp collection as already downloaded.

    Fetches the full collection and writes all sale_item_ids to *state_file*
    without downloading anything.  Returns the number of items marked.
    """
    session_file = _ensure_session(bc_config)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(session_file))
        page = context.new_page()
        page.goto(
            f"https://bandcamp.com/{bc_config.username}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        fan_id = _get_fan_id_from_page(page, bc_config.username)
        collection = _fetch_collection(page, fan_id)
        context.close()
        browser.close()

    state = _load_state(state_file)
    newly_marked = 0
    for item in collection:
        key = str(item["sale_item_id"])
        if key not in state:
            state[key] = time.time()
            newly_marked += 1

    _save_state(state_file, state)
    logger.info(
        "Marked %d item(s) as synced (%d already recorded). "
        "Future `sync` runs will only download new purchases.",
        newly_marked,
        len(collection) - newly_marked,
    )
    return newly_marked


def sync_new_purchases(
    bc_config: BandcampConfig,
    staging_dir: Path,
    state_file: Path,
    status_callback: Callable[[str], None] | None = None,
) -> list[Path]:
    """Download any purchases not yet recorded in *state_file* to *staging_dir*.

    Returns a list of paths to the downloaded ZIP files.
    """
    session_file = _ensure_session(bc_config)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(session_file))
        page = context.new_page()
        page.goto(
            f"https://bandcamp.com/{bc_config.username}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        fan_id = _get_fan_id_from_page(page, bc_config.username)
        logger.info("Fetched fan_id=%s for user %r", fan_id, bc_config.username)
        state = _load_state(state_file)
        collection = _fetch_collection(page, fan_id)

        new_items = [
            item for item in collection if str(item["sale_item_id"]) not in state
        ]

        if not new_items:
            context.close()
            browser.close()
            logger.info("No new purchases to download.")
            return []

        logger.info("%d new purchase(s) to download.", len(new_items))

        # Scrape download-page URLs from the collection page DOM.
        new_item_ids = {item["sale_item_id"] for item in new_items}
        download_links = _get_download_links(page, bc_config.username, new_item_ids)
        context.close()
        browser.close()

    staging_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[Path] = []
    for item in new_items:
        dl_url = download_links.get(item["sale_item_id"])
        if not dl_url:
            logger.warning(
                "No download link found on collection page for %r by %r "
                "(sale_item_id=%s) — skipping.",
                item.get("item_title"),
                item.get("band_name"),
                item.get("sale_item_id"),
            )
            continue
        item["redownload_url"] = dl_url
        try:
            if status_callback:
                status_callback(
                    f"{item.get('item_title', '?')} by {item.get('band_name', '?')}"
                )
            path = _download_item(item, bc_config, staging_dir, session_file)
            downloaded.append(path)
            state[str(item["sale_item_id"])] = time.time()
            _save_state(state_file, state)
            logger.info("Downloaded: %s", path.name)
        except Exception as exc:
            logger.error(
                "Failed to download %r by %r: %s",
                item.get("item_title"),
                item.get("band_name"),
                exc,
            )

    return downloaded


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


def _session_file() -> Path:
    return _state_dir() / "bandcamp_session.json"


def _ensure_session(bc_config: BandcampConfig) -> Path:
    """Return a valid Playwright storage_state file path.

    If ``cookie_file`` is configured, synthesize a session from it (escape hatch
    for users managing cookies manually). Otherwise, check for a saved Playwright
    session and validate it; if absent or expired, run the interactive login flow.
    """
    if bc_config.cookie_file:
        return _session_from_cookie_file(bc_config.cookie_file)

    sf = _session_file()
    if sf.exists() and _validate_session(sf):
        return sf

    if sf.exists():
        logger.info("Bandcamp session expired — opening browser to re-authenticate.")
        sf.unlink()
    else:
        logger.info("No Bandcamp session found — opening browser to log in.")

    _run_interactive_login(sf)
    return sf


def _validate_session(session_file: Path) -> bool:
    """Return True if *session_file* represents a live Bandcamp session.

    Two-step check:
    1. Fast cookie inspection — no network round-trip.
    2. Live API probe — a definitive server-side confirmation.
       Network errors are treated optimistically (cookies look valid → True).
    """
    try:
        state = json.loads(session_file.read_text())
    except Exception:
        return False

    # Step 1: check for a non-expired js_logged_in=1 cookie.
    now = time.time()
    cookies: list[dict[str, Any]] = state.get("cookies", [])

    def _cookie_valid(c: dict[str, Any]) -> bool:
        if c.get("name") != "js_logged_in" or c.get("value") != "1":
            return False
        expires = c.get("expires", -1)
        return (
            expires < 0 or expires > now
        )  # expires < 0 means session cookie (never expires)

    logged_in = any(_cookie_valid(c) for c in cookies)
    if not logged_in:
        return False

    # Step 2: confirm with an authenticated API endpoint.
    try:
        import requests as std_requests

        cookie_dict = {c["name"]: c["value"] for c in cookies}
        resp = std_requests.get(
            "https://bandcamp.com/api/fan/2/collection_summary",
            cookies=cookie_dict,
            timeout=10,
            allow_redirects=False,
        )
        if resp.status_code in (401, 403, 302):
            return False
    except Exception:
        pass  # network error — trust the cookie check

    return True


def _run_interactive_login(session_file: Path) -> None:
    """Open a headed Playwright browser for the user to log in, then save the session."""
    print(
        "\n[tune-shifter] Opening browser — please log in to Bandcamp.\n"
        "The window will close automatically once login is detected.\n"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(
            "https://bandcamp.com/login", wait_until="domcontentloaded", timeout=30_000
        )

        # Wait until Bandcamp redirects away from the login page (login completed).
        page.wait_for_url(
            lambda url: "bandcamp.com" in url and "login" not in url,
            timeout=300_000,  # 5 minutes for the user to complete login
        )

        session_file.parent.mkdir(parents=True, exist_ok=True)
        state = context.storage_state()
        session_file.write_text(json.dumps(state))
        os.chmod(session_file, 0o600)
        logger.info("Session saved to %s", session_file)
        context.close()
        browser.close()


def _session_from_cookie_file(cookie_file: Path) -> Path:
    """Build a synthetic Playwright storage_state from a Netscape cookies.txt file.

    Returns a temp file path. This is the escape hatch for users who manage
    cookies manually rather than using the interactive login flow.
    """
    import tempfile

    cookies: list[dict[str, Any]] = []
    try:
        for line in cookie_file.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and "bandcamp.com" in parts[0]:
                try:
                    expires = float(parts[4])
                except ValueError:
                    expires = -1.0
                cookies.append(
                    {
                        "name": parts[5],
                        "value": parts[6],
                        "domain": parts[0],
                        "path": parts[2],
                        "expires": expires,
                        "httpOnly": False,
                        "secure": parts[3].upper() == "TRUE",
                    }
                )
    except OSError as exc:
        raise CookieError(f"Could not read cookie file {cookie_file}: {exc}") from exc

    if not cookies:
        raise CookieError(f"No Bandcamp cookies found in {cookie_file}")

    state: dict[str, Any] = {"cookies": cookies, "origins": []}
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="tune-shifter-session-")
    tf = Path(tmp)
    os.close(fd)
    tf.write_text(json.dumps(state))
    os.chmod(tf, 0o600)
    return tf


# ---------------------------------------------------------------------------
# Fan ID and collection
# ---------------------------------------------------------------------------


def _get_fan_id_from_page(page: Any, username: str) -> int:
    url = f"https://bandcamp.com/{username}"
    blob = _extract_pagedata(page.content(), url)
    fan_id: int = blob["fan_data"]["fan_id"]
    return fan_id


def _extract_pagedata(html: str, url: str) -> dict[str, Any]:
    match = re.search(r'id="pagedata"[^>]*data-blob="([^"]+)"', html)
    if not match:
        raise BandcampAPIError(f"Could not find pagedata blob in {url}")
    return json.loads(html_lib.unescape(match.group(1)))


def _fetch_collection(page: Any, fan_id: int) -> list[dict[str, Any]]:
    """Fetch all collection items (visible + hidden), deduplicated by sale_item_id."""
    seen: set[int] = set()
    items: list[dict[str, Any]] = []
    for endpoint in (_COLLECTION_URL, _HIDDEN_URL):
        for item in _paginate(page, endpoint, fan_id):
            item_id: int = item["sale_item_id"]
            if item_id not in seen:
                seen.add(item_id)
                items.append(item)
    return items


def _get_download_links(
    page: Any,
    username: str,
    item_ids: set[int],
) -> dict[int, str]:
    """Navigate to the fan collection page and scrape download-page URLs for *item_ids*.

    Scrolls incrementally until all requested IDs are found or the page is
    exhausted.  Returns a dict mapping sale_item_id → download-page URL.
    Items absent from the page (e.g. hidden or removed) are omitted.
    """
    page.goto(
        f"https://bandcamp.com/{username}/",
        wait_until="networkidle",
        timeout=30_000,
    )
    found: dict[int, str] = {}
    prev_height = 0

    while True:
        links: dict[int, str] = page.evaluate("""() => {
                const out = {};
                document.querySelectorAll(
                    'a[href*="bandcamp.com/download?"][href*="sitem_id="]'
                ).forEach(a => {
                    const m = a.href.match(/sitem_id=(\\d+)/);
                    if (m) out[parseInt(m[1])] = a.href;
                });
                return out;
            }""")
        for raw_id, url in links.items():
            iid = int(raw_id)
            if iid in item_ids:
                found[iid] = url

        if set(found.keys()) >= item_ids:
            break  # found every item we need

        height: int = page.evaluate("() => document.body.scrollHeight")
        if height == prev_height:
            break  # bottom reached, no more items will load
        prev_height = height
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)

    return found


def _paginate(page: Any, endpoint: str, fan_id: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    older_than_token = f"{int(time.time())}:0:a::"

    while True:
        payload = {
            "fan_id": fan_id,
            "count": _COLLECTION_PAGE_BATCH,
            "older_than_token": older_than_token,
        }
        result: dict[str, Any] = page.evaluate(
            """async ([url, body]) => {
                const r = await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                return await r.json();
            }""",
            [endpoint, payload],
        )

        if result.get("error"):
            raise BandcampAPIError(
                f"Collection API error from {endpoint}: {result.get('error')}"
            )

        page_items: list[dict[str, Any]] = result.get("items", [])
        items.extend(page_items)

        if len(page_items) < _COLLECTION_PAGE_BATCH:
            break
        older_than_token = result.get("last_token", "")
        if not older_than_token:
            break

    return items


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _download_item(
    item: dict[str, Any],
    bc_config: BandcampConfig,
    staging_dir: Path,
    session_file: Path,
) -> Path:
    band_name: str = item.get("band_name", "Unknown Artist")
    item_title: str = item.get("item_title", "Unknown Album")
    sale_item_id: int = item["sale_item_id"]
    redownload_url: str | None = item.get("redownload_url")

    if not redownload_url:
        raise BandcampAPIError(
            f"No redownload URL for item {sale_item_id} "
            f"({item_title!r} by {band_name!r})"
        )

    safe_name = re.sub(r'[<>:"/\\|?*]', "_", f"{band_name} - {item_title}")
    dest = staging_dir / f"{safe_name}.zip"

    logger.info("Downloading %r by %r…", item_title, band_name)
    _browser_download(redownload_url, bc_config.format, session_file, dest)
    return dest


def _browser_download(
    redownload_url: str,
    fmt: str,
    session_file: Path,
    dest: Path,
) -> None:
    """Navigate the Bandcamp download page via headless browser and save the ZIP.

    The download page presents a ``<select>`` for format choice and a Knockout.js-
    bound ``<a>`` link that becomes visible once the server generates the CDN URL.
    """
    fmt_label = _FORMAT_LABELS.get(fmt, fmt.upper())

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(session_file),
            accept_downloads=True,
        )
        page = context.new_page()

        logger.debug("Browser: download page %s", redownload_url)
        page.goto(redownload_url, wait_until="domcontentloaded", timeout=30_000)

        # Select the desired format from the <select> dropdown (if present).
        # Options contain the file size too (e.g. "MP3 V0 - 80.5MB"), so match
        # by checking whether the option text starts with our format label.
        if page.query_selector("select"):
            page.evaluate(
                """([label]) => {
                    const sel = document.querySelector('select');
                    if (!sel) return;
                    const upper = label.toUpperCase();
                    for (const opt of sel.options) {
                        if (opt.text.toUpperCase().includes(upper)) {
                            opt.selected = true;
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            break;
                        }
                    }
                }""",
                [fmt_label],
            )

        # Wait for the Knockout binding to resolve and the CDN link to appear.
        download_link = page.wait_for_selector(
            'a[href*="bcbits.com/download"]',
            state="visible",
            timeout=15_000,
        )
        if download_link is None:
            context.close()
            browser.close()
            raise BandcampAPIError(
                f"Download link for format {fmt!r} ({fmt_label!r}) "
                f"never became ready on {redownload_url}"
            )

        with page.expect_download() as dl_info:
            download_link.click()
        download = dl_info.value

        temp_path = download.path()
        if temp_path is None:
            context.close()
            browser.close()
            raise BandcampAPIError(f"Download did not complete for {redownload_url}")

        shutil.move(str(temp_path), str(dest))
        context.close()
        browser.close()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def _load_state(state_file: Path) -> dict[str, float]:
    if not state_file.exists():
        return {}
    try:
        return dict(json.loads(state_file.read_text()))
    except Exception:
        logger.warning("Could not read state file %s — starting fresh.", state_file)
        return {}


def _save_state(state_file: Path, state: dict[str, float]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2))
