#!/usr/bin/env bash
#
# merge_forked_album.sh — heal a "forked" album in a kamp library.db.
#
# A fork is two `albums` rows that are really the same release, split because a
# metadata mismatch (classically a trailing space in album_artist, but also any
# tag divergence: "A & B" vs "A / B", punctuation, etc.) defeated kamp's
# match-by-(album_artist, album) join. The download path then minted a second
# local album + artist instead of merging the downloaded files into the
# Bandcamp album that carries sale_item_id.
#
# This script reassigns the DROP album's tracks onto the KEEP album, deletes the
# orphan album, merges the duplicate artist (preserving play_time), normalizes
# the surviving names to a canonical string, recomputes albums.source, and
# verifies. It runs in ONE transaction after a timestamped backup.
#
# Pick KEEP = the album that carries sale_item_id (the Bandcamp origin) so the
# merged album stays linked to its collection item. kamp's tracks_for_album()
# hides the bandcamp:// rows whenever local tracks exist under the same
# album_id, so after the merge you see one album playing the downloaded files.
#
# Usage:
#   merge_forked_album.sh <db> <keep_album_id> <drop_album_id> [canonical_album_artist]
#
# If canonical_album_artist is omitted it defaults to TRIM() of the KEEP album's
# current album_artist. Pass it explicitly when the divergence is not just
# whitespace (e.g. unify "Homeboy Sandman / Edan" -> "Homeboy Sandman & Edan").
#
# Find candidates first (albums that collapse to the same trimmed/lowered key):
#   sqlite3 library.db "
#     SELECT TRIM(LOWER(album_artist)) k, TRIM(LOWER(album)) a,
#            GROUP_CONCAT(id) ids, COUNT(*) n
#     FROM albums GROUP BY k, a HAVING n > 1;"
#
set -euo pipefail

