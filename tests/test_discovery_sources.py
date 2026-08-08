"""Criteria registry and BandcampDiscoverySource tests (KAMP-647).

No network: a fake session returns canned bodies, and the real captured fixtures
supply the markup, so these exercise the actual parsers rather than mocks of them.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
