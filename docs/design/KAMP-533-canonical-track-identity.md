# Canonical track identity — design note

**Epic:** KAMP-533 · **Spike:** KAMP-534 · **Status:** proposed (review before KAMP-535)

This note ratifies the target data model for splitting a track's *identity* from the
per-mode *sources* that deliver its bytes and from its mutable *stats*. It is the
execution reference for KAMP-535–539. Two independent reviews shaped it: a data-safety
pass (migration correctness) and a maintainability pass (provider-agnostic +
multi-client future-proofing). Their required outcomes are the acceptance checklist at
the end.

---

## 1. Problem & current schema

kamp stores **one `tracks` row per URI**. `tracks.file_path TEXT NOT NULL UNIQUE`
([library.py](../../kamp_core/library.py) `_DDL`, tracks table) is the de-facto
identity. The streaming copy and the downloaded copy of the same track are therefore
two independent rows:

| id | file_path | source | favorite | play_count |
|----|-----------|--------|----------|------------|
| 120331 | `bandcamp://390849875/12` | bandcamp | 1 | 2 |
| 133040 | `/…/Alewya/Zero/12 - Cairo FM.mp3` | local | 0 | 1 |

`favorite` / `play_count` / `last_played` live on each row and are reconciled only at
two discrete moments — scan time (`inherit_remote_favorites` / `inherit_remote_play_counts`)
and download removal (`remove_download`, which `MAX`-merges local→remote). Nothing
reconciles them when a stat is mutated at runtime, so favoriting a track via the
transport after its album is downloaded writes the local row while the album page still
renders the streaming row (**KAMP-532**).

Patching this means adding coalescing logic to *every* mutable track property so it
fans out to all duplicate rows — unmaintainable, and it regenerates the same bug class
for every future per-track attribute. The fix is to stop duplicating the track.

Two facts make the fix cheaper than it looks:

- **`tracks.id` is already an `AUTOINCREMENT` PK and is already surfaced as
  `TrackOut.id`** ([server.py](../../kamp_core/server.py)). The canonical id exists; the
  work is making it *the* identity and moving the URI out.
- **`sessions` is already keyed per-service** (`service TEXT PRIMARY KEY`) — auth is
  already provider-agnostic. Sources should be too.

`_canonical_track_key` ([library.py](../../kamp_core/library.py), mirrored in
[playback.py](../../kamp_core/playback.py)) is a **misnomer** relative to this design:
it only normalizes `bandcamp://` slash forms — a *URI* operation — and does not unify a
local row with its streaming sibling. Under the new model it operates on
`track_sources.uri`, and "…_track_key" wrongly implies it yields a track identity (now
`tracks.id`). Both copies are **renamed `_canonical_track_uri`** in the core phase
(KAMP-536).

---

## 2. Target schema — three tables

Identity, delivery, and mutable state have three different lifecycles and are split into
three tables.

