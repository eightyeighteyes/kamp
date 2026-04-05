"""Tests for KampContext structured data types."""

from __future__ import annotations

import pickle

from kamp_daemon.ext import ArtworkQuery, ArtworkResult, TrackMetadata

# ---------------------------------------------------------------------------
# AC #1 / AC #2 — types exist with correct primitive fields
# ---------------------------------------------------------------------------


def test_track_metadata_fields() -> None:
    t = TrackMetadata(
        title="Tha Carter III",
        artist="Lil Wayne",
        album="Tha Carter III",
        album_artist="Lil Wayne",
        year="2008",
        track_number=1,
        mbid="abc-123",
    )
    assert t.title == "Tha Carter III"
    assert t.artist == "Lil Wayne"
    assert t.album == "Tha Carter III"
    assert t.album_artist == "Lil Wayne"
    assert t.year == "2008"
    assert t.track_number == 1
    assert t.mbid == "abc-123"


def test_artwork_query_fields() -> None:
    q = ArtworkQuery(
        mbid="rel-456",
        release_group_mbid="rg-789",
        album="Madvillainy",
        artist="Madvillain",
    )
    assert q.mbid == "rel-456"
    assert q.release_group_mbid == "rg-789"
    assert q.album == "Madvillainy"
    assert q.artist == "Madvillain"


def test_artwork_result_fields() -> None:
    r = ArtworkResult(image_bytes=b"\xff\xd8\xff", mime_type="image/jpeg")
    assert r.image_bytes == b"\xff\xd8\xff"
    assert r.mime_type == "image/jpeg"


# ---------------------------------------------------------------------------
# AC #4 — picklable across subprocess IPC boundary
# ---------------------------------------------------------------------------


def test_track_metadata_pickle_roundtrip() -> None:
    original = TrackMetadata(
        title="Alright",
        artist="Kendrick Lamar",
        album="To Pimp a Butterfly",
        album_artist="Kendrick Lamar",
        year="2015",
        track_number=7,
        mbid="deadbeef",
    )
    assert pickle.loads(pickle.dumps(original)) == original


def test_artwork_query_pickle_roundtrip() -> None:
    original = ArtworkQuery(
        mbid="m1",
        release_group_mbid="rg1",
        album="Illmatic",
        artist="Nas",
    )
    assert pickle.loads(pickle.dumps(original)) == original


def test_artwork_result_pickle_roundtrip() -> None:
    original = ArtworkResult(image_bytes=b"\x89PNG", mime_type="image/png")
    assert pickle.loads(pickle.dumps(original)) == original
