"""Translate MagicCriteria into a parameterized SQLite WHERE fragment."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kamp_core.library import Condition, Group, MagicCriteria

# ---------------------------------------------------------------------------
# Field registry
# ---------------------------------------------------------------------------

# A track's effective (preferred) source, reconstructed from track_sources
# (KAMP-542). Reproduces post-collapse tracks.source (preferred delivery, mapped
# file->'local' / stream->'bandcamp'), with a COALESCE fallback to the legacy
# column for a sourceless row (dropped with the column in KAMP-539). Correlated
# on `tracks.id` — the magic-playlist evaluation queries alias the row source as
# `tracks`. Lets `track.source` criteria read track_sources with no value rewrite
# in stored criteria_json (the compared values stay 'local'/'bandcamp'), so the
# existing is/is_not/contains operator handling works unchanged.
_EFFECTIVE_SOURCE_SQL = (
    "COALESCE((SELECT CASE WHEN s.kind = 'file' THEN 'local' ELSE 'bandcamp' END"
    " FROM track_sources s WHERE s.track_id = tracks.id"
    " ORDER BY s.is_available DESC, (s.kind = 'file') DESC, s.id LIMIT 1),"
    " 'local')"  # KAMP-539: tracks.source dropped; default matches its old DEFAULT
)

# Maps field name → (sql_column_expr, value_type).
# value_type controls coercion and, for "year", special CAST wrapping on
# numeric operators.
_FIELD_MAP: dict[str, tuple[str, str]] = {
    "track.favorite": ("tracks.favorite", "bool"),
    "album.favorite": ("albums.favorite", "bool"),
    "album.play_count_avg": ("albums.play_count_avg", "float"),
    "track.play_count": ("tracks.play_count", "int"),
    # NULL last_played treated as 0 so numeric comparisons work on unplayed tracks.
    "track.last_played": ("COALESCE(tracks.last_played, 0)", "float"),
    "track.date_added": ("tracks.date_added", "float"),
    # release_date is TEXT; numeric ops use CAST so CAST("2023-03-15" AS INTEGER) = 2023.
    "track.year": ("tracks.release_date", "year"),
    "track.genre": ("tracks.genre", "text"),
    "track.artist": ("tracks.artist", "text"),
    "track.album_artist": ("tracks.album_artist", "text"),
    "track.album": ("tracks.album", "text"),
    "track.source": (_EFFECTIVE_SOURCE_SQL, "text"),
}

# Fields whose SQL expression references the albums table.
_ALBUM_FIELDS: frozenset[str] = frozenset({"album.favorite", "album.play_count_avg"})

# Operators that need a numeric (CAST) column expression for "year".
_NUMERIC_OPS: frozenset[str] = frozenset({"gt", "lt", "gte", "lte"})

# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def _coerce(value: str, vtype: str) -> Any:
    """Convert the wire string *value* to the Python type expected by SQLite."""
    if vtype == "bool":
        return 1 if value.lower() == "true" else 0
    if vtype == "int":
        if not value.strip():
            raise ValueError(f"empty value for int field")
        return int(value)
    if vtype == "float":
        if not value.strip():
            raise ValueError(f"empty value for float field")
        return float(value)
    # "text" and "year" pass through as-is; year CAST happens in SQL.
    return value


# ---------------------------------------------------------------------------
# Condition → SQL fragment
# ---------------------------------------------------------------------------

_OP_MAP: dict[str, str] = {
    "is": "= ?",
    "is_not": "!= ?",
    "gt": "> ?",
    "lt": "< ?",
    "gte": ">= ?",
    "lte": "<= ?",
    "contains": "LIKE ?",
    "not_contains": "NOT LIKE ?",
}

# Relative-date ops: evaluate at query time so stored criteria stay meaningful
# regardless of when they were saved.
_RELATIVE_OPS: dict[str, int] = {
    "in_last_days": 86400,
    "in_last_weeks": 604800,
    "in_last_months": 2592000,  # 30-day month approximation
}


def _condition_sql(cond: "Condition") -> tuple[str, list[Any], bool]:
    """Return ``(sql_fragment, params, needs_album_join)`` for a single condition."""
    field = cond.field
    op = cond.op
    value = cond.value

    # in_playlist is a special subquery field.
    if field == "in_playlist":
        playlist_id = int(value)
        subquery = (
            "EXISTS (SELECT 1 FROM playlist_tracks"
            " WHERE playlist_id = ? AND track_id = tracks.id)"
        )
        if op == "is_not":
            subquery = "NOT " + subquery
        return subquery, [playlist_id], False

    if field not in _FIELD_MAP:
        raise ValueError(f"Unknown magic playlist field: {field!r}")

    col_expr, vtype = _FIELD_MAP[field]
    needs_join = field in _ALBUM_FIELDS

    # Relative-date ops compute the threshold at query time so stored criteria
    # remain correct regardless of when they were saved.
    if op in _RELATIVE_OPS:
        seconds_per_unit = _RELATIVE_OPS[op]
        try:
            amount = int(value)
        except (ValueError, TypeError):
            raise ValueError(
                f"in_last operators require an integer value, got {value!r}"
            )
        fragment = f"{col_expr} > CAST(strftime('%s','now') AS INTEGER) - ? * {seconds_per_unit}"
        return fragment, [amount], needs_join

    if op not in _OP_MAP:
        raise ValueError(f"Unknown magic playlist operator: {op!r}")

    # For year with numeric operators, wrap the column in CAST so SQLite
    # compares integers rather than string-collation order.
    if vtype == "year" and op in _NUMERIC_OPS:
        col_expr = f"CAST({col_expr} AS INTEGER)"

    param: Any
    if op == "contains":
        param = f"%{value}%"
    elif op == "not_contains":
        param = f"%{value}%"
    else:
        param = _coerce(value, vtype)

    sql_op = _OP_MAP[op]
    fragment = f"{col_expr} {sql_op}"
    return fragment, [param], needs_join


# ---------------------------------------------------------------------------
# Group → SQL fragment
# ---------------------------------------------------------------------------


def _group_sql(group: "Group") -> tuple[str, list[Any], bool]:
    """Return ``(sql_fragment, params, needs_album_join)`` for a condition group."""
    if not group.conditions:
        return "0", [], False

    joiner = " AND " if group.match == "all" else " OR "
    parts: list[str] = []
    params: list[Any] = []
    needs_join = False

    for cond in group.conditions:
        frag, p, nj = _condition_sql(cond)
        parts.append(frag)
        params.extend(p)
        needs_join = needs_join or nj

    block = f"({joiner.join(parts)})"
    if group.negate:
        block = f"NOT {block}"
    return block, params, needs_join


# ---------------------------------------------------------------------------
# MagicCriteria → full WHERE fragment
# ---------------------------------------------------------------------------


def build_query(criteria: "MagicCriteria") -> tuple[str, list[Any], bool]:
    """Translate *criteria* into a parameterized SQLite WHERE fragment.

    Returns ``(where_fragment, params, needs_album_join)``.  The caller is
    responsible for composing the full SELECT and adding a LEFT JOIN on albums
    when ``needs_album_join`` is True.  An empty criteria (no groups) returns
    ``("0", [], False)`` — matching nothing — so the caller always gets a valid
    SQL fragment.
    """
    if not criteria.groups:
        return "0", [], False

    joiner = " AND " if criteria.match == "all" else " OR "
    parts: list[str] = []
    params: list[Any] = []
    needs_join = False

    for group in criteria.groups:
        frag, p, nj = _group_sql(group)
        parts.append(frag)
        params.extend(p)
        needs_join = needs_join or nj

    return joiner.join(parts), params, needs_join
