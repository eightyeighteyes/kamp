"""The persisted Bandcamp wishlist table (KAMP-666).

KAMP-652 walked the wishlist into an in-memory set of album ids with an hour
TTL, so every restart cost a cold nine-page walk — ~37s on a real account —
during which the first crate had no wishlist filtering at all.

Two behaviours carry most of the risk and are tested hardest here:

* **Reads are album-only.** A wishlist holds individual tracks too, and the
  candidate side keys on ids that `normalise_item_id` has stripped to a bare
  numeral. A track id in the exclusion set would shadow an unrelated album.
* **A partial walk must delete nothing.** Pruning is keyed on the walk start
  time, and a row the user wishlisted mid-walk has a NULL `walked_at` that must
  survive rather than read as infinitely stale.
"""

from __future__ import annotations

from pathlib import Path

from kamp_core.library import LibraryIndex


def _seed(
    index: LibraryIndex,
    tralbum_id: str,
    *,
    tralbum_type: str = "a",
    walked_at: float | None = 100.0,
    title: str = "An Album",
) -> None:
    index.upsert_wishlist_items(
        [
            {
                "tralbum_id": tralbum_id,
                "tralbum_type": tralbum_type,
                "band_name": "A Band",
                "item_title": title,
                "item_url": f"https://b.bandcamp.com/album/{tralbum_id}",
                "item_art_id": "42",
                "band_id": "7",
                "added": "01 Jan 2025 00:00:00 GMT",
            }
        ],
        walked_at=walked_at,
    )


def test_upsert_then_read_back_album_ids(tmp_path: Path) -> None:
    index = LibraryIndex(tmp_path / "library.db")
    _seed(index, "111")
    _seed(index, "222")
    assert index.wishlist_album_ids() == {"111", "222"}
    index.close()


def test_upsert_writes_a_whole_page_at_once(tmp_path: Path) -> None:
    index = LibraryIndex(tmp_path / "library.db")
    written = index.upsert_wishlist_items(
        [{"tralbum_id": str(n)} for n in range(5)], walked_at=100.0
    )
    assert written == 5
    assert index.wishlist_album_ids() == {"0", "1", "2", "3", "4"}
    index.close()


def test_upsert_drops_rows_with_no_id_and_tolerates_an_empty_page(
    tmp_path: Path,
) -> None:
    """An id-less row would be an exclusion entry that excludes nothing."""
    index = LibraryIndex(tmp_path / "library.db")
    assert index.upsert_wishlist_items([]) == 0
    assert index.upsert_wishlist_items([{"tralbum_id": ""}, {"band_name": "x"}]) == 0
    assert index.wishlist_album_ids() == set()
    index.close()


def test_upsert_defaults_missing_metadata_rather_than_raising(tmp_path: Path) -> None:
    """A sparse row still carries its id, which is the part exclusion needs."""
    index = LibraryIndex(tmp_path / "library.db")
    index.upsert_wishlist_items([{"tralbum_id": "111"}], walked_at=100.0)
    row = index._conn.execute("SELECT * FROM bandcamp_wishlist").fetchone()
    assert row["tralbum_type"] == "a"
    assert row["band_name"] == ""
    assert row["item_title"] == ""
    index.close()


def test_a_wishlisted_track_never_shadows_an_album(tmp_path: Path) -> None:
    """Tracks are stored but excluded from the exclusion set.

    The candidate side compares against ids stripped of their album-/track-
    prefix, so a track id leaking into this set would suppress an unrelated
    album that happens to share the number. Storing both and filtering on read
    is what keeps them distinct.
    """
    index = LibraryIndex(tmp_path / "library.db")
    _seed(index, "555", tralbum_type="a", title="The Album")
    _seed(index, "555", tralbum_type="t", title="A Standalone Track")

    rows = index._conn.execute("SELECT COUNT(*) FROM bandcamp_wishlist").fetchone()[0]
    assert rows == 2  # distinct rows, not one overwriting the other

    # Only the album is offered to the filter.
    assert index.wishlist_album_ids() == {"555"}
    index.close()


def test_upsert_is_idempotent_on_the_composite_key(tmp_path: Path) -> None:
    index = LibraryIndex(tmp_path / "library.db")
    _seed(index, "111", title="First")
    _seed(index, "111", title="Second")
    row = index._conn.execute(
        "SELECT item_title, COUNT(*) AS n FROM bandcamp_wishlist"
    ).fetchone()
    assert row["n"] == 1
    assert row["item_title"] == "Second"
    index.close()


