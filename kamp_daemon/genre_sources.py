"""Genre sources for the post-ingest enrichment step (KAMP-587).

MusicBrainz gives kamp poor genre data, so ingested albums are enriched with
genres from additional sources — starting with Last.fm via pylast. Fetching is
best-effort: it runs asynchronously after ingest (never on the pipeline's
critical path), is wall-clock bounded (pylast hardcodes a 20s read timeout with
no override), swallows all network errors, and can never fail an ingest.

Last.fm top-tags are an unbounded, noisy vocabulary, so raw tags are filtered
through a bundled canonical allowlist (``data/genres.txt``) rather than chased
with a denylist — the ingest merge is additive, so anything written once is
sticky. The MusicBrainz-as-a-second-source variant is a deferred fast-follow
through the same ``GenreSource`` interface.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from kamp_core.library import LibraryIndex

    from .config import Config

logger = logging.getLogger(__name__)

# Minimum Last.fm tag weight (0-100) to consider; below this is long-tail noise.
_MIN_TAG_WEIGHT = 10
# How many top tags to pull per entity before filtering.
_TOP_TAGS_LIMIT = 20
# Per-album wall-clock budget for a source fetch. pylast's read timeout is 20s
# and cannot be overridden, so bound it ourselves and abandon on expiry.
_FETCH_TIMEOUT_SECONDS = 8.0

_ALLOWLIST_PATH = Path(__file__).parent / "data" / "genres.txt"
# casefold -> canonical display name; loaded once.
_allowlist: dict[str, str] | None = None


@dataclass
class GenreQuery:
    """The album-level identity a source is queried on. Keyed on album-artist
    (never a track artist) so compilations/various-artists resolve correctly."""

    album_artist: str
    album: str


class GenreSource(ABC):
    """A best-effort source of canonical genre names for an album."""

    @abstractmethod
    def fetch(self, query: GenreQuery) -> list[str]:
        """Return canonical genres for *query*, or [] on any failure."""


def _load_allowlist() -> dict[str, str]:
    global _allowlist
    if _allowlist is None:
        out: dict[str, str] = {}
        try:
            for line in _ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
                name = line.strip()
                if name and not name.startswith("#"):
                    out.setdefault(name.casefold(), name)
        except OSError as exc:
            # A missing data file (e.g. not staged into the frozen bundle) must
            # not crash — it degrades to "no genres pass the filter", and the
            # WARN makes that visible rather than silent.
            logger.warning(
                "genre allowlist not loaded from %s: %s", _ALLOWLIST_PATH, exc
            )
        _allowlist = out
    return _allowlist


def canonicalize(tags: list[str]) -> list[str]:
    """Filter raw tags to the canonical allowlist (case-insensitive), emit the
    allowlist's canonical casing, order-preserving and de-duplicated."""
    allow = _load_allowlist()
    seen: dict[str, str] = {}
    for tag in tags:
        cf = tag.strip().casefold()
        if cf in allow and cf not in seen:
            seen[cf] = allow[cf]
    return list(seen.values())


def _run_with_timeout(fn: Callable[[], list[str]], timeout: float) -> list[str]:
    """Run *fn* on a daemon thread with a wall-clock budget; [] on timeout/error.
    The abandoned thread may keep running a hung network call, but as a daemon it
    never blocks shutdown and its result is discarded."""
    result: list[list[str]] = []
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(fn())
        except BaseException as exc:  # noqa: BLE001 — best-effort, swallow all
            error.append(exc)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        logger.warning("genre fetch exceeded %ss budget; abandoning", timeout)
        return []
    if error:
        logger.warning("genre fetch failed (best-effort): %s", error[0])
        return []
    return result[0] if result else []


class LastfmGenreSource(GenreSource):
    """Last.fm genres via pylast, read-only (needs only the app API key). Fetches
    album then artist top-tags, keeps tags at/above a weight threshold, and
    filters them to the canonical allowlist."""

    def __init__(self, network: Any = None) -> None:
        # network is injectable for tests; built lazily from the shared API key.
        self._network = network

    def _get_network(self) -> Any:
        if self._network is None:
            import pylast  # noqa: PLC0415 — heavy import, only when enabled

            from kamp_core.scrobbler import LASTFM_API_KEY  # noqa: PLC0415

            self._network = pylast.LastFMNetwork(api_key=LASTFM_API_KEY)
        return self._network

    def fetch(self, query: GenreQuery) -> list[str]:
        raw = _run_with_timeout(lambda: self._fetch_raw(query), _FETCH_TIMEOUT_SECONDS)
        return canonicalize(raw)

    def _fetch_raw(self, query: GenreQuery) -> list[str]:
        net = self._get_network()
        tags: list[str] = []
        getters: list[Callable[[], list[Any]]] = [
            lambda: net.get_album(query.album_artist, query.album).get_top_tags(
                limit=_TOP_TAGS_LIMIT
            ),
            lambda: net.get_artist(query.album_artist).get_top_tags(
                limit=_TOP_TAGS_LIMIT
            ),
        ]
        for getter in getters:
            try:
                for top in getter():
                    try:
                        weight = int(top.weight or 0)
                    except (TypeError, ValueError):
                        weight = 0
                    if weight >= _MIN_TAG_WEIGHT:
                        tags.append(str(top.item.get_name()))
            except Exception as exc:  # noqa: BLE001 — one getter failing is fine
                logger.debug("Last.fm getter failed (best-effort): %s", exc)
        return tags


