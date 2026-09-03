"""Criteria registry and BandcampDiscoverySource tests (KAMP-647).

No network: a fake session returns canned bodies, and the real captured fixtures
supply the markup, so these exercise the actual parsers rather than mocks of them.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from kamp_daemon.bandcamp_ratelimit import BandcampGovernor
from kamp_daemon.discovery import (
    ALBUM_PAGE,
    ARTIST_PAGE,
    DISCOVER_API,
    FANCOLLECTION,
    PREVIEW,
    SAVE_REMOTE,
    Candidate,
    SeedProfile,
    SimpleBudget,
    crate_budget,
)
from kamp_daemon.discovery_criteria import (
    REGISTRY,
    Criterion,
    Seed,
    _genre_top_seeds,
    criteria_for,
    seed_dimension,
)
from kamp_daemon.discovery_sources import (
    BandcampDiscoverySource,
    RateLimitedError,
)
from kamp_core.library import SeedAlbum, SeedArtist

FIXTURES = Path(__file__).parent / "fixtures" / "discovery"


def _fixture(name: str) -> str:
    for suffix in (".html.gz", ".json.gz"):
        path = FIXTURES / f"{name}{suffix}"
        if path.exists():
            return gzip.decompress(path.read_bytes()).decode()
    pytest.skip(f"fixture {name} not captured")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def wait(self, timeout: float) -> bool:
        self.t += timeout
        return False


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class FakeSession:
    """Records requests and replays canned bodies.

    ``post_bodies`` replays a *sequence* where the crumb-retry path needs the
    second answer to differ from the first; ``post_body`` is the single-answer
    shorthand. Same for ``get_bodies`` / ``get_body``.
    """

    def __init__(
        self,
        get_body: str = "",
        post_body: str = "",
        *,
        post_bodies: list[str] | None = None,
        post_statuses: list[int] | None = None,
    ) -> None:
        self.get_body = get_body
        self.post_body = post_body
        self.post_bodies = post_bodies
        self.post_statuses = post_statuses
        self.gets: list[str] = []
        self.posts: list[tuple[str, Any]] = []
        self.post_forms: list[dict[str, Any]] = []
        self.post_headers: list[dict[str, str]] = []
        self.get_status = 200
        self.post_status = 200

    def get(self, url: str, timeout: int = 30) -> FakeResponse:
        self.gets.append(url)
        return FakeResponse(self.get_body, self.get_status)

    def post(
        self,
        url: str,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> FakeResponse:
        n = len(self.posts)
        self.posts.append((url, json if json is not None else data))
        if data is not None:
            self.post_forms.append(dict(data))
        self.post_headers.append(dict(headers or {}))
        body = (
            self.post_bodies[min(n, len(self.post_bodies) - 1)]
            if self.post_bodies
            else self.post_body
        )
        status = (
            self.post_statuses[min(n, len(self.post_statuses) - 1)]
            if self.post_statuses
            else self.post_status
        )
        return FakeResponse(body, status)


def _source(session: FakeSession) -> BandcampDiscoverySource:
    return BandcampDiscoverySource(
        session, governor=BandcampGovernor(clock=FakeClock())
    )


def _profile(**kw: Any) -> SeedProfile:
    return SeedProfile(**kw)


def _album_seed(
    album_id: int = 1,
    url: str = "https://a.bandcamp.com/album/x",
    last_played_at: float | None = None,
):
    return SeedAlbum(
        album_id=album_id,
        album_artist="Artist",
        album="Album",
        album_url=url,
        tralbum_id="111",
        last_played_at=last_played_at,
    )


# ---------------------------------------------------------------------------
# The registry — one parametrized test covers the AC for every criterion
# ---------------------------------------------------------------------------


RICH_PROFILE = SeedProfile(
    recent_album_ids={1},
    recent_albums=[_album_seed()],
    favorite_album_ids={2},
    favorite_albums=[_album_seed(2, "https://b.bandcamp.com/album/y")],
    favorite_artists=[
        SeedArtist(name="Four Tet", artist_page="https://fourtet.bandcamp.com/music")
    ],
    # owned_count=1 so the lone-album criterion has something; a second artist
    # with more than one keeps that filter honest rather than vacuous.
    played_artists=[
        SeedArtist(
            name="Loraine James",
            artist_page="https://lorainejames.bandcamp.com/music",
            owned_count=1,
            play_time=9000.0,
        ),
        SeedArtist(
            name="Four Tet",
            artist_page="https://fourtet.bandcamp.com/music",
            owned_count=4,
            play_time=8000.0,
        ),
    ],
    anniversary_albums=[_album_seed(9, "https://c.bandcamp.com/album/z")],
    top_artists=["Four Tet"],
    top_genres=["ambient", "dub techno"],
    labels=["Ghostly"],
)


class TestCriteriaRegistry:
    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_produces_seeds_with_provenance(self, criterion: Criterion) -> None:
        """Every criterion must name what produced it — the epic's core promise is
        that each pick explains itself, so a seed with no attribution is invalid."""
        seeds = list(criterion.seeds(RICH_PROFILE))
        assert seeds, f"{criterion.key} produced no seeds from a rich profile"
        for seed in seeds:
            assert seed.why.strip()
            assert seed.seed_data.get("kind")
            assert seed.target

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_thin_profile_never_raises(self, criterion: Criterion) -> None:
        """A brand-new library is the common first run, not an edge case."""
        list(criterion.seeds(SeedProfile()))

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_endpoint_class_is_funded(self, criterion: Criterion) -> None:
        """crate_budget denies unknown classes, so an undeclared one would return
        empty forever and look exactly like parser drift."""
        assert crate_budget().allow(criterion.endpoint_class) is True

    def test_thin_profile_still_yields_the_chart_criterion(self) -> None:
        """The un-personalised fallback: a new user gets a crate, not an apology."""
        keys = [c.key for c in criteria_for(SeedProfile())]
        assert keys == ["best_seller"]

    def test_rich_profile_runs_several_criteria(self) -> None:
        # Raised with the registry (KAMP-658). Left at 4 it would stay green while
        # half the criteria produced nothing, which is the opposite of its job.
        assert len(criteria_for(RICH_PROFILE)) >= 6

    def test_the_lone_album_criterion_skips_artists_you_own_several_by(self) -> None:
        """The claim is about a gap on the shelf, so an artist with four albums
        in the collection must not produce "you have just the one here"."""
        lone = next(c for c in REGISTRY if c.key == "lone_album_artist")
        names = {s.seed_data["artist"] for s in lone.seeds(RICH_PROFILE)}
        assert names == {"Loraine James"}

    def test_the_artist_criterion_falls_through_to_what_you_play(self) -> None:
        """Favourites first, then artists merely played a lot — and the two say
        different things, because starring and playing are different acts."""
        fav = next(c for c in REGISTRY if c.key == "favorite_artist")
        seeds = list(fav.seeds(RICH_PROFILE))
        whys = {s.seed_data["artist"]: s.why for s in seeds}
        assert "already know you like" in whys["Four Tet"]
        assert "keep going back to" in whys["Loraine James"]

    def test_a_favourite_is_not_offered_twice_by_the_fall_through(self) -> None:
        """Four Tet is in both lists; the artist page must be seeded once."""
        fav = next(c for c in REGISTRY if c.key == "favorite_artist")
        names = [s.seed_data["artist"] for s in fav.seeds(RICH_PROFILE)]
        assert names.count("Four Tet") == 1

    def test_also_like_dedupes_an_album_that_is_both_recent_and_favourite(
        self,
    ) -> None:
        same = _album_seed(7)
        profile = SeedProfile(
            recent_album_ids={7}, recent_albums=[same], favorite_albums=[same]
        )
        seeds = list(REGISTRY[0].seeds(profile))
        assert len(seeds) == 1

    def test_recent_and_favourite_seeds_say_different_things(self) -> None:
        """The clerk card must not claim you played something you only starred."""
        recent = SeedProfile(recent_album_ids={1}, recent_albums=[_album_seed(1)])
        fav = SeedProfile(favorite_albums=[_album_seed(2)])
        assert "recently" in list(REGISTRY[0].seeds(recent))[0].why
        assert "favourited" in list(REGISTRY[0].seeds(fav))[0].why

    def test_a_favourite_gone_quiet_gets_its_own_line(self) -> None:
        """KAMP-658's "clerk remembers". Folded into also_like rather than made a
        criterion of its own, because this selector already reaches these albums
        through favorite_albums."""
        import time as _t

        stale = _album_seed(3, last_played_at=_t.time() - 200 * 86400)
        seeds = list(REGISTRY[0].seeds(SeedProfile(favorite_albums=[stale])))
        assert "not put" in seeds[0].why
        assert seeds[0].seed_data["dormant"] is True

    def test_a_favourite_played_last_month_is_not_called_dormant(self) -> None:
        import time as _t

        warm = _album_seed(4, last_played_at=_t.time() - 30 * 86400)
        seeds = list(REGISTRY[0].seeds(SeedProfile(favorite_albums=[warm])))
        assert "favourited" in seeds[0].why
        assert seeds[0].seed_data["dormant"] is False

    def test_a_favourite_never_played_is_not_called_dormant(self) -> None:
        """ "You have not put it on in a while" is false for a record that has
        never been on. Never-played reads as an ordinary favourite."""
        seeds = list(REGISTRY[0].seeds(SeedProfile(favorite_albums=[_album_seed(5)])))
        assert "favourited" in seeds[0].why
        assert seeds[0].seed_data["dormant"] is False


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


class TestGatherAgainstFixtures:
    def test_also_like_produces_candidates_with_identity_and_provenance(self) -> None:
        session = FakeSession(get_body=_fixture("album_page_with_recs"))
        profile = SeedProfile(recent_album_ids={1}, recent_albums=[_album_seed()])
        found = _source(session).gather(profile, crate_budget())

        assert found, "the captured album page should yield recommendations"
        for candidate in found:
            assert candidate.provider == "bandcamp"
            assert candidate.provider_item_id.isdigit()
            assert candidate.item_url.startswith("https://")
            assert candidate.criterion == "also_like"
            assert candidate.why
            assert candidate.seed["album"] == "Album"

    def test_discover_candidates_come_back_parsed(self) -> None:
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        profile = SeedProfile(top_genres=["ambient"])
        found = _source(session).gather(profile, crate_budget())
        assert found
        assert {c.criterion for c in found} <= {
            "genre_top",
            "best_seller",
            "older_than_ten",
        }

    def test_discover_payload_matches_the_documented_shape(self) -> None:
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        _source(session).gather(SeedProfile(top_genres=["ambient"]), crate_budget())
        _, payload = session.posts[0]
        assert payload["tag_norm_names"] == ["ambient"]
        assert payload["slice"] == "top"
        assert payload["include_result_types"] == ["a"]

    def test_display_genres_are_sent_as_bandcamp_slugs(self) -> None:
        """The profile carries "Indie Rock" so the clerk card can say it; the API
        needs "indie-rock" or it answers with an empty set that looks like a bug."""
        session = FakeSession(post_body='{"results": []}')
        _source(session).gather(SeedProfile(top_genres=["Indie Rock"]), crate_budget())
        _, payload = session.posts[0]
        assert payload["tag_norm_names"] == ["indie-rock"]

    def test_owned_and_wishlisted_results_are_excluded(self) -> None:
        """Bandcamp does the exclusion for us on this surface; honour it."""
        payload = {
            "results": [
                {
                    "item_id": 1,
                    "item_url": "https://a.bandcamp.com/album/owned",
                    "band_name": "A",
                    "title": "Owned",
                    "is_owned": True,
                },
                {
                    "item_id": 2,
                    "item_url": "https://b.bandcamp.com/album/fresh",
                    "band_name": "B",
                    "title": "Fresh",
                },
            ]
        }
        session = FakeSession(post_body=json.dumps(payload))
        found = _source(session).gather(SeedProfile(top_genres=["x"]), crate_budget())
        assert [c.title for c in found] == ["Fresh"]

    def test_old_album_criterion_filters_on_release_year(self) -> None:
        """The discover time facet is a recency window, so age is filtered here."""
        payload = {
            "results": [
                {
                    "item_id": 1,
                    "item_url": "https://a.bandcamp.com/album/new",
                    "title": "New",
                    "release_date": "2025-01-01 00:00:00 UTC",
                },
                {
                    "item_id": 2,
                    "item_url": "https://b.bandcamp.com/album/old",
                    "title": "Old",
                    "release_date": "2009-01-01 00:00:00 UTC",
                },
            ]
        }
        session = FakeSession(post_body=json.dumps(payload))
        source = _source(session)
        # Only the old-album criterion, so the filter is unambiguous.
        found = source._run_criterion(
            next(c for c in REGISTRY if c.key == "older_than_ten"),
            SeedProfile(top_genres=["ambient"]),
            crate_budget(),
        )
        assert [c.title for c in found] == ["Old"]

    def test_candidates_on_custom_domains_are_dropped(self) -> None:
        """We could never fetch art or a preview for them in a packaged build."""
        payload = {
            "results": [
                {
                    "item_id": 1,
                    "item_url": "https://music.example.com/album/x",
                    "title": "Custom",
                },
                {
                    "item_id": 2,
                    "item_url": "https://ok.bandcamp.com/album/y",
                    "title": "Fine",
                },
            ]
        }
        session = FakeSession(post_body=json.dumps(payload))
        found = _source(session).gather(SeedProfile(top_genres=["x"]), crate_budget())
        assert [c.title for c in found] == ["Fine"]

    def test_albums_the_user_already_owns_are_dropped(self) -> None:
        """Only the discover surface reports ownership itself. A favourite artist's
        discography is precisely where owned records cluster, so without this the
        crate recommends the user their own collection — which a first end-to-end
        run against the real library did.

        The owned set comes from the profile's purchase_dates, so no database
        access is needed inside the provider.
        """
        html = (
            '<div id="music-grid">'
            '<li data-item-id="album-111"><a href="/album/owned">'
            '<p class="title">Already Yours</p></a></li>'
            '<li data-item-id="album-222"><a href="/album/new">'
            '<p class="title">Not Yet</p></a></li>'
            "</div>"
        )
        session = FakeSession(get_body=html)
        profile = SeedProfile(
            favorite_artists=[
                SeedArtist(name="Band", artist_page="https://band.bandcamp.com/music")
            ],
            purchase_dates={"111": 1.0},
        )
        found = _source(session).gather(profile, crate_budget())
        assert [c.title for c in found] == ["Not Yet"]

    def test_discography_candidates_take_their_artist_from_the_seed(self) -> None:
        """The grid carries no artist name; every entry belongs to the page."""
        html = (
            '<div id="music-grid">'
            '<li data-item-id="album-9"><a href="/album/x">'
            '<p class="title">Record</p></a></li>'
            "</div>"
        )
        profile = SeedProfile(
            favorite_artists=[
                SeedArtist(
                    name="Frankie Rose", artist_page="https://fr.bandcamp.com/music"
                )
            ]
        )
        found = _source(FakeSession(get_body=html)).gather(profile, crate_budget())
        assert found[0].artist == "Frankie Rose"
        assert found[0].title == "Record"

    def test_duplicate_candidates_are_deduped_across_criteria(self) -> None:
        """~15% of recommendations recur across seeds."""
        payload = {
            "results": [
                {
                    "item_id": 99,
                    "item_url": "https://a.bandcamp.com/album/same",
                    "title": "Same",
                }
            ]
        }
        session = FakeSession(post_body=json.dumps(payload))
        profile = SeedProfile(top_genres=["a", "b"])
        found = _source(session).gather(profile, crate_budget())
        assert len(found) == 1


class TestSeedDimension:
    """KAMP-665: what a crate can have too much of.

    A crate took three records off one album page and covered two criteria with
    the same genre. The dimension is the thing that must not repeat — read from
    the provenance a seed already carries rather than a new field, so it
    generalises past genre to artist and album for free.
    """

    def test_a_genre_seed_is_keyed_on_its_genre(self) -> None:
        """Both discover criteria read top_genres, so both must key the same way
        or the exclusion between them cannot work."""
        top = seed_dimension({"kind": "genre", "genre": "Rock"})
        old = seed_dimension({"kind": "genre_old", "genre": "Rock"})
        assert top == old, "genre_top and older_than_ten must collide on Rock"

    def test_case_and_spacing_do_not_defeat_it(self) -> None:
        """'Dub Techno' and 'dub techno' are one genre wearing two hats — taste
        signals come from tags typed by hundreds of different labels."""
        assert seed_dimension({"kind": "genre", "genre": " Dub Techno "}) == (
            seed_dimension({"kind": "genre_old", "genre": "dub techno"})
        )

    def test_artists_and_albums_have_their_own_dimensions(self) -> None:
        assert seed_dimension({"kind": "artist", "artist": "Four Tet"}) is not None
        assert seed_dimension({"kind": "album", "album_id": 7}) is not None
        assert seed_dimension({"kind": "artist", "artist": "Four Tet"}) != (
            seed_dimension({"kind": "album", "album_id": 7})
        )

    def test_a_seed_with_nothing_to_share_has_no_dimension(self) -> None:
        """The chart carries no personal claim and there is only one of it.

        None means "never excluded" rather than "excluded from everything" — an
        empty-string key would make the single chart seed collide with itself and
        the criterion would vanish from every crate after the first.
        """
        assert seed_dimension({"kind": "chart"}) is None
        assert seed_dimension({}) is None
        assert seed_dimension({"kind": "genre", "genre": ""}) is None


class TestSpreadWithinACrate:
    """KAMP-665: one criterion should not take everything from one seed."""

    def test_a_criterion_reads_more_than_one_seed(self) -> None:
        """It used to stop at the first productive seed, so three of a crate's
        records could come off a single album page."""
        session = FakeSession(get_body=_fixture("album_page_with_recs"))
        profile = SeedProfile(
            recent_album_ids={1, 2, 3},
            recent_albums=[
                _album_seed(album_id=i, url=f"https://a{i}.bandcamp.com/album/x")
                for i in (1, 2, 3)
            ],
        )
        _source(session).gather(profile, crate_budget(), {})
        assert len(session.gets) >= 2, "still stopping at the first productive seed"

    def test_the_spread_stays_inside_the_budget(self) -> None:
        """Variety is bought from the allowance, never from more of it.

        These endpoints are the scarce resource — KAMP-637/639 are both about a
        crate build earning a 429 that cascades account-wide — so the guard is
        that the spend never exceeds what crate_budget() funds.
        """
        budget = crate_budget()
        session = FakeSession(
            get_body=_fixture("album_page_with_recs"),
            post_body=_fixture("discover_web_ambient_top"),
        )
        _source(session).gather(RICH_PROFILE, budget, {})

        for endpoint_class, cap in budget.limits.items():
            assert budget.spent.get(endpoint_class, 0) <= cap, endpoint_class
        # And the collection endpoint is never touched at all — funded at zero as
        # a tripwire rather than a limit.
        assert budget.spent.get(FANCOLLECTION, 0) == 0

    def test_one_criterion_cannot_eat_a_shared_class_allowance(self) -> None:
        """Three criteria sit on DISCOVER_API's six requests.

        Without a per-criterion seed cap the first of them would spend the lot on
        its own seed list — genre_top alone offers twenty seeds against a profile
        of ten genres — and the other two would find nothing left, trading one
        kind of narrowness for another.

        Asserted on the REQUESTS, not the candidates: every seed replays the same
        canned body here, so gather's id-dedupe would credit all of them to
        whichever criterion ran first and the candidate list would say nothing
        about who got to spend.
        """
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        budget = crate_budget()
        profile = SeedProfile(top_genres=[f"g{i}" for i in range(10)])
        _source(session).gather(profile, budget, {})

        spent = budget.spent[DISCOVER_API]
        assert spent <= budget.limits[DISCOVER_API], "spilled past the class cap"
        assert spent >= 3, "at least one request each for the three criteria"
        # The chart carries no tag, so its request is the one identifiable by
        # shape — proof the criteria after genre_top still had budget to spend.
        assert any(
            not p[1]["tag_norm_names"] for p in session.posts
        ), "genre_top consumed the whole discover allowance"


class TestGenreExclusionAcrossCriteria:
    def test_the_two_genre_criteria_do_not_take_the_same_genre(self) -> None:
        """Both read top_genres and both started at its head, so one genre
        covered two criteria in the same crate — measured on a real library."""
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        profile = SeedProfile(top_genres=["rock", "metal", "dub techno"])
        _source(session).gather(profile, crate_budget(), {})

        tags = [
            p[1]["tag_norm_names"] for p in session.posts if p[1].get("tag_norm_names")
        ]
        flat = [t[0] for t in tags if t]
        assert len(flat) == len(set(flat)), f"a genre was fetched twice: {flat}"

    def test_a_skipped_genre_is_not_owed_another_turn(self) -> None:
        """The offset advances past a skipped seed, and that is deliberate.

        A dimension only enters the used set when the seed that claimed it
        actually produced records, so a skip always means "the crate already has
        records for this genre, from the other criterion". The genre was covered;
        this criterion simply was not the one to cover it. Parking the offset
        behind it would leave this criterion permanently one step behind whichever
        one runs first.
        """
        source = _source(FakeSession())
        criterion = Criterion(
            key="fake",
            surface="fake",
            endpoint_class=ALBUM_PAGE,
            seeds=lambda _p: [
                Seed(target=f"https://x/{i}", why="", seed_data={"genre": g})
                for i, g in enumerate(["rock", "metal", "jazz", "funk"])
            ],
            label="fake",
        )
        state: dict[str, Any] = {}
        source._run_seed = lambda *a, **k: [MagicMock()]  # type: ignore[method-assign]
        source._run_criterion(
            criterion,
            SeedProfile(),
            crate_budget(),
            set(),
            state,
            used={"genre:rock"},
        )
        # rock skipped, then metal and jazz taken (the two-seed spread) — so the
        # next crate resumes at funk rather than re-reading any of them.
        assert state["seeds"]["fake"] == 3


class TestACrateReflectsTheLibrarysRange:
    """The user-visible complaint, end to end (KAMP-665).

    A user with 800 albums and six genres got a crate that looked like it knew
    one album and one genre. These run the real criteria against the real parsers
    with a fake session, so they fail if any layer stops spreading.
    """

    def test_a_library_dominated_by_one_genre_still_names_several(self) -> None:
        """Raw track count puts the broadest genre first and keeps it there.

        The ranking is right — Rock really is the biggest thing in that library —
        so the fix is not to rerank it but to stop both discover criteria starting
        at its head.
        """
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        # The shape of a real library: one enormous genre, then a long tail.
        profile = SeedProfile(top_genres=["rock", "metal", "dub techno", "ambient"])
        _source(session).gather(profile, crate_budget(), {})

        tags = {t[0] for _, p in session.posts if (t := p["tag_norm_names"])}
        assert len(tags) >= 2, f"the whole crate came from one genre: {tags}"

    def test_the_favourite_artist_criterion_reads_more_than_one_artist(self) -> None:
        """Both favorite_artist picks in a real crate were the same artist."""
        session = FakeSession(get_body=_fixture("artist_discography"))
        profile = SeedProfile(
            favorite_artists=[
                SeedArtist(
                    name=f"Band {i}", artist_page=f"https://b{i}.bandcamp.com/music"
                )
                for i in range(4)
            ]
        )
        _source(session).gather(profile, crate_budget(), {})
        assert len(set(session.gets)) >= 2, "one artist page supplied the lot"

    def test_consecutive_crates_change_which_artists_they_read(self) -> None:
        """The rotation from KAMP-661 applies per criterion, so favourite_artist
        gets it too — asserted rather than assumed, because the acceptance
        criteria name this case specifically and 'the mechanism is generic' is
        the kind of claim that is true right up until it is not."""
        session = FakeSession(get_body=_fixture("artist_discography"))
        profile = SeedProfile(
            favorite_artists=[
                SeedArtist(
                    name=f"Band {i}", artist_page=f"https://b{i}.bandcamp.com/music"
                )
                for i in range(4)
            ]
        )
        source = _source(session)
        state: dict[str, Any] = {}

        source.gather(profile, crate_budget(), state)
        first = set(session.gets)
        session.gets.clear()
        source.gather(profile, crate_budget(), state)
        assert not (first & set(session.gets)), "the same artists two crates running"

    def test_a_whole_crate_gather_stays_within_every_class_budget(self) -> None:
        """The guard on the whole story: variety comes out of the allowance.

        Asserted across a rich profile that exercises every criterion at once,
        because the allowance is per endpoint class and three criteria share
        DISCOVER_API's six — the interesting failure is one of them starving the
        others, not any single criterion overspending.
        """
        budget = crate_budget()
        session = FakeSession(
            get_body=_fixture("album_page_with_recs"),
            post_body=_fixture("discover_web_ambient_top"),
        )
        _source(session).gather(RICH_PROFILE, budget, {})
        assert budget.spent[ALBUM_PAGE] <= budget.limits[ALBUM_PAGE]
        assert budget.spent[DISCOVER_API] <= budget.limits[DISCOVER_API]
        assert budget.spent.get(ARTIST_PAGE, 0) <= budget.limits[ARTIST_PAGE]
        assert budget.spent.get(FANCOLLECTION, 0) == 0

    def test_no_criterion_is_starved_by_the_budget(self) -> None:
        """The failure the budget test above cannot see (KAMP-658).

        `gather` iterates criteria in registry order and stops asking once a class
        is exhausted, so a registry that outgrows its allowance starves whichever
        criteria come last — silently, and while the <= assertions above stay
        green. Two criteria per endpoint class times two seeds is exactly the
        allowance today; a third on either class breaks this, which is the point.

        Given ENOUGH GENRES on purpose. With only two, `older_than_ten` is skipped
        for a reason that is not starvation: `genre_top` claims both genre
        dimensions first and the KAMP-665 variety rule deliberately stops the
        second genre criterion reusing them. That is correct behaviour, and a test
        that could not tell it apart from budget exhaustion would be worse than no
        test at all.
        """
        budget = crate_budget()
        session = FakeSession(
            get_body=_fixture("album_page_with_recs"),
            post_body=_fixture("discover_web_ambient_top"),
        )
        profile = replace(
            RICH_PROFILE, top_genres=["ambient", "dub techno", "shoegaze", "dub"]
        )
        state: dict[str, Any] = {}
        _source(session).gather(profile, budget, state)

        wanted = {c.key for c in criteria_for(profile)}
        ran = set(state.get("seeds", {}))
        assert wanted <= ran, f"never got a request: {sorted(wanted - ran)}"


class TestRotationAndPagination:
    """KAMP-661: the reachable candidate space has to grow with use.

    Everything here asserts on what the source *asked for*, not on what came back
    — variety is a property of the requests, and a fake that returns the same body
    every time would make an output-based assertion pass for the wrong reason.
    """

    # A THIN profile throughout the pagination tests, deliberately. It yields
    # seeds for nothing but the chart, so exactly one criterion with exactly one
    # seed runs and rotation cannot move. Testing pagination against a profile
    # with genres conflates the two: rotation correctly advances to the `rand`
    # slice on the second crate, which is a different query with its own place in
    # the results, so the cursor legitimately starts over and the assertion fails
    # for a reason that is not a bug.
    def test_the_discover_cursor_is_carried_into_the_next_gather(self) -> None:
        """Page two, without a network.

        Before this, `"cursor": None` was hard-coded, so every crate for the life
        of the install re-asked for the first 20 rows of the same query.
        """
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        source = _source(session)
        state: dict[str, Any] = {}

        source.gather(SeedProfile(), crate_budget(), state)
        first = session.posts[0][1]["cursor"]
        session.posts.clear()
        source.gather(SeedProfile(), crate_budget(), state)
        second = session.posts[0][1]["cursor"]

        assert first is None, "the first ever request has no page to continue from"
        assert second, "the second gather did not continue where the first stopped"

    def test_an_empty_page_drops_the_cursor_rather_than_pinning_the_seed(self) -> None:
        """A cursor that has walked off the end must not strand the query there.

        Storing it unconditionally is the worse bug: that query would return
        nothing for the rest of the install, invisibly, which is a quieter version
        of the failure this story exists to remove.
        """
        state: dict[str, Any] = {}
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        _source(session).gather(SeedProfile(), crate_budget(), state)
        assert any(v for v in state.get("cursors", {}).values())

        dry = FakeSession(post_body='{"results": [], "cursor": "zzz"}')
        _source(dry).gather(SeedProfile(), crate_budget(), state)
        assert not any(v for v in state.get("cursors", {}).values())

    def test_consecutive_gathers_use_different_seeds_for_the_same_criterion(
        self,
    ) -> None:
        """Rotation, asserted on the URL fetched rather than on the candidates.

        FOUR albums, not two. KAMP-665 lets a criterion read two seeds per crate,
        so a two-album profile is fully consumed every time and there is nothing
        left to rotate — the offset wraps straight back to the head, correctly.
        Rotation is only observable once the list is longer than the spread.
        """
        session = FakeSession(get_body=_fixture("album_page_with_recs"))
        profile = SeedProfile(
            recent_album_ids={1, 2, 3, 4},
            recent_albums=[
                _album_seed(album_id=i, url=f"https://a{i}.bandcamp.com/album/x")
                for i in (1, 2, 3, 4)
            ],
        )
        source = _source(session)
        state: dict[str, Any] = {}

        source.gather(profile, crate_budget(), state)
        first = list(session.gets)
        session.gets.clear()
        source.gather(profile, crate_budget(), state)
        second = list(session.gets)

        assert first and second
        assert not (set(first) & set(second)), "a seed was re-read the very next crate"

    def test_rotation_advances_past_a_seed_that_produced_nothing(self) -> None:
        """Otherwise a dead seed at the head of the list is retried forever.

        Advancing only past PRODUCTIVE seeds looks right and is the trap: a seed
        that yields nothing — a deleted album, an artist page with one release —
        would sit at the head of every crate's fetch and the rotation would never
        begin.

        Driven through _run_criterion with a stubbed _run_seed rather than through
        gather: the point is precisely which seeds were TRIED, and a fake session
        cannot make the first fetch barren and the second fruitful.
        """
        source = _source(FakeSession())
        tried: list[Any] = []

        def fake_run_seed(criterion, seed, budget, owned, state=None):  # noqa: ANN001
            tried.append(seed.target)
            # Barren, barren, then a hit — so it stops on the third of three.
            return [MagicMock()] if len(tried) == 3 else []

        source._run_seed = fake_run_seed  # type: ignore[method-assign]
        criterion = Criterion(
            key="fake",
            surface="fake",
            endpoint_class=ALBUM_PAGE,
            seeds=lambda _p: [
                Seed(target=f"https://x/{i}", why="", seed_data={}) for i in range(3)
            ],
            label="fake",
        )
        state: dict[str, Any] = {}
        source._run_criterion(criterion, SeedProfile(), crate_budget(), set(), state)

        assert len(tried) == 3
        # Past all three, wrapping — not parked on the first barren one.
        assert state["seeds"]["fake"] == 0

        tried.clear()
        source._run_criterion(criterion, SeedProfile(), crate_budget(), set(), state)
        assert tried[0] == "https://x/0", "wrapped offset should restart the list"

    def test_genre_top_reaches_both_slices_over_successive_crates(self) -> None:
        """Same tag, same request cost, a different part of the catalogue.

        slice=top skews hard to the current year; alternating with rand is free
        variety that needs no new mechanism — the seed list carries both and
        rotation cycles them.
        """
        seeds = [
            s.target for s in _genre_top_seeds(SeedProfile(top_genres=["ambient"]))
        ]
        assert {s["slice"] for s in seeds} == {"top", "rand"}

    def test_a_criterion_with_no_seeds_is_not_a_division_by_zero(self) -> None:
        """`start = offset % len(seeds)` needs the guard above it.

        criteria_for() filters seedless criteria out before gather ever sees
        them, so this is only reachable directly — which is exactly why it is
        worth pinning rather than trusting the caller to keep filtering.
        """
        criterion = Criterion(
            key="empty",
            surface="fake",
            endpoint_class=ALBUM_PAGE,
            seeds=lambda _p: [],
            label="empty",
        )
        source = _source(FakeSession())
        assert (
            source._run_criterion(criterion, SeedProfile(), crate_budget(), set(), {})
            == []
        )

    def test_a_budget_stop_does_not_advance_past_seeds_never_tried(self) -> None:
        """The offset records where to RESUME, so it may only move over seeds that
        actually got a request. Advancing on the loop counter instead would skip
        whatever the budget cut off, and those seeds would never be read.

        Five seeds against a budget for two, so the expected offset is 2 — a
        number that is neither "none tried" nor "all tried", which is what makes
        the assertion mean something.
        """
        source = _source(FakeSession())
        budget = SimpleBudget(limits={ALBUM_PAGE: 2})
        criterion = Criterion(
            key="fake",
            surface="fake",
            endpoint_class=ALBUM_PAGE,
            seeds=lambda _p: [
                Seed(target=f"https://x/{i}", why="", seed_data={}) for i in range(5)
            ],
            label="fake",
        )

        def barren(_c, _s, b, _o, _st=None):  # noqa: ANN001, ANN202
            # Spends the budget the way a real fetch would, and finds nothing —
            # so the loop keeps going until the budget, not the results, stops it.
            b.consume(ALBUM_PAGE)
            return []

        source._run_seed = barren  # type: ignore[method-assign]
        state: dict[str, Any] = {}
        source._run_criterion(criterion, SeedProfile(), budget, set(), state)
        assert state["seeds"]["fake"] == 2

    def test_state_is_optional_so_every_existing_caller_still_works(self) -> None:
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        assert _source(session).gather(
            SeedProfile(top_genres=["ambient"]), crate_budget()
        )


class TestFetchPolicy:
    def test_unfetchable_seed_host_spends_no_request(self) -> None:
        session = FakeSession(get_body="<html></html>")
        source = _source(session)
        budget = crate_budget()
        body = source._fetch(ALBUM_PAGE, "https://music.example.com/album/x", budget)
        assert body is None
        assert session.gets == []
        assert budget.spent.get(ALBUM_PAGE, 0) == 0

    def test_dead_seed_is_skipped_not_raised(self) -> None:
        session = FakeSession(get_body="")
        session.get_status = 404
        source = _source(session)
        assert (
            source._fetch(ALBUM_PAGE, "https://a.bandcamp.com/album/x", crate_budget())
            is None
        )

    def test_429_stops_the_whole_gather(self) -> None:
        """One rate limit must not be rediscovered by every remaining criterion."""
        session = FakeSession(post_body="")
        session.post_status = 429
        source = _source(session)
        profile = SeedProfile(top_genres=["a", "b", "c"])
        found = source.gather(profile, crate_budget())
        assert found == []
        assert len(session.posts) == 1, "gather kept going after a 429"

    def test_429_is_reported_to_the_governor(self) -> None:
        governor = BandcampGovernor(clock=FakeClock())
        session = FakeSession(post_body="")
        session.post_status = 429
        source = BandcampDiscoverySource(session, governor=governor)
        with pytest.raises(RateLimitedError):
            source._fetch(
                DISCOVER_API, "https://bandcamp.com/api/x", crate_budget(), payload={}
            )
        assert governor.blocked_for(DISCOVER_API) > 0

    def test_exhausted_budget_spends_nothing(self) -> None:
        session = FakeSession(get_body="<html></html>")
        source = _source(session)
        budget = SimpleBudget(limits={ALBUM_PAGE: 0}, default_limit=0)
        assert (
            source._fetch(ALBUM_PAGE, "https://a.bandcamp.com/album/x", budget) is None
        )
        assert session.gets == []

    def test_shutdown_during_wait_aborts_without_requesting(self) -> None:
        class StoppingClock(FakeClock):
            def wait(self, timeout: float) -> bool:
                return True  # interrupted

        governor = BandcampGovernor(clock=StoppingClock())
        governor.report_429(ALBUM_PAGE)
        session = FakeSession(get_body="x")
        source = BandcampDiscoverySource(session, governor=governor)
        with pytest.raises(RateLimitedError):
            source._fetch(ALBUM_PAGE, "https://a.bandcamp.com/album/x", crate_budget())
        assert session.gets == []

    def test_network_error_is_not_fatal(self) -> None:
        class Boom(FakeSession):
            def get(self, url: str, timeout: int = 30) -> FakeResponse:
                raise OSError("network down")

        source = _source(Boom())
        assert (
            source._fetch(ALBUM_PAGE, "https://a.bandcamp.com/album/x", crate_budget())
            is None
        )

    def test_a_broken_criterion_does_not_break_the_crate(self, caplog) -> None:
        """genre_sources' best-effort contract, restated for criteria."""

        class Exploding(BandcampDiscoverySource):
            def _run_criterion(self, criterion, profile, budget):  # type: ignore[no-untyped-def]
                if criterion.key == "genre_top":
                    raise ValueError("boom")
                return []

        source = Exploding(FakeSession(), governor=BandcampGovernor(clock=FakeClock()))
        assert source.gather(RICH_PROFILE, crate_budget()) == []
        assert "best-effort" in caplog.text


