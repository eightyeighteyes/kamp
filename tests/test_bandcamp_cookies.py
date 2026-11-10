"""KAMP-746: only the cookies Bandcamp authentication needs are persisted."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any
from unittest.mock import patch

import keyring.errors
import pytest
from pytest_mock import MockerFixture

from kamp_core.library import CredentialStorageError, LibraryIndex
from kamp_daemon.bandcamp import (
    AUTH_COOKIE_NAMES,
    _store_username_in_session,
    filter_session_cookies,
    prune_stored_session,
    store_login_session,
)

_TRACKERS = [
    "_ga",
    "_ga_MN4RN3JYWL",
    "_fbp",
    "_rdt_uuid",
    "cookie_preferences",
    "cart_client_id",
]


def _cookie(name: str, value: str = "v") -> dict[str, Any]:
    return {"name": name, "value": value, "domain": ".bandcamp.com", "path": "/"}


def _logout_cookie(username: str) -> dict[str, Any]:
    return _cookie("logout", urllib.parse.quote(json.dumps({"username": username})))


def _login_jar() -> list[dict[str, Any]]:
    """The jar the login window hands over: auth cookies plus everything else."""
    auth = [_cookie(n, "1" if n == "js_logged_in" else "v") for n in AUTH_COOKIE_NAMES]
    return auth + [_cookie(n) for n in _TRACKERS] + [_cookie("cf_clearance")]


def _names(session: dict[str, Any]) -> set[str]:
    return {c["name"] for c in session["cookies"]}


@pytest.fixture
def index(tmp_path: Path) -> Any:
    """A real LibraryIndex on conftest's working in-memory credential store.

    Used to force ``NoKeyringError`` so sessions landed in the plaintext DB
    column; since KAMP-750 that path raises instead.
    """
    idx = LibraryIndex(tmp_path / "library.db")
    yield idx
    idx.close()


class TestFilterSessionCookies:
    def test_keeps_every_allowlisted_cookie(self) -> None:
        filtered = filter_session_cookies({"cookies": _login_jar()})
        assert _names(filtered) == set(AUTH_COOKIE_NAMES)

    def test_drops_trackers_and_unknown_names(self) -> None:
        jar = _login_jar() + [_cookie("some_future_cookie")]
        names = _names(filter_session_cookies({"cookies": jar}))
        assert names.isdisjoint(_TRACKERS)
        assert "some_future_cookie" not in names

    def test_cloudflare_cookies_are_not_persisted(self) -> None:
        # Chromium owns and refreshes these; a stored copy would be stale and,
        # via the relay's inject/remove, would clobber the live one.
        jar = _login_jar() + [_cookie("__cf_bm")]
        names = _names(filter_session_cookies({"cookies": jar}))
        assert "cf_clearance" not in names
        assert "__cf_bm" not in names

    def test_the_locally_checked_login_marker_survives(self) -> None:
        # _validate_session rejects a jar without js_logged_in=1 before it ever
        # reaches the network, so dropping it would silently log the user out.
        assert "js_logged_in" in AUTH_COOKIE_NAMES
        assert "identity" in AUTH_COOKIE_NAMES

    def test_preserves_non_cookie_keys(self) -> None:
        session = {"cookies": _login_jar(), "origins": [], "username": "fan"}
        filtered = filter_session_cookies(session)
        assert filtered["username"] == "fan"
        assert filtered["origins"] == []

    def test_does_not_mutate_its_input(self) -> None:
        session = {"cookies": _login_jar()}
        before = json.dumps(session, sort_keys=True)
        filter_session_cookies(session)
        assert json.dumps(session, sort_keys=True) == before

    def test_is_idempotent(self) -> None:
        once = filter_session_cookies({"cookies": _login_jar()})
        assert filter_session_cookies(once) == once

    def test_tolerates_missing_cookie_list(self) -> None:
        assert filter_session_cookies({"username": "fan"}) == {
            "username": "fan",
            "cookies": [],
        }


class TestStoreLoginSession:
    def test_persists_only_allowlisted_cookies(self, index: LibraryIndex) -> None:
        jar = _login_jar() + [_logout_cookie("fan")]
        store_login_session(index, {"cookies": jar, "origins": []})
        stored = index.get_session("bandcamp")
        assert stored is not None
        assert _names(stored) <= set(AUTH_COOKIE_NAMES)
        assert _names(stored).isdisjoint(_TRACKERS)

    def test_username_comes_from_the_raw_jar(self, index: LibraryIndex) -> None:
        # The logout cookie is read before filtering, so the username is stored
        # whether or not `logout` itself is on the allowlist.
        jar = _login_jar() + [_logout_cookie("fan")]
        with patch("kamp_daemon.bandcamp._get_fan_info") as fan_info:
            store_login_session(index, {"cookies": jar, "origins": []})
        fan_info.assert_not_called()
        stored = index.get_session("bandcamp")
        assert stored is not None
        assert stored["username"] == "fan"

    def test_fallback_lookup_sees_only_the_filtered_jar(
        self, index: LibraryIndex
    ) -> None:
        # If the fallback ran on the full jar it could succeed while the jar we
        # actually store is broken -- hiding exactly the failure to watch for.
        seen: list[set[str]] = []

        def _fake_session(session_data: dict[str, Any]) -> object:
            seen.append(_names(session_data))
            return object()

        jar = [c for c in _login_jar() if c["name"] != "logout"]
        with (
            patch("kamp_daemon.bandcamp._make_requests_session", _fake_session),
            patch("kamp_daemon.bandcamp._get_fan_info", return_value=(1, "fan")),
        ):
            store_login_session(index, {"cookies": jar, "origins": []})
        assert seen and seen[0] <= set(AUTH_COOKIE_NAMES)
        stored = index.get_session("bandcamp")
        assert stored is not None
        assert stored["username"] == "fan"

    def test_fallback_failure_still_stores_the_session(
        self, index: LibraryIndex
    ) -> None:
        jar = [c for c in _login_jar() if c["name"] != "logout"]
        with patch(
            "kamp_daemon.bandcamp._get_fan_info", side_effect=RuntimeError("offline")
        ):
            store_login_session(index, {"cookies": jar, "origins": []})
        stored = index.get_session("bandcamp")
        assert stored is not None
        assert "username" not in stored


class TestPruneStoredSession:
    def test_rewrites_a_session_carrying_trackers(self, index: LibraryIndex) -> None:
        index.set_session("bandcamp", {"cookies": _login_jar(), "username": "fan"})
        result = prune_stored_session(index, index.get_session("bandcamp"))
        stored = index.get_session("bandcamp")
        assert stored is not None
        assert _names(stored) == set(AUTH_COOKIE_NAMES)
        assert stored["username"] == "fan"
        assert result == stored

    def test_leaves_a_clean_session_unwritten(self, index: LibraryIndex) -> None:
        clean = filter_session_cookies({"cookies": _login_jar()})
        index.set_session("bandcamp", clean)
        with patch.object(index, "set_session") as set_session:
            assert prune_stored_session(index, clean) == clean
        set_session.assert_not_called()

    def test_absent_session_is_skipped(self, index: LibraryIndex) -> None:
        with patch.object(index, "set_session") as set_session:
            assert prune_stored_session(index, None) is None
        set_session.assert_not_called()

    def test_write_failure_still_returns_the_filtered_session(
        self, index: LibraryIndex
    ) -> None:
        # A failed keychain write must not cost the user their session, and the
        # caller should still hand out the filtered jar for this run.
        session = {"cookies": _login_jar()}
        with patch.object(index, "set_session", side_effect=OSError("locked")):
            result = prune_stored_session(index, session)
        assert result is not None
        assert _names(result) == set(AUTH_COOKIE_NAMES)


class TestFailClosedCallers:
    """KAMP-750: where a credential write may fail, and what each caller does."""

    @pytest.fixture
    def failing_store(self, mocker: MockerFixture) -> None:
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.KeyringError("locked"),
        )

    def test_login_propagates_storage_error(
        self, index: LibraryIndex, failing_store: None
    ) -> None:
        """The login handler must see it, so the user is told and can retry."""
        with pytest.raises(CredentialStorageError):
            store_login_session(index, {"cookies": _login_jar(), "origins": []})

    def test_username_enrichment_does_not_kill_sync(
        self, index: LibraryIndex, mocker: MockerFixture
    ) -> None:
        """All three sync entry points funnel through this before the walk.

        A raise here would abort the whole sync before any work happened, for
        the sake of a cosmetic username field. The in-memory session is still
        updated, so this run uses the right name.
        """
        session = {"cookies": [_cookie("identity")], "username": "old"}
        index.set_session("bandcamp", dict(session))
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.KeyringError("locked"),
        )

        _store_username_in_session("new", session, index)  # must not raise

        assert session["username"] == "new"
        stored = index.get_session("bandcamp")
        assert stored is not None and stored["username"] == "old"

    def test_prune_survives_storage_error(
        self, index: LibraryIndex, mocker: MockerFixture
    ) -> None:
        stored = {"cookies": _login_jar(), "origins": []}
        index.set_session("bandcamp", stored)
        mocker.patch(
            "kamp_core.library.keyring.set_password",
            side_effect=keyring.errors.KeyringError("locked"),
        )
        result = prune_stored_session(index, stored)
        assert result is not None
        assert _names(result) == set(AUTH_COOKIE_NAMES)
