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
import time as _time
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

# How long a favourite has to have gone unplayed before the clerk remarks on it
# (KAMP-658). Six months: long enough that "you have not put it on in a while" is
# true rather than pedantic, short enough that a real library has some.
_DORMANT_SECS = 183 * 86400


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


def seed_dimension(seed_data: dict[str, Any]) -> str | None:
    """What a crate can only stand so much of, or None if the seed shares nothing.

    A crate read narrower than the library it came from (KAMP-665): three records
    off one album page, and one genre covering both discover criteria. Preventing
    that needs a name for the thing being repeated, and the seeds already carry
    it — ``seed_data`` exists to explain a pick, and "what explains it" and "what
    it must not duplicate" turn out to be the same question.

    Read off the provenance rather than added as a field, which is what makes it
    work ACROSS criteria: ``genre_top`` says ``kind='genre'`` and
    ``older_than_ten`` says ``kind='genre_old'``, but both name a genre, so both
    produce the same key and cannot both take Rock.

    Case- and space-folded, because these are tags typed by hundreds of different
    labels and "Dub Techno" is not a second genre.

    **None means "never excluded"**, not "excluded from everything". The chart
    seed carries no personal claim and there is exactly one of it; giving it an
    empty-string key would make it collide with itself and drop the criterion
    from every crate after the first.
    """
    for field_name in ("genre", "artist", "album_id", "label"):
        value = seed_data.get(field_name)
        if value in (None, ""):
            continue
        # Namespaced so an artist called "Rock" is not the genre Rock. `genre` is
        # deliberately NOT namespaced per criterion -- the whole point is that the
        # two genre criteria collide with each other.
        kind = "genre" if field_name == "genre" else field_name
        return f"{kind}:{str(value).strip().casefold()}"
    return None


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
    now = _time.time()
    seen: set[int] = set()
    for seed_album in [*profile.recent_albums, *profile.favorite_albums]:
        if seed_album.album_id in seen:
            continue
        seen.add(seed_album.album_id)
        recent = seed_album.album_id in profile.recent_album_ids
        # A favourite gone quiet for half a year is a different claim from a
        # favourite in general, and it is the more interesting one — the clerk
        # noticing what has slipped off your turntable rather than what is on it
        # (KAMP-658). Folded in here rather than added as its own criterion: this
        # selector ALREADY reaches those albums via favorite_albums, so a second
        # criterion would collide with this one on the album_id seed dimension
        # and spend an album-page request to relabel a card it would have
        # produced anyway.
        #
        # Never played at all is deliberately NOT this line: "you have not put it
        # on in a while" is false for a record that has never been on.
        dormant = (
            not recent
            and seed_album.last_played_at is not None
            and now - seed_album.last_played_at >= _DORMANT_SECS
        )
        if recent:
            why = f"Filed next to {seed_album.album}, which you played recently."
        elif dormant:
            why = f"You have not put {seed_album.album} on in a while — this sits beside it."
        else:
            why = f"You favourited {seed_album.album} — this sits beside it."
        yield Seed(
            target=seed_album.album_url,
            why=why,
            seed_data={
                "kind": "album",
                "album_id": seed_album.album_id,
                "album": seed_album.album,
                "album_artist": seed_album.album_artist,
                "recent": recent,
                "dormant": dormant,
            },
        )


def _genre_top_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """Selling well right now within a genre the user actually listens to.

    Two seeds per genre, not one (KAMP-661). ``slice=top`` for the same tag
    returns the same twenty albums next week, so a criterion that only ever asks
    for it contributes the same records to every crate forever. ``rand`` reaches a
    different part of the same catalogue at identical request cost — the trick
    ``_old_album_seeds`` already relies on to get past the current year's
    releases.

    Emitting both as ordinary seeds means the rotation in ``_run_criterion``
    cycles them for free: no slice-picking logic anywhere, and the copy stays
    honest because each variant carries its own ``why``.
    """
    for genre in profile.top_genres:
        yield Seed(
            target={"tag": genre, "slice": "top"},
            why=f"You've been deep in {genre} lately; this is near the top of the pile.",
            seed_data={"kind": "genre", "genre": genre},
        )
        yield Seed(
            target={"tag": genre, "slice": "rand"},
            why=f"Pulled at random from the {genre} racks.",
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
    """More from a band the user already knows they like.

    Favourites first, then — once those are exhausted — artists they simply play
    a lot without ever having starred anything (KAMP-658). The fall-through is
    folded in here rather than made a criterion of its own: both would read the
    same discography surface and emit the same `artist` seed dimension, so a
    separate row would only have contended for the four-request artist-page
    budget with this one while saying nearly the same thing.

    The two `why` lines are deliberately different claims. Starring something and
    playing it are different acts, and the card must not report one as the other.
    """
    seen: set[str] = set()
    for artist in profile.favorite_artists:
        seen.add(artist.name.casefold())
        yield Seed(
            target=artist.artist_page,
            why=f"Another one from {artist.name}, who you already know you like.",
            seed_data={"kind": "artist", "artist": artist.name},
        )
    for artist in profile.played_artists:
        if artist.name.casefold() in seen:
            continue
        seen.add(artist.name.casefold())
        yield Seed(
            target=artist.artist_page,
            why=f"You keep going back to {artist.name}. Here is another of theirs.",
            seed_data={"kind": "artist", "artist": artist.name},
        )


def _purchase_anniversary_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """Album pages of records bought around this time last year.

    A real anniversary, not a proxy: `added_at` is Bandcamp's own `purchased`
    field rather than when kamp synced or when the files landed on disk. The
    window is applied by the profile builder, so this stays a pure read.
    """
    for seed_album in profile.anniversary_albums:
        yield Seed(
            target=seed_album.album_url,
            why=f"You bought {seed_album.album} about a year ago.",
            seed_data={
                "kind": "album",
                "album_id": seed_album.album_id,
                "album": seed_album.album,
                "album_artist": seed_album.album_artist,
            },
        )


def _lone_album_artist_seeds(profile: SeedProfile) -> Iterable[Seed]:
    """The artist you play constantly and own exactly one record by.

    A gap in the shelf rather than a taste match, which is why it earns its own
    criterion where the play-time fall-through above does not: the claim is about
    what is MISSING, and that is a different sentence from "another one from a
    band you like".

    `owned_count` counts albums in the Bandcamp collection, so the copy says
    "here" rather than claiming to know the user's whole shelf — a record bought
    somewhere else would make a flat "you only own one" false.
    """
    for artist in profile.played_artists:
        if artist.owned_count != 1:
            continue
        yield Seed(
            target=artist.artist_page,
            why=f"You play {artist.name} a lot and have just the one here.",
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
    # Appended, never inserted: tests index REGISTRY[0] positionally, and
    # criteria_for() preserves this order for gather.
    Criterion(
        key="lone_album_artist",
        surface=SURFACE_DISCOGRAPHY,
        endpoint_class=ARTIST_PAGE,
        seeds=_lone_album_artist_seeds,
        label="you only have the one by them",
    ),
    Criterion(
        key="purchase_anniversary",
        surface=SURFACE_ALBUM_RECS,
        endpoint_class=ALBUM_PAGE,
        seeds=_purchase_anniversary_seeds,
        label="a year to the week since you bought it",
    ),
)


def criteria_for(profile: SeedProfile) -> list[Criterion]:
    """The criteria worth running for *profile*, in the order they should run.

    A thin profile (a brand-new library) yields no seeds for anything
    personalised, so only the chart criterion survives — which is exactly the
    intended fallback rather than an empty crate.
    """
    return [c for c in REGISTRY if any(True for _ in c.seeds(profile))]