```sql
-- Catalog identity ONLY. Global, slowly-changing. One row per logical track.
CREATE TABLE tracks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL DEFAULT '',
    artist           TEXT NOT NULL DEFAULT '',
    album_artist     TEXT NOT NULL DEFAULT '',
    album            TEXT NOT NULL DEFAULT '',
    release_date     TEXT NOT NULL DEFAULT '',
    track_number     INTEGER NOT NULL DEFAULT 0,
    disc_number      INTEGER NOT NULL DEFAULT 1,
    album_id         INTEGER REFERENCES albums(id),
    mb_release_id    TEXT NOT NULL DEFAULT '',
    mb_recording_id  TEXT NOT NULL DEFAULT '',
    genre            TEXT NOT NULL DEFAULT '',
    label            TEXT NOT NULL DEFAULT '',
    -- User catalog corrections (KAMP-467). Global, not per-endpoint. Stay here.
    display_title        TEXT,
    display_album        TEXT,
    display_album_artist TEXT,
    date_added       REAL
);
-- Dropped from tracks: file_path, source, ext, embedded_art, mb…(kept), duration,
-- is_available, stream_url, stream_url_expires_at, file_mtime, sale_item_id,
-- favorite, play_count, last_played.

-- One row per way-to-get-the-bytes. Two ORTHOGONAL axes replace the old mode enum:
--   kind     = HOW the bytes arrive   (file on disk vs. streamed)
--   provider = WHO the catalog relationship is with (adapter-owned)
CREATE TABLE track_sources (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id              INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    kind                  TEXT NOT NULL CHECK (kind IN ('file','stream')),
    provider              TEXT NOT NULL DEFAULT '',   -- '' | 'bandcamp' | 'qobuz' | 'subvert' | 'ampwall'
    provider_item_id      TEXT,                       -- generalizes sale_item_id; adapter-interpreted; NO hard FK
    uri                   TEXT NOT NULL UNIQUE,        -- file path, or <provider>://… scheme
    ext                   TEXT NOT NULL DEFAULT '',
    duration              REAL NOT NULL DEFAULT 0,
    embedded_art          INTEGER NOT NULL DEFAULT 0,
    file_mtime            REAL,
    is_available          INTEGER NOT NULL DEFAULT 1,
    stream_url            TEXT,
    stream_url_expires_at REAL
);
CREATE INDEX track_sources_track_idx    ON track_sources(track_id);
CREATE INDEX track_sources_provider_idx ON track_sources(provider, provider_item_id);
-- ONLY uri is UNIQUE. Deliberately NO UNIQUE(track_id, kind|mode): .mp3 + .flac of one
-- track, and the same track streamable from two providers, are legitimate plural states.

-- Mutable stats, separated for clean identity + cross-device concurrency.
CREATE TABLE track_stats (
    track_id    INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    favorite    INTEGER NOT NULL DEFAULT 0,
    play_count  INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    updated_at  REAL          -- last-writer-wins timestamp for concurrent multi-client edits
);
-- NO profile_id. "Multi-client" here means endpoint fanout for a SINGLE listener
-- (phone + desktop + WebDAV), not multiple users. Stats stay one row per track.
```

### Why `track_stats` is split now, not deferred

v44 already rewrites every read and write of `favorite`/`play_count`/`last_played` —
they change tables regardless, and the merge rules are identical whether they land on
`tracks` or `track_stats`. Deferring the split guarantees a **third migration** that
re-sweeps the identical query-site list the day WebDAV multi-client lands. The
incremental cost of splitting now is one JOIN (or a compatibility VIEW); SQLite does not
care. `updated_at` buys cross-device last-writer-wins for free.

### Column-placement rationale (the split table)

| Column(s) | Table | Why |
|-----------|-------|-----|
| title, artist, album\*, track/disc number, release_date, mb ids, genre, label, display\_\* | `tracks` | Catalog identity; global; same for every source. `display_*` are catalog corrections, not taste. |
| `favorite`, `play_count`, `last_played` | `track_stats` | Mutable listener state; the exact columns whose per-row divergence caused KAMP-532. |
| `kind`, `provider`, `provider_item_id`, `uri` | `track_sources` | Identify and locate one delivery of the bytes. |
| `ext`, `duration`, `embedded_art`, `file_mtime`, `is_available`, `stream_url*` | `track_sources` | **Provably per-source.** A stream's duration/ext/embedded_art/availability differ from the transcoded local file's — merging them onto one row is wrong for one of the two. |

---

## 3. Mappings & the preferred-source rule

Today's rows map onto sources as:

| Today | `kind` | `provider` | `provider_item_id` |
|-------|--------|-----------|--------------------|
| `source='local'` unaffiliated rip | `file` | `''` | NULL |
| Bandcamp download | `file` | `bandcamp` | `<sale_item_id>` |
| Bandcamp stream sibling | `stream` | `bandcamp` | `<sale_item_id>` |
| (future) Qobuz stream | `stream` | `qobuz` | `<qobuz track id>` |

`provider_item_id` generalizes `sale_item_id` with **no hard FK**. Validation stays in
adapter code — exactly today's "valid_sids only, FK-safe" pattern in the upsert path.
`bandcamp_collection` stays as-is, adapter-owned; each future provider brings its own
collection table. **No** unified `provider_collections` table now (YAGNI).

