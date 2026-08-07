#!/usr/bin/env python3
"""Recon spike for the Discovery Crate epic — KAMP-644.

Answers, with evidence, what the Discovery Crate stories are about to be built on:
which Bandcamp surfaces are fetchable, what identity fields they carry, and how
they behave on the transport the shipped app actually uses.

This is a *throwaway investigation tool*, not a parser library.  Nothing here is
intended to survive into ``kamp_daemon``: KAMP-647 rewrites every parser it needs
under test.  ``scripts/`` sits outside the coverage source, and that exemption is
a hazard rather than a convenience — code written here to dodge the coverage bar
would arrive in production untested.  So this file only ever fetches, inspects,
reports, and captures.

Two transports, because a verdict is only as good as the transport it was proven on:

* ``direct``  — plain ``requests`` with the stored cookies.  What dev machines use.
* ``relay``   — POST to the running daemon's ``/api/v1/bandcamp/proxy-fetch``, which
  hands the request to Electron's Chromium stack.  What *every shipped build* uses,
  on every platform, because ``_needs_proxy_session()`` is true whenever frozen.

Usage:
    poetry run python scripts/spike_discovery_recon.py session
    poetry run python scripts/spike_discovery_recon.py fetch <url> [--transport relay]
    poetry run python scripts/spike_discovery_recon.py fetch <url> --dump --save name

Read-only by default: it fetches and reports.  Nothing is written to the repo, and
nothing mutates the Bandcamp account, unless a flag explicitly says so.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html as html_lib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# Where raw captures land for inspection.  Deliberately NOT the repo: promoting a
# capture into tests/fixtures/ is a separate, scrubbed, deliberate step.
SCRATCH = Path(
    "/private/tmp/claude-501/-Users-tterry-repos-kamp/"
    "bb43eae7-3762-46c1-8a3b-571b4a8c2720/scratchpad/recon"
)

PROXY_FETCH_URL = "http://127.0.0.1:47483/api/v1/bandcamp/proxy-fetch"

# Match what bandcamp.py sends, so we are characterising the same request shape.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


def auth_token() -> str:
    """Return the daemon's shared secret, which lives beside the library DB."""
    from kamp_daemon.config import _state_dir

    token_path = _state_dir() / ".token"
    if not token_path.exists():
        sys.exit(f"No auth token at {token_path} — is the Kamp app running?")
    return token_path.read_text().strip()


def load_session() -> dict[str, Any]:
    """Return the stored Bandcamp session, sourced from the running daemon.

    Not from the Keychain, and not from the DB, both of which are closed to a dev
    script on this machine:

    * **Keychain** — the *packaged, signed* app has bundle identity and stores the
      session in the Data Protection Keychain.  Unsigned dev Python gets
      ``errSecMissingEntitlement`` from DPC and falls back to the Login Keychain,
      which has no kamp item.  So dev tooling simply cannot read the shipped app's
      session (verified: ``security find-generic-password -s kamp -a bandcamp``
      reports "could not be found").
    * **DB** — the live daemon holds ``library.db`` in WAL mode, and a read-only
      connection intermittently fails to open it.  Opening it read-write via
      ``LibraryIndex`` would additionally re-run ``_migrate()`` against production.

    The daemon's own endpoint is the authoritative source: it is exactly where the
    Electron relay pulls cookies from before each proxy-fetch, so sourcing them
    here means both transports carry identical credentials.  When the app is not
    running we fall back to the ``sessions`` table in a *snapshot copy* of the
    library DB, which still carries a plaintext cookie blob.  Those cookies may be
    staler than the Keychain's (Bandcamp rotates ``identity`` on responses), so the
    source in use is always printed.
    """
    try:
        resp = requests.get(
            "http://127.0.0.1:47483/api/v1/bandcamp/session-cookies",
            headers={"X-Kamp-Token": auth_token()},
            timeout=10,
        )
        resp.raise_for_status()
        cookies = resp.json().get("cookies", [])
        if cookies:
            print(f"[session] source: running daemon ({len(cookies)} cookies)")
            return {"cookies": cookies}
    except (requests.RequestException, SystemExit):
        pass

    snapshot = SCRATCH / "library-snapshot.db"
    if not snapshot.exists():
        sys.exit(
            "No session available: daemon is down and no DB snapshot exists.\n"
            f"Start the Kamp app, or copy library.db to {snapshot}."
        )
    import sqlite3

    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT session_json FROM sessions WHERE service = 'bandcamp'"
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        sys.exit("No Bandcamp session in the DB snapshot — log in via the Kamp app.")
    data: dict[str, Any] = json.loads(row[0])
    print(
        f"[session] source: DB snapshot ({len(data.get('cookies', []))} cookies) "
        "— may be staler than the Keychain"
    )
    return data


def cookie_dict(session_data: dict[str, Any]) -> dict[str, str]:
    return {
        c["name"]: c["value"]
        for c in session_data.get("cookies", [])
        if c.get("domain", "").endswith("bandcamp.com")
    }


# ---------------------------------------------------------------------------
# Scrubbing — this repository is public
# ---------------------------------------------------------------------------


