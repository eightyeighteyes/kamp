"""Discovery groundwork tests — schema, accessors, profile, ABC (KAMP-645).

Named to sit alongside tests/test_discovery_fixtures.py (KAMP-644), which guards the
captured Bandcamp fixtures rather than this code.

The bias throughout is toward the cases that actually break: FK interactions with an
account reset, the buffered-vs-shown distinction, boundary conditions on the recency
window, and a degenerate empty library. Happy paths are covered incidentally.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from kamp_core.library import _SCHEMA_VERSION, LibraryIndex
from kamp_daemon.discovery import (
    ALBUM_PAGE,
    DISCOVER_API,
    FANCOLLECTION,
    PREVIEW,
    SAVE_REMOTE,
    Candidate,
    DiscoverySource,
    PreviewStream,
    RequestBudget,
    SeedProfile,
    SimpleBudget,
    UnsupportedCapability,
    build_seed_profile,
    crate_budget,
)


@pytest.fixture
def index(tmp_path: Path) -> LibraryIndex:
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


def _add_collection_row(
    index: LibraryIndex,
    sale_item_id: str,
    *,
    band_name: str = "Band",
    item_title: str = "Album",
    tralbum_id: str = "",
    album_url: str = "",
    keywords: list[str] | None = None,
    added_at: float = 0.0,
) -> None:
    index._conn.execute(
        "INSERT INTO bandcamp_collection"
        " (sale_item_id, band_name, item_title, tralbum_id, album_url, keywords,"
        "  added_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            sale_item_id,
            band_name,
            item_title,
            tralbum_id,
            album_url,
            json.dumps(keywords) if keywords is not None else None,
            added_at,
        ),
    )
    index._conn.commit()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_fresh_db_is_v62_with_discovery_tables(self, tmp_path: Path) -> None:
        LibraryIndex(tmp_path / "library.db").close()
        conn = sqlite3.connect(str(tmp_path / "library.db"))
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
        assert version == 62
        assert {"discovery_items", "discovery_events"} <= tables

    def test_fresh_db_accepts_a_retraction_without_any_migration(
        self, tmp_path: Path
    ) -> None:
        """_DDL carries the widened CHECK, so a new DB never needs the rebuild."""
        index = LibraryIndex(tmp_path / "library.db")
        try:
            item = index.add_discovery_candidate(
                provider="bandcamp", provider_item_id="1"
            )
            index.record_discovery_event(item, "unwishlisted")
        finally:
            index.close()

    def test_v61_db_migrates_to_v62_and_then_accepts_a_retraction(
        self, tmp_path: Path
    ) -> None:
        """The CHECK on discovery_events.kind cannot be ALTERed, so it is rebuilt.

        Before the rebuild an 'unwishlisted' row is an IntegrityError; after it, the
        same insert is accepted and every pre-existing event is still there with its
        id intact.
        """
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "wishlisted")
        event_id = index._conn.execute("SELECT id FROM discovery_events").fetchone()[
            "id"
        ]

        # Recreate the pre-migration CHECK: _DDL already built the widened one, so
        # a synthetic v61 DB has to be rebuilt back down to it or the test proves
        # nothing about the migration.
        index._conn.executescript(
            "ALTER TABLE discovery_events RENAME TO discovery_events_old;"
            "CREATE TABLE discovery_events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " item_id INTEGER NOT NULL REFERENCES discovery_items(id) ON DELETE RESTRICT,"
            " provider TEXT NOT NULL DEFAULT 'bandcamp',"
            " provider_item_id TEXT NOT NULL DEFAULT '',"
            " kind TEXT NOT NULL,"
            " at REAL NOT NULL DEFAULT (unixepoch()),"
            " detail TEXT,"
            " CHECK (kind IN ('shown', 'previewed', 'wishlisted', 'url_copied',"
            "                 'dismissed', 'purchased')));"
            "INSERT INTO discovery_events SELECT * FROM discovery_events_old;"
            "DROP TABLE discovery_events_old;"
        )
        index._conn.execute("UPDATE schema_version SET version = 61")
        index._conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            index._conn.execute(
                "INSERT INTO discovery_events (item_id, kind) VALUES (?, 'unwishlisted')",
                (item,),
            )
        index._conn.rollback()
        index.close()

        reopened = LibraryIndex(db)
        try:
            version = reopened._conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()["version"]
            surviving = reopened._conn.execute(
                "SELECT id, kind FROM discovery_events"
            ).fetchall()
            # The retraction the old CHECK rejected is now accepted.
            reopened.record_discovery_event(item, "unwishlisted")
            state = reopened._conn.execute(
                "SELECT state FROM discovery_items WHERE id = ?", (item,)
            ).fetchone()["state"]
        finally:
            reopened.close()

        assert version == 62
        # id copied verbatim -- nothing downstream keys on an event id today, but a
        # renumbering rebuild would be a silent trap for whatever does first.
        assert [(r["id"], r["kind"]) for r in surviving] == [(event_id, "wishlisted")]
        assert state == "fresh"

    def test_v62_rebuild_keeps_the_indexes(self, tmp_path: Path) -> None:
        """DROP TABLE takes its indexes with it; they must be put back."""
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        index._conn.execute("UPDATE schema_version SET version = 61")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        try:
            names = {
                r["name"]
                for r in reopened._conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='index' AND tbl_name='discovery_events'"
                )
            }
        finally:
            reopened.close()
        assert {"discovery_events_item_idx", "discovery_events_kind_at_idx"} <= names

    def test_v60_db_migrates_forward_preserving_data(self, tmp_path: Path) -> None:
        db = tmp_path / "library.db"
        index = LibraryIndex(db)
        _add_collection_row(index, "sale-1")
        index._conn.execute("UPDATE schema_version SET version = 60")
        index._conn.commit()
        index.close()

        reopened = LibraryIndex(db)
        try:
            version = reopened._conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()["version"]
            surviving = reopened._conn.execute(
                "SELECT COUNT(*) AS c FROM bandcamp_collection"
            ).fetchone()["c"]
        finally:
            reopened.close()
        # Against the constant, not a literal: this test is about a v60 DB reaching
        # the current schema with its rows intact, and pinning the number here means
        # every later migration has to come back and edit it.
        assert version == _SCHEMA_VERSION
        assert surviving == 1

    def test_crate_slot_is_unique_but_buffered_rows_are_exempt(
        self, index: LibraryIndex
    ) -> None:
        """Many candidates may sit unshown; two cannot share one crate slot."""
        for n in range(3):
            index.add_discovery_candidate(
                provider="bandcamp", provider_item_id=f"buffered-{n}"
            )

        first = index.add_discovery_candidate(provider="bandcamp", provider_item_id="a")
        second = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="b"
        )
        index._conn.execute(
            "UPDATE discovery_items SET crate_no = 1, position = 0 WHERE id = ?",
            (first,),
        )
        index._conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            index._conn.execute(
                "UPDATE discovery_items SET crate_no = 1, position = 0 WHERE id = ?",
                (second,),
            )
            index._conn.commit()
        index._conn.rollback()

    def test_crate_no_and_position_must_agree(self, index: LibraryIndex) -> None:
        """A half-promoted row is a bug; the CHECK makes it unrepresentable."""
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="x")
        with pytest.raises(sqlite3.IntegrityError):
            index._conn.execute(
                "UPDATE discovery_items SET crate_no = 1 WHERE id = ?", (item,)
            )
            index._conn.commit()
        index._conn.rollback()

    def test_events_cannot_be_orphaned(self, index: LibraryIndex) -> None:
        """ON DELETE RESTRICT keeps the stats ledger from being silently pruned."""
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="x")
        index.record_discovery_event(item, "purchased")
        with pytest.raises(sqlite3.IntegrityError):
            index._conn.execute("DELETE FROM discovery_items WHERE id = ?", (item,))
            index._conn.commit()
        index._conn.rollback()

    def test_event_free_buffered_rows_can_be_swept(self, index: LibraryIndex) -> None:
        """The other half of RESTRICT: KAMP-657's TTL sweep must still work."""
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="x")
        index._conn.execute("DELETE FROM discovery_items WHERE id = ?", (item,))
        index._conn.commit()
        assert index.discovery_item_id("bandcamp", "x") is None