class TestCapabilities:
    def test_preview_and_the_wishlist_write_are_both_offered(self) -> None:
        """SAVE_REMOTE was withheld until KAMP-653 on the belief that the relay
        could not send a form body. It can; the earlier verdict was measured
        against a spike helper that had no form path."""
        caps = _source(FakeSession()).capabilities
        assert PREVIEW in caps
        assert SAVE_REMOTE in caps

    def test_preview_resolves_an_mp3_from_the_album_page(self) -> None:
        import html as html_lib

        blob = {
            "trackinfo": [
                {
                    "track_num": 1,
                    "title": "One",
                    "file": {"mp3-128": "https://cdn/1.mp3"},
                }
            ]
        }
        html = f'<div data-tralbum="{html_lib.escape(json.dumps(blob), quote=True)}">'
        source = _source(FakeSession(get_body=html))
        stream = source.resolve_preview(
            Candidate(
                provider="bandcamp",
                provider_item_id="1",
                item_url="https://a.bandcamp.com/album/x",
            )
        )
        assert stream is not None
        assert stream.url == "https://cdn/1.mp3"
        assert stream.title == "One"

    def test_preview_returns_none_when_the_page_has_no_audio(self) -> None:
        source = _source(FakeSession(get_body="<html>nothing</html>"))
        assert (
            source.resolve_preview(
                Candidate(
                    provider="bandcamp",
                    provider_item_id="1",
                    item_url="https://a.bandcamp.com/album/x",
                )
            )
            is None
        )


