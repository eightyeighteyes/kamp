"""Parser tests against the captured Bandcamp fixtures (KAMP-647).

No network anywhere: the parsers are pure ``str -> data``, so real markup runs
through them directly. Fixtures were captured in KAMP-644 and are integrity- and
privacy-guarded by tests/test_discovery_fixtures.py.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from kamp_daemon.discovery_bandcamp_parsers import (
    FACET_FAMILIES,
    art_url_from_image,
    normalise_item_id,
    parse_also_like,
    parse_band_id,
    parse_collect_ok,
    parse_crumbs,
    parse_discography,
    parse_discover_facets,
    parse_discover_results,
    parse_fresh_crumb,
    parse_is_wishlisted,
    release_year,
    strip_tracking,
    tag_slug,
)

FIXTURES = Path(__file__).parent / "fixtures" / "discovery"


def _fixture(name: str) -> str:
    for suffix in (".html.gz", ".json.gz"):
        path = FIXTURES / f"{name}{suffix}"
        if path.exists():
            return gzip.decompress(path.read_bytes()).decode()
    pytest.skip(f"fixture {name} not captured")


@pytest.fixture(scope="module")
def album_page() -> str:
    return _fixture("album_page_with_recs")


@pytest.fixture(scope="module")
def discover_root() -> str:
    return _fixture("discover_root")


@pytest.fixture(scope="module")
def discover_results() -> str:
    return _fixture("discover_web_ambient_top")


class TestAlsoLike:
    def test_parses_every_recommendation_with_full_identity(
        self, album_page: str
    ) -> None:
        """Identity is what the v61 schema keys on; a rec without it is unusable."""
        result = parse_also_like(album_page)
        assert result.marker_present is True
        assert len(result) >= 5
        for item in result.items:
            assert item["provider_item_id"].isdigit()
            assert item["item_url"].startswith("https://")
            assert item["artist"]
            assert item["title"]

    def test_extracts_art_and_provenance_material(self, album_page: str) -> None:
        """The supporters line and fan comment are the clerk-card material the
        recon flagged as better than templated copy."""
        items = parse_also_like(album_page).items
        assert any(i["art_url"] and "f4.bcbits.com" in i["art_url"] for i in items)
        assert any("fans who also own" in i["supporters"] for i in items)
        assert any(i["fan_comment"] for i in items)

    def test_inline_audio_url_is_unwrapped_from_its_json(self, album_page: str) -> None:
        """data-audiourl is a JSON object keyed by format, not a bare URL.

        Reading it as a string yields something unplayable that would only fail
        at the point of pressing play (KAMP-651). Every recommendation carries
        one, which is what lets a preview start before the album page loads.
        """
        items = parse_also_like(album_page).items
        assert items
        for item in items:
            assert item["audio_url"], "every recommendation should carry an mp3"
            assert item["audio_url"].startswith("https://")
            assert not item["audio_url"].startswith("{")

    @pytest.mark.parametrize(
        "raw",
        ["", "not json", "[]", "{}", '{"flac": "https://x/y.flac"}', '{"mp3-128": ""}'],
    )
    def test_unusable_audio_url_is_none(self, raw: str) -> None:
        from kamp_daemon.discovery_bandcamp_parsers import _audio_url

        assert _audio_url(raw) is None

    def test_tracking_parameter_is_stripped(self, album_page: str) -> None:
        """Bandcamp appends ?from=<seed>, so the same album reached from two seeds
        would otherwise look like two albums and defeat cross-seed dedupe."""
        for item in parse_also_like(album_page).items:
            assert "?" not in item["item_url"]

    def test_empty_page_is_not_reported_as_drift(self) -> None:
        """No marker means the page simply has no rec block — not a parser failure."""
        result = parse_also_like("<html><body>nothing</body></html>")
        assert result.items == []
        assert result.marker_present is False

    def test_marker_without_items_is_drift(self, caplog) -> None:
        """The case that must warn: the block is there and we understood none of it."""
        result = parse_also_like('<div class="recommendations-container"></div>')
        assert result.marker_present is True
        result.warn_if_drifted("also_like", "https://x.bandcamp.com/album/y")
        assert "probably drifted" in caplog.text

    def test_honest_empty_does_not_warn(self, caplog) -> None:
        """Warning on both cases would fill the log until nobody reads it."""
        parse_also_like("<html></html>").warn_if_drifted("also_like", "https://x")
        assert caplog.text == ""


class TestDiscoverResults:
    def test_parses_identity_and_release_date(self, discover_results: str) -> None:
        result = parse_discover_results(discover_results)
        assert result.marker_present is True
        assert len(result) > 0
        for item in result.items:
            assert item["provider_item_id"].isdigit()
            assert item["item_url"].startswith("https://")
            assert "release_date" in item

    def test_passes_through_bandcamp_exclusion_flags(
        self, discover_results: str
    ) -> None:
        """Bandcamp does the owned/wishlisted filtering for us on this surface;
        the parser reports the flags and applies no policy of its own."""
        for item in parse_discover_results(discover_results).items:
            assert isinstance(item["is_owned"], bool)
            assert isinstance(item["is_wishlisted"], bool)

    def test_art_url_is_built_from_the_image_object(
        self, discover_results: str
    ) -> None:
        """This surface returns primary_image as an OBJECT, not a URL.

        Passing it through stored a dict in a TEXT column; SQLite refused the
        bind, and since the builder skips a row it cannot persist rather than
        failing the crate, every discover-surface candidate silently vanished
        while the album-page ones carried on. The fixture had the object shape
        all along — nothing asserted the type.
        """
        items = parse_discover_results(discover_results).items
        assert items
        for item in items:
            assert item["art_url"].startswith("https://f4.bcbits.com/img/a")
            assert item["art_url"].endswith(".jpg")

    def test_exclusion_flags_are_honoured_when_true(self) -> None:
        """The captured fixture is anonymous, so both flags are always false in it.
        This synthetic payload is the deliberate, documented exception to the
        full-page fixture rule — it is the only way to cover the true branch."""
        payload = {
            "results": [
                {
                    "item_id": 1,
                    "item_url": "https://a.bandcamp.com/album/owned",
                    "band_name": "A",
                    "title": "Owned",
                    "is_owned": True,
                    "is_wishlisted": False,
                },
                {
                    "item_id": 2,
                    "item_url": "https://b.bandcamp.com/album/wish",
                    "band_name": "B",
                    "title": "Wished",
                    "is_owned": False,
                    "is_wishlisted": True,
                },
            ]
        }
        items = parse_discover_results(payload).items
        assert [i["is_owned"] for i in items] == [True, False]
        assert [i["is_wishlisted"] for i in items] == [False, True]

    def test_carries_the_cursor_for_the_next_page(self, discover_results: str) -> None:
        """The whole of KAMP-661 hangs off this value being kept.

        It was parsed and dropped, so every crate asked for the first page of the
        same query — which is why the candidate pool looked exhausted after about
        five digs when the response itself reports six figures of results.
        """
        assert parse_discover_results(discover_results).cursor

    def test_a_response_without_a_cursor_is_the_end_of_the_line(self) -> None:
        """None rather than '' — the caller stores it and must be able to tell
        'no more pages' from 'a page I have not asked for yet'."""
        assert parse_discover_results({"results": []}).cursor is None

    def test_malformed_json_is_not_drift(self) -> None:
        result = parse_discover_results("not json")
        assert result.items == []
        assert result.marker_present is False
        assert result.cursor is None

    def test_empty_result_list_is_an_honest_empty_and_does_not_warn(
        self, caplog
    ) -> None:
        """A tag outside Bandcamp's vocabulary legitimately returns zero rows.

        Treating that as drift made the warning fire on every genuinely empty
        query — found by running the criteria against a real library, where a
        mis-cased genre produced four false alarms in one gather.
        """
        result = parse_discover_results({"results": []})
        assert result.marker_present is True
        assert result.items == []
        assert result.drifted is False
        result.warn_if_drifted("discover", "https://x")
        assert caplog.text == ""

    def test_missing_results_key_is_drift(self, caplog) -> None:
        """The shape changed — that is the case worth shouting about."""
        result = parse_discover_results({"something_else": []})
        assert result.drifted is True
        result.warn_if_drifted("discover", "https://x")
        assert "probably drifted" in caplog.text


class TestDiscoverFacets:
    def test_reads_the_whole_vocabulary(self, discover_root: str) -> None:
        facets = parse_discover_facets(discover_root)
        for family in FACET_FAMILIES:
            assert facets[family], f"facet family {family} is empty"

    def test_times_is_a_recency_window_not_a_release_year_filter(
        self, discover_root: str
    ) -> None:
        """The finding that reshaped criterion 5. If this ever grows year-like
        entries, the 10+-years criterion can stop filtering client-side."""
        slugs = {t["slug"] for t in parse_discover_facets(discover_root)["times"]}
        assert slugs <= {
            "fresh",
            "today",
            "this-week",
            "1w",
            "2w",
            "3w",
            "4w",
            "5w",
            "6w",
        }

    def test_slices_include_the_best_seller_sort(self, discover_root: str) -> None:
        """Best sellers is a discover slice, not a separate surface to build."""
        slugs = {s["slug"] for s in parse_discover_facets(discover_root)["slices"]}
        assert {"top", "new", "rand"} <= slugs

    def test_missing_blob_returns_empty(self) -> None:
        assert parse_discover_facets("<html></html>") == {}


class TestDiscography:
    def test_parses_the_captured_artist_page(self) -> None:
        """Real markup, not a hand-built grid."""
        html = _fixture("artist_discography")
        result = parse_discography(
            html, base_url="https://floatingpoints.bandcamp.com/music"
        )
        assert result.marker_present is True
        assert len(result) >= 5
        for item in result.items:
            assert item["provider_item_id"].isdigit()
            assert item["item_url"].startswith("https://floatingpoints.bandcamp.com/")

    def test_grid_entries_carry_a_title(self) -> None:
        """Without this the crate renders blank cards — which is exactly what a
        first end-to-end run against the real library produced."""
        result = parse_discography(
            _fixture("artist_discography"),
            base_url="https://floatingpoints.bandcamp.com/music",
        )
        assert all(item["title"] for item in result.items)
        assert any(item["art_url"] for item in result.items)

    def test_ids_stay_paired_with_their_own_album(self) -> None:
        """Entries are matched as whole <li> blocks rather than by zipping separate
        id and href passes — that only holds while every entry has both in the same
        order, and fails by pairing the wrong id with the wrong album rather than by
        returning nothing."""
        html = (
            '<div id="music-grid">'
            '<li data-item-id="album-111"><a href="/album/one">'
            '<p class="title">One</p></a></li>'
            # No href: must be skipped entirely, not shift the pairing.
            '<li data-item-id="album-222"><p class="title">Orphan</p></li>'
            '<li data-item-id="album-333"><a href="/album/three">'
            '<p class="title">Three</p></a></li>'
            "</div>"
        )
        items = parse_discography(html, base_url="https://b.bandcamp.com/music").items
        assert [(i["provider_item_id"], i["title"]) for i in items] == [
            ("111", "One"),
            ("333", "Three"),
        ]

    def test_finds_nothing_in_an_album_page_and_says_so(self, album_page: str) -> None:
        """The cross-page collision case, testable for free.

        An album page has no music grid, so the discography parser must find
        nothing AND report no marker — if a future regex started matching album
        markup, this fails.
        """
        result = parse_discography(album_page, base_url="https://x.bandcamp.com/music")
        assert result.marker_present is False
        assert result.items == []

    def test_parses_a_grid(self) -> None:
        html = (
            '<div id="music-grid">'
            '<li data-item-id="album-123"><a href="/album/one"></a></li>'
            '<li data-item-id="album-456"><a href="/album/two"></a></li>'
            "</div>"
        )
        result = parse_discography(html, base_url="https://band.bandcamp.com/music")
        assert result.marker_present is True
        assert [i["provider_item_id"] for i in result.items] == ["123", "456"]
        assert result.items[0]["item_url"] == "https://band.bandcamp.com/album/one"


@pytest.mark.live
class TestFixturesStillMatchReality:
    """Fixture-rot detection. Deselected in CI; run with ``-m live``.

    A committed fixture keeps every parser test green while the live markup drifts
    underneath it — the failure mode that makes an unofficial-endpoint feature die
    silently. These re-fetch the real pages and assert only that the *structure*
    still parses, not that the content matches, since content changes constantly
    and is not what we are guarding.

    docs/discovery-recon.md promised this test; KAMP-647 built it.
    """

    @staticmethod
    def _live_get(url: str) -> str:
        import requests

        resp = requests.get(
            url,
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()
        return resp.text

    def test_album_page_still_has_a_recommendation_block(self) -> None:
        html = self._live_get("https://floatingpoints.bandcamp.com/album/promises")
        result = parse_also_like(html)
        assert result.marker_present, "the also-like block has moved or gone"
        assert len(result) > 0, "the rec block is present but parses to nothing"

    def test_discover_page_still_ships_its_facet_vocabulary(self) -> None:
        facets = parse_discover_facets(self._live_get("https://bandcamp.com/discover"))
        assert facets, "the DiscoverApp blob has moved"
        for family in FACET_FAMILIES:
            assert facets[family], f"facet family {family} is now empty"

    def test_artist_page_still_renders_a_music_grid(self) -> None:
        url = "https://floatingpoints.bandcamp.com/music"
        result = parse_discography(self._live_get(url), base_url=url)
        assert result.marker_present, "the music grid has moved or gone"
        assert len(result) > 0


class TestItemFieldTypes:
    """Every parser must emit the same field types, whatever surface it read.

    The builder writes these straight into TEXT columns, so a field that is not
    a string is a persistence failure — and a skipped row, being non-fatal, takes
    the whole surface down quietly. Asserting the contract across all three
    parsers is cheap; the discover surface already broke it once.
    """

    def _all_items(self, album_page: str, discover_results: str) -> list[dict]:
        url = "https://band.bandcamp.com/music"
        return [
            *parse_also_like(album_page).items,
            *parse_discover_results(discover_results).items,
            *parse_discography(_fixture("artist_discography"), base_url=url).items,
        ]

    def test_art_url_is_a_string_or_none(
        self, album_page: str, discover_results: str
    ) -> None:
        items = self._all_items(album_page, discover_results)
        assert items
        for item in items:
            assert item["art_url"] is None or isinstance(item["art_url"], str)

    def test_text_fields_are_strings(
        self, album_page: str, discover_results: str
    ) -> None:
        for item in self._all_items(album_page, discover_results):
            for field in ("provider_item_id", "item_url", "title"):
                assert isinstance(item[field], str), field


class TestArtUrlFromImage:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # The discover API's object form — the shape that broke persistence.
            ({"image_id": 123, "is_art": True}, "https://f4.bcbits.com/img/a123_0.jpg"),
            ({"image_id": 9, "is_art": False}, None),
            ({}, None),
            (456, "https://f4.bcbits.com/img/a456_0.jpg"),
            ("789", "https://f4.bcbits.com/img/a789_0.jpg"),
            # Already a URL (the HTML surfaces): pass through untouched.
            (
                "https://f4.bcbits.com/img/a1_16.jpg",
                "https://f4.bcbits.com/img/a1_16.jpg",
            ),
            (None, None),
            ("", None),
        ],
    )
    def test_tolerates_every_shape_bandcamp_uses(self, raw, expected) -> None:
        assert art_url_from_image(raw) == expected


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("album-3149089081", "3149089081"),
            ("track-42", "42"),
            (368758684, "368758684"),
            ("2194023454", "2194023454"),
            (None, ""),
        ],
    )
    def test_item_ids_normalise_across_surfaces(self, raw, expected) -> None:
        """One concept, three spellings: data-albumid, item_id, data-item-id."""
        assert normalise_item_id(raw) == expected

    @pytest.mark.parametrize(
        "display,slug",
        [
            ("Rock", "rock"),
            ("Indie Rock", "indie-rock"),
            ("hip-hop/rap", "hip-hop-rap"),
            ("singer-songwriter", "singer-songwriter"),
            ("  Dub   Techno ", "dub-techno"),
            ("R&B", "rb"),
            ("", ""),
        ],
    )
    def test_genre_names_become_bandcamp_tag_slugs(self, display, slug) -> None:
        """The discover API matches normalised slugs and answers an unknown tag
        with an empty result set rather than an error — so a mis-cased genre looks
        exactly like a broken criterion. Real bug, found by running it."""
        assert tag_slug(display) == slug

    def test_strip_tracking(self) -> None:
        assert (
            strip_tracking("https://a.bandcamp.com/album/x?from=footer-cc-a1")
            == "https://a.bandcamp.com/album/x"
        )

    @pytest.mark.parametrize(
        "value,expected",
        [("2011-04-01 00:00:00 UTC", 2011), ("", None), ("no digits", None)],
    )
    def test_release_year(self, value, expected) -> None:
        assert release_year(value) == expected


class TestCrumbs:
    """The CSRF tokens the wishlist write needs (KAMP-653)."""

    def test_crumbs_are_parsed_and_html_unescaped(self) -> None:
        html = (
            '<meta id="js-crumbs-data" data-crumbs="{&quot;collect_item_cb&quot;:'
            "&quot;|collect_item_cb|1754|abc=&quot;,&quot;uncollect_item_cb&quot;:"
            '&quot;|uncollect_item_cb|1754|def=&quot;}">'
        )
        assert parse_crumbs(html) == {
            "collect_item_cb": "|collect_item_cb|1754|abc=",
            "uncollect_item_cb": "|uncollect_item_cb|1754|def=",
        }

    def test_an_anonymous_page_has_an_empty_crumb_map(self) -> None:
        """Logged-out pages ship `data-crumbs="{}"` — present but empty.

        Distinct from a missing tag, and the reason "is the tag there" is not a
        usable logged-in check. The captured fixture is anonymous, so this is the
        shape the fixture itself has.
        """
        assert parse_crumbs('<meta id="js-crumbs-data" data-crumbs="{}">') == {}

    def test_a_missing_or_malformed_tag_yields_nothing_rather_than_raising(
        self,
    ) -> None:
        assert parse_crumbs("<html><body>nothing here</body></html>") == {}
        assert parse_crumbs('<meta id="js-crumbs-data" data-crumbs="{oops">') == {}

    def test_the_real_fixture_is_anonymous_and_therefore_crumbless(
        self, album_page: str
    ) -> None:
        """Guards the privacy rule as much as the parser: a captured page that
        started carrying live crumbs would fail here as well as in
        tests/test_discovery_fixtures.py."""
        assert parse_crumbs(album_page) == {}


class TestWriteIdentity:
    """band_id and is_wishlisted, both read off the album page (KAMP-653)."""

    def test_band_id_comes_from_current_not_selling_band_id(self) -> None:
        """They diverge on label-released albums, and only one of them works.

        Sending selling_band_id returns HTTP 200 with {"ok":true} and silently
        does nothing — verified live against a real account. So this must never
        fall back to it: a fallback would report success and change nothing.
        """
        html = (
            '<script data-tralbum="{&quot;id&quot;:1,&quot;current&quot;:'
            '{&quot;band_id&quot;:692277828,&quot;selling_band_id&quot;:237579501}}">'
        )
        assert parse_band_id(html) == "692277828"

    def test_band_id_is_none_when_absent(self) -> None:
        assert parse_band_id('<script data-tralbum="{&quot;id&quot;:1}">') is None
        assert parse_band_id("<html></html>") is None

    def test_selling_band_id_is_never_used_as_a_fallback(self) -> None:
        """The dangerous shape: band_id absent, selling_band_id right there.

        Falling back would send a value the endpoint accepts with {"ok":true}
        while doing nothing, so the caller must be told it has no band_id rather
        than handed one that fails silently.
        """
        html = (
            '<script data-tralbum="{&quot;current&quot;:'
            '{&quot;selling_band_id&quot;:237579501}}">'
        )
        assert parse_band_id(html) is None

    def test_the_real_album_fixture_yields_a_band_id(self, album_page: str) -> None:
        assert parse_band_id(album_page) == "2009518365"

    @pytest.mark.parametrize("flag,expected", [(True, True), (False, False)])
    def test_is_wishlisted_is_read_from_fan_tralbum_data(self, flag, expected) -> None:
        blob = "true" if flag else "false"
        html = (
            '<div id="pagedata" data-blob="{&quot;fan_tralbum_data&quot;:'
            "{&quot;is_wishlisted&quot;:" + blob + '}}"></div>'
        )
        assert parse_is_wishlisted(html) is expected

    def test_is_wishlisted_is_none_when_the_page_cannot_say(self) -> None:
        """None is not False. An anonymous page carries fan_tralbum_data: null,
        and treating that as "not wishlisted" would turn a logged-out response
        into a confident negative."""
        assert parse_is_wishlisted("<html></html>") is None
        assert (
            parse_is_wishlisted(
                '<div id="pagedata" data-blob="{&quot;fan_tralbum_data&quot;:null}">'
            )
            is None
        )

    def test_the_real_album_fixture_cannot_say(self, album_page: str) -> None:
        """Captured anonymously, so fan_tralbum_data is null — exactly the case
        that must not be mistaken for False."""
        assert parse_is_wishlisted(album_page) is None


class TestCollectResponse:
    """Reading a `*_cb` reply. The status is not the answer (KAMP-653)."""

    def test_only_ok_true_without_an_error_counts_as_success(self) -> None:
        assert parse_collect_ok('{"ok":true}') is True

    @pytest.mark.parametrize(
        "body",
        [
            # The exact shape a JSON-encoded request comes back with, at HTTP 200.
            '{"error":true,"ok":false,"exception":"InsistError: old or no crumb"}',
            '{"ok":false}',
            '{"ok":true,"error":true}',  # both set: an error is still an error
            '{"error":"invalid_crumb","crumb":"|c|1|z="}',
            "not json at all",
            "[]",
            "",
        ],
    )
    def test_everything_else_is_a_failure(self, body: str) -> None:
        assert parse_collect_ok(body) is False

    def test_a_stale_crumb_yields_the_replacement(self) -> None:
        assert (
            parse_fresh_crumb('{"error":"invalid_crumb","crumb":"|collect|9|zz="}')
            == "|collect|9|zz="
        )

    @pytest.mark.parametrize(
        "body",
        [
            '{"error":true,"ok":false}',  # a different error: not retryable
            '{"error":"invalid_crumb"}',  # says stale but offers nothing to retry with
            '{"error":"invalid_crumb","crumb":""}',
            '{"ok":true}',
            "garbage",
        ],
    )
    def test_no_replacement_crumb_offered(self, body: str) -> None:
        assert parse_fresh_crumb(body) is None