# ---------------------------------------------------------------------------
# The FK that reintroduces KAMP-528 if it is wrong
# ---------------------------------------------------------------------------


class TestAccountResetWithPurchasedPick:
    def test_clear_bandcamp_collection_survives_a_purchased_discovery_item(
        self, index: LibraryIndex
    ) -> None:
        """An account reset must not crash once a crate pick has been purchased.

        clear_bandcamp_collection() nulls the child FKs it knows about before deleting
        the ledger, because foreign_keys=ON turns a dangling child into an
        IntegrityError -- the crash KAMP-528 fixed. discovery_items is a child it does
        NOT know about, so purchased_sale_item_id is declared ON DELETE SET NULL. If
        that action is ever dropped, this test fails with the original KAMP-528 crash.
        """
        _add_collection_row(index, "sale-1", tralbum_id="777")
        item = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="777"
        )
        index._conn.execute(
            "UPDATE discovery_items SET purchased_sale_item_id = ?, purchased_at = ?"
            " WHERE id = ?",
            ("sale-1", time.time(), item),
        )
        index._conn.commit()
        index.record_discovery_event(item, "purchased")

        index.clear_bandcamp_collection()

        row = index._conn.execute(
            "SELECT purchased_sale_item_id, state FROM discovery_items WHERE id = ?",
            (item,),
        ).fetchone()
        assert row is not None, "the discovery item must survive an account reset"
        assert row["purchased_sale_item_id"] is None
        # The event ledger is the ground truth for attribution and is untouched.
        assert row["state"] == "purchased"
        assert (
            index._conn.execute(
                "SELECT COUNT(*) AS c FROM discovery_events WHERE kind = 'purchased'"
            ).fetchone()["c"]
            == 1
        )


