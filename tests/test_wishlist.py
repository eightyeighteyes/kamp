"""Wishlist read + cache tests (KAMP-652).

No fixture file: the spike captures pages anonymously on purpose (public repo),
and a wishlist payload is inherently personal. These payloads are hand-built
from a measured real response — 824 items over 9 pages, 813 albums and 11
tracks, rows carrying tralbum_id/tralbum_type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from kamp_core.library import LibraryIndex
from kamp_daemon.discovery import FANCOLLECTION
from kamp_daemon.wishlist import (
    PAGE_SIZE,
    WishlistCache,
    fetch_wishlist_album_ids,
)


@pytest.fixture
def index(tmp_path: Path) -> Iterator[LibraryIndex]:
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


def _row(tralbum_id: int, tralbum_type: str = "a") -> dict[str, Any]:
    return {
        "tralbum_id": tralbum_id,
        "tralbum_type": tralbum_type,
        "item_id": tralbum_id,
        "band_name": "Band",
        "item_title": "Album",
    }


def _page(rows: list[dict[str, Any]], last_token: str = "tok") -> dict[str, Any]:
    return {"items": rows, "last_token": last_token, "more_available": True}


class FakeResponse:
    def __init__(self, body: Any = None, status_code: int = 200) -> None:
        self._body = body if body is not None else {}
        self.status_code = status_code

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    """Replays canned pages and records what was asked for."""

    def __init__(self, pages: list[Any]) -> None:
        self.pages = pages
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, json: Any = None, timeout: int = 30, headers: Any = None):
        self.posts.append(dict(json or {}))
        page = self.pages[min(len(self.posts) - 1, len(self.pages) - 1)]
        if isinstance(page, Exception):
            raise page
        return page if isinstance(page, FakeResponse) else FakeResponse(page)


def _gov() -> MagicMock:
    gov = MagicMock()
    gov.blocked_for.return_value = 0.0
    return gov


class TestWalk:
    def test_the_first_page_carries_a_seeded_token(self) -> None:
        """Omitting it returns zero rows with NO error — indistinguishable from
        an empty wishlist, and exactly how this looked when first measured."""
        session = FakeSession([_page([_row(1)], last_token="")])
        fetch_wishlist_album_ids(session, 42, governor=_gov(), now=lambda: 1000.0)

        sent = session.posts[0]
        assert sent["older_than_token"] == "1000:0:a::"
        assert sent["fan_id"] == 42
        assert sent["count"] == PAGE_SIZE

    def test_paginates_until_a_short_page(self) -> None:
        pages = [_page([_row(i) for i in range(PAGE_SIZE)]), _page([_row(999)])]
        ids = fetch_wishlist_album_ids(FakeSession(pages), 1, governor=_gov())
        assert "999" in ids
        assert len(ids) == PAGE_SIZE + 1

    def test_stops_on_a_falsy_token_even_with_a_full_page(self) -> None:
        """The shipped collection walk does not trust more_available; nor does
        this — a full page with no token is the end."""
        full = _page([_row(i) for i in range(PAGE_SIZE)], last_token="")
        session = FakeSession([full, _page([_row(12345)])])
        ids = fetch_wishlist_album_ids(session, 1, governor=_gov())
        assert len(session.posts) == 1
        assert "12345" not in ids

    def test_only_albums_enter_the_set(self) -> None:
        """A wishlist holds tracks too, and normalise_item_id flattens
        `track-N` and `album-N` to the same string — so a wishlisted track would
        silently suppress an unrelated album sharing its numeric id."""
        page = _page([_row(1, "a"), _row(2, "t"), _row(3, "a")], last_token="")
        ids = fetch_wishlist_album_ids(FakeSession([page]), 1, governor=_gov())
        assert ids == {"1", "3"}

    def test_pages_after_the_first_are_governor_spaced(self) -> None:
        gov = _gov()
        pages = [_page([_row(i) for i in range(PAGE_SIZE)]), _page([_row(999)])]
        fetch_wishlist_album_ids(FakeSession(pages), 1, governor=gov)
        # Spacing between pages, but never before the first — that would add
        # latency to a walk that is already the most expensive call available.
        assert gov.wait_turn.call_count == 1
        gov.wait_turn.assert_called_with(FANCOLLECTION)

    def test_the_page_cap_truncates_and_says_so(self, caplog: Any) -> None:
        full = _page([_row(i) for i in range(PAGE_SIZE)])
        ids = fetch_wishlist_album_ids(
            FakeSession([full]), 1, governor=_gov(), max_pages=3
        )
        assert len(ids) == PAGE_SIZE
        assert "partial" in caplog.text


class TestWalkFailures:
    def test_a_429_keeps_what_was_already_collected(self) -> None:
        """A partial exclusion beats none, and discarding pages would make the
        most expensive call in the design also the most wasteful."""
        gov = _gov()
        pages = [
            FakeResponse(_page([_row(i) for i in range(PAGE_SIZE)])),
            FakeResponse(status_code=429),
        ]
        ids = fetch_wishlist_album_ids(FakeSession(pages), 1, governor=gov)
        assert len(ids) == PAGE_SIZE
        gov.report_429.assert_called_once_with(FANCOLLECTION)

    @pytest.mark.parametrize("status", [401, 403, 302, 500])
    def test_a_rejection_never_clears_the_session(self, status: int) -> None:
        """_paginate clears the Bandcamp session on 401/403. Logging the user
        out because a discovery nicety got a 403 is wildly disproportionate, so
        this returns empty and leaves the session alone."""
        session = FakeSession([FakeResponse(status_code=status)])
        assert fetch_wishlist_album_ids(session, 1, governor=_gov()) == set()

    def test_a_network_error_returns_what_it_had(self) -> None:
        pages = [
            FakeResponse(_page([_row(i) for i in range(PAGE_SIZE)])),
            RuntimeError("connection reset"),
        ]
        ids = fetch_wishlist_album_ids(FakeSession(pages), 1, governor=_gov())
        assert len(ids) == PAGE_SIZE

    def test_an_unparseable_page_is_not_fatal(self) -> None:
        session = FakeSession([FakeResponse(ValueError("not json"))])
        assert fetch_wishlist_album_ids(session, 1, governor=_gov()) == set()


class TestMarkingCrateItems:
    """The crate on screen was assembled against whatever was cached at the
    time — nothing at all, on the first build after launch."""

    def _crate(self, index: Any, ids: list[str]) -> list[int]:
        rows = []
        for position, provider_item_id in enumerate(ids):
            row = index.add_discovery_candidate(
                provider="bandcamp", provider_item_id=provider_item_id, title="X"
            )
            index.place_in_crate(row, 1, position)
            rows.append(row)
        return rows

    def test_a_wishlisted_item_gets_the_state(self, index: Any) -> None:
        from kamp_daemon.wishlist import mark_wishlisted_crate_items

        self._crate(index, ["1", "2", "3"])
        assert mark_wishlisted_crate_items(index, {"2"}) == 1
        states = {r["provider_item_id"]: r["state"] for r in index.crate_items(1)}
        assert states == {"1": "fresh", "2": "wishlisted", "3": "fresh"}

    def test_marking_is_idempotent(self, index: Any) -> None:
        from kamp_daemon.wishlist import mark_wishlisted_crate_items

        self._crate(index, ["1"])
        mark_wishlisted_crate_items(index, {"1"})
        assert mark_wishlisted_crate_items(index, {"1"}) == 0

    def test_a_purchased_pick_is_not_demoted(self, index: Any) -> None:
        """record_discovery_event is highest-rank-wins, and this must not fight
        it: bought outranks wishlisted."""
        from kamp_daemon.wishlist import mark_wishlisted_crate_items

        rows = self._crate(index, ["1"])
        index.record_discovery_event(rows[0], "purchased")
        mark_wishlisted_crate_items(index, {"1"})
        assert index.crate_items(1)[0]["state"] == "purchased"

    def test_no_crate_is_harmless(self, index: Any) -> None:
        from kamp_daemon.wishlist import mark_wishlisted_crate_items

        assert mark_wishlisted_crate_items(index, {"1"}) == 0


class TestCache:
    def _cache(self, t: list[float]) -> WishlistCache:
        return WishlistCache(ttl=100.0, now=lambda: t[0])

    def test_empty_before_the_first_walk(self) -> None:
        cache = WishlistCache()
        assert cache.ids == set()
        assert cache.is_fresh is False

    def test_refresh_populates_and_marks_fresh(self) -> None:
        t = [1000.0]
        cache = self._cache(t)
        page = _page([_row(1), _row(2)], last_token="")
        cache.refresh(FakeSession([page]), 42, governor=_gov())
        assert cache.ids == {"1", "2"}
        assert cache.is_fresh is True

    def test_a_fresh_cache_is_not_re_walked(self) -> None:
        """Nine pages amortised across many crates is the whole point."""
        t = [1000.0]
        cache = self._cache(t)
        session = FakeSession([_page([_row(1)], last_token="")])
        cache.refresh(session, 42, governor=_gov())
        cache.refresh(session, 42, governor=_gov())
        assert len(session.posts) == 1

    def test_an_expired_cache_is_re_walked(self) -> None:
        """Un-wishlisting in a browser must not be suppressed forever."""
        t = [1000.0]
        cache = self._cache(t)
        session = FakeSession([_page([_row(1)], last_token="")])
        cache.refresh(session, 42, governor=_gov())
        t[0] += 101.0
        cache.refresh(session, 42, governor=_gov())
        assert len(session.posts) == 2

    def test_a_cooldown_skips_the_walk_without_a_request(self) -> None:
        """Consulted, never waited on — a background thread parked for 300s on
        this endpoint is how it ends up back on a blocking path."""
        gov = _gov()
        gov.blocked_for.return_value = 300.0
        session = FakeSession([_page([_row(1)], last_token="")])
        WishlistCache().refresh(session, 42, governor=gov)
        assert session.posts == []
        gov.wait_turn.assert_not_called()

    def test_a_failed_walk_keeps_the_previous_ids(self) -> None:
        """An empty result is far likelier to be a failed walk than an empty
        wishlist; replacing good data with nothing turns the filter off."""
        t = [1000.0]
        cache = self._cache(t)
        cache.refresh(
            FakeSession([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        t[0] += 101.0
        cache.refresh(FakeSession([FakeResponse(status_code=500)]), 42, governor=_gov())
        assert cache.ids == {"1"}

    def test_add_records_kamps_own_write(self) -> None:
        cache = WishlistCache()
        cache.add("77")
        assert "77" in cache.ids

    def test_invalidate_drops_everything(self) -> None:
        """The ids belong to an account that can change."""
        cache = WishlistCache()
        cache.refresh(
            FakeSession([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        cache.invalidate()
        assert cache.ids == set()
        assert cache.is_fresh is False

    def test_ids_returns_a_copy(self) -> None:
        cache = WishlistCache()
        cache.add("1")
        cache.ids.add("2")
        assert cache.ids == {"1"}