@dataclass
class Scrubber:
    """Forbidden patterns, built from the live session's own secrets.

    A denylist of field names would be guesswork; this is built from the actual
    values that must never be published — every cookie value, the fan id, the
    username — plus the token-shaped literals Bandcamp embeds in logged-in pages
    (``crumb`` values are live CSRF tokens).

    The repo is public and git history is forever, so this runs at capture time
    *and* as a committed test.  A capture that trips it is discarded, not fixed up.
    """

    secrets: list[str] = field(default_factory=list)

    # A live crumb, not the word "crumb".  Crumbs are shaped |action|epoch|hmac=
    # and an anonymous page ships `data-crumbs="{}"` — refusing on the bare
    # substring would reject every clean capture, which is how a safety check
    # gets switched off.  Match the token instead.
    crumb_token = re.compile(r"\|[a-z_/0-9]{3,}\|\d{9,}\|[A-Za-z0-9+/=]{8,}")

    @classmethod
    def from_session(
        cls, session_data: dict[str, Any], extra: list[str] | None = None
    ) -> "Scrubber":
        # Cookie *values* only.  Names like "identity" and "logout" are ordinary
        # words that appear in any page; treating them as secrets produced false
        # positives on every capture and taught the reader to ignore the warning.
        secrets = [
            str(c["value"])
            for c in session_data.get("cookies", [])
            if c.get("value") and len(str(c["value"])) >= 12
        ]
        if session_data.get("username"):
            secrets.append(str(session_data["username"]))
        secrets += [str(e) for e in (extra or []) if str(e)]
        return cls(secrets=secrets)

    # Fan-identifying JSON keys, in both raw and HTML-escaped form (album pages
    # embed JSON inside attributes, so `"` arrives as `&quot;`).
    _REDACTIONS: tuple[tuple[str, str], ...] = (
        (r'(&quot;|")fan_id\1:\s*\d+', r"\1fan_id\1:0"),
        (r'(&quot;|")(fan_username|username|fan_name)\1:\1[^&"]*\1', r"\1\2\1:\1x\1"),
        (r'(&quot;|")(token|fan_photo|image_id)\1:\1?[^&",}]*\1?', r"\1\2\1:\1x\1"),
        (r'data-crumbs="[^"]*"', 'data-crumbs="{}"'),
    )

    @staticmethod
    def redact(text: str) -> str:
        """Strip fan identifiers from a capture before it is committed.

        Necessary even for an anonymous capture: an album page publicly lists the
        fans who *collected* it, so a page for an album the account owns embeds
        that account's own fan_id and username — and every other collector's too.
        Those are real people, and this repository is public, so the supporters
        data is neutralised rather than republished.  Page structure and length
        survive, which is what the fixture is actually for.
        """
        out = text
        for pattern, repl in Scrubber._REDACTIONS:
            out = re.sub(pattern, repl, out)
        return out

    def findings(self, text: str) -> list[str]:
        """Return which forbidden things appear in *text* — never the values."""
        hits: list[str] = []
        for secret in self.secrets:
            # Word-boundary match for all-digit secrets: a 7-digit fan id matches
            # by accident inside the longer numeric ids a Bandcamp page is full of.
            found = (
                re.search(rf"\b{re.escape(secret)}\b", text)
                if secret.isdigit()
                else (secret in text)
            )
            if found:
                hits.append(f"session-secret (len={len(secret)}, {secret[:3]}…)")
        if self.crumb_token.search(text):
            hits.append("live crumb token")
        # Any surviving fan identifier, whoever it belongs to.  Album pages list
        # their collectors publicly, so these are third parties' names as often as
        # the account holder's, and neither belongs in a public repo.
        for key in ("fan_id", "fan_username", "fan_name"):
            m = re.search(
                rf'(?:&quot;|"){key}(?:&quot;|"):\s*(?:&quot;|")?([^,&"}}]+)', text
            )
            if m and m.group(1) not in ("0", "x"):
                hits.append(f"unredacted {key}")
        return hits


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


@dataclass
class Fetched:
    status: int
    text: str
    content_type: str
    final_url: str
    elapsed: float
    transport: str
    # The relay cannot surface response headers at all, so this is None there.
    # That is itself a finding: rate-limit calibration through the relay is blind.
    headers: dict[str, str] | None

    @property
    def looks_like_cloudflare_challenge(self) -> bool:
        """Heuristic from KAMP-636: a ~3 KB text/html body where JSON/a page was due."""
        return (
            "text/html" in self.content_type
            and len(self.text) < 6000
            and ("/_fs-ch-" in self.text or "challenge" in self.text.lower())
        )


class DirectTransport:
    """Plain requests + cookies.  What macOS/Linux dev uses; NOT what ships."""

    name = "direct"

    def __init__(
        self, session_data: dict[str, Any], *, anonymous: bool = False
    ) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.anonymous = anonymous
        if anonymous:
            # No cookies at all.  Fixtures are captured this way on purpose: a
            # logged-in page embeds live crumbs, fan_id and fan_username, and this
            # repository is public, so an anonymous capture is clean by
            # construction rather than by remembering to redact.
            return
        for name, value in cookie_dict(session_data).items():
            self.session.cookies.set(name, value, domain=".bandcamp.com", path="/")

    def get(self, url: str, timeout: int = 25) -> Fetched:
        start = time.monotonic()
        resp = self.session.get(url, timeout=timeout, allow_redirects=True)
        return Fetched(
            status=resp.status_code,
            text=resp.text,
            content_type=resp.headers.get("Content-Type", ""),
            final_url=resp.url,
            elapsed=time.monotonic() - start,
            transport=self.name,
            headers=dict(resp.headers),
        )

    def post_json(
        self,
        url: str,
        payload: Any,
        timeout: int = 25,
        headers: dict[str, str] | None = None,
    ) -> Fetched:
        start = time.monotonic()
        resp = self.session.post(url, json=payload, timeout=timeout, headers=headers)
        return Fetched(
            status=resp.status_code,
            text=resp.text,
            content_type=resp.headers.get("Content-Type", ""),
            final_url=resp.url,
            elapsed=time.monotonic() - start,
            transport=self.name,
            headers=dict(resp.headers),
        )

    def post_form(
        self,
        url: str,
        payload: Any,
        timeout: int = 25,
        headers: dict[str, str] | None = None,
    ) -> Fetched:
        """Form-encoded POST — what the browser actually does for ``*_cb`` endpoints.

        Has no relay equivalent: ``_ProxySession._fetch`` accepts only ``json=``
        and drops a ``data=`` kwarg into ``**_kwargs``.  Kept here specifically to
        compare against :meth:`post_json` and settle whether shipped builds can
        perform this call at all.
        """
        start = time.monotonic()
        resp = self.session.post(url, data=payload, timeout=timeout, headers=headers)
        return Fetched(
            status=resp.status_code,
            text=resp.text,
            content_type=resp.headers.get("Content-Type", ""),
            final_url=resp.url,
            elapsed=time.monotonic() - start,
            transport=self.name,
            headers=dict(resp.headers),
        )


class RelayTransport:
    """Route through the running daemon → Electron → Chromium, as shipped builds do.

    Requires the Kamp app to be running; the shared secret lives beside the DB.
    """

    name = "relay"

    def __init__(self) -> None:
        self.token = auth_token()

    def _relay(self, method: str, url: str, body: str | None, timeout: int) -> Fetched:
        start = time.monotonic()
        headers = {"User-Agent": UA}
        if body is not None:
            headers["Content-Type"] = "application/json"
        resp = requests.post(
            PROXY_FETCH_URL,
            json={"url": url, "method": method, "headers": headers, "body": body},
            headers={"X-Kamp-Token": self.token},
            timeout=timeout * 2 + 10,
        )
        resp.raise_for_status()
        data = resp.json()
        return Fetched(
            status=data["status"],
            text=data["body"],
            content_type=data.get("content_type", ""),
            final_url=data.get("url") or url,
            elapsed=time.monotonic() - start,
            transport=self.name,
            headers=None,  # structurally unavailable through the relay
        )

    def get(self, url: str, timeout: int = 25) -> Fetched:
        return self._relay("GET", url, None, timeout)

    def post_json(self, url: str, payload: Any, timeout: int = 25) -> Fetched:
        # Note: the relay carries a JSON *string* body.  A form-encoded body has
        # no representation here at all — see the transport notes in
        # docs/discovery-recon.md.
        return self._relay("POST", url, json.dumps(payload), timeout)


