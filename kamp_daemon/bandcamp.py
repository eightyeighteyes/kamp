"""Bandcamp collection sync and purchase downloader.

Uses the unofficial fancollection API (reverse-engineered from Bandcamp's web app)
and plain HTTP requests for all operations.  All API endpoints are undocumented
and may change without notice.

Authentication
--------------
Login is handled externally: the kamp Electron app opens a BrowserWindow for the
user to authenticate, writes the resulting cookies to ``bandcamp_session.json`` in
the kamp state directory, and the daemon picks them up here.  ``_ensure_session``
reads that file; it no longer launches a browser itself.  If no valid session exists,
``NeedsLoginError`` is raised and the caller is responsible for triggering the Electron
login flow (e.g. via the menu bar "Login" item).

Download URL discovery
----------------------
Bandcamp pre-generates signed CDN download URLs for every format and embeds them as
JSON in the ``<div id="pagedata">`` blob on the download page.  We read:

    ``pagedata["download_items"][0]["downloads"][enc]["url"]``

to obtain the direct download URL without any JavaScript execution.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import sys
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

import requests as _requests

from .config import BandcampConfig

if TYPE_CHECKING:
    from kamp_core.library import LibraryIndex

logger = logging.getLogger(__name__)

_COLLECTION_URL = "https://bandcamp.com/api/fancollection/1/collection_items"
_HIDDEN_URL = "https://bandcamp.com/api/fancollection/1/hidden_items"
_COLLECTION_PAGE_BATCH = 20

# Realistic browser User-Agent — Bandcamp rejects obvious bot strings.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# URL of the kamp server's Bandcamp HTTP proxy relay endpoint.
_PROXY_FETCH_URL = "http://127.0.0.1:8000/api/v1/bandcamp/proxy-fetch"


def _is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle.

    PyInstaller sets sys.frozen = True in the bundled executable.  We use this
    to switch from direct requests (dev) to the Electron proxy relay (built app)
    so that bandcamp.com traffic goes through Chromium's TLS stack instead of
    PyInstaller's bundled OpenSSL, which Cloudflare flags.
    """
    return bool(getattr(sys, "frozen", False))


class _ProxyResponse:
    """Minimal requests.Response lookalike backed by a proxy-fetch result.

    Returned by _ProxySession so call sites in bandcamp.py work without
    modification regardless of whether we are in dev or bundled mode.
    """

    def __init__(self, status_code: int, text: str, content_type: str) -> None:
        self.status_code = status_code
        self.text = text
        self.content_type = content_type
        self.ok = 200 <= status_code < 300

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise _requests.HTTPError(f"HTTP {self.status_code}")


