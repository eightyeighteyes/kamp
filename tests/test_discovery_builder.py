"""Crate builder tests (KAMP-648).

Everything here runs against a fake DiscoverySource — the builder's job is
selection, exclusion and persistence, and none of that should need a network or
even a Bandcamp-shaped candidate. The bias is toward the outcomes that are
visibly wrong to a user: a crate that collapses to one item, a crate that
recommends records they already own, a crate that repeats last week's, and a
rate limit that presents as a hang.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import pytest

from kamp_core.library import LibraryIndex
from kamp_daemon.discovery import (
    ALBUM_PAGE,
    ARTIST_PAGE,
    DISCOVER_API,
    Candidate,
    DiscoverySource,
    RequestBudget,
    SeedProfile,
)
from kamp_daemon.discovery_builder import CRATE_SIZE, build_crate


@pytest.fixture
def index(tmp_path: Path) -> Iterator[LibraryIndex]:
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


def _candidate(item_id: str, criterion: str = "also_like", **kw: Any) -> Candidate:
    return Candidate(
        provider="fake",
        provider_item_id=item_id,
        item_url=f"https://band.bandcamp.com/album/{item_id}",
        artist=kw.pop("artist", f"Artist {item_id}"),
        title=kw.pop("title", f"Title {item_id}"),
        criterion=criterion,
        why=f"because {criterion}",
        # Overridable, because KAMP-665 made the seed something the builder reads
        # rather than provenance it only stores.
        seed=kw.pop("seed", {"kind": criterion}),
        **kw,
    )


def _spread(counts: dict[str, int]) -> list[Candidate]:
    """Candidates grouped by criterion, ids unique across the whole spread."""
    out: list[Candidate] = []
    n = 0
    for criterion, count in counts.items():
        for _ in range(count):
            n += 1
            out.append(_candidate(str(n), criterion))
    return out


class _FakeSource(DiscoverySource):
    provider_id = "fake"

    def __init__(
        self,
        candidates: list[Candidate],
        caps: dict[str, int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._candidates = candidates
        self._caps = caps or {}
        self._error = error
        self.gather_calls = 0

    @property
    def criterion_caps(self) -> dict[str, int]:
        return self._caps

    def gather(
        self,
        profile: SeedProfile,
        budget: RequestBudget,
        state: Any = None,
    ) -> list[Candidate]:
        self.gather_calls += 1
        self.last_state = state
        if self._error is not None:
            raise self._error
        return list(self._candidates)


class _PaginatingSource(DiscoverySource):
    """A source with a deep well, reachable only by carrying the cursor.

    Stands in for the discover API: it holds far more candidates than one crate
    needs and hands out the next page each time, remembering where it got to in
    the *state* the builder passes back. A source that ignored the state would
    return page one forever, which is the bug KAMP-661 is about.
    """

    provider_id = "fake"

    def __init__(self, pool: int = 200, page: int = 20) -> None:
        self._pool = pool
        self._page = page
        self.states: list[dict[str, Any]] = []

    def gather(
        self,
        profile: SeedProfile,
        budget: RequestBudget,
        state: Any = None,
    ) -> list[Candidate]:
        state = {} if state is None else state
        self.states.append(dict(state))
        offset = int(state.get("offset", 0))
        # Its own id namespace. _spread() numbers from 1, so a bare str(n) here
        # would collide with candidates another fake source had already put in a
        # crate — and `seen_before` would then reject them for the right reason
        # under a test asserting the wrong thing.
        rows = [
            _candidate(f"page-{n}", "genre_top")
            for n in range(offset, min(offset + self._page, self._pool))
        ]
        state["offset"] = offset + len(rows)
        return rows


class _FakeGovernor:
    """Only the surface the builder uses."""

    def __init__(self, blocked: float = 0.0) -> None:
        self.blocked = blocked

    def blocked_for(self, endpoint_class: str) -> float:
        return self.blocked


class _Publisher:
    """Stands in for the API layer's publish(), recording every push."""

    def __init__(self) -> None:
        #: Accumulated status after each push — what a client would see.
        self.pushes: list[dict[str, Any]] = []
        #: Exactly the fields each call changed.
        self.raw: list[dict[str, Any]] = []
        self.status: dict[str, Any] = {}

    def __call__(self, fields: dict[str, Any]) -> None:
        self.raw.append(dict(fields))
        self.status.update(fields)
        self.pushes.append(dict(self.status))


