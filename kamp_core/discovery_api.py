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

import hashlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING, cast

from fastapi import FastAPI, HTTPException, Response

from kamp_core.proxy_hosts import ART_HOSTS, host_allowed

if TYPE_CHECKING:  # pragma: no cover - types only
    from kamp_core.library import LibraryIndex

logger = logging.getLogger(__name__)

#: WebSocket event name. One event carries the whole crate, mirroring the
#: ``download.queue`` snapshot idiom rather than streaming per-item deltas —
#: a reconnecting client then needs no replay, just the REST snapshot.
CRATE_EVENT = "discovery.crate"

#: Preview transport state (KAMP-651). Pushed on transitions rather than per
#: frame; the UI interpolates position between them from position_updated_at.
PREVIEW_EVENT = "discovery.preview"

#: What GET .../preview/state returns when no preview engine exists at all —
#: the same shape the player publishes, so the UI has one branch, not two.
_IDLE_PREVIEW: dict[str, Any] = {
    "state": "idle",
    "item_id": None,
    "track_num": None,
    "title": "",
    "position": 0.0,
    "position_updated_at": 0.0,
    "duration": 0.0,
    "buffering": False,
    "tracks": [],
    "error": None,
}

#: Records in a full crate. Mirrors discovery_builder.CRATE_SIZE, duplicated
#: rather than imported because kamp_core does not depend on kamp_daemon.
CRATE_SIZE = 10

#: States a build can end in. Reaching one releases the single-build lock, so
#: this set is load-bearing: a state missing from it would wedge the feature on
#: "already building" until the daemon restarted.
_TERMINAL_STATES = frozenset({"ready", "empty", "error", "paused", "idle"})

# Bandcamp's art CDN encodes the rendition in the filename: a<id>_<code>.jpg.
#
# Only the two aspect-preserving codes are offered, and the exclusions are the
# point rather than an oversight. Measured against the live CDN: _0 is the
# artist's original (197 KB to 7.2 MB, any aspect); _10 caps the long edge at
# 1200px and preserves aspect (125-440 KB). _5/_16/_2 force 700x700 -- for a
# 635x611 original they *upscale it and square it* -- and _16 is additionally the
# same pixels as _5 at ~40% fewer bytes. kamp does not degrade covers, so those
# renditions are not reachable through this endpoint at all.
ART_SIZES: frozenset[int] = frozenset({0, 10})
DEFAULT_ART_SIZE = 10

_BCBITS_SIZE = re.compile(r"_(\d+)\.jpg$")

# Why a wishlist write failed, as a status. Five distinguishable outcomes rather
# than a blanket 502, because the UI says something different for each and a
# single code would flatten "Bandcamp asked us to slow down" and "you have been
# logged out" into the same shrug. The reason string travels as `detail`; the
# renderer owns the wording.
_WISHLIST_STATUS: dict[str, int] = {
    "unknown_item": 404,
    "not_connected": 503,
    "unsupported": 501,
    "needs_login": 401,
    "rate_limited": 429,
    "rejected": 502,
}

_ART_TIMEOUT = 10.0  # a static CDN on the render path, not the 30s API budget
_MAX_ART_BYTES = 16 * 1024 * 1024

# The URL is keyed on (item id, size) and both are write-once -- discovery_items.id
# is AUTOINCREMENT so ids are never reused, and add_discovery_candidate never
# updates art_url -- so the response really is immutable.
_ART_CACHE_CONTROL = "public, max-age=31536000, immutable"
# Failures get a short positive TTL rather than no-store: art_url is write-once,
# so a miss stays a miss, and without this ten cards remounting on a tab switch
# is a fetch storm against the CDN.
_ART_MISS_CACHE_CONTROL = "public, max-age=300"

_INITIAL_STATUS: dict[str, Any] = {
    "state": "idle",
    "crate_no": None,
    "filled": 0,
    "short": False,
    "paused_until": 0.0,
    "hints": [],
    "thin": False,
}


def sized_art_url(art_url: str, size: int) -> str:
    """Swap the bcbits rendition code in *art_url*, or return it untouched.

    Parsers store ``_0`` (content identity); the delivery size is a presentation
    decision made here. A URL that does not carry a rendition suffix is left
    alone rather than guessed at.
    """
    return _BCBITS_SIZE.sub(f"_{size}.jpg", art_url)


