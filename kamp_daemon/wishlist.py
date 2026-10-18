"""Reading the Bandcamp wishlist, for crate exclusion (KAMP-652, KAMP-666).

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
* its result is persisted (KAMP-666) rather than re-walked per crate, and
  survives a restart — before that, every launch paid the full ~37s with no
  wishlist filtering at all on the first crate of the session;
* the governor is *consulted* and never waited on — a background thread that
  sleeps 300s inside a cooldown is how the collection endpoint ends up back on
  a blocking path.

The walk reports **completeness** alongside its rows, and that is load-bearing.
Deleting rows the walk did not see is only correct if the walk actually saw
everything; against a 429 on page 2 of 9 it would delete ~625 real rows from
disk. A ``set[str]`` return could not express the difference — four of this
module's failure paths and the page cap all produced one indistinguishable from
a genuinely complete result.
"""

from __future__ import annotations

import logging
import threading
import time as _time
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

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
WALK_TTL_SECS = 3600.0


class WalkResult(NamedTuple):
    """What a walk saw, and whether it saw all of it.

    ``complete`` is the only thing that licenses a prune. It is True solely
    when the walk reached a natural end -- a short page, or a full page with no
    continuation token. Every failure and the page cap report False, and the
    rows collected so far still come back: a partial exclusion beats none, and
    discarding the pages already paid for would make the most expensive call in
    the design also the most wasteful.
    """

    rows: list[dict[str, Any]]
    complete: bool


def _wishlist_row(item: dict[str, Any]) -> dict[str, Any] | None:
    """One API item reduced to the columns bandcamp_wishlist keeps, or None.

    Tracks are kept as well as albums, which is a change from KAMP-652's
    album-only set. The reason the set had to drop them does not apply to rows:
    ``normalise_item_id`` flattens ``track-N`` and ``album-N`` to the same
    string, so a track id in an *exclusion set* silently suppresses an
    unrelated album -- but the table is keyed ``(tralbum_id, tralbum_type)`` and
    the read accessor filters to albums, so the two stay distinct. Keeping
    tracks is what lets a prune notice an un-wishlisted one.

    Measured on a real account: ``item_type`` and ``tralbum_type`` agree on
    every row (814 album/a, 11 track/t, zero disagreements), and
    ``item_id == tralbum_id`` throughout. Either field would do; this prefers
    ``tralbum_*`` because that is the identity the candidate side keys on, and
    falls back to ``item_id`` rather than drop a row over a spelling.
    """
    tralbum_id = item.get("tralbum_id") or item.get("item_id")
    if tralbum_id in (None, ""):
        return None
    return {
        "tralbum_id": str(tralbum_id),
        "tralbum_type": str(item.get("tralbum_type") or "a"),
        "band_name": str(item.get("band_name") or ""),
        "item_title": str(item.get("item_title") or ""),
        "item_url": str(item.get("item_url") or ""),
        "item_art_id": str(item.get("item_art_id") or item.get("art_id") or ""),
        "band_id": str(item.get("band_id") or ""),
        "added": str(item.get("added") or ""),
    }


def walk_wishlist(
    session: "_AnySession",
    fan_id: int,
    *,
    governor: BandcampGovernor | None = None,
    max_pages: int = MAX_PAGES,
    now: Callable[[], float] = _time.time,
) -> WalkResult:
    """Walk the fan's wishlist. Returns what it got; never raises.

    See :class:`WalkResult` for why the completeness flag is not optional.
    """
    gov = governor or get_governor()
    rows: list[dict[str, Any]] = []

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
            return WalkResult(rows, False)

        status = resp.status_code
        if status == 429:
            # Report so the crate builder and download drain back off too, then
            # keep what we have.
            gov.report_429(FANCOLLECTION)
            logger.warning("wishlist: rate-limited after %d page(s)", page)
            return WalkResult(rows, False)
        if status != 200:
            # Deliberately NOT clearing the session the way _paginate does on a
            # 401/403: logging the user out of Bandcamp because a discovery
            # nicety got a 403 is wildly out of proportion to what failed.
            logger.warning("wishlist: HTTP %d on page %d", status, page + 1)
            return WalkResult(rows, False)
        gov.report_ok(FANCOLLECTION)

        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            logger.warning("wishlist: unparseable page %d", page + 1)
            return WalkResult(rows, False)

        items: list[dict[str, Any]] = body.get("items") or []
        for item in items:
            row = _wishlist_row(item)
            if row is not None:
                rows.append(row)

        # Terminate the way the shipped collection walk does. Note it does not
        # trust `more_available`; neither does this. Both of these are the walk
        # reaching the end of the wishlist, which is the complete case.
        if len(items) < PAGE_SIZE:
            return WalkResult(rows, True)
        token = str(body.get("last_token") or "")
        if not token:
            return WalkResult(rows, True)

    logger.warning(
        "wishlist: stopped at the %d-page cap with %d row(s); exclusion is partial",
        max_pages,
        len(rows),
    )
    return WalkResult(rows, False)


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