def _build(
    index: LibraryIndex,
    source: DiscoverySource,
    *,
    governor: Any = None,
    publish: Any = None,
    **kw: Any,
) -> dict[str, Any]:
    return build_crate(
        index,
        source,
        governor=governor or _FakeGovernor(),
        publish=publish or _Publisher(),
        **kw,
    )


def _titles(index: LibraryIndex, crate_no: int) -> list[str]:
    return [row["title"] for row in index.crate_items(crate_no)]


def _criteria(index: LibraryIndex, crate_no: int) -> list[str]:
    return [row["criterion"] for row in index.crate_items(crate_no)]


# ---------------------------------------------------------------------------
# Selection and variety
# ---------------------------------------------------------------------------


class TestSelection:
    def test_builds_a_full_crate_of_ten(self, index: LibraryIndex) -> None:
        source = _FakeSource(_spread({"a": 8, "b": 8, "c": 8}))
        status = _build(index, source)
        assert status["state"] == "ready"
        assert status["crate_no"] == 1
        assert len(index.crate_items(1)) == CRATE_SIZE

    def test_positions_are_dense_and_ordered(self, index: LibraryIndex) -> None:
        _build(index, _FakeSource(_spread({"a": 8, "b": 8})))
        positions = [row["position"] for row in index.crate_items(1)]
        assert positions == list(range(CRATE_SIZE))

    def test_crate_spans_several_criteria(self, index: LibraryIndex) -> None:
        """Round-robin, not first-come: one prolific criterion must not fill it."""
        source = _FakeSource(_spread({"a": 30, "b": 4, "c": 4}))
        _build(index, source)
        used = _criteria(index, 1)
        assert set(used) == {"a", "b", "c"}
        # 'a' has enough to fill the crate on its own; round-robin holds it to
        # roughly a third.
        assert used.count("a") <= 4

    def test_a_criterion_may_repeat(self, index: LibraryIndex) -> None:
        """Variety means spanning several, not one card each."""
        _build(index, _FakeSource(_spread({"a": 8, "b": 8})))
        used = _criteria(index, 1)
        assert used.count("a") > 1 and used.count("b") > 1


