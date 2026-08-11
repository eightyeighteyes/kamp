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
    #: Set by the parser when the response's *shape* was not understood. Each
    #: surface decides what that means for itself, because the answer differs:
    #: an HTML block that exists but yields no entries is drift, while a JSON
    #: ``results: []`` is Bandcamp honestly saying it has nothing. Inferring drift
    #: from "marker present and empty" got the JSON case backwards and warned on
    #: every genuinely empty query.
    drifted: bool = False
    #: Opaque continuation token for surfaces that page (KAMP-661). Only the
    #: discover API sets it; None means "this surface does not page" or "there is
    #: nothing after this", and the caller may not tell those apart — it stores
    #: whatever it gets and stops paging when it gets None.
    cursor: str | None = None

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def warn_if_drifted(self, surface: str, url: str) -> None:
        if not self.drifted:
            return
        logger.warning(
            "discovery: %s response was not understood from %s — "
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


def tag_slug(name: str) -> str:
    """Convert a display genre name to Bandcamp's ``tag_norm_names`` spelling.

    The discover API matches on normalised slugs — lowercase, hyphen-separated
    (``rock``, ``indie-rock``, ``hip-hop-rap``) — while kamp's genres carry display
    casing (``Rock``, ``Indie Rock``). Sending the display form is not an error:
    the API cheerfully returns an empty result set for a tag it does not know, so
    the criterion silently produces nothing and looks like a parser problem. This
    was found by running the criteria against a real library.
    """
    slug = re.sub(r"[\s/_]+", "-", (name or "").strip().lower())
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return re.sub(r"-{2,}", "-", slug).strip("-")


#: Bandcamp's art CDN pattern, matching ``fetch_album_art_bytes``' spelling
#: (``kamp_daemon/bandcamp.py``): ``a<art_id>_<size>.jpg``, size 0 = original.
_ART_URL = "https://f4.bcbits.com/img/a{art_id}_0.jpg"


def art_url_from_image(raw: Any) -> str | None:
    """Build a CDN art URL from whatever a surface calls its cover image.

    The HTML surfaces embed a finished URL, but the discover API returns
    ``primary_image`` as an **object** — ``{"image_id": ..., "is_art": true}`` —
    so passing it straight through stored a dict in a TEXT column. SQLite refused
    the bind, and because a failed row is skipped rather than fatal, every
    discover-surface candidate silently vanished from the crate while the album
    pages carried on working. Tolerant of all three shapes on purpose: this is
    remote data whose spelling has already changed once.
    """
    if isinstance(raw, dict):
        if not raw.get("is_art", True):
            return None
        raw = raw.get("image_id")
    if raw is None or raw == "":
        return None
    text = str(raw)
    if text.startswith("http"):
        return text
    return _ART_URL.format(art_id=text)


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


def _audio_url(raw: str) -> str | None:
    """Pull a playable mp3 out of a recommendation's ``data-audiourl``.

    The attribute is a JSON *object* keyed by format (``{"mp3-128": "..."}``),
    not a bare URL -- reading it as a string yields something unplayable that
    would only fail at the point of pressing play.
    """
    if not raw:
        return None
    try:
        files = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(files, dict):
        return None
    url = files.get("mp3-128") or files.get("mp3-v0")
    return url if isinstance(url, str) and url else None


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
                # An inline mp3-128 for the album's first track, present on every
                # recommendation KAMP-644 measured (48/48). Kept so a preview can
                # start playing immediately instead of waiting out a cold engine
                # spawn plus an album-page fetch; the full track list still needs
                # the page, but the listener does not wait for it.
                "audio_url": _audio_url(_attr(body, "data-audiourl")),
                "supporters": _clean_text(supporters.group(1)) if supporters else "",
                "fan_comment": _clean_text(comment.group(1)) if comment else "",
            }
        )
    # The block is on the page but we understood none of it.
    result.drifted = result.marker_present and not result.items
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
    # A present-but-empty `results` is an honest "nothing matched" — Bandcamp
    # returns exactly that for a tag outside its vocabulary, which is a normal
    # query outcome and must not be reported as drift. Drift on this surface means
    # the response parsed as JSON but no longer has a results key at all.
    # The cursor is the whole of KAMP-661: it was parsed and dropped, so every
    # crate re-asked for page one of the same query and the candidate pool looked
    # exhausted after about five digs. The captured fixture reports 815,356
    # results behind it. Coerced to str|None because an absent key, an explicit
    # null and an empty string all mean the same thing to the caller.
    cursor = body.get("cursor")
    result = ParseResult(
        marker_present=isinstance(rows, list),
        drifted="results" not in body,
        cursor=str(cursor) if cursor else None,
    )
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
                "art_url": art_url_from_image(row.get("primary_image")),
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
# Matched as whole <li> blocks rather than by zipping separate findall passes for
# ids and hrefs: that only works while every entry has both, in the same order,
# and fails by silently pairing the wrong id with the wrong album rather than by
# returning nothing.
_DISCO_ITEM = re.compile(
    r'<li data-item-id="((?:album|track)-\d+)"(.*?)</li>', re.DOTALL
)
_DISCO_HREF = re.compile(r'<a href="(/(?:album|track)/[^"]+)"')
_DISCO_TITLE = re.compile(r'<p class="title">\s*(.*?)\s*(?:<|$)', re.DOTALL)
_DISCO_ART = re.compile(r"https://f4\.bcbits\.com/img/a(\d+)_")