def _fetch_art_bytes(url: str) -> bytes | None:
    """Download cover art from the CDN with a plain, unauthenticated session.

    f4.bcbits.com serves art publicly with no cookies and is not behind the bot
    management that guards bandcamp.com itself (KAMP-636), so this deliberately
    does not go through the Electron relay -- and must not, since the relay
    carries session cookies an image request has no need of.
    """
    import requests  # noqa: PLC0415 - keeps kamp_core import-light

    try:
        resp = requests.get(url, timeout=_ART_TIMEOUT, stream=True)
        if resp.status_code != 200:
            logger.debug("crate art: HTTP %d from %s", resp.status_code, url)
            return None
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(64 * 1024):
            total += len(chunk)
            if total > _MAX_ART_BYTES:
                # Remote data: read it bounded rather than trusting the host.
                logger.warning("crate art: oversized response from %s", url)
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except Exception as exc:  # noqa: BLE001 - a missing cover is not an error
        logger.debug("crate art: fetch failed for %s: %s", url, exc)
        return None


def register_discovery_routes(
    app: FastAPI,
    *,
    index: "LibraryIndex",
    broadcast: Callable[[dict[str, Any]], None],
    on_build_start: Callable[[], None] | None = None,
    art_cache_dir: Path | None = None,
    fetch_bytes: Callable[[str], bytes | None] | None = None,
    preview: Any = None,
    wishlist_write: Callable[[dict[str, Any], bool], str] | None = None,
) -> None:
    """Register the Discovery Crate routes on *app*.

    Exposes ``app.state.discovery_publish``: the builder's single channel for
    reporting progress. It updates the status and broadcasts in one step so
    "changed the state but forgot to notify" is not representable — the failure
    that presents as a backend which works and a UI that never moves.

    *preview* is a ``kamp_daemon.discovery_preview.PreviewPlayer``, taken as
    ``Any`` because kamp_core does not depend on kamp_daemon. It is optional so
    the routes still register (and 503) in a test app that has no engine.
    Also exposes ``app.state.discovery_preview_snapshot`` for the WS accept
    frame and ``app.state.stop_preview`` for the main transport.
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
        items = index.crate_items(crate_no) if crate_no is not None else []
        snap["items"] = items
        if snap.get("state") != "building":
            # The status is in-memory; the crate is not. After a daemon restart
            # _status is still _INITIAL_STATUS while items holds a full crate, so
            # a restored crate of ten reported filled=0 and a genuinely short one
            # reported short=False -- wrong fill progress on every launch, and the
            # quiet short-crate note suppressed. Derive both from what is actually
            # there. Only while a build is in flight does the builder's own count
            # win, since then it is ahead of the rows and this crate_no may still
            # be the previous crate's.
            snap["filled"] = len(items)
            snap["short"] = bool(items) and len(items) < CRATE_SIZE
        # The digging history rides along rather than being fetched separately
        # (KAMP-655). Five COUNTs over two small indexed tables, against a
        # snapshot that already reads every row of the crate -- so the numbers
        # are live with no second request and no staleness, and the end-of-crate
        # tally cannot drift from the lifetime line.
        snap["stats"] = index.discovery_stats()
        # Scoped to the crate on screen, which is always the LATEST one -- see
        # crate_no above. If crate browsing ever arrives this has to follow it.
        snap["crate_stats"] = (
            index.discovery_stats(crate_no=crate_no) if crate_no is not None else None
        )
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
    # Digging history (KAMP-655)
    # ------------------------------------------------------------------

    @app.get("/api/v1/discovery/stats")
    def get_discovery_stats() -> dict[str, Any]:
        """The digging history on its own.

        The crate snapshot already carries these, so the UI does not need this —
        it is the reconnect path and the answer to "what does kamp know about my
        digging", which deserves an address of its own rather than being buried
        in a crate payload. Same function either way, so they cannot disagree.
        """
        return {"stats": index.discovery_stats()}

    @app.delete("/api/v1/discovery/history")
    def clear_discovery_history(forget_seen: bool = False) -> dict[str, Any]:
        """Erase the digging history. The user's data is theirs to wipe.

        ``forget_seen`` is a materially different act, not a stronger version of
        the same one: it drops the seen ledger, so records already shown start
        coming round again. The UI must say that rather than a generic warning.
        """
        try:
            index.clear_discovery_history(forget_seen=forget_seen)
        except Exception as exc:  # noqa: BLE001
            logger.exception("discovery: could not clear the digging history")
            raise HTTPException(
                status_code=500, detail="could not clear the history"
            ) from exc
        # Republish: the numbers are on the snapshot, and forget_seen has just
        # emptied the crate the client is looking at.
        _publish({})
        return {"ok": True}

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
        """The fallback action, and the one offered when a wishlist write fails.

        Recorded as engagement but deliberately does NOT move the item's state:
        copying a link is not passing on it, and the user may still preview or
        wishlist afterwards.
        """
        return _record(item_id, "url_copied")

    # ------------------------------------------------------------------
    # Wishlist write (KAMP-653)
    # ------------------------------------------------------------------

    def _wishlist(item_id: int, *, add: bool) -> dict[str, Any]:
        """Write to the provider's wishlist, then record it — in that order.

        Nothing is written locally until the provider confirms. The feature's one
        promise is that the heart means the record really is on your Bandcamp
        wishlist, so an optimistic write would be the feature lying.

        The remote call itself is injected, because it needs ``Candidate`` and a
        provider session and kamp_core cannot import kamp_daemon — the same reason
        ``preview`` is injected. It answers with a machine reason and no
        user-facing prose: the daemon does not write brand voice, and the renderer
        maps the reason to the clerk's line.
        """
        item = index.discovery_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown_item")
        if wishlist_write is None:
            raise HTTPException(status_code=503, detail="not_connected")

        reason = wishlist_write(dict(item), add)
        if reason != "ok":
            raise HTTPException(
                status_code=_WISHLIST_STATUS.get(reason, 502), detail=reason
            )

        # Confirmed by the provider. Only now does any of it become true locally.
        _record(item_id, "wishlisted" if add else "unwishlisted")
        return {"ok": True, "wishlisted": add}

    @app.post("/api/v1/discovery/items/{item_id}/wishlist")
    def wishlist_item(item_id: int) -> dict[str, Any]:
        """Put this record on the user's Bandcamp wishlist."""
        return _wishlist(item_id, add=True)

    @app.post("/api/v1/discovery/items/{item_id}/unwishlist")
    def unwishlist_item(item_id: int) -> dict[str, Any]:
        """Take it back off again."""
        return _wishlist(item_id, add=False)

    # ------------------------------------------------------------------
    # Preview (KAMP-651)
    # ------------------------------------------------------------------

    def _preview_or_503() -> Any:
        if preview is None:
            raise HTTPException(status_code=503, detail="preview is unavailable")
        return preview

    def _push_preview(snapshot: dict[str, Any]) -> None:
        broadcast({"type": PREVIEW_EVENT, **snapshot})

    # The player is built before create_app, so it cannot be handed this
    # directly; the daemon looks it up here at call time instead (the same
    # deferred-lookup the notify_* helpers use).
    app.state.discovery_preview_publish = _push_preview

    if preview is not None:
        # The player owns its own state; this is only how it reaches clients.
        app.state.discovery_preview_snapshot = preview.snapshot
        # The main transport always wins. Exposed for the player endpoints in
        # server.py, following the drain_for_track_async precedent.
        app.state.stop_preview = preview.release_for_main

    @app.get("/api/v1/discovery/preview/state")
    def get_preview_state() -> dict[str, Any]:
        """The reconnect path. ``_broadcast`` no-ops with no client attached, so
        a renderer reload mid-preview would otherwise leave audible audio with
        no controls anywhere on screen."""
        if preview is None:
            return dict(_IDLE_PREVIEW)
        return cast(dict[str, Any], preview.snapshot())

    @app.post("/api/v1/discovery/preview/play")
    def preview_play(req: dict[str, Any]) -> dict[str, Any]:
        """Start previewing an item, optionally at a given track."""
        item_id = req.get("item_id")
        if not isinstance(item_id, int):
            raise HTTPException(status_code=422, detail="item_id must be an integer")
        track_num = req.get("track_num")
        return cast(
            dict[str, Any],
            _preview_or_503().play(
                item_id, track_num if isinstance(track_num, int) else None
            ),
        )

    @app.post("/api/v1/discovery/preview/{action}")
    def preview_action(
        action: str, req: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """pause / resume / toggle / stop / next / prev / seek.

        One route rather than six near-identical ones; the action allowlist is
        what keeps it from being an arbitrary method call.
        """
        player = _preview_or_503()
        if action in ("pause", "resume", "toggle", "stop"):
            return cast(dict[str, Any], getattr(player, action)())
        if action == "next":
            return cast(dict[str, Any], player.step(1))
        if action == "prev":
            return cast(dict[str, Any], player.step(-1))
        if action == "seek":
            position = (req or {}).get("position")
            if not isinstance(position, (int, float)):
                raise HTTPException(status_code=422, detail="position must be a number")
            return cast(dict[str, Any], player.seek(float(position)))
        raise HTTPException(status_code=404, detail=f"unknown preview action: {action}")

    # ------------------------------------------------------------------
    # Album art (KAMP-649)
    # ------------------------------------------------------------------

    _fetch = fetch_bytes or _fetch_art_bytes

    @app.get("/api/v1/discovery/art")
    def get_crate_art(item_id: int, s: int = DEFAULT_ART_SIZE) -> Response:
        """Proxy and cache cover art for one crate pick.

        The renderer CSP does not allow f4.bcbits.com, and ``/api/v1/album-art``
        resolves through the user's collection, so it cannot serve an album they
        do not own. Proxying here keeps CSP untouched and gets the disk cache for
        free. No auth work is needed in the UI: Electron injects X-Kamp-Token on
        every local-API request including <img src>.

        Every failure is a 404 or 400 rather than a 5xx -- the UI renders a
        placeholder either way, and a missing cover is not a server fault.
        """
        if s not in ART_SIZES:
            raise HTTPException(status_code=400, detail=f"Unsupported art size: {s}")
        if art_cache_dir is None:
            raise HTTPException(status_code=404, detail="No art found")

        row = index.discovery_item(item_id)
        art_url = (row or {}).get("art_url") or ""
        if not art_url:
            raise HTTPException(status_code=404, detail="No art found")

        # The stored URL is remote data: art_url_from_image passes through any
        # string starting with http, so an arbitrary host can reach the database.
        # 400 rather than _validate_proxy_url's 422 -- that path validates a
        # client-supplied URL, where blaming the caller is right; here the caller
        # sent a perfectly good item id and our own stored data is at fault.
        if not host_allowed(art_url, ART_HOSTS):
            logger.warning("crate art: refusing host for item %d: %s", item_id, art_url)
            raise HTTPException(status_code=400, detail="Art host not allowed")

        url = sized_art_url(art_url, s)
        # Keyed on content identity, NOT provider_item_id: that column is only
        # unique paired with provider, and it is unvalidated remote text, so a
        # crafted data-albumid would write outside the cache directory. Hashing
        # the URL also lets two providers listing the same album share one file.
        key = hashlib.sha256(url.encode()).hexdigest()
        # A subdirectory so discovery art stays separable from collection art.
        # NOTE: neither cache is pruned today (see the sibling writer in
        # kamp_daemon/bandcamp.py); when a sweep is added it will meet this
        # directory entry at the top level of art_cache and must not unlink it.
        cache_dir = art_cache_dir / "discovery"
        cache_path = cache_dir / f"{key}.jpg"
        if cache_path.exists():
            return Response(
                content=cache_path.read_bytes(),
                media_type="image/jpeg",
                headers={"Cache-Control": _ART_CACHE_CONTROL},
            )

        data = _fetch(url)
        if not data:
            raise HTTPException(
                status_code=404,
                detail="No art found",
                headers={"Cache-Control": _ART_MISS_CACHE_CONTROL},
            )

        # Written atomically: a focus card and its rail sleeve are the *same*
        # item, so concurrent requests for one URL are the common case and a
        # half-written file would be served as a broken image.
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = cache_dir / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                tmp.write_bytes(data)
                os.replace(tmp, cache_path)
            finally:
                tmp.unlink(missing_ok=True)
        except OSError:
            # A full or read-only disk costs caching, not the image.
            logger.warning("crate art: could not cache %s", cache_path, exc_info=True)

        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": _ART_CACHE_CONTROL},
        )