**Preferred-source resolution** (deterministic, provider-agnostic) — used by the API,
playback, and WebDAV:

```sql
ORDER BY (kind='file') DESC,      -- a present local file beats a stream
         is_available DESC,        -- available beats unavailable
         provider_priority,        -- a small static provider ranking
         id                        -- stable tiebreak
LIMIT 1
```

---

## 4. API / UI identity

`TrackOut` keys on `id` and gains a `sources` list (each: kind, provider, uri,
is_available, duration). Endpoints keyed on `file_path` (favorite, queue ops, scrobble,
`get_tracks`) accept/return the canonical `id`.

**Transition tactic:** keep `TrackOut.file_path` alive through KAMP-537 as a **computed**
preferred-source URI, so the ~24 UI files migrate their React keys / favorite-target
from `file_path` to `id` incrementally. Delete the field in the cleanup phase (KAMP-539).
Flipping identity and removing the field in one PR is where this epic would stall.

---

## 5. Queue / now-playing

Store **`track_id` only** (plus an optional `pinned_source_id` for a rare explicit user
choice), and resolve the concrete source via the preferred-source rule at load time.

Consequence: a download completing mid-queue upgrades playback from stream→file
automatically; removing a download degrades it stream-ward automatically — both for
free. This **deletes** `remove_download`'s "swap a queue entry to its streaming
counterpart before deleting the file" choreography (`streaming_track_for_local_id`)
rather than porting it. The queue no longer holds URIs, so there is nothing to swap.

`player_state.track_path` and `queue_state.tracks` (a "JSON array of file paths") are
rewritten to carry `track_id`. Both are currently `CHECK (id = 1)` single-row tables;
under WebDAV multi-client they become per-client — **deferred** (see §8), but named here
so no new code assumes the singleton.

---

## 6. Magic-playlist criteria (`criteria.py`)

`_FIELD_MAP` binds several fields to columns that move:

- `track.favorite → tracks.favorite`, `track.play_count → tracks.play_count`,
  `track.last_played → tracks.last_played` — now on `track_stats`; the criteria SQL
  builder JOINs `track_stats` (LEFT JOIN so an unplayed/never-favorited track still
  matches, defaulting to 0/NULL).
- `track.source → tracks.source` — column removed. Replace with a **value-mapped EXISTS
  predicate** so stored `criteria_json` blobs migrate **without rewriting their values**:
  - stored value `'local'` ⇒ `EXISTS (track_sources ts WHERE ts.track_id = tracks.id AND ts.kind = 'file')`
  - any other value `v` ⇒ `EXISTS (track_sources ts WHERE ts.track_id = tracks.id AND ts.provider = v)`
  - `'qobuz'` becomes a valid criterion the day the adapter ships — zero criteria.py churn.

`albums.source` (`'local'|'bandcamp'|'mixed'`) is derived data and may lag; generalize
it later, not in v44.

---

## 7. Migration — spec

> **Sequencing (expand/contract) — refined during KAMP-535.** Dropping columns from
> `tracks` breaks every reader (`Track`, `_row_to_track`, the ~62 `bandcamp://` sites,
> `TrackOut`, `criteria.py`), and that rewrite is KAMP-536 — so the transform cannot land
> in a single migration that also ships to a working `main`. It is split across the
> phases as expand → migrate → contract:
> - **v44 / KAMP-535 (expand):** create `track_sources` + `track_stats` **empty** (in
>   `_DDL`), bump the schema version. No backfill, no collapse, no drops. Provably
>   zero behavior change; the old `tracks` columns stay authoritative.
> - **KAMP-536 (collapse + read switch):** a later migration performs the collapse/merge
>   below and the code switches reads/writes to the new tables (dual-writing the old
>   columns as a safety net). The old columns remain the source the collapse reads from.
> - **KAMP-539 (contract):** drop the now-duplicated columns from `tracks` once nothing
>   reads them.
>
> Everything below (matching, survivor-id, merge, repoint, crash-safety) is the
> **collapse migration** and lands in KAMP-536, not v44. v44 itself is only the empty-
> table creation + version bump.