class TestSeedCaps:
    """KAMP-665: a crate can have too much of one SEED, not just one criterion.

    Measured on a real crate: three of its ten records came off a single album
    page, so three clerk lines all said "filed next to DOGGOD". The builder had
    no way to notice, because Candidate.seed was provenance it never read.
    """

    def test_no_more_than_two_records_share_a_seed(self, index: LibraryIndex) -> None:
        """A SEED-RICH profile, which is the condition the cap needs to hold.

        Six seeds at two apiece covers a crate of ten with room over. Give it
        three and the cap is arithmetically unsatisfiable — 3 x 2 = 6 — and the
        backfill correctly overruns it rather than shipping a six-record crate.
        That case has its own test below; this one is about the cap doing its job
        when it can.
        """
        candidates = []
        for n in range(3):
            candidates += [
                _candidate(
                    f"a{n}{i}", "also_like", seed={"kind": "album", "album_id": n}
                )
                for i in range(4)
            ]
        for genre in ("rock", "metal"):
            candidates += [
                _candidate(
                    f"g{genre}{i}", "genre_top", seed={"kind": "genre", "genre": genre}
                )
                for i in range(4)
            ]
        candidates += [
            _candidate(
                f"x{i}", "favorite_artist", seed={"kind": "artist", "artist": "X"}
            )
            for i in range(4)
        ]

        _build(index, _FakeSource(candidates))
        items = index.crate_items(1)
        assert len(items) == CRATE_SIZE, "the cap must not have cost records"
        seeds = [json.dumps(row["seed"], sort_keys=True) for row in items]
        for seed, count in Counter(seeds).items():
            assert count <= 2, f"{count} records share one seed: {seed}"

    def test_a_criterion_spends_its_slots_on_different_seeds(
        self, index: LibraryIndex
    ) -> None:
        """The bug the seed cap alone did not catch, found in crate 24.

        The round-robin gives five criteria two slots each over a crate of ten,
        so a cap of two per seed never binds — a criterion only ever had two
        cards to place. It then took both off the FIRST seed in its group,
        because groups are built in gather order and a seed's candidates are
        contiguous. Fetching a second album page changed nothing about what
        landed in the crate.

        So the choice inside a group has to prefer the seed used least so far.
        Costs no requests: it is the same candidates, picked in a better order.

        FIVE criteria over a crate of ten is the real shape — that is what makes
        two slots each, and two slots is where a cap of two stops binding. With
        fewer criteria each gets more slots and the cap catches it, which is why
        this has to mirror the live registry rather than a convenient pair.
        """
        # Two seeds each, exactly as _SEEDS_PER_CRITERION gathers.
        candidates: list[Candidate] = []
        for criterion, dimension in (
            ("also_like", "album_id"),
            ("genre_top", "genre"),
            ("older_than_ten", "genre"),
            ("favorite_artist", "artist"),
            ("best_seller", None),
        ):
            for s in range(2):
                for i in range(4):
                    seed = (
                        {"kind": criterion, dimension: f"{criterion}-{s}"}
                        if dimension
                        else {"kind": "chart"}
                    )
                    candidates.append(
                        _candidate(f"{criterion}{s}{i}", criterion, seed=seed)
                    )

        _build(index, _FakeSource(candidates))
        rows = index.crate_items(1)
        assert len(rows) == CRATE_SIZE

        # Every criterion that had two seeds available should have used both.
        for criterion in (
            "also_like",
            "genre_top",
            "older_than_ten",
            "favorite_artist",
        ):
            seeds = {
                json.dumps(r["seed"], sort_keys=True)
                for r in rows
                if r["criterion"] == criterion
            }
            picked = [r for r in rows if r["criterion"] == criterion]
            if len(picked) > 1:
                assert (
                    len(seeds) > 1
                ), f"{criterion} spent {len(picked)} slots on one seed: {seeds}"

    def test_the_cap_is_overrun_rather_than_shipping_a_short_crate(
        self, index: LibraryIndex
    ) -> None:
        """Three seeds cannot cover ten records at two apiece.

        The cap is a preference, exactly as the criterion caps are (KAMP-648): a
        crate that leans on one album page is a better answer than a crate with
        four empty slots.
        """
        candidates = []
        for n in range(3):
            candidates += [
                _candidate(
                    f"a{n}{i}", "also_like", seed={"kind": "album", "album_id": n}
                )
                for i in range(8)
            ]
        status = _build(index, _FakeSource(candidates))
        assert len(index.crate_items(1)) == CRATE_SIZE
        assert status["short"] is False

    def test_a_seed_cap_never_shrinks_the_crate(self, index: LibraryIndex) -> None:
        """The same rule the criterion cap follows (KAMP-648).

        A thin profile can yield a single seed's worth of candidates and nothing
        else; honouring the cap unconditionally would hand that user a two-record
        crate, which is a worse answer than a crate that leans on one album page.
        """
        source = _FakeSource(
            [
                _candidate(str(n), "best_seller", seed={"kind": "chart"})
                for n in range(20)
            ]
        )
        status = _build(index, source)
        assert len(index.crate_items(1)) == CRATE_SIZE
        assert status["short"] is False

    def test_candidates_without_a_seed_dimension_are_not_all_one_seed(
        self, index: LibraryIndex
    ) -> None:
        """The chart has no dimension, and must not therefore be capped to two.

        Treating "no dimension" as a single shared key would collapse every
        chart pick into one bucket and cap the criterion at two by accident.
        """
        source = _FakeSource(
            [
                _candidate(str(n), "best_seller", seed={"kind": "chart"})
                for n in range(6)
            ]
            + [
                _candidate(f"x{n}", "also_like", seed={"kind": "album", "album_id": n})
                for n in range(6)
            ]
        )
        _build(index, source)
        assert len(index.crate_items(1)) == CRATE_SIZE