def enabled_sources(config: "Config") -> list[GenreSource]:
    """The genre sources enabled by config. Empty when nothing is on, so the
    caller can cheaply skip enrichment entirely."""
    sources: list[GenreSource] = []
    if config.tagging.lastfm_genres:
        sources.append(LastfmGenreSource())
    return sources


def fetch_all_genres(sources: list[GenreSource], query: GenreQuery) -> list[str]:
    """Union the canonical genres from every source, order-preserving, deduped.
    Each source is already best-effort ([] on failure), so this never raises."""
    seen: dict[str, str] = {}
    for source in sources:
        try:
            names = source.fetch(query)
        except Exception as exc:  # noqa: BLE001 — a buggy source can't break enrich
            logger.warning(
                "genre source %s failed (best-effort): %s",
                type(source).__name__,
                exc,
            )
            names = []
        for name in names:
            cf = name.casefold()
            if cf not in seen:
                seen[cf] = name
    return list(seen.values())


def enrich_album_genres(
    index: "LibraryIndex", track_ids: list[int], config: "Config"
) -> list[str]:
    """Fetch genres for the album containing *track_ids* from the enabled sources
    and merge them in — DB via ``apply_genres(merge)`` and, for local tracks, the
    files. The shared enrichment core (KAMP-587; KAMP-591 reuses it library-wide).

    Best-effort and idempotent: returns the genres it applied ([] when nothing is
    enabled, no tracks resolve, or no source yielded a genre). Files must be
    written too, not just the DB: a local file's re-scan REPLACES its DB genres
    from the file, so a DB-only addition would be wiped on the next scan.
    """
    sources = enabled_sources(config)
    if not sources or not track_ids:
        return []
    tracks = [t for t in (index.get_track_by_id(tid) for tid in track_ids) if t]
    if not tracks:
        return []

    # album_artist is the album-level artist (not the per-track performer), so
    # this keys correctly for compilations / various-artists.
    first = tracks[0]
    query = GenreQuery(album_artist=first.album_artist, album=first.album)
    genres = fetch_all_genres(sources, query)
    if not genres:
        return []

    index.apply_genres([t.id for t in tracks], genres, mode="merge")

    from kamp_core.library import write_meta_tags_to_file  # noqa: PLC0415

    for track in tracks:
        if track.is_remote:
            continue
        # Re-read the post-merge canonical set. The DB read exposes it as the
        # denormalized "; "-joined `genre` string (the `genres` list is a
        # write-path field, empty on DB read), so split it for the file write.
        merged = index.get_track_by_id(track.id)
        if merged is None:
            continue
        names = [g.strip() for g in merged.genre.split(";") if g.strip()]
        try:
            write_meta_tags_to_file(Path(merged.file_path), genres=names)
        except Exception as exc:  # noqa: BLE001 — best-effort file write
            logger.warning(
                "genre enrich: file write failed for %s (best-effort): %s",
                merged.file_path,
                exc,
            )
    return genres


def enrich_new_tracks(
    index: "LibraryIndex", tracks: list[Any], config: "Config"
) -> None:
    """Enrich the albums of newly-scanned LOCAL tracks (KAMP-587 trigger core).

    Called with a scan's ``new_tracks`` after each ingest. Groups by album so an
    album is fetched once, skips remote/streaming tracks (they have no file to
    stamp), and is a cheap no-op when no source is enabled. Synchronous and
    best-effort; the daemon runs it on a background thread so a slow Last.fm can
    never stall the scan.
    """
    if not enabled_sources(config):
        return
    by_album: dict[tuple[str, str], list[int]] = {}
    for track in tracks:
        if not track.is_remote:
            by_album.setdefault((track.album_artist, track.album), []).append(track.id)
    for ids in by_album.values():
        try:
            enrich_album_genres(index, ids, config)
        except (
            Exception
        ) as exc:  # noqa: BLE001 — one album failing can't break the rest
            logger.warning("genre enrichment failed (best-effort): %s", exc)
