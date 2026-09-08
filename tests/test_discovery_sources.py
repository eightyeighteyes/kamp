"""Criteria registry and BandcampDiscoverySource tests (KAMP-647).

No network: a fake session returns canned bodies, and the real captured fixtures
supply the markup, so these exercise the actual parsers rather than mocks of them.
"""

from __future__ import annotations

import gzip
import html
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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
    digging_phrase,
    phrasings,
    seed_dimension,
)
from kamp_daemon.discovery_sources import (
    CRITERION_CAPS,
    BandcampDiscoverySource,
    RateLimitedError,
    _SEEDS_PER_CRITERION,
    _seeds_allowed,
)
from kamp_core.discovery_api import UNPERSONALISED_CRITERIA
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
    # Its own field since KAMP-690, filtered in the query rather than out of
    # `played_artists` here. Four Tet is deliberately absent — the criterion's
    # claim is "you have just the one", and a test below pins that.
    lone_album_artists=[
        SeedArtist(
            name="Loraine James",
            artist_page="https://lorainejames.bandcamp.com/music",
            owned_count=1,
            play_time=9000.0,
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
    def test_alternative_phrasings_are_usable_and_distinct(
        self, criterion: Criterion
    ) -> None:
        """KAMP-664. A variant that renders blank is worse than the repeat it
        replaces, and one that renders the sentence it is standing in for buys
        nothing -- so every criterion's alternatives must be sayable and pairwise
        different from each other and from the original."""
        for seed in criterion.seeds(RICH_PROFILE):
            lines = [seed.why, *phrasings(criterion.key, seed.seed_data)]
            assert all(line.strip() for line in lines), criterion.key
            assert len(set(lines)) == len(lines), (criterion.key, lines)

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_phrasings_tolerate_a_seed_from_an_older_build(
        self, criterion: Criterion
    ) -> None:
        """Rows sitting in the KAMP-657 buffer were written before the
        discriminators existed, and their seeds outlive any one release. A variant
        that needs a key the stored seed lacks must degrade to a sentence, not to a
        KeyError on the crate build."""
        for line in phrasings(criterion.key, {"kind": criterion.key}):
            assert line.strip()

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_every_seed_can_say_what_it_is_about_to_do(
        self, criterion: Criterion
    ) -> None:
        """KAMP-693. A dig is 15-30 seconds and this line is what fills them, so
        every seed of every criterion has to produce one -- a criterion that fell
        through to a blank would freeze the line for its whole turn, which is the
        symptom the ticket exists to remove."""
        for seed in criterion.seeds(RICH_PROFILE):
            phrase = digging_phrase(criterion.key, seed.seed_data)
            assert phrase.strip()
            assert phrase.endswith("…")

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_the_digging_line_names_the_thing_it_is_following(
        self, criterion: Criterion
    ) -> None:
        """The point of the line is that it is specific. "Digging…" for twenty
        seconds is a spinner with words; naming the record, band or genre being
        followed is the shop actually being dug through."""
        # kind -> the seed_data field carrying the subject. Not the same thing:
        # `genre` and `genre_old` are two kinds naming one field, which is exactly
        # what lets seed_dimension stop them both taking Rock.
        fields = {
            "album": "album",
            "artist": "artist",
            "genre": "genre",
            "genre_old": "genre",
            "chart": None,  # the chart is about nobody, and says so
        }
        subject = "Ambivalent Sausage"
        for seed in criterion.seeds(RICH_PROFILE):
            field = fields[seed.seed_data["kind"]]
            if field is None:
                continue
            seeded = {**seed.seed_data, field: subject}
            assert subject in digging_phrase(criterion.key, seeded)

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_the_digging_line_survives_a_seed_it_does_not_recognise(
        self, criterion: Criterion
    ) -> None:
        """Same reason phrasings degrades rather than raising: this runs inside
        the gather, and a KeyError here would cost the crate to decorate it."""
        assert digging_phrase(criterion.key, {}).strip()
        assert digging_phrase("no_such_criterion", {"kind": "album"}).strip()

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_thin_profile_never_raises(self, criterion: Criterion) -> None:
        """A brand-new library is the common first run, not an edge case."""
        list(criterion.seeds(SeedProfile()))

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_endpoint_class_is_funded(self, criterion: Criterion) -> None:
        """crate_budget denies unknown classes, so an undeclared one would return
        empty forever and look exactly like parser drift."""
        assert crate_budget().allow(criterion.endpoint_class) is True

    @pytest.mark.parametrize("criterion", REGISTRY, ids=lambda c: c.key)
    def test_no_criterion_reads_more_seeds_than_it_can_place(
        self, criterion: Criterion
    ) -> None:
        """A seed is one request against the endpoints that rate-limit hardest,
        and SEED_CAP is 1, so a criterion's card cap IS its seed ceiling (KAMP-698).

        genre_top and older_than_ten each read two seeds and are capped at one
        card. The second was pure cost: the backfill pass drops caps and can take
        a second card from the first seed's remaining twenty-odd items, so the
        extra fetch bought a card the crate could already have had.
        """
        cap = CRITERION_CAPS.get(criterion.key)
        if cap is None:
            return
        assert _seeds_allowed(criterion) <= cap

    def test_the_seed_allowance_is_derived_from_the_cap_not_restated(self) -> None:
        """Raising a cap must raise the seeds that serve it, with nothing to edit.

        There are already two lists to keep in agreement (_SEEDS_FOR and
        CRITERION_CAPS); a third hardcoded one would be a third chance for them to
        disagree silently, and the symptom — a criterion quietly unable to fill
        its own cap — is invisible in any single crate.
        """
        capped = next(c for c in REGISTRY if CRITERION_CAPS.get(c.key) == 1)
        assert _seeds_allowed(capped) == 1
        with patch.dict(
            "kamp_daemon.discovery_sources.CRITERION_CAPS", {capped.key: 3}
        ):
            assert _seeds_allowed(capped) == _SEEDS_PER_CRITERION

    def test_an_uncapped_criterion_keeps_its_full_allowance(self) -> None:
        """The cap is a ceiling, never a floor. also_like is uncapped and reads
        four seeds because KAMP-683 wants four also-like records off four
        different album pages."""
        also_like = next(c for c in REGISTRY if c.key == "also_like")
        assert also_like.key not in CRITERION_CAPS
        assert _seeds_allowed(also_like) == 4

    def test_thin_profile_still_yields_the_chart_criterion(self) -> None:
        """The un-personalised fallback: a new user gets a crate, not an apology."""
        keys = [c.key for c in criteria_for(SeedProfile())]
        assert keys == ["best_seller"]

    def test_a_rich_profile_runs_every_criterion(self) -> None:
        """Set equality, not a floor (KAMP-690).

        It was `>= 6` against seven criteria — raised from 4 with the registry in
        KAMP-658 — and a floor cannot tell "all seven ran" from "six ran and one
        is dead". That is not hypothetical: a criterion that starts reading a NEW
        SeedProfile field silently produces nothing until RICH_PROFILE populates
        it, and this test would have stayed green through it. Naming the set is
        what makes a missing criterion fail loudly.
        """
        assert {c.key for c in criteria_for(RICH_PROFILE)} == {c.key for c in REGISTRY}

    def test_no_genre_line_claims_listening_or_recency(self) -> None:
        """KAMP-664. taste_genres counts tag rows and Bandcamp keywords — it has
        no play signal and no time filter — so a genre line may talk about the
        shelf and nothing else. "You've been deep in Rock lately" was two
        invented claims in one sentence."""
        banned = ("lately", "recently", "you listen", "you've been", "you have been")
        for key in ("genre_top", "older_than_ten"):
            criterion = next(c for c in REGISTRY if c.key == key)
            for seed in criterion.seeds(RICH_PROFILE):
                # The alternatives are held to the same rule as the sentence they
                # stand in for. A variant is still a claim on a card, so exempting
                # them would just move the invented claim one hop away.
                for line in (seed.why, *phrasings(key, seed.seed_data)):
                    low = line.casefold()
                    for phrase in banned:
                        assert phrase not in low, f"{key} claims '{phrase}': {line}"

    def test_the_unpersonalised_criteria_constant_is_current(self) -> None:
        """KAMP-664. discovery_api derives the "we had nothing to go on" banner
        from the stored criteria, and names them in its own constant because it
        must not import kamp_daemon. This is the seam that keeps the two in step:
        add a criterion that yields seeds from an empty profile and the banner
        would silently stop appearing, so fail here instead."""
        thin = SeedProfile()
        yields_from_nothing = {c.key for c in REGISTRY if list(c.seeds(thin))}
        assert yields_from_nothing == set(UNPERSONALISED_CRITERIA)

    def test_the_top_genre_makes_a_stronger_claim_than_the_tail(self) -> None:
        """Rank is the honest stand-in for dominance: a share would need a
        denominator taste_genres cannot give, since its count mixes per-track tag
        rows with per-album keyword hits."""
        profile = replace(
            RICH_PROFILE, top_genres=["ambient"] + [f"g{i}" for i in range(9)]
        )
        genre_top = next(c for c in REGISTRY if c.key == "genre_top")
        by_rank = {s.seed_data["rank"]: s for s in genre_top.seeds(profile)}
        assert by_rank[0].seed_data["genre"] == "ambient"
        assert by_rank[0].why != by_rank[9].why

    def test_the_lone_album_criterion_skips_artists_you_own_several_by(self) -> None:
        """The claim is about a gap on the shelf, so an artist with four albums
        in the collection must not produce "you have just the one here"."""
        lone = next(c for c in REGISTRY if c.key == "lone_album_artist")
        names = {s.seed_data["artist"] for s in lone.seeds(RICH_PROFILE)}
        assert names == {"Loraine James"}

    def test_the_artist_criterion_falls_through_to_what_you_play(self) -> None:
        """Favourites first, then artists merely played a lot — and the two make
        different claims, because starring and playing are different acts.

        Asserted on the `starred` discriminator and the distinctness of the two
        sentences, not on their wording (KAMP-664). Pinning prose here made every
        copy pass a test change, and the thing that must hold is that the claims
        stay distinguishable, not that they use particular words."""
        fav = next(c for c in REGISTRY if c.key == "favorite_artist")
        seeds = {s.seed_data["artist"]: s for s in fav.seeds(RICH_PROFILE)}
        assert seeds["Four Tet"].seed_data["starred"] is True
        assert seeds["Loraine James"].seed_data["starred"] is False
        assert seeds["Four Tet"].why != seeds["Loraine James"].why

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
        recent_seed = list(REGISTRY[0].seeds(recent))[0]
        fav_seed = list(REGISTRY[0].seeds(fav))[0]
        assert recent_seed.seed_data["recent"] is True
        assert fav_seed.seed_data["recent"] is False
        assert recent_seed.why != fav_seed.why

    def test_a_favourite_gone_quiet_gets_its_own_line(self) -> None:
        """KAMP-658's "clerk remembers". Folded into also_like rather than made a
        criterion of its own, because this selector already reaches these albums
        through favorite_albums."""
        import time as _t

        stale = _album_seed(3, last_played_at=_t.time() - 200 * 86400)
        seeds = list(REGISTRY[0].seeds(SeedProfile(favorite_albums=[stale])))
        assert seeds[0].seed_data["dormant"] is True
        # Distinct from the plain-favourite line, without pinning either wording.
        plain = list(REGISTRY[0].seeds(SeedProfile(favorite_albums=[_album_seed(3)])))
        assert seeds[0].why != plain[0].why

    def test_a_favourite_played_last_month_is_not_called_dormant(self) -> None:
        import time as _t

        warm = _album_seed(4, last_played_at=_t.time() - 30 * 86400)
        seeds = list(REGISTRY[0].seeds(SeedProfile(favorite_albums=[warm])))
        assert seeds[0].seed_data["dormant"] is False

    def test_a_favourite_never_played_is_not_called_dormant(self) -> None:
        """ "You have not put it on in a while" is false for a record that has
        never been on. Never-played reads as an ordinary favourite."""
        seeds = list(REGISTRY[0].seeds(SeedProfile(favorite_albums=[_album_seed(5)])))
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

    def test_the_gather_says_what_it_is_about_to_fetch(self) -> None:
        """KAMP-693: the 15-30 seconds of a dig, narrated.

        One call per seed actually fetched, which is one per HTTP round trip —
        that is what makes the line move at the pace the work does rather than on
        a timer with nothing behind it.
        """
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        seen: list[tuple[str, dict[str, Any]]] = []
        _source(session).gather(
            SeedProfile(top_genres=["ambient"]),
            crate_budget(),
            on_seed=lambda criterion, seed: seen.append((criterion, seed)),
        )
        assert seen
        assert len(seen) == len(session.posts)
        assert all(seed.get("kind") for _criterion, seed in seen)

    def test_the_gather_reports_a_seed_before_fetching_it(self) -> None:
        """Before, not after: a line that appears once a fetch has RETURNED
        describes work that is already done, and the last one would never be seen
        at all."""
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        order: list[str] = []
        source = _source(session)

        def _note(criterion: str, seed: dict[str, Any]) -> None:
            order.append(f"say:{criterion}")

        original = source._run_seed

        def _watched(*args: Any, **kwargs: Any) -> Any:
            order.append("fetch")
            return original(*args, **kwargs)

        source._run_seed = _watched  # type: ignore[method-assign]
        source.gather(
            SeedProfile(top_genres=["ambient"]), crate_budget(), on_seed=_note
        )
        assert order[0].startswith("say:")
        assert order[1] == "fetch"

    def test_a_broken_progress_callback_cannot_cost_the_crate(self) -> None:
        """It is a line of copy. A criterion that raises is already best-effort
        here; a narrator that raises must not be worse than that."""
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))

        def _boom(criterion: str, seed: dict[str, Any]) -> None:
            raise RuntimeError("no")

        found = _source(session).gather(
            SeedProfile(top_genres=["ambient"]), crate_budget(), on_seed=_boom
        )
        assert found

    def test_a_skipped_seed_is_never_announced(self) -> None:
        """A seed the crate already covers is skipped without spending a request,
        so announcing it would put a line on screen for work that never happens —
        and, worse, name a genre the crate is not actually digging through."""
        session = FakeSession(post_body=_fixture("discover_web_ambient_top"))
        seen: list[tuple[str, dict[str, Any]]] = []
        _source(session).gather(
            SeedProfile(top_genres=["ambient"]),
            crate_budget(),
            on_seed=lambda criterion, seed: seen.append((criterion, seed)),
        )
        assert len(seen) == len(session.posts)

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