class TestCriterionCaps:
    def test_chart_pick_is_capped_when_other_criteria_exist(
        self, index: LibraryIndex
    ) -> None:
        source = _FakeSource(
            _spread({"also_like": 8, "genre_top": 8, "best_seller": 8}),
            caps={"best_seller": 1},
        )
        _build(index, source)
        assert _criteria(index, 1).count("best_seller") == 1

    def test_a_cap_never_shrinks_the_crate(self, index: LibraryIndex) -> None:
        """The thin-profile case: a brand-new library yields only chart picks.

        criteria_for() returns just best_seller for an empty library, so a cap
        honoured unconditionally would hand a first-run user a one-item crate.
        The cap is a preference, and backfilling past it is what keeps the crate
        full.
        """
        source = _FakeSource(_spread({"best_seller": 20}), caps={"best_seller": 1})
        status = _build(index, source)
        assert len(index.crate_items(1)) == CRATE_SIZE
        assert status["short"] is False

    def test_capped_candidates_are_used_last(self, index: LibraryIndex) -> None:
        """Backfill must exhaust the uncapped criteria before overrunning a cap."""
        source = _FakeSource(
            _spread({"also_like": 4, "best_seller": 20}), caps={"best_seller": 1}
        )
        _build(index, source)
        used = _criteria(index, 1)
        assert used.count("also_like") == 4
        assert used.count("best_seller") == 6


class TestOrdering:
    def test_group_order_varies_between_crates(self, tmp_path: Path) -> None:
        """Without a shuffle, slot 0 is the same criterion in every crate forever.

        criteria_for() preserves REGISTRY order and gather() iterates it, so the
        group sequence is otherwise fixed for the life of the install.
        """
        firsts = set()
        for seed in range(12):
            idx = LibraryIndex(tmp_path / f"crate-{seed}.db")
            try:
                _build(
                    idx,
                    _FakeSource(_spread({"a": 8, "b": 8, "c": 8})),
                    rng=random.Random(seed),
                )
                firsts.add(_criteria(idx, 1)[0])
            finally:
                idx.close()
        assert len(firsts) > 1

    def test_ordering_is_deterministic_for_a_given_seed(self, tmp_path: Path) -> None:
        def run(path: Path) -> list[str]:
            idx = LibraryIndex(path)
            try:
                _build(
                    idx,
                    _FakeSource(_spread({"a": 8, "b": 8, "c": 8})),
                    rng=random.Random(7),
                )
                return _criteria(idx, 1)
            finally:
                idx.close()

        assert run(tmp_path / "one.db") == run(tmp_path / "two.db")


# ---------------------------------------------------------------------------
# Exclusions — a miss here is a brand bug, not a cosmetic one
# ---------------------------------------------------------------------------


class TestExclusions:
    def _own(
        self, index: LibraryIndex, tralbum_id: str, artist: str, title: str
    ) -> None:
        index._conn.execute(
            "INSERT INTO bandcamp_collection"
            " (sale_item_id, band_name, item_title, tralbum_id, added_at)"
            " VALUES (?, ?, ?, ?, 0)",
            (f"sale-{tralbum_id}", artist, title, tralbum_id),
        )
        index._conn.commit()

    def test_owned_by_tralbum_id_is_excluded(self, index: LibraryIndex) -> None:
        self._own(index, "1", "Artist 1", "Title 1")
        _build(index, _FakeSource(_spread({"a": 12})))
        assert "Title 1" not in _titles(index, 1)

    def test_owned_by_artist_title_fallback_is_excluded(
        self, index: LibraryIndex
    ) -> None:
        """Collection rows whose tralbum_id was never backfilled still count."""
        self._own(index, "", "artist 2", "title 2")  # NOCASE match
        _build(index, _FakeSource(_spread({"a": 12})))
        assert "Title 2" not in _titles(index, 1)

    def test_wishlisted_is_excluded(self, index: LibraryIndex) -> None:
        _build(index, _FakeSource(_spread({"a": 12})), wishlist_ids={"3"})
        assert "Title 3" not in _titles(index, 1)

    def test_a_previous_crate_is_never_repeated(self, index: LibraryIndex) -> None:
        """The seen-ledger is cross-crate; the same source must yield new items."""
        candidates = _spread({"a": 25})
        _build(index, _FakeSource(candidates))
        _build(index, _FakeSource(candidates))
        first, second = index.crate_items(1), index.crate_items(2)
        assert len(second) == CRATE_SIZE
        assert not {r["provider_item_id"] for r in first} & {
            r["provider_item_id"] for r in second
        }

    def test_buffered_rows_stay_eligible(self, index: LibraryIndex) -> None:
        """seen_before keys on crate_no, not row existence (KAMP-645/657)."""
        index.add_discovery_candidate(provider="fake", provider_item_id="1")
        _build(index, _FakeSource(_spread({"a": 12})))
        assert "1" in {r["provider_item_id"] for r in index.crate_items(1)}


