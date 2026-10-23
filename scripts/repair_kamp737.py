"""One-shot repair for KAMP-737: restore bandcamp_collection rows flattened by
an unguarded mark_collection_synced run.

On 2026-10-17 19:10:02 a dev build invoked mark_collection_synced against a
production library. It rewrote 833 rows with mode='local', added_at=0 and
num_streamable_tracks=0, because upsert_collection_item assigned mode and the
watermark unconditionally and floors added_at through MIN(). The streaming sync
skips mode='local' before it can reclassify anything, so nothing self-heals:
pre-orders lose their banner permanently, stream-only albums claim to be
downloaded, and purchase dates cannot rise again.

The code path is fixed, so this is only needed for a library that was already
hit. It restores three columns from a pre-incident backup, row by row, and only
where the live value is demonstrably the damaged one:

  mode                   restored when the live row says 'local' but owns no
                         file sources, so an album genuinely downloaded since
                         the backup keeps its 'local'
  added_at               restored when the live value is 0 and the backup has
                         a real timestamp
  num_streamable_tracks  restored when the live value is 0 and the backup has
                         a non-zero watermark

Rows absent from the backup (bought since) are never touched. Nothing is
deleted and no row is created.

Deliberately NOT shipped as a schema migration. A migration cannot rely on a
backup, and every backup-free discriminator tested was too blunt: "local with
stream sources and no file sources" mislabels about one row in ten, because
mode='local' in this schema overwhelmingly means *pre-owned* (inherited from
the v19 bandcamp_state.json import) rather than *we hold the files* -- only 97
of 720 genuinely-local rows have any file-source provenance at all.

Run from the repo root. Dry run by default:

    poetry run python scripts/repair_kamp737.py --backup ~/.local/share/kamp/library.db.bak-20261017-174838
    poetry run python scripts/repair_kamp737.py --backup <path> --apply

Stop the daemon first. SQLite in WAL mode keeps recent commits in a sidecar
file, so a reader that ignores -wal sees stale data; this script opens the live
DB directly rather than copying it, which keeps the WAL in play.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB = Path("~/.local/share/kamp/library.db").expanduser()

# Rows the bulk write touched all share this synced_at, to the second. Used only
# to report how much of the damage this backup can account for.
INCIDENT_SYNCED_AT = 1792278602  # 2026-10-17 19:10:02 local


def _connect(
    path: Path, *, read_only: bool, immutable: bool = False
) -> sqlite3.Connection:
    """Open *path*, optionally read-only.

    `immutable=1` is for the backup only. _backup_db leaves the snapshot in WAL
    mode, and opening a WAL database read-only needs a readable -shm beside it
    to coordinate with writers. A backup has none and never gains one, so a
    plain mode=ro open fails with "unable to open database file". Declaring it
    immutable is both true and the documented way out.
    """
    params = []
    if read_only:
        params.append("mode=ro")
    if immutable:
        params.append("immutable=1")
    uri = f"file:{path}" + ("?" + "&".join(params) if params else "")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _daemon_is_running(db_path: Path) -> bool:
    """True when a daemon.pid alongside the DB names a live process."""
    pid_file = db_path.parent / "daemon.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        import os

        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--backup", required=True, type=Path, help="pre-incident library.db.bak-*"
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="live library.db")
    ap.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = ap.parse_args()

    db_path = args.db.expanduser()
    backup_path = args.backup.expanduser()
    for p in (db_path, backup_path):
        if not p.exists():
            raise SystemExit(f"No such file: {p}")

    if args.apply and _daemon_is_running(db_path):
        raise SystemExit(
            "The kamp daemon appears to be running. Quit the app first — a "
            "concurrent sync could re-flatten rows while this repairs them."
        )

    live = _connect(db_path, read_only=not args.apply)
    bak = _connect(backup_path, read_only=True, immutable=True)

    before = {
        r["sale_item_id"]: r
        for r in bak.execute(
            "SELECT sale_item_id, mode, added_at, num_streamable_tracks"
            " FROM bandcamp_collection"
        )
    }
    # Sale items whose tracks carry at least one real file source. These were
    # genuinely downloaded, so their 'local' is the truth regardless of what the
    # backup said.
    downloaded = {
        r["provider_item_id"]
        for r in live.execute(
            "SELECT DISTINCT provider_item_id FROM track_sources"
            " WHERE kind = 'file' AND provider_item_id IS NOT NULL"
            "   AND provider_item_id != ''"
        )
    }

    mode_fixes: list[tuple[str, str, str]] = []
    date_fixes: list[tuple[str, float]] = []
    wm_fixes: list[tuple[str, int]] = []
    touched_by_incident = 0

    for row in live.execute(
        "SELECT sale_item_id, mode, added_at, num_streamable_tracks, synced_at,"
        "       band_name, item_title"
        " FROM bandcamp_collection"
    ):
        sid = row["sale_item_id"]
        was = before.get(sid)
        if was is None:
            continue  # acquired after the backup; not ours to touch
        if row["synced_at"] and int(row["synced_at"]) == INCIDENT_SYNCED_AT:
            touched_by_incident += 1

        if (
            row["mode"] == "local"
            and was["mode"] in ("preorder", "remote")
            and sid not in downloaded
        ):
            mode_fixes.append((sid, row["mode"], was["mode"]))
        if row["added_at"] == 0 and (was["added_at"] or 0) > 0:
            date_fixes.append((sid, was["added_at"]))
        if (
            row["num_streamable_tracks"] == 0
            and (was["num_streamable_tracks"] or 0) > 0
        ):
            wm_fixes.append((sid, was["num_streamable_tracks"]))

    print(
        f"live rows           : {sum(1 for _ in live.execute('SELECT 1 FROM bandcamp_collection'))}"
    )
    print(f"backup rows         : {len(before)}")
    print(f"rows stamped by the incident: {touched_by_incident}")
    print(f"mode restores       : {len(mode_fixes)}")
    for sid, now_mode, was_mode in mode_fixes[:10]:
        print(f"    {sid}: {now_mode} -> {was_mode}")
    if len(mode_fixes) > 10:
        print(f"    … and {len(mode_fixes) - 10} more")
    print(f"added_at restores   : {len(date_fixes)}")
    print(f"watermark restores  : {len(wm_fixes)}")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to commit.")
        return

    with live:
        for sid, _now_mode, was_mode in mode_fixes:
            live.execute(
                "UPDATE bandcamp_collection SET mode = ? WHERE sale_item_id = ?",
                (was_mode, sid),
            )
        for sid, was_added in date_fixes:
            live.execute(
                "UPDATE bandcamp_collection SET added_at = ? WHERE sale_item_id = ?",
                (was_added, sid),
            )
        for sid, was_wm in wm_fixes:
            live.execute(
                "UPDATE bandcamp_collection SET num_streamable_tracks = ?"
                " WHERE sale_item_id = ?",
                (was_wm, sid),
            )

    # Verify against the backup rather than trusting the UPDATEs.
    bad = 0
    for row in live.execute(
        "SELECT sale_item_id, mode, added_at, num_streamable_tracks"
        " FROM bandcamp_collection"
    ):
        was = before.get(row["sale_item_id"])
        if was is None or row["sale_item_id"] in downloaded:
            continue
        if row["mode"] != was["mode"] and was["mode"] in ("preorder", "remote"):
            print(
                f"  ! {row['sale_item_id']} mode is {row['mode']}, expected {was['mode']}"
            )
            bad += 1
        if (was["added_at"] or 0) > 0 and row["added_at"] != was["added_at"]:
            print(f"  ! {row['sale_item_id']} added_at is {row['added_at']}")
            bad += 1

    print(f"\nApplied. Post-check mismatches: {bad}")
    for mode, n in live.execute(
        "SELECT mode, COUNT(*) n FROM bandcamp_collection GROUP BY mode"
    ):
        print(f"  {mode:9} {n}")
    live.close()
    bak.close()


if __name__ == "__main__":
    main()
