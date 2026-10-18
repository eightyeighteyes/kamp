"""Wishlist walk + walker tests (KAMP-652, persisted by KAMP-666).

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
from kamp_daemon.discovery import FANCOLLECTION, PREVIEW, SAVE_REMOTE
from kamp_daemon.wishlist import (
    PAGE_SIZE,
    WishlistWalker,
    walk_wishlist,
    write_wishlist,
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
        "item_url": f"https://band.bandcamp.com/album/{tralbum_id}",
        "item_art_id": "900",
        "band_id": "7",
        "added": "01 Jan 2025 00:00:00 GMT",
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


def _ids(result: Any) -> set[str]:
    return {str(row["tralbum_id"]) for row in result.rows}


class TestWalk:
    def test_the_first_page_carries_a_seeded_token(self) -> None:
        """Omitting it returns zero rows with NO error — indistinguishable from
        an empty wishlist, and exactly how this looked when first measured."""
        session = FakeSession([_page([_row(1)], last_token="")])
        walk_wishlist(session, 42, governor=_gov(), now=lambda: 1000.0)

        sent = session.posts[0]
        assert sent["older_than_token"] == "1000:0:a::"
        assert sent["fan_id"] == 42
        assert sent["count"] == PAGE_SIZE

    def test_paginates_until_a_short_page(self) -> None:
        pages = [_page([_row(i) for i in range(PAGE_SIZE)]), _page([_row(999)])]
        result = walk_wishlist(FakeSession(pages), 1, governor=_gov())
        assert "999" in _ids(result)
        assert len(result.rows) == PAGE_SIZE + 1
        assert result.complete is True

    def test_stops_on_a_falsy_token_even_with_a_full_page(self) -> None:
        """The shipped collection walk does not trust more_available; nor does
        this — a full page with no token is the end."""
        full = _page([_row(i) for i in range(PAGE_SIZE)], last_token="")
        session = FakeSession([full, _page([_row(12345)])])
        result = walk_wishlist(session, 1, governor=_gov())
        assert len(session.posts) == 1
        assert "12345" not in _ids(result)
        assert result.complete is True

    def test_tracks_come_back_as_rows_too(self) -> None:
        """The walk no longer filters; the table stores both types and the read
        accessor filters to albums.

        Storing tracks is what lets the prune delete an un-wishlisted track,
        and keying on (tralbum_id, tralbum_type) is what stops one shadowing an
        album with the same numeral — the thing the old set-building filter was
        protecting against by throwing the track away.
        """
        page = _page([_row(1, "a"), _row(2, "t"), _row(3, "a")], last_token="")
        result = walk_wishlist(FakeSession([page]), 1, governor=_gov())
        assert {(str(r["tralbum_id"]), r["tralbum_type"]) for r in result.rows} == {
            ("1", "a"),
            ("2", "t"),
            ("3", "a"),
        }

    def test_a_row_with_no_id_is_dropped(self) -> None:
        """An id-less row is an exclusion entry that excludes nothing."""
        page = _page([{"tralbum_type": "a", "band_name": "Band"}], last_token="")
        result = walk_wishlist(FakeSession([page]), 1, governor=_gov())
        assert result.rows == []
        assert result.complete is True

    def test_item_id_stands_in_for_a_missing_tralbum_id(self) -> None:
        """Measured on a real account they agree on every row, but the fallback
        was there before and removing it would be an unforced regression."""
        page = _page([{"item_id": 55, "tralbum_type": "a"}], last_token="")
        result = walk_wishlist(FakeSession([page]), 1, governor=_gov())
        assert _ids(result) == {"55"}

    def test_pages_after_the_first_are_governor_spaced(self) -> None:
        gov = _gov()
        pages = [_page([_row(i) for i in range(PAGE_SIZE)]), _page([_row(999)])]
        walk_wishlist(FakeSession(pages), 1, governor=gov)
        # Spacing between pages, but never before the first — that would add
        # latency to a walk that is already the most expensive call available.
        assert gov.wait_turn.call_count == 1
        gov.wait_turn.assert_called_with(FANCOLLECTION)

    def test_the_page_cap_is_incomplete_not_complete(self, caplog: Any) -> None:
        """Truncation is a partial view, so it must not license a prune."""
        full = _page([_row(i) for i in range(PAGE_SIZE)])
        result = walk_wishlist(FakeSession([full]), 1, governor=_gov(), max_pages=3)
        assert len(result.rows) == PAGE_SIZE * 3
        assert result.complete is False
        assert "partial" in caplog.text

    def test_rows_are_not_deduplicated_by_the_walk(self, index: Any) -> None:
        """A list, not a set — the composite primary key does the deduplicating.

        Worth pinning: the KAMP-652 walk returned a set, so a page the session
        happened to serve twice collapsed for free. The upsert is what makes
        that safe now, and doing it in SQL rather than in the walk is also what
        lets a track and an album share a numeral.
        """
        repeated = _page([_row(1), _row(1), _row(1, "t")], last_token="")
        result = walk_wishlist(FakeSession([repeated]), 1, governor=_gov())
        assert len(result.rows) == 3
        index.upsert_wishlist_items(result.rows, walked_at=100.0)
        assert (
            index._conn.execute("SELECT COUNT(*) FROM bandcamp_wishlist").fetchone()[0]
            == 2
        )
        assert index.wishlist_album_ids() == {"1"}


class TestWalkFailures:
    """Every failure path reports ``complete=False``.

    ``-> set[str]`` could not distinguish any of these from a genuinely empty
    or genuinely complete wishlist, which is what made "delete rows no longer
    present" unsafe to build on it: a 429 on page 2 of 9 would have deleted
    ~625 real rows from disk.
    """

    def test_a_429_keeps_what_was_already_collected(self) -> None:
        """A partial exclusion beats none, and discarding pages would make the
        most expensive call in the design also the most wasteful."""
        gov = _gov()
        pages = [
            FakeResponse(_page([_row(i) for i in range(PAGE_SIZE)])),
            FakeResponse(status_code=429),
        ]
        result = walk_wishlist(FakeSession(pages), 1, governor=gov)
        assert len(result.rows) == PAGE_SIZE
        assert result.complete is False
        gov.report_429.assert_called_once_with(FANCOLLECTION)

    @pytest.mark.parametrize("status", [401, 403, 302, 500])
    def test_a_rejection_never_clears_the_session(self, status: int) -> None:
        """_paginate clears the Bandcamp session on 401/403. Logging the user
        out because a discovery nicety got a 403 is wildly disproportionate, so
        this returns empty and leaves the session alone."""
        session = FakeSession([FakeResponse(status_code=status)])
        result = walk_wishlist(session, 1, governor=_gov())
        assert result.rows == []
        assert result.complete is False

    def test_a_network_error_returns_what_it_had(self) -> None:
        pages = [
            FakeResponse(_page([_row(i) for i in range(PAGE_SIZE)])),
            RuntimeError("connection reset"),
        ]
        result = walk_wishlist(FakeSession(pages), 1, governor=_gov())
        assert len(result.rows) == PAGE_SIZE
        assert result.complete is False

    def test_an_unparseable_page_is_not_fatal(self) -> None:
        session = FakeSession([FakeResponse(ValueError("not json"))])
        result = walk_wishlist(session, 1, governor=_gov())
        assert result.rows == []
        assert result.complete is False


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


class TestWalker:
    """The walker is coordination over a persisted table, not a cache.

    The ids live in bandcamp_wishlist and survive a restart — that is the whole
    of KAMP-666. What is still in memory is the concurrent-walk guard and the
    removal-during-walk set, neither of which a column can express.
    """

    def _walker(self, index: Any, t: list[float]) -> WishlistWalker:
        return WishlistWalker(index, ttl=100.0, now=lambda: t[0])

    def test_empty_before_the_first_walk(self, index: Any) -> None:
        walker = WishlistWalker(index)
        assert walker.album_ids == set()
        assert walker.is_fresh is False

    def test_a_restart_keeps_the_ids(self, tmp_path: Path) -> None:
        """The headline acceptance criterion: exclusion works from the first
        crate after a restart, with no 37s walk in front of it."""
        db = tmp_path / "library.db"
        first = LibraryIndex(db)
        WishlistWalker(first, now=lambda: 1000.0).refresh(
            FakeSession([_page([_row(1), _row(2)], last_token="")]),
            42,
            governor=_gov(),
        )
        first.close()

        second = LibraryIndex(db)
        walker = WishlistWalker(second, now=lambda: 1001.0)
        assert walker.album_ids == {"1", "2"}
        assert walker.is_fresh is True
        second.close()

    def test_refresh_populates_and_marks_fresh(self, index: Any) -> None:
        t = [1000.0]
        walker = self._walker(index, t)
        page = _page([_row(1), _row(2)], last_token="")
        walker.refresh(FakeSession([page]), 42, governor=_gov())
        assert walker.album_ids == {"1", "2"}
        assert walker.is_fresh is True

    def test_a_fresh_walk_is_not_repeated(self, index: Any) -> None:
        """Nine pages amortised across many crates is the whole point."""
        t = [1000.0]
        walker = self._walker(index, t)
        session = FakeSession([_page([_row(1)], last_token="")])
        walker.refresh(session, 42, governor=_gov())
        walker.refresh(session, 42, governor=_gov())
        assert len(session.posts) == 1

    def test_a_stale_walk_is_repeated(self, index: Any) -> None:
        """Un-wishlisting in a browser must not be suppressed forever."""
        t = [1000.0]
        walker = self._walker(index, t)
        session = FakeSession([_page([_row(1)], last_token="")])
        walker.refresh(session, 42, governor=_gov())
        t[0] += 101.0
        walker.refresh(session, 42, governor=_gov())
        assert len(session.posts) == 2

    def test_a_cooldown_skips_the_walk_without_a_request(self, index: Any) -> None:
        """Consulted, never waited on — a background thread parked for 300s on
        this endpoint is how it ends up back on a blocking path."""
        gov = _gov()
        gov.blocked_for.return_value = 300.0
        session = FakeSession([_page([_row(1)], last_token="")])
        WishlistWalker(index).refresh(session, 42, governor=gov)
        assert session.posts == []
        gov.wait_turn.assert_not_called()

    def test_a_concurrent_walk_is_refused(self, index: Any) -> None:
        """The reason this class survives KAMP-666 at all.

        A build is 15-30s and a walk ~40s, and the warm-up is spawned
        fire-and-forget, so dig → wait → dig overlaps them routinely. Two
        nine-page walks at once is the KAMP-637/639 cascade on the endpoint
        this module's docstring calls the hardest-limited one.
        """
        walker = WishlistWalker(index)
        reentered: list[int] = []

        class WalkDuringWalk(FakeSession):
            def post(self, url, json=None, timeout=30, headers=None):  # type: ignore[no-untyped-def]
                inner = FakeSession([_page([_row(9)], last_token="")])
                walker.refresh(inner, 42, governor=_gov())
                reentered.append(len(inner.posts))
                return super().post(url, json=json, timeout=timeout, headers=headers)

        walker.refresh(
            WalkDuringWalk([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        assert reentered == [0]  # the inner walk made no request at all


class TestWalkerCompleteness:
    """A partial walk must never delete a row.

    The KAMP-652 walk had seven return points, four of them failures, all
    returning an indistinguishable ``set[str]``. Pruning against that would
    delete ~625 real rows on a 429 at page 2 of 9, which is why the completeness
    flag arrived in the same change as the table.
    """

    def test_a_complete_walk_prunes_what_it_did_not_see(self, index: Any) -> None:
        """An album un-wishlisted upstream becomes offerable again."""
        t = [1000.0]
        walker = WishlistWalker(index, ttl=100.0, now=lambda: t[0])
        walker.refresh(
            FakeSession([_page([_row(1), _row(2)], last_token="")]), 42, governor=_gov()
        )
        t[0] += 101.0
        walker.refresh(
            FakeSession([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        assert walker.album_ids == {"1"}

    def test_an_incomplete_walk_writes_its_rows_but_deletes_nothing(
        self, index: Any
    ) -> None:
        """A partial result still improves the filter; it just cannot prune."""
        t = [1000.0]
        walker = WishlistWalker(index, ttl=100.0, now=lambda: t[0])
        walker.refresh(
            FakeSession([_page([_row(1), _row(2)], last_token="")]), 42, governor=_gov()
        )
        t[0] += 101.0

        # A full first page, then a 429 — nothing says 1 and 2 are still there.
        walker.refresh(
            FakeSession(
                [
                    FakeResponse(_page([_row(i) for i in range(PAGE_SIZE)])),
                    FakeResponse(status_code=429),
                ]
            ),
            42,
            governor=_gov(),
        )
        assert {"1", "2"} <= walker.album_ids
        assert "50" in walker.album_ids  # the page it did get was still written

    def test_an_incomplete_walk_does_not_stamp_itself_fresh(self, index: Any) -> None:
        """The live miniature of the same bug: the old cache marked itself fresh
        for any non-empty result, so a 429 after page 1 suppressed re-walks for
        an hour with 100 of 825 ids."""
        t = [1000.0]
        walker = WishlistWalker(index, ttl=100.0, now=lambda: t[0])
        walker.refresh(
            FakeSession(
                [
                    FakeResponse(_page([_row(i) for i in range(PAGE_SIZE)])),
                    FakeResponse(status_code=429),
                ]
            ),
            42,
            governor=_gov(),
        )
        assert walker.is_fresh is False

        session = FakeSession([_page([_row(1)], last_token="")])
        walker.refresh(session, 42, governor=_gov())
        assert len(session.posts) == 1  # re-walked immediately, not in an hour

    def test_a_complete_walk_of_an_empty_wishlist_empties_the_table(
        self, index: Any
    ) -> None:
        """The case the old "an empty result means a failed walk" heuristic got
        wrong. Completeness answers it directly: emptying every record off a
        wishlist is a thing people do, and the filter must follow."""
        t = [1000.0]
        walker = WishlistWalker(index, ttl=100.0, now=lambda: t[0])
        walker.refresh(
            FakeSession([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        t[0] += 101.0
        walker.refresh(FakeSession([_page([], last_token="")]), 42, governor=_gov())
        assert walker.album_ids == set()
        assert walker.is_fresh is True

    def test_a_failed_walk_keeps_what_was_on_disk(self, index: Any) -> None:
        """Replacing good data with nothing would quietly turn the filter off."""
        t = [1000.0]
        walker = WishlistWalker(index, ttl=100.0, now=lambda: t[0])
        walker.refresh(
            FakeSession([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        t[0] += 101.0
        walker.refresh(
            FakeSession([FakeResponse(status_code=500)]), 42, governor=_gov()
        )
        assert walker.album_ids == {"1"}


class TestWalkerOwnWrites:
    """kamp's own wishlist clicks (KAMP-653) land on disk immediately."""

    def test_add_writes_through(self, index: Any) -> None:
        walker = WishlistWalker(index)
        walker.add("77")
        assert index.wishlist_album_ids() == {"77"}

    def test_discard_writes_through(self, index: Any) -> None:
        """Without it, an album the user just un-wishlisted stays excluded from
        every crate until the next walk, with nothing to explain why."""
        walker = WishlistWalker(index)
        walker.refresh(
            FakeSession([_page([_row(1), _row(2)], last_token="")]), 42, governor=_gov()
        )
        walker.discard("1")
        assert walker.album_ids == {"2"}

    def test_an_add_during_a_walk_survives_the_walk_landing(self, index: Any) -> None:
        """Survives by design, not by luck: the row goes in with a NULL
        walked_at, the walk's own rows are stamped with its start time, and the
        prune only touches rows that carry a stamp older than that."""
        walker = WishlistWalker(index, now=lambda: 1000.0)
        clicked: list[str] = []

        class ClickMidWalk(FakeSession):
            def post(self, url, json=None, timeout=30, headers=None):  # type: ignore[no-untyped-def]
                if not clicked:
                    clicked.append("77")
                    walker.add("77")
                return super().post(url, json=json, timeout=timeout, headers=headers)

        # The walk's own view is stale: it has never seen 77.
        walker.refresh(
            ClickMidWalk([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        assert walker.album_ids == {"1", "77"}

    def test_a_removal_during_a_walk_is_not_undone_by_it(self, index: Any) -> None:
        """The case no column can carry. The row is gone, so there is nothing
        left to timestamp, and the walk — which read Bandcamp ~40s ago and
        still sees it — would simply put it back."""
        walker = WishlistWalker(index, now=lambda: 1000.0)
        clicked: list[str] = []

        class ClickMidWalk(FakeSession):
            def post(self, url, json=None, timeout=30, headers=None):  # type: ignore[no-untyped-def]
                if not clicked:
                    clicked.append("1")
                    walker.discard("1")
                return super().post(url, json=json, timeout=timeout, headers=headers)

        walker.refresh(
            ClickMidWalk([_page([_row(1), _row(2)], last_token="")]),
            42,
            governor=_gov(),
        )
        assert walker.album_ids == {"2"}

    def test_a_re_add_during_a_walk_beats_the_removal(self, index: Any) -> None:
        """Last write wins. Replaying the removal would undo a click Bandcamp
        has already confirmed."""
        walker = WishlistWalker(index, now=lambda: 1000.0)
        clicked: list[str] = []

        class ClickMidWalk(FakeSession):
            def post(self, url, json=None, timeout=30, headers=None):  # type: ignore[no-untyped-def]
                if not clicked:
                    clicked.append("1")
                    walker.discard("1")
                    walker.add("1")
                return super().post(url, json=json, timeout=timeout, headers=headers)

        walker.refresh(
            ClickMidWalk([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        assert walker.album_ids == {"1"}

    def test_mutations_outside_a_walk_do_not_leak_into_the_next_one(
        self, index: Any
    ) -> None:
        """A removal made before a walk is already reflected in what that walk
        read, and replaying it would pin the row deleted past a genuine
        re-wishlisting made in a browser."""
        t = [1000.0]
        walker = WishlistWalker(index, ttl=100.0, now=lambda: t[0])
        walker.discard("1")
        walker.refresh(
            FakeSession([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        assert walker.album_ids == {"1"}

    def test_invalidate_drops_the_rows_and_the_freshness(self, index: Any) -> None:
        """The ids belong to an account that can change, and persisted rows
        carry the other account's band_name and item_title."""
        walker = WishlistWalker(index, now=lambda: 1000.0)
        walker.refresh(
            FakeSession([_page([_row(1)], last_token="")]), 42, governor=_gov()
        )
        walker.invalidate()
        assert walker.album_ids == set()
        assert walker.is_fresh is False


class _FakeSource:
    """Stands in for BandcampDiscoverySource at the write boundary."""

    def __init__(
        self,
        *,
        result: Any = True,
        caps: frozenset[str] = frozenset({PREVIEW, SAVE_REMOTE}),
    ) -> None:
        self.result = result
        self.capabilities = caps
        self.saved: list[Any] = []
        self.unsaved: list[Any] = []

    def _answer(self, candidate: Any) -> bool:
        if isinstance(self.result, Exception):
            raise self.result
        return bool(self.result)

    def save_remote(self, candidate: Any) -> bool:
        self.saved.append(candidate)
        return self._answer(candidate)

    def unsave_remote(self, candidate: Any) -> bool:
        self.unsaved.append(candidate)
        return self._answer(candidate)


_ITEM = {
    "id": 1,
    "provider": "bandcamp",
    "provider_item_id": "555",
    "item_url": "https://a.bandcamp.com/album/x",
    "artist": "Artist",
    "title": "Title",
}


class TestWriteWishlist:
    def test_a_confirmed_add_reports_ok_and_records_the_row(self, index: Any) -> None:
        walker = WishlistWalker(index)
        source = _FakeSource()
        assert write_wishlist(source, _ITEM, add=True, walker=walker) == "ok"
        assert walker.album_ids == {"555"}
        assert source.saved[0].item_url == "https://a.bandcamp.com/album/x"

    def test_a_confirmed_removal_drops_the_row(self, index: Any) -> None:
        """Otherwise the album the user just removed stays excluded from every
        crate until the next walk, with nothing to explain why."""
        walker = WishlistWalker(index)
        walker.add("555")
        assert write_wishlist(_FakeSource(), _ITEM, add=False, walker=walker) == "ok"
        assert walker.album_ids == set()

    def test_nothing_is_written_when_the_remote_write_fails(self, index: Any) -> None:
        walker = WishlistWalker(index)
        source = _FakeSource(result=False)
        assert write_wishlist(source, _ITEM, add=True, walker=walker) == "rejected"
        assert walker.album_ids == set()

    def test_no_source_is_not_connected(self) -> None:
        assert write_wishlist(None, _ITEM, add=True) == "not_connected"

    def test_a_source_without_the_capability_is_unsupported_not_rejected(self) -> None:
        """The capability gate, and the first thing in the codebase to read the
        property. Without it, a provider that cannot write is indistinguishable
        from Bandcamp saying no — and the UI would blame Bandcamp."""
        source = _FakeSource(caps=frozenset({PREVIEW}))
        assert write_wishlist(source, _ITEM, add=True) == "unsupported"
        assert source.saved == []

    def test_a_rate_limit_is_reported_as_such(self) -> None:
        from kamp_daemon.discovery_sources import RateLimitedError

        source = _FakeSource(result=RateLimitedError("cooling down"))
        assert write_wishlist(source, _ITEM, add=True) == "rate_limited"

    def test_an_expired_session_asks_for_a_login(self) -> None:
        from kamp_daemon.bandcamp import NeedsLoginError

        source = _FakeSource(result=NeedsLoginError("session rejected"))
        assert write_wishlist(source, _ITEM, add=True) == "needs_login"

    def test_an_unexpected_error_is_rejected_rather_than_raised(self) -> None:
        """A failed nicety must not 500 the endpoint."""
        source = _FakeSource(result=RuntimeError("boom"))
        assert write_wishlist(source, _ITEM, add=True) == "rejected"

    def test_the_candidate_carries_the_identity_the_post_needs(self) -> None:
        source = _FakeSource()
        write_wishlist(source, _ITEM, add=True)
        candidate = source.saved[0]
        assert candidate.provider_item_id == "555"
        assert candidate.provider == "bandcamp"
