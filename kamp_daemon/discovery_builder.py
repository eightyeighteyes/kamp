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

import json
import logging
import random
import time
from collections import Counter
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
from .discovery_criteria import phrasings, seed_dimension

if TYPE_CHECKING:  # pragma: no cover - types only
    from kamp_core.library import LibraryIndex

logger = logging.getLogger(__name__)

#: Records in a crate. Ten is the epic's number, and it is a product decision
#: rather than a tuning knob: a crate you can finish in a sitting is the whole
#: affordance.
CRATE_SIZE = 10

#: How many records in one crate may come from a single seed (KAMP-665).
#:
#: Two, because one is too strict — a genre you actually listen to earning two
#: records is a crate reflecting your taste, not a crate repeating itself — and
#: three is what the complaint was: three cards off one album page, three clerk
#: lines naming the same record. Enforced as a preference, not a ceiling; the
#: backfill in select_crate overruns it rather than shipping a short crate.
SEED_CAP = 2

#: Where the source's scratch space is kept between crates (KAMP-661): which seed
#: each criterion stopped on, how far into each paginated query it has read. One
#: settings row rather than a table, because the shape is the source's own and the
#: builder only round-trips it — nothing here or in the schema knows what is in it.
_ROTATION_KEY = "discovery.rotation"

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
        # Stated rather than left, and that is load-bearing: _publish MERGES, so a
        # flag omitted here would carry over from the previous build and a full
        # crate would inherit the last one's "you have seen everything" (KAMP-661).
        exhausted=False,
        # Genres come from the local profile, not the provider: it lets the UI
        # name what is being dug through without the builder knowing any
        # provider's criteria.
        hints=list(profile.top_genres[:_HINT_LIMIT]),
        # A brand-new library yields seeds for nothing but the chart, so the crate
        # is real but un-personalised. The UI says so in one line rather than
        # letting the picks imply a taste read we did not make.
        thin=profile.is_thin,
    )

    # The source's memory of previous crates (KAMP-661). Loaded before the gather
    # and saved after it, so seed rotation and discover pagination survive a
    # daemon restart — held in memory they would reset on every launch and the
    # pool would look exhausted again each time, which is the same bug on a
    # longer fuse.
    rotation = _load_rotation(index)
    try:
        candidates = source.gather(profile, budget or crate_budget(), rotation)
    except Exception:  # noqa: BLE001 - a provider must not take the daemon with it
        logger.exception("discovery: gather failed")
        return _publish(publish, state="error")
    finally:
        # Saved even when the gather raised: whatever it managed to advance before
        # failing is still progress, and re-reading those same pages next time is
        # exactly the waste this exists to stop.
        _save_rotation(index, rotation)

    # Swept before it is read, not at daemon start: sweep_orphan_pending_ingest is
    # the naming precedent but runs once at launch, and this daemon can run for
    # weeks. Sweeping first is safe — the read below simply sees what survived, and
    # a row past its TTL is one we would not want to offer anyway.
    try:
        index.sweep_discovery_buffer()
    except Exception:  # noqa: BLE001 - housekeeping must not cost a crate
        logger.warning("discovery: buffer sweep failed", exc_info=True)

    # Stock from previous builds, offered only if this gather cannot fill the
    # crate on its own (KAMP-657). Fallback rather than blend: the gather spends
    # the same requests either way, so mixing leftovers into a healthy build buys
    # nothing and costs freshness. What the buffer is actually for is the build
    # that came back thin — a 429, an exhausted budget, pages picked over — where
    # today the user just gets a short crate.
    #
    # Fresh wins any duplicate: its `why` was computed against the current
    # profile, and `_deal` dedupes on id() rather than on identity, so two objects
    # for the same album would both be dealable and the second place_in_crate would
    # MOVE the row rather than add one — leaving a hole in a crate reporting itself
    # full.
    fresh_ids = {(c.provider, c.provider_item_id) for c in candidates}
    stock = [
        c
        for c in _rehydrate(index.buffered_candidates())
        if (c.provider, c.provider_item_id) not in fresh_ids
    ]

    picks = select_crate(
        candidates,
        index=index,
        caps=source.criterion_caps,
        size=size,
        rng=rng,
        wishlist_ids=wishlist_ids,
        extra=stock,
    )
    # Attributed BEFORE anything is written, and that ordering is the whole
    # correctness of the flag: place first and this crate's own ten records are
    # `seen_before` by the time we ask, so every short crate would report the well
    # dry — including a three-record crate where nothing had been seen at all.
    #
    # Counted against the FRESH picks only. `candidates` is the gather, so letting
    # buffered picks into `picked` compares two different sets and makes the
    # inequality trivially easier — a crate topped up from stock would report the
    # racks picked over when the buffer was simply drained by `in_library`.
    fresh_picked = sum(
        1 for p in picks if (p.provider, p.provider_item_id) in fresh_ids
    )
    dry = len(picks) < size and _short_because_seen(
        candidates, index=index, picked=fresh_picked
    )

    _vary_notes(picks)

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

    # Keep what this gather found and could not use (KAMP-657). After the guard
    # above on purpose: that branch means writes are failing, and firing fifty
    # more at a database that just rejected ten is the wrong move.
    #
    # Deliberately not filtered by `_excluded` — exclusion is re-checked at
    # assembly because a candidate can be bought or wishlisted while it sits, so
    # filtering now would only bake in a verdict that has to be re-taken anyway.
    _buffer_surplus(index, candidates, picks)

    short = placed < size
    # `dry` was measured against the picks; `short` is measured against what
    # actually landed. Both have to hold: a crate short only because rows failed
    # to persist is a write problem, not a picked-over rack, and must not be
    # explained to the user as one.
    exhausted = short and dry
    if short:
        logger.info(
            "discovery: short crate %d (%d/%d)%s",
            crate_no,
            placed,
            size,
            " — everything else was already shown" if exhausted else "",
        )
    return _publish(
        publish,
        state="ready",
        crate_no=crate_no,
        short=short,
        exhausted=exhausted,
    )