def make_transport(
    name: str, session_data: dict[str, Any], *, anonymous: bool = False
) -> Any:
    if anonymous:
        return DirectTransport(session_data, anonymous=True)
    return DirectTransport(session_data) if name == "direct" else RelayTransport()


# ---------------------------------------------------------------------------
# Exploratory extraction (throwaway — KAMP-647 writes the real parsers)
# ---------------------------------------------------------------------------


def extract_blob(html: str, element_id: str) -> dict[str, Any]:
    """Return the ``data-blob`` JSON hung off the element with *element_id*.

    Bandcamp uses this pattern under more than one id: album/fan pages use
    ``pagedata``, while /discover ships its initial state on ``DiscoverApp``
    (marked ``data-ssr-rendered``).  ``bandcamp.py`` only knows the ``pagedata``
    spelling today, which is why the discover surface first looked JS-rendered.
    """
    m = re.search(rf'id="{element_id}"[^>]*data-blob="([^"]+)"', html)
    if not m:
        return {}
    try:
        blob: dict[str, Any] = json.loads(html_lib.unescape(m.group(1)))
        return blob
    except json.JSONDecodeError:
        return {}


def extract_pagedata(html: str) -> dict[str, Any]:
    return extract_blob(html, "pagedata")


def extract_tralbum(html: str) -> dict[str, Any]:
    m = re.search(r'data-tralbum="([^"]+)"', html)
    if not m:
        return {}
    try:
        blob: dict[str, Any] = json.loads(html_lib.unescape(m.group(1)))
        return blob
    except json.JSONDecodeError:
        return {}