All of the following land in **one migration** (the KAMP-536 collapse). The items under
"one-way doors" fix the *shape* of the tables and the *site list* the rewrite sweeps.

### One-way doors (must all be in v44)
- Two-axis `track_sources` (`kind` + `provider`), soft `(provider, provider_item_id)`
  provenance (no FK), `track_stats` split, `uri`-UNIQUE-only.

### Matching (which rows collapse into one canonical track)
- **Rows with `album_id IS NULL` match a sibling ONLY by `provider_item_id`** — never by
  `(album_id, track_number, disc_number)`. SQLite treats NULLs as equal in `GROUP BY`,
  and `track_number` defaults to 0, so an album-tuple key fuses **every untagged loose
  single in the library into one bucket**. This is the single worst failure mode; the
  key must exclude it by construction. Loose singles are a known, non-trivial population
  (the v42/v43 heals operate on exactly `source='local' AND album_id IS NULL`).
- **Full-album rows** match by `(album_id, track_number, disc_number)` **and** must agree
  on `provider_item_id` when both carry one. A differing provenance id is evidence the
  two rows are *different* purchases → do **not** merge.
- **Quarantine, don't guess:** any bucket with >2 rows, or >1 row of the same
  `(provider, kind)`, is written to a report and left un-merged. Duplicate-numbered
  tracks (hidden/bonus tracks, mis-tagged rips) are legal today and must not be fused
  irreversibly.
- Rows with no `provider_item_id` and no `album_id` each stay their own canonical track.

### Survivor id + repoint-before-delete
- Survivor = the **lower `tracks.id`** (deterministic).
- Every reference to the dropped id is repointed **in the same transaction, before the
  delete**:
  - `playlist_tracks.track_id` — `REFERENCES tracks(id) ON DELETE CASCADE`; a naive
    delete **silently removes the track from the user's playlist**. `UPDATE … SET
    track_id = <survivor>`, de-duping within a playlist.
  - `deferred_ops.track_id` — `UNIQUE`; drain or delete the losing id's op; handle the
    UNIQUE collision if both rows have one.
  - `tracks_fts` rowid (= `tracks.id`, manually synced) — `DELETE FROM tracks_fts WHERE
    rowid = <loser>`, else search hits resolve to nothing.
  - `player_state.track_path`, `queue_state.tracks` — rewrite path→`track_id` for the
    survivor.

### Per-field merge (COALESCE-non-empty, NOT survivor-wins)
- `favorite` = `MAX`, `play_count` = `MAX`, `last_played` = latest → `track_stats`.
- `display_*` — streaming value wins when both set (it is a streaming-metadata feature);
  otherwise COALESCE the non-NULL one. Never silently drop a user edit.
- `mb_recording_id`, `mb_release_id`, `genre`, `label` — **non-empty wins**. A local
  file's MusicBrainz match must not be wiped by a blank streaming row.
  (`extension_audit_log.track_mbid` keys audit entries by `mb_recording_id`; losing it
  disconnects the audit trail.)
- Per-source columns (`ext/duration/embedded_art/is_available/file_mtime/stream_url*`)
  are **copied from each matching original row** into its own `track_sources` row —
  never merged.

### Constraint pre-flight
- **De-dup normalized `uri`** (via `_canonical_track_uri`, the renamed
  `_canonical_track_key`) before inserting `track_sources` — two rows differing only by `bandcamp:/` vs `bandcamp://` slash form
  are distinct under today's `file_path UNIQUE` but collide under `uri UNIQUE`. Pick a
  winner deterministically.
- **Validate each `provider_item_id`** against its provider collection table; NULL-with-
  warning any stale id (mirrors the upsert-time guard) so a bulk insert can't fail on an
  un-synced item.

### Crash safety
- Whole v44 in **one transaction**, with the `schema_version` bump as the **last**
  statement → a crash rolls back atomically and a re-run starts clean (no double-counted
  `play_count`). MAX-merge is only idempotent while the source rows are still the
  originals, so atomicity, not MAX, is what guarantees safety.
