"""Reading the Bandcamp wishlist, for crate exclusion (KAMP-652).

The Discovery Crate must never offer a record the user has already set aside.
The discover surface reports ``is_wishlisted`` itself, but album-page
recommendations and discography entries — about half a crate — carry no such
flag, so those need the wishlist itself.

**This is the endpoint Bandcamp rate-limits hardest.** It is the same
``fancollection`` family as the collection walk that earned the 429 cascade
behind KAMP-637/639, and it is *not one request*: a measured real account is
**824 items across 9 pages**, which at the governor's 5s ``FANCOLLECTION``
spacing is roughly 40 seconds. Everything in this module follows from that:

* the walk runs in the background and never blocks a crate build;
* its result is cached for an hour rather than re-walked per crate;
* the governor is *consulted* and never waited on — a background thread that
  sleeps 300s inside a cooldown is how the collection endpoint ends up back on
  a blocking path.
"""

from __future__ import annotations

import logging
import threading
import time as _time
from typing import TYPE_CHECKING, Any, Callable

from .bandcamp_ratelimit import BandcampGovernor, get_governor
from .discovery import FANCOLLECTION

if TYPE_CHECKING:  # pragma: no cover - types only
    from kamp_core.library import LibraryIndex

    from .bandcamp import _AnySession

logger = logging.getLogger(__name__)

WISHLIST_URL = "https://bandcamp.com/api/fancollection/1/wishlist_items"

#: Rows per page. Matches ``_COLLECTION_PAGE_BATCH``; the API honours 100 and
#: returns exactly that many, so a smaller value only multiplies the page count
#: against the endpoint that limits hardest (KAMP-639).
PAGE_SIZE = 100

#: Stop after this many pages. A very large wishlist would otherwise walk for
#: minutes; truncating is fine for an exclusion filter, silently truncating is
#: not, so this logs when it bites.
MAX_PAGES = 30

#: How long a walk's result stands. Long enough that nine pages amortise across
#: many crates, short enough that *un*-wishlisting something in a browser is not
#: suppressed for the rest of the session -- exclusion is supposed to reflect
#: the state at assembly time, not at launch.
CACHE_TTL_SECS = 3600.0


