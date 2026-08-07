"""Discovery Crate REST + WebSocket surface (KAMP-648).

A separate module from :mod:`kamp_core.server` on purpose. ``create_app`` is
already 4,600 lines; growing it by another view's worth of routes makes it worse,
and refactoring it is not this story's job. So the closure style is preserved
exactly — routes close over ``index`` and a ``broadcast`` callable handed in by
``create_app`` — and only the file changes.

Nothing here imports :mod:`kamp_daemon`. The rate-limit cooldown reaches the UI
as a ``paused_until`` timestamp written by the builder, which already holds the
governor, so this layer needs no knowledge of rate limiting at all. That is a
deliberate departure from the signature sketched on the ticket, which passed a
governor in.

KAMP-649 adds the crate art proxy to this module.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TYPE_CHECKING

from fastapi import FastAPI, HTTPException

if TYPE_CHECKING:  # pragma: no cover - types only
    from kamp_core.library import LibraryIndex

logger = logging.getLogger(__name__)

#: WebSocket event name. One event carries the whole crate, mirroring the
#: ``download.queue`` snapshot idiom rather than streaming per-item deltas —
#: a reconnecting client then needs no replay, just the REST snapshot.
CRATE_EVENT = "discovery.crate"

#: States a build can end in. Reaching one releases the single-build lock, so
#: this set is load-bearing: a state missing from it would wedge the feature on
#: "already building" until the daemon restarted.
_TERMINAL_STATES = frozenset({"ready", "empty", "error", "paused", "idle"})

_INITIAL_STATUS: dict[str, Any] = {
    "state": "idle",
    "crate_no": None,
    "filled": 0,
    "short": False,
    "paused_until": 0.0,
    "hints": [],
}


def register_discovery_routes(
    app: FastAPI,
    *,
    index: "LibraryIndex",
    broadcast: Callable[[dict[str, Any]], None],
    on_build_start: Callable[[], None] | None = None,
) -> None:
    """Register the Discovery Crate routes on *app*.

    Exposes ``app.state.discovery_publish``: the builder's single channel for
    reporting progress. It updates the status and broadcasts in one step so
    "changed the state but forgot to notify" is not representable — the failure
    that presents as a backend which works and a UI that never moves.
    """
    _status: dict[str, Any] = dict(_INITIAL_STATUS)
    # Guards _status and the single-build flag together. Builds are serialized
    # for a correctness reason, not just tidiness: two builders would compute the
    # same next_crate_no() and collide on the partial unique index over
    # (crate_no, position).
    _lock = threading.Lock()
    _building = [False]

    def _snapshot() -> dict[str, Any]:
        """The payload both the REST endpoint and the WS event carry.

        One function rather than two agreeing call sites, so "REST snapshot
        equals WS snapshot shape" holds by construction.
        """
        with _lock:
            snap = dict(_status)
        crate_no = snap.get("crate_no")
        if crate_no is None:
            crate_no = index.latest_crate_no()
            snap["crate_no"] = crate_no
        snap["items"] = index.crate_items(crate_no) if crate_no is not None else []
        return snap

    def _publish(fields: dict[str, Any]) -> None:
        """Merge *fields* into the status and push the result to every client."""
        with _lock:
            _status.update(fields)
            if fields.get("state") in _TERMINAL_STATES:
                _building[0] = False
        broadcast({"type": CRATE_EVENT, **_snapshot()})

    app.state.discovery_publish = _publish

    # ------------------------------------------------------------------
    # Crate
    # ------------------------------------------------------------------

    @app.get("/api/v1/discovery/crate")
    def get_crate() -> dict[str, Any]:
        """The current crate plus build status — the reconnect path.

        ``_broadcast`` no-ops when no WebSocket client is attached, so this is
        the source of truth on mount and after a reconnect, not a convenience.
        Returns the same shape as the ``discovery.crate`` event minus its
        ``type`` discriminator, exactly as ``GET /api/v1/downloads`` relates to
        ``download.queue``.
        """
        return _snapshot()

    @app.post("/api/v1/discovery/crate/new")
    def new_crate() -> dict[str, Any]:
        """Start a build. 409 while one is already running.

        The flag is set here, synchronously, under the lock — deliberately
        unlike ``start_genre_backfill``, which tests a flag only the worker
        thread ever sets, so two rapid POSTs both spawn a thread and both are
        told they started. Harmless for an hours-long backfill; for a crate the
        loser's spinner would simply never resolve.
        """
        if on_build_start is None:
            raise HTTPException(status_code=503, detail="discovery is unavailable")
        with _lock:
            if _building[0]:
                raise HTTPException(
                    status_code=409, detail="a crate is already building"
                )
            _building[0] = True
        try:
            on_build_start()
        except Exception:
            with _lock:
                _building[0] = False
            logger.exception("discovery: could not start a crate build")
            raise HTTPException(status_code=500, detail="could not start the build")
        return {"started": True}

    # ------------------------------------------------------------------
    # Per-item engagement
    # ------------------------------------------------------------------

    def _record(item_id: int, kind: str) -> dict[str, Any]:
        try:
            index.record_discovery_event(item_id, kind)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _publish({})  # state unchanged; the item's cached state moved
        return {"ok": True}

    @app.post("/api/v1/discovery/items/{item_id}/dismiss")
    def dismiss_item(item_id: int) -> dict[str, Any]:
        """Pass on a record. Recorded, never deleted — the ledger is history."""
        return _record(item_id, "dismissed")

    @app.post("/api/v1/discovery/items/{item_id}/url-copied")
    def url_copied(item_id: int) -> dict[str, Any]:
        """The always-available action while wishlist-write is unbuilt (KAMP-653).

        Recorded as engagement but deliberately does NOT move the item's state:
        copying a link is not passing on it, and the user may still preview or
        wishlist afterwards.
        """
        return _record(item_id, "url_copied")
