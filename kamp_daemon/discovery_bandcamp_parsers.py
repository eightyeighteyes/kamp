"""Pure parsers for Bandcamp's discovery surfaces (KAMP-647).

Every function here is ``str -> data`` with **zero I/O**. That is deliberate: it
is what lets the fixture tests import this module and run real markup through it
without a single network mock, and it keeps the fetching, rate limiting and
retry policy in one place (``discovery_sources``) instead of smeared across five
parsers.

Shapes were captured and verified in KAMP-644; see ``docs/discovery-recon.md``.
All of these surfaces are unofficial and will eventually drift, which is why each
parser reports *how* it failed rather than just returning empty — see
:class:`ParseResult`.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Items plus enough context to tell drift from an honest empty answer.

    The distinction matters more than it looks. A parser that returns ``[]``
    could mean either "Bandcamp had nothing for this query" — which is normal,
    e.g. a user genre outside Bandcamp's tag vocabulary — or "the markup changed
    and we now understand nothing", which is a silent feature death.

    ``marker_present`` records whether the structural landmark was there. Empty
    items *with* the marker is drift and warns; empty items *without* any query
    result is just an empty answer and does not. Warning on both would fill the
    log with false alarms until nobody reads it, which is the failure the
    warn-on-empty rule exists to prevent.
    """

    items: list[dict[str, Any]] = field(default_factory=list)
    marker_present: bool = False

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def warn_if_drifted(self, surface: str, url: str) -> None:
        if self.items or not self.marker_present:
            return
        logger.warning(
            "discovery: %s markup present but parsed 0 items from %s — "
            "the surface has probably drifted",
            surface,
            url,
        )


# ---------------------------------------------------------------------------
# Identity normalisation
# ---------------------------------------------------------------------------

# The same tralbum id is spelled three different ways depending on which surface
# you scraped it from, and item_type is "a" on the discover API but "album" on
# the *_cb endpoints. Normalising once here keeps that trivia out of the criteria
# and out of the schema.
_ALBUM_ID_PREFIX = re.compile(r"^(?:album|track)-(\d+)$")


def normalise_item_id(raw: Any) -> str:
    """Return a bare numeric tralbum id from any of its spellings."""
    text = str(raw or "").strip()
    match = _ALBUM_ID_PREFIX.match(text)
    return match.group(1) if match else text


def strip_tracking(url: str) -> str:
    """Drop Bandcamp's ``?from=`` attribution parameter from an item URL.

    The same album reached from two seeds carries two different ``from`` values,
    so leaving it on would defeat cross-seed dedupe and store a URL that says
    more about our crawl than about the album.
    """
    return (url or "").split("?")[0]


# ---------------------------------------------------------------------------
# Album page: the "if you like ... you may also like" block
# ---------------------------------------------------------------------------

_RECS_CONTAINER = 'class="recommendations-container"'
_REC_ITEM = re.compile(r'<li class="recommended-album[^"]*"(.*?)</li>', re.DOTALL)
_REC_LINK = re.compile(r'<a class="album-link" href="([^"]+)"')
_REC_ART = re.compile(r"https://f4\.bcbits\.com/img/a(\d+)_")
_REC_SUPPORTERS = re.compile(r'<p class="supporters-text">(.*?)</p>', re.DOTALL)
_REC_COMMENT = re.compile(r'<span class="comment-contents">(.*?)</span>', re.DOTALL)


