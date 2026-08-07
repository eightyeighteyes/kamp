"""Crate assembly — candidates in, a crate of ten out (KAMP-648).

This is the orchestration layer of the Discovery Crate (KAMP-643). KAMP-647's
:class:`~kamp_daemon.discovery_sources.BandcampDiscoverySource` produces roughly
sixty candidates for a handful of requests; this module decides which ten the
user actually sees, in what order, and writes them down.

**No threads and no FastAPI here.** The daemon owns the thread (mirroring
``_on_genre_backfill_start``) and :mod:`kamp_core.discovery_api` owns the routes.
Keeping this a plain function is what lets the whole selection policy be tested
against a fake source with no network, no event loop and no app.

Everything the UI learns goes through the injected ``publish`` callable, which
updates the status and broadcasts it in one step. A bare mutable dict was the
obvious alternative and was rejected: it makes "mutated the state but forgot to
notify" representable, which is the failure mode that turns a working backend
into a UI that never updates.
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Any, Callable, Protocol, Sequence

from .discovery import (
    ALBUM_PAGE,
    ARTIST_PAGE,
    DISCOVER_API,
    Candidate,
    DiscoverySource,
    RequestBudget,
    SeedProfile,
    build_seed_profile,
    crate_budget,
)

if TYPE_CHECKING:  # pragma: no cover - types only
    from kamp_core.library import LibraryIndex

logger = logging.getLogger(__name__)

#: Records in a crate. Ten is the epic's number, and it is a product decision
#: rather than a tuning knob: a crate you can finish in a sitting is the whole
#: affordance.
CRATE_SIZE = 10

#: The endpoint classes discovery spends. Each carries its own cooldown, so a
#: single "am I rate limited" answer has to consider all of them.
_ENDPOINT_CLASSES = (ALBUM_PAGE, DISCOVER_API, ARTIST_PAGE)

#: How many genres to hand the UI for its "flipping through ..." status lines.
_HINT_LIMIT = 5


class _Governor(Protocol):
    def blocked_for(self, endpoint_class: str) -> float: ...


Publish = Callable[[dict[str, Any]], None]


def build_crate(
    index: "LibraryIndex",
    source: DiscoverySource,
    *,
    publish: Publish,
    governor: _Governor | None = None,
    profile: SeedProfile | None = None,
    budget: RequestBudget | None = None,
    wishlist_ids: set[str] | None = None,
    rng: random.Random | None = None,
    size: int = CRATE_SIZE,
    now: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Assemble, persist and publish one crate. Returns the final status.

    Never raises: a broken provider costs a crate, not the daemon thread that
    called it. Every exit path publishes a terminal state, because a status left
    on ``building`` is indistinguishable from a hang.
    """
    clock = now or time.time
    rng = rng or random.Random()
    governor = governor or _default_governor()

    # Read the cooldown BEFORE gathering, and refuse rather than wait. wait_turn()
    # blocks silently until a 60/120/300s cooldown expires, and the build *after*
    # a rate-limited one never sees a 429 of its own -- it simply sits inside its
    # first fetch with nothing on screen. That is the KAMP-639 failure exactly:
    # a pause that isn't visible is a hang. Refusing keeps the longest possible
    # wait inside a build down to one spacing interval, which is also why no
    # cancel endpoint is needed.
    blocked = max(governor.blocked_for(cls) for cls in _ENDPOINT_CLASSES)
    if blocked > 0:
        logger.info("discovery: crate build deferred, %.0fs of cooldown left", blocked)
        return _publish(
            publish,
            state="paused",
            paused_until=clock() + blocked,
            detail="",
        )

    if profile is None:
        profile = build_seed_profile(index)

    _publish(
        publish,
        state="building",
        paused_until=0.0,
        filled=0,
        crate_no=None,
        short=False,
        # Genres come from the local profile, not the provider: it lets the UI
        # name what is being dug through without the builder knowing any
        # provider's criteria.
        hints=list(profile.top_genres[:_HINT_LIMIT]),
    )

    try:
        candidates = source.gather(profile, budget or crate_budget())
    except Exception:  # noqa: BLE001 - a provider must not take the daemon with it
        logger.exception("discovery: gather failed")
        return _publish(publish, state="error")

    picks = select_crate(
        candidates,
        index=index,
        caps=source.criterion_caps,
        size=size,
        rng=rng,
        wishlist_ids=wishlist_ids,
    )
    if not picks:
        # Do not burn a crate number on nothing. An empty crate is a distinct
        # state from a short one -- the UI offers a retry rather than a tally.
        logger.warning(
            "discovery: no candidates survived exclusion (%d gathered)",
            len(candidates),
        )
        return _publish(publish, state="empty", crate_no=None)

    crate_no = index.next_crate_no()
    # Position counts what actually landed, not what was picked. A row that
    # fails to persist must not leave a hole in the slot sequence -- the rail
    # renders by position, so a gap is a blank sleeve the user can focus and
    # never fill.
    placed = 0
    for candidate in picks:
        try:
            item_id = index.add_discovery_candidate(
                provider=candidate.provider,
                provider_item_id=candidate.provider_item_id,
                item_url=candidate.item_url,
                artist=candidate.artist,
                title=candidate.title,
                art_url=candidate.art_url,
                label=candidate.label,
                release_date=candidate.release_date,
                criterion=candidate.criterion,
                why=candidate.why,
                seed_json=candidate.seed_json(),
            )
            index.place_in_crate(item_id, crate_no, placed)
        except Exception:  # noqa: BLE001 - one bad row costs a card, not the crate
            logger.exception(
                "discovery: could not place %s in crate %d",
                candidate.item_url,
                crate_no,
            )
            continue
        placed += 1
        _publish(publish, crate_no=crate_no, filled=placed)

    if placed == 0:
        # next_crate_no() is a read, so nothing was consumed by trying.
        logger.warning("discovery: crate %d persisted nothing", crate_no)
        return _publish(publish, state="empty", crate_no=None)

    short = placed < size
    if short:
        logger.info("discovery: short crate %d (%d/%d)", crate_no, placed, size)
    return _publish(publish, state="ready", crate_no=crate_no, short=short)


