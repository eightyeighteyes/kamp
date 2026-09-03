"""Discovery Crate foundations — types, taste profile, provider abstraction (KAMP-645).

The Discovery Crate (KAMP-643) recommends albums the user does *not* own. This module
holds the pieces every other discovery story builds on:

* :class:`Candidate` — one recommended album, with the provenance that lets the UI say
  why it is there.
* :class:`SeedProfile` — what the user's own library says about their taste, computed
  from local data only.
* :class:`RequestBudget` — the seam the rate-limit governor (KAMP-646) plugs into.
* :class:`DiscoverySource` — the provider interface, modelled on the deliberately tiny
  :class:`~kamp_daemon.genre_sources.GenreSource` ABC.

**Module boundaries for the epic**, so this file does not quietly become a second
god-module: this module owns *types and the profile only*. KAMP-647 adds the Bandcamp
fetchers in ``discovery_sources.py``; KAMP-648 adds the crate builder in
``discovery_builder.py``.

Nothing here performs I/O. Providers do, and they are handed a profile rather than a
database — a provider that reads the DB directly would couple every future service to
kamp's schema, which is precisely what the "accommodates future services" requirement
is trying to avoid.
"""

from __future__ import annotations

import json
import logging
import time as _time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from collections.abc import MutableMapping

    from kamp_core.library import LibraryIndex, SeedAlbum, SeedArtist

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request budget — the seam; KAMP-646 supplies the policy
# ---------------------------------------------------------------------------

# Endpoint classes, named after what KAMP-644 actually measured. They are separate
# because their limits are different, not as a matter of taste: album pages returned
# 429 after 57 requests in 39s, while the discover API absorbed 120 at the same rate
# without complaint. A single global request count would be born wrong.
ALBUM_PAGE = "album_page"
DISCOVER_API = "discover_api"
FANCOLLECTION = "fancollection"
ARTIST_PAGE = "artist_page"


class RequestBudget(Protocol):
    """How many requests a gather may spend, per endpoint class.

    A Protocol rather than a concrete class: KAMP-646's governor replaces the policy
    (deferral, shared backoff, coordination with the sync and download workers)
    without touching a single call signature here or in KAMP-647.
    """

    def allow(self, endpoint_class: str) -> bool:
        """True if one more request against *endpoint_class* is permitted."""

    def consume(self, endpoint_class: str, n: int = 1) -> None:
        """Record *n* requests spent against *endpoint_class*."""


@dataclass
class SimpleBudget:
    """A per-class counting budget.

    Deliberately dumb: it counts and it stops. It does not defer, back off, or know
    that other workers exist — spacing and cooldown are
    :class:`~kamp_daemon.bandcamp_ratelimit.BandcampGovernor`'s job. The two are
    kept separate on purpose: ``allow`` is a predicate and must never block, so a
    caller can ask "could I afford this?" without committing to a wait.
    """

    limits: dict[str, int] = field(default_factory=dict)
    default_limit: int = 10
    spent: dict[str, int] = field(default_factory=dict)

    def allow(self, endpoint_class: str) -> bool:
        cap = self.limits.get(endpoint_class, self.default_limit)
        return self.spent.get(endpoint_class, 0) < cap

    def consume(self, endpoint_class: str, n: int = 1) -> None:
        self.spent[endpoint_class] = self.spent.get(endpoint_class, 0) + n


def crate_budget() -> SimpleBudget:
    """The per-class request allowance for building one crate (KAMP-646).

    Calibrated from KAMP-644, which measured a varied crate at 3-6 requests
    total — so these caps carry roughly 2x margin rather than being a target.

    ``FANCOLLECTION`` is 0 on purpose, and is a tripwire rather than a limit:
    crate building must never touch the collection endpoint, which is both the
    most expensive call available and the one that rate-limits hardest. An
    accidental walk should fail a test loudly instead of quietly earning a 429
    that cascades into the download queue.

    ``default_limit=0`` likewise denies any endpoint class nobody has thought
    about, so a new fetcher must declare its class deliberately.
    """
    return SimpleBudget(
        limits={ALBUM_PAGE: 8, DISCOVER_API: 6, ARTIST_PAGE: 4, FANCOLLECTION: 0},
        default_limit=0,
    )


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """One recommended album, from any provider.

    ``criterion`` is a *provider-scoped* label (e.g. ``'also_like'``). The crate
    builder enforces variety across criteria without interpreting them — that is what
    lets a future non-Bandcamp provider invent its own criteria without teaching the
    builder about them.

    ``why`` is the human sentence the clerk card shows, and ``seed`` is the structured
    attribution behind it. Both are required for a pick to be shown: the feature's
    promise is that every recommendation explains itself, and KAMP-657 re-validates
    ``seed`` before showing a buffered candidate so the explanation is still true at
    the moment it is read.
    """

    provider: str
    provider_item_id: str
    item_url: str
    artist: str = ""
    title: str = ""
    art_url: str | None = None
    label: str = ""
    release_date: str = ""
    criterion: str = ""
    why: str = ""
    seed: dict[str, Any] = field(default_factory=dict)

    def seed_json(self) -> str:
        return json.dumps(self.seed, sort_keys=True)