def fetch_wishlist_album_ids(
    session: "_AnySession",
    fan_id: int,
    *,
    governor: BandcampGovernor | None = None,
    max_pages: int = MAX_PAGES,
    now: Callable[[], float] = _time.time,
) -> set[str]:
    """Every album id in the fan's wishlist. Returns what it got; never raises.

    A partial result is strictly better than none for an exclusion filter, so a
    failure mid-walk keeps the pages already collected.
    """
    gov = governor or get_governor()
    ids: set[str] = set()

    # The first page needs a seeded token as much as any other. Omitting it
    # returns **zero rows with no error** -- indistinguishable from an empty
    # wishlist, which is exactly how this looked the first time it was measured.
    token = f"{int(now())}:0:a::"

    for page in range(max_pages):
        if page > 0:
            # Spacing between pages only. wait_turn would also honour a 60/120/300s
            # cooldown, which is the one thing a background walk must not do --
            # see the module docstring.
            gov.wait_turn(FANCOLLECTION)
        try:
            resp = session.post(
                WISHLIST_URL,
                json={
                    "fan_id": fan_id,
                    "count": PAGE_SIZE,
                    "older_than_token": token,
                },
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
        except Exception:  # noqa: BLE001 - a failed nicety is not an error state
            logger.warning("wishlist: page %d failed", page + 1, exc_info=True)
            return ids

        status = resp.status_code
        if status == 429:
            # Report so the crate builder and download drain back off too, then
            # keep what we have.
            gov.report_429(FANCOLLECTION)
            logger.warning("wishlist: rate-limited after %d page(s)", page)
            return ids
        if status != 200:
            # Deliberately NOT clearing the session the way _paginate does on a
            # 401/403: logging the user out of Bandcamp because a discovery
            # nicety got a 403 is wildly out of proportion to what failed.
            logger.warning("wishlist: HTTP %d on page %d", status, page + 1)
            return ids
        gov.report_ok(FANCOLLECTION)

        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            logger.warning("wishlist: unparseable page %d", page + 1)
            return ids

        items: list[dict[str, Any]] = body.get("items") or []
        for item in items:
            # Albums only. A wishlist holds individual tracks too (11 of 825 on
            # the measured account), and normalise_item_id flattens `track-N`
            # and `album-N` to the same string -- so a wishlisted track would
            # silently suppress an unrelated album sharing its numeric id.
            #
            # Measured on a real account: `item_type` and `tralbum_type` agree on
            # every row (814 album/a, 11 track/t, zero disagreements), and
            # item_id == tralbum_id throughout. Either field would do; this uses
            # tralbum_* because that is the identity the candidate side keys on.
            if item.get("tralbum_type") != "a":
                continue
            tralbum_id = item.get("tralbum_id") or item.get("item_id")
            if tralbum_id is not None:
                ids.add(str(tralbum_id))

        # Terminate the way the shipped collection walk does. Note it does not
        # trust `more_available`; neither does this.
        if len(items) < PAGE_SIZE:
            return ids
        token = str(body.get("last_token") or "")
        if not token:
            return ids

    logger.warning(
        "wishlist: stopped at the %d-page cap with %d albums; exclusion is partial",
        max_pages,
        len(ids),
    )
    return ids


def mark_wishlisted_crate_items(index: "LibraryIndex", ids: set[str]) -> int:
    """Flag any record in the current crate that turns out to be wishlisted.

    The crate is assembled against whatever the cache held at the time, so an
    item can be shown before the wishlist is known -- the very first crate after
    launch is built with nothing cached at all. Rather than leave those looking
    new, the walk marks them once it completes, and the rail draws its heart.

    Only ever moves a record forward: ``record_discovery_event`` is
    highest-rank-wins, so an already-purchased pick is not demoted, and calling
    this twice writes a second event but no second state change.
    """
    crate_no = index.latest_crate_no()
    if crate_no is None or not ids:
        return 0
    marked = 0
    for item in index.crate_items(crate_no):
        if item["state"] == "wishlisted":
            continue
        if str(item["provider_item_id"]) not in ids:
            continue
        try:
            index.record_discovery_event(int(item["id"]), "wishlisted")
            marked += 1
        except Exception:  # noqa: BLE001 - a badge is not worth failing over
            logger.warning("wishlist: could not mark item %s", item["id"])
    if marked:
        logger.info("wishlist: marked %d crate item(s) as already wishlisted", marked)
    return marked


class WishlistCache:
    """The wishlist ids, walked rarely and read often.

    Locked because KAMP-653's ``add`` will arrive on a request thread while a
    crate build reads on another; builds are serialised today, so this is not
    yet reachable, but the lock is cheaper than remembering later.
    """

    def __init__(
        self,
        *,
        ttl: float = CACHE_TTL_SECS,
        now: Callable[[], float] = _time.time,
    ) -> None:
        self._ttl = ttl
        self._now = now
        self._lock = threading.Lock()
        self._ids: set[str] = set()
        self._fetched_at: float | None = None
        self._walking = False

    @property
    def ids(self) -> set[str]:
        """A copy of what is known right now — empty before the first walk.

        Deliberately does not block on a walk in progress: a crate built without
        the wishlist is better than a crate the user waits 40 seconds for.
        """
        with self._lock:
            return set(self._ids)

    @property
    def is_fresh(self) -> bool:
        with self._lock:
            return (
                self._fetched_at is not None
                and self._now() - self._fetched_at < self._ttl
            )

    def add(self, tralbum_id: str) -> None:
        """Record a wishlist addition kamp made itself (KAMP-653).

        Covers only kamp's own writes. A wishlist change made in a browser is
        invisible until the next walk, which is why the per-item check on the
        preview fetch exists.
        """
        with self._lock:
            self._ids.add(str(tralbum_id))

    def invalidate(self) -> None:
        """Drop everything — the ids belong to an account that can change."""
        with self._lock:
            self._ids = set()
            self._fetched_at = None

    def refresh(
        self,
        session: "_AnySession",
        fan_id: int,
        *,
        governor: BandcampGovernor | None = None,
    ) -> None:
        """Walk and replace, unless the data is fresh or a walk is in flight."""
        gov = governor or get_governor()
        with self._lock:
            if self._walking:
                return
            if (
                self._fetched_at is not None
                and self._now() - self._fetched_at < self._ttl
            ):
                return
            # Consulted, never waited on: a cooldown means skip this round and
            # try on the next build, rather than parking a thread for minutes on
            # the endpoint that limits hardest.
            if gov.blocked_for(FANCOLLECTION) > 0:
                logger.info("wishlist: skipping the walk, endpoint is cooling down")
                return
            self._walking = True

        try:
            ids = fetch_wishlist_album_ids(session, fan_id, governor=gov)
        finally:
            with self._lock:
                self._walking = False

        if not ids:
            # An empty result is far more likely to be a failed walk than an
            # empty wishlist, and replacing good data with nothing would quietly
            # turn the filter off.
            logger.info("wishlist: walk returned nothing; keeping what we had")
            return

        with self._lock:
            self._ids = ids
            self._fetched_at = self._now()
        logger.info("wishlist: %d albums cached", len(ids))
