"""Unit tests for kamp_core.criteria.build_query."""

from __future__ import annotations

import pytest

from kamp_core.criteria import build_query
from kamp_core.library import Condition, Group, MagicCriteria

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _criteria(*groups: Group, match: str = "all") -> MagicCriteria:
    return MagicCriteria(groups=list(groups), match=match)


def _group(*conditions: Condition, match: str = "all", negate: bool = False) -> Group:
    return Group(conditions=list(conditions), match=match, negate=negate)


def _cond(field: str, op: str, value: str) -> Condition:
    return Condition(field=field, op=op, value=value)


# ---------------------------------------------------------------------------
# Empty criteria / empty group
# ---------------------------------------------------------------------------


def test_empty_criteria_returns_nothing() -> None:
    frag, params, join = build_query(MagicCriteria(groups=[], match="all"))
    assert frag == "0"
    assert params == []
    assert join is False


def test_empty_group_returns_nothing() -> None:
    frag, params, join = build_query(_criteria(_group()))
    assert frag == "0"
    assert params == []
    assert join is False


# ---------------------------------------------------------------------------
# Bool coercion
# ---------------------------------------------------------------------------


def test_track_favorite_true() -> None:
    frag, params, join = build_query(
        _criteria(_group(_cond("track.favorite", "is", "true")))
    )
    assert "tracks.favorite" in frag
    assert params == [1]
    assert join is False


def test_track_favorite_false() -> None:
    frag, params, join = build_query(
        _criteria(_group(_cond("track.favorite", "is", "false")))
    )
    assert params == [0]


def test_album_favorite_sets_join_flag() -> None:
    frag, params, join = build_query(
        _criteria(_group(_cond("album.favorite", "is", "true")))
    )
    assert "albums.favorite" in frag
    assert params == [1]
    assert join is True


def test_album_play_count_avg_sets_join_flag() -> None:
    frag, params, join = build_query(
        _criteria(_group(_cond("album.play_count_avg", "gt", "3")))
    )
    assert "albums.play_count_avg" in frag
    assert "> ?" in frag
    assert params == [3.0]
    assert join is True


# ---------------------------------------------------------------------------
# Numeric fields
# ---------------------------------------------------------------------------


def test_track_play_count_gt() -> None:
    frag, params, join = build_query(
        _criteria(_group(_cond("track.play_count", "gt", "5")))
    )
    assert "tracks.play_count" in frag
    assert "> ?" in frag
    assert params == [5]


def test_track_play_count_lte() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.play_count", "lte", "10")))
    )
    assert "<= ?" in frag
    assert params == [10]


def test_track_last_played_uses_coalesce() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.last_played", "gt", "1000000.0")))
    )
    assert "COALESCE(tracks.last_played, 0)" in frag
    assert params == [1000000.0]


def test_track_date_added_gte() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.date_added", "gte", "1700000000.0")))
    )
    assert "tracks.date_added" in frag
    assert ">= ?" in frag
    assert params == [1700000000.0]


# ---------------------------------------------------------------------------
# Year field — TEXT column with CAST for numeric ops
# ---------------------------------------------------------------------------


def test_track_year_gt_uses_cast() -> None:
    frag, params, _ = build_query(_criteria(_group(_cond("track.year", "gt", "2010"))))
    assert "CAST(tracks.release_date AS INTEGER)" in frag
    assert "> ?" in frag
    assert params == ["2010"]


def test_track_year_lt_uses_cast() -> None:
    frag, params, _ = build_query(_criteria(_group(_cond("track.year", "lt", "2000"))))
    assert "CAST(tracks.release_date AS INTEGER)" in frag


def test_track_year_is_does_not_cast() -> None:
    frag, params, _ = build_query(_criteria(_group(_cond("track.year", "is", "2020"))))
    assert "CAST" not in frag
    assert "tracks.release_date" in frag
    assert "= ?" in frag
    assert params == ["2020"]


# ---------------------------------------------------------------------------
# Text fields — is / is_not / contains / not_contains
# ---------------------------------------------------------------------------


def test_track_artist_is() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.artist", "is", "Weezer")))
    )
    assert "tracks.artist" in frag
    assert "= ?" in frag
    assert params == ["Weezer"]


def test_track_artist_is_not() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.artist", "is_not", "Nickelback")))
    )
    assert "!= ?" in frag
    assert params == ["Nickelback"]


def test_track_genre_is_uses_track_genres_exists() -> None:
    # KAMP-586: genre resolves via the normalized join, not the flat column.
    frag, params, join = build_query(
        _criteria(_group(_cond("track.genre", "is", "Jazz")))
    )
    assert "EXISTS" in frag
    assert "track_genres" in frag
    assert "g.name = ? COLLATE NOCASE" in frag
    assert not frag.startswith("NOT")
    assert params == ["Jazz"]
    assert join is False


def test_track_genre_is_not_negates_exists() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.genre", "is_not", "Jazz")))
    )
    assert "NOT EXISTS" in frag
    assert params == ["Jazz"]


def test_track_genre_contains_wraps_value() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.genre", "contains", "Rock")))
    )
    assert "EXISTS" in frag
    assert "g.name LIKE ?" in frag
    assert params == ["%Rock%"]


def test_track_genre_not_contains() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.genre", "not_contains", "Pop")))
    )
    assert "NOT EXISTS" in frag
    assert "g.name LIKE ?" in frag
    assert params == ["%Pop%"]


def test_track_album_contains() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.album", "contains", "Blue")))
    )
    assert "tracks.album" in frag
    assert params == ["%Blue%"]