def _vary_notes(picks: "Sequence[Candidate]") -> None:
    """Give repeated clerk notes an alternative phrasing, in place (KAMP-664).

    One seed's sentence rides every candidate that seed produced, and the seed cap
    is a preference rather than a ceiling, so a crate can arrive with the same
    line ten times over. Walking the picks and restating the repeats is the only
    place this can happen: the sentence is chosen per seed, and whether it repeats
    is a fact about the crate, which nothing upstream can see.

    **A note is never blanked.** Where the alternatives run out the line repeats,
    because every pick explaining itself is the promise the feature rests on and a
    silent card breaks it far worse than a dull one does.

    Runs before persistence so the stored sentence is the one shown. Surplus keeps
    its original — a variant is a fact about one crate's composition and means
    nothing to a row sitting in the buffer.

    One consequence worth naming: after this, `why` and `seed_json` no longer
    determine each other, so anything that re-renders from the seed will not
    reproduce the stored sentence.
    """
    used: Counter[str] = Counter()
    for candidate in picks:
        # Least-used wins rather than first-unused: with ten cards off one seed,
        # first-unused spends each alternative once and then falls back to the
        # original for the rest, which is barely better than not varying at all.
        options = [candidate.why, *phrasings(candidate.criterion, candidate.seed)]
        candidate.why = min(options, key=lambda text: (used[text], options.index(text)))
        used[candidate.why] += 1


def _rehydrate(rows: "Sequence[dict[str, Any]]") -> list[Candidate]:
    """Turn stored buffer rows back into Candidates (KAMP-657).

    `seed` arrives already parsed by the accessor, which is also where a
    malformed blob is absorbed — so a candidate whose provenance did not survive
    the round trip still carries its `why` and is still showable.
    """
    return [
        Candidate(
            provider=row["provider"],
            provider_item_id=row["provider_item_id"],
            item_url=row["item_url"],
            artist=row["artist"],
            title=row["title"],
            art_url=row["art_url"],
            label=row["label"],
            release_date=row["release_date"],
            criterion=row["criterion"],
            why=row["why"],
            seed=row.get("seed") or {},
        )
        for row in rows
    ]


