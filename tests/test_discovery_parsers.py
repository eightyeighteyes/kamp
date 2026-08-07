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
    normalise_item_id,
    parse_also_like,
    parse_discography,
    parse_discover_facets,
    parse_discover_results,
    release_year,
    strip_tracking,
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

    def test_malformed_json_is_not_drift(self) -> None:
        result = parse_discover_results("not json")
        assert result.items == []
        assert result.marker_present is False

    def test_empty_result_list_is_an_honest_empty(self) -> None:
        """A tag outside Bandcamp's vocabulary legitimately returns zero rows."""
        result = parse_discover_results({"results": []})
        assert result.marker_present is True
        assert result.items == []


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