def select_crate(
    candidates: Sequence[Candidate],
    *,
    index: "LibraryIndex",
    caps: dict[str, int] | None = None,
    size: int = CRATE_SIZE,
    rng: random.Random | None = None,
    wishlist_ids: set[str] | None = None,
) -> list[Candidate]:
    """Pick up to *size* candidates, excluded and varied. Pure apart from reads.

    Exclusion runs here, at assembly, rather than at capture: a candidate may
    have been bought or wishlisted since it was gathered, and a buffered one
    (KAMP-657) may have been sitting for weeks.
    """
    rng = rng or random.Random()
    caps = caps or {}
    wishlist_ids = wishlist_ids or set()

    groups = _group_by_criterion(
        c
        for c in candidates
        if not _excluded(c, index=index, wishlist_ids=wishlist_ids)
    )
    if not groups:
        return []

    # Shuffle the group order per crate. criteria_for() preserves REGISTRY order
    # and gather() iterates it, so without this slot 0 is the same criterion in
    # every crate for the life of the install -- a poor look for a feature whose
    # entire affordance is dealing another one.
    order = list(groups)
    rng.shuffle(order)

    picks = _deal(groups, order, size, caps)
    if len(picks) < size:
        # A cap is a preference, not a ceiling: honouring one to the point of
        # shrinking the crate is how a brand-new library (whose only criterion is
        # the chart) would get a one-item crate. Backfill from what the caps held
        # back, uncapped criteria having already been exhausted by _deal.
        picks.extend(_deal(groups, order, size - len(picks), caps={}, skip=picks))
    return picks


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _deal(
    groups: dict[str, list[Candidate]],
    order: list[str],
    size: int,
    caps: dict[str, int],
    skip: list[Candidate] | None = None,
) -> list[Candidate]:
    """Round-robin one card per criterion until *size* or nothing is left.

    Round-robin is what enforces "a criterion may repeat but a crate must span
    several" without the builder interpreting a single label -- which is what
    keeps a future non-Bandcamp provider from having to teach it their criteria.
    """
    taken = {id(c) for c in (skip or [])}
    counts: dict[str, int] = {}
    picks: list[Candidate] = []
    while len(picks) < size:
        progressed = False
        for criterion in order:
            if len(picks) >= size:
                break
            cap = caps.get(criterion)
            if cap is not None and counts.get(criterion, 0) >= cap:
                continue
            for candidate in groups[criterion]:
                if id(candidate) in taken:
                    continue
                taken.add(id(candidate))
                counts[criterion] = counts.get(criterion, 0) + 1
                picks.append(candidate)
                progressed = True
                break
        if not progressed:
            break
    return picks


def _group_by_criterion(
    candidates: "Sequence[Candidate] | Any",
) -> dict[str, list[Candidate]]:
    """Group preserving first-seen order; dict insertion order is the run order."""
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.criterion, []).append(candidate)
    return groups


def _excluded(
    candidate: Candidate, *, index: "LibraryIndex", wishlist_ids: set[str]
) -> bool:
    """The three exclusion classes the epic requires.

    A miss here is the feature's worst failure: recommending someone a record
    they already own reads as the clerk not knowing his own shop.
    """
    if candidate.provider_item_id in wishlist_ids:
        return True
    if index.seen_before(candidate.provider, candidate.provider_item_id):
        return True
    return index.in_library(
        candidate.provider_item_id, candidate.artist, candidate.title
    )


def _publish(publish: Publish, **fields: Any) -> dict[str, Any]:
    publish(fields)
    return fields


def _default_governor() -> _Governor:
    from .bandcamp_ratelimit import get_governor  # noqa: PLC0415 - import cycle

    return get_governor()
