"""Byte-level tests: a removed credential must not survive in the DB files.

KAMP-748. Everything else about credential storage is tested through the API --
"is the row NULL", "is it in the keychain". None of that says anything about the
*bytes*, and the bytes are the exposure: NULLing a column unlinks a value, it
does not erase it.

Two distinct mechanisms are covered, because they fail for different reasons and
one of them was the actual cause of residue found in a production database:

* ``DROP TABLE`` releases whole pages onto the freelist. ``PRAGMA secure_delete``
  defaults to FAST, which zeroes freed space only within pages that are being
  rewritten anyway -- it does not touch pages freed whole. The v11->v12 migration
  drops a ``sessions`` table holding plaintext credentials, so it leaks under the
  default.
* ``set_session`` had no WAL checkpoint, so superseding a credential left the
  previous value's page image in ``library.db-wal`` indefinitely.

Design notes, each of which cost a measurement to learn:

* **The ``no_keyring`` fixture is mandatory, not hygiene.** Without it, on macOS
  dev ``set_session`` succeeds against the Login Keychain and sets
  ``session_json = None``, so the test plants nothing and passes vacuously.
* **Every test asserts a positive control** -- that the sentinel really is on
  disk before the operation under test. ``test_clear_session_truncates_wal``
  shows why: it asserts the WAL is 0 bytes without ever checking the credential
  was in the WAL, so it would pass on a build where nothing was written at all.
* **Payload size matters and 2381 bytes is not enough.** At the default 4096-byte
  page size the maximum in-page payload is 4061 bytes, so a real 2381-byte cookie
  jar lives entirely in one leaf page and allocates no overflow pages. Only a
  payload above that threshold exercises freed *overflow* pages. Both are
  parametrised; the large case is the one that would catch a regression if cookie
  jars grow.
* ``-shm`` is deliberately not searched. It holds page numbers and hashes, not
  page content, and is deleted when the last connection closes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import keyring.errors
import pytest
from pytest_mock import MockerFixture

from kamp_core.library import LibraryIndex

#: High-entropy marker. Searched as raw bytes, so it must not collide with
#: anything SQLite or the schema writes, and must not look like a real cookie
#: name -- matching on key names like "identity" or "cookies" is unreliable
#: because they are also plausible album and artist names.
SENTINEL = "kamp748-3f9b1c7d5e2a4068-residue-marker"

#: Below and above the measured in-page/overflow boundary (4061 bytes at
#: page_size 4096). The names are asserted, not decorative: a future page_size
#: change must fail loudly rather than silently make the large case in-page.
IN_PAGE_SIZE = 2381  # the real observed bandcamp payload size
OVERFLOW_SIZE = 6000


def _payload(total: int) -> dict[str, str]:
    """A session dict whose JSON embeds the sentinel and reaches *total* bytes."""
    filler = "x" * max(total - len(SENTINEL), 0)
    return {"marker": SENTINEL, "pad": filler}


def _file_hits(db_path: Path, needle: str) -> dict[str, int]:
    """Count raw-byte occurrences of *needle* in the DB and its WAL."""
    raw = needle.encode("utf-8")
    out: dict[str, int] = {}
    for label, path in (("db", db_path), ("wal", Path(str(db_path) + "-wal"))):
        out[label] = path.read_bytes().count(raw) if path.exists() else 0
    return out


def _total_hits(db_path: Path, needle: str = SENTINEL) -> int:
    return sum(_file_hits(db_path, needle).values())


@pytest.fixture()
def no_keyring(mocker: MockerFixture) -> None:
    """Force the plaintext DB fallback so there is something to find on disk.

    Mirrors ``TestSessionManagement.no_keyring`` in test_library.py. Without it
    these tests plant nothing and pass for the wrong reason -- see the module
    docstring.
    """
    mocker.patch("kamp_core.library._mac_kc", None)
    mocker.patch("kamp_core.library._win_cred", None)
    err = keyring.errors.NoKeyringError()
    mocker.patch("kamp_core.library.keyring.get_password", side_effect=err)
    mocker.patch("kamp_core.library.keyring.set_password", side_effect=err)
    mocker.patch("kamp_core.library.keyring.delete_password", side_effect=err)


class TestPageSizeAssumption:
    """The payload sizes above are only meaningful at the default page size."""

    def test_overflow_boundary_is_where_we_think_it_is(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        page_size = index._conn.execute("PRAGMA page_size").fetchone()[0]
        index.close()

        assert page_size == 4096, (
            "payload sizes in this module assume page_size 4096;"
            f" got {page_size}. Recompute IN_PAGE_SIZE/OVERFLOW_SIZE against"
            " the new max in-page payload (usable_size - 35)."
        )
        assert IN_PAGE_SIZE < page_size - 35 < OVERFLOW_SIZE


class TestDropTableResidue:
    """DROP TABLE on a credential-bearing table must not leave the bytes behind.

    This is the mechanism that actually leaked in production. The v11->v12
    migration (library.py, "migrate session credentials from plaintext DB column
    to the OS keychain") does:

        CREATE TABLE sessions_new (...);
        INSERT INTO sessions_new SELECT * FROM sessions;
        DROP TABLE sessions;                      <-- frees whole pages
        ALTER TABLE sessions_new RENAME TO sessions;

    and then NULLs each column as it moves the credential into the keychain. The
    drop releases whole pages that still hold plaintext, and ``secure_delete``
    defaults to FAST, which skips exactly those pages.

    The shape is exercised directly rather than by seeding a v11 database and
    running the real ``_migrate``. Reaching back to schema 11 from the current
    fresh schema drags the whole migration ladder through columns the modern DDL
    no longer has (``file_mtime`` and friends) -- the synthetic-old-schema trap
    in CLAUDE.md. What matters is the invariant, which is a property of our
    connections and not of any one migration: a table dropped on a
    ``LibraryIndex`` connection does not strand its contents. That protects the
    next credential-bearing migration as well as this one.
    """

    def _drop_table_shape(self, index: LibraryIndex) -> None:
        """Replay the v11->v12 table-swap verbatim."""
        index._conn.executescript("""
            CREATE TABLE sessions_new (
                service      TEXT NOT NULL PRIMARY KEY,
                session_json TEXT,
                updated_at   REAL NOT NULL
            );
            INSERT INTO sessions_new SELECT * FROM sessions;
            DROP TABLE sessions;
            ALTER TABLE sessions_new RENAME TO sessions;
            """)
        index._conn.execute("UPDATE sessions SET session_json = NULL")
        index._conn.commit()

    def test_dropping_a_credential_table_leaves_no_bytes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            # Enough rows that the dropped table spans several pages, so whole
            # pages (not just part of one) land on the freelist.
            index._conn.executemany(
                "INSERT INTO sessions (service, session_json, updated_at)"
                " VALUES (?, ?, 0)",
                [(f"filler{i}", "y" * IN_PAGE_SIZE) for i in range(40)],
            )
            index._conn.execute(
                "INSERT INTO sessions (service, session_json, updated_at)"
                " VALUES (?, ?, 0)",
                ("bandcamp", json.dumps(_payload(IN_PAGE_SIZE))),
            )
            index._conn.commit()
            index._checkpoint_wal("test setup")
            assert _total_hits(db_path), (
                "positive control failed: the credential never reached the DB"
                " files, so this test cannot prove anything about erasure"
            )

            self._drop_table_shape(index)
            index._checkpoint_wal("test")

            assert _file_hits(db_path, SENTINEL) == {"db": 0, "wal": 0}
        finally:
            index.close()

    def test_the_default_pragma_would_not_have_caught_this(
        self, tmp_path: Path
    ) -> None:
        """Pin *why* the pragma is set, so nobody removes it as redundant.

        Under the FAST default the same shape strands the credential. If this
        ever starts passing with secure_delete left at its default, the
        explicit ON in _make_conn has become unnecessary and the comment there
        is wrong -- but that is a deliberate finding, not a silent one.
        """
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index._conn.execute("PRAGMA secure_delete=FAST")
            assert index._conn.execute("PRAGMA secure_delete").fetchone()[0] == 2, (
                "only the FAST keyword selects 2; the integer 2 is parsed as a"
                " boolean and sets ON"
            )
            index._conn.executemany(
                "INSERT INTO sessions (service, session_json, updated_at)"
                " VALUES (?, ?, 0)",
                [(f"filler{i}", "y" * IN_PAGE_SIZE) for i in range(40)],
            )
            index._conn.execute(
                "INSERT INTO sessions (service, session_json, updated_at)"
                " VALUES (?, ?, 0)",
                ("bandcamp", json.dumps(_payload(IN_PAGE_SIZE))),
            )
            index._conn.commit()
            index._checkpoint_wal("test setup")

            self._drop_table_shape(index)
            index._checkpoint_wal("test")

            assert _total_hits(db_path) > 0, (
                "FAST no longer strands a dropped credential table on this"
                " SQLite build -- re-evaluate the secure_delete=ON comment in"
                " _make_conn"
            )
        finally:
            index.close()


class TestSetSessionResidue:
    """Superseding a credential must not leave the old one in the WAL."""

    @pytest.mark.parametrize(
        "size", [IN_PAGE_SIZE, OVERFLOW_SIZE], ids=["in_page", "overflow"]
    )
    def test_overwriting_a_session_erases_the_previous_value(
        self, tmp_path: Path, no_keyring: None, size: int
    ) -> None:
        """set_session had no checkpoint, so the pre-image stayed in the WAL.

        The UPSERT replaces ``session_json`` in place and commits. Without a
        checkpoint the superseded page image sits in ``library.db-wal`` until an
        ordinary checkpoint happens to recycle those frames, which is not
        prompt and is not guaranteed.
        """
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index.set_session("bandcamp", _payload(size))
            assert _total_hits(db_path), "positive control failed: nothing planted"

            index.set_session("bandcamp", {"marker": "replacement-value"})

            assert _file_hits(db_path, SENTINEL) == {"db": 0, "wal": 0}
        finally:
            index.close()


class TestClearSessionResidue:
    """Disconnecting must erase the credential, not merely unlink the row."""

    @pytest.mark.parametrize(
        "size", [IN_PAGE_SIZE, OVERFLOW_SIZE], ids=["in_page", "overflow"]
    )
    def test_clear_session_erases_the_credential_bytes(
        self, tmp_path: Path, no_keyring: None, size: int
    ) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index.set_session("bandcamp", _payload(size))
            assert _total_hits(db_path), "positive control failed: nothing planted"

            index.clear_session("bandcamp")

            assert _file_hits(db_path, SENTINEL) == {"db": 0, "wal": 0}
        finally:
            index.close()

    def test_a_surviving_companion_row_does_not_shield_the_cleared_one(
        self, tmp_path: Path, no_keyring: None
    ) -> None:
        """Whether the leaf page is emptied or merely rewritten changes the path.

        With a second service still present the page stays in use and is
        rewritten in place; with none it is freed whole. Those are the two
        secure_delete regimes, so both need covering.
        """
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index.set_session("bandcamp", _payload(IN_PAGE_SIZE))
            index.set_session("lastfm", {"session_key": "keep-me-0d4f"})
            assert _total_hits(db_path), "positive control failed: nothing planted"

            index.clear_session("bandcamp")

            assert _file_hits(db_path, SENTINEL) == {"db": 0, "wal": 0}
            # The surviving credential must be untouched.
            assert index.get_session("lastfm") == {"session_key": "keep-me-0d4f"}
        finally:
            index.close()


class TestBackupResidue:
    """A snapshot must not become a permanent plaintext copy of a credential.

    ``sqlite3.Connection.backup`` is a page-level copy and brings freelist pages
    with it, so a ``.bak-*`` taken at any point after a plaintext credential
    existed can carry it even when the live row is already NULL. Backups are
    uniquely named and never pruned (KAMP-737), so each one is permanent.
    """

    def test_backup_does_not_carry_the_credential(
        self, tmp_path: Path, no_keyring: None
    ) -> None:
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index.set_session("bandcamp", _payload(IN_PAGE_SIZE))
            assert _total_hits(db_path), "positive control failed: nothing planted"

            assert index._backup_db("KAMP-748 test") is True
        finally:
            index.close()

        backups = list(tmp_path.glob("library.db.bak-*"))
        assert len(backups) == 1, f"expected one backup, got {backups}"
        raw = backups[0].read_bytes()
        assert raw.count(SENTINEL.encode("utf-8")) == 0

    def test_backup_file_is_owner_only(self, tmp_path: Path) -> None:
        """The destination is created outside _make_conn's umask guard.

        Observed backups are 0600, but nothing in the code enforces it -- under
        a 022 umask a fresh sqlite file is created 0644, so the mode is
        environment-dependent rather than guaranteed.
        """
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            assert index._backup_db("KAMP-748 mode test") is True
        finally:
            index.close()

        backup = next(tmp_path.glob("library.db.bak-*"))
        assert backup.stat().st_mode & 0o777 == 0o600


class TestResidueScrub:
    """The one-off pass that erases residue already on disk."""

    def test_scrub_removes_pre_existing_residue(
        self, tmp_path: Path, no_keyring: None
    ) -> None:
        """Residue predating the fix needs an explicit VACUUM to clear.

        secure_delete prevents *new* residue; it cannot reach bytes that are
        already in freed pages. Simulated here with secure_delete=OFF, which is
        the regime those bytes were written under.
        """
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index._conn.execute("PRAGMA secure_delete=OFF")
            index.set_session("bandcamp", _payload(IN_PAGE_SIZE))
            index._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            index.clear_session("bandcamp")
            assert _total_hits(db_path), (
                "positive control failed: expected residue to survive"
                " clear_session with secure_delete=OFF"
            )

            index.scrub_credential_residue()

            assert _file_hits(db_path, SENTINEL) == {"db": 0, "wal": 0}
        finally:
            index.close()

    def test_scrub_runs_once(self, tmp_path: Path) -> None:
        """Keyed on a settings marker, so a restart does not re-VACUUM."""
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            assert index.scrub_credential_residue() is True
            assert index.scrub_credential_residue() is False
        finally:
            index.close()

    def test_scrub_leaves_the_marker_unset_when_it_fails(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A locked DB must be retried next start, not silently skipped."""
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            mocker.patch.object(
                index,
                "_vacuum",
                side_effect=sqlite3.OperationalError("database is locked"),
            )
            assert index.scrub_credential_residue() is False
            mocker.stopall()
            # Retried on the next call because the marker was never written.
            assert index.scrub_credential_residue() is True
        finally:
            index.close()

    def test_scrub_preserves_library_contents(self, tmp_path: Path) -> None:
        """VACUUM rewrites the whole file; live data must survive it."""
        db_path = tmp_path / "library.db"
        index = LibraryIndex(db_path)
        try:
            index.set_setting("unrelated.key", "unrelated-value")
            index.scrub_credential_residue()
            assert index.get_setting("unrelated.key") == "unrelated-value"
            assert index._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            index.close()