def _album_html(tracks: list[dict[str, Any]], item_type: str = "album") -> str:
    import html as html_lib

    blob = {"item_type": item_type, "trackinfo": tracks}
    return f'<div data-tralbum="{html_lib.escape(json.dumps(blob), quote=True)}">'


def _candidate(url: str = "https://a.bandcamp.com/album/x") -> Candidate:
    return Candidate(provider="bandcamp", provider_item_id="1", item_url=url)


class TestPreviewTracks:
    def test_returns_every_playable_track_in_order(self) -> None:
        """One request buys the whole album, so next/prev costs nothing more."""
        html = _album_html(
            [
                {
                    "track_num": 1,
                    "title": "One",
                    "duration": 60.5,
                    "file": {"mp3-128": "https://cdn/1.mp3?ts=1000"},
                },
                {
                    "track_num": 2,
                    "title": "Two",
                    "duration": 90.0,
                    "file": {"mp3-128": "https://cdn/2.mp3?ts=1000"},
                },
            ]
        )
        tracks = _source(FakeSession(get_body=html)).preview_tracks(_candidate())
        assert [t.track_num for t in tracks] == [1, 2]
        assert [t.title for t in tracks] == ["One", "Two"]
        assert tracks[0].duration == 60.5

    def test_expiry_comes_from_the_urls_own_timestamp(self) -> None:
        """ts is when Bandcamp signed the URL — more accurate than fetch time,
        since the page itself may have been served from a cache."""
        html = _album_html(
            [{"track_num": 1, "file": {"mp3-128": "https://cdn/1.mp3?ts=1000000"}}]
        )
        tracks = _source(FakeSession(get_body=html)).preview_tracks(_candidate())
        assert tracks[0].expires_at == 1000000 + 86400

    def test_unreleased_tracks_are_skipped_not_fatal(self) -> None:
        """A pre-order album has tracks with no stream; the rest still play."""
        html = _album_html(
            [
                {"track_num": 1, "title": "Teaser", "file": {}},
                {
                    "track_num": 2,
                    "title": "Real",
                    "file": {"mp3-128": "https://cdn/2.mp3"},
                },
            ]
        )
        tracks = _source(FakeSession(get_body=html)).preview_tracks(_candidate())
        assert [t.title for t in tracks] == ["Real"]

    def test_single_track_page_is_numbered_one(self) -> None:
        """item_type='track' pages expose track_num=None (KAMP-526)."""
        html = _album_html(
            [
                {
                    "track_num": None,
                    "title": "Lone",
                    "file": {"mp3-128": "https://cdn/1.mp3"},
                }
            ],
            item_type="track",
        )
        tracks = _source(FakeSession(get_body=html)).preview_tracks(_candidate())
        assert tracks[0].track_num == 1

    def test_resolve_preview_is_the_first_of_the_list(self) -> None:
        """One parser, not two that can disagree."""
        # ts= pins expires_at so the two calls are comparable; without it the
        # fallback is time.time() and they differ by microseconds.
        html = _album_html(
            [
                {
                    "track_num": 1,
                    "title": "One",
                    "file": {"mp3-128": "https://cdn/1.mp3?ts=1000"},
                },
                {
                    "track_num": 2,
                    "title": "Two",
                    "file": {"mp3-128": "https://cdn/2.mp3?ts=1000"},
                },
            ]
        )
        source = _source(FakeSession(get_body=html))
        assert (
            source.resolve_preview(_candidate())
            == source.preview_tracks(_candidate())[0]
        )

    def test_a_custom_domain_is_refused_without_fetching(self) -> None:
        """item_url is remote data read back out of discovery_items."""
        session = FakeSession(get_body=_album_html([]))
        source = _source(session)
        assert (
            source.preview_tracks(_candidate("https://evil.example.com/album/x")) == []
        )
        assert session.gets == []