def test_track_album_artist_contains() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.album_artist", "contains", "Beach")))
    )
    assert "tracks.album_artist" in frag
    assert params == ["%Beach%"]


def test_track_source_is() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.source", "is", "bandcamp")))
    )
    # KAMP-542: track.source resolves via track_sources (the preferred delivery),
    # not the tracks.source column, so KAMP-539 can drop it. The compared value is
    # unchanged, so stored criteria_json migrates without a rewrite.
    assert "track_sources" in frag
    assert params == ["bandcamp"]


# ---------------------------------------------------------------------------
# in_playlist special field
# ---------------------------------------------------------------------------


def test_in_playlist_is() -> None:
    frag, params, join = build_query(_criteria(_group(_cond("in_playlist", "is", "7"))))
    assert "EXISTS" in frag
    assert "NOT EXISTS" not in frag
    assert "playlist_tracks" in frag
    assert params == [7]
    assert join is False


def test_in_playlist_is_not() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("in_playlist", "is_not", "3")))
    )
    assert "NOT EXISTS" in frag
    assert params == [3]


# ---------------------------------------------------------------------------
# Group composition — AND / OR / negate
# ---------------------------------------------------------------------------


def test_group_match_all_uses_and() -> None:
    g = _group(
        _cond("track.artist", "is", "A"),
        _cond("track.genre", "is", "B"),
        match="all",
    )
    frag, _, _ = build_query(_criteria(g))
    assert " AND " in frag


def test_group_match_any_uses_or() -> None:
    g = _group(
        _cond("track.artist", "is", "A"),
        _cond("track.genre", "is", "B"),
        match="any",
    )
    frag, _, _ = build_query(_criteria(g))
    assert " OR " in frag


def test_group_negate_wraps_in_not() -> None:
    g = _group(_cond("track.favorite", "is", "true"), negate=True)
    frag, _, _ = build_query(_criteria(g))
    assert frag.startswith("NOT (")


# ---------------------------------------------------------------------------
# MagicCriteria top-level composition
# ---------------------------------------------------------------------------


def test_criteria_match_all_joins_groups_with_and() -> None:
    g1 = _group(_cond("track.artist", "is", "X"))
    g2 = _group(_cond("track.genre", "is", "Y"))
    frag, params, _ = build_query(_criteria(g1, g2, match="all"))
    assert " AND " in frag
    assert params == ["X", "Y"]


def test_criteria_match_any_joins_groups_with_or() -> None:
    g1 = _group(_cond("track.artist", "is", "X"))
    g2 = _group(_cond("track.genre", "is", "Y"))
    frag, params, _ = build_query(_criteria(g1, g2, match="any"))
    assert " OR " in frag


def test_needs_album_join_propagates_through_groups() -> None:
    g1 = _group(_cond("track.artist", "is", "X"))
    g2 = _group(_cond("album.favorite", "is", "true"))
    _, _, join = build_query(_criteria(g1, g2))
    assert join is True


def test_needs_album_join_false_when_no_album_fields() -> None:
    g = _group(_cond("track.artist", "is", "X"))
    _, _, join = build_query(_criteria(g))
    assert join is False


# ---------------------------------------------------------------------------
# Multi-condition params order
# ---------------------------------------------------------------------------


def test_params_are_ordered_correctly() -> None:
    g = _group(
        _cond("track.artist", "is", "Alvvays"),
        _cond("track.year", "gt", "2010"),
        match="all",
    )
    _, params, _ = build_query(_criteria(g))
    assert params[0] == "Alvvays"
    assert params[1] == "2010"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unknown_field_raises() -> None:
    g = _group(_cond("track.nonsense", "is", "x"))
    with pytest.raises(ValueError, match="Unknown magic playlist field"):
        build_query(_criteria(g))


def test_unknown_operator_raises() -> None:
    g = _group(_cond("track.artist", "between", "A"))
    with pytest.raises(ValueError, match="Unknown magic playlist operator"):
        build_query(_criteria(g))


def test_empty_value_for_int_field_raises() -> None:
    g = _group(_cond("track.play_count", "is", ""))
    with pytest.raises(ValueError, match="empty value"):
        build_query(_criteria(g))


def test_empty_value_for_float_field_raises() -> None:
    g = _group(_cond("track.last_played", "gt", ""))
    with pytest.raises(ValueError, match="empty value"):
        build_query(_criteria(g))


# ---------------------------------------------------------------------------
# Relative-date operators (in_last_days / in_last_weeks / in_last_months)
# ---------------------------------------------------------------------------


def test_in_last_days_produces_relative_sql() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.date_added", "in_last_days", "7")))
    )
    assert "strftime('%s','now')" in frag
    assert "86400" in frag
    assert params == [7]


def test_in_last_weeks_produces_relative_sql() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.date_added", "in_last_weeks", "2")))
    )
    assert "strftime('%s','now')" in frag
    assert "604800" in frag
    assert params == [2]


def test_in_last_months_produces_relative_sql() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.date_added", "in_last_months", "3")))
    )
    assert "strftime('%s','now')" in frag
    assert "2592000" in frag
    assert params == [3]


def test_in_last_days_invalid_value_raises() -> None:
    g = _group(_cond("track.date_added", "in_last_days", "not_a_number"))
    with pytest.raises(ValueError, match="integer value"):
        build_query(_criteria(g))


def test_in_last_days_on_last_played_field() -> None:
    frag, params, _ = build_query(
        _criteria(_group(_cond("track.last_played", "in_last_days", "30")))
    )
    assert "COALESCE(tracks.last_played, 0)" in frag
    assert params == [30]