def write_wishlist(
    source: Any,
    item: dict[str, Any],
    *,
    add: bool,
    walker: "WishlistWalker | None" = None,
) -> str:
    """Add or remove one crate item on the provider's wishlist (KAMP-653).

    Returns ``"ok"``, or a machine reason the API layer maps to a status. Lives
    here rather than in ``kamp_core.discovery_api`` because it needs
    :class:`~kamp_daemon.discovery.Candidate` and a provider session, and
    ``kamp_core`` cannot import ``kamp_daemon`` -- the same boundary that makes
    the preview player an injected object.

    Reasons, not prose: the daemon does not write brand voice. The renderer turns
    each of these into the clerk's line.
    """
    from .discovery import SAVE_REMOTE, Candidate, UnsupportedCapability
    from .discovery_sources import RateLimitedError

    if source is None:
        return "not_connected"
    # The capability gate, and the first thing in the codebase to read the
    # property. Without it a provider that cannot write would surface as a
    # generic failure indistinguishable from the remote service saying no.
    if SAVE_REMOTE not in getattr(source, "capabilities", frozenset()):
        return "unsupported"

    candidate = Candidate(
        provider=str(item.get("provider") or "bandcamp"),
        provider_item_id=str(item.get("provider_item_id") or ""),
        item_url=str(item.get("item_url") or ""),
        artist=str(item.get("artist") or ""),
        title=str(item.get("title") or ""),
    )
    try:
        ok = source.save_remote(candidate) if add else source.unsave_remote(candidate)
    except RateLimitedError:
        return "rate_limited"
    except UnsupportedCapability:
        return "unsupported"
    except Exception as exc:  # noqa: BLE001
        # NeedsLoginError is raised by _get_fan_info on a 401/403/302. Matched by
        # name because importing it here would drag kamp_daemon.bandcamp -- and
        # its module-level requests setup -- into every caller.
        if type(exc).__name__ == "NeedsLoginError":
            return "needs_login"
        logger.warning(
            "wishlist: write failed for item %s", item.get("id"), exc_info=True
        )
        return "rejected"

    if not ok:
        return "rejected"

    # Keep the exclusion filter honest immediately. Without the discard half, an
    # album the user just removed stays suppressed from every crate until the
    # next walk with nothing to explain why.
    if walker is not None:
        if add:
            walker.add(candidate.provider_item_id)
        else:
            walker.discard(candidate.provider_item_id)
    return "ok"


