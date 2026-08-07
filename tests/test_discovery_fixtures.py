"""Integrity and privacy guards for the discovery fixtures (KAMP-644).

The fixtures under ``tests/fixtures/discovery/`` are captured from live Bandcamp
pages by ``scripts/spike_discovery_recon.py`` and exist so KAMP-647 can build
parsers against real markup without hitting the network.  Two things can go
quietly wrong with captured data, and this module guards both:

1. **Privacy.**  This repository is public.  Bandcamp album pages publicly list
   the fans who collected an album, so a capture embeds real people's fan ids and
   usernames — including the capturing account's own — and a logged-in capture
   would additionally carry live CSRF crumbs.  Captures are redacted before being
   written; these tests make that permanent rather than a one-time act of care.

   The assertions are deliberately **pattern-based**.  Hard-coding the account's
   username in order to assert its absence would publish the very thing we are
   protecting.

2. **Rot.**  A committed fixture keeps parser tests green while the live markup
   drifts underneath them.  Checksums catch silent edits, and the structural
   marker assertions mean a fixture that no longer contains what the findings
   claim fails loudly instead of quietly parsing to nothing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "discovery"
MANIFEST = FIXTURE_DIR / "manifest.json"

# A live crumb is shaped |action|epoch|hmac= — the bare word "crumb" is fine and
# appears on every page, including anonymous ones (which ship data-crumbs="{}").
CRUMB_TOKEN = re.compile(r"\|[a-z_/0-9]{3,}\|\d{9,}\|[A-Za-z0-9+/=]{8,}")

# Fan identifiers in raw or HTML-escaped JSON. Redaction rewrites the values to
# 0 / "x", so anything else surviving is unredacted personal data.
FAN_KEYS = ("fan_id", "fan_username", "fan_name")
REDACTED_VALUES = {"0", "x", '"x"', "&quot;x&quot;"}


def _manifest() -> dict[str, dict[str, object]]:
    if not MANIFEST.exists():
        pytest.skip("no discovery fixtures captured yet")
    data: dict[str, dict[str, object]] = json.loads(MANIFEST.read_text())
    return data


def _fixture_path(name: str) -> Path:
    for suffix in (".html.gz", ".json.gz"):
        candidate = FIXTURE_DIR / f"{name}{suffix}"
        if candidate.exists():
            return candidate
    raise AssertionError(f"manifest lists {name!r} but no fixture file exists")


def _read(name: str) -> str:
    return gzip.decompress(_fixture_path(name).read_bytes()).decode()


def _fixture_names() -> list[str]:
    if not MANIFEST.exists():
        return []
    return sorted(json.loads(MANIFEST.read_text()))


FIXTURE_NAMES = _fixture_names()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_decompresses_and_matches_checksum(name: str) -> None:
    """A fixture that has been edited by hand is no longer the thing we captured."""
    entry = _manifest()[name]
    raw = gzip.decompress(_fixture_path(name).read_bytes())
    assert len(raw) == entry["bytes"], f"{name}: byte length drifted from manifest"
    assert hashlib.sha256(raw).hexdigest() == entry["sha256"], (
        f"{name}: checksum mismatch — the fixture was modified after capture. "
        "Re-capture with scripts/spike_discovery_recon.py rather than editing."
    )


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_carries_no_live_crumb(name: str) -> None:
    """Live CSRF tokens must never reach a public repository."""
    assert not CRUMB_TOKEN.search(_read(name)), f"{name}: contains a live crumb token"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_carries_no_unredacted_fan_identifiers(name: str) -> None:
    """Album pages list their collectors; those are real people, redacted or absent."""
    text = _read(name)
    for key in FAN_KEYS:
        for match in re.finditer(
            rf'(?:&quot;|"){key}(?:&quot;|"):\s*(?:&quot;|")?([^,&"}}]*)', text
        ):
            value = match.group(1).strip()
            assert (
                value in REDACTED_VALUES or value == ""
            ), f"{name}: unredacted {key} — captures must be redacted before commit"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_was_captured_anonymously(name: str) -> None:
    """Anonymous captures are clean by construction rather than by remembering."""
    assert _manifest()[name]["logged_in"] is False


def test_album_page_fixture_still_contains_a_recommendation_block() -> None:
    """The structural claim KAMP-647's also-like parser is built on.

    Without this, a fixture could rot into markup with no recommendations at all
    and every parser test would still pass by finding nothing.
    """
    if "album_page_with_recs" not in FIXTURE_NAMES:
        pytest.skip("album fixture not captured")
    html = _read("album_page_with_recs")
    assert 'class="recommendations-container"' in html
    items = re.findall(r'<li class="recommended-album', html)
    assert len(items) >= 3, f"expected several recommendations, found {len(items)}"
    # Identity is what the v61 schema keys on, so assert it is actually present.
    assert re.search(r'data-albumid="\d+"', html), "no tralbum id on any recommendation"


def test_discover_api_fixture_shape() -> None:
    """The discover results contract: item_id identity plus a release date."""
    if "discover_web_ambient_top" not in FIXTURE_NAMES:
        pytest.skip("discover fixture not captured")
    payload = json.loads(_read("discover_web_ambient_top"))
    results = payload["results"]
    assert results, "discover fixture has no results"
    for row in results[:5]:
        assert row["item_id"], "discover result missing item_id"
        assert row["item_url"], "discover result missing item_url"
        assert "release_date" in row


def test_discover_root_fixture_carries_the_facet_vocabulary() -> None:
    """Genres/locations/times/slices are read from this blob, not hard-coded."""
    if "discover_root" not in FIXTURE_NAMES:
        pytest.skip("discover root fixture not captured")
    html = _read("discover_root")
    match = re.search(r'id="DiscoverApp"[^>]*data-blob="([^"]+)"', html)
    assert match, "DiscoverApp blob missing — the discover page structure changed"
    import html as html_lib

    blob = json.loads(html_lib.unescape(match.group(1)))
    state = blob["appData"]["initialState"]
    for facet in ("genres", "subgenres", "locations", "times", "slices"):
        assert state[facet], f"facet vocabulary {facet!r} is empty"
    # The time facet is a recency window, not a release-year filter. If this ever
    # grows year-like entries, the "over 10 years old" criterion can be simplified
    # (see docs/discovery-recon.md).
    assert {t["slug"] for t in state["times"]} <= {
        "fresh",
        "today",
        "this-week",
        "1w",
        "2w",
        "3w",
        "4w",
        "5w",
        "6w",
    }
