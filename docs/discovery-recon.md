# Discovery recon findings (KAMP-644)

Reconnaissance for the Discovery Crate epic (KAMP-643). Everything downstream — the v61 schema
(KAMP-645), the fetchers and criteria (KAMP-647), the governor's budget (KAMP-646) — rests on
unofficial Bandcamp endpoints. This document is the single home for what was actually observed;
the Jira issue links here rather than duplicating it.

**Status: in progress.** Sections below are filled in as each item is investigated.

---

## Pre-registered no-go criteria

Written down *before* any request was made, so a verdict cannot be rationalised after the fact.
A spike where every item returns "go" has tested nothing. `no-go` and `unknown` are successful
outcomes; the only failure is an unevidenced claim.

Every verdict must carry: the exact request (method, URL, headers, transport), a committed fixture
with a manifest entry, and the observed fields **quoted from the capture** rather than paraphrased.

### Item A — identity fields per surface

Blocks KAMP-645, which is scheduled in parallel and whose schema hardcodes
`UNIQUE(provider, provider_item_id)` with `provider_item_id = tralbum_id`.

- **go** — every result on every go-surface carries a stable numeric item id (tralbum_id or
  equivalent) that is present for albums the user does *not* own, and it round-trips: the id
  observed on a discovery surface matches the id on that album's own page.
- **no-go** — results carry only a URL and/or an art id, with no stable numeric identity; or the id
  present is only meaningful post-purchase (i.e. it is a `sale_item_id` analogue).
- **unknown** — an id is present but its stability across surfaces could not be confirmed.
- **Consequence of anything but `go`:** KAMP-645's UNIQUE key must be amended in writing *before*
  that story starts. The fallback identity is `item_url` (normalised), which is weaker — it changes
  if an artist renames a release.

### Item B — discover/tag surface

Four of the epic's six criteria depend on this surface.

- **go** — the result set is present in the fetched document (or in a documented JSON endpoint we
  can call directly), and at least the genre/tag facet meets the facet test below.
- **no-go** — results are rendered client-side from an XHR we cannot reach with the session, or the
  surface requires JS execution to produce any items at all.
- **unknown** — reachable but the facet semantics could not be validated within the timebox.

**A facet counts as working only if all three hold.** Passing `&year=2014` and getting results back
is *not* evidence — a silently ignored parameter looks identical to a working one:

1. Two distinct values produce materially different result sets.
2. A nonsense value errors or returns empty (rather than the unfiltered default).
3. Results reproduce across two runs at least 10 minutes apart.

Facets to rule on individually: genre/tag, sort (best-selling vs new arrivals), date/year
(needed by the 10+-years and Anniversary criteria), location (needed by Local Scene), label if any.

### Item C — also-like block and post-exclusion yield

- **go** — the recommendation block is parseable from the album page and post-exclusion yield is
  high enough that a 10-item crate is achievable inside a sane request budget.
- **no-go** — the block is absent for a material share of seed albums, or yield is so low that
  most recommendations are already owned.
- **unknown** — parseable but the sample was too small to characterise yield.

**Yield is the number nobody has measured.** "You may also like" is personalised to the logged-in
fan and plausibly biased toward what they already bought. Of N recommendations for a seed album,
how many survive exclusion against the 829 albums already in `bandcamp_collection`? If 50–60% are
already owned, KAMP-646's per-crate budget is wrong by roughly 2x and KAMP-648's "short crate"
degradation becomes the *normal* path rather than the exception. Sample size must be stated with
the number.

### Item 7 — wishlist write

- **go** — requires a real HTTP 200 on an add, confirmed by re-reading the wishlist. A crumb that
  merely parses proves nothing about field names, `item_type`, or referer checks.
- **no-go** — the POST cannot be constructed or is rejected; **or** it succeeds on the direct
  transport but cannot be expressed through the relay (see the transport note below), because the
  relay is the only path shipped builds take.
- **unknown — not attempted** is the honest verdict if the round trip is not run.

---

## Transport: verdicts are only as good as the transport they were proven on

`_needs_proxy_session()` returns `_is_frozen() or sys.platform == "win32"`
(`kamp_daemon/bandcamp.py`). The shipped app is a PyInstaller bundle on every platform, so
**every real user reaches Bandcamp through the Electron relay**, never through plain `requests`.
A spike run only under Poetry characterises a transport nobody ships.