class TestPrune:
    def test_prune_removes_rows_an_older_walk_did_not_see(self, tmp_path: Path) -> None:
        """An album un-wishlisted upstream disappears on the next walk."""
        index = LibraryIndex(tmp_path / "library.db")
        _seed(index, "stale", walked_at=100.0)
        _seed(index, "fresh", walked_at=200.0)

        removed = index.prune_wishlist(before=150.0)

        assert removed == 1
        assert index.wishlist_album_ids() == {"fresh"}
        index.close()

    def test_prune_keeps_a_row_the_user_added_mid_walk(self, tmp_path: Path) -> None:
        """NULL walked_at means "no walk has seen this", not "infinitely old".

        This is why walked_at is nullable rather than NOT NULL DEFAULT 0 like
        bandcamp_collection.added_at — a 0 default would read as stale and the
        prune would silently delete what the user just wishlisted.
        """
        index = LibraryIndex(tmp_path / "library.db")
        _seed(index, "fresh", walked_at=200.0)
        _seed(index, "just-added", walked_at=None)

        removed = index.prune_wishlist(before=150.0)

        assert removed == 0
        assert index.wishlist_album_ids() == {"fresh", "just-added"}
        index.close()

    def test_prune_also_removes_stale_tracks(self, tmp_path: Path) -> None:
        """Tracks are invisible to reads but still have to be pruned."""
        index = LibraryIndex(tmp_path / "library.db")
        _seed(index, "999", tralbum_type="t", walked_at=100.0)

        assert index.prune_wishlist(before=150.0) == 1
        assert (
            index._conn.execute("SELECT COUNT(*) FROM bandcamp_wishlist").fetchone()[0]
            == 0
        )
        index.close()


class TestSingleWrites:
    def test_add_then_remove_one_item(self, tmp_path: Path) -> None:
        """kamp's own wishlist writes land immediately (KAMP-653).

        Without the removal half, an album the user just un-wishlisted stays
        excluded from every crate until the next walk, with nothing to explain
        why.
        """
        index = LibraryIndex(tmp_path / "library.db")
        index.add_wishlist_item("777")
        assert index.wishlist_album_ids() == {"777"}

        index.remove_wishlist_item("777")
        assert index.wishlist_album_ids() == set()
        index.close()

    def test_add_leaves_walked_at_null(self, tmp_path: Path) -> None:
        """So an in-flight walk's prune cannot delete it."""
        index = LibraryIndex(tmp_path / "library.db")
        index.add_wishlist_item("777")
        walked_at = index._conn.execute(
            "SELECT walked_at FROM bandcamp_wishlist"
        ).fetchone()["walked_at"]
        assert walked_at is None
        index.close()

    def test_add_does_not_clobber_a_walked_row(self, tmp_path: Path) -> None:
        """Re-adding something the walk already knows keeps its metadata."""
        index = LibraryIndex(tmp_path / "library.db")
        _seed(index, "777", title="Real Title", walked_at=100.0)
        index.add_wishlist_item("777")
        row = index._conn.execute("SELECT item_title FROM bandcamp_wishlist").fetchone()
        assert row["item_title"] == "Real Title"
        index.close()


class TestWalkTimestamp:
    """Freshness is a settings row, not MAX(walked_at) over the table.

    MAX cannot tell an empty wishlist from a never-walked one, and the decision
    to re-walk is built on exactly that distinction.
    """

    def test_never_walked_reads_as_none(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        assert index.wishlist_walked_at() is None
        index.close()

    def test_a_walk_of_an_empty_wishlist_is_still_a_walk(self, tmp_path: Path) -> None:
        """The case MAX(walked_at) gets wrong: no rows, but walked."""
        index = LibraryIndex(tmp_path / "library.db")
        index.set_wishlist_walked_at(1000.0)
        assert index.wishlist_walked_at() == 1000.0
        assert index.wishlist_album_ids() == set()
        index.close()

    def test_a_corrupt_value_reads_as_never_walked_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """settings.value is TEXT NOT NULL. A hand-edited or half-written value
        must not take the daemon down at startup; the worst it may cost is one
        extra walk."""
        index = LibraryIndex(tmp_path / "library.db")
        index.set_setting("bandcamp.wishlist_walked_at", "not-a-number")
        assert index.wishlist_walked_at() is None
        index.close()


class TestClear:
    """Disconnect must not leave one account's wishlist visible to the next."""

    def test_clear_empties_the_table(self, tmp_path: Path) -> None:
        index = LibraryIndex(tmp_path / "library.db")
        _seed(index, "111")
        _seed(index, "222", tralbum_type="t")

        index.clear_wishlist()

        assert (
            index._conn.execute("SELECT COUNT(*) FROM bandcamp_wishlist").fetchone()[0]
            == 0
        )
        index.close()

    def test_clear_also_drops_the_walk_timestamp(self, tmp_path: Path) -> None:
        """Rows and freshness are one piece of state, so one method owns both.

        Clearing only the rows leaves the next account reading "walked
        recently", skipping its walk, and filtering on an empty table for the
        rest of the TTL.
        """
        index = LibraryIndex(tmp_path / "library.db")
        _seed(index, "111")
        index.set_wishlist_walked_at(1000.0)

        index.clear_wishlist()

        assert index.wishlist_walked_at() is None
        index.close()
