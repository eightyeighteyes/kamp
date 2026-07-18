"""Library-wide genre backfill worker (KAMP-591).

The "Update Library Genres" button runs this over every album: it re-fetches
genres from the new sources and merges them in via the shared per-album unit
(``enrich_album_genres``, KAMP-587) — Last.fm for all albums, plus each Bandcamp
album's original artist tags (cached from KAMP-588, or a one-time page re-scrape
for pre-588 albums whose cache is empty; a re-sync never backfills those).

The run can take hours on a large library, so it is:
- **Resumable** — driven by the ``albums.genres_enriched_at`` checkpoint, so a
  crash or cancel resumes from the un-enriched albums instead of restarting.
- **Cancellable** — a ``threading.Event`` checked before each album and before
  each network op.
- **Best-effort** — any source/album failing is logged and skipped; a
  circuit-breaker disables Last.fm for the rest of the run if it goes dark, so
  thousands of stacked timeouts don't turn a down service into a multi-hour stall.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable

from .genre_sources import enrich_album_genres

if TYPE_CHECKING:
    from kamp_core.library import LibraryIndex

    from .config import Config

logger = logging.getLogger(__name__)

# Pacing between albums (Bandcamp page GETs are HTML scraping — a ban risk — so a
# floor sleep spaces them; only cache-miss albums re-scrape).
_THROTTLE_S = 1.0
# An album whose enrich took ~this long AND yielded nothing almost certainly hit
# the Last.fm wall-clock timeout — count it toward the circuit breaker.
_LASTFM_SLOW_S = 6.0
_LASTFM_BREAKER_N = 5

# State strings for the progress payload.
RUNNING, DONE, CANCELLED = "running", "done", "cancelled"

ProgressCb = Callable[[int, int, str], None]


def _bandcamp_extra_genres(
    index: "LibraryIndex", album: dict[str, Any], session: Any, cancel: Any
) -> list[str]:
    """The album's Bandcamp tags, applied verbatim (588-consistent): cached
    keywords if present, else a one-time proxy re-scrape that is cached. [] for
    non-Bandcamp albums, no session, or a failed/empty scrape (an empty result is
    NEVER cached — it may be a silent Cloudflare challenge page)."""
    if not album.get("sale_item_id"):
        return []
    raw = album.get("keywords")
    if raw:  # cache hit — no network
        try:
            return list(json.loads(raw))
        except (ValueError, TypeError):
            return []
    album_url = album.get("album_url")
    if not session or not album_url or cancel.is_set():
        return []
    try:
        # session is a proxy-aware session (Cloudflare-safe when frozen); never a
        # raw requests.Session. .text works for both, like fetch_album_tracks.
        from .bandcamp import parse_album_keywords  # noqa: PLC0415

        resp = session.get(album_url, timeout=30)
        keywords = parse_album_keywords(resp.text)
    except Exception as exc:  # noqa: BLE001 — best-effort re-scrape
        logger.info(
            "genre backfill: Bandcamp re-scrape failed for %s (best-effort): %s",
            album_url,
            exc,
        )
        return []
    if keywords:  # only cache a real result
        index.set_collection_keywords(str(album["sale_item_id"]), keywords)
    return keywords


def run_genre_backfill(
    index: "LibraryIndex",
    config: "Config",
    session: Any,
    notify: ProgressCb,
    cancel: Any,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Enrich genres for every pending album. *session* may be None (no Bandcamp
    login) — Last.fm still runs. *notify(done, total, state)* reports progress."""
    pending = index.albums_pending_genre_enrichment()
    total = len(pending)
    notify(0, total, RUNNING)
    if total == 0:
        notify(0, 0, DONE)
        return

    lastfm_ok = True
    consecutive_slow = 0
    cfg_no_lastfm = replace(
        config, tagging=replace(config.tagging, lastfm_genres=False)
    )

    for done, album in enumerate(pending, start=1):
        if cancel.is_set():
            notify(done - 1, total, CANCELLED)
            return
        ids = [
            t.id for t in index.tracks_for_album(album["album_artist"], album["album"])
        ]
        if ids and not cancel.is_set():
            # When applying Bandcamp labels is disabled, skip the re-scrape
            # entirely — no point paying the network (and ban risk) to warm a
            # cache the user has turned off; the cheap sync-time cache still runs.
            extra = (
                _bandcamp_extra_genres(index, album, session, cancel)
                if config.tagging.bandcamp_genres
                else []
            )
            cfg = config if lastfm_ok else cfg_no_lastfm
            started = time.monotonic()
            try:
                applied = enrich_album_genres(index, ids, cfg, extra_genres=extra)
            except Exception as exc:  # noqa: BLE001 — one album can't break the run
                logger.warning(
                    "genre backfill: enrich failed for %r (best-effort): %s",
                    album["album"],
                    exc,
                )
                applied = []
            elapsed = time.monotonic() - started
            if lastfm_ok:
                if not applied and elapsed >= _LASTFM_SLOW_S:
                    consecutive_slow += 1
                    if consecutive_slow >= _LASTFM_BREAKER_N:
                        lastfm_ok = False
                        logger.warning(
                            "genre backfill: Last.fm looks unreachable "
                            "(%d slow empty albums) — disabling it for the rest "
                            "of this run; Bandcamp continues",
                            consecutive_slow,
                        )
                elif applied:
                    consecutive_slow = 0

        # Checkpoint after each album (even empty ones) so a resume skips it.
        index.mark_album_genres_enriched(album["id"], time.time())
        notify(done, total, RUNNING)
        if done < total:
            sleep(_THROTTLE_S)

    notify(total, total, DONE)