- **Backup-first:** snapshot the whole sqlite file before any v44 statement; offer
  restore only if v44 itself aborts. Mirrors the v39 forked-album heal discipline
  (idempotent, try/except + rollback, INFO-logged).

### v44 pseudocode (execution sketch for KAMP-535)

```
begin transaction
snapshot db file to <db>.pre-v44.bak
create tracks_new, track_sources, track_stats

# 1. bucket existing tracks rows
for each row r in tracks:
    if r.album_id is NULL:
        key = ('sid', r.sale_item_id) if r.sale_item_id else ('solo', r.id)
    else:
        key = ('album', r.album_id, r.track_number, r.disc_number, r.sale_item_id or None)
    buckets[key].append(r)

# 2. collapse
for key, rows in buckets:
    if len(rows) > 2 or duplicate (provider, kind) within rows:
        report_quarantine(key, rows); keep rows un-merged as separate tracks; continue
    survivor = min(rows, key=id)
    insert tracks_new(survivor.id, merged catalog fields)     # COALESCE-non-empty
    insert track_stats(survivor.id, MAX favorite, MAX play_count, latest last_played)
    for r in rows:
        insert track_sources(track_id=survivor.id, kind, provider, provider_item_id,
                             normalized uri, per-source cols)  # skip uri dupes
    for loser in rows where id != survivor.id:
        update playlist_tracks set track_id=survivor.id where track_id=loser.id  # dedupe
        reassign/drop deferred_ops(loser.id)
        delete from tracks_fts where rowid=loser.id
        rewrite player_state/queue_state refs loser→survivor

# 3. swap tables, set schema_version LAST
drop tracks; rename tracks_new -> tracks; rebuild indexes/fts triggers
update schema_version set version = 44
commit   # atomic: crash before here => clean rollback
```

---

## 8. Explicitly deferred (name it, do not build it)

Two-way doors — reversible additions later, left as seams by the schema above:

- **Users / auth.** No `profile_id`; multi-client is single-listener endpoint fanout.
- **Per-client `queue_state` / `player_state`.** Currently `CHECK (id = 1)` singletons;
  WebDAV makes them per-client — a localized rebuild, not a v44 concern.
- **The WebDAV layer itself.** Addressing principle to honor when it lands: **id is
  identity, DAV paths are a computed projection.** Expose `/dav/tracks/<id>.<ext>`
  (stable, immutable) and a human-browsable `/dav/library/<Artist>/<Album>/<nn Title>`
  view *computed from* catalog metadata. **No table ever stores a DAV path** — that is
  the rule that keeps path-as-identity from creeping back in.
- **`albums.source` / album-level provenance generalization**, per-endpoint album/artist
  stats (`albums.favorite`, `albums.play_count_avg`, `artists.play_time`).
- **Not doing:** a MusicBrainz recording→release entity hierarchy (MB ids are attributes,
  not entities kamp owns); an event log / CRDT (SQLite single-writer + relative
  `play_count = play_count + 1` + LWW `updated_at` covers kamp-scale concurrency); a
  polymorphic `provider_collections` super-table; per-user playlists.

---

## 9. Reference audit — everything storing a track id or path

Routed to the phase that owns each. Nothing that stores a track identity is
unaccounted for.