def _buffer_surplus(
    index: "LibraryIndex",
    candidates: "Sequence[Candidate]",
    picks: "Sequence[Candidate]",
) -> int:
    """Persist what this gather found and did not place. Returns rows written."""
    placed_keys = {(p.provider, p.provider_item_id) for p in picks}
    surplus = [
        c for c in candidates if (c.provider, c.provider_item_id) not in placed_keys
    ]
    if not surplus:
        return 0
    try:
        return index.buffer_candidates(
            [
                {
                    "provider": c.provider,
                    "provider_item_id": c.provider_item_id,
                    "item_url": c.item_url,
                    "artist": c.artist,
                    "title": c.title,
                    "art_url": c.art_url,
                    "label": c.label,
                    "release_date": c.release_date,
                    "criterion": c.criterion,
                    "why": c.why,
                    "seed_json": c.seed_json(),
                }
                for c in surplus
            ]
        )
    except Exception:  # noqa: BLE001 - a cache write must not fail a built crate
        logger.warning("discovery: could not buffer surplus", exc_info=True)
        return 0


def select_crate(
    candidates: Sequence[Candidate],
    *,
    index: "LibraryIndex",
    caps: dict[str, int] | None = None,
    size: int = CRATE_SIZE,
    rng: random.Random | None = None,
    wishlist_ids: set[str] | None = None,
    extra: Sequence[Candidate] | None = None,
) -> list[Candidate]:
    """Pick up to *size* candidates, excluded and varied. Pure apart from reads.

    Exclusion runs here, at assembly, rather than at capture: a candidate may
    have been bought or wishlisted since it was gathered, and a buffered one
    (KAMP-657) may have been sitting for weeks.
    """
    rng = rng or random.Random()
    caps = caps or {}
    wishlist_ids = wishlist_ids or set()

    picks: list[Candidate] = []
    groups = _group_by_criterion(
        c
        for c in candidates
        if not _excluded(c, index=index, wishlist_ids=wishlist_ids)
    )
    if groups:
        # Shuffle the group order per crate. criteria_for() preserves REGISTRY
        # order and gather() iterates it, so without this slot 0 is the same
        # criterion in every crate for the life of the install -- a poor look for
        # a feature whose entire affordance is dealing another one.
        order = list(groups)
        rng.shuffle(order)

        picks = _deal(groups, order, size, caps, seed_cap=SEED_CAP)
        if len(picks) < size:
            # A cap is a preference, not a ceiling: honouring one to the point of
            # shrinking the crate is how a brand-new library (whose only criterion
            # is the chart) would get a one-item crate. Backfill from what the caps
            # held back, uncapped criteria having already been exhausted by _deal.
            #
            # The seed cap is dropped here for the same reason as the criterion
            # caps (KAMP-665): a thin profile can yield one seed's worth of
            # candidates and nothing else, and a two-record crate is a worse answer
            # than a crate that leans on one album page.
            picks.extend(_deal(groups, order, size - len(picks), caps={}, skip=picks))

    # Stock, and only once the fresh pool has had every chance (KAMP-657). This is
    # the whole reason the buffer exists: a gather cut short by a 429, an
    # exhausted budget or picked-over pages still fills its crate. On a healthy
    # build this branch never runs, so leftovers cannot dilute a crate that did
    # not need them.
    #
    # `skip=picks` carries the seed-cap accounting across, so stock cannot pile a
    # third record onto a seed the fresh pass already used twice.
    if extra and len(picks) < size:
        stock = _group_by_criterion(
            c for c in extra if not _excluded(c, index=index, wishlist_ids=wishlist_ids)
        )
        if stock:
            stock_order = list(stock)
            rng.shuffle(stock_order)
            picks.extend(
                _deal(stock, stock_order, size - len(picks), caps={}, skip=picks)
            )
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
    seed_cap: int | None = None,
) -> list[Candidate]:
    """Round-robin one card per criterion until *size* or nothing is left.

    Round-robin is what enforces "a criterion may repeat but a crate must span
    several" without the builder interpreting a single label -- which is what
    keeps a future non-Bandcamp provider from having to teach it their criteria.

    *seed_cap* bounds how many cards may come from one SEED (KAMP-665). One album
    page returns about twenty recommendations, so a criterion could satisfy the
    round-robin while quietly taking three of its cards off the same page --
    three clerk lines all reading "filed next to DOGGOD". A criterion is not a
    fine enough unit to catch that; the seed is.

    A candidate whose seed names nothing shareable is never capped. The chart is
    the case: it carries no personal claim and one seed produces the whole
    criterion, so folding every chart pick into a single bucket would cap the
    criterion at two by accident.

    **The cap is not enough on its own, and measuring a live crate is what showed
    it.** Five criteria over ten slots is two slots each, so a cap of two never
    binds -- a criterion only ever had two cards to place. It then spent both on
    the FIRST seed in its group, because groups are built in gather order and one
    seed's candidates are contiguous, so gathering a second album page changed
    nothing about what reached the crate. Crates 22, 23 and 24 each had two
    records from one album page for exactly this reason.

    So the pick inside a group goes to the seed used LEAST so far. It costs no
    requests -- the same candidates in a better order -- and it is what makes the
    extra seeds gathered upstream actually show up in the crate.
    """
    taken = {id(c) for c in (skip or [])}
    counts: dict[str, int] = {}
    # Seeded from what an earlier pass already took, so the backfill below cannot
    # undo the cap it is backfilling past.
    seed_counts: dict[str, int] = {}
    for candidate in skip or []:
        key = seed_dimension(candidate.seed)
        if key is not None:
            seed_counts[key] = seed_counts.get(key, 0) + 1

    picks: list[Candidate] = []
    while len(picks) < size:
        progressed = False
        for criterion in order:
            if len(picks) >= size:
                break
            cap = caps.get(criterion)
            if cap is not None and counts.get(criterion, 0) >= cap:
                continue

            # Least-used seed first. Stable within a tie, so a group whose seeds
            # are all equally used keeps its gather order and the existing
            # ordering tests still describe the behaviour.
            available = [c for c in groups[criterion] if id(c) not in taken]
            available.sort(
                key=lambda c: seed_counts.get(seed_dimension(c.seed) or "", 0)
            )

            for candidate in available:
                key = seed_dimension(candidate.seed)
                if (
                    seed_cap is not None
                    and key is not None
                    and seed_counts.get(key, 0) >= seed_cap
                ):
                    continue
                taken.add(id(candidate))
                counts[criterion] = counts.get(criterion, 0) + 1
                if key is not None:
                    seed_counts[key] = seed_counts.get(key, 0) + 1
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