def summarise(obj: Any, depth: int = 0, max_depth: int = 2) -> str:
    """Render a shallow shape summary of a JSON blob — keys and value types."""
    pad = "  " * depth
    if isinstance(obj, dict):
        if depth >= max_depth:
            return f"{{{len(obj)} keys: {', '.join(list(obj)[:8])}…}}"
        lines = []
        for k, v in list(obj.items())[:40]:
            lines.append(f"{pad}  {k}: {summarise(v, depth + 1, max_depth)}")
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        return f"[{len(obj)} items] first={summarise(obj[0], depth + 1, max_depth)}"
    if isinstance(obj, str):
        return f"str({len(obj)}) {obj[:60]!r}" if len(obj) > 60 else repr(obj)
    return repr(obj)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_session(args: argparse.Namespace) -> int:
    data = load_session()
    cookies = cookie_dict(data)
    print(f"[session] cookie names: {sorted(cookies)}")
    print(f"[session] username in session data: {data.get('username', '(absent)')}")

    transport = make_transport(args.transport, data)
    print(f"\n[probe] collection_summary via {transport.name}")
    got = transport.get("https://bandcamp.com/api/fan/2/collection_summary")
    print(f"    status={got.status} in {got.elapsed:.2f}s  ct={got.content_type}")
    if got.looks_like_cloudflare_challenge:
        print("    ✗ Cloudflare challenge page — this transport is blocked")
        return 2
    if got.status != 200:
        print(f"    ✗ unexpected status; body head: {got.text[:200]!r}")
        return 2
    summary = json.loads(got.text)
    fan_id = summary.get("fan_id")
    subdomain = (summary.get("collection_summary") or {}).get("url_hints", {})
    print(f"    ✓ fan_id={fan_id}  url_hints={subdomain}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    data = load_session()
    transport = make_transport(args.transport, data, anonymous=args.anon)
    print(f"[fetch] {args.url}\n        via {transport.name}")
    got = transport.get(args.url)
    print(
        f"    status={got.status}  bytes={len(got.text)}  "
        f"in {got.elapsed:.2f}s  ct={got.content_type}"
    )
    if got.final_url != args.url:
        print(f"    final_url={got.final_url}")
    if got.headers:
        interesting = {
            k: v
            for k, v in got.headers.items()
            if k.lower()
            in {
                "retry-after",
                "x-ratelimit-remaining",
                "server",
                "cf-ray",
                "set-cookie",
            }
        }
        if interesting:
            # Redact Set-Cookie values; we only care that it was present.
            if "Set-Cookie" in interesting:
                interesting["Set-Cookie"] = "(present, redacted)"
            print(f"    headers of note: {interesting}")
    else:
        print("    headers: UNAVAILABLE (relay cannot surface response headers)")

    if got.looks_like_cloudflare_challenge:
        print("    ✗ looks like a Cloudflare challenge page")

    if args.dump:
        pd = extract_pagedata(got.text)
        tr = extract_tralbum(got.text)
        print(f"\n    pagedata: {'present' if pd else 'ABSENT'}")
        if pd:
            print(f"      top-level keys: {sorted(pd)}")
        print(f"    data-tralbum: {'present' if tr else 'ABSENT'}")
        if tr:
            print(f"      top-level keys: {sorted(tr)}")

    if args.save:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        raw = SCRATCH / f"{args.save}.{transport.name}.html"
        raw.write_text(got.text)
        digest = hashlib.sha256(got.text.encode()).hexdigest()[:16]
        print(f"\n    saved {len(got.text)} bytes -> {raw}  sha256:{digest}")
        scrub = Scrubber.from_session(data)
        hits = scrub.findings(got.text)
        print(f"    scrub check: {'CLEAN' if not hits else 'CONTAINS SECRETS'}")
        for h in sorted(set(hits)):
            print(f"      ! {h}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Dump part of a previously saved capture.

    Analysis runs against saved bytes rather than re-fetching: the origin we are
    characterising is the one that rate-limits hardest, and a spike has no excuse
    for spending a request on a question it already has the page for.
    """
    path = SCRATCH / args.file
    if not path.exists():
        sys.exit(f"No capture at {path}")
    html = path.read_text()
    if args.blob == "tralbum":
        blob = extract_tralbum(html)
    else:
        # Any other value is treated as an element id carrying a data-blob.
        blob = extract_blob(html, args.blob)
    if not blob:
        sys.exit(f"No {args.blob} blob in {path}")

    node: Any = blob
    if args.key:
        for part in args.key.split("."):
            if isinstance(node, list):
                node = node[int(part)]
            elif isinstance(node, dict) and part in node:
                node = node[part]
            else:
                sys.exit(f"Key path {args.key!r} missing at segment {part!r}")

    if args.raw:
        print(json.dumps(node, indent=2)[: args.limit])
    else:
        print(summarise(node, max_depth=args.depth))
    return 0


REC_LI = re.compile(r'<li class="recommended-album[^"]*"(.*?)</li>', re.DOTALL)
REC_SECTION = re.compile(
    r'<div class="recs-section ([a-z-]+)".*?<p class="section-title">\s*(.*?)\s*</p>',
    re.DOTALL,
)


def parse_recs(html: str) -> list[dict[str, Any]]:
    """Throwaway extraction of the album-page recommendation block.

    KAMP-647 rewrites this properly under test; it exists here only to count and
    characterise what the surface returns.
    """
    out: list[dict[str, Any]] = []
    for body in REC_LI.findall(html):

        def attr(name: str) -> str | None:
            m = re.search(rf'{name}="([^"]*)"', body)
            return html_lib.unescape(m.group(1)) if m else None

        link = re.search(r'<a class="album-link" href="([^"?]+)', body)
        art = re.search(r"(https://f4\.bcbits\.com/img/a(\d+)_[^\"']+)", body)
        supporters = re.search(r'<p class="supporters-text">(.*?)</p>', body, re.DOTALL)
        comment = re.search(
            r'<span class="comment-contents">(.*?)</span>', body, re.DOTALL
        )
        audio = attr("data-audiourl")
        out.append(
            {
                "tralbum_id": attr("data-albumid"),
                "track_id": attr("data-trackid"),
                "title": attr("data-albumtitle"),
                "artist": attr("data-artist"),
                "artist_id": attr("data-artistid"),
                "from": attr("data-from"),
                "item_url": link.group(1) if link else None,
                "art_id": art.group(2) if art else None,
                "has_mp3_128": bool(audio and "mp3-128" in audio),
                "supporters": (
                    re.sub(r"\s+", " ", html_lib.unescape(supporters.group(1))).strip()
                    if supporters
                    else None
                ),
                "has_fan_comment": bool(comment),
            }
        )
    return out


def owned_index() -> tuple[set[str], set[str]]:
    """Return (tralbum_ids, normalised album_urls) already in the collection."""
    import sqlite3

    snapshot = SCRATCH / "library-snapshot.db"
    if not snapshot.exists():
        return set(), set()
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT COALESCE(tralbum_id,''), COALESCE(album_url,'') "
            "FROM bandcamp_collection"
        ).fetchall()
    finally:
        conn.close()
    ids = {str(r[0]) for r in rows if r[0]}
    urls = {r[1].split("?")[0].rstrip("/").lower() for r in rows if r[1]}
    return ids, urls


def cmd_recs(args: argparse.Namespace) -> int:
    """Item A + C: identity fields on the also-like block, and post-exclusion yield."""
    path = SCRATCH / args.file
    if not path.exists():
        sys.exit(f"No capture at {path}")
    html = path.read_text()

    sections = REC_SECTION.findall(html)
    print(f"[sections] {len(sections)} rec section(s):")
    for cls, title in sections:
        print(f"    {cls}: {re.sub(r'\\s+', ' ', title)[:80]}")

    recs = parse_recs(html)
    print(f"\n[recs] {len(recs)} recommendation(s) parsed")
    if not recs:
        print("    ✗ none found — parser or surface changed")
        return 2

    complete = sum(1 for r in recs if r["tralbum_id"] and r["item_url"])
    with_audio = sum(1 for r in recs if r["has_mp3_128"])
    with_art = sum(1 for r in recs if r["art_id"])
    with_supporters = sum(1 for r in recs if r["supporters"])
    with_comment = sum(1 for r in recs if r["has_fan_comment"])
    print(f"    tralbum_id + item_url present: {complete}/{len(recs)}")
    print(f"    mp3-128 preview URL embedded:  {with_audio}/{len(recs)}")
    print(f"    art id extractable:            {with_art}/{len(recs)}")
    print(f"    'supported by N fans' line:    {with_supporters}/{len(recs)}")
    print(f"    fan comment present:           {with_comment}/{len(recs)}")

    owned_ids, owned_urls = owned_index()
    if owned_ids:
        owned = [
            r
            for r in recs
            if str(r["tralbum_id"]) in owned_ids
            or (r["item_url"] or "").rstrip("/").lower() in owned_urls
        ]
        pct = 100.0 * len(owned) / len(recs)
        print(
            f"\n[yield] {len(recs) - len(owned)}/{len(recs)} survive exclusion "
            f"({len(owned)} already owned, {pct:.0f}% overlap) "
            f"against {len(owned_ids)} collection items"
        )
        for r in owned:
            print(f"    owned: {r['artist']} — {r['title']}")

    for r in recs[: args.show]:
        print(
            f"\n  - {r['artist']} — {r['title']}\n"
            f"    tralbum_id={r['tralbum_id']} artist_id={r['artist_id']} "
            f"art_id={r['art_id']}\n"
            f"    url={r['item_url']}\n"
            f"    from={r['from']}  mp3-128={r['has_mp3_128']}\n"
            f"    supporters={r['supporters']!r}"
        )
    return 0


def seed_albums(limit: int) -> list[tuple[str, str, str]]:
    """Pick seed albums from the collection snapshot, favouring real listening.

    Ordered by album-level favourite then recency, because that is what the
    epic's criteria actually seed from — a random sample would characterise a
    surface nobody's crate would be built from.
    """
    import sqlite3

    snapshot = SCRATCH / "library-snapshot.db"
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT bc.band_name, bc.item_title, bc.album_url
            FROM bandcamp_collection bc
            LEFT JOIN albums a ON a.sale_item_id = bc.sale_item_id
            WHERE bc.album_url LIKE '%.bandcamp.com/album/%'
            ORDER BY COALESCE(a.favorite, 0) DESC,
                     COALESCE(a.last_played_at, 0) DESC,
                     bc.added_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1], r[2]) for r in rows]


def cmd_yield(args: argparse.Namespace) -> int:
    """Item C: characterise post-exclusion yield across several seeds.

    The number nobody had measured.  If most recommendations were already owned,
    the per-crate request budget (KAMP-646) would be wrong by a multiple and the
    "short crate" path (KAMP-648) would be the normal case rather than the
    exception.  Also measures cross-seed overlap, because a crate needs ten
    *distinct* items and recommendations clustering on the same few albums would
    quietly cap crate size.
    """
    data = load_session()
    transport = make_transport(args.transport, data)
    owned_ids, owned_urls = owned_index()
    seeds = seed_albums(args.limit)
    print(f"[yield] {len(seeds)} seeds via {transport.name}, {len(owned_ids)} owned\n")

    all_ids: dict[str, int] = {}
    totals = {"recs": 0, "owned": 0, "complete": 0, "audio": 0}
    per_seed: list[tuple[str, int, int]] = []

    for i, (band, title, url) in enumerate(seeds, 1):
        if i > 1:
            time.sleep(args.delay)
        got = transport.get(url)
        if got.status != 200 or got.looks_like_cloudflare_challenge:
            print(f"  {i}. {band} — {title}: status={got.status} SKIPPED")
            continue
        recs = parse_recs(got.text)
        owned_here = [
            r
            for r in recs
            if str(r["tralbum_id"]) in owned_ids
            or (r["item_url"] or "").rstrip("/").lower() in owned_urls
        ]
        totals["recs"] += len(recs)
        totals["owned"] += len(owned_here)
        totals["complete"] += sum(1 for r in recs if r["tralbum_id"] and r["item_url"])
        totals["audio"] += sum(1 for r in recs if r["has_mp3_128"])
        for r in recs:
            if r["tralbum_id"]:
                all_ids[str(r["tralbum_id"])] = all_ids.get(str(r["tralbum_id"]), 0) + 1
        per_seed.append((f"{band} — {title}", len(recs), len(owned_here)))
        print(
            f"  {i}. {band[:24]} — {title[:26]}: {len(recs)} recs, "
            f"{len(owned_here)} owned, {got.elapsed:.2f}s"
        )

    if not totals["recs"]:
        print("\n✗ no recommendations parsed from any seed")
        return 2

    fresh = totals["recs"] - totals["owned"]
    distinct = len(all_ids)
    repeats = sum(1 for c in all_ids.values() if c > 1)
    print(
        f"\n[totals] {totals['recs']} recs across {len(per_seed)} seeds\n"
        f"  already owned:        {totals['owned']} "
        f"({100.0 * totals['owned'] / totals['recs']:.0f}%)\n"
        f"  survive exclusion:    {fresh} "
        f"({100.0 * fresh / totals['recs']:.0f}%)\n"
        f"  identity complete:    {totals['complete']}/{totals['recs']}\n"
        f"  mp3-128 embedded:     {totals['audio']}/{totals['recs']}\n"
        f"  distinct tralbum_ids: {distinct} "
        f"({repeats} appeared under more than one seed)\n"
        f"  recs per request:     {totals['recs'] / len(per_seed):.1f}"
    )
    print(
        f"\n  => a 10-item crate needs ~{10.0 / max(fresh / len(per_seed), 0.1):.1f} "
        "album-page requests from this criterion alone"
    )
    return 0


DISCOVER_API = "https://bandcamp.com/api/discover/1/discover_web"


def discover_params(
    tag: str | None = None,
    slice_: str = "top",
    geoname: int = 0,
    time_facet: int | None = None,
    size: int = 20,
    category: int = 0,
) -> dict[str, Any]:
    """Build a discover_web payload matching what the Vue app sends.

    Shape lifted from ``makeParams`` in the DiscoverApp bundle rather than
    guessed, so we are exercising the same contract the site uses.
    """
    return {
        "category_id": category,
        "tag_norm_names": [tag] if tag else [],
        "geoname_id": geoname,
        "slice": slice_,
        "time_facet_id": time_facet,
        "cursor": None,
        "size": size,
        "include_result_types": ["a"],
        "followed_bands": False,
    }


def discover_ids(payload: dict[str, Any]) -> list[str]:
    # The discover API spells the tralbum id `item_id`; album-page rec blocks
    # spell the same thing `data-albumid`.  One concept, two names — worth
    # normalising once in KAMP-647 rather than at every call site.
    return [
        str(r.get("item_id")) for r in payload.get("results", []) if r.get("item_id")
    ]


def cmd_discover(args: argparse.Namespace) -> int:
    """Item B: is the discover surface reachable, and are its facets real?"""
    data = load_session()
    transport = make_transport(args.transport, data)
    params = discover_params(
        tag=args.tag,
        slice_=args.slice,
        geoname=args.geoname,
        time_facet=args.time,
        size=args.size,
    )
    print(f"[discover] POST {DISCOVER_API} via {transport.name}\n  params={params}")
    got = transport.post_json(DISCOVER_API, params)
    print(f"  status={got.status} in {got.elapsed:.2f}s ct={got.content_type}")
    if got.status != 200:
        print(f"  ✗ body head: {got.text[:300]!r}")
        return 2

    body = json.loads(got.text)
    results = body.get("results", [])
    print(
        f"  ✓ {len(results)} results  "
        f"batch_result_count={body.get('batch_result_count')} "
        f"cursor={'yes' if body.get('cursor') else 'no'}"
    )
    if not results:
        return 2

    first = results[0]
    print(f"\n  result keys: {sorted(first)}")
    owned_ids, owned_urls = owned_index()
    ours = sum(1 for r in results if str(r.get("item_id")) in owned_ids)
    theirs = sum(1 for r in results if r.get("is_owned"))
    wishlisted = sum(1 for r in results if r.get("is_wishlisted"))
    with_release = sum(1 for r in results if r.get("release_date"))
    print(
        f"  identity: item_id={first.get('item_id')} "
        f"item_type={first.get('item_type')} band_id={first.get('band_id')}"
    )
    # Bandcamp reports ownership itself; cross-check it against our own ledger,
    # because a disagreement would mean our exclusion has a blind spot.
    print(
        f"  owned per our collection: {ours}/{len(results)}   "
        f"per Bandcamp is_owned: {theirs}/{len(results)}"
    )
    print(f"  is_wishlisted true: {wishlisted}/{len(results)}")
    print(f"  release_date present: {with_release}/{len(results)}")
    # Release-year spread decides whether the epic's "over 10 years old"
    # criterion can be met by filtering discover results, given the time facet
    # only reaches back six weeks.
    years: dict[str, int] = {}
    for r in results:
        rd = str(r.get("release_date") or "")
        m = re.search(r"(\d{4})", rd)
        if m:
            years[m.group(1)] = years.get(m.group(1), 0) + 1
    if years:
        spread = ", ".join(f"{y}:{n}" for y, n in sorted(years.items()))
        oldest = min(years)
        print(f"  release years: {spread}   (oldest {oldest})")

    for r in results[: args.show]:
        print(
            f"    - {r.get('band_name')} — {r.get('title')} "
            f"(item_id={r.get('item_id')}, released={r.get('release_date')})"
        )

    if args.save:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        out = SCRATCH / f"{args.save}.{transport.name}.json"
        out.write_text(got.text)
        print(f"\n  saved -> {out}")
    return 0


def cmd_facets(args: argparse.Namespace) -> int:
    """Prove or disprove each facet, rather than assuming a 200 means it worked.

    A silently ignored query parameter is indistinguishable from a working one if
    you only check that results came back.  A facet counts as real only if two
    distinct values give materially different result sets and a nonsense value is
    rejected rather than silently falling back to the default.
    """
    data = load_session()
    transport = make_transport(args.transport, data)

    def fetch(label: str, **kw: Any) -> tuple[list[str], int]:
        got = transport.post_json(DISCOVER_API, discover_params(size=20, **kw))
        if got.status != 200:
            print(f"    {label}: HTTP {got.status}")
            return [], got.status
        ids = discover_ids(json.loads(got.text))
        print(f"    {label}: {len(ids)} results")
        time.sleep(args.delay)
        return ids, got.status

    def overlap(a: list[str], b: list[str]) -> str:
        if not a or not b:
            return "n/a"
        shared = len(set(a) & set(b))
        return f"{shared}/{min(len(a), len(b))} shared"

    print(f"[facets] via {transport.name}\n")

    print("  tag/genre facet:")
    elec, _ = fetch("electronic", tag="electronic")
    jazz, _ = fetch("jazz", tag="jazz")
    nonsense, ns_status = fetch("nonsense-tag", tag="zzzz-not-a-real-tag-zzzz")
    print(
        f"    -> distinct values differ: {overlap(elec, jazz)}\n"
        f"    -> nonsense value: {len(nonsense)} results (HTTP {ns_status})"
    )

    print("\n  slice/sort facet:")
    top, _ = fetch("slice=top", tag="electronic", slice_="top")
    new, _ = fetch("slice=new", tag="electronic", slice_="new")
    print(f"    -> top vs new: {overlap(top, new)}")

    print("\n  time facet (recency window, NOT release year):")
    t_none, _ = fetch("time=None", tag="electronic", slice_="new")
    t_week, _ = fetch(
        "time=1 (this week)", tag="electronic", slice_="new", time_facet=1
    )
    t_6w, _ = fetch(
        "time=7 (6 weeks ago)", tag="electronic", slice_="new", time_facet=7
    )
    print(
        f"    -> none vs this-week: {overlap(t_none, t_week)}\n"
        f"    -> this-week vs 6-weeks: {overlap(t_week, t_6w)}"
    )

    print("\n  location facet:")
    anywhere, _ = fetch("geoname=0 (anywhere)", tag="electronic")
    berlin, _ = fetch("geoname=2950159 (berlin)", tag="electronic", geoname=2950159)
    print(f"    -> anywhere vs berlin: {overlap(anywhere, berlin)}")
    return 0


WISHLIST_API = "https://bandcamp.com/api/fancollection/1/wishlist_items"
COLLECT_URL = "https://bandcamp.com/collect_item_cb"
UNCOLLECT_URL = "https://bandcamp.com/uncollect_item_cb"


def fetch_crumbs(transport: Any, page_url: str) -> dict[str, str]:
    """Return the ``js-crumbs-data`` map from a logged-in page.

    Crumbs are per-action CSRF tokens shaped ``|<action>|<ts>|<hmac>=``, embedded
    in a ``<meta id="js-crumbs-data">`` tag on any logged-in page.  They are
    short-lived: the server answers a stale one with HTTP 403 and
    ``{"error": "invalid_crumb", "crumb": "<fresh>"}``, which is the documented
    refresh path (the site's own ``Crumb.ajax`` retries twice on exactly that).
    """
    got = transport.get(page_url)
    m = re.search(r'id="js-crumbs-data"[^>]*data-crumbs="([^"]+)"', got.text)
    if not m:
        return {}
    crumbs: dict[str, str] = json.loads(html_lib.unescape(m.group(1)))
    return crumbs


def fan_id_of(transport: Any) -> int:
    got = transport.get("https://bandcamp.com/api/fan/2/collection_summary")
    return int(json.loads(got.text)["fan_id"])


def wishlist_ids(transport: Any, fan_id: int, count: int = 100) -> set[str]:
    payload = {
        "fan_id": fan_id,
        "count": count,
        "older_than_token": f"{int(time.time())}:0:a::",
    }
    got = transport.post_json(WISHLIST_API, payload)
    if got.status != 200:
        raise RuntimeError(f"wishlist_items HTTP {got.status}: {got.text[:200]}")
    body = json.loads(got.text)
    return {str(i.get("item_id")) for i in body.get("items", []) if i.get("item_id")}


def collect_ok(got: Fetched) -> bool:
    """True only if a ``*_cb`` call really succeeded.

    These endpoints answer **HTTP 200 with an error body** — a JSON-encoded
    request comes back 200 carrying
    ``{"error":true,"ok":false,"exception":"...InsistError...no crumb..."}``.
    Checking the status alone reports success for a call that did nothing, which
    is precisely how the first round trip left an album on the account: the undo
    step saw 200, skipped its form-encoded fallback, and never actually removed.
    """
    if got.status != 200:
        return False
    try:
        body = json.loads(got.text)
    except json.JSONDecodeError:
        return False
    return bool(body.get("ok")) and not body.get("error")


def cmd_wishlist_remove(args: argparse.Namespace) -> int:
    """Remove one album from the wishlist (form-encoded — the encoding that works)."""
    data = load_session()
    transport = make_transport(args.transport, data)
    fan_id = fan_id_of(transport)
    crumbs = fetch_crumbs(transport, args.album_url)
    if "uncollect_item_cb" not in crumbs:
        print(f"  ✗ no uncollect crumb; keys={sorted(crumbs)[:8]}")
        return 2
    payload = {
        "fan_id": fan_id,
        "item_id": int(args.item_id),
        "item_type": args.item_type,
        "band_id": int(args.band_id),
        "crumb": crumbs["uncollect_item_cb"],
    }
    got = transport.post_form(
        UNCOLLECT_URL,
        payload,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": args.album_url,
            "Origin": "https://bandcamp.com",
        },
    )
    print(f"[remove] status={got.status} ok={collect_ok(got)} body={got.text[:140]!r}")
    time.sleep(1.5)
    page = transport.get(args.album_url)
    ftd = extract_pagedata(page.text).get("fan_tralbum_data") or {}
    print(f"  verify: album page reports is_wishlisted={ftd.get('is_wishlisted')}")
    return 0 if not ftd.get("is_wishlisted") else 2


def cmd_wishlist(args: argparse.Namespace) -> int:
    """Read the fan wishlist — the exclusion source, and safe (no mutation)."""
    data = load_session()
    transport = make_transport(args.transport, data)
    fan_id = fan_id_of(transport)
    payload = {
        "fan_id": fan_id,
        "count": args.count,
        "older_than_token": f"{int(time.time())}:0:a::",
    }
    print(f"[wishlist] POST {WISHLIST_API} via {transport.name}")
    got = transport.post_json(WISHLIST_API, payload)
    print(f"  status={got.status} in {got.elapsed:.2f}s ct={got.content_type}")
    if got.status != 200:
        print(f"  ✗ body head: {got.text[:300]!r}")
        return 2
    body = json.loads(got.text)
    items = body.get("items", [])
    print(
        f"  ✓ {len(items)} item(s); more_available={body.get('more_available')} "
        f"last_token={'yes' if body.get('last_token') else 'no'}"
    )
    if items:
        print(f"  item keys: {sorted(items[0])[:18]}")
    for i in items[: args.show]:
        print(
            f"    - {i.get('band_name')} — {i.get('item_title')} "
            f"(item_id={i.get('item_id')}, type={i.get('item_type')})"
        )
    return 0


def cmd_wishlist_roundtrip(args: argparse.Namespace) -> int:
    """Add an album to the wishlist, verify, then remove it and verify — net zero.

    A crumb that merely parses proves nothing about field names, item_type, or
    referer checks, so the only honest verdict for KAMP-653 comes from a real 200.
    Doing it as a round trip means the account ends where it started.

    Also settles the question the relay poses: ``_ProxySession`` can send only
    JSON bodies, while the site's own ``Crumb.ajax`` defaults to form encoding.
    Both encodings are tried, and which ones work is the actual finding.
    """
    if not args.yes:
        print("Refusing to mutate the account without --yes.")
        return 1

    data = load_session()
    transport = make_transport(args.transport, data)
    fan_id = fan_id_of(transport)

    print(f"[roundtrip] target item_id={args.item_id} band_id={args.band_id}")
    print(f"  transport={transport.name} fan_id={fan_id}")

    # Verify via the album page's own fan_tralbum_data rather than by walking the
    # wishlist: the walk pages at 100 and this account has more than that, so a
    # first-page check could report "absent" for something already wishlisted.
    # The album page carries an exact per-item is_wishlisted flag for one request.
    def is_wishlisted() -> bool | None:
        got = transport.get(args.album_url)
        blob = extract_pagedata(got.text)
        ftd = blob.get("fan_tralbum_data") or {}
        return ftd.get("is_wishlisted")

    before = is_wishlisted()
    print(f"  album page reports is_wishlisted={before} before we touch anything")
    if before:
        print("  ✗ target is already wishlisted — pick another album")
        return 1

    crumbs = fetch_crumbs(transport, args.crumb_page)
    if "collect_item_cb" not in crumbs:
        print(f"  ✗ no collect_item_cb crumb; keys={sorted(crumbs)[:8]}")
        return 2
    print(
        f"  crumbs found: collect={('collect_item_cb' in crumbs)} "
        f"uncollect={('uncollect_item_cb' in crumbs)}"
    )

    base = {
        "fan_id": fan_id,
        "item_id": int(args.item_id),
        # "album", not the discover API's "a" — the same concept is spelled
        # differently on the two surfaces, and the wrong one earns a bare 400.
        "item_type": args.item_type,
        "band_id": int(args.band_id),
    }
    # jQuery's $.ajax sets X-Requested-With, and *_cb endpoints are referer-checked.
    ajax_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": args.album_url,
        "Origin": "https://bandcamp.com",
    }

    # Try JSON first: it is the only encoding the relay can express, so if it
    # works, shipped builds can wishlist without touching _ProxySession.
    print("\n  [add] attempt 1: JSON body (the encoding the relay can send)")
    got = transport.post_json(
        COLLECT_URL, {**base, "crumb": crumbs["collect_item_cb"]}, headers=ajax_headers
    )
    print(f"    status={got.status} ok={collect_ok(got)} body={got.text[:160]!r}")
    json_worked = collect_ok(got)

    if not json_worked and args.transport == "direct":
        print("\n  [add] attempt 2: form-encoded (what the browser sends)")
        form = dict(base)
        form["crumb"] = crumbs["collect_item_cb"]
        got = transport.post_form(COLLECT_URL, form, headers=ajax_headers)
        print(f"    status={got.status} ok={collect_ok(got)} body={got.text[:160]!r}")

    time.sleep(1.5)
    landed = is_wishlisted()
    print(f"\n  verify: album page now reports is_wishlisted={landed}")
    if not landed:
        print("  ✗ add did not land — recording as no-go, nothing to undo")
        return 2

    # Undo, so the account ends exactly where it started.
    print("\n  [remove] restoring account state")
    uncrumb = crumbs.get("uncollect_item_cb")
    got = transport.post_json(
        UNCOLLECT_URL, {**base, "crumb": uncrumb}, headers=ajax_headers
    )
    print(f"    json status={got.status} ok={collect_ok(got)} body={got.text[:120]!r}")
    # Gate the fallback on the parsed body, never the status: these endpoints
    # return 200 for a failed call, and trusting the status here is what stranded
    # an album on the account the first time round.
    if not collect_ok(got) and args.transport == "direct":
        form = dict(base)
        form["crumb"] = uncrumb or ""
        got = transport.post_form(UNCOLLECT_URL, form, headers=ajax_headers)
        print(
            f"    form status={got.status} ok={collect_ok(got)} body={got.text[:120]!r}"
        )

    time.sleep(1.5)
    still = is_wishlisted()
    gone = not still
    print(f"  verify: album page now reports is_wishlisted={still}")
    if not gone:
        print("  ! LEFT ON ACCOUNT — remove it manually on bandcamp.com")
        return 2
    print("\n  ✓ round trip complete, account state restored")
    return 0


FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "discovery"
)


def cmd_capture(args: argparse.Namespace) -> int:
    """Capture scrubbed fixtures + a manifest into tests/fixtures/discovery/.

    Captured **anonymously**: an authenticated page carries live CSRF crumbs and
    the account's fan id, and this repo is public, so the safe capture is the one
    that never had secrets rather than the one we remembered to clean.  The
    trade-off is recorded in the manifest (`logged_in: false`) and in the findings
    doc — auth-only fields such as `fan_tralbum_data.is_wishlisted` are documented
    there rather than fixtured.

    Full pages are stored gzipped as the ground truth.  A fixture trimmed to the
    interesting block could never catch a parser matching something *elsewhere* on
    the page, which is the failure mode of every regex parser in bandcamp.py.
    """
    data = load_session()
    anon = DirectTransport(data, anonymous=True)
    scrub = Scrubber.from_session(data, extra=[str(args.fan_id)] if args.fan_id else [])
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {}
    manifest_path = FIXTURE_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())

    targets: list[tuple[str, str, str]] = [
        ("album_page_with_recs", "GET", args.album_url),
        ("discover_root", "GET", "https://bandcamp.com/discover"),
    ]

    failures = 0
    for name, method, url in targets:
        got = anon.get(url)
        if got.status != 200:
            print(f"  ✗ {name}: HTTP {got.status}")
            failures += 1
            continue
        redacted = Scrubber.redact(got.text)
        hits = scrub.findings(redacted)
        if hits:
            print(f"  ✗ {name}: REFUSING to write — {sorted(set(hits))}")
            failures += 1
            continue
        raw = redacted.encode()
        out = FIXTURE_DIR / f"{name}.html.gz"
        out.write_bytes(gzip.compress(raw))
        manifest[name] = {
            "source_url": url,
            "method": method,
            "captured_at": args.captured_at,
            "transport": "direct",
            "logged_in": False,
            "request_headers": {"User-Agent": UA},
            "status": got.status,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "note": (
                "Anonymous capture: this repo is public and a logged-in page "
                "embeds live crumbs and fan_id."
            ),
        }
        print(f"  ✓ {name}: {len(raw)} bytes -> {out.name} (gz {out.stat().st_size})")
        time.sleep(1.0)

    # The discover results API, captured anonymously as JSON.
    got = anon.post_json(DISCOVER_API, discover_params(tag="ambient", slice_="top"))
    if got.status == 200 and not scrub.findings(Scrubber.redact(got.text)):
        raw = Scrubber.redact(got.text).encode()
        out = FIXTURE_DIR / "discover_web_ambient_top.json.gz"
        out.write_bytes(gzip.compress(raw))
        manifest["discover_web_ambient_top"] = {
            "source_url": DISCOVER_API,
            "method": "POST",
            "request_body": discover_params(tag="ambient", slice_="top"),
            "captured_at": args.captured_at,
            "transport": "direct",
            "logged_in": False,
            "request_headers": {"User-Agent": UA},
            "status": got.status,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "note": (
                "Anonymous: is_owned/is_wishlisted are present but always false "
                "without a session; their authenticated behaviour is in "
                "docs/discovery-recon.md."
            ),
        }
        print(f"  ✓ discover_web_ambient_top: {len(raw)} bytes")
    else:
        print(f"  ✗ discover_web: HTTP {got.status} or scrub hit")
        failures += 1

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"\n  manifest -> {manifest_path}")
    return 1 if failures else 0


def cmd_context(args: argparse.Namespace) -> int:
    """Print characters around a needle in a saved capture (grep is line-based)."""
    path = SCRATCH / args.file
    if not path.exists():
        sys.exit(f"No capture at {path}")
    html = path.read_text()
    needle = args.needle
    idx = html.find(needle)
    if idx < 0:
        print(f"needle {needle!r} not found")
        return 2
    count = html.count(needle)
    print(f"{count} occurrence(s); showing first at offset {idx}\n")
    print(html[max(0, idx - args.before) : idx + args.after])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        choices=("direct", "relay"),
        default="direct",
        help="direct = dev requests; relay = what shipped builds use",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("session", help="validate the stored session and print fan info")

    p_fetch = sub.add_parser("fetch", help="fetch one URL and report on it")
    p_fetch.add_argument("url")
    p_fetch.add_argument("--dump", action="store_true", help="summarise embedded blobs")
    p_fetch.add_argument(
        "--save", help="save raw body to the scratch dir under this name"
    )
    p_fetch.add_argument("--anon", action="store_true", help="send no cookies")

    p_ins = sub.add_parser("inspect", help="dump part of a saved capture (no network)")
    p_ins.add_argument("file", help="filename under the scratch dir")
    p_ins.add_argument(
        "--blob",
        default="pagedata",
        help="'tralbum', or an element id carrying data-blob (pagedata, DiscoverApp)",
    )
    p_ins.add_argument("--key", help="dotted key path, e.g. rec_footer.0")
    p_ins.add_argument("--depth", type=int, default=2)
    p_ins.add_argument("--raw", action="store_true", help="print raw JSON")
    p_ins.add_argument("--limit", type=int, default=4000)

    p_recs = sub.add_parser("recs", help="parse the also-like block; report yield")
    p_recs.add_argument("file")
    p_recs.add_argument("--show", type=int, default=3)

    p_yield = sub.add_parser("yield", help="measure post-exclusion yield across seeds")
    p_yield.add_argument("--limit", type=int, default=6)
    p_yield.add_argument("--delay", type=float, default=2.0)

    p_disc = sub.add_parser("discover", help="call the discover_web API")
    p_disc.add_argument("--tag")
    p_disc.add_argument("--slice", default="top", choices=("top", "new", "rand"))
    p_disc.add_argument("--geoname", type=int, default=0)
    p_disc.add_argument("--time", type=int, default=None)
    p_disc.add_argument("--size", type=int, default=20)
    p_disc.add_argument("--show", type=int, default=5)
    p_disc.add_argument("--save")

    p_fac = sub.add_parser("facets", help="prove/disprove each discover facet")
    p_fac.add_argument("--delay", type=float, default=1.5)

    p_wl = sub.add_parser("wishlist", help="read the fan wishlist (no mutation)")
    p_wl.add_argument("--count", type=int, default=100)
    p_wl.add_argument("--show", type=int, default=5)

    p_rt = sub.add_parser(
        "wishlist-roundtrip", help="add then remove one album (net zero); needs --yes"
    )
    p_rt.add_argument("--item-id", required=True)
    p_rt.add_argument("--band-id", required=True)
    p_rt.add_argument("--item-type", default="a")
    p_rt.add_argument(
        "--album-url", required=True, help="target album page, for verification"
    )
    p_rt.add_argument(
        "--crumb-page", required=True, help="logged-in page to crumb from"
    )
    p_rt.add_argument("--yes", action="store_true", help="required to mutate")

    p_rm = sub.add_parser("wishlist-remove", help="remove one album from the wishlist")
    p_rm.add_argument("--item-id", required=True)
    p_rm.add_argument("--band-id", required=True)
    p_rm.add_argument("--item-type", default="album")
    p_rm.add_argument("--album-url", required=True)

    p_cap = sub.add_parser("capture", help="write scrubbed fixtures + manifest")
    p_cap.add_argument("--album-url", required=True)
    p_cap.add_argument("--captured-at", required=True, help="ISO date, e.g. 2026-08-06")
    p_cap.add_argument("--fan-id", help="extra secret to assert absent")

    p_ctx = sub.add_parser("context", help="print text around a needle in a capture")
    p_ctx.add_argument("file")
    p_ctx.add_argument("needle")
    p_ctx.add_argument("--before", type=int, default=200)
    p_ctx.add_argument("--after", type=int, default=1200)

    args = parser.parse_args()
    if args.cmd == "session":
        return cmd_session(args)
    if args.cmd == "fetch":
        return cmd_fetch(args)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "context":
        return cmd_context(args)
    if args.cmd == "recs":
        return cmd_recs(args)
    if args.cmd == "yield":
        return cmd_yield(args)
    if args.cmd == "discover":
        return cmd_discover(args)
    if args.cmd == "facets":
        return cmd_facets(args)
    if args.cmd == "wishlist":
        return cmd_wishlist(args)
    if args.cmd == "wishlist-roundtrip":
        return cmd_wishlist_roundtrip(args)
    if args.cmd == "wishlist-remove":
        return cmd_wishlist_remove(args)
    if args.cmd == "capture":
        return cmd_capture(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