# ---------------------------------------------------------------------------
# Degradation — short crates and rate limits are normal, not errors
# ---------------------------------------------------------------------------


class TestVarietyAcrossSessions:
    """KAMP-661 — the reachable pool has to grow with use, not stay fixed."""

    def test_ten_consecutive_crates_are_full_and_share_no_records(
        self, index: LibraryIndex
    ) -> None:
        """The acceptance criterion the whole story is built around.

        Before this, roughly five crates exhausted the reachable candidates and
        every crate after that was silently short — the pool was one page of one
        query per criterion, re-fetched forever, against a `seen_before` that
        (correctly) excludes anything already shown.
        """
        source = _PaginatingSource()
        seen: set[str] = set()
        for crate_no in range(1, 11):
            status = _build(index, source)
            assert status["state"] == "ready", f"crate {crate_no} did not build"
            assert status["short"] is False, f"crate {crate_no} came up short"
            titles = _titles(index, crate_no)
            assert len(titles) == 10
            assert not (seen & set(titles)), f"crate {crate_no} repeated a record"
            seen.update(titles)
        assert len(seen) == 100

    def test_the_rotation_state_survives_between_builds(
        self, index: LibraryIndex
    ) -> None:
        """Persisted in one settings row, so it outlives the daemon.

        Held in memory it would reset on every restart, and the pool would look
        exhausted again after each launch — the same bug on a longer fuse.
        """
        source = _PaginatingSource()
        _build(index, source)
        _build(index, source)
        assert source.states[0] == {}, "the first build starts with a blank slate"
        assert source.states[1].get("offset") == 20, "state did not come back"

        # Round-tripped through the settings row rather than the source object.
        stored = json.loads(index.get_setting("discovery.rotation") or "{}")
        assert stored["offset"] == 40

    def test_a_source_that_never_ran_leaves_no_setting(
        self, index: LibraryIndex
    ) -> None:
        """A cooldown refuses before the gather, so there is nothing to remember."""
        _build(index, _FakeSource(_spread({"a": 12})), governor=_FakeGovernor(42.0))
        assert index.get_setting("discovery.rotation") is None

    def test_corrupt_rotation_state_costs_variety_not_the_crate(
        self, index: LibraryIndex
    ) -> None:
        """It is a hint, and a hint that fails to parse is worth exactly one crate
        of variety — never an exception on the build thread."""
        index.set_setting("discovery.rotation", "{not json")
        status = _build(index, _PaginatingSource())
        assert status["state"] == "ready"

    def test_a_rotation_state_of_the_wrong_type_is_discarded(
        self, index: LibraryIndex
    ) -> None:
        """Valid JSON, wrong shape — a list where a dict belongs."""
        index.set_setting("discovery.rotation", "[1, 2, 3]")
        assert _build(index, _PaginatingSource())["state"] == "ready"

    def test_a_failed_state_write_does_not_lose_the_crate(
        self, index: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The crate is the product; the rotation state is a cache of where to
        resume. Letting a failed cache write take a built crate with it would be
        exactly the wrong trade."""
        monkeypatch.setattr(
            index,
            "set_setting",
            lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk")),
        )
        status = _build(index, _PaginatingSource())
        assert status["state"] == "ready"
        assert len(index.crate_items(1)) == 10


class TestTheWellRunningDry:
    def test_a_crate_short_because_everything_was_seen_says_so(
        self, index: LibraryIndex
    ) -> None:
        """Distinct from a budget stop: the user is owed the difference.

        A half-empty crate with no explanation reads as "discovery is broken";
        "we have shown you everything in these racks lately" is a true sentence
        about a working feature.
        """
        source = _FakeSource(_spread({"a": 12}))
        _build(index, source)  # first crate takes ten of the twelve
        status = _build(index, source)  # only two are left, and both are new
        assert status["short"] is True
        assert status["exhausted"] is True

    def test_a_full_crate_is_never_exhausted(self, index: LibraryIndex) -> None:
        status = _build(index, _PaginatingSource())
        assert status["short"] is False
        assert status["exhausted"] is False

    def test_a_thin_gather_is_short_but_not_exhausted(
        self, index: LibraryIndex
    ) -> None:
        """Nothing was rejected for having been seen — the well is not dry, the
        source simply found little. Saying "you have seen everything" here would
        be the clerk inventing a reason."""
        status = _build(index, _FakeSource(_spread({"a": 3})))
        assert status["short"] is True
        assert status["exhausted"] is False

    def test_exhausted_does_not_stick_to_the_next_crate(
        self, index: LibraryIndex
    ) -> None:
        """_publish MERGES, so a flag left unset carries over from the last build.

        Same trap `short` already has to work around: every terminal path must
        state the flag rather than leaving it.
        """
        source = _FakeSource(_spread({"a": 12}))
        _build(index, source)
        assert _build(index, source)["exhausted"] is True
        assert _build(index, _PaginatingSource())["exhausted"] is False


class TestDegradation:
    def test_short_crate_persists_what_was_found(self, index: LibraryIndex) -> None:
        status = _build(index, _FakeSource(_spread({"a": 3})))
        assert status["state"] == "ready"
        assert status["short"] is True
        assert len(index.crate_items(1)) == 3

    def test_an_empty_gather_is_not_a_crate(self, index: LibraryIndex) -> None:
        """No crate number is burned when there is nothing to put in it."""
        status = _build(index, _FakeSource([]))
        assert status["state"] == "empty"
        assert index.latest_crate_no() is None
        assert index.next_crate_no() == 1

    def test_cooldown_refuses_to_build_at_all(self, index: LibraryIndex) -> None:
        """A pause that is not visible is a hang (KAMP-639).

        wait_turn() blocks silently for up to 300s after a 429, and the *next*
        build never sees a 429 of its own — it just sits there. So the cooldown
        has to be read before gathering and surfaced instead.
        """
        source = _FakeSource(_spread({"a": 12}))
        publisher = _Publisher()
        status = _build(
            index,
            source,
            governor=_FakeGovernor(blocked=42.0),
            publish=publisher,
            now=lambda: 1000.0,
        )
        assert source.gather_calls == 0
        assert status["state"] == "paused"
        assert status["paused_until"] == pytest.approx(1042.0)
        assert index.latest_crate_no() is None

    def test_paused_until_is_the_longest_cooldown(self, index: LibraryIndex) -> None:
        """Discovery spans three endpoint classes with independent cooldowns."""

        class _Uneven:
            def blocked_for(self, endpoint_class: str) -> float:
                return {ALBUM_PAGE: 5.0, DISCOVER_API: 90.0, ARTIST_PAGE: 0.0}[
                    endpoint_class
                ]

        status = _build(index, _FakeSource([]), governor=_Uneven(), now=lambda: 0.0)
        assert status["paused_until"] == pytest.approx(90.0)

    def test_a_failing_gather_reports_error_without_crashing(
        self, index: LibraryIndex
    ) -> None:
        status = _build(index, _FakeSource([], error=RuntimeError("boom")))
        assert status["state"] == "error"
        assert index.latest_crate_no() is None

    def test_a_row_that_will_not_persist_leaves_no_gap(
        self, index: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One bad row costs a card, not the crate — and not a blank slot.

        The rail renders by position, so skipping a position would leave a
        sleeve the user can focus and never fill.
        """
        real = index.place_in_crate
        calls = {"n": 0}

        def _flaky(item_id: int, crate_no: int, position: int) -> None:
            calls["n"] += 1
            if calls["n"] == 3:
                raise sqlite3.OperationalError("disk full")
            real(item_id, crate_no, position)

        monkeypatch.setattr(index, "place_in_crate", _flaky)
        status = _build(index, _FakeSource(_spread({"a": 12})))

        positions = [row["position"] for row in index.crate_items(1)]
        assert positions == list(range(CRATE_SIZE - 1))
        # short must reflect what landed, not what was picked.
        assert status["short"] is True

    def test_a_crate_that_persists_nothing_is_empty_not_ready(
        self, index: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail(*_a: object, **_kw: object) -> None:
            raise sqlite3.OperationalError("disk full")

        monkeypatch.setattr(index, "place_in_crate", _fail)
        status = _build(index, _FakeSource(_spread({"a": 12})))
        assert status["state"] == "empty"
        assert index.latest_crate_no() is None


# ---------------------------------------------------------------------------
# Status publication
# ---------------------------------------------------------------------------


class TestPublication:
    def test_building_is_announced_before_the_slow_part(
        self, index: LibraryIndex
    ) -> None:
        publisher = _Publisher()
        _build(index, _FakeSource(_spread({"a": 12})), publish=publisher)
        assert publisher.pushes[0]["state"] == "building"
        assert publisher.pushes[-1]["state"] == "ready"

    def test_each_item_is_published_as_it_lands(self, index: LibraryIndex) -> None:
        publisher = _Publisher()
        _build(index, _FakeSource(_spread({"a": 12})), publish=publisher)
        filled = [f["filled"] for f in publisher.raw if "filled" in f]
        # 0 on the opening 'building' push, then one per row as it is committed.
        assert filled == list(range(CRATE_SIZE + 1))

    def test_a_thin_profile_is_announced(self, index: LibraryIndex) -> None:
        """A brand-new library gets a real crate, but an un-personalised one — the
        UI says so rather than letting chart picks imply a taste read."""
        publisher = _Publisher()
        _build(
            index,
            _FakeSource(_spread({"best_seller": 12})),
            publish=publisher,
            profile=SeedProfile(),
        )
        assert publisher.pushes[0]["thin"] is True

    def test_a_real_profile_is_not_thin(self, index: LibraryIndex) -> None:
        publisher = _Publisher()
        _build(
            index,
            _FakeSource(_spread({"a": 12})),
            publish=publisher,
            profile=SeedProfile(top_genres=["dub techno"]),
        )
        assert publisher.pushes[0]["thin"] is False

    def test_crate_size_matches_the_api_layer(self) -> None:
        """CRATE_SIZE is spelled twice — kamp_core cannot import kamp_daemon — and
        a drift would make the API call a full crate short."""
        from kamp_core.discovery_api import CRATE_SIZE as API_CRATE_SIZE

        assert API_CRATE_SIZE == CRATE_SIZE

    def test_hints_carry_the_profile_genres(self, index: LibraryIndex) -> None:
        """KAMP-650 rotates status lines naming what is being dug through.

        The genres come from the local profile rather than the provider, so the
        copy stays true without teaching the builder any provider's criteria.
        """
        publisher = _Publisher()
        _build(
            index,
            _FakeSource(_spread({"a": 12})),
            publish=publisher,
            profile=SeedProfile(top_genres=["dub techno", "ambient"]),
        )
        assert publisher.pushes[0]["hints"] == ["dub techno", "ambient"]

    def test_the_process_governor_is_used_by_default(
        self, index: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without this the builder would run unspaced against the endpoint class
        that actually 429s, and never see the download drain's reported limits."""
        import kamp_daemon.bandcamp_ratelimit as ratelimit

        monkeypatch.setattr(ratelimit, "get_governor", lambda: _FakeGovernor(30.0))
        status = build_crate(
            index,
            _FakeSource(_spread({"a": 12})),
            publish=_Publisher(),
            now=lambda: 0.0,
        )
        assert status["state"] == "paused"
        assert status["paused_until"] == pytest.approx(30.0)

    def test_paused_until_clears_on_a_clean_build(self, index: LibraryIndex) -> None:
        publisher = _Publisher()
        publisher.status["paused_until"] = 999.0
        _build(index, _FakeSource(_spread({"a": 12})), publish=publisher)
        assert publisher.status["paused_until"] == 0.0
