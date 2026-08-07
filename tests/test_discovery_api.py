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


@pytest.fixture
def index(tmp_path: Path) -> Iterator[LibraryIndex]:
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


class _Harness:
    def __init__(self, index: LibraryIndex, on_build_start: Any = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.build_calls = 0
        self.app = FastAPI()

        def _default_start() -> None:
            self.build_calls += 1

        register_discovery_routes(
            self.app,
            index=index,
            broadcast=self.events.append,
            on_build_start=(
                _default_start if on_build_start is None else on_build_start
            ),
        )
        self.client = TestClient(self.app)

    @property
    def publish(self) -> Any:
        return self.app.state.discovery_publish


@pytest.fixture
def harness(index: LibraryIndex) -> _Harness:
    return _Harness(index)


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

    def test_dismiss_records_and_moves_state(
        self, index: LibraryIndex, harness: _Harness
    ) -> None:
        item = self._item(index)
        assert (
            harness.client.post(f"/api/v1/discovery/items/{item}/dismiss").status_code
            == 200
        )
        assert index.crate_items(1)[0]["state"] == "dismissed"

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
        harness.client.post(f"/api/v1/discovery/items/{item}/dismiss")
        assert harness.events[-1]["items"][0]["state"] == "dismissed"

    def test_unknown_item_is_a_404(self, harness: _Harness) -> None:
        assert (
            harness.client.post("/api/v1/discovery/items/999/dismiss").status_code
            == 404
        )


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
