---
id: TASK-132
title: move application configuration inside the database
status: To Do
assignee: []
created_date: '2026-04-15 02:48'
updated_date: '2026-04-18 17:23'
labels:
  - feature
  - backend
  - 'estimate: lp'
milestone: m-29
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
We have outgrown the TOML config file. All application configuration and preferences should be stored in the SQLite database alongside playback state and session data. This eliminates the external file dependency, enables atomic updates, and gives us a single source of truth for all persistent application state.

`bandcamp.username` and `bandcamp.cookie_file` are deprecated as part of this migration and will not be carried forward to the database.

**Config keys migrated to DB (13 total):**

| Key | Type | Default |
|-----|------|---------|
| `paths.watch_folder` | str (Path) | `~/Music/staging` |
| `paths.library` | str (Path) | `~/Music` |
| `musicbrainz.trust_musicbrainz_when_tags_conflict` | bool | `true` |
| `artwork.min_dimension` | int | `1000` |
| `artwork.max_bytes` | int | `1_000_000` |
| `library.path_template` | str | `{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}` |
| `bandcamp.format` | str | `mp3-v0` |
| `bandcamp.poll_interval_minutes` | int | `0` |
| `lastfm.username` | str | null |
| `lastfm.session_key` | str | null |
| `ui.active_view` | str | `library` |
| `ui.sort_order` | str | `album_artist` |
| `ui.queue_panel_open` | int | `0` |

**Deprecated (not migrated):**

| Key | Reason |
|-----|--------|
| `bandcamp.username` | Deprecated |
| `bandcamp.cookie_file` | Deprecated |
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All 13 active config keys are read from and written to a new `settings` table in the SQLite database
- [ ] #2 A one-time migration reads an existing `config.toml` on startup and populates the `settings` table; `bandcamp.username` and `bandcamp.cookie_file` are silently dropped during migration; after migration the TOML file is left in place but no longer read
- [ ] #3 `Config.load()` reads from the database and returns the same `Config` object shape as before — callers are unchanged
- [ ] #4 `config_set <key> <value>` CLI command writes to the database instead of the TOML file; attempting to set a deprecated key returns a clear error
- [ ] #5 `first_run_setup` and `bandcamp_setup` write initial values to the database instead of creating a TOML file; they no longer prompt for or store `username` or `cookie_file`
- [ ] #6 `ConfigMonitor` watchdog is removed; config changes take effect immediately on write (no file watching needed)
- [ ] #7 Existing validation (allowed values for `bandcamp.format`, `ui.active_view`, `ui.sort_order`) is preserved
- [ ] #8 Schema version is bumped and a `_migrate()` step creates the `settings` table
- [ ] #9 All existing config tests pass; new tests cover the migration path (TOML present → DB populated, deprecated keys dropped) and the no-TOML path (fresh install → defaults written)
- [ ] #10 README and any user-facing docs referring to `config.toml`, `bandcamp.username`, or `bandcamp.cookie_file` are updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
**Estimate:** LP

### 1. DB schema — bump to version 11

Add to `_migrate()` in `kamp_core/library.py`:

```sql
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
)
```

No `type` column needed — use the existing `_CONFIG_KEY_TYPES` dict in `config.py` for coercion on read.

### 2. Add `get_setting` / `set_setting` to `LibraryIndex`

```python
def get_setting(self, key: str) -> str | None: ...
def set_setting(self, key: str, value: str) -> None: ...
def get_all_settings(self) -> dict[str, str]: ...
```

Follow the same pattern as `get_session` / `set_session` in `library.py`.

### 3. Refactor `Config` to read/write from DB

- `Config.load(db: LibraryIndex) -> Config` — reads all rows from `settings`, applies defaults for missing keys, coerces types via `_CONFIG_KEY_TYPES`.
- `Config.write_defaults(db: LibraryIndex) -> None` — writes all default values for keys not yet present (used on fresh install).
- Remove `tomllib` import and TOML parsing logic.
- Remove `bandcamp.username` and `bandcamp.cookie_file` from `_CONFIG_KEY_TYPES`, `_CONFIG_KEY_CHOICES`, and `Config` dataclass fields.
- Keep all other validation logic unchanged.

### 4. One-time TOML migration

In `Config.load()`, before reading from DB: if `settings` table is empty AND `config.toml` exists, parse the TOML file and write each key into `settings`, skipping `bandcamp.username` and `bandcamp.cookie_file`. Log a one-time info message: "Migrated config.toml → database (deprecated keys dropped); file left in place as backup."

### 5. Update `config_set` CLI command

Change the writer from `config_set(path, key, value)` (in-place TOML text manipulation) to `db.set_setting(key, value)`. If the key is `bandcamp.username` or `bandcamp.cookie_file`, exit with a clear deprecation error. Validate choices before writing (same as today).

### 6. Update `first_run_setup` and `bandcamp_setup`

Write initial key/value pairs to `settings` table via `db.set_setting()`. Remove all file I/O (`open`, TOML write). Remove prompts for `username` and `cookie_file`. Remove the `ConfigMonitor` (watchdog file watcher) — no longer needed since config changes are applied immediately on write.

### 7. Update call sites

- `DaemonCore.__init__`: pass `db` to `Config.load(db)` instead of a file path.
- Remove `ConfigMonitor` instantiation.
- Audit any Bandcamp code that reads `config.bandcamp_username` or `config.bandcamp_cookie_file` and remove or replace those references.
- Confirm `Watcher`, `Syncer`, and HTTP server still receive a `Config` object (no interface change needed).

### 8. Tests

- `tests/test_config.py`: swap fixture from tmp TOML file → tmp DB.
- Add test: existing `config.toml` with deprecated keys → migration drops them, 13 active keys present.
- Add test: fresh install (no TOML) → defaults written to DB.
- Add test: `config_set` writes to DB, `Config.load()` returns updated value.
- Add test: `config_set bandcamp.username` → deprecation error.
- Use `--no-cov` when running single test file.

### 9. Docs

- Update `README.md`: remove references to `config.toml` location and manual editing, remove `bandcamp.username` and `bandcamp.cookie_file` from any config reference.
- Update any setup/onboarding copy that mentions editing the TOML file or Bandcamp cookie setup.
<!-- SECTION:PLAN:END -->