class TestPreviewNeverWaitsOnTheGovernor:
    """bandcamp_ratelimit documents itself as a non-playback tool.

    wait_turn blocks until a 60/120/300s cooldown expires, so a listener who
    clicked play would get a hang with nothing on screen. The outcome is still
    reported, so a 429 earned here makes the crate builder back off instead.
    """

    def test_a_cooldown_does_not_delay_a_click(self) -> None:
        governor = MagicMock()
        governor.blocked_for.return_value = 300.0
        html = _album_html([{"track_num": 1, "file": {"mp3-128": "https://cdn/1.mp3"}}])
        source = BandcampDiscoverySource(FakeSession(get_body=html), governor=governor)

        assert len(source.preview_tracks(_candidate())) == 1
        governor.wait_turn.assert_not_called()
        governor.report_ok.assert_called_once_with("album_page")

    def test_a_429_is_reported_and_raised(self) -> None:
        governor = MagicMock()
        session = FakeSession()
        session.get_status = 429
        source = BandcampDiscoverySource(session, governor=governor)
        with pytest.raises(RateLimitedError):
            source.preview_tracks(_candidate())
        governor.report_429.assert_called_once_with("album_page")
        governor.wait_turn.assert_not_called()


# ---------------------------------------------------------------------------
# Wishlist write (KAMP-653)
# ---------------------------------------------------------------------------