# ---------------------------------------------------------------------------
# seen_before / in_library
# ---------------------------------------------------------------------------


class TestExclusionPredicates:
    def test_buffered_candidate_is_not_seen(self, index: LibraryIndex) -> None:
        """The KAMP-657 invariant: a gathered-but-unshown row stays eligible."""
        index.add_discovery_candidate(provider="bandcamp", provider_item_id="123")
        assert index.seen_before("bandcamp", "123") is False

    def test_shown_candidate_is_seen(self, index: LibraryIndex) -> None:
        item = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="123"
        )
        index._conn.execute(
            "UPDATE discovery_items SET crate_no = 1, position = 0 WHERE id = ?",
            (item,),
        )
        index._conn.commit()
        assert index.seen_before("bandcamp", "123") is True

    def test_seen_before_is_provider_scoped(self, index: LibraryIndex) -> None:
        item = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="123"
        )
        index._conn.execute(
            "UPDATE discovery_items SET crate_no = 1, position = 0 WHERE id = ?",
            (item,),
        )
        index._conn.commit()
        assert index.seen_before("discogs", "123") is False

    def test_in_library_matches_on_tralbum_id(self, index: LibraryIndex) -> None:
        _add_collection_row(index, "sale-1", tralbum_id="4242")
        assert index.in_library("4242") is True
        assert index.in_library("9999") is False

    def test_in_library_falls_back_to_nocase_artist_title(
        self, index: LibraryIndex
    ) -> None:
        """Rows whose tralbum_id was never backfilled still have to match."""
        _add_collection_row(
            index, "sale-1", band_name="Four Tet", item_title="Pink", tralbum_id=""
        )
        assert index.in_library("", artist="four tet", title="PINK") is True
        assert index.in_library("", artist="Four Tet", title="Rounds") is False

    def test_in_library_handles_custom_domain_and_dead_urls(
        self, index: LibraryIndex
    ) -> None:
        """~1% of a real collection is on a Bandcamp Pro custom domain, and a stored
        album_url can 404. Neither is an identity signal, so neither may affect
        matching."""
        _add_collection_row(
            index,
            "sale-1",
            band_name="Artist",
            item_title="Album",
            tralbum_id="555",
            album_url="https://music.example.com/album/x",
        )
        assert index.in_library("555") is True
        assert index.in_library("", artist="Artist", title="Album") is True


# ---------------------------------------------------------------------------
# Event ledger and derived state
# ---------------------------------------------------------------------------