@dataclass
class PreviewStream:
    """A playable preview URL, resolved on demand and never persisted.

    Bandcamp's ``mp3-128`` URLs are signed with a ``ts`` parameter and expire in about
    a day (KAMP-644), so a stored one is a bug waiting to surface as silent playback
    failure. Resolve at play time; throw it away after.

    "Never persisted" means never written to the database. KAMP-651 does keep a
    resolved list **in memory for the session**, keyed on ``expires_at`` so it is
    dropped before it can go stale -- otherwise every next/prev inside one album
    would cost another album-page fetch.
    """

    url: str
    track_num: int = 1
    title: str = ""
    duration: float = 0.0
    #: Unix time this URL stops working. Derived from the URL's own ``ts``, which
    #: is when Bandcamp signed it -- more accurate than our fetch time, since the
    #: page itself may have been served from a cache.
    expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at) and _time.time() >= self.expires_at


class UnsupportedCapability(RuntimeError):
    """Raised when a capability is used that the source does not currently offer."""


# ---------------------------------------------------------------------------
# Seed profile
# ---------------------------------------------------------------------------


@dataclass
class SeedProfile:
    """What the user's own library says about their taste.

    Computed entirely from the local database — no network, no server-side profile,
    nothing about the user leaves the machine. That is a product promise as much as an
    implementation detail.

    The qualifying sets are deliberately exposed as plain data rather than being
    consumed internally and discarded. KAMP-657 re-validates a buffered candidate's
    seed before showing it (taste drifts; "because you've been on a dub techno kick
    lately" stops being true), and that check must be a membership lookup rather than
    a re-derivation of the whole profile.
    """

    recent_album_ids: set[int] = field(default_factory=set)
    # Fetchable seeds — they carry the Bandcamp page URL, not just the display
    # identity. A criterion needs an address to GET; an album title is the mutable
    # tag, and a local-only album has no page at all.
    recent_albums: list["SeedAlbum"] = field(default_factory=list)
    favorite_album_ids: set[int] = field(default_factory=set)
    favorite_albums: list["SeedAlbum"] = field(default_factory=list)
    favorite_artists: list["SeedArtist"] = field(default_factory=list)
    #: Artists ranked by accumulated play time rather than by what was starred
    #: (KAMP-658), each carrying `owned_count` and a fetchable page. Distinct from
    #: `top_artists`, which is names only — a criterion that wants to say "you
    #: only have the one by them" needs the count and the address, not a name.
    played_artists: list["SeedArtist"] = field(default_factory=list)
    top_artists: list[str] = field(default_factory=list)
    top_genres: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    purchase_dates: dict[str, float] = field(default_factory=dict)

    @property
    def is_thin(self) -> bool:
        """True when there is not enough listening history to personalise from.

        A brand-new library is the common first-run case, not an edge case. Callers
        should fall back to un-personalised criteria (charts) rather than producing an
        empty crate or dividing by zero over an empty signal.
        """
        return not (
            self.recent_album_ids
            or self.favorite_album_ids
            or self.top_artists
            or self.top_genres
        )

    def has_genre(self, name: str) -> bool:
        return name.casefold() in {g.casefold() for g in self.top_genres}

    def has_artist(self, name: str) -> bool:
        return name.casefold() in {a.casefold() for a in self.top_artists}

    def has_label(self, name: str) -> bool:
        return name.casefold() in {label.casefold() for label in self.labels}


def build_seed_profile(
    index: "LibraryIndex",
    *,
    days: int = 30,
    genre_limit: int = 25,
    artist_limit: int = 25,
    label_limit: int = 25,
) -> SeedProfile:
    """Assemble a :class:`SeedProfile` from local library signals.

    Reuses the existing accessors wherever one already means the right thing —
    ``top_artists`` is already ranked by duration-weighted ``artists.play_time``,
    which is exactly the signal wanted here.
    """
    recent = index.recently_played_albums(days=days)
    favorites = index.favorite_albums()
    return SeedProfile(
        recent_album_ids={s.album_id for s in recent},
        recent_albums=recent,
        favorite_album_ids={s.album_id for s in favorites},
        favorite_albums=favorites,
        favorite_artists=index.favorite_artists_with_pages(artist_limit),
        played_artists=index.played_artists_with_pages(artist_limit),
        top_artists=[a.name for a in index.top_artists(artist_limit)],
        top_genres=[name for name, _ in index.taste_genres(genre_limit)],
        labels=[name for name, _ in index.taste_labels(limit=label_limit)],
        purchase_dates=dict(index.collection_purchase_dates()),
    )


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

PREVIEW = "preview"
SAVE_REMOTE = "save_remote"