Verified constraints on `_ProxySession` (`kamp_daemon/bandcamp.py:163`), each of which can turn a
direct-transport "go" into a shipped-build failure:

| Constraint | Consequence |
| --- | --- |
| `_fetch(..., *, headers, json, timeout, **_kwargs)` — no `data=` | A form-encoded POST is silently sent with an empty body. If Bandcamp's wishlist write is form-encoded, wishlist-add is blocked in every shipped build until `_ProxySession`, the server model, and the Electron handler are extended. |
| `_ProxyResponse` exposes status/text/content_type/url/ok only | No `Retry-After`, no `Set-Cookie`. Rate-limit calibration through the relay is blind by construction. |
| No `.content` (text only) | Binary responses cannot round-trip. |
| GET/POST only; redirects auto-followed, opaque | Redirect chains cannot be inspected. |
| `_ALLOWED_PROXY_HOSTS` = `bandcamp.com`, `f4.bcbits.com`, `t4.bcbits.com` (`kamp_core/server.py:873`) | Bandcamp Pro custom domains 422. Measured against the live collection: **8 of 829 owned albums (~1%)** sit on custom domains. Recommendations pointing at such hosts are unfetchable in packaged builds and need a documented skip in KAMP-647. |

Accordingly: **every verdict below is tagged with the transport it was proven on**, and items A/B/C
are each exercised once through `POST /api/v1/bandcamp/proxy-fetch` in addition to the direct path.
Crumb acquisition and crumb use must happen on the *same* transport — a crumb fetched by `requests`
and posted through Electron's cookie jar is a different session pairing.

---

## Requirement handed to KAMP-647: parsers must warn on a zero-result parse

`parse_album_keywords` already does this deliberately, so that a Cloudflare challenge page or a
markup change cannot ship the feature silently dead. Every discovery parser must follow it: a parse
that finds zero items where items were expected logs a WARNING naming the surface and URL. Silent
emptiness is the failure mode that turns "discovery is broken" into a support mystery, and it is
exactly what unofficial endpoints produce when they drift.

---

## Findings

Investigated 2026-08-06. Every verdict below names the transport it was proven on.
Requests were made from a macOS dev checkout against a live account with 829
collection items; the account was left exactly as found.

### Verdict summary

| Item | Verdict | Proven on |
| --- | --- | --- |
| A — identity fields | **go** | direct + relay |
| B — discover surface | **go** | direct + relay |
| C — also-like block and yield | **go** | direct + relay |
| Best sellers | **go** (it is a discover slice) | direct + relay |
| Artist discography | **go** | direct |
| Wishlist read | **go** | direct |
| Wishlist add/remove | **go on direct, NO-GO on the relay** | both |
| "Over 10 years old" via the time facet | **no-go** (workaround below) | direct |
| Anniversary (specific month/year) | **no-go** | direct |
| Label pages / Label-Mates | **unknown** — not reached in the timebox | — |
| Local Scene | **go, but only for 36 cities** | direct |
| Rate limit located | **yes** — 429 at 57 album pages in 39s; discover API clean at 120 | direct |

### A — identity fields (go)

Every discovery surface carries a stable numeric tralbum id, present for albums the
account does not own, and it round-trips: the id on a recommendation matches the id
on that album's own page. KAMP-645 may keep `UNIQUE(provider, provider_item_id)`
with `provider_item_id = tralbum_id` as designed. **No amendment needed.**

It is spelled differently on each surface, which is a normalisation job for KAMP-647
rather than a risk:

| Surface | Identity field | Example |
| --- | --- | --- |
| Album-page recommendation | `data-albumid` (also `id="id-<n>"`) | `3149089081` |
| Discover results API | `item_id` | `368758684` |
| Artist discography grid | `data-item-id="album-<n>"` | `album-533227808` |

`item_type` is spelled differently too — `"a"` on the discover API, `"album"` on the
`*_cb` endpoints and in wishlist rows. Sending the wrong one earns a bare HTTP 400.

### B — discover/tag surface (go)

The `/discover` page is **not** JS-rendered in the way it first appears. It ships its
initial state server-side on `<div id="DiscoverApp" data-ssr-rendered data-blob="…">`
— a different element id from the `pagedata` blob `bandcamp.py` already knows, which
is the only reason it looked client-only at first glance. That blob carries the whole
facet vocabulary: **27 genres, 237 subgenres, 36 locations, 9 times, 3 slices**. Read
it rather than hard-coding lists.