class TestEventLedger:
    def test_state_precedence_is_highest_rank_not_last_write(
        self, index: LibraryIndex
    ) -> None:
        """previewed -> dismissed -> purchased -> a late 'shown' stays 'purchased'.

        Attribution runs on a collection sync, out of order with UI events, so a
        last-event-wins cache would quietly downgrade a purchase.
        """
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        for kind in ("shown", "previewed", "dismissed", "purchased", "shown"):
            index.record_discovery_event(item, kind)
        state = index._conn.execute(
            "SELECT state FROM discovery_items WHERE id = ?", (item,)
        ).fetchone()["state"]
        assert state == "purchased"

    def test_dismissed_does_not_override_wishlisted(self, index: LibraryIndex) -> None:
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "wishlisted")
        index.record_discovery_event(item, "dismissed")
        state = index._conn.execute(
            "SELECT state FROM discovery_items WHERE id = ?", (item,)
        ).fetchone()["state"]
        assert state == "wishlisted"

    def test_every_event_is_recorded_even_when_state_is_unchanged(
        self, index: LibraryIndex
    ) -> None:
        """state is a cache; the ledger is the truth and must not lose rows."""
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        for _ in range(3):
            index.record_discovery_event(item, "url_copied")
        assert (
            index._conn.execute(
                "SELECT COUNT(*) AS c FROM discovery_events"
            ).fetchone()["c"]
            == 3
        )

    def test_events_denormalise_provider_identity(self, index: LibraryIndex) -> None:
        item = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="abc"
        )
        index.record_discovery_event(item, "shown")
        row = index._conn.execute(
            "SELECT provider, provider_item_id FROM discovery_events"
        ).fetchone()
        assert (row["provider"], row["provider_item_id"]) == ("bandcamp", "abc")

    def test_unknown_item_raises(self, index: LibraryIndex) -> None:
        with pytest.raises(ValueError):
            index.record_discovery_event(9999, "shown")

    def _state(self, index: LibraryIndex, item: int) -> str:
        row = index._conn.execute(
            "SELECT state FROM discovery_items WHERE id = ?", (item,)
        ).fetchone()
        return str(row["state"])

    def test_unwishlisting_falls_back_to_what_the_ledger_still_says(
        self, index: LibraryIndex
    ) -> None:
        """Not to 'fresh' — the record was still previewed, and that happened.

        This is the one event that moves state *down*, so it cannot use the
        highest-rank-wins fast path. It recomputes from the ledger instead, which
        is what the schema has always claimed state is.
        """
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "previewed")
        index.record_discovery_event(item, "wishlisted")
        assert self._state(index, item) == "wishlisted"

        index.record_discovery_event(item, "unwishlisted")
        assert self._state(index, item) == "previewed"

    def test_unwishlisting_a_record_never_wishlisted_changes_nothing(
        self, index: LibraryIndex
    ) -> None:
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "previewed")
        index.record_discovery_event(item, "unwishlisted")
        assert self._state(index, item) == "previewed"

    def test_unwishlisting_does_not_undo_a_purchase(self, index: LibraryIndex) -> None:
        """Buying it is a different fact from wishlisting it, and outranks both."""
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "wishlisted")
        index.record_discovery_event(item, "purchased")
        index.record_discovery_event(item, "unwishlisted")
        assert self._state(index, item) == "purchased"

    def test_rewishlisting_after_a_retraction_sticks(self, index: LibraryIndex) -> None:
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "wishlisted")
        index.record_discovery_event(item, "unwishlisted")
        index.record_discovery_event(item, "wishlisted")
        assert self._state(index, item) == "wishlisted"

    def test_retraction_falls_back_past_a_dismissal_to_the_dismissal(
        self, index: LibraryIndex
    ) -> None:
        """Passing on a record outranks previewing it, and the pass still stands."""
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "previewed")
        index.record_discovery_event(item, "dismissed")
        index.record_discovery_event(item, "wishlisted")
        index.record_discovery_event(item, "unwishlisted")
        assert self._state(index, item) == "dismissed"

    def test_a_bare_retraction_on_an_untouched_record_is_fresh(
        self, index: LibraryIndex
    ) -> None:
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "unwishlisted")
        assert self._state(index, item) == "fresh"

    def test_the_retraction_itself_is_recorded(self, index: LibraryIndex) -> None:
        """The ledger is history: an un-wishlist is a thing the user did."""
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "wishlisted")
        index.record_discovery_event(item, "unwishlisted")
        kinds = [
            r["kind"]
            for r in index._conn.execute(
                "SELECT kind FROM discovery_events WHERE item_id = ? ORDER BY id",
                (item,),
            )
        ]
        assert kinds == ["wishlisted", "unwishlisted"]

    def test_retraction_recompute_respects_event_order_not_row_order(
        self, index: LibraryIndex
    ) -> None:
        """Attribution back-dates events, so `at` and insertion order can disagree.

        A wishlist added *after* the retraction in wall-clock terms must survive it,
        even though its row was inserted first.
        """
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        index.record_discovery_event(item, "wishlisted", at=5000.0)
        index.record_discovery_event(item, "unwishlisted", at=1000.0)
        assert self._state(index, item) == "wishlisted"

    def test_unknown_event_kind_raises(self, index: LibraryIndex) -> None:
        item = index.add_discovery_candidate(provider="bandcamp", provider_item_id="1")
        with pytest.raises(ValueError):
            index.record_discovery_event(item, "teleported")

    def test_candidate_insert_is_idempotent(self, index: LibraryIndex) -> None:
        """Re-gathering an album must not duplicate it or resurrect a shown one."""
        first = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="1", artist="A"
        )
        again = index.add_discovery_candidate(
            provider="bandcamp", provider_item_id="1", artist="A"
        )
        assert first == again


# ---------------------------------------------------------------------------
# Crate assembly accessors (KAMP-648)
# ---------------------------------------------------------------------------