def _attr(body: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', body)
    return html_lib.unescape(match.group(1)) if match else ""


def parse_also_like(html: str) -> ParseResult:
    """Parse the album page's recommendation block.

    Server-rendered, roughly seven entries per page. Each carries the identity we
    need plus two things worth more than templated copy: a "supported by N fans
    who also own X" line and a real listener's review. Those are handed through
    for the crate's provenance card rather than discarded.
    """
    result = ParseResult(marker_present=_RECS_CONTAINER in html)
    for body in _REC_ITEM.findall(html):
        item_id = normalise_item_id(_attr(body, "data-albumid"))
        link = _REC_LINK.search(body)
        if not item_id or not link:
            continue
        art = _REC_ART.search(body)
        supporters = _REC_SUPPORTERS.search(body)
        comment = _REC_COMMENT.search(body)
        result.items.append(
            {
                "provider_item_id": item_id,
                "item_url": strip_tracking(html_lib.unescape(link.group(1))),
                "artist": _attr(body, "data-artist"),
                "title": _attr(body, "data-albumtitle"),
                "artist_id": _attr(body, "data-artistid"),
                "art_url": art.group(0) + "0.jpg" if art else None,
                "supporters": _clean_text(supporters.group(1)) if supporters else "",
                "fan_comment": _clean_text(comment.group(1)) if comment else "",
            }
        )
    return result


def _clean_text(raw: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", raw))).strip()


# ---------------------------------------------------------------------------
# Discover: facet vocabulary (server-rendered) and results (JSON API)
# ---------------------------------------------------------------------------

_DISCOVER_BLOB = re.compile(r'id="DiscoverApp"[^>]*data-blob="([^"]+)"')

# The facet families the discover page ships with its initial state. Read them
# rather than hard-coding: Bandcamp's genre list changes, and a stale hard-coded
# slug silently returns zero results.
FACET_FAMILIES = ("genres", "subgenres", "locations", "times", "slices")


def parse_discover_facets(html: str) -> dict[str, list[dict[str, Any]]]:
    """Return the discover page's facet vocabulary, or {} if it is not there.

    Note the ``times`` family is a six-week *recency* window (fresh, today,
    this-week, 1w..6w) describing when an item surfaced on Bandcamp — NOT a
    release-year filter. Criterion 5 ("over ten years old") therefore cannot use
    it and filters ``release_date`` client-side instead.
    """
    match = _DISCOVER_BLOB.search(html)
    if not match:
        return {}
    try:
        blob = json.loads(html_lib.unescape(match.group(1)))
    except json.JSONDecodeError:
        return {}
    state = (blob.get("appData") or {}).get("initialState") or {}
    return {family: state.get(family) or [] for family in FACET_FAMILIES}


def parse_discover_results(payload: str | dict[str, Any]) -> ParseResult:
    """Parse a ``discover_web`` response.

    Results carry ``is_owned`` and ``is_wishlisted``, so Bandcamp performs
    exclusion for us on this surface — those flags are passed through for the
    caller to filter on rather than being applied here, since this module does no
    policy.
    """
    if isinstance(payload, str):
        try:
            body: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            return ParseResult(marker_present=False)
    else:
        body = payload

    rows = body.get("results")
    result = ParseResult(marker_present=isinstance(rows, list))
    for row in rows or []:
        item_id = normalise_item_id(row.get("item_id"))
        if not item_id:
            continue
        result.items.append(
            {
                "provider_item_id": item_id,
                "item_url": strip_tracking(row.get("item_url") or ""),
                "artist": row.get("band_name") or row.get("album_artist") or "",
                "title": row.get("title") or "",
                "art_url": row.get("primary_image") or None,
                "release_date": row.get("release_date") or "",
                "is_owned": bool(row.get("is_owned")),
                "is_wishlisted": bool(row.get("is_wishlisted")),
                "band_id": str(row.get("band_id") or ""),
            }
        )
    return result


def release_year(release_date: str) -> int | None:
    """Pull a four-digit year out of a Bandcamp release date string."""
    match = re.search(r"(\d{4})", release_date or "")
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Artist discography
# ---------------------------------------------------------------------------

_DISCO_MARKER = 'id="music-grid"'
_DISCO_ITEM = re.compile(r'data-item-id="((?:album|track)-\d+)"')
_DISCO_GRID_LINK = re.compile(r'<a href="(/(?:album|track)/[^"]+)"')


def parse_discography(html: str, *, base_url: str = "") -> ParseResult:
    """Parse an artist's ``/music`` grid into item ids and URLs.

    Grid entries carry ids as ``album-<n>`` and relative hrefs, so *base_url* (the
    artist page) is needed to build absolute URLs.
    """
    result = ParseResult(marker_present=_DISCO_MARKER in html)
    ids = _DISCO_ITEM.findall(html)
    hrefs = _DISCO_GRID_LINK.findall(html)
    root = re.sub(r"/music/?$", "", base_url or "")
    for raw_id, href in zip(ids, hrefs):
        result.items.append(
            {
                "provider_item_id": normalise_item_id(raw_id),
                "item_url": f"{root}{href}" if root else href,
            }
        )
    return result