Results are *not* in the HTML. They come from an RPC the page calls as
`Discover_1 / discover_web`:

```
POST https://bandcamp.com/api/discover/1/discover_web
{"category_id":0, "tag_norm_names":["ambient"], "geoname_id":0, "slice":"top",
 "time_facet_id":null, "cursor":null, "size":20,
 "include_result_types":["a"], "followed_bands":false}
```

Body shape lifted from the page bundle's own `makeParams`, not guessed. `size` up to
60 works in one call, and `cursor` paginates. Each result carries `item_id`,
`item_url`, `band_id`, `band_name`, `title`, `release_date`, `primary_image`,
`featured_track` — **and `is_owned` / `is_wishlisted`**, so Bandcamp performs
exclusion for us on this surface. Cross-checked against our own
`bandcamp_collection`: the two agreed exactly (0/20 owned on both).

**Facets, each held to the three-part test** (two values differ; a nonsense value is
rejected; reproducible across runs):

| Facet | Verdict | Evidence |
| --- | --- | --- |
| tag/genre | real | `electronic` vs `jazz` shared 0/20; a nonsense tag returned **0 results**, not an unfiltered fallback |
| slice (sort) | real | `top` vs `new` shared 0/20 |
| time | real | none vs this-week 0/20; this-week vs 6-weeks 0/20 |
| location | real | anywhere vs berlin 0/20 |

The nonsense-tag result is the load-bearing one: a silently ignored parameter is
indistinguishable from a working one if you only check that results came back.

**The time facet is a recency window, not a release-year filter.** Its full range is
`fresh, today, this-week, 1w … 6w` — six weeks, and it refers to when an item
surfaced on Bandcamp, not when it was released. Consequences:

- The epic's *"an album from over 10 years ago that overlaps with the user's genres"*
  **cannot** use it. It is still achievable: `slice=rand` returns a broad release-year
  spread (a 60-result ambient sample ran 1998–2026, with **12/60 at least ten years
  old**), so the criterion becomes `slice=rand` plus a client-side `release_date`
  filter at roughly a 20% hit rate. `slice=top` will not do — 49 of 60 were from the
  current year.
- The Whimsy "Anniversary Pressing" idea (a specific month, 5/10/15/20 years back) is
  **no-go**: at that hit rate the odds of landing a specific anniversary month are too
  low to build a criterion on.
- **Local Scene** is feasible but bounded: locations are 36 named cities with geoname
  ids (`amsterdam=2759794`, `berlin=2950159`, …), so the criterion only fires for users
  whose collection maps to one of them.

### C — also-like block and post-exclusion yield (go)

The recommendation block is plain server-rendered HTML inside
`<div class="recommendations-container">`, one `<li class="recommended-album">` per
entry, on the album page we are already fetching. Each entry carries far more than
identity:

- `data-albumid`, `data-artistid`, `data-albumtitle`, `data-artist`
- `data-audiourl` — **an `mp3-128` stream URL inline**, so a preview needs no second
  fetch of the recommended album's own page
- album art on `f4.bcbits.com` (art id extractable), and `data-from=footer-cc-a<seed>`
  which records the seed album
- `<p class="supporters-text">` — e.g. *"supported by 265 fans who also own 'Pink'"*
- `<p class="comment">` — a real fan's written review

The last two are unusually good raw material for the clerk-card provenance the UI
stories want: genuine human sentences about the record, not templated copy.

