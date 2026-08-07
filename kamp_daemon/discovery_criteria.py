"""The criteria that turn a taste profile into things worth fetching (KAMP-647).

A criterion is a **(surface, seed selector, provenance)** triple, not a bespoke
function. Modelling it as a data table keeps the fourteen-ish ideas the epic
collected from becoming fourteen hand-rolled code paths, and makes the story's
"each criterion unit-tested with a seeded profile" acceptance criterion a single
parametrised test over the registry.

Seed selection is **pure** — it reads a :class:`SeedProfile` and returns targets.
No I/O lives here; the source fetches, and only the source knows that rate
limiting exists.

**Scope.** Five criteria ship. KAMP-646's budget funds roughly 8 album-page,
6 discover and 4 artist-page requests per crate, so the epic's full list could
never all run — half would hit an exhausted budget on every build and never be
demonstrable. The rest are deferred (cheap to add on this machinery) or blocked
on data kamp does not have; see the KAMP-647 plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .discovery import ALBUM_PAGE, ARTIST_PAGE, DISCOVER_API, SeedProfile

logger = logging.getLogger(__name__)

# Surfaces a criterion can be fetched from. The source dispatches on these.
SURFACE_ALBUM_RECS = "album_recs"
SURFACE_DISCOVER = "discover"
SURFACE_DISCOGRAPHY = "discography"

# How far back "over ten years old" reaches, in years.
OLD_ALBUM_YEARS = 10


@dataclass(frozen=True)
class Seed:
    """One fetchable target plus the attribution it will carry.

    ``target`` is a URL for page surfaces, or discover query parameters for the
    API surface. ``why`` and ``seed_data`` are the crate card's provenance: the
    epic's promise is that every pick explains itself, so a seed that cannot say
    what produced it is not a valid seed.
    """

    target: Any
    why: str
    seed_data: dict[str, Any]


@dataclass(frozen=True)
class Criterion:
    """A named way of turning taste into fetch targets."""

    key: str
    surface: str
    endpoint_class: str
    seeds: Callable[[SeedProfile], Iterable[Seed]]
    #: Human label for logs; the user-facing copy is the per-seed ``why``.
    label: str


# ---------------------------------------------------------------------------
# Seed selectors — pure functions of the profile
# ---------------------------------------------------------------------------


def _also_like_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """Album pages whose recommendation block we want to read.

    Recent listens first, then favourites — a favourite the user played last week
    is a stronger signal than one they starred two years ago, and the recency list
    is already ordered. Deduped by album_id so an album that is both recent and
    favourited is not fetched twice.
    """
    seen: set[int] = set()
    for seed_album in [*profile.recent_albums, *profile.favorite_albums]:
        if seed_album.album_id in seen:
            continue
        seen.add(seed_album.album_id)
        recent = seed_album.album_id in profile.recent_album_ids
        yield Seed(
            target=seed_album.album_url,
            why=(
                f"Filed next to {seed_album.album}, which you played recently."
                if recent
                else f"You favourited {seed_album.album} — this sits beside it."
            ),
            seed_data={
                "kind": "album",
                "album_id": seed_album.album_id,
                "album": seed_album.album,
                "album_artist": seed_album.album_artist,
                "recent": recent,
            },
        )


def _genre_top_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """Best-selling right now within a genre the user actually listens to."""
    for genre in profile.top_genres:
        yield Seed(
            target={"tag": genre, "slice": "top"},
            why=f"You've been deep in {genre} lately; this is near the top of the pile.",
            seed_data={"kind": "genre", "genre": genre},
        )


def _best_seller_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """The chart pick, with no personal claim attached.

    Deliberately un-personalised, and the copy says so: dressing a chart up as a
    taste match is the one thing the brand guardrails forbid outright. It is also
    the criterion that carries a thin profile, since it needs no history at all.
    """
    yield Seed(
        target={"tag": None, "slice": "top"},
        why="Selling fast on Bandcamp right now.",
        seed_data={"kind": "chart"},
    )


def _old_album_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """Older records inside a genre the user likes.

    Uses ``slice=rand`` rather than the discover *time* facet: that facet is a
    six-week recency window describing when an item surfaced on Bandcamp, not
    when it was released (KAMP-644). ``slice=top`` skews overwhelmingly to the
    current year, so only the random slice reaches back. The age filter is
    applied client-side on ``release_date``.
    """
    for genre in profile.top_genres[:3]:
        yield Seed(
            target={"tag": genre, "slice": "rand", "size": 60},
            why=f"An older {genre} record — the kind that turns up at the back.",
            seed_data={"kind": "genre_old", "genre": genre},
        )


def _favorite_artist_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """More from a band the user has already favourited."""
    for artist in profile.favorite_artists:
        yield Seed(
            target=artist.artist_page,
            why=f"Another one from {artist.name}, who you already know you like.",
            seed_data={"kind": "artist", "artist": artist.name},
        )


REGISTRY: tuple[Criterion, ...] = (
    Criterion(
        key="also_like",
        surface=SURFACE_ALBUM_RECS,
        endpoint_class=ALBUM_PAGE,
        seeds=_also_like_seeds,
        label="filed next to something you played",
    ),
    Criterion(
        key="genre_top",
        surface=SURFACE_DISCOVER,
        endpoint_class=DISCOVER_API,
        seeds=_genre_top_seeds,
        label="top of a genre you listen to",
    ),
    Criterion(
        key="best_seller",
        surface=SURFACE_DISCOVER,
        endpoint_class=DISCOVER_API,
        seeds=_best_seller_seeds,
        label="selling fast right now",
    ),
    Criterion(
        key="older_than_ten",
        surface=SURFACE_DISCOVER,
        endpoint_class=DISCOVER_API,
        seeds=_old_album_seeds,
        label="an older record in your genres",
    ),
    Criterion(
        key="favorite_artist",
        surface=SURFACE_DISCOGRAPHY,
        endpoint_class=ARTIST_PAGE,
        seeds=_favorite_artist_seeds,
        label="more from a favourite band",
    ),
)


def criteria_for(profile: SeedProfile) -> list[Criterion]:
    """The criteria worth running for *profile*, in the order they should run.

    A thin profile (a brand-new library) yields no seeds for anything
    personalised, so only the chart criterion survives — which is exactly the
    intended fallback rather than an empty crate.
    """
    return [c for c in REGISTRY if any(True for _ in c.seeds(profile))]
