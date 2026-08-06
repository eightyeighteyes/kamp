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

_Filled in as each item is investigated._

### A — identity fields

_pending_

### B — discover/tag surface

_pending_

### C — also-like block and yield

_pending_

### Opportunistic items

_pending_

### Request-cost table

_pending_

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