**Yield — the number nobody had measured.** Across 7 seed albums (one seed 404'd):

| Measure | Result |
| --- | --- |
| Recommendations returned | 48 (~6.9 per album page) |
| Identity complete (`tralbum_id` + `item_url`) | 48/48 |
| `mp3-128` preview embedded | 48/48 |
| **Already owned (excluded)** | **0 (0%)** |
| Distinct ids | 41 (5 appeared under more than one seed) |
| Album-page requests per 10-item crate | **~1.5** |

Post-exclusion yield is ~100%, the opposite of the feared 50–60% overlap. The likely
mechanism is that the block is a storefront: it recommends what you have *not* bought.
Two caveats: the sample is one account's taste, and recommendations are personalised,
so a fresh user's numbers will differ. Cross-seed duplication (~15%) is small but real,
so the crate builder must dedupe across seeds — ten *distinct* items is the requirement.

**KAMP-646 consequence:** the per-crate request budget can be far smaller than the
15–25 the story assumes. See the cost table below.

### Wishlist (read: go — write: go on direct, no-go on the relay)

**Read** — `POST https://bandcamp.com/api/fancollection/1/wishlist_items` with
`{fan_id, count, older_than_token}`, the same shape as the collection walk. Returns
100 rows per call with `more_available` and `last_token`; this account has more than
100, so KAMP-652 must paginate.

**A cheaper exclusion source exists for the common case.** Any logged-in album page
carries `pagedata.fan_tralbum_data` with `is_wishlisted`, `is_purchased`,
`follows_band` and `follows_label` for that album, and the discover API returns
`is_owned`/`is_wishlisted` per result. So a full wishlist walk is only needed to
exclude *before* fetching; per-item state is free on pages we already have.

**Write** — proven end to end with a real add, verification, and removal; the account
was returned to its original state.

- Crumbs live in `<meta id="js-crumbs-data" data-crumbs="{…}">` on any logged-in page,
  keyed per action, shaped `|action|epoch|hmac=`. Both `collect_item_cb` and
  `uncollect_item_cb` are present, so add *and* remove are available.
- `POST https://bandcamp.com/collect_item_cb` with
  `{fan_id, item_id, item_type:"album", band_id, crumb}` plus
  `X-Requested-With: XMLHttpRequest` and a `Referer`.
- **The body must be form-encoded.** The identical call with a JSON body returns
  HTTP 200 carrying
  `{"error":true,"ok":false,"exception":"…InsistError… old or no crumb specified …"}`.
  Form-encoded returns `{"ok":true}` and the album page then reports
  `is_wishlisted: true`.

> **This is the blocker for KAMP-653, and it is a kamp problem rather than a Bandcamp
> one.** `_ProxySession._fetch` accepts only `json=`; a `data=` kwarg falls into
> `**_kwargs` and is silently dropped, and the relay's wire format carries a single
> `body` string with a JSON content type. Since every shipped build routes through the
> relay, **wishlist-add cannot work in any packaged build today**. Making it work means
> extending `_ProxySession`, the proxy-fetch request model in `kamp_core/server.py`,
> and the Electron handler in `kamp_ui/src/main/index.ts` to carry a form-encoded body
> and content type. That work belongs in KAMP-653 and should be sized into it.

**Trap for whoever implements it: these endpoints answer HTTP 200 on failure.**
Success must be read from the parsed body (`ok: true` and no `error`), never the
status. This is not theoretical — the first round trip here checked the status, took a
200-with-error-body for success, skipped its fallback, and left an album on the
account until it was removed explicitly.

### Artist discography (go)

`https://<artist>.bandcamp.com/music` renders a grid of
`data-item-id="album-<tralbum_id>"` entries (16 on the sampled page). One request per
artist serves the Deep Cut, favourites-band, and New From an Old Friend criteria.

### Not reached in the timebox

- **Label pages / Label-Mates** — `unknown`. The harder half was always resolving our
  free-text `albums.label` strings to a label page, and that is untested. KAMP-647
  should keep this criterion droppable, as its ticket already says.
- The `followed_bands: true` discover parameter and `fan_follows_label` are visible but
  unexercised; they may offer a cheaper "labels you follow" signal than label pages.

### Request-cost table

Counted, not extrapolated. This is the shape KAMP-646 needs — per endpoint class,
because the collection endpoint is the one that rate-limits hardest and per-item pages
are cheap.

| Criterion | Requests | Yields | Endpoint class |
| --- | --- | --- | --- |
| Also-like from a seed album | 1 GET | ~6.9 candidates, ~100% fresh | album page (cheap) |
| Discover by genre/tag | 1 POST (`size` up to 60) | up to 60, pre-filtered by `is_owned` | discover API |
| Best sellers | 1 POST (`slice=top`) | same | discover API |
| Over 10 years old | 1 POST (`slice=rand`, `size=60`) | ~12 of 60 qualify | discover API |
| Local scene | 1 POST (+`geoname_id`) | up to 60 | discover API |
| Artist discography | 1 GET per artist | ~16 | artist page (cheap) |
| Wishlist walk (pre-filter) | ceil(N/100) POSTs | — | fancollection (**expensive**) |
| Per-item wishlist state | 0 extra | — | free on pages already fetched |

**A varied 10-item crate is reachable in roughly 3–6 requests**, none of them against
the collection endpoint. That is an order of magnitude below the 15–25 KAMP-646
assumed. The one expensive call is the wishlist walk; note that
`fan_tralbum_data.is_wishlisted` and the discover API's `is_wishlisted` make it
avoidable for anything already on a fetched page.

Observed latency, for context rather than as a limit: album page ~0.4–0.8s direct and
~1.25s relayed; discover API ~0.3–0.5s on both.

### Where the limit actually bites

Measured last, with the app quit, stopping at the first 429. Direct transport, because
the relay cannot surface response headers at all.

| Endpoint class | Result |
| --- | --- |
| `discover_web` API | **120 requests at ~85/min — no 429** (lower bound, not a ceiling) |
| Album page | **429 after 57 requests in 39s (~87/min)** |

Three things follow, and the first is the reason a single budget number was always the
wrong shape:

1. **The classes have different limits.** The album page — the endpoint behind the
   flagship also-like criterion — is the more constrained of the two, while the
   discover API absorbed twice the volume without complaint. The governor should
   budget per class, not globally.
2. **Bandcamp offered no `Retry-After`** even on the direct transport, so there is no
   server-provided backoff hint to honour. The governor's own 60/120/300 ladder is the
   only signal available, and through the relay not even the 429's headers are visible.
3. **The headroom is enormous.** A crate costs ~1.5 album-page requests; the limit sat
   at 57 in a sustained burst, i.e. roughly 38 crates' worth back to back. Normal use
   will not approach this. The limit is nonetheless real and reachable, which is the
   argument for having a governor at all rather than for tuning it aggressively.

### Other things worth knowing before KAMP-647

- **A stored `album_url` can 404.** One of eight collection seeds returned 404, so the
  crate builder must treat a dead seed as a skip rather than an error.
- **~1% of the collection is on custom domains.** 8 of 829 `album_url` values are not
  under `.bandcamp.com`. `_ALLOWED_PROXY_HOSTS` permits only `bandcamp.com`,
  `f4.bcbits.com` and `t4.bcbits.com`, so recommendations pointing at a Bandcamp Pro
  custom domain will 422 in packaged builds. Skip such candidates, or widen the
  allowlist deliberately as a reviewed change — it is an SSRF boundary.
- **Dev tooling cannot read the shipped app's Bandcamp session.** The packaged app has
  bundle identity and stores it in the Data Protection Keychain; unsigned dev Python
  gets `errSecMissingEntitlement` and falls back to a Login Keychain that has no kamp
  item. Source cookies from the running daemon
  (`GET /api/v1/bandcamp/session-cookies`) instead — which is also exactly where the
  relay gets them.
- **Bandcamp rotates a `session` cookie on ordinary GETs** (observed `Set-Cookie` on
  album pages). Nothing broke, but it is the mechanism behind "a second client can
  disturb the app's session", so keep concurrent clients in mind.
- **Every discovery parser must warn on a zero-result parse**, per the requirement
  above. All of these surfaces are unofficial and will drift.

---

## Fixture contract

Fixtures live under `tests/fixtures/discovery/`, validated by `tests/test_discovery_fixtures.py`.

**This repository is public.** Logged-in Bandcamp `pagedata` carries live `crumbs` CSRF tokens,
`fan_id`, fan/tralbum state, and signed asset URLs. A raw capture committed here would put those in
public git history permanently, where removing them means rewriting a published branch. The scrub is
therefore an asserted invariant, not an act of care: capture fails, and the committed test fails, if
any forbidden pattern survives.

- **Full page, gzipped, is the contract; trimmed extracts are a convenience.** A fixture trimmed to
  just the interesting block cannot catch the failure mode where a regex matches something else on
  the page — which is the failure mode of every parser in `bandcamp.py`, since they regex whole pages.
- **Manifest per fixture:** `captured_at`, source URL, transport, `logged_in`, request headers sent
  (Accept-Language matters — results are geo- and locale-personalised), status, byte length, sha256.
- **Staleness is otherwise undetectable.** Fixtures captured today keep parser tests green while live
  markup drifts underneath them. Mitigated by a one-command recapture and a `@pytest.mark.live` test,
  deselected in CI, that re-fetches and asserts the parsers still find the same structure.
- **Personalisation caveat:** also-like results reflect one fan's taste and one IP's geography.
  Fixtures from this account are not representative of a fresh user's, and findings say so.