def _load_rotation(index: "LibraryIndex") -> dict[str, Any]:
    """The source's scratch space from the settings row, or a blank slate.

    Never raises. The blob is a hint about where to resume, so a value that fails
    to parse — hand-edited, or written by a build that shaped it differently —
    costs exactly one crate's worth of variety. Letting it reach the build thread
    would cost the crate, which is a far worse trade for a cache.
    """
    raw = index.get_setting(_ROTATION_KEY)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        logger.warning("discovery: rotation state unreadable, starting over")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_rotation(index: "LibraryIndex", rotation: dict[str, Any]) -> None:
    """Persist the scratch space. Never raises, for the same reason as above."""
    if not rotation:
        # Nothing learned — a source that ignores the state, or a gather that
        # never got as far as a request. Writing "{}" would be noise.
        return
    try:
        index.set_setting(_ROTATION_KEY, json.dumps(rotation))
    except Exception:  # noqa: BLE001 - a cache write must not fail a built crate
        logger.warning("discovery: could not save rotation state", exc_info=True)


def _short_because_seen(
    candidates: "Sequence[Candidate]", *, index: "LibraryIndex", picked: int
) -> bool:
    """True when the shortfall is the well being dry rather than a thin gather.

    The distinction is the whole point of the flag. A crate can come up short
    because the budget stopped us, because a 429 cut the gather off, or because
    everything found had already been shown — and only the last of those is a
    sentence the user should be told, because only it means the racks are
    genuinely picked over rather than something having gone wrong.

    Attributed by re-running the one exclusion that means "you have already seen
    this". `seen_before` is an indexed lookup and the candidate list is tens of
    rows, so re-checking is cheaper than threading a counter out of select_crate
    and through a signature every existing caller and test depends on.
    """
    if not candidates:
        return False
    seen = sum(
        1 for c in candidates if index.seen_before(c.provider, c.provider_item_id)
    )
    # Every candidate that did not make the crate had already been shown.
    return seen > 0 and picked + seen >= len(candidates)


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
