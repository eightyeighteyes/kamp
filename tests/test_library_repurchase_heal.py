"""The v62 -> v63 heal for releases bought twice (KAMP-682).

Two `bandcamp_collection` rows can share one `tralbum_id` — the stable release
identity — while carrying different `sale_item_id`s. Track identity keyed on
`sale_item_id`, so a second purchase ingested a whole second track set and the
album showed double its tracks.

The heal collapses only the provable case: one sale item's tracks carry file
sources and the other's are stream-only. Everything ambiguous is left alone,
because guessing which copy the user wants is worse than leaving a visible
duplicate they can report.

State is seeded with direct SQL rather than through the upsert helpers so each
test says exactly what pre-migration shape it is exercising.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kamp_core.library import LibraryIndex

TRALBUM = "3498893942"


def _seed_release(
    index: LibraryIndex,
    sale_item_id: str,
    *,
    kind: str,
    tralbum_id: str = TRALBUM,
    n_tracks: int = 2,
    play_count: int = 0,
) -> list[int]:
    """Add a collection row plus *n_tracks* tracks, each with one source."""
    c = index._conn
    c.execute(
        "INSERT INTO bandcamp_collection (sale_item_id, band_name, item_title,"
        " tralbum_id, mode) VALUES (?, 'Band', 'Album', ?, 'remote')",
        (sale_item_id, tralbum_id),
    )
    ids: list[int] = []
    for n in range(1, n_tracks + 1):
        cur = c.execute(
            "INSERT INTO tracks (title, artist, album_artist, album, release_date,"
            " track_number, disc_number, mb_release_id, mb_recording_id)"
            " VALUES (?, 'Band', 'Band', 'Album', '', ?, 1, '', '')",
            (f"Song {n}", n),
        )
        tid = int(cur.lastrowid or 0)
        ids.append(tid)
        uri = (
            f"/music/{sale_item_id}/{n}.mp3"
            if kind == "file"
            else f"bandcamp://{sale_item_id}/{n}"
        )
        c.execute(
            "INSERT INTO track_sources (track_id, kind, provider, provider_item_id,"
            " uri, ext) VALUES (?, ?, 'bandcamp', ?, ?, 'mp3')",
            (tid, kind, sale_item_id, uri),
        )
        if play_count:
            c.execute(
                "INSERT INTO track_stats (track_id, favorite, play_count, updated_at)"
                " VALUES (?, 0, ?, 0)",
                (tid, play_count),
            )
    c.commit()
    return ids


def _downgrade(index: LibraryIndex, version: int = 62) -> None:
    index._conn.execute("UPDATE schema_version SET version = ?", (version,))
    index._conn.commit()


def _n_tracks(index: LibraryIndex) -> int:
    return int(index._conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])


def test_fresh_db_is_current_and_heals_nothing(tmp_path: Path) -> None:
    """A fresh database lands on v63 with no heal to run."""
    index = LibraryIndex(tmp_path / "library.db")
    version = index._conn.execute("SELECT version FROM schema_version").fetchone()[
        "version"
    ]
    index.close()
    assert version == 63


def test_heal_collapses_a_repurchased_release(tmp_path: Path) -> None:
    """The stream-only twin merges into the file-bearing set."""
    db = tmp_path / "library.db"
    index = LibraryIndex(db)
    kept = _seed_release(index, "389633897", kind="file")
    _seed_release(index, "394186224", kind="stream")
    assert _n_tracks(index) == 4
    _downgrade(index)
    index.close()

    reopened = LibraryIndex(db)
    try:
        version = reopened._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()["version"]
        surviving = [
            int(r["id"]) for r in reopened._conn.execute("SELECT id FROM tracks")
        ]
        files = reopened._conn.execute(
            "SELECT COUNT(*) FROM track_sources WHERE kind = 'file'"
        ).fetchone()[0]
    finally:
        reopened.close()

    assert version == 63
    # The downloaded copy is the one that survives, by id.
    assert surviving == kept
    assert files == 2


def test_heal_preserves_the_higher_play_count(tmp_path: Path) -> None:
    """Stats are MAX-merged onto the survivor, not discarded.

    A delete-based heal would throw away the listening history attached to the
    copy being removed. This is the reason the heal merges.
    """
    db = tmp_path / "library.db"
    index = LibraryIndex(db)
    _seed_release(index, "389633897", kind="file", n_tracks=1, play_count=1)
    _seed_release(index, "394186224", kind="stream", n_tracks=1, play_count=7)
    _downgrade(index)
    index.close()

    reopened = LibraryIndex(db)
    try:
        plays = reopened._conn.execute("SELECT play_count FROM track_stats").fetchall()
    finally:
        reopened.close()

    assert [int(r["play_count"]) for r in plays] == [7]


@pytest.mark.parametrize("kind", ["stream", "file"])
def test_heal_quarantines_ambiguous_pairs(tmp_path: Path, kind: str) -> None:
    """Two sets of the same kind cannot be told apart, so nothing is touched.

    Both stream-only or both file-bearing means there is no evidence for which
    copy the user actually wants. Leaving a visible duplicate is recoverable;
    merging the wrong way is not.
    """
    db = tmp_path / "library.db"
    index = LibraryIndex(db)
    _seed_release(index, "111", kind=kind)
    _seed_release(index, "222", kind=kind)
    _downgrade(index)
    index.close()

    reopened = LibraryIndex(db)
    try:
        n = _n_tracks(reopened)
    finally:
        reopened.close()

    assert n == 4


@pytest.mark.parametrize("bogus", ["", "None", "0"])
def test_heal_ignores_unusable_tralbum_ids(tmp_path: Path, bogus: str) -> None:
    """A malformed release id must never group unrelated albums.

    Two of 837 real collection rows carry a blank tralbum_id. Grouping on one
    would merge genuinely different albums — the worst outcome available here.
    """
    db = tmp_path / "library.db"
    index = LibraryIndex(db)
    _seed_release(index, "111", kind="file", tralbum_id=bogus)
    _seed_release(index, "222", kind="stream", tralbum_id=bogus)
    _downgrade(index)
    index.close()

    reopened = LibraryIndex(db)
    try:
        n = _n_tracks(reopened)
    finally:
        reopened.close()

    assert n == 4


def test_heal_skips_tracks_with_no_positional_counterpart(tmp_path: Path) -> None:
    """Only tracks that correspond one-to-one are merged.

    A loser track with no matching disc/track number on the survivor is not
    provably the same recording, so it is left where it is rather than folded
    into an arbitrary neighbour.
    """
    db = tmp_path / "library.db"
    index = LibraryIndex(db)
    _seed_release(index, "389633897", kind="file", n_tracks=1)
    _seed_release(index, "394186224", kind="stream", n_tracks=3)
    _downgrade(index)
    index.close()

    reopened = LibraryIndex(db)
    try:
        n = _n_tracks(reopened)
    finally:
        reopened.close()

    # Track 1 merged; tracks 2 and 3 had no counterpart and survive.
    assert n == 3