class WishlistWalker:
    """Walk coordination over the persisted wishlist (KAMP-652, KAMP-666).

    Not a cache any more. The ids live in ``bandcamp_wishlist`` and survive a
    restart, which is the entire point of KAMP-666 — reading them is a SELECT,
    not a 40-second walk. What is still in memory is the coordination state
    that no column can express:

    * ``_walking``, the only thing stopping two concurrent nine-page walks.
      Crate builds are serialized, but a build is 15-30s and a walk ~40s, and
      the warm-up is spawned fire-and-forget, so dig → wait → dig overlaps them
      routinely. Two walks at once is the KAMP-637/639 cascade on the endpoint
      this module's docstring calls the hardest-limited one.
    * ``_removed_during_walk``, so a removal made *while* a walk is in flight
      is not undone when it lands. There is genuinely nothing on disk to carry
      this: the row is gone, so there is no timestamp to compare, and the walk
      — which read Bandcamp ~40s ago and still sees the item — would simply
      insert it again.

    An *addition* during a walk needs no such bookkeeping, and that is a
    property of the schema rather than luck: the row goes in with a NULL
    ``walked_at``, the walk stamps its own rows with its start time, and the
    prune only deletes rows carrying a stamp older than that.

    Locked because KAMP-653's ``add``/``discard`` arrive on a request thread
    while a crate build reads on another.
    """

    def __init__(
        self,
        index: "LibraryIndex",
        *,
        ttl: float = WALK_TTL_SECS,
        now: Callable[[], float] = _time.time,
    ) -> None:
        self._index = index
        self._ttl = ttl
        self._now = now
        self._lock = threading.Lock()
        self._walking = False
        self._removed_during_walk: set[tuple[str, str]] = set()

    @property
    def album_ids(self) -> set[str]:
        """Album ids to exclude, straight off disk.

        Deliberately does not block on a walk in progress: a crate built
        against slightly stale rows is better than a crate the user waits 40
        seconds for. Not memoised either — this is read once per build, not per
        candidate, so an 825-row SELECT is noise, and a layer in front of it
        would re-create the second source of truth KAMP-666 removed.
        """
        return self._index.wishlist_album_ids()

    @property
    def is_fresh(self) -> bool:
        """Whether a *complete* walk has run inside the TTL.

        Only complete walks stamp the timestamp, so a 429 after page 1 leaves
        this False and the next build re-walks. The old in-memory cache marked
        itself fresh for any non-empty result, which suppressed re-walks for an
        hour with 100 of 825 ids.
        """
        walked_at = self._index.wishlist_walked_at()
        return walked_at is not None and self._now() - walked_at < self._ttl

    def add(self, tralbum_id: str, tralbum_type: str = "a") -> None:
        """Record a wishlist addition kamp made itself (KAMP-653).

        Covers only kamp's own writes. A wishlist change made in a browser is
        invisible until the next walk.
        """
        key = (str(tralbum_id), tralbum_type)
        with self._lock:
            # A re-add after a mid-walk removal must not be re-deleted when the
            # walk lands: last write wins, and Bandcamp has confirmed this one.
            self._removed_during_walk.discard(key)
        self._index.add_wishlist_item(*key)

    def discard(self, tralbum_id: str, tralbum_type: str = "a") -> None:
        """Record a wishlist removal kamp made itself (KAMP-653).

        Without this a removed album stays excluded from every crate until the
        next walk -- the user takes it off their wishlist and kamp keeps
        refusing to offer it, with nothing to explain why.
        """
        key = (str(tralbum_id), tralbum_type)
        with self._lock:
            if self._walking:
                self._removed_during_walk.add(key)
        self._index.remove_wishlist_item(*key)

    def invalidate(self) -> None:
        """Drop everything — the rows belong to an account that can change.

        ``clear_wishlist`` drops the walk timestamp along with the rows, so the
        next account walks rather than reading "fresh" over an empty table.
        """
        with self._lock:
            self._removed_during_walk.clear()
        self._index.clear_wishlist()

    def refresh(
        self,
        session: "_AnySession",
        fan_id: int,
        *,
        governor: BandcampGovernor | None = None,
    ) -> None:
        """Walk, write, and prune — unless fresh, cooling down, or already walking."""
        gov = governor or get_governor()
        with self._lock:
            if self._walking:
                logger.info("wishlist: a walk is already in flight; skipping")
                return
            walked_at = self._index.wishlist_walked_at()
            if walked_at is not None and self._now() - walked_at < self._ttl:
                return
            # Consulted, never waited on: a cooldown means skip this round and
            # try on the next build, rather than parking a thread for minutes on
            # the endpoint that limits hardest.
            if gov.blocked_for(FANCOLLECTION) > 0:
                logger.info("wishlist: skipping the walk, endpoint is cooling down")
                return
            self._walking = True
            self._removed_during_walk.clear()
            started = self._now()

        # _walking stays set through the writes, not just the walk. Clearing it
        # first would leave a window where a removal lands after the flag drops
        # but before the upsert, so it is neither recorded nor re-applied — and
        # the walk's own rows put it straight back.
        try:
            result = walk_wishlist(session, fan_id, governor=gov)
            self._apply(result, started)
        finally:
            with self._lock:
                self._walking = False
                self._removed_during_walk.clear()

    def _apply(self, result: WalkResult, started: float) -> None:
        """Land a walk's rows, then prune and stamp only if it was complete."""
        self._index.upsert_wishlist_items(result.rows, walked_at=started)

        # Re-apply removals made while the walk was in flight. The walk reflects
        # Bandcamp as of ~40s ago; these are newer and were confirmed by
        # Bandcamp, so they win over whatever the pages said.
        with self._lock:
            removed = set(self._removed_during_walk)
        for tralbum_id, tralbum_type in removed:
            self._index.remove_wishlist_item(tralbum_id, tralbum_type)

        if not result.complete:
            # The rows we did get are still worth keeping -- upserts only add --
            # but a partial view cannot say what is absent, and it must not mark
            # itself fresh or the next build would trust the gap for an hour.
            logger.info(
                "wishlist: partial walk, %d row(s) written and nothing pruned",
                len(result.rows),
            )
            return

        pruned = self._index.prune_wishlist(started)
        self._index.set_wishlist_walked_at(started)
        logger.info("wishlist: %d row(s) walked, %d pruned", len(result.rows), pruned)