def _write_page(
    *,
    band_id: str | int | None = "692277828",
    selling_band_id: str | int | None = "237579501",
    is_wishlisted: bool | None = False,
    crumbs: dict[str, str] | None = None,
) -> str:
    """An album page carrying the three things the POST needs.

    Hand-built rather than captured: a real logged-in page embeds live CSRF
    crumbs and a fan_id, and this repository is public.
    """
    import html as html_lib

    current: dict[str, Any] = {}
    if band_id is not None:
        current["band_id"] = band_id
    if selling_band_id is not None:
        current["selling_band_id"] = selling_band_id
    tralbum = html_lib.escape(json.dumps({"id": 1, "current": current}), quote=True)

    fan_data = None if is_wishlisted is None else {"is_wishlisted": is_wishlisted}
    pagedata = html_lib.escape(json.dumps({"fan_tralbum_data": fan_data}), quote=True)

    if crumbs is None:
        crumbs = {
            "collect_item_cb": "|collect_item_cb|1754|abc=",
            "uncollect_item_cb": "|uncollect_item_cb|1754|def=",
        }
    crumb_attr = html_lib.escape(json.dumps(crumbs), quote=True)

    return (
        f'<meta id="js-crumbs-data" data-crumbs="{crumb_attr}">'
        f'<div id="pagedata" data-blob="{pagedata}"></div>'
        f'<script data-tralbum="{tralbum}"></script>'
    )