class DiscoverySource(ABC):
    """A provider of discovery candidates.

    Modelled on :class:`~kamp_daemon.genre_sources.GenreSource`'s shape — small, one
    real job — but explicitly **not** on its "return [] on any failure, silently"
    contract. See :meth:`gather`.
    """

    #: Stable key written to discovery_items.provider.
    provider_id: str = ""

    @property
    def capabilities(self) -> frozenset[str]:
        """What this source can do *right now*.

        A property rather than a class constant because capability can depend on
        runtime state — the transport, the credentials, what the remote service is
        currently willing to do — not just on which provider this is. A constant
        would let the UI render a control that silently does nothing, which is the
        class of failure ``docs/discovery-recon.md`` exists to prevent.

        The original example here was Bandcamp's wishlist write, which was thought
        to be inexpressible through the Electron relay. It was not: the relay
        carries a form body fine and KAMP-653 ships the write on every platform.
        The property still earns its keep, because a source with no Bandcamp
        session can do neither.
        """
        return frozenset()

    @property
    def criterion_caps(self) -> dict[str, int]:
        """How many picks from a given criterion a single crate should prefer.

        The crate builder enforces variety across ``criterion`` labels without
        interpreting them, which is what lets a future provider invent its own
        criteria. But "at most one best-seller per crate" genuinely does require
        knowing which label is the chart — so the *provider* names it here and the
        builder honours it as opaque data.

        A cap is a preference, not a ceiling: KAMP-648 backfills past it rather
        than shipping a short crate, because a brand-new library's only available
        criterion is the chart and a hard cap would hand that user a one-item
        crate.
        """
        return {}

    @abstractmethod
    def gather(
        self,
        profile: SeedProfile,
        budget: RequestBudget,
        state: "MutableMapping[str, Any] | None" = None,
    ) -> list[Candidate]:
        """Return candidates for *profile*, spending no more than *budget* allows.

        Criteria are provider-internal: the source decides what to look for and
        self-describes each result via ``criterion``/``why``/``seed``.

        On failure, return what was gathered rather than raising — one broken criterion
        should cost a card, not the crate. **But an empty return obliges the source to
        have logged a WARNING naming the surface and URL.** Every discovery surface is
        an unofficial endpoint that will eventually drift, and silent emptiness is how
        that arrives as "discovery is broken" with nothing in the log to explain it.

        *state* is scratch space the source may carry between crates: which seed it
        stopped on, how far into a paginated query it has read (KAMP-661). Its shape
        is the source's own business — the caller only persists it and hands it back,
        which is what keeps rotation out of the builder and out of the schema. Mutate
        it in place; it must stay JSON-serialisable. Optional, and a source that
        needs no memory may ignore it, but one that ignores it can only ever reach
        the first page of whatever it queries.
        """

    def preview_tracks(self, candidate: Candidate) -> list[PreviewStream]:
        """Every playable track of *candidate*, in track order. May be empty.

        The primary preview API, and the one implementations should override:
        :meth:`resolve_preview` is derived from it so there is a single parser
        rather than two that can disagree.

        Deliberately **not** budgeted, unlike :meth:`gather`: preview is
        user-initiated and one click should never be refused because a background
        gather spent the allowance. Implementations must also not *wait* on the
        rate-limit governor here — it is documented as a non-playback tool, and a
        cooldown would hang a click for up to five minutes. Report the outcome to
        it instead, so a crate build backs off without a listener paying for it.

        Raises :class:`UnsupportedCapability` if ``PREVIEW`` is not in
        :attr:`capabilities`.
        """
        raise UnsupportedCapability(f"{self.provider_id} cannot preview")

    def resolve_preview(self, candidate: Candidate) -> PreviewStream | None:
        """The first playable track of *candidate*, or None if there is none."""
        return next(iter(self.preview_tracks(candidate)), None)

    def save_remote(self, candidate: Candidate) -> bool:
        """Save *candidate* to the provider's own list (Bandcamp: the wishlist).

        Returns True only on a confirmed success. Callers must check
        :attr:`capabilities` first; calling without the capability raises
        :class:`UnsupportedCapability` so a missing gate fails loudly instead of
        looking like a rejection by the remote service.

        Implementations must confirm success from the *parsed response body*, never
        from the HTTP status: Bandcamp's ``*_cb`` endpoints answer 200 with an error
        payload, and trusting the status is what left an album stranded on a real
        account during the KAMP-644 spike.

        Idempotent by contract. Saving something already saved is a success, not an
        error — the user's intent is satisfied either way, and the alternative is an
        error message for a record that is sitting exactly where they wanted it.
        """
        raise UnsupportedCapability(f"{self.provider_id} cannot save remotely")

    def unsave_remote(self, candidate: Candidate) -> bool:
        """Take *candidate* back off the provider's list. The inverse of
        :meth:`save_remote`, and gated by the same :data:`SAVE_REMOTE` capability.

        One capability for both directions because a provider that can add to a
        remote list can generally remove from it — Bandcamp serves both from one
        crumb mechanism. Splitting them would be inventing a distinction no
        provider has yet made.

        Same rules as :meth:`save_remote`: confirm from the parsed body, and treat
        removing something already absent as a success.
        """
        raise UnsupportedCapability(f"{self.provider_id} cannot save remotely")