class _ProxySession:
    """Drop-in replacement for requests.Session that routes via Electron's net module.

    Used when running inside the PyInstaller bundle, where the bundled OpenSSL
    has a different TLS fingerprint (JA3/JA4) that Cloudflare flags for
    bandcamp.com requests.  Requests are forwarded to the local kamp server's
    proxy-fetch endpoint; Electron picks them up, executes them via net.fetch
    (Chromium network stack, which holds cf_clearance), and posts results back.

    Only GET and POST are needed by bandcamp.py.  The ``stream`` and
    ``allow_redirects`` kwargs accepted by requests.Session are silently
    consumed — Electron follows redirects automatically.
    """

    def __init__(self) -> None:
        self.headers: dict[str, str] = {"User-Agent": _UA}
        # cookies is not used — Electron's session.defaultSession holds them.
        self.cookies: Any = None

    def _fetch(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        timeout: int | float = 30,
        **_kwargs: Any,
    ) -> _ProxyResponse:
        req_headers = dict(self.headers)
        if headers:
            req_headers.update(headers)
        body: str | None = None
        if json is not None:
            req_headers["Content-Type"] = "application/json"
            body = _json_dumps(json)

        # The proxy relay chain adds significant latency: the request goes
        # subprocess → server → WS → Electron → net.fetch(bandcamp.com) → back.
        # net.fetch on a real Bandcamp API call can take 10–20s on a slow
        # connection, so the outer timeout must be considerably larger than the
        # inner one.  2× + 10s gives ≥50s headroom for a 20s inner timeout.
        proxy_timeout = float(timeout) * 2 + 10
        resp = _requests.post(
            _PROXY_FETCH_URL,
            json={"url": url, "method": method, "headers": req_headers, "body": body},
            timeout=proxy_timeout,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return _ProxyResponse(
            status_code=data["status"],
            text=data["body"],
            content_type=data.get("content_type", "text/html"),
        )

    def get(self, url: str, **kwargs: Any) -> _ProxyResponse:
        return self._fetch("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _ProxyResponse:
        return self._fetch("POST", url, **kwargs)


# Alias json.dumps to avoid shadowing in _ProxySession._fetch
_json_dumps = json.dumps

# Union type accepted by all internal helpers that take an HTTP session.
_AnySession = Union[_requests.Session, "_ProxySession"]


class CookieError(Exception):
    pass


class BandcampAPIError(Exception):
    pass


class NeedsLoginError(Exception):
    """Raised when no valid Bandcamp session exists.

    The caller should trigger the Electron BrowserWindow login flow and retry.
    """


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def mark_collection_synced(
    bc_config: BandcampConfig,
    state_file: Path,
    index: "LibraryIndex",
) -> int:
    """Record every item in the Bandcamp collection as already downloaded.

    Fetches the full collection and writes all sale_item_ids to *state_file*
    without downloading anything.  Returns the number of items marked.
    """
    session_data = _ensure_session(bc_config, index)
    session = _make_requests_session(session_data)
    fan_id, username = _get_fan_info(session)
    if not username:
        username = _username_from_logout_cookie(session_data.get("cookies", []))
    _store_username_in_session(username, session_data, index)
    collection = _fetch_collection(fan_id, session, index)

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
    watch_dir: Path,
    state_file: Path,
    index: "LibraryIndex",
    status_callback: Callable[[str], None] | None = None,
) -> list[Path]:
    """Download any purchases not yet recorded in *state_file* to *watch_dir*.

    Returns a list of paths to the downloaded ZIP files.
    """
    session_data = _ensure_session(bc_config, index)
    session = _make_requests_session(session_data)
    fan_id, username = _get_fan_info(session)
    if not username:
        username = _username_from_logout_cookie(session_data.get("cookies", []))
    _store_username_in_session(username, session_data, index)
    logger.info("Fetched fan_id=%s for user %r", fan_id, username)

    state = _load_state(state_file)
    collection = _fetch_collection(fan_id, session, index)

    new_items = [item for item in collection if str(item["sale_item_id"]) not in state]

    if not new_items:
        logger.info("No new purchases to download.")
        return []

    logger.info("%d new purchase(s) to download.", len(new_items))

    # Scrape download-page URLs from the collection page HTML.
    new_item_ids = {item["sale_item_id"] for item in new_items}
    download_links = _get_download_links(username, new_item_ids, session)

    watch_dir.mkdir(parents=True, exist_ok=True)

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
            path = _download_item(item, bc_config, watch_dir, session)
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


def _ensure_session(bc_config: BandcampConfig, index: "LibraryIndex") -> dict[str, Any]:
    """Return valid session data for the Bandcamp account.

    Reads from the DB and validates; if absent or expired, raises
    ``NeedsLoginError`` so the caller can trigger the Electron BrowserWindow
    login flow.
    """
    data = index.get_session("bandcamp")
    if data is not None and _validate_session(data):
        return data

    if data is not None:
        logger.info("Bandcamp session expired — clearing session and prompting login.")
        index.clear_session("bandcamp")
    else:
        # get_session returns None for two distinct reasons: credentials are
        # genuinely absent, OR the keychain was unreachable (already logged as
        # WARNING by get_session). Either way, prompt for login.
        logger.info("No Bandcamp session available — login required.")

    raise NeedsLoginError(
        "No valid Bandcamp session. "
        "Click Login in the kamp menu bar to authenticate."
    )


def _make_requests_session(
    session_data: dict[str, Any],
) -> Union[_requests.Session, "_ProxySession"]:
    """Build an HTTP session authenticated with cookies from *session_data*.

    In the PyInstaller bundle (``sys.frozen`` is True), returns a ``_ProxySession``
    that routes all requests through the local kamp server, which forwards them
    via Electron's net module (Chromium TLS, real browser fingerprint).  In dev,
    returns a normal ``requests.Session`` with the cookies loaded directly.
    """
    if _is_frozen():
        # In the bundled app, Electron's session.defaultSession holds the
        # Bandcamp cookies (set during the interactive login flow) and
        # _ProxySession routes requests through Electron's net module which
        # automatically attaches them.
        return _ProxySession()

    session = _requests.Session()
    session.headers["User-Agent"] = _UA
    for cookie in session_data.get("cookies", []):
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain", ".bandcamp.com"),
            path=cookie.get("path", "/"),
        )
    return session


