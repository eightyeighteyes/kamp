"""The Bandcamp discovery provider (KAMP-647).

Owns exactly three things: the fetch loop, the one place that talks to the
network, and capability reporting. Parsing lives in
:mod:`kamp_daemon.discovery_bandcamp_parsers`, seed selection in
:mod:`kamp_daemon.discovery_criteria`, and neither of those knows the network
exists.

That split is the point. Rate limiting, budget accounting, dead-seed handling and
the custom-domain skip all live in :meth:`BandcampDiscoverySource._fetch`, so a
criterion cannot forget one of them — a criterion asks for a document and gets a
document or nothing.
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING, Any

from kamp_core.proxy_hosts import FETCHABLE_HOSTS, host_allowed

from .bandcamp_ratelimit import BandcampGovernor, get_governor
from .discovery import (
    PREVIEW,
    Candidate,
    DiscoverySource,
    PreviewStream,
    RequestBudget,
    SeedProfile,
)
from .discovery_bandcamp_parsers import (
    ParseResult,
    parse_also_like,
    parse_discography,
    parse_discover_results,
    release_year,
    tag_slug,
)
from .discovery_criteria import (
    OLD_ALBUM_YEARS,
    SURFACE_ALBUM_RECS,
    SURFACE_DISCOGRAPHY,
    SURFACE_DISCOVER,
    Criterion,
    Seed,
    criteria_for,
)

if TYPE_CHECKING:  # pragma: no cover - types only
    from .bandcamp import _AnySession

logger = logging.getLogger(__name__)

DISCOVER_API_URL = "https://bandcamp.com/api/discover/1/discover_web"


def _is_fetchable(url: str) -> bool:
    """True if discovery may fetch *url*.

    A Bandcamp Pro custom domain matches nothing in FETCHABLE_HOSTS, so a
    candidate pointing at one is unfetchable in every packaged build (~1% of a
    real collection) and is skipped rather than attempted.
    """
    return host_allowed(url, FETCHABLE_HOSTS)


class RateLimitedError(RuntimeError):
    """Raised internally when the origin rate-limits us mid-gather."""


class BandcampDiscoverySource(DiscoverySource):
    """Turns a taste profile into Bandcamp candidates."""

    provider_id = "bandcamp"

    def __init__(
        self,
        session: "_AnySession",
        *,
        governor: BandcampGovernor | None = None,
        now: Any = None,
    ) -> None:
        # Session arrives by constructor injection: gather(profile, budget) offers
        # no route to one, and capabilities depend on being logged in.
        self._session = session
        self._governor = governor or get_governor()
        self._now = now

    @property
    def capabilities(self) -> frozenset[str]:
        """Preview only, for now.

        ``save_remote`` (wishlist-add) is deliberately absent: it needs a
        form-encoded POST that the Electron relay cannot express, so it would be
        dead in every packaged build until KAMP-653 extends the relay. Declaring
        it here would render a wishlist button that silently does nothing.
        """
        return frozenset({PREVIEW})

    @property
    def criterion_caps(self) -> dict[str, int]:
        """One chart pick per crate.

        ``best_seller`` is the only criterion carrying no personal claim, and the
        brand guardrails forbid letting un-personalised content dominate a crate
        that presents itself as dug for you. Every other criterion may repeat as
        the round-robin allows.
        """
        return {"best_seller": 1}

    # ------------------------------------------------------------------
    # The only place that touches the network
    # ------------------------------------------------------------------

    def _fetch(
        self,
        endpoint_class: str,
        url: str,
        budget: RequestBudget,
        *,
        payload: Any = None,
    ) -> str | None:
        """Spend one request, or return None having spent nothing.

        Owns every policy a criterion would otherwise have to remember:
        the budget, the governor's spacing and cooldown, the custom-domain skip,
        and dead seeds. Returns the response body, or None when the request was
        skipped or failed in a way the caller should treat as "no document".

        Raises :class:`RateLimitedError` on a 429 so the whole gather stops rather
        than letting each remaining criterion discover the limit separately.
        """
        if not budget.allow(endpoint_class):
            logger.debug("discovery: %s budget exhausted", endpoint_class)
            return None

        if not _is_fetchable(url):
            # A Bandcamp Pro custom domain. The relay would 422 it, so skip
            # rather than burn a request proving that.
            logger.debug("discovery: skipping unfetchable host %s", url)
            return None

        if not self._governor.wait_turn(endpoint_class):
            # Shutting down. Do not issue the request.
            raise RateLimitedError("shutting down")

        budget.consume(endpoint_class)
        try:
            if payload is None:
                resp = self._session.get(url, timeout=30)
            else:
                resp = self._session.post(url, json=payload, timeout=30)
        except Exception as exc:  # noqa: BLE001 - one bad fetch is not a crate failure
            logger.warning("discovery: fetch failed for %s: %s", url, exc)
            return None

        # Status is read explicitly rather than via raise_for_status(): a relayed
        # response is a _ProxyResponse, and a 429 has to be distinguishable from a
        # 404 to avoid feeding a dead seed into the rate-limit ladder.
        status = resp.status_code
        if status == 429:
            self._governor.report_429(endpoint_class)
            raise RateLimitedError(f"429 from {url}")
        if status == 404:
            # A stored album_url can go dead; that is a skip, not an error.
            logger.debug("discovery: seed gone (404) %s", url)
            return None
        if status >= 400:
            logger.warning("discovery: HTTP %d from %s", status, url)
            return None

        self._governor.report_ok(endpoint_class)
        return resp.text

    # ------------------------------------------------------------------
    # Gather
    # ------------------------------------------------------------------

    def gather(self, profile: SeedProfile, budget: RequestBudget) -> list[Candidate]:
        """Collect candidates across the criteria that suit *profile*.

        Mirrors ``genre_sources.fetch_all_genres``: each criterion is best-effort
        and a broken one costs a card rather than the crate. Unlike that helper,
        this stops early on a 429 — letting the remaining criteria each rediscover
        the limit would turn one rate limit into several and log it repeatedly.
        """
        out: list[Candidate] = []
        seen: set[str] = set()

        # Albums the user already owns. The discover surface reports is_owned
        # itself, but album-page recommendations and the discography grid do not,
        # and the discography of a favourite artist is precisely where owned
        # records cluster — recommending someone their own collection is the
        # clerk who does not know his own shop. The profile already carries these
        # ids, so no database access is needed here.
        owned = set(profile.purchase_dates)

        for criterion in criteria_for(profile):
            try:
                found = self._run_criterion(criterion, profile, budget, owned)
            except RateLimitedError as exc:
                logger.warning("discovery: stopping gather early — %s", exc)
                break
            except Exception:  # noqa: BLE001 - a buggy criterion cannot break the crate
                logger.warning(
                    "discovery: criterion %s failed (best-effort)",
                    criterion.key,
                    exc_info=True,
                )
                continue
            for candidate in found:
                # Dedupe within the gather: ~15% of recommendations recur across
                # seeds (KAMP-644). Crate-level variety and caps are KAMP-648's.
                if candidate.provider_item_id in seen:
                    continue
                seen.add(candidate.provider_item_id)
                out.append(candidate)
        return out

    def _run_criterion(
        self,
        criterion: Criterion,
        profile: SeedProfile,
        budget: RequestBudget,
        owned: set[str] | None = None,
    ) -> list[Candidate]:
        found: list[Candidate] = []
        for seed in criterion.seeds(profile):
            if not budget.allow(criterion.endpoint_class):
                break
            found.extend(self._run_seed(criterion, seed, budget, owned or set()))
            if found:
                # One good seed per criterion is enough for a crate; spending the
                # rest of the budget deepening a single criterion would starve the
                # others and make every crate look the same.
                break
        return found

    def _run_seed(
        self,
        criterion: Criterion,
        seed: Seed,
        budget: RequestBudget,
        owned: set[str],
    ) -> list[Candidate]:
        if criterion.surface == SURFACE_DISCOVER:
            return self._discover(criterion, seed, budget, owned)
        if criterion.surface == SURFACE_ALBUM_RECS:
            body = self._fetch(criterion.endpoint_class, str(seed.target), budget)
            if body is None:
                return []
            result = parse_also_like(body)
            result.warn_if_drifted(criterion.surface, str(seed.target))
            return self._to_candidates(criterion, seed, result, owned)
        if criterion.surface == SURFACE_DISCOGRAPHY:
            body = self._fetch(criterion.endpoint_class, str(seed.target), budget)
            if body is None:
                return []
            result = parse_discography(body, base_url=str(seed.target))
            result.warn_if_drifted(criterion.surface, str(seed.target))
            # The grid carries no artist name — every entry is the page's artist,
            # which the seed already knows.
            artist = str(seed.seed_data.get("artist", ""))
            for item in result.items:
                item.setdefault("artist", artist)
            return self._to_candidates(criterion, seed, result, owned)
        logger.warning("discovery: unknown surface %r", criterion.surface)
        return []

    def _discover(
        self,
        criterion: Criterion,
        seed: Seed,
        budget: RequestBudget,
        owned: set[str],
    ) -> list[Candidate]:
        params: dict[str, Any] = dict(seed.target)
        # Normalise here rather than in the criterion: the seed carries the display
        # genre so the clerk card can say "you've been deep in Indie Rock lately",
        # while the API needs "indie-rock".
        tag = tag_slug(params.get("tag") or "")
        payload = {
            "category_id": 0,
            "tag_norm_names": [tag] if tag else [],
            "geoname_id": 0,
            "slice": params.get("slice", "top"),
            "time_facet_id": None,
            "cursor": None,
            "size": params.get("size", 20),
            "include_result_types": ["a"],
            "followed_bands": False,
        }
        body = self._fetch(
            criterion.endpoint_class, DISCOVER_API_URL, budget, payload=payload
        )
        if body is None:
            return []
        result = parse_discover_results(body)
        result.warn_if_drifted(criterion.surface, DISCOVER_API_URL)

        # Bandcamp reports ownership on this surface, so let it do the exclusion.
        result.items = [
            item
            for item in result.items
            if not item.get("is_owned") and not item.get("is_wishlisted")
        ]
        if criterion.key == "older_than_ten":
            result.items = [item for item in result.items if self._is_old_enough(item)]
        return self._to_candidates(criterion, seed, result, owned)

    def _is_old_enough(self, item: dict[str, Any]) -> bool:
        year = release_year(item.get("release_date", ""))
        if year is None:
            return False
        now_year = _time.gmtime(self._now() if self._now else _time.time()).tm_year
        return now_year - year >= OLD_ALBUM_YEARS

    def _to_candidates(
        self,
        criterion: Criterion,
        seed: Seed,
        result: ParseResult,
        owned: set[str],
    ) -> list[Candidate]:
        out: list[Candidate] = []
        for item in result.items:
            if item["provider_item_id"] in owned:
                # Recommending someone a record they already own is the clerk who
                # does not know his own shop. Only the discover surface reports
                # ownership itself; this covers the other two.
                continue
            url = item.get("item_url") or ""
            if not _is_fetchable(url):
                # Same reasoning as the fetch-side skip: a candidate we could
                # never fetch art or a preview for is not a usable card.
                continue
            out.append(
                Candidate(
                    provider=self.provider_id,
                    provider_item_id=item["provider_item_id"],
                    item_url=url,
                    artist=item.get("artist", ""),
                    title=item.get("title", ""),
                    art_url=item.get("art_url"),
                    release_date=item.get("release_date", ""),
                    criterion=criterion.key,
                    why=seed.why,
                    seed=dict(seed.seed_data),
                )
            )
        return out

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview_tracks(self, candidate: Candidate) -> list[PreviewStream]:
        """Every playable track on the candidate's own album page.

        One request for the whole album, so stepping through tracks costs
        nothing further while the URLs are alive.

        **Does not wait on the governor**, unlike every other request this class
        makes. ``bandcamp_ratelimit`` documents itself as a non-playback tool for
        exactly this reason: ``wait_turn`` blocks until a 60/120/300s cooldown
        expires, and a listener who clicked play would get a hang with nothing on
        screen to explain it. The outcome is still reported, so a 429 earned here
        makes the *crate builder* back off -- the cost lands on the background
        work rather than on the click.
        """
        from .bandcamp import parse_tralbum, stream_url_expiry

        # item_url is remote data read back out of discovery_items, so it gets
        # the same host check the art proxy applies (KAMP-649). A Bandcamp Pro
        # custom domain also lands here, and is unfetchable in packaged builds.
        if not _is_fetchable(candidate.item_url):
            logger.debug("discovery: preview host not fetchable %s", candidate.item_url)
            return []

        try:
            resp = self._session.get(candidate.item_url, timeout=30)
        except Exception:  # noqa: BLE001 - a failed preview is not an error state
            logger.warning("discovery: preview fetch failed for %s", candidate.item_url)
            return []

        status = resp.status_code
        if status == 429:
            self._governor.report_429("album_page")
            raise RateLimitedError(f"429 from {candidate.item_url}")
        if status != 200:
            logger.warning(
                "discovery: preview HTTP %d from %s", status, candidate.item_url
            )
            return []
        self._governor.report_ok("album_page")

        tralbum = parse_tralbum(resp.text)
        if not tralbum:
            logger.warning("discovery: no tralbum on %s", candidate.item_url)
            return []

        # A standalone single-track page exposes its lone track with
        # track_num=None; it is #1 everywhere else in kamp, so match that here.
        is_single = tralbum.get("item_type") == "track"
        out: list[PreviewStream] = []
        for track in tralbum.get("trackinfo") or []:
            files = track.get("file") or {}
            url = files.get("mp3-128") or files.get("mp3-v0")
            if not url:
                continue  # unreleased / pre-order tracks carry no stream
            out.append(
                PreviewStream(
                    url=url,
                    track_num=int(track.get("track_num") or (1 if is_single else 1)),
                    title=track.get("title") or "",
                    duration=float(track.get("duration") or 0.0),
                    expires_at=stream_url_expiry(url),
                )
            )
        if not out:
            logger.info("discovery: nothing streamable on %s", candidate.item_url)
        return out