if [[ $# -lt 3 ]]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed '1d'  # print the header as help
  exit 2
fi

DB=$1
KEEP=$2
DROP=$3
CANON=${4:-}

q() { sqlite3 "$DB" "$@"; }

[[ -f $DB ]] || { echo "no such db: $DB" >&2; exit 1; }
[[ "$KEEP" =~ ^[0-9]+$ && "$DROP" =~ ^[0-9]+$ ]] || { echo "album ids must be integers" >&2; exit 1; }
[[ "$KEEP" != "$DROP" ]] || { echo "keep and drop are the same album" >&2; exit 1; }

# Resolve the two album rows up front; bail if either is missing. One query per
# field — packing fields into a delimited string and splitting in the shell is
# fragile (delimiter bytes don't reliably round-trip through read/IFS).
[[ $(q "SELECT COUNT(*) FROM albums WHERE id=$KEEP;") == 1 ]] || { echo "keep album $KEEP not found" >&2; exit 1; }
[[ $(q "SELECT COUNT(*) FROM albums WHERE id=$DROP;") == 1 ]] || { echo "drop album $DROP not found" >&2; exit 1; }

keep_aa=$(q "SELECT album_artist FROM albums WHERE id=$KEEP;")
keep_alb=$(q "SELECT album FROM albums WHERE id=$KEEP;")
keep_artist=$(q "SELECT IFNULL(artist_id,'') FROM albums WHERE id=$KEEP;")
keep_sale=$(q "SELECT IFNULL(sale_item_id,'') FROM albums WHERE id=$KEEP;")
drop_aa=$(q "SELECT album_artist FROM albums WHERE id=$DROP;")
drop_alb=$(q "SELECT album FROM albums WHERE id=$DROP;")
drop_artist=$(q "SELECT IFNULL(artist_id,'') FROM albums WHERE id=$DROP;")

# Default canonical artist = trimmed KEEP album_artist (computed in SQL so the
# shell never has to quote the raw name).
if [[ -z $CANON ]]; then
  CANON=$(q "SELECT TRIM(album_artist) FROM albums WHERE id=$KEEP;")
fi
canon_sql=$(printf '%s' "$CANON" | sed "s/'/''/g")  # single-quote-escaped for SQL

echo "DB:    $DB"
echo "KEEP:  album $KEEP  [$keep_aa] / [$keep_alb]  artist_id=$keep_artist  sale_item_id=${keep_sale:-<none>}"
echo "DROP:  album $DROP  [$drop_aa] / [$drop_alb]  artist_id=$drop_artist"
echo "CANON: [$CANON]"
[[ -n $keep_sale ]] || echo "WARNING: KEEP album has no sale_item_id — confirm it is the Bandcamp origin." >&2

# --- Decide the surviving artist (keep the one with more play_time) -----------
# artists are referenced only by albums.artist_id (tracks store a plain string).
art_keep=$keep_artist
art_drop=$drop_artist
art_survivor=""
art_loser=""
if [[ -n $art_keep && -n $art_drop && $art_keep != "$art_drop" ]]; then
  pt_keep=$(q "SELECT IFNULL(play_time,0) FROM artists WHERE id=$art_keep;")
  pt_drop=$(q "SELECT IFNULL(play_time,0) FROM artists WHERE id=$art_drop;")
  # string-safe float compare via sqlite
  if [[ $(q "SELECT ($pt_drop > $pt_keep);") == 1 ]]; then
    art_survivor=$art_drop; art_loser=$art_keep
  else
    art_survivor=$art_keep; art_loser=$art_drop
  fi
  echo "ARTIST: survivor=$art_survivor (sum play_time), loser=$art_loser (deleted)"
fi

# --- Backup -------------------------------------------------------------------
ts=$(q "SELECT strftime('%Y%m%d-%H%M%S','now');")
bak="$DB.bak-$ts"
cp "$DB" "$bak"
echo "backup: $bak"

# --- Merge (single transaction) ----------------------------------------------
sqlite3 "$DB" <<SQL
BEGIN;

-- 1. Move the DROP album's tracks onto the KEEP album.
UPDATE tracks SET album_id = $KEEP WHERE album_id = $DROP;

-- 2. Drop the now-empty duplicate album (frees the UNIQUE(album_artist,album) slot).
DELETE FROM albums WHERE id = $DROP;

-- 3. Merge artists: fold loser play_time into survivor, repoint refs, delete loser.
$( [[ -n $art_survivor && -n $art_loser ]] && cat <<ART
UPDATE artists SET play_time = play_time
  + (SELECT IFNULL(play_time,0) FROM artists WHERE id = $art_loser)
  WHERE id = $art_survivor;
UPDATE albums SET artist_id = $art_survivor WHERE artist_id = $art_loser;
DELETE FROM artists WHERE id = $art_loser
  AND NOT EXISTS (SELECT 1 FROM albums WHERE artist_id = $art_loser);
ART
)

-- 4. Normalize names to the canonical string (no collision now that DROP is gone).
UPDATE albums SET album_artist = '$canon_sql' WHERE id = $KEEP;
UPDATE tracks SET album_artist = '$canon_sql', artist = '$canon_sql' WHERE album_id = $KEEP;
$( [[ -n $art_survivor ]] && echo "UPDATE artists SET name = '$canon_sql' WHERE id = $art_survivor;" )

-- 5. Recompute albums.source from the surviving tracks (local present => 'local').
UPDATE albums SET source = (
  SELECT CASE
    WHEN COUNT(CASE WHEN t.source='local' THEN 1 END) > 0 THEN 'local'
    WHEN COUNT(DISTINCT t.source) > 1 THEN 'mixed'
    ELSE MIN(t.source) END
  FROM tracks t WHERE t.album_id = albums.id
) WHERE id = $KEEP;

-- 6. Keep bandcamp_collection.band_name consistent (no-op if unlinked).
UPDATE bandcamp_collection SET band_name = '$canon_sql'
  WHERE sale_item_id = (SELECT sale_item_id FROM albums WHERE id = $KEEP)
    AND sale_item_id IS NOT NULL;

COMMIT;
SQL

# --- Verify -------------------------------------------------------------------
echo "--- result ---"
sqlite3 -header -column "$DB" "
  SELECT id, album_artist, album, source, IFNULL(sale_item_id,'<none>') sale_item_id, artist_id
  FROM albums WHERE id = $KEEP;"
sqlite3 -header -column "$DB" "
  SELECT CASE WHEN file_path LIKE 'bandcamp://%' THEN 'stream' ELSE 'local' END kind,
         COUNT(*) n FROM tracks WHERE album_id = $KEEP GROUP BY kind;"
drop_left=$(q "SELECT COUNT(*) FROM albums WHERE id=$DROP;")
echo "drop album rows remaining: $drop_left (expect 0)"
echo "integrity: $(q 'PRAGMA integrity_check;')"
echo "If the kamp server is running, restart it (or rescan) to refresh its cached album list."