def _validate_session(session_data: dict[str, Any]) -> bool:
    """Return True if *session_data* represents a live Bandcamp session.

    Two-step check:
    1. Fast cookie inspection — no network round-trip.
    2. Live API probe — a definitive server-side confirmation.
       Network errors are treated optimistically (cookies look valid → True).
    """
    # Step 1: check for a non-expired js_logged_in=1 cookie.
    now = time.time()
    cookies: list[dict[str, Any]] = session_data.get("cookies", [])

    def _cookie_valid(c: dict[str, Any]) -> bool:
        if c.get("name") != "js_logged_in" or c.get("value") != "1":
            return False
        expires: float = float(c.get("expires", -1))
        return (
            expires < 0 or expires > now
        )  # expires < 0 means session cookie (never expires)

    logged_in = any(_cookie_valid(c) for c in cookies)
    if not logged_in:
        return False

    # Step 2: confirm with an authenticated API endpoint.
    try:
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        resp = _requests.get(
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


def _session_from_cookie_file(cookie_file: Path) -> dict[str, Any]:
    """Build session data from a Netscape cookies.txt file.

    Returns the session dict directly.  This is the escape hatch for users who
    manage cookies manually rather than using the interactive login flow.
    """
    cookies: list[dict[str, Any]] = []
    try:
        for line in cookie_file.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and (
                parts[0] == "bandcamp.com" or parts[0].endswith(".bandcamp.com")
            ):
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

    return {"cookies": cookies, "origins": []}


# ---------------------------------------------------------------------------
# Fan ID and collection
# ---------------------------------------------------------------------------


_COLLECTION_SUMMARY_URL = "https://bandcamp.com/api/fan/2/collection_summary"


def _get_fan_info(session: _AnySession) -> tuple[int, str]:
    """Return (fan_id, username) for the authenticated session.

    Uses the authenticated collection_summary API endpoint rather than scraping
    the profile page HTML.  The profile page is served behind Cloudflare's bot
    detection and returns a JS challenge to non-browser TLS fingerprints; the
    API endpoint is not subject to the same check because it requires valid
    session cookies to return a 200.
    """
    resp = session.get(_COLLECTION_SUMMARY_URL, timeout=20, allow_redirects=False)
    if resp.status_code in (401, 403, 302):
        raise NeedsLoginError(
            f"Bandcamp session rejected ({resp.status_code}) — please log in again."
        )
    resp.raise_for_status()
    try:
        data: dict[str, Any] = resp.json()
    except Exception as exc:
        logger.error(
            "_get_fan_info: JSON decode failed — status=%s response_head=%r",
            resp.status_code,
            resp.text[:500],
        )
        raise BandcampAPIError(f"collection_summary returned non-JSON: {exc}") from exc
    fan_id: int = data["fan_id"]
    # Bandcamp moved the username out of the top-level "username" key; fall
    # back to url_hints.subdomain (the URL slug used in collection page URLs).
    username: str = data.get("username", "") or data.get("url_hints", {}).get(
        "subdomain", ""
    )
    return fan_id, username


def _username_from_logout_cookie(cookies: list[dict[str, Any]]) -> str:
    """Extract username from the Bandcamp ``logout`` cookie.

    Bandcamp's ``logout`` cookie value is URL-encoded JSON: ``{"username":"…"}``.
    This is a reliable, zero-network-request source of the username that is
    always present right after login.  Used as a fallback when the API does not
    return the username field.
    """
    for c in cookies:
        if c.get("name") == "logout":
            try:
                payload = json.loads(urllib.parse.unquote(c["value"]))
                return str(payload.get("username", ""))
            except Exception:
                pass
    return ""


def _store_username_in_session(
    username: str,
    session_data: dict[str, Any],
    index: "LibraryIndex",
) -> None:
    """Persist *username* into the stored session row if it has changed.

    No-op when *username* is empty or already matches the stored value.
    """
    if username and session_data.get("username") != username:
        session_data["username"] = username
        index.set_session("bandcamp", session_data)


def _extract_pagedata(html: str, url: str) -> dict[str, Any]:
    match = re.search(r'id="pagedata"[^>]*data-blob="([^"]+)"', html)
    if not match:
        # Log the start of the response to diagnose bot-detection pages or
        # unexpected redirects on download pages.
        logger.error(
            "_extract_pagedata: no pagedata in %s — response head: %r",
            url,
            html[:500],
        )
        raise BandcampAPIError(f"Could not find pagedata blob in {url}")
    result: dict[str, Any] = json.loads(html_lib.unescape(match.group(1)))
    return result


def _fetch_collection(
    fan_id: int, session: _AnySession, index: "LibraryIndex"
) -> list[dict[str, Any]]:
    """Fetch all collection items (visible + hidden), deduplicated by sale_item_id."""
    seen: set[int] = set()
    items: list[dict[str, Any]] = []
    for endpoint in (_COLLECTION_URL, _HIDDEN_URL):
        for item in _paginate(endpoint, fan_id, session, index):
            item_id: int = item["sale_item_id"]
            if item_id not in seen:
                seen.add(item_id)
                items.append(item)
    return items


def _paginate(
    endpoint: str, fan_id: int, session: _AnySession, index: "LibraryIndex"
) -> list[dict[str, Any]]:
    """POST paginated requests to *endpoint* and return all items."""
    items: list[dict[str, Any]] = []
    older_than_token = f"{int(time.time())}:0:a::"

    while True:
        payload = {
            "fan_id": fan_id,
            "count": _COLLECTION_PAGE_BATCH,
            "older_than_token": older_than_token,
        }
        resp = session.post(
            endpoint,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code in (401, 403, 302):
            # Session expired mid-sync — clear keychain+DB so the next run re-prompts login.
            logger.info(
                "Bandcamp session rejected by API (HTTP %d) — clearing session.",
                resp.status_code,
            )
            index.clear_session("bandcamp")
            raise BandcampAPIError(
                f"Bandcamp session expired (HTTP {resp.status_code}) — "
                "session cleared. Click Login in the kamp menu bar to re-authenticate."
            )
        resp.raise_for_status()

        result: dict[str, Any] = resp.json()

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


def _get_download_links(
    username: str,
    item_ids: set[int],
    session: _AnySession,
) -> dict[int, str]:
    """GET the fan collection page and parse download-page URLs from HTML.

    Returns a dict mapping sale_item_id → redownload_url.  Items not found
    in the HTML (hidden or recently purchased) are omitted from the result.
    """
    url = f"https://bandcamp.com/{username}/"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    # Match: <a href="https://...bandcamp.com/download?...sitem_id=NNN...">
    pattern = re.compile(
        r'href="(https://[^"]*bandcamp\.com/download\?[^"]*sitem_id=(\d+)[^"]*)"'
    )
    found: dict[int, str] = {}
    for match in pattern.finditer(resp.text):
        sid = int(match.group(2))
        if sid in item_ids:
            found[sid] = html_lib.unescape(match.group(1))
    return found


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _get_cdn_url(redownload_url: str, fmt: str, session: _AnySession) -> str:
    """GET the download page and extract the pre-signed CDN URL for *fmt*.

    Bandcamp embeds ``download_items[0].downloads[enc].url`` in the pagedata
    JSON blob on the download page — all format URLs are server-generated and
    require no JavaScript execution to obtain.

    Raises ``BandcampAPIError`` if the item is not ready or *fmt* is absent.
    """
    resp = session.get(redownload_url, timeout=30)
    resp.raise_for_status()

    blob = _extract_pagedata(resp.text, redownload_url)

    # download_items is the canonical key; digital_items is a fallback seen
    # in some page variants.
    items: list[dict[str, Any]] = (
        blob.get("download_items") or blob.get("digital_items") or []
    )
    if not items:
        raise BandcampAPIError(
            f"No download_items found in pagedata for {redownload_url}"
        )

    item = items[0]
    downloads: dict[str, Any] = item.get("downloads") or {}

    if not downloads:
        # Item is still being transcoded (recently purchased).
        raise BandcampAPIError(
            f"Item {item.get('sale_id')} ({item.get('title')!r}) has no download "
            "URLs yet — it may still be processing.  Try again later."
        )

    fmt_data = downloads.get(fmt)
    if not fmt_data:
        available = ", ".join(downloads.keys())
        raise BandcampAPIError(
            f"Format {fmt!r} not available. Available formats: {available}"
        )

    cdn_url: str = fmt_data["url"]
    return cdn_url


def _download_item(
    item: dict[str, Any],
    bc_config: BandcampConfig,
    watch_dir: Path,
    session: _AnySession,
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
    dest = watch_dir / f"{safe_name}.zip"

    logger.info("Downloading %r by %r…", item_title, band_name)
    cdn_url = _get_cdn_url(redownload_url, bc_config.format, session)

    # CDN download URLs (popplers5.bandcamp.com) are pre-signed and do not
    # require Bandcamp session cookies, so a plain requests.Session is fine
    # even in frozen mode.  Streaming the large ZIP through the Electron proxy
    # relay would buffer the entire file in memory; using requests directly
    # avoids that.  If Cloudflare proves to be an issue on the CDN subdomain,
    # see TASK-127 AC #3.
    dl_session = _requests.Session()
    dl_session.headers["User-Agent"] = _UA
    _download_file(cdn_url, dest, dl_session)
    return dest


def _download_file(
    cdn_url: str,
    dest: Path,
    session: _requests.Session,
) -> None:
    """Stream *cdn_url* to *dest*, following redirects."""
    with session.get(cdn_url, stream=True, timeout=300, allow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)


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