@pytest.fixture
def fan_id(monkeypatch: pytest.MonkeyPatch) -> int:
    """_get_fan_info is one authenticated GET; stub it. It is not what is under
    test here and has its own coverage in tests/test_bandcamp.py."""
    import kamp_daemon.bandcamp as bc

    monkeypatch.setattr(bc, "_get_fan_info", lambda session: (4346318, "fan"))
    return 4346318


class TestWishlistWrite:
    def test_add_posts_a_form_and_confirms_from_the_body(self, fan_id: int) -> None:
        session = FakeSession(get_body=_write_page(), post_body='{"ok":true}')
        assert _source(session).save_remote(_candidate()) is True

        url, _ = session.posts[0]
        assert url == "https://bandcamp.com/collect_item_cb"
        # data=, not json=: the identical call with a JSON body answers HTTP 200
        # carrying an InsistError about a missing crumb.
        assert session.post_forms[0] == {
            "fan_id": fan_id,
            "item_id": "1",
            # "album", not the discover API's "a"; the short form earns a bare 400.
            "item_type": "album",
            "band_id": "692277828",
            "crumb": "|collect_item_cb|1754|abc=",
        }

    def test_remove_uses_the_uncollect_endpoint_and_its_own_crumb(
        self, fan_id: int
    ) -> None:
        session = FakeSession(
            get_body=_write_page(is_wishlisted=True), post_body='{"ok":true}'
        )
        assert _source(session).unsave_remote(_candidate()) is True
        assert session.posts[0][0] == "https://bandcamp.com/uncollect_item_cb"
        assert session.post_forms[0]["crumb"] == "|uncollect_item_cb|1754|def="

    def test_origin_is_sent_and_referer_is_not(self, fan_id: int) -> None:
        """Bandcamp insists on an origin OR a referrer, but Chromium blocks a
        manually-set Referer on net.request (net::ERR_BLOCKED_BY_CLIENT). A
        Referer here would work in dev and fail in every packaged build."""
        session = FakeSession(get_body=_write_page(), post_body='{"ok":true}')
        _source(session).save_remote(_candidate())
        headers = session.post_headers[0]
        assert headers["Origin"] == "https://bandcamp.com"
        assert headers["X-Requested-With"] == "XMLHttpRequest"
        assert "Referer" not in headers

    def test_band_id_is_current_band_id_never_selling_band_id(
        self, fan_id: int
    ) -> None:
        """Sending selling_band_id returns HTTP 200 with {"ok":true} and does
        nothing — verified live. The wrong field is not a failure we would even
        notice at runtime, so it has to be got right here."""
        session = FakeSession(get_body=_write_page(), post_body='{"ok":true}')
        _source(session).save_remote(_candidate())
        assert session.post_forms[0]["band_id"] == "692277828"

    def test_a_page_without_a_band_id_refuses_rather_than_guessing(
        self, fan_id: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        """selling_band_id is right there and wrong. Falling back to it would
        report success for a record that never moved."""
        session = FakeSession(
            get_body=_write_page(band_id=None), post_body='{"ok":true}'
        )
        assert _source(session).save_remote(_candidate()) is False
        assert session.posts == []
        assert "refusing to guess" in caplog.text

    def test_a_200_carrying_an_error_body_is_a_failure(self, fan_id: int) -> None:
        """The trap that stranded an album on a real account: these endpoints
        answer 200 on failure, so the status is never the answer."""
        session = FakeSession(
            get_body=_write_page(),
            post_body='{"error":true,"ok":false,"exception":"InsistError: no crumb"}',
        )
        session.post_status = 200
        assert _source(session).save_remote(_candidate()) is False

    def test_a_stale_crumb_is_retried_once_with_the_fresh_one(
        self, fan_id: int
    ) -> None:
        session = FakeSession(
            get_body=_write_page(),
            post_bodies=[
                '{"error":"invalid_crumb","crumb":"|collect_item_cb|9999|new="}',
                '{"ok":true}',
            ],
            post_statuses=[403, 200],
        )
        assert _source(session).save_remote(_candidate()) is True
        assert len(session.posts) == 2
        assert session.post_forms[0]["crumb"] == "|collect_item_cb|1754|abc="
        assert session.post_forms[1]["crumb"] == "|collect_item_cb|9999|new="
        # The page is fetched once: the fresh crumb rides in on the error body.
        assert len(session.gets) == 1

    def test_the_crumb_retry_happens_at_most_once(self, fan_id: int) -> None:
        """A second invalid_crumb is not a crumb problem, and retrying forever
        would hammer the endpoint class closest to its rate limit."""
        session = FakeSession(
            get_body=_write_page(),
            post_bodies=['{"error":"invalid_crumb","crumb":"|c|9|new="}'],
            post_statuses=[403],
        )
        assert _source(session).save_remote(_candidate()) is False
        assert len(session.posts) == 2

    def test_a_non_crumb_failure_is_not_retried(self, fan_id: int) -> None:
        session = FakeSession(
            get_body=_write_page(), post_body='{"error":true,"ok":false}'
        )
        assert _source(session).save_remote(_candidate()) is False
        assert len(session.posts) == 1

    def test_already_wishlisted_is_a_silent_success_costing_no_post(
        self, fan_id: int
    ) -> None:
        """The page we had to fetch anyway already answered. Bandcamp agrees — a
        repeat collect_item_cb returns ok:true — so this saves a request rather
        than changing the outcome."""
        session = FakeSession(get_body=_write_page(is_wishlisted=True))
        assert _source(session).save_remote(_candidate()) is True
        assert session.posts == []

    def test_removing_something_already_absent_is_a_silent_success(
        self, fan_id: int
    ) -> None:
        session = FakeSession(get_body=_write_page(is_wishlisted=False))
        assert _source(session).unsave_remote(_candidate()) is True
        assert session.posts == []

    def test_an_unknown_wishlist_state_still_attempts_the_write(
        self, fan_id: int
    ) -> None:
        """None is not False. A page that cannot say must not short-circuit
        either direction — it means we could not tell, so do the work."""
        session = FakeSession(
            get_body=_write_page(is_wishlisted=None), post_body='{"ok":true}'
        )
        assert _source(session).save_remote(_candidate()) is True
        assert len(session.posts) == 1

    def test_a_crumbless_page_fails_without_posting(
        self, fan_id: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A logged-out page ships data-crumbs="{}" — the session has expired."""
        session = FakeSession(get_body=_write_page(crumbs={}))
        assert _source(session).save_remote(_candidate()) is False
        assert session.posts == []
        assert "session expired" in caplog.text

    def test_a_cooldown_refuses_immediately_without_any_request(
        self, fan_id: int
    ) -> None:
        """Checked, never waited on. wait_turn would hang a click for up to five
        minutes; unlike preview there is no partial answer worth giving, so this
        refuses and lets the UI say why."""
        governor = MagicMock()
        governor.blocked_for.return_value = 300.0
        session = FakeSession(get_body=_write_page(), post_body='{"ok":true}')
        source = BandcampDiscoverySource(session, governor=governor)

        with pytest.raises(RateLimitedError):
            source.save_remote(_candidate())
        assert session.gets == []
        assert session.posts == []
        governor.wait_turn.assert_not_called()

    def test_a_429_on_the_post_is_reported_and_raised(self, fan_id: int) -> None:
        governor = MagicMock()
        governor.blocked_for.return_value = 0.0
        session = FakeSession(get_body=_write_page())
        session.post_status = 429
        source = BandcampDiscoverySource(session, governor=governor)

        with pytest.raises(RateLimitedError):
            source.save_remote(_candidate())
        governor.report_429.assert_called_with(ALBUM_PAGE)

    def test_an_unfetchable_host_fails_without_a_request(self, fan_id: int) -> None:
        """Unreachable for a built crate — _to_candidates drops custom domains —
        but item_url is remote data read back out of the database."""
        session = FakeSession(get_body=_write_page())
        candidate = _candidate("https://music.example.com/album/x")
        assert _source(session).save_remote(candidate) is False
        assert session.gets == []
