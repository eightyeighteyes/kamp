"""Tests for tune_shifter.bandcamp."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tune_shifter.bandcamp import (
    BandcampAPIError,
    CookieError,
    _load_state,
    _save_state,
    _validate_session,
    mark_collection_synced,
    sync_new_purchases,
)
from tune_shifter.config import BandcampConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bc_config(tmp_path: Path) -> BandcampConfig:
    return BandcampConfig(
        username="testuser",
        cookie_file=None,
        format="mp3-v0",
        poll_interval_minutes=0,
    )


def _make_pagedata_html(blob: dict[str, Any]) -> str:
    import html as html_lib

    encoded = html_lib.escape(json.dumps(blob), quote=True)
    return f'<div id="pagedata" data-blob="{encoded}"></div>'


def _profile_html(fan_id: int = 12345) -> str:
    return _make_pagedata_html({"fan_data": {"fan_id": fan_id}})


def _collection_response(
    items: list[dict[str, Any]], last_token: str = ""
) -> dict[str, Any]:
    return {"items": items, "last_token": last_token}


def _item(
    sale_item_id: int,
    band: str = "Band",
    title: str = "Album",
) -> dict[str, Any]:
    return {
        "sale_item_type": "p",
        "sale_item_id": sale_item_id,
        "band_name": band,
        "item_title": title,
    }


def _download_links(items: list[dict[str, Any]]) -> dict[int, str]:
    """Fake collection-page DOM link extraction result."""
    return {
        item[
            "sale_item_id"
        ]: f"https://bandcamp.com/download?sitem_id={item['sale_item_id']}"
        for item in items
    }


def _make_session_file(tmp_path: Path) -> Path:
    """Create a fake Playwright storage_state with valid-looking session cookies."""
    future = int(time.time()) + 86400
    state = {
        "cookies": [
            {
                "name": "js_logged_in",
                "value": "1",
                "domain": ".bandcamp.com",
                "path": "/",
                "expires": future,
                "httpOnly": False,
                "secure": True,
            },
            {
                "name": "client_id",
                "value": "abc123",
                "domain": ".bandcamp.com",
                "path": "/",
                "expires": future,
                "httpOnly": False,
                "secure": True,
            },
        ],
        "origins": [],
    }
    sf = tmp_path / "session.json"
    sf.write_text(json.dumps(state))
    return sf


def _make_playwright_mock(
    collection_items: list[dict[str, Any]],
    fan_id: int = 12345,
) -> MagicMock:
    """Return a mock for `sync_playwright` serving both the collection API and DOM flows.

    ``page.evaluate`` is called for two distinct purposes:
    - Collection API fetch (async fetch JS) → returns ``_collection_response``
    - DOM link extraction (querySelectorAll JS) → returns ``_download_links``
    """
    page = MagicMock()
    page.content.return_value = _profile_html(fan_id)

    def evaluate_side_effect(js: str, args: Any = None) -> Any:
        if "querySelectorAll" in js:
            # DOM link extraction call
            return _download_links(collection_items)
        # Collection API fetch call
        return _collection_response(collection_items)

    page.evaluate.side_effect = evaluate_side_effect

    context = MagicMock()
    context.new_page.return_value = page

    browser = MagicMock()
    browser.new_context.return_value = context

    pw = MagicMock()
    pw.chromium.launch.return_value = browser
    pw.__enter__ = MagicMock(return_value=pw)
    pw.__exit__ = MagicMock(return_value=False)

    return MagicMock(return_value=pw)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


class TestState:
    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        assert _load_state(tmp_path / "nonexistent.json") == {}

    def test_round_trip(self, tmp_path: Path) -> None:
        f = tmp_path / "state.json"
        _save_state(f, {"123": 1234567890.0})
        assert _load_state(f) == {"123": 1234567890.0}

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "state.json"
        f.write_text("not json")
        assert _load_state(f) == {}


# ---------------------------------------------------------------------------
# _validate_session
# ---------------------------------------------------------------------------


class TestValidateSession:
    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        assert not _validate_session(tmp_path / "nonexistent.json")

    def test_corrupted_file_returns_false(self, tmp_path: Path) -> None:
        sf = tmp_path / "session.json"
        sf.write_text("not json")
        assert not _validate_session(sf)

    def test_missing_js_logged_in_returns_false(self, tmp_path: Path) -> None:
        sf = tmp_path / "session.json"
        sf.write_text(json.dumps({"cookies": [], "origins": []}))
        assert not _validate_session(sf)

    def test_expired_cookie_returns_false(self, tmp_path: Path) -> None:
        sf = tmp_path / "session.json"
        past = int(time.time()) - 1
        state = {
            "cookies": [{"name": "js_logged_in", "value": "1", "expires": past}],
            "origins": [],
        }
        sf.write_text(json.dumps(state))
        assert not _validate_session(sf)

    def test_session_cookie_expires_minus_one_is_valid(self, tmp_path: Path) -> None:
        """Playwright stores session cookies with expires=-1; these must be treated as valid."""
        sf = tmp_path / "session.json"
        state = {
            "cookies": [{"name": "js_logged_in", "value": "1", "expires": -1}],
            "origins": [],
        }
        sf.write_text(json.dumps(state))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"fan_id": 123}'
        with patch("requests.get", return_value=mock_resp):
            assert _validate_session(sf)

    def test_valid_cookies_but_api_401_returns_false(self, tmp_path: Path) -> None:
        sf = _make_session_file(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("requests.get", return_value=mock_resp):
            assert not _validate_session(sf)

    def test_valid_cookies_and_api_200_returns_true(self, tmp_path: Path) -> None:
        sf = _make_session_file(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"fan_id": 123}'
        with patch("requests.get", return_value=mock_resp):
            assert _validate_session(sf)

    def test_network_error_treated_as_valid(self, tmp_path: Path) -> None:
        """If the live API check fails with a network error, trust the cookies."""
        sf = _make_session_file(tmp_path)
        with patch("requests.get", side_effect=OSError("network error")):
            assert _validate_session(sf)


# ---------------------------------------------------------------------------
# sync_new_purchases
# ---------------------------------------------------------------------------


class TestSyncNewPurchases:
    def _run(
        self,
        tmp_path: Path,
        items: list[dict[str, Any]],
        state: dict[str, float] | None = None,
    ) -> list[Path]:
        staging = tmp_path / "staging"
        state_file = tmp_path / "state.json"
        session_file = _make_session_file(tmp_path)
        if state:
            _save_state(state_file, state)

        config = _bc_config(tmp_path)
        pw_mock = _make_playwright_mock(items)

        def fake_download(
            item: dict[str, Any],
            bc_config: BandcampConfig,
            staging_dir: Path,
            sf: Path,
        ) -> Path:
            staging_dir.mkdir(parents=True, exist_ok=True)
            path = staging_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake zip")
            return path

        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock),
            patch("tune_shifter.bandcamp._download_item", side_effect=fake_download),
        ):
            return sync_new_purchases(config, staging, state_file)

    def test_downloads_new_items(self, tmp_path: Path) -> None:
        items = [_item(1, "Artist A", "Album 1"), _item(2, "Artist B", "Album 2")]
        paths = self._run(tmp_path, items)
        assert len(paths) == 2
        assert all(p.suffix == ".zip" for p in paths)
        assert all(p.exists() for p in paths)

    def test_skips_known_items(self, tmp_path: Path) -> None:
        items = [_item(1), _item(2)]
        existing_state = {"1": time.time(), "2": time.time()}
        paths = self._run(tmp_path, items, state=existing_state)
        assert paths == []

    def test_downloads_only_new_items(self, tmp_path: Path) -> None:
        items = [_item(1), _item(2), _item(3)]
        existing_state = {"1": time.time()}
        paths = self._run(tmp_path, items, state=existing_state)
        assert len(paths) == 2

    def test_state_updated_after_download(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        staging = tmp_path / "staging"
        session_file = _make_session_file(tmp_path)
        config = _bc_config(tmp_path)
        items = [_item(99)]
        pw_mock = _make_playwright_mock(items)

        def fake_download(
            item: dict[str, Any], bc_config: BandcampConfig, staging_dir: Path, sf: Path
        ) -> Path:
            staging_dir.mkdir(parents=True, exist_ok=True)
            path = staging_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake")
            return path

        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock),
            patch("tune_shifter.bandcamp._download_item", side_effect=fake_download),
        ):
            sync_new_purchases(config, staging, state_file)

        assert "99" in _load_state(state_file)

    def test_state_persists_across_calls(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        staging = tmp_path / "staging"
        session_file = _make_session_file(tmp_path)
        config = _bc_config(tmp_path)
        call_num = 0

        def fake_download(
            item: dict[str, Any], bc_config: BandcampConfig, staging_dir: Path, sf: Path
        ) -> Path:
            nonlocal call_num
            call_num += 1
            staging_dir.mkdir(parents=True, exist_ok=True)
            path = staging_dir / f"{item['sale_item_id']}_{call_num}.zip"
            path.write_bytes(b"fake")
            return path

        pw_mock1 = _make_playwright_mock([_item(42)])
        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock1),
            patch("tune_shifter.bandcamp._download_item", side_effect=fake_download),
        ):
            sync_new_purchases(config, staging, state_file)

        pw_mock2 = _make_playwright_mock([_item(42)])
        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock2),
            patch("tune_shifter.bandcamp._download_item", side_effect=fake_download),
        ):
            paths = sync_new_purchases(config, staging, state_file)

        assert paths == []

    def test_skips_failed_download_continues_others(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        state_file = tmp_path / "state.json"
        session_file = _make_session_file(tmp_path)
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]
        pw_mock = _make_playwright_mock(items)
        call_num = 0

        def fake_download(
            item: dict[str, Any], bc_config: BandcampConfig, staging_dir: Path, sf: Path
        ) -> Path:
            nonlocal call_num
            call_num += 1
            if call_num == 1:
                raise BandcampAPIError("simulated failure")
            staging_dir.mkdir(parents=True, exist_ok=True)
            path = staging_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake")
            return path

        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock),
            patch("tune_shifter.bandcamp._download_item", side_effect=fake_download),
        ):
            paths = sync_new_purchases(config, staging, state_file)

        assert len(paths) == 1

    def test_warns_when_item_not_on_collection_page(self, tmp_path: Path) -> None:
        """Items missing from the DOM scrape should be warned and skipped."""
        staging = tmp_path / "staging"
        state_file = tmp_path / "state.json"
        session_file = _make_session_file(tmp_path)
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]

        # DOM scrape only finds item 2, not item 1
        page = MagicMock()
        page.content.return_value = _profile_html()

        def evaluate_side_effect(js: str, args: Any = None) -> Any:
            if "querySelectorAll" in js:
                return _download_links([_item(2)])  # item 1 absent
            return _collection_response(items)

        page.evaluate.side_effect = evaluate_side_effect
        context = MagicMock()
        context.new_page.return_value = page
        browser = MagicMock()
        browser.new_context.return_value = context
        pw = MagicMock()
        pw.chromium.launch.return_value = browser
        pw.__enter__ = MagicMock(return_value=pw)
        pw.__exit__ = MagicMock(return_value=False)
        pw_mock = MagicMock(return_value=pw)

        def fake_download(
            item: dict[str, Any], bc_config: BandcampConfig, staging_dir: Path, sf: Path
        ) -> Path:
            staging_dir.mkdir(parents=True, exist_ok=True)
            path = staging_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake")
            return path

        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock),
            patch("tune_shifter.bandcamp._download_item", side_effect=fake_download),
        ):
            paths = sync_new_purchases(config, staging, state_file)

        assert len(paths) == 1  # only item 2 downloaded


# ---------------------------------------------------------------------------
# mark_collection_synced
# ---------------------------------------------------------------------------


class TestMarkCollectionSynced:
    def test_marks_all_items_without_downloading(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        session_file = _make_session_file(tmp_path)
        config = _bc_config(tmp_path)
        items = [_item(1, "Artist A", "Album 1"), _item(2, "Artist B", "Album 2")]
        pw_mock = _make_playwright_mock(items)

        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock),
        ):
            count = mark_collection_synced(config, state_file)

        assert count == 2
        state = _load_state(state_file)
        assert "1" in state
        assert "2" in state
        assert not any(tmp_path.rglob("*.zip"))

    def test_skips_already_recorded_items(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        _save_state(state_file, {"1": 0.0})
        session_file = _make_session_file(tmp_path)
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]
        pw_mock = _make_playwright_mock(items)

        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock),
        ):
            count = mark_collection_synced(config, state_file)

        assert count == 1
        assert "2" in _load_state(state_file)

    def test_subsequent_sync_downloads_nothing(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        staging = tmp_path / "staging"
        session_file = _make_session_file(tmp_path)
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]

        pw_mock1 = _make_playwright_mock(items)
        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock1),
        ):
            mark_collection_synced(config, state_file)

        pw_mock2 = _make_playwright_mock(items)
        with (
            patch("tune_shifter.bandcamp._ensure_session", return_value=session_file),
            patch("tune_shifter.bandcamp.sync_playwright", pw_mock2),
            patch("tune_shifter.bandcamp._download_item"),
        ):
            paths = sync_new_purchases(config, staging, state_file)

        assert paths == []