# ---------------------------------------------------------------------------
# Wishlist write material (KAMP-653)
# ---------------------------------------------------------------------------

_CRUMBS = re.compile(r'id="js-crumbs-data"[^>]*data-crumbs="([^"]*)"')
_TRALBUM = re.compile(r'data-tralbum="([^"]+)"')
_PAGEDATA = re.compile(r'id="pagedata"[^>]*data-blob="([^"]+)"')


def _escaped_json(match: re.Match[str] | None) -> Any:
    """Decode one HTML-escaped JSON attribute, or None if it will not decode."""
    if match is None:
        return None
    try:
        return json.loads(html_lib.unescape(match.group(1)))
    except (ValueError, TypeError):
        return None


def parse_crumbs(html: str) -> dict[str, str]:
    """The per-action CSRF tokens on any logged-in page.

    Shaped ``|<action>|<epoch>|<hmac>=`` and keyed by the endpoint they authorise
    (``collect_item_cb``, ``uncollect_item_cb``). Short-lived: a stale one earns
    HTTP 403 with a fresh crumb in the error body, which is the documented
    refresh path.

    Returns ``{}`` for a logged-out page, which ships ``data-crumbs="{}"`` — the
    tag is present either way, so its presence is not a logged-in check.
    """
    blob = _escaped_json(_CRUMBS.search(html))
    if not isinstance(blob, dict):
        return {}
    return {str(k): str(v) for k, v in blob.items()}


def parse_band_id(html: str) -> str | None:
    """The band that ``collect_item_cb`` collects under.

    From ``data-tralbum``'s ``current.band_id`` — the same blob
    :func:`kamp_daemon.bandcamp.parse_tralbum` reads for the track list.

    **Never ``current.selling_band_id``, and never a fallback to it.** The two
    diverge on label-released albums, and sending the wrong one returns HTTP 200
    carrying ``{"ok":true}`` while doing nothing at all — verified live by sending
    it deliberately. A fallback would therefore report success and change nothing,
    which is worse than failing: the UI would show a done-state for a record that
    never left the shop.
    """
    blob = _escaped_json(_TRALBUM.search(html))
    if not isinstance(blob, dict):
        return None
    current = blob.get("current")
    band_id = current.get("band_id") if isinstance(current, dict) else None
    return None if band_id is None else str(band_id)


def parse_is_wishlisted(html: str) -> bool | None:
    """Whether the logged-in fan already has this album wishlisted.

    ``None`` when the page cannot say — an anonymous page carries
    ``fan_tralbum_data: null``. Deliberately tri-state: collapsing that to False
    would turn "we were not logged in" into a confident "not wishlisted", which
    the caller would act on.
    """
    blob = _escaped_json(_PAGEDATA.search(html))
    if not isinstance(blob, dict):
        return None
    fan_data = blob.get("fan_tralbum_data")
    if not isinstance(fan_data, dict):
        return None
    value = fan_data.get("is_wishlisted")
    return None if value is None else bool(value)


def parse_collect_ok(body: str) -> bool:
    """Did a ``*_cb`` call actually succeed?

    **Never trust the HTTP status here.** These endpoints answer 200 with an error
    payload — a JSON-encoded request comes back 200 carrying
    ``{"error":true,"ok":false,"exception":"...InsistError..."}``. Checking the
    status alone reports success for a call that did nothing, which is precisely
    how the KAMP-644 spike left an album stranded on a real account.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    return bool(parsed.get("ok")) and not parsed.get("error")


def parse_fresh_crumb(body: str) -> str | None:
    """The replacement crumb Bandcamp hands back when ours was stale.

    A 403 carrying ``{"error":"invalid_crumb","crumb":"<fresh>"}`` is a documented,
    recoverable flow — the site's own ``Crumb.ajax`` retries on exactly this.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("error") != "invalid_crumb":
        return None
    crumb = parsed.get("crumb")
    return str(crumb) if crumb else None


def parse_discography(html: str, *, base_url: str = "") -> ParseResult:
    """Parse an artist's ``/music`` grid.

    Grid entries carry ids as ``album-<n>`` and *relative* hrefs, so *base_url*
    (the artist page) is needed to build absolute URLs.

    The grid gives no artist name — every entry belongs to the page's artist, so
    the caller supplies it from the seed rather than parsing it back out.
    """
    result = ParseResult(marker_present=_DISCO_MARKER in html)
    root = re.sub(r"/music/?$", "", base_url or "")
    for raw_id, body in _DISCO_ITEM.findall(html):
        href = _DISCO_HREF.search(body)
        if not href:
            continue
        title = _DISCO_TITLE.search(body)
        art = _DISCO_ART.search(body)
        result.items.append(
            {
                "provider_item_id": normalise_item_id(raw_id),
                "item_url": f"{root}{href.group(1)}" if root else href.group(1),
                "title": _clean_text(title.group(1)) if title else "",
                "art_url": art.group(0) + "0.jpg" if art else None,
                "band_id": _attr(body, "data-band-id"),
            }
        )
    result.drifted = result.marker_present and not result.items
    return result
