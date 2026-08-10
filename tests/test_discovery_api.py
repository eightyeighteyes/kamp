"""Discovery Crate REST + WS surface tests (KAMP-648).

Most tests register the routes on a bare FastAPI app with a recording broadcast,
so the snapshot the WebSocket would carry is inspectable — create_app's
``_broadcast`` deliberately no-ops with no client attached, which would hide
exactly the parity this story has to guarantee. One test goes the long way
through ``create_app`` to prove the routes are actually wired and that the auth
middleware covers a module it knows nothing about.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kamp_core.discovery_api import CRATE_EVENT, register_discovery_routes
from kamp_core.library import LibraryIndex

# Sentinel so a test can pass wishlist_write=None (the not-connected case) and
# have it mean None rather than "use the default".
_UNSET = object()


@pytest.fixture
def index(tmp_path: Path) -> Iterator[LibraryIndex]:
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


class _Harness:
    def __init__(
        self,
        index: LibraryIndex,
        on_build_start: Any = None,
        art_cache_dir: Path | None = None,
        art_bytes: bytes | None = b"JPEGDATA",
        wishlist_write: Any = _UNSET,
    ) -> None:
        self.events: list[dict[str, Any]] = []
        self.build_calls = 0
        self.fetched: list[str] = []
        self.art_bytes = art_bytes
        self.wishlist_calls: list[tuple[dict[str, Any], bool]] = []
        self.wishlist_reason = "ok"
        self.app = FastAPI()

        def _default_wishlist(item: dict[str, Any], add: bool) -> str:
            self.wishlist_calls.append((item, add))
            return self.wishlist_reason

        def _default_start() -> None:
            self.build_calls += 1

        def _fetch(url: str) -> bytes | None:
            self.fetched.append(url)
            return self.art_bytes

        register_discovery_routes(
            self.app,
            index=index,
            broadcast=self.events.append,
            on_build_start=(
                _default_start if on_build_start is None else on_build_start
            ),
            art_cache_dir=art_cache_dir,
            fetch_bytes=_fetch,
            wishlist_write=(
                _default_wishlist if wishlist_write is _UNSET else wishlist_write
            ),
        )
        self.client = TestClient(self.app)

    @property
    def publish(self) -> Any:
        return self.app.state.discovery_publish


@pytest.fixture
def harness(index: LibraryIndex) -> _Harness:
    return _Harness(index)


@pytest.fixture
def art_dir(tmp_path: Path) -> Path:
    return tmp_path / "art_cache"


def _stock(index: LibraryIndex, crate_no: int, count: int = 3) -> None:
    for position in range(count):
        item = index.add_discovery_candidate(
            provider="bandcamp",
            provider_item_id=f"{crate_no}-{position}",
            artist=f"Artist {position}",
            title=f"Title {position}",
            criterion="also_like",
            why="because",
            seed_json='{"kind": "album"}',
        )
        index.place_in_crate(item, crate_no, position)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestCrateSnapshot:
    def test_first_launch_is_an_empty_crate_not_an_error(
        self, harness: _Harness
    ) -> None:
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["state"] == "idle"
        assert body["items"] == []
        assert body["crate_no"] is None
        assert body["paused_until"] == 0.0

    def test_snapshot_returns_the_latest_crate_after_a_restart(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """The status is in-memory; the crate is not. A fresh daemon must still
        serve the last crate, which is the whole reconnect path."""
        _stock(index, 1)
        _stock(index, 2, count=2)
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["crate_no"] == 2
        assert [item["title"] for item in body["items"]] == ["Title 0", "Title 1"]

    def test_rest_and_ws_snapshots_are_the_same_shape(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        _stock(index, 1)
        harness.publish({"state": "ready", "crate_no": 1})
        pushed = harness.events[-1]
        rest = harness.client.get("/api/v1/discovery/crate").json()

        assert pushed["type"] == CRATE_EVENT
        assert {k: v for k, v in pushed.items() if k != "type"} == rest

    def test_a_restored_crate_reports_what_is_actually_in_it(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """The status is in-memory and the crate is not, so after a daemon restart
        _status is still _INITIAL_STATUS while items holds a full crate.

        Reporting filled=0 for a crate of ten made the UI's fill progress wrong on
        every launch, and short=False for a crate that really is short suppressed
        the quiet note. Both are derived from items whenever no build is running.
        """
        _stock(index, 1, count=10)
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["state"] == "idle"
        assert body["filled"] == 10
        assert body["short"] is False

    def test_a_restored_short_crate_still_reads_as_short(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        _stock(index, 1, count=4)
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["filled"] == 4
        assert body["short"] is True

    def test_a_live_build_keeps_its_own_progress(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """While building, filled counts what has landed so far and must NOT be
        overwritten by the (already larger) row count of a previous crate."""
        _stock(index, 1, count=10)
        harness.publish({"state": "building", "filled": 3, "short": False})
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["filled"] == 3

    def test_publishing_pushes_to_clients(self, harness: _Harness) -> None:
        harness.publish({"state": "building", "hints": ["dub techno"]})
        assert harness.events[-1]["state"] == "building"
        assert harness.events[-1]["hints"] == ["dub techno"]


# ---------------------------------------------------------------------------
# Build lifecycle
# ---------------------------------------------------------------------------


class TestBuildLifecycle:
    def test_new_crate_starts_a_build(self, harness: _Harness) -> None:
        assert harness.client.post("/api/v1/discovery/crate/new").status_code == 200
        assert harness.build_calls == 1

    def test_a_second_build_is_refused_while_one_runs(self, harness: _Harness) -> None:
        """The flag is set synchronously in the route, not by the worker thread.

        start_genre_backfill tests a flag only its worker sets, so two rapid
        POSTs both spawn a thread and both are told they started. For a crate
        that leaves the loser's spinner unresolved.
        """
        harness.client.post("/api/v1/discovery/crate/new")
        second = harness.client.post("/api/v1/discovery/crate/new")
        assert second.status_code == 409
        assert harness.build_calls == 1

    @pytest.mark.parametrize("state", ["ready", "empty", "error", "paused"])
    def test_every_terminal_state_releases_the_lock(
        self, harness: _Harness, state: str
    ) -> None:
        """A state missing from the terminal set wedges the feature on 409 until
        the daemon restarts, so each one is pinned individually."""
        harness.client.post("/api/v1/discovery/crate/new")
        harness.publish({"state": state})
        assert harness.client.post("/api/v1/discovery/crate/new").status_code == 200
        assert harness.build_calls == 2

    def test_a_non_terminal_state_does_not_release_the_lock(
        self, harness: _Harness
    ) -> None:
        harness.client.post("/api/v1/discovery/crate/new")
        harness.publish({"state": "building", "filled": 4})
        assert harness.client.post("/api/v1/discovery/crate/new").status_code == 409

    def test_a_failed_start_releases_the_lock(self, index: LibraryIndex) -> None:
        """Otherwise one transient failure disables digging until restart."""

        def _boom() -> None:
            raise RuntimeError("no session")

        harness = _Harness(index, on_build_start=_boom)
        assert harness.client.post("/api/v1/discovery/crate/new").status_code == 500
        # Not a 409 the second time round.
        assert harness.client.post("/api/v1/discovery/crate/new").status_code == 500

    def test_no_callback_reports_unavailable(self, index: LibraryIndex) -> None:
        app = FastAPI()
        register_discovery_routes(
            app, index=index, broadcast=lambda _e: None, on_build_start=None
        )
        assert TestClient(app).post("/api/v1/discovery/crate/new").status_code == 503


# ---------------------------------------------------------------------------
# Per-item engagement
# ---------------------------------------------------------------------------


class TestItemEvents:
    def _item(self, index: LibraryIndex) -> int:
        item = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="x", title="X"
        )
        index.place_in_crate(item, 1, 0)
        return item

    def test_there_is_no_way_to_pass_on_a_record(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """The dismiss endpoint is gone (KAMP-674).

        Not merely unused by the UI: passing changed nothing a user could
        observe, so leaving the writer reachable would keep minting 'dismissed'
        rows that nothing reads. 405 rather than 404 because FastAPI still has a
        route for the {item_id} prefix.
        """
        item = self._item(index)
        assert harness.client.post(
            f"/api/v1/discovery/items/{item}/dismiss"
        ).status_code in (404, 405)
        assert index.crate_items(1)[0]["state"] == "fresh"

    def test_copying_a_link_is_not_passing_on_it(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """url_copied is engagement, not rejection — the user may still preview
        or wishlist afterwards, so the card must not grey out."""
        item = self._item(index)
        harness.client.post(f"/api/v1/discovery/items/{item}/url-copied")
        assert index.crate_items(1)[0]["state"] == "fresh"
        assert (
            index._conn.execute(
                "SELECT COUNT(*) AS c FROM discovery_events WHERE kind = 'url_copied'"
            ).fetchone()["c"]
            == 1
        )

    def test_an_item_event_pushes_a_fresh_snapshot(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        item = self._item(index)
        harness.events.clear()
        harness.client.post(f"/api/v1/discovery/items/{item}/url-copied")
        assert len(harness.events) == 1
        assert [i["id"] for i in harness.events[-1]["items"]] == [item]

    def test_unknown_item_is_a_404(self, harness: _Harness) -> None:
        assert (
            harness.client.post("/api/v1/discovery/items/999/url-copied").status_code
            == 404
        )


# ---------------------------------------------------------------------------
# Digging history (KAMP-655)
# ---------------------------------------------------------------------------


class TestDiggingHistoryRoutes:
    def test_the_snapshot_carries_the_history(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """Riding the snapshot is what keeps the numbers live without a second
        request, and what stops the tally and the lifetime line disagreeing."""
        _stock(index, 1, count=2)
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["stats"]["records"] == 2
        assert body["crate_stats"]["records"] == 2

    def test_an_empty_library_reports_no_crate_stats(self, harness: _Harness) -> None:
        """There is no crate to tally, which is different from a crate of zero."""
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["stats"]["records"] == 0
        assert body["crate_stats"] is None

    def test_the_endpoint_and_the_snapshot_agree(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        _stock(index, 1, count=3)
        endpoint = harness.client.get("/api/v1/discovery/stats").json()["stats"]
        snapshot = harness.client.get("/api/v1/discovery/crate").json()["stats"]
        assert endpoint == snapshot

    def test_the_numbers_move_with_an_event(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        _stock(index, 1, count=2)
        item = index.crate_items(1)[0]["id"]
        harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        assert harness.events[-1]["stats"]["wishlisted"] == 1

    def test_clearing_stats_keeps_the_crate(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        _stock(index, 1, count=2)
        item = index.crate_items(1)[0]["id"]
        harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")

        resp = harness.client.delete("/api/v1/discovery/history")

        assert resp.status_code == 200
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["stats"]["wishlisted"] == 0
        assert body["stats"]["records"] == 2, "the records themselves stay"
        assert len(body["items"]) == 2

    def test_clearing_everything_empties_the_crate(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        _stock(index, 1, count=2)
        harness.client.delete("/api/v1/discovery/history?forget_seen=true")
        body = harness.client.get("/api/v1/discovery/crate").json()
        assert body["items"] == []
        assert body["stats"]["records"] == 0
        assert index.seen_before("bandcamp", "1-0") is False

    def test_clearing_republishes_so_open_clients_update(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """Without this the numbers stay on screen until something else moves."""
        _stock(index, 1, count=2)
        harness.events.clear()
        harness.client.delete("/api/v1/discovery/history")
        assert harness.events[-1]["stats"]["records"] == 2


# ---------------------------------------------------------------------------
# Wishlist write (KAMP-653)
# ---------------------------------------------------------------------------


class TestWishlistRoutes:
    def _item(self, index: LibraryIndex) -> int:
        item = index.add_discovery_candidate(
            provider="bandcamp",
            provider_item_id="555",
            title="X",
            item_url="https://a.bandcamp.com/album/x",
        )
        index.place_in_crate(item, 1, 0)
        return item

    def test_a_confirmed_add_records_the_event_and_moves_state(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        item = self._item(index)
        resp = harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "wishlisted": True}
        assert index.crate_items(1)[0]["state"] == "wishlisted"

    def test_a_confirmed_removal_walks_the_state_back(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        item = self._item(index)
        harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        resp = harness.client.post(f"/api/v1/discovery/items/{item}/unwishlist")
        assert resp.json() == {"ok": True, "wishlisted": False}
        assert index.crate_items(1)[0]["state"] == "fresh"

    def test_the_provider_is_handed_the_whole_item(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """It needs item_url to fetch the crumb page and provider_item_id for the
        POST; passing an id alone would make it re-read the row."""
        item = self._item(index)
        harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        sent, add = harness.wishlist_calls[0]
        assert add is True
        assert sent["provider_item_id"] == "555"
        assert sent["item_url"] == "https://a.bandcamp.com/album/x"

    def test_nothing_is_written_locally_unless_the_provider_confirms(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """The whole promise of the feature: the heart means it really is on your
        Bandcamp wishlist. An optimistic write would be the feature lying."""
        item = self._item(index)
        harness.wishlist_reason = "rejected"
        assert (
            harness.client.post(f"/api/v1/discovery/items/{item}/wishlist").status_code
            == 502
        )
        assert index.crate_items(1)[0]["state"] == "fresh"
        assert (
            index._conn.execute(
                "SELECT COUNT(*) AS c FROM discovery_events WHERE kind = 'wishlisted'"
            ).fetchone()["c"]
            == 0
        )

    @pytest.mark.parametrize(
        "reason,status",
        [
            ("not_connected", 503),
            ("unsupported", 501),
            ("needs_login", 401),
            ("rate_limited", 429),
            ("rejected", 502),
        ],
    )
    def test_each_failure_gets_its_own_status_and_reason(
        self, index: LibraryIndex, harness: _Harness, reason: str, status: int
    ) -> None:
        """A blanket 502 would flatten "Bandcamp asked us to slow down" and "you
        have been logged out" into the same shrug."""
        item = self._item(index)
        harness.wishlist_reason = reason
        resp = harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        assert resp.status_code == status
        assert resp.json()["detail"] == reason

    def test_an_unknown_reason_is_still_a_failure(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """A provider inventing a new reason must not read as success."""
        item = self._item(index)
        harness.wishlist_reason = "something_new"
        assert (
            harness.client.post(f"/api/v1/discovery/items/{item}/wishlist").status_code
            == 502
        )

    def test_no_writer_at_all_is_not_connected(self, index: LibraryIndex) -> None:
        """A server-only context — or a daemon with no Bandcamp session — must
        503 rather than 500 on a missing callable."""
        harness = _Harness(index, wishlist_write=None)
        item = self._item(index)
        resp = harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "not_connected"

    def test_an_unknown_item_never_reaches_the_provider(
        self, harness: _Harness
    ) -> None:
        assert (
            harness.client.post("/api/v1/discovery/items/999/wishlist").status_code
            == 404
        )
        assert harness.wishlist_calls == []

    def test_a_confirmed_write_pushes_a_fresh_snapshot(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        """The rail draws its heart off item.state, so the write has to reach
        every client rather than just the tab that clicked."""
        item = self._item(index)
        harness.events.clear()
        harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        assert harness.events[-1]["items"][0]["state"] == "wishlisted"

    def test_a_failed_write_pushes_nothing(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        item = self._item(index)
        harness.wishlist_reason = "rate_limited"
        harness.events.clear()
        harness.client.post(f"/api/v1/discovery/items/{item}/wishlist")
        assert harness.events == []


# ---------------------------------------------------------------------------
# Art proxy (KAMP-649)
# ---------------------------------------------------------------------------

ART = "https://f4.bcbits.com/img/a123_0.jpg"


def _candidate(
    index: LibraryIndex,
    art_url: str | None = ART,
    provider_item_id: str = "1",
    provider: str = "bandcamp",
) -> int:
    return index.add_discovery_candidate(
        provider=provider,
        provider_item_id=provider_item_id,
        artist="Band",
        title="X",
        art_url=art_url,
    )


class TestArtProxy:
    def _get(self, harness: _Harness, item_id: int, **params: Any) -> Any:
        query = "".join(f"&{k}={v}" for k, v in params.items())
        return harness.client.get(f"/api/v1/discovery/art?item_id={item_id}{query}")

    def test_cache_miss_fetches_and_stores(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        harness = _Harness(index, art_cache_dir=art_dir)
        resp = self._get(harness, _candidate(index))

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.content == b"JPEGDATA"
        assert len(harness.fetched) == 1
        assert list((art_dir / "discovery").glob("*.jpg"))

    def test_cache_hit_does_not_fetch(self, index: LibraryIndex, art_dir: Path) -> None:
        """The AC's 'cache hit -> no network', asserted on the seam itself."""
        harness = _Harness(index, art_cache_dir=art_dir)
        item = _candidate(index)
        self._get(harness, item)
        harness.fetched.clear()

        resp = self._get(harness, item)
        assert resp.status_code == 200
        assert resp.content == b"JPEGDATA"
        assert harness.fetched == []

    def test_art_is_served_immutable(self, index: LibraryIndex, art_dir: Path) -> None:
        """Safe because discovery_items.id is AUTOINCREMENT (never reused) and
        add_discovery_candidate never updates art_url — so id -> art is
        write-once."""
        harness = _Harness(index, art_cache_dir=art_dir)
        resp = self._get(harness, _candidate(index))
        assert "immutable" in resp.headers["cache-control"]

    def test_two_items_with_the_same_art_share_one_file(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        """The key is content identity, so a re-listed album is fetched once."""
        harness = _Harness(index, art_cache_dir=art_dir)
        first = _candidate(index, provider_item_id="1")
        second = _candidate(index, provider_item_id="2", provider="other")
        self._get(harness, first)
        self._get(harness, second)

        assert len(list((art_dir / "discovery").glob("*.jpg"))) == 1
        assert len(harness.fetched) == 1

    # -- sizes ---------------------------------------------------------

    def test_default_size_is_the_aspect_preserving_1200(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        harness = _Harness(index, art_cache_dir=art_dir)
        self._get(harness, _candidate(index))
        assert harness.fetched == ["https://f4.bcbits.com/img/a123_10.jpg"]

    def test_size_zero_serves_the_untouched_original(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        harness = _Harness(index, art_cache_dir=art_dir)
        self._get(harness, _candidate(index), s=0)
        assert harness.fetched == [ART]

    @pytest.mark.parametrize("size", [5, 16, 2, 9, 7, 999, -1])
    def test_squaring_and_unknown_sizes_are_refused(
        self, index: LibraryIndex, art_dir: Path, size: int
    ) -> None:
        """5/16/2 are not cheaper versions of the artwork — they force 700x700
        and upscale a smaller original. kamp does not degrade covers, so they are
        excluded from the allowlist rather than merely unused."""
        harness = _Harness(index, art_cache_dir=art_dir)
        resp = self._get(harness, _candidate(index), s=size)
        assert resp.status_code == 400
        assert harness.fetched == []

    def test_sizes_differ_in_the_cache(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        harness = _Harness(index, art_cache_dir=art_dir)
        item = _candidate(index)
        self._get(harness, item)
        self._get(harness, item, s=0)
        assert len(list((art_dir / "discovery").glob("*.jpg"))) == 2

    def test_a_url_without_a_size_suffix_is_left_alone(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        harness = _Harness(index, art_cache_dir=art_dir)
        odd = "https://f4.bcbits.com/img/cover.png"
        self._get(harness, _candidate(index, art_url=odd))
        assert harness.fetched == [odd]

    # -- failure matrix ------------------------------------------------

    def test_unknown_item_is_404(self, index: LibraryIndex, art_dir: Path) -> None:
        harness = _Harness(index, art_cache_dir=art_dir)
        assert self._get(harness, 9999).status_code == 404

    def test_item_without_art_is_404(self, index: LibraryIndex, art_dir: Path) -> None:
        """art_url is nullable and every parser can produce None."""
        harness = _Harness(index, art_cache_dir=art_dir)
        resp = self._get(harness, _candidate(index, art_url=None))
        assert resp.status_code == 404
        assert harness.fetched == []

    def test_no_cache_dir_is_404_not_500(self, index: LibraryIndex) -> None:
        harness = _Harness(index, art_cache_dir=None)
        assert self._get(harness, _candidate(index)).status_code == 404

    def test_disallowed_host_is_refused_without_fetching(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        """art_url_from_image passes through any string starting with http, so an
        arbitrary host really can reach the database."""
        harness = _Harness(index, art_cache_dir=art_dir)
        evil = _candidate(index, art_url="https://evil.example.com/a_0.jpg")
        resp = self._get(harness, evil)
        assert resp.status_code == 400
        assert harness.fetched == []

    def test_bandcamp_com_itself_is_not_an_art_host(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        """ART_HOSTS is deliberately narrower than the proxy allowlist."""
        harness = _Harness(index, art_cache_dir=art_dir)
        item = _candidate(index, art_url="https://bandcamp.com/img/a1_0.jpg")
        assert self._get(harness, item).status_code == 400

    def test_lookalike_host_is_refused(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        harness = _Harness(index, art_cache_dir=art_dir)
        item = _candidate(index, art_url="https://evilf4.bcbits.com.bad/a_0.jpg")
        assert self._get(harness, item).status_code == 400

    def test_a_failed_fetch_writes_nothing(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        harness = _Harness(index, art_cache_dir=art_dir, art_bytes=None)
        resp = self._get(harness, _candidate(index))
        assert resp.status_code == 404
        assert not list((art_dir / "discovery").glob("*.jpg"))

    def test_a_failed_fetch_is_briefly_cached(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        """art_url is write-once, so a 404 stays a 404 — without this, ten cards
        remounting on every tab switch is a fetch storm."""
        harness = _Harness(index, art_cache_dir=art_dir, art_bytes=None)
        resp = self._get(harness, _candidate(index))
        assert "max-age" in resp.headers.get("cache-control", "")

    # -- the traversal the ticket's suggested cache key would have opened --

    def test_a_hostile_provider_item_id_cannot_escape_the_cache_dir(
        self, index: LibraryIndex, art_dir: Path
    ) -> None:
        """normalise_item_id returns the raw string when it does not match
        ^(album|track)-\\d+$, so provider_item_id is unvalidated remote text.
        Keying the cache file on it would let a crafted data-albumid write
        anywhere; the key is a hash of the art URL instead.
        """
        harness = _Harness(index, art_cache_dir=art_dir)
        item = _candidate(index, provider_item_id="../../../../tmp/pwned")
        assert self._get(harness, item).status_code == 200

        written = list(art_dir.rglob("*.jpg"))
        assert len(written) == 1
        assert written[0].parent == art_dir / "discovery"
        assert not (art_dir.parent / "pwned.jpg").exists()


class TestArtCacheFailures:
    def test_an_unwritable_cache_costs_caching_not_the_image(
        self, index: LibraryIndex, art_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A full or read-only disk must not turn every crate card into a gap."""
        harness = _Harness(index, art_cache_dir=art_dir)

        def _boom(*_a: object, **_kw: object) -> None:
            raise OSError("no space left on device")

        monkeypatch.setattr(Path, "mkdir", _boom)
        resp = harness.client.get(f"/api/v1/discovery/art?item_id={_candidate(index)}")
        assert resp.status_code == 200
        assert resp.content == b"JPEGDATA"


class _FakeResponse:
    def __init__(self, status: int = 200, chunks: list[bytes] | None = None) -> None:
        self.status_code = status
        self._chunks = chunks if chunks is not None else [b"JPEG", b"DATA"]

    def iter_content(self, _size: int) -> list[bytes]:
        return self._chunks


class TestRealArtFetch:
    """The unstubbed CDN path — the seam every other test replaces."""

    def _patch(self, monkeypatch: pytest.MonkeyPatch, resp: Any) -> list[dict]:
        calls: list[dict] = []

        def _get(url: str, **kw: Any) -> Any:
            calls.append({"url": url, **kw})
            if isinstance(resp, Exception):
                raise resp
            return resp

        monkeypatch.setattr("requests.get", _get)
        return calls

    def test_streams_a_successful_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from kamp_core.discovery_api import _fetch_art_bytes

        calls = self._patch(monkeypatch, _FakeResponse())
        assert _fetch_art_bytes("https://f4.bcbits.com/img/a1_10.jpg") == b"JPEGDATA"
        # A static CDN on the render path, not the 30s Bandcamp API budget.
        assert calls[0]["timeout"] == 10.0
        assert calls[0]["stream"] is True

    def test_non_200_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kamp_core.discovery_api import _fetch_art_bytes

        self._patch(monkeypatch, _FakeResponse(status=404))
        assert _fetch_art_bytes("https://f4.bcbits.com/img/a1_10.jpg") is None

    def test_an_oversized_body_is_refused_mid_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Remote data is read bounded rather than trusted — the cap has to bite
        while streaming, since a hostile host can lie about Content-Length."""
        from kamp_core.discovery_api import _MAX_ART_BYTES, _fetch_art_bytes

        chunk = b"x" * (1024 * 1024)
        huge = [chunk] * (_MAX_ART_BYTES // len(chunk) + 2)
        self._patch(monkeypatch, _FakeResponse(chunks=huge))
        assert _fetch_art_bytes("https://f4.bcbits.com/img/a1_10.jpg") is None

    def test_a_network_error_is_none_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from kamp_core.discovery_api import _fetch_art_bytes

        self._patch(monkeypatch, RuntimeError("connection reset"))
        assert _fetch_art_bytes("https://f4.bcbits.com/img/a1_10.jpg") is None


class FakePreview:
    """The PreviewPlayer surface the routes touch."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.state: dict[str, Any] = {"state": "idle", "item_id": None}

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

    def play(self, item_id: int, track_num: int | None = None) -> dict[str, Any]:
        self.calls.append(("play", (item_id, track_num)))
        self.state = {"state": "playing", "item_id": item_id}
        return self.snapshot()

    def _simple(self, name: str) -> dict[str, Any]:
        self.calls.append((name, None))
        return self.snapshot()

    def pause(self) -> dict[str, Any]:
        return self._simple("pause")

    def resume(self) -> dict[str, Any]:
        return self._simple("resume")

    def toggle(self) -> dict[str, Any]:
        return self._simple("toggle")

    def stop(self) -> dict[str, Any]:
        return self._simple("stop")

    def step(self, delta: int) -> dict[str, Any]:
        self.calls.append(("step", delta))
        return self.snapshot()

    def seek(self, position: float) -> dict[str, Any]:
        self.calls.append(("seek", position))
        return self.snapshot()

    def release_for_main(self) -> None:
        self.calls.append(("release_for_main", None))

    def set_volume(self, v: int) -> None:
        self.calls.append(("set_volume", v))

    def set_muted(self, m: bool) -> None:
        self.calls.append(("set_muted", m))


def _preview_app(index: LibraryIndex, preview: Any) -> Any:
    app = FastAPI()
    register_discovery_routes(
        app, index=index, broadcast=lambda _e: None, preview=preview
    )
    return TestClient(app)


class TestPreviewRoutes:
    def test_play_starts_a_preview(self, index: LibraryIndex) -> None:
        preview = FakePreview()
        client = _preview_app(index, preview)
        body = client.post(
            "/api/v1/discovery/preview/play", json={"item_id": 7, "track_num": 2}
        ).json()
        assert body["state"] == "playing"
        assert preview.calls == [("play", (7, 2))]

    def test_play_requires_an_integer_item(self, index: LibraryIndex) -> None:
        client = _preview_app(index, FakePreview())
        assert client.post("/api/v1/discovery/preview/play", json={}).status_code == 422

    @pytest.mark.parametrize("action", ["pause", "resume", "toggle", "stop"])
    def test_simple_actions(self, index: LibraryIndex, action: str) -> None:
        preview = FakePreview()
        client = _preview_app(index, preview)
        assert client.post(f"/api/v1/discovery/preview/{action}").status_code == 200
        assert preview.calls == [(action, None)]

    @pytest.mark.parametrize("action,delta", [("next", 1), ("prev", -1)])
    def test_stepping(self, index: LibraryIndex, action: str, delta: int) -> None:
        preview = FakePreview()
        client = _preview_app(index, preview)
        client.post(f"/api/v1/discovery/preview/{action}")
        assert preview.calls == [("step", delta)]

    def test_seek_requires_a_number(self, index: LibraryIndex) -> None:
        client = _preview_app(index, FakePreview())
        assert (
            client.post(
                "/api/v1/discovery/preview/seek", json={"position": "soon"}
            ).status_code
            == 422
        )

    def test_an_unknown_action_is_404_not_an_arbitrary_call(
        self, index: LibraryIndex
    ) -> None:
        """One route serves several verbs, so the allowlist is what stops it
        being a remote method call on the player."""
        preview = FakePreview()
        client = _preview_app(index, preview)
        assert client.post("/api/v1/discovery/preview/shutdown").status_code == 404
        assert preview.calls == []

    def test_state_is_the_reconnect_path(self, index: LibraryIndex) -> None:
        preview = FakePreview()
        client = _preview_app(index, preview)
        client.post("/api/v1/discovery/preview/play", json={"item_id": 3})
        body = client.get("/api/v1/discovery/preview/state").json()
        assert body["item_id"] == 3

    def test_no_engine_reports_unavailable_but_still_answers_state(
        self, index: LibraryIndex
    ) -> None:
        """The routes register either way so the UI has one code path; state
        returns the idle shape rather than an error the view must special-case."""
        client = _preview_app(index, None)
        assert client.post("/api/v1/discovery/preview/stop").status_code == 503
        assert client.get("/api/v1/discovery/preview/state").json()["state"] == "idle"


class TestMainTransportWins:
    """Any main-transport POST stops a preview — except volume and mute, which
    are a change to how loud everything is, not a demand for the floor."""

    def _app(self, index: LibraryIndex, preview: Any) -> Any:
        from kamp_core.server import create_app

        engine = MagicMock()
        engine.state = MagicMock(playing=False, position=0.0, duration=0.0, volume=100)
        queue = MagicMock()
        queue.current.return_value = None
        queue.peek_next.return_value = None
        return TestClient(
            create_app(index=index, engine=engine, queue=queue, preview_player=preview)
        )

    @pytest.mark.parametrize(
        "path", ["/api/v1/player/pause", "/api/v1/player/resume", "/api/v1/player/stop"]
    )
    def test_transport_posts_stop_the_preview(
        self, index: LibraryIndex, path: str
    ) -> None:
        preview = FakePreview()
        self._app(index, preview).post(path)
        assert ("release_for_main", None) in preview.calls

    def test_volume_mirrors_instead_of_stopping(self, index: LibraryIndex) -> None:
        """Otherwise the slider moves everything except what you can hear."""
        preview = FakePreview()
        self._app(index, preview).post("/api/v1/player/volume", json={"volume": 40})
        assert ("set_volume", 40) in preview.calls
        assert ("release_for_main", None) not in preview.calls

    def test_mute_mirrors_instead_of_stopping(self, index: LibraryIndex) -> None:
        preview = FakePreview()
        self._app(index, preview).post("/api/v1/player/mute", json={"muted": True})
        assert ("set_muted", True) in preview.calls
        assert ("release_for_main", None) not in preview.calls

    def test_a_get_does_not_stop_the_preview(self, index: LibraryIndex) -> None:
        preview = FakePreview()
        self._app(index, preview).get("/api/v1/player/state")
        assert preview.calls == []


class TestArtHostAllowlist:
    def test_art_hosts_are_a_subset_of_the_proxy_allowlist(self) -> None:
        """Two lists that must not drift apart: anything the art proxy will fetch
        must also be something the relay would have permitted."""
        from kamp_core.proxy_hosts import ALLOWED_PROXY_HOSTS, ART_HOSTS

        assert ART_HOSTS < ALLOWED_PROXY_HOSTS


# ---------------------------------------------------------------------------
# Wiring into create_app
# ---------------------------------------------------------------------------


class TestServerWiring:
    def _app(self, index: LibraryIndex, **kw: Any) -> Any:
        from kamp_core.server import create_app

        engine = MagicMock()
        queue = MagicMock()
        queue.current.return_value = None
        queue.peek_next.return_value = None
        return create_app(index=index, engine=engine, queue=queue, **kw)

    def test_routes_are_registered_by_create_app(self, index: LibraryIndex) -> None:
        client = TestClient(self._app(index))
        assert client.get("/api/v1/discovery/crate").status_code == 200

    def test_auth_middleware_covers_the_new_module(self, index: LibraryIndex) -> None:
        """The middleware is declared in server.py and knows nothing about
        discovery_api; it must still wrap routes registered from there."""
        client = TestClient(self._app(index, auth_token="secret"))
        assert client.get("/api/v1/discovery/crate").status_code == 401
        ok = client.get("/api/v1/discovery/crate", headers={"X-Kamp-Token": "secret"})
        assert ok.status_code == 200

    def test_the_wishlist_writer_reaches_the_route(self, index: LibraryIndex) -> None:
        """create_app takes it and register_discovery_routes must receive it —
        the KAMP-436 failure shape: a callable set on a wrapper that the thing
        doing the work never sees."""
        calls: list[tuple[dict[str, Any], bool]] = []

        def _write(item: dict[str, Any], add: bool) -> str:
            calls.append((item, add))
            return "ok"

        item = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="1", item_url="https://a.b.com/x"
        )
        index.place_in_crate(item, 1, 0)
        client = TestClient(self._app(index, wishlist_write=_write))

        assert (
            client.post(f"/api/v1/discovery/items/{item}/wishlist").status_code == 200
        )
        assert calls and calls[0][1] is True

    def test_purchase_notification_broadcasts_the_album_not_just_an_id(
        self, index: LibraryIndex
    ) -> None:
        """The crate snapshot only ever rebuilds the LATEST crate, so a pick
        bought out of an older one has no card to read artist/title from. The
        toast has to be self-contained (KAMP-654)."""
        engine = MagicMock()
        # The WS accept frame reads real numbers off the engine; a bare MagicMock
        # makes the position extrapolation compare a mock with an int.
        engine.state.playing = False
        engine.state.duration = 0.0
        engine.state.position = 0.0
        engine.state.position_updated_at = 0.0
        queue = MagicMock()
        queue.current.return_value = None
        queue.peek_next.return_value = None

        from kamp_core.server import create_app

        app = create_app(index=index, engine=engine, queue=queue)
        sent: list[dict[str, Any]] = []
        # Captured through a real WS client rather than by reaching into internals.
        with TestClient(app) as client:
            with client.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # accept frame
                app.state.notify_crate_purchase(
                    [{"id": 7, "artist": "abracadabra", "title": "peel away"}]
                )
                for _ in range(20):
                    msg = ws.receive_json()
                    if msg.get("type") == "discovery.purchased":
                        sent.append(msg)
                        break
        assert sent, "no discovery.purchased frame was broadcast"
        assert sent[0]["items"] == [
            {"id": 7, "artist": "abracadabra", "title": "peel away"}
        ]

    def test_a_server_without_a_writer_503s_rather_than_500s(
        self, index: LibraryIndex
    ) -> None:
        item = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="1", item_url="https://a.b.com/x"
        )
        index.place_in_crate(item, 1, 0)
        client = TestClient(self._app(index))
        assert (
            client.post(f"/api/v1/discovery/items/{item}/wishlist").status_code == 503
        )