def _rec_page(*audio: str) -> str:
    """An album page whose recommendation block is exactly *audio*.

    Synthetic because the captured fixture is checksum-locked AND 7/7 positive --
    it holds no unplayable record to test against, and one cannot be added to it.
    Each argument is the raw `data-audiourl` attribute for one recommendation.
    """
    # Attributes are double-quoted with the JSON entity-escaped inside, which is
    # how Bandcamp really ships them — `_attr` only matches double quotes, so a
    # single-quoted attribute would silently read as absent and the record would
    # look unknown rather than unplayable.
    recs = "".join(
        f'<li class="recommended-album" data-albumid="{n}" data-artist="Artist {n}"'
        f' data-albumtitle="Title {n}" data-audiourl="{html.escape(raw, quote=True)}">'
        f'<a class="album-link" href="https://b{n}.bandcamp.com/album/x"></a></li>'
        for n, raw in enumerate(audio)
    )
    return f'<div id="detail_recommendations">{recs}</div>'


class TestUnplayableRecords:
    """KAMP-670: a record the surface says has no audio never reaches a crate."""

    PLAYS = '{"mp3-128": "https://t4.bcbits.com/stream/x"}'
    SILENT = '{"flac": "https://x/y.flac"}'
    UNREADABLE = "not json"

    @staticmethod
    def _seed() -> Seed:
        return Seed(
            target="https://a.bandcamp.com/album/x",
            why="because",
            seed_data={"kind": "album", "album_id": 1},
        )

    def _gather_one_seed(self, page: str) -> tuple[list[Any], FakeSession]:
        session = FakeSession(get_body=page)
        source = _source(session)
        criterion = next(c for c in REGISTRY if c.key == "also_like")
        got, _dropped = source._run_seed(
            criterion, self._seed(), crate_budget(), set(), {}
        )
        return got, session

    def test_a_record_with_no_playable_format_is_dropped(self) -> None:
        got, _ = self._gather_one_seed(_rec_page(self.PLAYS, self.SILENT))
        assert [c.provider_item_id for c in got] == ["0"]

    def test_a_record_we_could_not_read_is_kept(self) -> None:
        """The distinction the whole design rests on. An attribute we failed to
        parse is our blind spot, not Bandcamp saying no -- and the preview-time
        message is the honest answer for a record we do not know about."""
        got, _ = self._gather_one_seed(_rec_page(self.PLAYS, self.UNREADABLE))
        assert [c.provider_item_id for c in got] == ["0", "1"]

    def test_a_page_where_everything_is_unplayable_is_treated_as_drift(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The failure this module is architected against: a format change that
        empties a criterion while every test still passes.

        A definite negative has never been observed -- 7/7, 20/20, 48/48 across
        every sample ever taken -- so ALL of them at once means we stopped
        understanding the format, not that Bandcamp shipped a page of silent
        records. Say so and keep them.
        """
        with caplog.at_level("WARNING", logger="kamp_daemon.discovery_sources"):
            got, _ = self._gather_one_seed(_rec_page(self.SILENT, self.SILENT))
        assert len(got) == 2, "a format change must not empty the criterion"
        assert "parser drift" in caplog.text

    def test_dropping_a_seed_dry_does_not_buy_another_request(self) -> None:
        """The acceptance criterion "no increase in requests per crate", made
        checkable -- and the one the fixtures cannot test, being all-positive.

        `productive` decides whether to walk to the next seed, and it used to be
        judged on the post-filter list. So a seed filtered down to nothing read as
        a seed that FOUND nothing, and the loop spent another fetch on the
        endpoint class that rate-limits hardest.
        """
        # One playable record the user already owns, and one with no audio. The
        # mix matters: a page that is ENTIRELY unplayable is treated as drift and
        # kept, so the only way a seed empties via this filter is alongside one of
        # the older drops.
        session = FakeSession(get_body=_rec_page(self.PLAYS, self.SILENT))
        source = _source(session)
        criterion = next(c for c in REGISTRY if c.key == "also_like")
        profile = SeedProfile(
            recent_album_ids={1, 2, 3, 4},
            recent_albums=[
                _album_seed(album_id=i, url=f"https://a{i}.bandcamp.com/album/x")
                for i in (1, 2, 3, 4)
            ],
        )
        source._run_criterion(criterion, profile, crate_budget(), {"0"}, {})
        # Four seeds tried, exactly what also_like is allowed since KAMP-689 gave
        # it four (SEED_CAP is 1, so its card count IS its seed count). Before the
        # KAMP-670 fix this walked the whole seed list until the budget stopped it.
        assert len(session.gets) == 4, f"spent {len(session.gets)} requests"

    @staticmethod
    def _pick(criterion: str) -> Candidate:
        return Candidate(
            provider="bandcamp",
            provider_item_id="1",
            item_url="https://a.bandcamp.com/album/x",
            criterion=criterion,
        )

    @pytest.mark.parametrize(
        "criterion",
        ["also_like", "purchase_anniversary", "genre_top", "best_seller"],
    )
    def test_a_vouched_criterion_costs_no_request_to_confirm(
        self, criterion: str
    ) -> None:
        """Every criterion whose surface carries the signal was already settled
        during the gather. Asking again would pay twice for the same answer."""
        session = FakeSession()
        budget = crate_budget()
        assert _source(session).confirm_playable(self._pick(criterion), budget) is None
        assert session.gets == []
        assert budget.spent.get(ALBUM_PAGE, 0) == 0

    @pytest.mark.parametrize("criterion", ["favorite_artist", "lone_album_artist"])
    def test_a_discography_pick_is_checked_against_its_album_page(
        self, criterion: str
    ) -> None:
        """The gap this closes: the /music grid says nothing about audio, so the
        only way to know is the album page — and only for a pick."""
        session = FakeSession(
            get_body='<script data-tralbum="'
            + html.escape('{"trackinfo": [{"title": "A", "file": {}}]}', quote=True)
            + '"></script>'
        )
        budget = crate_budget()
        assert _source(session).confirm_playable(self._pick(criterion), budget) is False
        assert len(session.gets) == 1
        assert budget.spent[ALBUM_PAGE] == 1

    def test_an_exhausted_budget_places_the_record_unchecked(self) -> None:
        """Bounded by the gather's own allowance rather than a fresh one, so a
        build degrades to the old behaviour instead of overrunning the endpoint
        class that rate-limits hardest."""
        session = FakeSession()
        budget = SimpleBudget(limits={ALBUM_PAGE: 0})
        assert (
            _source(session).confirm_playable(self._pick("favorite_artist"), budget)
            is None
        )
        assert session.gets == [], "spent a request it could not afford"

    @pytest.mark.parametrize(
        ("body", "status", "reason"),
        [
            ("", 404, "http_404"),
            ("<html>nothing here</html>", 200, "no_tralbum"),
            # Double-quoted with the JSON entity-escaped inside, the way
            # parse_tralbum actually matches it.
            (
                '<script data-tralbum="'
                + html.escape('{"trackinfo": [{"title": "A", "file": {}}]}', quote=True)
                + '"></script>',
                200,
                "no_streams",
            ),
        ],
    )
    def test_every_preview_failure_names_its_cause(
        self,
        body: str,
        status: int,
        reason: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """KAMP-670. "No preview for this one." has five causes, and they used to
        be five differently-worded lines at three levels -- with the one that
        matters most, an album carrying no stream at all, the quietest of them at
        INFO. A user reporting the failure left nothing behind to say WHICH.

        One prefix to grep, one reason word to tell them apart.
        """
        session = FakeSession()
        session.get_body = body
        session.get_status = status
        candidate = Candidate(
            provider="bandcamp",
            provider_item_id="1",
            item_url="https://a.bandcamp.com/album/x",
        )
        with caplog.at_level("WARNING", logger="kamp_daemon.discovery_sources"):
            assert _source(session).preview_tracks(candidate) == []
        assert f"preview unavailable ({reason})" in caplog.text

    def test_a_discography_record_is_never_dropped(self) -> None:
        """Two of seven criteria sit on a surface with no audio signal at all. A
        rule that dropped them would zero those criteria on a guess."""
        session = FakeSession(get_body=_fixture("artist_discography"))
        source = _source(session)
        criterion = next(c for c in REGISTRY if c.key == "favorite_artist")
        seed = Seed(
            target="https://fourtet.bandcamp.com/music",
            why="because",
            seed_data={"kind": "artist", "artist": "Four Tet"},
        )
        got, dropped = source._run_seed(criterion, seed, crate_budget(), set(), {})
        assert got, "the discography surface must still yield candidates"
        assert dropped == 0


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


class TestTheGatherOverlapsItsEndpointClasses:
    """KAMP-698. A dig was 15-30 seconds and every request in it was serial.

    `_MIN_SPACING` is keyed per endpoint class and `wait_turn` reserves its slot
    under a lock -- explicitly so two threads in one class cannot both go -- so
    the three classes never needed to wait on each other. They did it anyway,
    purely because gather looped criteria in one thread.
    """

    def test_the_classes_run_at_the_same_time(self) -> None:
        """The whole point, and it has to be asserted on OVERLAP rather than on
        elapsed time, which would be a flaky way to say the same thing.

        Each class records when it is inside a fetch. If the gather is serial, no
        two classes are ever in one at the same moment.
        """
        import threading

        inside: dict[str, int] = {}
        overlapped: set[frozenset[str]] = set()
        lock = threading.Lock()
        gate = threading.Barrier(3, timeout=5)

        class _Watched(FakeSession):
            def _note(self, endpoint_class: str) -> None:
                with lock:
                    inside[endpoint_class] = inside.get(endpoint_class, 0) + 1
                    live = {k for k, v in inside.items() if v > 0}
                    if len(live) > 1:
                        overlapped.add(frozenset(live))

            def get(self, url: str, timeout: int = 30) -> Any:
                cls = ARTIST_PAGE if url.endswith("/music") else ALBUM_PAGE
                self._note(cls)
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
                resp = super().get(url, timeout)
                with lock:
                    inside[cls] -= 1
                return resp

            def post(self, url: str, **kw: Any) -> Any:
                self._note(DISCOVER_API)
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
                resp = super().post(url, **kw)
                with lock:
                    inside[DISCOVER_API] -= 1
                return resp

        session = _Watched(
            get_body=_fixture("album_page_with_recs"),
            post_body=_fixture("discover_web_ambient_top"),
        )
        _source(session).gather(RICH_PROFILE, crate_budget(), {})
        # The barrier only releases when three different classes are waiting in a
        # fetch at once, so reaching this line at all is the property. The
        # recorded set makes the failure message say which classes made it.
        assert overlapped, "no two endpoint classes were ever in flight together"

    def test_the_same_candidates_come_back(self) -> None:
        """Concurrency must not change WHAT a crate is offered, only when.

        Compared against a deliberately serialised run of the same source, so
        this stays true if the grouping or merge order is ever reworked.
        """

        def _gather(max_workers: int) -> list[str]:
            session = FakeSession(
                get_body=_fixture("album_page_with_recs"),
                post_body=_fixture("discover_web_ambient_top"),
            )
            found = _source(session).gather(
                RICH_PROFILE, crate_budget(), {}, max_workers=max_workers
            )
            return [c.provider_item_id for c in found]

        assert _gather(1) == _gather(4)

    def test_the_request_count_per_class_is_unchanged(self) -> None:
        """The one number that must not move. These endpoints rate-limit hardest
        and a 429 cascades account-wide (KAMP-639), so overlapping them is only
        acceptable while it costs exactly what it cost before."""

        def _spend(max_workers: int) -> dict[str, int]:
            session = FakeSession(
                get_body=_fixture("album_page_with_recs"),
                post_body=_fixture("discover_web_ambient_top"),
            )
            budget = crate_budget()
            _source(session).gather(RICH_PROFILE, budget, {}, max_workers=max_workers)
            return dict(budget.spent)

        assert _spend(4) == _spend(1)

    def test_a_rate_limit_in_one_class_stops_every_class(self) -> None:
        """A 429 is account-wide, not that class's problem (KAMP-639).

        Serially this fell out of a bare `break`. Concurrently it has to be said
        out loud, or the other two workers keep hammering an account that has
        already been told to stop -- turning one rate limit into three.
        """
        session = FakeSession(get_body=_fixture("album_page_with_recs"))
        session.post_status = 429
        budget = crate_budget()
        profile = replace(RICH_PROFILE, top_genres=[f"g{i}" for i in range(25)])
        _source(session).gather(profile, budget, {}, max_workers=4)
        # The discover worker hits the 429 on its first request; the album and
        # artist workers must not spend their full allowance afterwards.
        assert budget.spent.get(ALBUM_PAGE, 0) < budget.limits[ALBUM_PAGE]

    def test_the_rotation_survives_concurrent_writers(self) -> None:
        """`_sub` is a check-then-create on the shared state dict, so two workers
        would each build a fresh {} and one would clobber the other's offsets --
        losing a whole class's rotation silently, which reads as a criterion that
        never varies rather than as a bug."""
        state: dict[str, Any] = {}
        session = FakeSession(
            get_body=_fixture("album_page_with_recs"),
            post_body=_fixture("discover_web_ambient_top"),
        )
        _source(session).gather(RICH_PROFILE, crate_budget(), state, max_workers=4)
        ran = {c.key for c in criteria_for(RICH_PROFILE)}
        recorded = set(state.get("seeds", {}))
        assert ran <= recorded, f"lost rotation for {sorted(ran - recorded)}"

    def test_no_seed_dimension_is_shared_across_endpoint_classes(self) -> None:
        """The property that lets `used` be partitioned by worker.

        Album dimensions arise only in ALBUM_PAGE criteria, genre only in
        DISCOVER_API, artist only in ARTIST_PAGE -- so the two criteria that
        genuinely collide over genres are both DISCOVER_API and stay in one
        thread, in order, exactly as before.

        `used` is locked anyway, so correctness does not rest on this. What this
        guards is the REASONING: a future criterion that read genres off an album
        page would make the guard order-dependent, and this fails loudly instead
        of producing a crate that quietly repeats a genre now and then.
        """
        kinds: dict[str, set[str]] = {}
        for criterion in REGISTRY:
            for seed in criterion.seeds(RICH_PROFILE):
                dimension = seed_dimension(seed.seed_data)
                if dimension is not None:
                    kind = dimension.split(":", 1)[0]
                    kinds.setdefault(kind, set()).add(criterion.endpoint_class)
        straddling = {k: v for k, v in kinds.items() if len(v) > 1}
        assert not straddling, f"dimension kinds spanning classes: {straddling}"


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
        source._run_seed = lambda *a, **k: ([MagicMock()], 0)  # type: ignore[method-assign]
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

    def test_a_capped_criterion_costs_one_request_not_two(self) -> None:
        """The saving, pinned exactly rather than as an upper bound (KAMP-698).

        The budget test above asserts `<= limits` and would stay green whichever
        way this moved — in either direction, which is the more dangerous half.

        Driven from a 25-genre profile because RICH_PROFILE carries ONE, and with
        one genre both genre_top seeds name it: the second is skipped by the
        `used` guard before it costs anything, so a single-genre profile cannot
        see this change at all. That is exactly the trap that made the first
        measurement of this ticket read "no saving".

        Three DISCOVER_API requests for three criteria, each capped at one card.
        It was five. The candidates gathered are unchanged.
        """
        budget = crate_budget()
        session = FakeSession(
            get_body=_fixture("album_page_with_recs"),
            post_body=_fixture("discover_web_ambient_top"),
        )
        profile = replace(RICH_PROFILE, top_genres=[f"genre-{i}" for i in range(25)])
        found = _source(session).gather(profile, budget, {})
        assert budget.spent[DISCOVER_API] == 3
        assert found

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


class TestSeedPoolsAreDeepEnoughToRotate:
    """KAMP-690: rotation only varies a crate if there is something to rotate.

    Rotation advances `_seeds_allowed(criterion)` per crate and wraps modulo the
    pool, so a pool barely larger than one crate's spread cycles straight back to
    the head. Measured on a real library, `lone_album_artist` held 4 seeds and
    `older_than_ten` 3 — repeating every two crates and every one and a half, and
    the user saw one artist "in many many crates".

    Driven from a profile through the real selectors rather than from a hand-built
    seed list, so these track what a criterion actually reaches rather than what a
    fixture says it does.
    """

    #: Crates a seed must survive before it may come round again. Ten is what the
    #: measured complaint needs: at two seeds a crate it means a pool of twenty,
    #: which takes the reported artist from every other crate to one in ten.
    MIN_CYCLE = 10

    @staticmethod
    def _rich() -> SeedProfile:
        """A profile with plenty of everything the two thin criteria read."""
        return replace(
            RICH_PROFILE,
            top_genres=[f"genre-{i}" for i in range(25)],
            lone_album_artists=[
                SeedArtist(
                    name=f"Solo {i}",
                    artist_page=f"https://solo{i}.bandcamp.com/music",
                    owned_count=1,
                    play_time=float(9000 - i * 100),
                )
                for i in range(20)
            ],
        )

    @pytest.mark.parametrize("key", ["lone_album_artist", "older_than_ten"])
    def test_a_seed_does_not_come_round_within_ten_crates(self, key: str) -> None:
        criterion = next(c for c in REGISTRY if c.key == key)
        seeds = list(criterion.seeds(self._rich()))
        cycle = len(seeds) / _seeds_allowed(criterion)
        assert cycle >= self.MIN_CYCLE, (
            f"{key} holds {len(seeds)} seeds and reads "
            f"{_seeds_allowed(criterion)} a crate — repeats every {cycle:.1f}"
        )

    def test_the_lone_album_pool_is_not_truncated_by_the_shared_limit(self) -> None:
        """The actual defect. played_artists_with_pages applied its LIMIT and the
        criterion then filtered owned_count == 1, so a library with 235 qualifying
        artists surfaced four — the ones that happened to rank inside the 25
        most-played artists OVERALL."""
        criterion = next(c for c in REGISTRY if c.key == "lone_album_artist")
        seeds = list(criterion.seeds(self._rich()))
        assert len(seeds) == 20, f"the pool was truncated to {len(seeds)}"

    def test_the_older_record_criterion_reads_every_genre(self) -> None:
        """It sliced top_genres[:3] while genre_top walked all of them, which is
        why the same three genres cycled forever."""
        criterion = next(c for c in REGISTRY if c.key == "older_than_ten")
        genres = {s.seed_data.get("genre") for s in criterion.seeds(self._rich())}
        assert len(genres) == 25, f"only reached {len(genres)} genres"


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

        EIGHT albums, because rotation is only observable once the list is longer
        than one crate's spread — a pool fully consumed every time wraps straight
        back to the head, correctly, and shows nothing. It was four while
        also_like read two seeds a crate; KAMP-689 takes it to four seeds, so the
        pool has to double to keep the assertion meaningful.
        """
        session = FakeSession(get_body=_fixture("album_page_with_recs"))
        profile = SeedProfile(
            recent_album_ids=set(range(1, 9)),
            recent_albums=[
                _album_seed(album_id=i, url=f"https://a{i}.bandcamp.com/album/x")
                for i in range(1, 9)
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
            # The second value is the KAMP-670 unplayable-drop count; zero here,
            # because these seeds find nothing rather than finding it unplayable.
            return ([MagicMock()], 0) if len(tried) == 3 else ([], 0)

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
            return [], 0

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