class TestCrateAssembly:
    def _buffered(self, index: LibraryIndex, item_id: str, **kw: object) -> int:
        return index.add_discovery_candidate(
            provider="bandcamp", provider_item_id=item_id, **kw  # type: ignore[arg-type]
        )

    def test_first_crate_is_numbered_one(self, index: LibraryIndex) -> None:
        """MAX() over an empty table is NULL, not 0 — COALESCE or crate 'None'."""
        assert index.next_crate_no() == 1
        assert index.latest_crate_no() is None

    def test_crate_numbers_increment(self, index: LibraryIndex) -> None:
        index.place_in_crate(self._buffered(index, "1"), 1, 0)
        assert index.next_crate_no() == 2
        assert index.latest_crate_no() == 1
        index.place_in_crate(self._buffered(index, "2"), 2, 0)
        assert index.next_crate_no() == 3
        assert index.latest_crate_no() == 2

    def test_placing_marks_seen_and_records_shown(self, index: LibraryIndex) -> None:
        """Promotion is what makes a candidate 'seen' — row existence is not."""
        item = self._buffered(index, "abc")
        assert index.seen_before("bandcamp", "abc") is False
        index.place_in_crate(item, 1, 3)

        assert index.seen_before("bandcamp", "abc") is True
        row = index._conn.execute(
            "SELECT crate_no, position, state FROM discovery_items WHERE id = ?",
            (item,),
        ).fetchone()
        assert (row["crate_no"], row["position"]) == (1, 3)
        # 'shown' maps to 'fresh' (rank 0), so the cached state must not move.
        assert row["state"] == "fresh"

        event = index._conn.execute(
            "SELECT kind, provider, provider_item_id FROM discovery_events"
        ).fetchone()
        # Denormalised identity is what KAMP-655's stats read after a row is pruned;
        # a hand-rolled INSERT that skipped it would leave the columns at ''.
        assert (event["kind"], event["provider"], event["provider_item_id"]) == (
            "shown",
            "bandcamp",
            "abc",
        )

    def test_unknown_item_raises(self, index: LibraryIndex) -> None:
        with pytest.raises(ValueError):
            index.place_in_crate(9999, 1, 0)

    def test_duplicate_slot_is_refused(self, index: LibraryIndex) -> None:
        """The partial unique index is all that serialises two builders.

        It fails on the UPDATE, before the event write, so nothing is stranded —
        but the promotion must not stick either.
        """
        index.place_in_crate(self._buffered(index, "1"), 1, 0)
        loser = self._buffered(index, "2")
        with pytest.raises(sqlite3.IntegrityError):
            index.place_in_crate(loser, 1, 0)

        row = index._conn.execute(
            "SELECT crate_no, position FROM discovery_items WHERE id = ?", (loser,)
        ).fetchone()
        assert (row["crate_no"], row["position"]) == (None, None)

    def test_a_failed_event_write_unpromotes_the_item(
        self, index: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The half-written state that actually matters: promoted, never recorded.

        The UPDATE lands first, so if the 'shown' insert then fails without a
        rollback the item is left in the crate with no ledger entry — invisible to
        the digging stats, and permanently suppressed by seen_before(), which keys
        on crate_no. Both writes must share one transaction.
        """
        item = self._buffered(index, "1")

        def _boom(*_a: object, **_kw: object) -> None:
            raise sqlite3.OperationalError("ledger unavailable")

        monkeypatch.setattr(index, "_append_event", _boom)
        with pytest.raises(sqlite3.OperationalError):
            index.place_in_crate(item, 1, 0)

        row = index._conn.execute(
            "SELECT crate_no, position FROM discovery_items WHERE id = ?", (item,)
        ).fetchone()
        assert (row["crate_no"], row["position"]) == (None, None)
        assert index.seen_before("bandcamp", "1") is False

    def test_crate_items_are_ordered_by_position(self, index: LibraryIndex) -> None:
        for position, item_id in ((2, "c"), (0, "a"), (1, "b")):
            index.place_in_crate(
                self._buffered(index, item_id, title=item_id.upper()), 7, position
            )
        titles = [row["title"] for row in index.crate_items(7)]
        assert titles == ["A", "B", "C"]

    def test_crate_items_carry_the_card_fields(self, index: LibraryIndex) -> None:
        """The snapshot must be renderable without a second lookup per card."""
        item = self._buffered(
            index,
            "42",
            item_url="https://band.bandcamp.com/album/x",
            artist="Band",
            title="X",
            art_url="https://f4.bcbits.com/img/a1_10.jpg",
            criterion="also_like",
            why="Filed next to something you played.",
            seed_json='{"kind": "album"}',
        )
        index.place_in_crate(item, 1, 0)
        row = index.crate_items(1)[0]
        assert row["id"] == item
        assert row["artist"] == "Band"
        assert row["criterion"] == "also_like"
        assert row["why"].startswith("Filed next to")
        assert row["state"] == "fresh"
        assert row["seed"] == {"kind": "album"}

    def test_crate_items_survive_unparseable_seed_json(
        self, index: LibraryIndex
    ) -> None:
        """A bad seed blob must cost provenance, not the whole crate snapshot."""
        item = self._buffered(index, "1")
        index._conn.execute(
            "UPDATE discovery_items SET seed_json = ? WHERE id = ?", ("{oops", item)
        )
        index._conn.commit()
        index.place_in_crate(item, 1, 0)
        assert index.crate_items(1)[0]["seed"] == {}

    def test_empty_crate_reads_as_empty(self, index: LibraryIndex) -> None:
        assert index.crate_items(99) == []

    def test_single_item_lookup_carries_the_art_fields(
        self, index: LibraryIndex
    ) -> None:
        """The art proxy resolves one item by id without loading a whole crate."""
        item = self._buffered(
            index,
            "42",
            artist="Band",
            title="X",
            art_url="https://f4.bcbits.com/img/a1_0.jpg",
        )
        row = index.discovery_item(item)
        assert row is not None
        assert row["provider"] == "bandcamp"
        assert row["provider_item_id"] == "42"
        assert row["art_url"] == "https://f4.bcbits.com/img/a1_0.jpg"

    def test_single_item_lookup_works_before_promotion(
        self, index: LibraryIndex
    ) -> None:
        """Buffered rows (crate_no NULL) must resolve too — KAMP-657 will show
        art for candidates that have never been in a crate."""
        item = self._buffered(index, "1", art_url="https://f4.bcbits.com/img/a2_0.jpg")
        assert index.discovery_item(item) is not None

    def test_unknown_item_lookup_is_none(self, index: LibraryIndex) -> None:
        assert index.discovery_item(9999) is None


# ---------------------------------------------------------------------------
# Seed profile
# ---------------------------------------------------------------------------


class TestSeedProfile:
    def _album(
        self,
        index: LibraryIndex,
        artist: str,
        title: str,
        *,
        last_played_at: float | None = None,
        favorite: int = 0,
        label: str = "",
        album_url: str | None = None,
        sale_item_id: str | None = None,
    ) -> int:
        """Insert an album, optionally linked to a collection row.

        The link matters: seed accessors only return albums with a fetchable
        Bandcamp URL, so an album created without one is deliberately invisible to
        them (a local-only album has no page to read recommendations from).
        """
        if album_url is not None:
            sale_item_id = sale_item_id or f"sale-{artist}-{title}"
            _add_collection_row(
                index,
                sale_item_id,
                band_name=artist,
                item_title=title,
                album_url=album_url,
            )
        cur = index._conn.execute(
            "INSERT INTO albums"
            " (album_artist, album, last_played_at, favorite, label, sale_item_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (artist, title, last_played_at, favorite, label, sale_item_id),
        )
        index._conn.commit()
        return int(cur.lastrowid or 0)

    def test_recency_window_boundaries(self, index: LibraryIndex) -> None:
        """29 days ago is recent; 31 is not. The boundary is the whole point."""
        now = time.time()
        url = "https://a.bandcamp.com/album/x"
        self._album(
            index, "Fresh", "In", last_played_at=now - 29 * 86400, album_url=url
        )
        self._album(
            index, "Stale", "Out", last_played_at=now - 31 * 86400, album_url=url
        )
        self._album(index, "Never", "Played", last_played_at=None, album_url=url)

        recent = index.recently_played_albums(days=30)
        assert {s.album for s in recent} == {"In"}

    def test_seeds_without_a_fetchable_url_are_excluded(
        self, index: LibraryIndex
    ) -> None:
        """A local-only album has no Bandcamp page, so it cannot seed a criterion.

        Returning it would hand fetchers a seed they can do nothing with, which
        surfaces later as an unexplained empty criterion rather than an absent one.
        """
        now = time.time()
        self._album(index, "Local", "Only", last_played_at=now - 3600)
        self._album(
            index,
            "Streamed",
            "Album",
            last_played_at=now - 3600,
            album_url="https://b.bandcamp.com/album/y",
        )
        assert {s.album for s in index.recently_played_albums()} == {"Album"}

    def test_taste_genres_unions_keywords_regardless_of_tagging_toggle(
        self, index: LibraryIndex
    ) -> None:
        """Bandcamp keywords are a taste signal even when the tagging option is off.

        They are only promoted into track_genres when tagging.bandcamp_genres is on
        AND only on an album's first index, so gating this read on that config would
        make a user's taste invisible because of an unrelated tagging preference.
        """
        index.set_setting("tagging.bandcamp_genres", "false")
        _add_collection_row(
            index, "sale-1", tralbum_id="1", keywords=["dub techno", "ambient"]
        )
        _add_collection_row(index, "sale-2", tralbum_id="2", keywords=["dub techno"])

        genres = dict(index.taste_genres())
        assert genres.get("dub techno") == 2
        assert genres.get("ambient") == 1

    def test_taste_genres_survives_malformed_keyword_blobs(
        self, index: LibraryIndex
    ) -> None:
        """The column is free-form TEXT; a bad row must not poison the profile."""
        _add_collection_row(index, "sale-1", tralbum_id="1", keywords=["techno"])
        index._conn.execute(
            "UPDATE bandcamp_collection SET keywords = ? WHERE sale_item_id = 'sale-1'",
            ("not json at all",),
        )
        _add_collection_row(index, "sale-2", tralbum_id="2", keywords=["techno"])
        index._conn.commit()
        assert dict(index.taste_genres()).get("techno") == 1

    def test_labels_group_case_insensitively(self, index: LibraryIndex) -> None:
        """albums.label is unnormalised free text: Ghostly and ghostly are one label."""
        self._album(index, "A", "1", label="Ghostly")
        self._album(index, "B", "2", label="ghostly")
        self._album(index, "C", "3", label="Kranky")

        labels = dict(index.taste_labels(min_albums=2))
        assert len(labels) == 1
        assert next(iter(labels)).casefold() == "ghostly"
        assert next(iter(labels.values())) == 2

    def test_labels_respect_the_minimum(self, index: LibraryIndex) -> None:
        self._album(index, "A", "1", label="Solo")
        assert index.taste_labels(min_albums=2) == []

    def test_profile_on_an_empty_library_is_thin_not_broken(
        self, index: LibraryIndex
    ) -> None:
        """The most likely first-run state, and the most likely to divide by zero."""
        profile = build_seed_profile(index)
        assert profile.is_thin is True
        assert profile.recent_album_ids == set()
        assert profile.top_genres == []
        assert profile.purchase_dates == {}

    def test_profile_collects_signals(self, index: LibraryIndex) -> None:
        now = time.time()
        recent_id = self._album(
            index,
            "Recent",
            "Album",
            last_played_at=now - 3600,
            album_url="https://recent.bandcamp.com/album/a",
        )
        fav_id = self._album(
            index,
            "Fav",
            "Album",
            favorite=1,
            album_url="https://fav.bandcamp.com/album/b",
        )
        _add_collection_row(
            index, "sale-1", tralbum_id="900", keywords=["shoegaze"], added_at=now
        )

        profile = build_seed_profile(index)
        assert recent_id in profile.recent_album_ids
        assert fav_id in profile.favorite_album_ids
        assert profile.has_genre("Shoegaze") is True
        assert profile.purchase_dates["900"] == pytest.approx(now)
        assert profile.is_thin is False

    def test_profile_seeds_carry_fetchable_urls(self, index: LibraryIndex) -> None:
        """The whole point of the seed types: a criterion needs an address."""
        self._album(
            index,
            "Artist",
            "Album",
            last_played_at=time.time(),
            album_url="https://artist.bandcamp.com/album/thing",
        )
        seed = build_seed_profile(index).recent_albums[0]
        assert seed.album_url == "https://artist.bandcamp.com/album/thing"
        assert seed.album_artist == "Artist"

    def test_favorite_artists_derive_a_page_from_an_owned_album(
        self, index: LibraryIndex
    ) -> None:
        """bandcamp_collection stores no band URL, so the artist page comes from
        the subdomain of an album we own."""
        self._album(
            index,
            "Four Tet",
            "Pink",
            favorite=1,
            album_url="https://fourtet.bandcamp.com/album/pink",
        )
        artists = index.favorite_artists_with_pages()
        assert len(artists) == 1
        assert artists[0].artist_page == "https://fourtet.bandcamp.com/music"

    def test_custom_domain_artists_are_skipped(self, index: LibraryIndex) -> None:
        """Bandcamp Pro custom domains yield no subdomain to build from, and are
        outside the Electron relay's allowlist, so a packaged build could not fetch
        them even if we guessed a URL."""
        self._album(
            index,
            "Indie",
            "Record",
            favorite=1,
            album_url="https://music.example.com/album/record",
        )
        assert index.favorite_artists_with_pages() == []

    def test_membership_helpers_are_case_insensitive(self) -> None:
        """KAMP-657 re-validates seeds through these, so casing must not matter."""
        profile = SeedProfile(
            top_genres=["Dub Techno"], top_artists=["Four Tet"], labels=["Ghostly"]
        )
        assert profile.has_genre("dub techno") is True
        assert profile.has_artist("FOUR TET") is True
        assert profile.has_label("ghostly") is True
        assert profile.has_genre("noise") is False


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------


class _FakeSource(DiscoverySource):
    """A minimal source, standing in for what KAMP-647 will build."""

    provider_id = "fake"

    def __init__(self, *, can_save: bool = False) -> None:
        self._can_save = can_save
        self.gathered_with: RequestBudget | None = None

    @property
    def capabilities(self) -> frozenset[str]:
        # Mirrors the real shape: a capability that depends on runtime conditions
        # rather than being fixed at class-definition time.
        caps = {PREVIEW}
        if self._can_save:
            caps.add(SAVE_REMOTE)
        return frozenset(caps)

    def gather(self, profile: SeedProfile, budget: RequestBudget) -> list[Candidate]:
        self.gathered_with = budget
        if not budget.allow(ALBUM_PAGE):
            return []
        budget.consume(ALBUM_PAGE)
        return [
            Candidate(
                provider="fake",
                provider_item_id="1",
                item_url="https://example.com/a",
                criterion="also_like",
                why="because you played X",
                seed={"album_id": 1},
            )
        ]

    def resolve_preview(self, candidate: Candidate) -> PreviewStream | None:
        return PreviewStream(url="https://example.com/a.mp3")


class TestDiscoverySource:
    def test_capabilities_are_evaluated_at_call_time(self) -> None:
        """Not a class constant: Bandcamp's save_remote depends on the transport, and
        a stale constant would render a wishlist button that silently no-ops."""
        source = _FakeSource(can_save=False)
        assert SAVE_REMOTE not in source.capabilities
        source._can_save = True
        assert SAVE_REMOTE in source.capabilities

    def test_save_remote_without_the_capability_raises(self) -> None:
        """Fails loudly rather than looking like a rejection by the remote service."""
        source = _FakeSource(can_save=False)
        candidate = Candidate(
            provider="fake", provider_item_id="1", item_url="https://example.com/a"
        )
        with pytest.raises(UnsupportedCapability):
            source.save_remote(candidate)

    def test_a_bare_source_offers_nothing_and_does_nothing(self) -> None:
        """Capabilities are opt-in. A source that declares none must not be assumed
        to preview or save just because the methods exist on the ABC."""

        class _Bare(DiscoverySource):
            provider_id = "np"

            def gather(
                self, profile: SeedProfile, budget: RequestBudget
            ) -> list[Candidate]:
                return []

        source = _Bare()
        candidate = Candidate(provider="np", provider_item_id="1", item_url="x")
        assert source.capabilities == frozenset()
        with pytest.raises(UnsupportedCapability):
            source.resolve_preview(candidate)
        with pytest.raises(UnsupportedCapability):
            source.save_remote(candidate)

    def test_gather_respects_the_budget(self) -> None:
        source = _FakeSource()
        budget = SimpleBudget(limits={ALBUM_PAGE: 1})
        assert len(source.gather(SeedProfile(), budget)) == 1
        assert source.gather(SeedProfile(), budget) == []

    def test_candidate_serialises_its_seed_deterministically(self) -> None:
        candidate = Candidate(
            provider="fake",
            provider_item_id="1",
            item_url="x",
            seed={"b": 2, "a": 1},
        )
        assert candidate.seed_json() == '{"a": 1, "b": 2}'


class TestCrateBudget:
    def test_caps_carry_margin_over_the_measured_crate_cost(self) -> None:
        """KAMP-644 measured a varied crate at 3-6 requests; these are ~2x that."""
        budget = crate_budget()
        assert budget.limits[ALBUM_PAGE] >= 6
        assert budget.limits[DISCOVER_API] >= 4

    def test_collection_endpoint_is_a_tripwire_not_a_limit(self) -> None:
        """Crate building must never walk the collection endpoint — it is both the
        most expensive call and the one that rate-limits hardest. A zero cap makes
        an accidental walk fail loudly here instead of quietly earning a 429."""
        assert crate_budget().allow(FANCOLLECTION) is False

    def test_unknown_endpoint_classes_are_denied(self) -> None:
        """A new fetcher must declare its class deliberately rather than inheriting
        a permissive default."""
        assert crate_budget().allow("some_new_surface") is False

    def test_budget_is_fresh_per_crate(self) -> None:
        first = crate_budget()
        first.consume(ALBUM_PAGE, 8)
        assert first.allow(ALBUM_PAGE) is False
        assert crate_budget().allow(ALBUM_PAGE) is True


class TestSimpleBudget:
    def test_classes_are_counted_separately(self) -> None:
        """Album pages and the discover API have different real-world limits, so a
        single global counter would be wrong by construction."""
        budget = SimpleBudget(limits={ALBUM_PAGE: 1, DISCOVER_API: 3})
        budget.consume(ALBUM_PAGE)
        assert budget.allow(ALBUM_PAGE) is False
        assert budget.allow(DISCOVER_API) is True

    def test_unlisted_classes_use_the_default(self) -> None:
        budget = SimpleBudget(default_limit=2)
        budget.consume("something_new", 2)
        assert budget.allow("something_new") is False