| Site | Stores | Phase |
|------|--------|-------|
| `tracks.file_path` (UNIQUE, identity) | URI | v44 (535) — moves to `track_sources.uri` |
| `tracks.sale_item_id` | provenance | v44 (535) — moves to `track_sources.provider_item_id` |
| `tracks.favorite/play_count/last_played` | stats | v44 (535) — move to `track_stats` |
| per-source cols on `tracks` | delivery | v44 (535) — move to `track_sources` |
| `playlist_tracks.track_id` (FK CASCADE) | id | v44 repoint (535) |
| `deferred_ops.track_id` (UNIQUE) | id | v44 repoint (535) |
| `tracks_fts` rowid = id | id | v44 repoint (535) |
| `player_state.track_path` | path | v44 rewrite → id (535) |
| `queue_state.tracks` (JSON paths) | paths | v44 rewrite → id (535); runtime queue-by-id (536) |
| `criteria.py` `_FIELD_MAP` stats fields | column refs | core (536) — JOIN `track_stats` |
| `criteria.py` `track.source` | column ref | core (536) — EXISTS value-map + `criteria_json` compat |
| `server.py` `TrackOut` + siblings (`file_path` identity) | field | API (537) — key on id; `file_path` computed then removed (539) |
| UI (~24 files) React key / favorite-target | field | UI (538) |
| `extension_audit_log.track_mbid` | mb id | no change — protected by COALESCE-non-empty on `mb_recording_id` |
| `sessions` (per-service) | — | no change — already provider-agnostic |

---

## 10. Phase routing

| Phase | Ticket | Owns |
|-------|--------|------|
| Design spike | KAMP-534 | this note |
| Schema (expand) | KAMP-535 | §2 tables created **empty** in `_DDL` + version bump; no backfill/collapse |
| Core rewrite + collapse | KAMP-536 | the §7 collapse migration (matching, repoints, quarantine report); reads/writes vs. `track_sources`/`track_stats`; queue-by-id; `criteria.py`; rename `_canonical_track_key` → `_canonical_track_uri` (both copies); dual-write old columns |
| Server API | KAMP-537 | `TrackOut` on id + `sources`; computed `file_path` |
| UI | KAMP-538 | React key / favorite-target → id |
| Cleanup + contract + regression | KAMP-539 | drop duplicated `tracks` columns; remove reconcile helpers; delete computed `file_path`; full regression |

> **Hand-off invariant (KAMP-535 → KAMP-536):** after the expand phase the child tables
> are **empty**, and there is **no** 1:1 guarantee — a fresh install and any track scanned
> between 535 and 536 have no child row. KAMP-536 must derive from the old `tracks`
> columns, **LEFT JOIN** the children (never inner-join and silently drop tracks), and
> dual-write the old columns as the safety net until KAMP-539 contracts.

> **Note for KAMP-535/536/537:** their current Jira descriptions were written against the
> earlier *two-table* `mode`-enum proposal. Reconcile them to this three-table,
> `kind`+`provider`, `track_stats` design before starting each.

---

## Acceptance checklist

Data-safety:
- [ ] Loose untagged singles (`album_id IS NULL`, `track_number=0`) provably do not fuse — matched by `provider_item_id` only.
- [ ] Rows with no provenance id and no `album_id` each stay a distinct canonical track.
- [ ] Mismatched-provenance pairs treated as distinct; buckets >2 rows or dup `(provider,kind)` quarantined.
- [ ] Survivor-id rule stated; `playlist_tracks`/`deferred_ops`/`tracks_fts`/`queue_state`/`player_state` each repoint-before-delete.
- [ ] `display_*`/`mb_*`/`genre`/`label` = COALESCE-non-empty with stated precedence.
- [ ] Per-source columns copied from matching original row, never merged.
- [ ] `uri` slash-form de-dup + `provider_item_id` validation specified.
- [ ] Single-transaction + `schema_version`-last + backup-first specified.
- [ ] Reference-audit complete.

Maintainability / future-proofing:
- [ ] `track_sources` uses `kind` + `provider` axes; adding a provider is adapter + rows, no schema change.
- [ ] Provenance is soft `(provider, provider_item_id)`, no hard FK; `bandcamp_collection` stays adapter-owned.
- [ ] `track_stats` separated, no `profile_id`; `updated_at` for cross-device LWW.
- [ ] Only `uri` UNIQUE — plural sources per track allowed.
- [ ] Queue stores `track_id` only; `remove_download` swap choreography deleted, not ported.
- [ ] `TrackOut.file_path` kept as computed field through 537, deleted in 539.
- [ ] `track.source` criteria value-mapping migrates `criteria_json` without value rewrites.
- [ ] WebDAV deferred but its "no table stores a DAV path" principle recorded.
