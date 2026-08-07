"""Criteria registry and BandcampDiscoverySource tests (KAMP-647).

No network: a fake session returns canned bodies, and the real captured fixtures
supply the markup, so these exercise the actual parsers rather than mocks of them.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from kamp_daemon.bandcamp_ratelimit import BandcampGovernor
from kamp_daemon.discovery import (
    ALBUM_PAGE,
    ARTIST_PAGE,
    DISCOVER_API,
    PREVIEW,
    SAVE_REMOTE,
    Candidate,
    SeedProfile,
    SimpleBudget,
    crate_budget,
)
from kamp_daemon.discovery_criteria import REGISTRY, Criterion, criteria_for
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
    """Records requests and replays canned bodies."""

    def __init__(self, get_body: str = "", post_body: str = "") -> None:
        self.get_body = get_body
        self.post_body = post_body
        self.gets: list[str] = []
        self.posts: list[tuple[str, Any]] = []
        self.get_status = 200
        self.post_status = 200

    def get(self, url: str, timeout: int = 30) -> FakeResponse:
        self.gets.append(url)
        return FakeResponse(self.get_body, self.get_status)

    def post(self, url: str, json: Any = None, timeout: int = 30) -> FakeResponse:
        self.posts.append((url, json))
        return FakeResponse(self.post_body, self.post_status)


def _source(session: FakeSession) -> BandcampDiscoverySource:
    return BandcampDiscoverySource(
        session, governor=BandcampGovernor(clock=FakeClock())
    )


def _profile(**kw: Any) -> SeedProfile:
    return SeedProfile(**kw)


def _album_seed(album_id: int = 1, url: str = "https://a.bandcamp.com/album/x"):
    return SeedAlbum(
        album_id=album_id,
        album_artist="Artist",
        album="Album",
        album_url=url,
        tralbum_id="111",
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
        assert len(criteria_for(RICH_PROFILE)) >= 4

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
    def test_preview_is_offered_and_wishlist_is_not(self) -> None:
        """save_remote needs a form-encoded POST the relay cannot send, so
        declaring it would render a button that silently does nothing."""
        caps = _source(FakeSession()).capabilities
        assert PREVIEW in caps
        assert SAVE_REMOTE not in caps

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
