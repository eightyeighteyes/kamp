"""Tests for kamp_daemon.bandcamp."""

from __future__ import annotations

import html as html_lib
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_mock import MockerFixture

from kamp_daemon.bandcamp import (
    BandcampAPIError,
    CookieError,
    NeedsLoginError,
    _ProxyResponse,
    _download_file,
    _download_item,
    _ensure_session,
    _extract_pagedata,
    _fetch_collection,
    _get_cdn_url,
    _get_download_links,
    _get_fan_info,
    _make_requests_session,
    _paginate,
    _resolve_cdn_redirect,
    _session_from_cookie_file,
    _username_from_logout_cookie,
    _validate_session,
    download_single_album,
    fetch_album_art_bytes,
    fetch_album_tracks,
    fetch_stream_url,
    mark_collection_synced,
    refresh_stream_url,
    sync_collection_stream,
    sync_new_purchases,
)
from kamp_daemon.config import BandcampConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bc_config(tmp_path: Path) -> BandcampConfig:
    return BandcampConfig(
        format="mp3-v0",
        poll_interval_minutes=0,
    )


def _make_pagedata_html(blob: dict[str, Any]) -> str:
    encoded = html_lib.escape(json.dumps(blob), quote=True)
    return f'<div id="pagedata" data-blob="{encoded}"></div>'


def _collection_response(
    items: list[dict[str, Any]],
    last_token: str = "",
    redownload_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    resp: dict[str, Any] = {"items": items, "last_token": last_token}
    if redownload_urls is not None:
        resp["redownload_urls"] = redownload_urls
    return resp


def _redownload_urls_for(items: list[dict[str, Any]]) -> dict[str, str]:
    """Build a redownload_urls dict matching what the Bandcamp API returns."""
    return {
        f"p{item['sale_item_id']}": f"https://bandcamp.com/download?sitem_id={item['sale_item_id']}&sig=fake"
        for item in items
    }


def _item(
    sale_item_id: int,
    band: str = "Band",
    title: str = "Album",
    item_url: str = "",
    tralbum_id: int = 0,
    purchased: str = "01 Jan 2024 00:00:00 GMT",
) -> dict[str, Any]:
    return {
        "sale_item_type": "p",
        "sale_item_id": sale_item_id,
        "band_name": band,
        "item_title": title,
        "item_url": item_url
        or f"https://{band.lower().replace(' ', '')}.bandcamp.com/album/{title.lower().replace(' ', '-')}",
        "tralbum_id": tralbum_id or sale_item_id * 10,
        "purchased": purchased,
    }


def _stream_album_page_html(
    tracks: list[dict[str, Any]] | None = None,
    release_date: str = "01 Jan 2020 00:00:00 GMT",
) -> str:
    """Build fake album page HTML with data-tralbum for fetch_album_tracks tests."""
    import html as _html
    import json as _json

    if tracks is None:
        tracks = [
            {"title": f"Track {i}", "track_num": i, "artist": None, "duration": 200.0}
            for i in range(1, 4)
        ]
    tralbum = {"trackinfo": tracks, "album_release_date": release_date}
    encoded = _html.escape(_json.dumps(tralbum), quote=True)
    return f'<html><body data-tralbum="{encoded}"></body></html>'


def _collection_page_html(items: list[dict[str, Any]]) -> str:
    """Build fake collection page HTML with one download link per item."""
    links = " ".join(
        f'<a href="https://bandcamp.com/download?sitem_id={item["sale_item_id"]}">'
        for item in items
    )
    return f"<html><body>{links}</body></html>"


def _download_page_html(sale_item_id: int, fmt: str = "mp3-v0") -> str:
    """Build fake download page HTML with a pre-signed CDN URL in pagedata."""
    blob = {
        "download_items": [
            {
                "sale_id": sale_item_id,
                "title": "Album",
                "downloads": {
                    "mp3-v0": {
                        "url": f"https://popplers5.bandcamp.com/download/album?enc=mp3-v0&sitem_id={sale_item_id}",
                        "encoding_name": "mp3-v0",
                    },
                    "flac": {
                        "url": f"https://popplers5.bandcamp.com/download/album?enc=flac&sitem_id={sale_item_id}",
                        "encoding_name": "flac",
                    },
                },
            }
        ]
    }
    return _make_pagedata_html(blob)


def _make_session_data() -> dict[str, Any]:
    """Return a fake session dict with valid-looking cookies."""
    future = int(time.time()) + 86400
    return {
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


def _make_requests_mock(
    items: list[dict[str, Any]],
    fan_id: int = 12345,
) -> MagicMock:
    """Build a mock requests.Session that serves collection + download-page responses."""
    session = MagicMock()

    collection_resp = MagicMock()
    collection_resp.status_code = 200
    collection_resp.json.return_value = _collection_response(
        items, redownload_urls=_redownload_urls_for(items)
    )
    collection_resp.raise_for_status = MagicMock()

    hidden_resp = MagicMock()
    hidden_resp.status_code = 200
    hidden_resp.json.return_value = _collection_response([])
    hidden_resp.raise_for_status = MagicMock()

    collection_page_resp = MagicMock()
    collection_page_resp.text = _collection_page_html(items)
    collection_page_resp.status_code = 200
    collection_page_resp.raise_for_status = MagicMock()

    def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
        if "api/fan/2" in url:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"fan_id": fan_id, "collection_summary": {}}
            r.raise_for_status = MagicMock()
            return r
        if "/download?" in url:
            # Download page for an individual item
            sid = int(
                next(
                    p.split("=")[1]
                    for p in url.split("?")[1].split("&")
                    if p.startswith("sitem_id=")
                )
            )
            r = MagicMock()
            r.text = _download_page_html(sid)
            r.status_code = 200
            r.raise_for_status = MagicMock()
            return r
        return collection_page_resp

    def post_side_effect(url: str, **kwargs: Any) -> MagicMock:
        return hidden_resp if "hidden_items" in url else collection_resp

    session.get.side_effect = get_side_effect
    session.post.side_effect = post_side_effect
    return session


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


class TestState:
    """Placeholder — _load_state/_save_state removed in KAMP-381.
    Collection state tests are in test_library.py::TestBandcampCollection."""


# ---------------------------------------------------------------------------
# _validate_session
# ---------------------------------------------------------------------------


class TestValidateSession:
    def test_empty_cookies_returns_false(self) -> None:
        assert not _validate_session({"cookies": [], "origins": []})

    def test_missing_js_logged_in_returns_false(self) -> None:
        assert not _validate_session({"cookies": [], "origins": []})

    def test_expired_cookie_returns_false(self) -> None:
        past = int(time.time()) - 1
        data = {
            "cookies": [{"name": "js_logged_in", "value": "1", "expires": past}],
            "origins": [],
        }
        assert not _validate_session(data)

    def test_session_cookie_expires_minus_one_is_valid(self) -> None:
        """Session cookies with expires=-1 must be treated as valid."""
        data = {
            "cookies": [{"name": "js_logged_in", "value": "1", "expires": -1}],
            "origins": [],
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("kamp_daemon.bandcamp._requests.get", return_value=mock_resp):
            assert _validate_session(data)

    def test_valid_cookies_but_api_401_returns_false(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("kamp_daemon.bandcamp._requests.get", return_value=mock_resp):
            assert not _validate_session(_make_session_data())

    def test_valid_cookies_and_api_200_returns_true(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("kamp_daemon.bandcamp._requests.get", return_value=mock_resp):
            assert _validate_session(_make_session_data())

    def test_network_error_treated_as_valid(self) -> None:
        """If the live API check fails with a network error, trust the cookies."""
        with patch(
            "kamp_daemon.bandcamp._requests.get", side_effect=OSError("network error")
        ):
            assert _validate_session(_make_session_data())


# ---------------------------------------------------------------------------
# _extract_pagedata
# ---------------------------------------------------------------------------


class TestExtractPagedata:
    def test_extracts_json_blob(self) -> None:
        html = _make_pagedata_html({"fan_data": {"fan_id": 99}})
        blob = _extract_pagedata(html, "https://example.com")
        assert blob["fan_data"]["fan_id"] == 99

    def test_raises_when_missing(self) -> None:
        with pytest.raises(BandcampAPIError, match="Could not find pagedata"):
            _extract_pagedata("<html><body>no data here</body></html>", "https://x.com")

    def test_html_entities_unescaped(self) -> None:
        # Bandcamp HTML-escapes the JSON blob; quotes become &quot; etc.
        blob = {"key": "value with 'quotes' & ampersands"}
        html = _make_pagedata_html(blob)
        result = _extract_pagedata(html, "https://example.com")
        assert result["key"] == blob["key"]


# ---------------------------------------------------------------------------
# _get_fan_info
# ---------------------------------------------------------------------------


class TestUsernameFromLogoutCookie:
    def test_extracts_username_from_logout_cookie(self) -> None:
        import urllib.parse

        value = urllib.parse.quote('{"username":"tedd-e-terry"}')
        cookies = [{"name": "logout", "value": value, "domain": ".bandcamp.com"}]
        assert _username_from_logout_cookie(cookies) == "tedd-e-terry"

    def test_returns_empty_when_no_logout_cookie(self) -> None:
        cookies = [{"name": "js_logged_in", "value": "1", "domain": ".bandcamp.com"}]
        assert _username_from_logout_cookie(cookies) == ""

    def test_returns_empty_on_malformed_value(self) -> None:
        cookies = [{"name": "logout", "value": "not-json", "domain": ".bandcamp.com"}]
        assert _username_from_logout_cookie(cookies) == ""

    def test_returns_empty_when_username_key_absent(self) -> None:
        import urllib.parse

        value = urllib.parse.quote('{"other":"data"}')
        cookies = [{"name": "logout", "value": value, "domain": ".bandcamp.com"}]
        assert _username_from_logout_cookie(cookies) == ""


class TestGetFanInfo:
    def test_returns_fan_id_and_username_from_collection_summary(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "fan_id": 42,
            "username": "testuser",
            "collection_summary": {},
        }
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        fan_id, username = _get_fan_info(session)
        assert fan_id == 42
        assert username == "testuser"

    def test_falls_back_to_url_hints_subdomain(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "fan_id": 42,
            "username": "",
            "url_hints": {"subdomain": "testuser"},
        }
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        fan_id, username = _get_fan_info(session)
        assert fan_id == 42
        assert username == "testuser"

    def test_returns_empty_username_when_absent(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"fan_id": 42, "collection_summary": {}}
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        fan_id, username = _get_fan_info(session)
        assert fan_id == 42
        assert username == ""

    def test_raises_needs_login_on_401(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 401
        session.get.return_value = resp
        with pytest.raises(NeedsLoginError):
            _get_fan_info(session)

    def test_raises_needs_login_on_302(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 302
        session.get.return_value = resp
        with pytest.raises(NeedsLoginError):
            _get_fan_info(session)


# ---------------------------------------------------------------------------
# _get_download_links
# ---------------------------------------------------------------------------


class TestGetDownloadLinks:
    def test_finds_links_in_html(self) -> None:
        session = MagicMock()
        items = [_item(10), _item(20)]
        resp = MagicMock()
        resp.text = _collection_page_html(items)
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        links = _get_download_links("testuser", {10, 20}, session)

        assert 10 in links
        assert 20 in links
        assert "sitem_id=10" in links[10]
        assert "sitem_id=20" in links[20]

    def test_only_returns_requested_ids(self) -> None:
        session = MagicMock()
        items = [_item(10), _item(20), _item(30)]
        resp = MagicMock()
        resp.text = _collection_page_html(items)
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        links = _get_download_links("testuser", {10}, session)

        assert 10 in links
        assert 20 not in links
        assert 30 not in links

    def test_returns_empty_when_no_links_found(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.text = "<html><body>no links</body></html>"
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        assert _get_download_links("testuser", {99}, session) == {}


# ---------------------------------------------------------------------------
# _get_cdn_url
# ---------------------------------------------------------------------------


class TestGetCdnUrl:
    def test_returns_url_for_requested_format(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.text = _download_page_html(42, fmt="mp3-v0")
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        url = _get_cdn_url(
            "https://bandcamp.com/download?sitem_id=42", "mp3-v0", session
        )

        assert "enc=mp3-v0" in url
        assert "sitem_id=42" in url

    def test_raises_for_unknown_format(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.text = _download_page_html(42)
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        with pytest.raises(BandcampAPIError, match="not available"):
            _get_cdn_url("https://bandcamp.com/download?sitem_id=42", "wav", session)

    def test_raises_when_downloads_empty(self) -> None:
        """Item still being transcoded — no download URLs yet."""
        session = MagicMock()
        blob = {"download_items": [{"sale_id": 1, "title": "Album", "downloads": {}}]}
        resp = MagicMock()
        resp.text = _make_pagedata_html(blob)
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        with pytest.raises(BandcampAPIError, match="no download URLs"):
            _get_cdn_url("https://bandcamp.com/download?sitem_id=1", "mp3-v0", session)

    def test_raises_when_no_download_items(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.text = _make_pagedata_html({"other": "data"})
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp

        with pytest.raises(BandcampAPIError, match="No download_items"):
            _get_cdn_url("https://bandcamp.com/download?sitem_id=1", "mp3-v0", session)


# ---------------------------------------------------------------------------
# sync_new_purchases
# ---------------------------------------------------------------------------


class TestSyncNewPurchases:
    def _run(
        self,
        tmp_path: Path,
        items: list[dict[str, Any]],
        known_ids: list[str] | None = None,
    ) -> list[Path]:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        # get_collection_state returns {sale_item_id: mode}; known IDs are 'local'.
        index.get_collection_state.return_value = {
            k: "local" for k in (known_ids or [])
        }

        def fake_download(
            item: dict[str, Any],
            bc_config: BandcampConfig,
            watch_dir: Path,
            session: Any,
        ) -> Path:
            watch_dir.mkdir(parents=True, exist_ok=True)
            path = watch_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake zip")
            return path

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp._download_item", side_effect=fake_download),
        ):
            return sync_new_purchases(config, watch_folder, index)

    def test_downloads_new_items(self, tmp_path: Path) -> None:
        items = [_item(1, "Artist A", "Album 1"), _item(2, "Artist B", "Album 2")]
        paths = self._run(tmp_path, items)
        assert len(paths) == 2
        assert all(p.suffix == ".zip" for p in paths)
        assert all(p.exists() for p in paths)

    def test_skips_known_items(self, tmp_path: Path) -> None:
        items = [_item(1), _item(2)]
        paths = self._run(tmp_path, items, known_ids=["1", "2"])
        assert paths == []

    def test_downloads_only_new_items(self, tmp_path: Path) -> None:
        items = [_item(1), _item(2), _item(3)]
        paths = self._run(tmp_path, items, known_ids=["1"])
        assert len(paths) == 2

    def test_state_updated_after_download(self, tmp_path: Path) -> None:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        items = [_item(99)]
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}

        def fake_download(
            item: dict[str, Any],
            bc_config: BandcampConfig,
            watch_dir: Path,
            session: Any,
        ) -> Path:
            watch_dir.mkdir(parents=True, exist_ok=True)
            path = watch_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake")
            return path

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp._download_item", side_effect=fake_download),
        ):
            sync_new_purchases(config, watch_folder, index)

        # Verify the index was asked to record the downloaded item.
        index.upsert_collection_item.assert_called_once()
        call_kwargs = index.upsert_collection_item.call_args
        assert call_kwargs[0][0] == "99"
        assert call_kwargs[1]["mode"] == "local"

    def test_state_persists_across_calls(self, tmp_path: Path) -> None:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        call_num = 0

        def fake_download(
            item: dict[str, Any],
            bc_config: BandcampConfig,
            watch_dir: Path,
            session: Any,
        ) -> Path:
            nonlocal call_num
            call_num += 1
            watch_dir.mkdir(parents=True, exist_ok=True)
            path = watch_dir / f"{item['sale_item_id']}_{call_num}.zip"
            path.write_bytes(b"fake")
            return path

        # First call: item 42 is new — gets downloaded.
        index1 = MagicMock()
        index1.get_collection_state.return_value = {}
        mock1 = _make_requests_mock([_item(42)])
        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch("kamp_daemon.bandcamp._make_requests_session", return_value=mock1),
            patch("kamp_daemon.bandcamp._download_item", side_effect=fake_download),
        ):
            sync_new_purchases(config, watch_folder, index1)

        # Second call: item 42 is already local — nothing downloaded.
        index2 = MagicMock()
        index2.get_collection_state.return_value = {"42": "local"}
        mock2 = _make_requests_mock([_item(42)])
        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch("kamp_daemon.bandcamp._make_requests_session", return_value=mock2),
            patch("kamp_daemon.bandcamp._download_item", side_effect=fake_download),
        ):
            paths = sync_new_purchases(config, watch_folder, index2)

        assert paths == []

    def test_skips_failed_download_continues_others(self, tmp_path: Path) -> None:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}
        call_num = 0

        def fake_download(
            item: dict[str, Any],
            bc_config: BandcampConfig,
            watch_dir: Path,
            session: Any,
        ) -> Path:
            nonlocal call_num
            call_num += 1
            if call_num == 1:
                raise BandcampAPIError("simulated failure")
            watch_dir.mkdir(parents=True, exist_ok=True)
            path = watch_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake")
            return path

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp._download_item", side_effect=fake_download),
        ):
            paths = sync_new_purchases(config, watch_folder, index)

        assert len(paths) == 1

    def test_warns_when_item_missing_from_redownload_urls(self, tmp_path: Path) -> None:
        """Items absent from the API redownload_urls dict are warned and skipped."""
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]

        mock_session = MagicMock()

        summary_resp = MagicMock()
        summary_resp.status_code = 200
        summary_resp.json.return_value = {"fan_id": 12345, "collection_summary": {}}
        summary_resp.raise_for_status = MagicMock()

        # API returns redownload_url only for item 2, not item 1.
        collection_resp = MagicMock()
        collection_resp.status_code = 200
        collection_resp.json.return_value = _collection_response(
            items, redownload_urls=_redownload_urls_for([_item(2)])
        )
        collection_resp.raise_for_status = MagicMock()

        hidden_resp = MagicMock()
        hidden_resp.status_code = 200
        hidden_resp.json.return_value = _collection_response([])
        hidden_resp.raise_for_status = MagicMock()

        mock_session.get.side_effect = lambda url, **kw: summary_resp
        mock_session.post.side_effect = lambda url, **kw: (
            hidden_resp if "hidden_items" in url else collection_resp
        )

        def fake_download(
            item: dict[str, Any],
            bc_config: BandcampConfig,
            watch_dir: Path,
            session: Any,
        ) -> Path:
            watch_dir.mkdir(parents=True, exist_ok=True)
            path = watch_dir / f"{item['sale_item_id']}.zip"
            path.write_bytes(b"fake")
            return path

        index = MagicMock()
        index.get_collection_state.return_value = {}
        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp._download_item", side_effect=fake_download),
        ):
            paths = sync_new_purchases(config, watch_folder, index)

        assert len(paths) == 1  # only item 2 downloaded

    def test_refreshes_metadata_for_existing_items(self, tmp_path: Path) -> None:
        """Existing items get band_name/album_url/tralbum_id refreshed even if not downloaded."""
        items = [
            _item(
                1,
                "The Artist",
                "The Album",
                item_url="https://theartist.bandcamp.com/album/the-album",
                tralbum_id=42,
            )
        ]
        paths = self._run(tmp_path, items, known_ids=["1"])

        # No downloads — item was already known.
        assert paths == []

        # upsert_collection_item should have been called once for the metadata refresh.
        index = MagicMock()
        index.get_collection_state.return_value = {"1": "local"}

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session",
                return_value=_make_requests_mock(items),
            ),
        ):
            sync_new_purchases(_bc_config(tmp_path), tmp_path / "watch", index)

        index.upsert_collection_item.assert_called_once()
        call_kwargs = index.upsert_collection_item.call_args[1]
        assert call_kwargs["band_name"] == "The Artist"
        assert (
            call_kwargs["album_url"] == "https://theartist.bandcamp.com/album/the-album"
        )
        assert call_kwargs["tralbum_id"] == "42"
        assert call_kwargs["synced_at"] is None  # preserved via COALESCE

    def test_passes_purchased_as_added_at_on_download(self, tmp_path: Path) -> None:
        """Downloaded items get added_at set to the parsed purchase timestamp."""
        purchased_str = "15 Mar 2025 10:00:00 GMT"
        items = [_item(1, purchased=purchased_str)]
        watch_folder = tmp_path / "watch"
        index = MagicMock()
        index.get_collection_state.return_value = {}

        zip_path = watch_folder / "1.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(b"PK\x03\x04")

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session",
                return_value=_make_requests_mock(items),
            ),
            patch(
                "kamp_daemon.bandcamp._download_item",
                return_value=zip_path,
            ),
        ):
            sync_new_purchases(_bc_config(tmp_path), watch_folder, index)

        call_kwargs = index.upsert_collection_item.call_args[1]
        from kamp_daemon.bandcamp import _parse_purchased

        expected = _parse_purchased(purchased_str)
        assert call_kwargs["added_at"] == expected
        assert call_kwargs["added_at"] is not None

    def test_passes_purchased_as_added_at_on_refresh(self, tmp_path: Path) -> None:
        """Existing items refreshed during sync get added_at from the purchase date."""
        purchased_str = "20 Feb 2023 12:00:00 GMT"
        items = [_item(1, purchased=purchased_str)]
        index = MagicMock()
        index.get_collection_state.return_value = {"1": "local"}

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session",
                return_value=_make_requests_mock(items),
            ),
        ):
            sync_new_purchases(_bc_config(tmp_path), tmp_path / "watch", index)

        call_kwargs = index.upsert_collection_item.call_args[1]
        from kamp_daemon.bandcamp import _parse_purchased

        assert call_kwargs["added_at"] == _parse_purchased(purchased_str)


# ---------------------------------------------------------------------------
# mark_collection_synced
# ---------------------------------------------------------------------------


class TestMarkCollectionSynced:
    def test_marks_all_items_without_downloading(self, tmp_path: Path) -> None:
        config = _bc_config(tmp_path)
        items = [_item(1, "Artist A", "Album 1"), _item(2, "Artist B", "Album 2")]
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
        ):
            count = mark_collection_synced(config, index)

        assert count == 2
        assert index.upsert_collection_item.call_count == 2
        assert not any(tmp_path.rglob("*.zip"))

    def test_skips_already_recorded_items(self, tmp_path: Path) -> None:
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {"1": "local"}

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
        ):
            count = mark_collection_synced(config, index)

        assert count == 1
        # upsert called for both (to keep metadata current), but only 1 was newly marked
        assert index.upsert_collection_item.call_count == 2

    def test_subsequent_sync_downloads_nothing(self, tmp_path: Path) -> None:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        items = [_item(1), _item(2)]

        # mark_collection_synced: both items become 'local'
        index1 = MagicMock()
        index1.get_collection_state.return_value = {}
        mock1 = _make_requests_mock(items)
        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch("kamp_daemon.bandcamp._make_requests_session", return_value=mock1),
        ):
            mark_collection_synced(config, index1)

        # sync_new_purchases: state shows both already local → nothing downloaded
        index2 = MagicMock()
        index2.get_collection_state.return_value = {"1": "local", "2": "local"}
        mock2 = _make_requests_mock(items)
        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch("kamp_daemon.bandcamp._make_requests_session", return_value=mock2),
            patch("kamp_daemon.bandcamp._download_item"),
        ):
            paths = sync_new_purchases(config, watch_folder, index2)

        assert paths == []


# ---------------------------------------------------------------------------
# NeedsLoginError
# ---------------------------------------------------------------------------


class TestNeedsLoginError:
    def test_raised_when_no_session_in_db(self, tmp_path: Path) -> None:
        config = _bc_config(tmp_path)
        index = MagicMock()
        index.get_session.return_value = None
        with pytest.raises(NeedsLoginError):
            sync_new_purchases(config, tmp_path / "watch", index)

    def test_raised_when_session_expired(self, tmp_path: Path) -> None:
        config = _bc_config(tmp_path)
        past = int(time.time()) - 1
        expired_data = {
            "cookies": [{"name": "js_logged_in", "value": "1", "expires": past}]
        }
        index = MagicMock()
        index.get_session.return_value = expired_data
        with pytest.raises(NeedsLoginError):
            sync_new_purchases(config, tmp_path / "watch", index)


# ---------------------------------------------------------------------------
# _make_requests_session
# ---------------------------------------------------------------------------


class TestMakeRequestsSession:
    """Direct ``requests.Session`` path — exercised on macOS / Linux dev.

    Force ``sys.platform`` to ``"linux"`` so the path runs identically on
    every CI runner including Windows (where ``_needs_proxy_session`` would
    otherwise return ``_ProxySession`` and these assertions about
    ``session.cookies`` would no longer apply).  See KAMP-290.
    """

    @pytest.fixture(autouse=True)
    def force_direct_path(self, mocker: MockerFixture) -> None:
        mocker.patch("kamp_daemon.bandcamp.sys.platform", "linux")

    def test_loads_cookies_from_session_data(self) -> None:
        session = _make_requests_session(_make_session_data())
        assert session.cookies.get("js_logged_in", domain=".bandcamp.com") == "1"

    def test_user_agent_header_set(self) -> None:
        session = _make_requests_session(_make_session_data())
        assert "User-Agent" in session.headers

    def test_empty_cookies_list(self) -> None:
        session = _make_requests_session({"cookies": [], "origins": []})
        assert len(list(session.cookies)) == 0


# ---------------------------------------------------------------------------
# _ensure_session
# ---------------------------------------------------------------------------


class TestEnsureSession:
    def _mock_index(self, session_data: dict[str, Any] | None) -> MagicMock:
        index = MagicMock()
        index.get_session.return_value = session_data
        return index

    def test_returns_session_data_when_valid(self, tmp_path: Path) -> None:
        data = _make_session_data()
        index = self._mock_index(data)
        config = _bc_config(tmp_path)
        with patch("kamp_daemon.bandcamp._validate_session", return_value=True):
            result = _ensure_session(config, index)
        assert result == data

    def test_raises_and_clears_session_when_invalid(self, tmp_path: Path) -> None:
        data = _make_session_data()
        index = self._mock_index(data)
        config = _bc_config(tmp_path)
        with patch("kamp_daemon.bandcamp._validate_session", return_value=False):
            with pytest.raises(NeedsLoginError):
                _ensure_session(config, index)
        index.clear_session.assert_called_once_with("bandcamp")

    def test_raises_when_no_session_in_db(self, tmp_path: Path) -> None:
        index = self._mock_index(None)
        config = _bc_config(tmp_path)
        with pytest.raises(NeedsLoginError):
            _ensure_session(config, index)
        index.clear_session.assert_not_called()


# ---------------------------------------------------------------------------
# _session_from_cookie_file
# ---------------------------------------------------------------------------


class TestSessionFromCookieFile:
    def _netscape_line(self, name: str, value: str, expires: int = -1) -> str:
        exp = str(expires) if expires >= 0 else "0"
        return f".bandcamp.com\tTRUE\t/\tTRUE\t{exp}\t{name}\t{value}\n"

    def test_parses_valid_cookies(self, tmp_path: Path) -> None:
        future = int(time.time()) + 86400
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            self._netscape_line("js_logged_in", "1", future)
            + self._netscape_line("client_id", "abc123", future)
        )
        state = _session_from_cookie_file(cookie_file)
        names = {c["name"] for c in state["cookies"]}
        assert names == {"js_logged_in", "client_id"}

    def test_ignores_comment_and_blank_lines(self, tmp_path: Path) -> None:
        future = int(time.time()) + 86400
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            "\n" + self._netscape_line("js_logged_in", "1", future)
        )
        state = _session_from_cookie_file(cookie_file)
        assert len(state["cookies"]) == 1

    def test_ignores_non_bandcamp_cookies(self, tmp_path: Path) -> None:
        future = int(time.time()) + 86400
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            f".example.com\tTRUE\t/\tTRUE\t{future}\ttoken\tXXX\n"
            + self._netscape_line("js_logged_in", "1", future)
        )
        state = _session_from_cookie_file(cookie_file)
        assert all(
            c["domain"] == "bandcamp.com" or c["domain"].endswith(".bandcamp.com")
            for c in state["cookies"]
        )

    def test_rejects_domain_substring_bypass(self, tmp_path: Path) -> None:
        # evil-bandcamp.com and notbandcamp.com must not pass the domain check
        future = int(time.time()) + 86400
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            f"evil-bandcamp.com\tTRUE\t/\tTRUE\t{future}\ttoken\tEVIL\n"
            f"notbandcamp.com\tTRUE\t/\tTRUE\t{future}\ttoken\tEVIL\n"
            + self._netscape_line("js_logged_in", "1", future)
        )
        state = _session_from_cookie_file(cookie_file)
        assert len(state["cookies"]) == 1
        assert state["cookies"][0]["name"] == "js_logged_in"

    def test_raises_cookie_error_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(CookieError, match="Could not read"):
            _session_from_cookie_file(tmp_path / "nonexistent.txt")

    def test_raises_cookie_error_when_no_bandcamp_cookies(self, tmp_path: Path) -> None:
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("# empty\n")
        with pytest.raises(CookieError, match="No Bandcamp cookies"):
            _session_from_cookie_file(cookie_file)

    def test_handles_non_numeric_expires(self, tmp_path: Path) -> None:
        """Non-numeric expires value in cookies.txt should fall back to -1."""
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            ".bandcamp.com\tTRUE\t/\tTRUE\tnot-a-number\tjs_logged_in\t1\n"
        )
        state = _session_from_cookie_file(cookie_file)
        assert state["cookies"][0]["expires"] == -1.0


# ---------------------------------------------------------------------------
# _paginate (error and multi-page paths)
# ---------------------------------------------------------------------------


class TestPaginate:
    def _make_post_mock(self, pages: list[list[dict[str, Any]]]) -> MagicMock:
        """Return a mock post() that yields successive pages with redownload_urls."""
        responses = []
        for i, items in enumerate(pages):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            last_token = f"token_{i + 1}" if i < len(pages) - 1 else ""
            resp.json.return_value = _collection_response(
                items,
                last_token=last_token,
                redownload_urls=_redownload_urls_for(items),
            )
            responses.append(resp)
        session = MagicMock()
        session.post.side_effect = responses
        return session

    def test_single_page(self) -> None:
        items = [_item(1), _item(2)]
        session = self._make_post_mock([items])
        index = MagicMock()
        result = _paginate(
            "https://bandcamp.com/fancollection/1/collection_items", 123, session, index
        )
        assert [r["sale_item_id"] for r in result] == [1, 2]

    def test_multiple_pages(self) -> None:
        page1 = [_item(i) for i in range(1, 6)]  # 5 items — matches batch size
        page2 = [_item(6)]
        session = self._make_post_mock([page1, page2])
        index = MagicMock()
        with patch("kamp_daemon.bandcamp._COLLECTION_PAGE_BATCH", 5):
            result = _paginate(
                "https://bandcamp.com/fancollection/1/collection_items",
                123,
                session,
                index,
            )
        assert [r["sale_item_id"] for r in result] == [1, 2, 3, 4, 5, 6]

    def test_raises_on_session_expired_401(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 401
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"items": [], "last_token": ""}
        session.post.return_value = resp
        index = MagicMock()
        with pytest.raises(BandcampAPIError, match="session expired"):
            _paginate(
                "https://bandcamp.com/fancollection/1/collection_items",
                123,
                session,
                index,
            )
        index.clear_session.assert_called_once_with("bandcamp")

    def test_raises_on_api_error_field(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "error": True,
            "error_message": "bad request",
            "items": [],
        }
        session.post.return_value = resp
        index = MagicMock()
        with pytest.raises(BandcampAPIError, match="Collection API error"):
            _paginate(
                "https://bandcamp.com/fancollection/1/collection_items",
                123,
                session,
                index,
            )

    def test_embeds_redownload_url_from_api_response(self) -> None:
        """redownload_url is extracted from per-page redownload_urls and set on each item."""
        page1 = [_item(1), _item(2)]
        page2 = [_item(3)]
        session = self._make_post_mock([page1, page2])
        index = MagicMock()
        with patch("kamp_daemon.bandcamp._COLLECTION_PAGE_BATCH", 2):
            result = _paginate(
                "https://bandcamp.com/fancollection/1/collection_items",
                123,
                session,
                index,
            )
        for item in result:
            assert "redownload_url" in item
            assert f"sitem_id={item['sale_item_id']}" in item["redownload_url"]

    def test_items_without_redownload_url_are_unchanged(self) -> None:
        """Items absent from redownload_urls dict are left without redownload_url."""
        items = [_item(1), _item(2)]
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        # redownload_urls only has item 1
        resp.json.return_value = _collection_response(
            items,
            redownload_urls={"p1": "https://bandcamp.com/download?sitem_id=1&sig=x"},
        )
        session = MagicMock()
        session.post.return_value = resp
        index = MagicMock()
        result = _paginate(
            "https://bandcamp.com/fancollection/1/collection_items", 123, session, index
        )
        assert (
            result[0].get("redownload_url")
            == "https://bandcamp.com/download?sitem_id=1&sig=x"
        )
        assert "redownload_url" not in result[1]

    def test_breaks_when_last_token_missing(self) -> None:
        """If the server returns a full page but no last_token, stop paginating."""
        page = [_item(i) for i in range(1, 6)]
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"items": page, "last_token": ""}
        session.post.return_value = resp
        index = MagicMock()
        with patch("kamp_daemon.bandcamp._COLLECTION_PAGE_BATCH", 5):
            result = _paginate(
                "https://bandcamp.com/fancollection/1/collection_items",
                123,
                session,
                index,
            )
        assert len(result) == 5
        assert session.post.call_count == 1


# ---------------------------------------------------------------------------
# _fetch_collection deduplication
# ---------------------------------------------------------------------------


class TestFetchCollection:
    def test_deduplicates_items_across_collection_and_hidden(self) -> None:
        """Items appearing in both collection and hidden endpoints are returned once."""
        shared = _item(99)
        unique_hidden = _item(100)

        session = MagicMock()

        def post_side(url: str, **kw: Any) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "hidden_items" in url:
                resp.json.return_value = {
                    "items": [shared, unique_hidden],
                    "last_token": "",
                }
            else:
                resp.json.return_value = {"items": [shared], "last_token": ""}
            return resp

        session.post.side_effect = post_side
        index = MagicMock()
        result = _fetch_collection(12345, session, index)
        ids = [r["sale_item_id"] for r in result]
        assert ids.count(99) == 1
        assert 100 in ids


# ---------------------------------------------------------------------------
# _download_item and _download_file
# ---------------------------------------------------------------------------


class TestDownloadItem:
    _CDN = "https://popplers5.bandcamp.com/download/album?enc=mp3-v0"
    _ITEM = {
        "sale_item_id": 42,
        "band_name": "Test Band",
        "item_title": "Test Album",
        "redownload_url": "https://bandcamp.com/download?sitem_id=42",
    }

    def test_dev_mode_uses_authenticated_session(self, tmp_path: Path) -> None:
        """Dev mode passes the authenticated requests.Session to _download_file directly."""
        import requests as _req

        watch_folder = tmp_path / "watch"
        watch_folder.mkdir()
        session = _req.Session()
        calls: list[tuple[str, Any]] = []

        with patch("kamp_daemon.bandcamp._get_cdn_url", return_value=self._CDN):
            with patch("kamp_daemon.bandcamp._resolve_cdn_redirect") as mock_resolve:
                with patch(
                    "kamp_daemon.bandcamp._download_file",
                    side_effect=lambda url, dest, sess: (
                        calls.append((url, sess)),
                        dest.write_bytes(b"fake"),
                    ),
                ):
                    _download_item(
                        self._ITEM, _bc_config(tmp_path), watch_folder, session
                    )

        mock_resolve.assert_not_called()
        assert len(calls) == 1
        assert calls[0][0] == self._CDN
        assert calls[0][1] is session

    def test_frozen_mode_resolves_redirect_and_uses_plain_session(
        self, tmp_path: Path
    ) -> None:
        """Frozen mode calls _resolve_cdn_redirect and downloads with a plain session."""
        watch_folder = tmp_path / "watch"
        watch_folder.mkdir()
        frozen_session = MagicMock()  # not a requests.Session → frozen-mode branch
        final_url = "https://bcbits.com/download/album?token=abc"
        calls: list[tuple[str, Any]] = []

        with patch("kamp_daemon.bandcamp._get_cdn_url", return_value=self._CDN):
            with patch(
                "kamp_daemon.bandcamp._resolve_cdn_redirect", return_value=final_url
            ) as mock_resolve:
                with patch(
                    "kamp_daemon.bandcamp._download_file",
                    side_effect=lambda url, dest, sess: (
                        calls.append((url, sess)),
                        dest.write_bytes(b"fake"),
                    ),
                ):
                    _download_item(
                        self._ITEM, _bc_config(tmp_path), watch_folder, frozen_session
                    )

        mock_resolve.assert_called_once_with(self._CDN, frozen_session)
        assert len(calls) == 1
        assert calls[0][0] == final_url
        # dl_session is a fresh plain Session, not the proxy session
        import requests as _req

        assert isinstance(calls[0][1], _req.Session)
        assert calls[0][1] is not frozen_session

    def test_raises_when_no_redownload_url(self, tmp_path: Path) -> None:
        watch_folder = tmp_path / "watch"
        watch_folder.mkdir()
        item = {
            "sale_item_id": 42,
            "band_name": "Band",
            "item_title": "Album",
            "redownload_url": None,
        }
        with pytest.raises(BandcampAPIError, match="No redownload URL"):
            _download_item(item, _bc_config(tmp_path), watch_folder, MagicMock())

    def test_sanitises_unsafe_characters_in_filename(self, tmp_path: Path) -> None:
        watch_folder = tmp_path / "watch"
        watch_folder.mkdir()
        item = {
            "sale_item_id": 1,
            "band_name": 'Band/With:Slashes"',
            "item_title": "Album<>",
            "redownload_url": "https://bandcamp.com/download?sitem_id=1",
        }
        with patch(
            "kamp_daemon.bandcamp._get_cdn_url",
            return_value="https://cdn.example.com/f",
        ):
            with patch(
                "kamp_daemon.bandcamp._resolve_cdn_redirect",
                return_value="https://cdn.example.com/f",
            ):
                with patch(
                    "kamp_daemon.bandcamp._download_file",
                    side_effect=lambda url, dest, sess: dest.write_bytes(b"x"),
                ):
                    path = _download_item(
                        item, _bc_config(tmp_path), watch_folder, MagicMock()
                    )
        assert "/" not in path.name
        assert ":" not in path.name


_ZIP_MAGIC = b"PK\x03\x04"
_FAKE_ZIP = _ZIP_MAGIC + b"\x00" * 20


class TestDownloadFile:
    def _make_resp(
        self, chunks: list[bytes], content_type: str = "application/zip"
    ) -> MagicMock:
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.raise_for_status = MagicMock()
        resp.headers = {"Content-Type": content_type}
        resp.iter_content.return_value = chunks
        return resp

    def test_writes_chunked_response_to_dest(self, tmp_path: Path) -> None:
        dest = tmp_path / "album.zip"
        session = MagicMock()
        session.get.return_value = self._make_resp([_FAKE_ZIP])

        _download_file("https://cdn.example.com/f.zip", dest, session)

        assert dest.read_bytes() == _FAKE_ZIP
        session.get.assert_called_once_with(
            "https://cdn.example.com/f.zip",
            stream=True,
            timeout=300,
            allow_redirects=True,
        )

    def test_renames_from_part_file(self, tmp_path: Path) -> None:
        """Download writes to .part first, then renames — no partial file visible."""
        dest = tmp_path / "album.zip"
        session = MagicMock()
        session.get.return_value = self._make_resp([_FAKE_ZIP])

        _download_file("https://cdn.example.com/f.zip", dest, session)

        assert dest.exists()
        assert not (tmp_path / "album.zip.part").exists()

    def test_cleans_up_part_file_on_error(self, tmp_path: Path) -> None:
        """On download failure the .part file is removed."""
        dest = tmp_path / "album.zip"
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.headers = {}
        resp.raise_for_status.side_effect = RuntimeError("network error")
        session = MagicMock()
        session.get.return_value = resp

        with pytest.raises(RuntimeError):
            _download_file("https://cdn.example.com/f.zip", dest, session)

        assert not dest.exists()
        assert not (tmp_path / "album.zip.part").exists()

    def test_raises_when_cdn_returns_non_zip(self, tmp_path: Path) -> None:
        """Non-ZIP CDN response (e.g. HTML error page) raises BandcampAPIError."""
        dest = tmp_path / "album.zip"
        html_body = b"<html>Error: access denied</html>"
        session = MagicMock()
        session.get.return_value = self._make_resp(
            [html_body], content_type="text/html"
        )

        with pytest.raises(BandcampAPIError, match="not a ZIP"):
            _download_file("https://cdn.example.com/f.zip", dest, session)

        assert not dest.exists()
        assert not (tmp_path / "album.zip.part").exists()


# ---------------------------------------------------------------------------
# status_callback in sync_new_purchases
# ---------------------------------------------------------------------------


class TestSyncStatusCallback:
    def test_status_callback_called_per_item(self, tmp_path: Path) -> None:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        items = [_item(1, "Band A", "Album A"), _item(2, "Band B", "Album B")]
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}

        statuses: list[str] = []

        def fake_download(
            item: dict[str, Any],
            bc_config: BandcampConfig,
            watch_dir: Path,
            session: Any,
        ) -> Path:
            watch_dir.mkdir(parents=True, exist_ok=True)
            p = watch_dir / f"{item['sale_item_id']}.zip"
            p.write_bytes(b"x")
            return p

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp._download_item", side_effect=fake_download),
        ):
            sync_new_purchases(
                config, watch_folder, index, status_callback=statuses.append
            )

        assert len(statuses) == 2
        assert any("Band A" in s for s in statuses)
        assert any("Band B" in s for s in statuses)


# ---------------------------------------------------------------------------
# _is_frozen / _ProxySession
# ---------------------------------------------------------------------------


class TestIsFrozen:
    def test_returns_false_in_normal_interpreter(self) -> None:
        from kamp_daemon.bandcamp import _is_frozen

        assert _is_frozen() is False

    def test_returns_true_when_sys_frozen_is_set(self) -> None:
        import sys

        from kamp_daemon.bandcamp import _is_frozen

        with patch.object(sys, "frozen", True, create=True):
            assert _is_frozen() is True


class TestMakeRequestsSessionFrozen:
    def test_returns_proxy_session_when_frozen(self, mocker: MockerFixture) -> None:
        import sys

        from kamp_daemon.bandcamp import _ProxySession, _make_requests_session

        # Pin platform to linux so we are isolating the frozen-only signal.
        mocker.patch("kamp_daemon.bandcamp.sys.platform", "linux")
        with patch.object(sys, "frozen", True, create=True):
            sess = _make_requests_session({"cookies": []})

        assert isinstance(sess, _ProxySession)

    def test_returns_requests_session_when_not_frozen(
        self, mocker: MockerFixture
    ) -> None:
        import requests

        from kamp_daemon.bandcamp import _make_requests_session

        # Pin platform to linux so this exercises the dev/non-Windows path.
        mocker.patch("kamp_daemon.bandcamp.sys.platform", "linux")
        sess = _make_requests_session({"cookies": []})
        assert isinstance(sess, requests.Session)


class TestMakeRequestsSessionWindowsDev:
    """KAMP-290: Windows dev must use the proxy too, not bare requests.

    The Python 3.11 .venv on Windows ships an OpenSSL whose JA3/JA4
    fingerprint Cloudflare flags for ``bandcamp.com``, returning an HTML
    anti-bot interstitial instead of the JSON API body.  We force the same
    Electron proxy path used in the bundled app so the request goes out
    with Chromium's TLS handshake.  Linux / macOS dev are unaffected.
    """

    def test_windows_dev_returns_proxy_session(self, mocker: MockerFixture) -> None:
        from kamp_daemon.bandcamp import _ProxySession, _make_requests_session

        # sys.frozen is False (the default for an interpreter); we are only
        # toggling sys.platform here.  This is the dev-mode signal.
        mocker.patch("kamp_daemon.bandcamp.sys.platform", "win32")

        sess = _make_requests_session(_make_session_data())
        assert isinstance(sess, _ProxySession)

    def test_macos_dev_returns_direct_session(self, mocker: MockerFixture) -> None:
        import requests

        from kamp_daemon.bandcamp import _make_requests_session

        mocker.patch("kamp_daemon.bandcamp.sys.platform", "darwin")
        sess = _make_requests_session(_make_session_data())
        assert isinstance(sess, requests.Session)

    def test_linux_dev_returns_direct_session(self, mocker: MockerFixture) -> None:
        import requests

        from kamp_daemon.bandcamp import _make_requests_session

        mocker.patch("kamp_daemon.bandcamp.sys.platform", "linux")
        sess = _make_requests_session(_make_session_data())
        assert isinstance(sess, requests.Session)


class TestProxySession:
    """Unit tests for _ProxySession — mock the proxy-fetch HTTP endpoint."""

    def _make_proxy_response(
        self, status: int, body: str, content_type: str = "application/json"
    ) -> MagicMock:
        """Build a mock for the requests.post() call inside _ProxySession._fetch."""
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {
            "status": status,
            "body": body,
            "content_type": content_type,
        }
        return m

    def test_get_calls_proxy_fetch_endpoint(self) -> None:
        from kamp_daemon.bandcamp import _PROXY_FETCH_URL, _ProxySession

        sess = _ProxySession()
        mock_post = self._make_proxy_response(200, '{"fan_id": 7}')

        with patch(
            "kamp_daemon.bandcamp._requests.post", return_value=mock_post
        ) as patched:
            resp = sess.get(
                "https://bandcamp.com/api/fan/2/collection_summary", timeout=20
            )

        patched.assert_called_once()
        call_kwargs = patched.call_args
        assert call_kwargs[0][0] == _PROXY_FETCH_URL
        payload = call_kwargs[1]["json"]
        assert payload["url"] == "https://bandcamp.com/api/fan/2/collection_summary"
        assert payload["method"] == "GET"
        assert payload["body"] is None

        assert resp.status_code == 200
        assert resp.json() == {"fan_id": 7}
        assert resp.ok is True

    def test_post_sends_json_body(self) -> None:
        from kamp_daemon.bandcamp import _ProxySession

        sess = _ProxySession()
        mock_post = self._make_proxy_response(200, '{"items": []}')

        with patch(
            "kamp_daemon.bandcamp._requests.post", return_value=mock_post
        ) as patched:
            sess.post(
                "https://bandcamp.com/api/fancollection/1/collection_items",
                json={"fan_id": 99, "count": 20},
                timeout=30,
            )

        payload = patched.call_args[1]["json"]
        assert payload["method"] == "POST"
        assert '"fan_id": 99' in payload["body"]
        assert payload["headers"]["Content-Type"] == "application/json"

    def test_raise_for_status_on_error_response(self) -> None:
        import requests

        from kamp_daemon.bandcamp import _ProxySession

        sess = _ProxySession()
        mock_post = self._make_proxy_response(403, "Forbidden", "text/html")

        with patch("kamp_daemon.bandcamp._requests.post", return_value=mock_post):
            resp = sess.get("https://bandcamp.com/something")

        assert resp.ok is False
        with pytest.raises(requests.HTTPError):
            resp.raise_for_status()

    def test_user_agent_header_is_forwarded(self) -> None:
        from kamp_daemon.bandcamp import _UA, _ProxySession

        sess = _ProxySession()
        mock_post = self._make_proxy_response(200, "{}")

        with patch(
            "kamp_daemon.bandcamp._requests.post", return_value=mock_post
        ) as patched:
            sess.get("https://bandcamp.com/u/")

        payload = patched.call_args[1]["json"]
        assert payload["headers"]["User-Agent"] == _UA

    def test_includes_auth_token_header(self, tmp_path: Path) -> None:
        from kamp_daemon.bandcamp import _ProxySession

        token_file = tmp_path / ".token"
        token_file.write_text("mysecret")

        sess = _ProxySession()
        mock_post = self._make_proxy_response(200, "{}")

        with (
            patch(
                "kamp_daemon.bandcamp._requests.post", return_value=mock_post
            ) as patched,
            patch("kamp_daemon.bandcamp.token_path", return_value=token_file),
        ):
            sess.get("https://bandcamp.com/api/test")

        assert patched.call_args[1]["headers"] == {"X-Kamp-Token": "mysecret"}

    def test_no_auth_header_when_token_missing(self, tmp_path: Path) -> None:
        from kamp_daemon.bandcamp import _ProxySession

        missing = tmp_path / ".token"  # does not exist

        sess = _ProxySession()
        mock_post = self._make_proxy_response(200, "{}")

        with (
            patch(
                "kamp_daemon.bandcamp._requests.post", return_value=mock_post
            ) as patched,
            patch("kamp_daemon.bandcamp.token_path", return_value=missing),
        ):
            sess.get("https://bandcamp.com/api/test")

        assert patched.call_args[1]["headers"] is None

    def test_head_method_sends_head_to_proxy(self) -> None:
        from kamp_daemon.bandcamp import _PROXY_FETCH_URL, _ProxySession

        sess = _ProxySession()
        bcbits_url = "https://p4.bcbits.com/download/album/abc/mp3-v0/123?token=x"
        mock_post = MagicMock()
        mock_post.raise_for_status.return_value = None
        mock_post.json.return_value = {
            "status": 200,
            "body": "",
            "content_type": "application/octet-stream",
            "url": bcbits_url,
        }

        with patch(
            "kamp_daemon.bandcamp._requests.post", return_value=mock_post
        ) as patched:
            resp = sess.head(
                "https://popplers5.bandcamp.com/download/album?enc=mp3-v0&id=1"
            )

        payload = patched.call_args[1]["json"]
        assert payload["method"] == "HEAD"
        assert "popplers5.bandcamp.com" in payload["url"]
        assert patched.call_args[0][0] == _PROXY_FETCH_URL
        assert resp.url == bcbits_url


# ---------------------------------------------------------------------------
# _resolve_cdn_redirect
# ---------------------------------------------------------------------------


class TestResolveCdnRedirect:
    _popplers_url = (
        "https://popplers5.bandcamp.com/download/album?enc=mp3-v0&id=1&sig=abc"
    )
    _bcbits_url = (
        "https://p4.bcbits.com/download/album/hash/mp3-v0/1?id=1&sig=x&token=t"
    )

    def test_dev_mode_follows_redirect(self) -> None:
        """GET+stream follows the popplers5 redirect; final URL comes from resp.url."""
        import requests

        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.url = self._bcbits_url
        session = MagicMock(spec=requests.Session)
        session.get.return_value = mock_resp

        result = _resolve_cdn_redirect(self._popplers_url, session)

        session.get.assert_called_once_with(
            self._popplers_url, stream=True, allow_redirects=True, timeout=30
        )
        assert result == self._bcbits_url

    def test_frozen_mode_uses_proxy_head(self) -> None:
        """_ProxySession.head routes through Electron; final URL comes from resp.url."""
        from kamp_daemon.bandcamp import _ProxySession

        proxy_resp = _ProxyResponse(
            status_code=200, text="", content_type="", url=self._bcbits_url
        )
        sess = MagicMock(spec=_ProxySession)
        sess.head.return_value = proxy_resp

        result = _resolve_cdn_redirect(self._popplers_url, sess)

        sess.head.assert_called_once_with(self._popplers_url, timeout=30)
        assert result == self._bcbits_url

    def test_frozen_mode_falls_back_when_url_none(self) -> None:
        """Falls back to the original popplers5 URL when proxy returns url=None."""
        from kamp_daemon.bandcamp import _ProxySession

        proxy_resp = _ProxyResponse(status_code=200, text="", content_type="", url=None)
        sess = MagicMock(spec=_ProxySession)
        sess.head.return_value = proxy_resp

        result = _resolve_cdn_redirect(self._popplers_url, sess)

        assert result == self._popplers_url


# ---------------------------------------------------------------------------
# fetch_stream_url
# ---------------------------------------------------------------------------


def _album_page_html(tracks: list[dict]) -> str:
    """Build fake Bandcamp album page HTML with data-tralbum attribute."""
    import html as html_lib
    import json

    tralbum = {"trackinfo": tracks}
    encoded = html_lib.escape(json.dumps(tralbum))
    return f'<html><body><div data-tralbum="{encoded}"></div></body></html>'


class TestFetchStreamUrl:
    _album_url = "https://artist.bandcamp.com/album/my-album"

    def test_returns_stream_url_for_track(self) -> None:
        track_data = [
            {"track_num": 1, "file": {"mp3-128": "https://cdn.example.com/1.mp3"}},
            {"track_num": 2, "file": {"mp3-128": "https://cdn.example.com/2.mp3"}},
        ]
        html = _album_page_html(track_data)
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=200, text=html)
        sess.get.return_value.raise_for_status = MagicMock()

        url, expires_at = fetch_stream_url(self._album_url, 2, sess)

        assert url == "https://cdn.example.com/2.mp3"
        import time

        assert expires_at > time.time() + 86000

    def test_falls_back_to_mp3_v0(self) -> None:
        track_data = [
            {"track_num": 1, "file": {"mp3-v0": "https://cdn.example.com/v0.mp3"}},
        ]
        html = _album_page_html(track_data)
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=200, text=html)
        sess.get.return_value.raise_for_status = MagicMock()

        url, _ = fetch_stream_url(self._album_url, 1, sess)

        assert url == "https://cdn.example.com/v0.mp3"

    def test_prefers_mp3_v0_when_both_available(self) -> None:
        track_data = [
            {
                "track_num": 1,
                "file": {
                    "mp3-128": "https://cdn.example.com/128.mp3",
                    "mp3-v0": "https://cdn.example.com/v0.mp3",
                },
            },
        ]
        html = _album_page_html(track_data)
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=200, text=html)
        sess.get.return_value.raise_for_status = MagicMock()

        url, _ = fetch_stream_url(self._album_url, 1, sess)

        assert url == "https://cdn.example.com/v0.mp3"

    def test_raises_when_no_data_tralbum(self) -> None:
        sess = MagicMock()
        sess.get.return_value = MagicMock(
            status_code=200, text="<html>no data here</html>"
        )
        sess.get.return_value.raise_for_status = MagicMock()

        with pytest.raises(BandcampAPIError, match="no data-tralbum"):
            fetch_stream_url(self._album_url, 1, sess)

    def test_raises_when_track_not_found(self) -> None:
        track_data = [
            {"track_num": 1, "file": {"mp3-128": "https://cdn.example.com/1.mp3"}}
        ]
        html = _album_page_html(track_data)
        sess = MagicMock()
        sess.get.return_value = MagicMock(status_code=200, text=html)
        sess.get.return_value.raise_for_status = MagicMock()

        with pytest.raises(BandcampAPIError, match="track_num=99 not found"):
            fetch_stream_url(self._album_url, 99, sess)


class TestMarkCollectionSyncedStoresAlbumUrl:
    """mark_collection_synced now passes album_url and tralbum_id from the API response."""

    def test_mark_collection_synced_stores_album_url(self, tmp_path: Path) -> None:
        config = _bc_config(tmp_path)
        items = [
            _item(
                1,
                "Artist",
                "Album",
                item_url="https://artist.bandcamp.com/album/album",
                tralbum_id=100,
            )
        ]
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
        ):
            mark_collection_synced(config, index)

        call_kwargs = index.upsert_collection_item.call_args[1]
        assert call_kwargs["album_url"] == "https://artist.bandcamp.com/album/album"


# ---------------------------------------------------------------------------
# _parse_purchased
# ---------------------------------------------------------------------------


class TestParsePurchased:
    def test_parses_gmt_string(self) -> None:
        from kamp_daemon.bandcamp import _parse_purchased

        ts = _parse_purchased("01 Jan 2024 00:00:00 GMT")
        assert ts is not None
        assert abs(ts - 1704067200.0) < 2  # 2024-01-01 UTC

    def test_returns_none_for_none(self) -> None:
        from kamp_daemon.bandcamp import _parse_purchased

        assert _parse_purchased(None) is None

    def test_returns_none_for_garbage(self) -> None:
        from kamp_daemon.bandcamp import _parse_purchased

        assert _parse_purchased("not a date") is None


# ---------------------------------------------------------------------------
# mark_collection_synced — added_at suppression
# ---------------------------------------------------------------------------


class TestMarkCollectionSyncedAddedAt:
    """mark_collection_synced must pass added_at=0 so items never appear as new arrivals."""

    def test_passes_added_at_zero(self, tmp_path: Path) -> None:
        config = _bc_config(tmp_path)
        items = [_item(1, purchased="01 Jan 2024 00:00:00 GMT")]
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
        ):
            mark_collection_synced(config, index)

        call_kwargs = index.upsert_collection_item.call_args[1]
        assert call_kwargs["added_at"] == 0


# ---------------------------------------------------------------------------
# fetch_album_art_bytes
# ---------------------------------------------------------------------------


def _tralbum_html(art_id: int) -> str:
    payload = html_lib.escape(json.dumps({"id": 99, "art_id": art_id, "tracks": []}))
    return f'<html><div data-tralbum="{payload}"></div></html>'


class TestFetchAlbumArtBytes:
    """fetch_album_art_bytes downloads and returns raw JPEG art bytes."""

    _SESSION_DATA: dict[str, Any] = {"cookies": []}

    def test_returns_jpeg_on_success(self) -> None:
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        album_page = MagicMock()
        album_page.raise_for_status = MagicMock()
        album_page.text = _tralbum_html(art_id=42424242)

        cdn_resp = MagicMock()
        cdn_resp.raise_for_status = MagicMock()
        cdn_resp.content = jpeg

        with (
            patch(
                "kamp_daemon.bandcamp._make_requests_session"
            ) as mock_session_builder,
            patch("kamp_daemon.bandcamp._requests.get", return_value=cdn_resp),
        ):
            mock_session = MagicMock()
            mock_session.get.return_value = album_page
            mock_session_builder.return_value = mock_session

            result = fetch_album_art_bytes(
                "https://artist.bandcamp.com/album/foo", self._SESSION_DATA
            )

        assert result == jpeg
        mock_session.get.assert_called_once_with(
            "https://artist.bandcamp.com/album/foo", timeout=30
        )

    def test_returns_none_on_missing_data_tralbum(self) -> None:
        page = MagicMock()
        page.raise_for_status = MagicMock()
        page.text = "<html>no tralbum here</html>"

        with patch("kamp_daemon.bandcamp._make_requests_session") as mock_sb:
            mock_sess = MagicMock()
            mock_sess.get.return_value = page
            mock_sb.return_value = mock_sess

            result = fetch_album_art_bytes(
                "https://artist.bandcamp.com/album/foo", self._SESSION_DATA
            )

        assert result is None

    def test_returns_none_on_missing_art_id(self) -> None:
        payload = html_lib.escape(json.dumps({"id": 99, "tracks": []}))
        html = f'<html><div data-tralbum="{payload}"></div></html>'
        page = MagicMock()
        page.raise_for_status = MagicMock()
        page.text = html

        with patch("kamp_daemon.bandcamp._make_requests_session") as mock_sb:
            mock_sess = MagicMock()
            mock_sess.get.return_value = page
            mock_sb.return_value = mock_sess

            result = fetch_album_art_bytes(
                "https://artist.bandcamp.com/album/foo", self._SESSION_DATA
            )

        assert result is None

    def test_returns_none_on_http_error(self) -> None:
        import requests as _req

        with patch("kamp_daemon.bandcamp._make_requests_session") as mock_sb:
            mock_sess = MagicMock()
            mock_sess.get.side_effect = _req.HTTPError("500 Server Error")
            mock_sb.return_value = mock_sess

            result = fetch_album_art_bytes(
                "https://artist.bandcamp.com/album/foo", self._SESSION_DATA
            )

        assert result is None

    def test_cdn_url_uses_art_id_from_tralbum(self) -> None:
        jpeg = b"\xff\xd8\xff"
        page = MagicMock()
        page.raise_for_status = MagicMock()
        page.text = _tralbum_html(art_id=777888999)

        cdn_resp = MagicMock()
        cdn_resp.raise_for_status = MagicMock()
        cdn_resp.content = jpeg

        with (
            patch("kamp_daemon.bandcamp._make_requests_session") as mock_sb,
            patch(
                "kamp_daemon.bandcamp._requests.get", return_value=cdn_resp
            ) as mock_get,
        ):
            mock_sess = MagicMock()
            mock_sess.get.return_value = page
            mock_sb.return_value = mock_sess

            result = fetch_album_art_bytes(
                "https://artist.bandcamp.com/album/foo", self._SESSION_DATA
            )

        assert result == jpeg
        # Verify CDN URL is constructed from art_id (not tralbum id=99).
        mock_get.assert_called_once_with(
            "https://f4.bcbits.com/img/a777888999_16.jpg", timeout=30
        )


# ---------------------------------------------------------------------------
# refresh_stream_url
# ---------------------------------------------------------------------------


class TestRefreshStreamUrl:
    _album_url = "https://artist.bandcamp.com/album/my-album"
    _session_data: dict = {"cookies": []}

    def test_returns_tuple_on_success(self) -> None:
        """Returns (url, expires_at) when fetch_stream_url succeeds."""
        expected = ("https://cdn.bcbits.com/stream/track.mp3", 9999999.0)
        with (
            patch("kamp_daemon.bandcamp._make_requests_session"),
            patch(
                "kamp_daemon.bandcamp.fetch_stream_url", return_value=expected
            ) as mock_fetch,
        ):
            result = refresh_stream_url(self._album_url, 3, self._session_data)

        assert result == expected
        mock_fetch.assert_called_once()

    def test_returns_none_on_exception(self) -> None:
        """Returns None (never raises) when fetch_stream_url raises."""
        with (
            patch("kamp_daemon.bandcamp._make_requests_session"),
            patch(
                "kamp_daemon.bandcamp.fetch_stream_url",
                side_effect=BandcampAPIError("not found"),
            ),
        ):
            result = refresh_stream_url(self._album_url, 99, self._session_data)

        assert result is None


class TestSyncCollectionStream:
    """Tests for sync_collection_stream — album-level collection indexing."""

    def _run(
        self,
        tmp_path: Path,
        items: list[dict[str, Any]],
        *,
        already_have_tracks: bool = True,
        fake_tracks: list[Any] | None = None,
        existing_state: dict[str, str] | None = None,
    ) -> tuple[tuple[int, int], MagicMock]:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = existing_state or {}
        # By default pretend tracks already exist so fetch_album_tracks is skipped.
        index.has_remote_album_tracks.return_value = already_have_tracks

        fake_tracks = fake_tracks or []
        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp.fetch_album_tracks", return_value=fake_tracks),
        ):
            result = sync_collection_stream(config, watch_folder, index)

        return result, index

    def test_upserts_all_items_as_remote(self, tmp_path: Path) -> None:
        items = [_item(1, "Artist A", "Album 1"), _item(2, "Artist B", "Album 2")]
        (album_count, _track_count), index = self._run(tmp_path, items)
        assert album_count == 2
        calls = index.upsert_collection_item.call_args_list
        assert len(calls) == 2
        assert all(c[1]["mode"] == "remote" for c in calls)

    def test_returns_album_count(self, tmp_path: Path) -> None:
        items = [_item(10), _item(20), _item(30)]
        (album_count, _), _ = self._run(tmp_path, items)
        assert album_count == 3

    def test_fetches_tracks_for_new_albums(self, tmp_path: Path) -> None:
        """Albums not yet in tracks table trigger a fetch_album_tracks call."""
        from kamp_core.library import Track
        from pathlib import Path as _Path

        fake_track = Track(
            file_path=_Path("bandcamp://1/1"),
            title="T",
            artist="A",
            album_artist="A",
            album="Album",
            year="2020",
            track_number=1,
            disc_number=1,
            ext="mp3",
            embedded_art=False,
            mb_release_id="",
            mb_recording_id="",
            source="bandcamp",
        )
        items = [_item(1)]
        (album_count, track_count), index = self._run(
            tmp_path, items, already_have_tracks=False, fake_tracks=[fake_track]
        )
        assert album_count == 1
        assert track_count == 1
        index.upsert_many.assert_called_once()

    def test_skips_track_fetch_for_existing_albums(self, tmp_path: Path) -> None:
        """Albums already in tracks table do not trigger fetch_album_tracks."""
        items = [_item(1), _item(2)]
        (_counts, _), index = self._run(tmp_path, items, already_have_tracks=True)
        index.upsert_many.assert_not_called()

    def test_re_upserts_already_remote_item(self, tmp_path: Path) -> None:
        """Idempotent: collection row is always refreshed even if tracks exist."""
        items = [_item(99)]
        (_counts, _), index = self._run(tmp_path, items, already_have_tracks=True)
        index.upsert_collection_item.assert_called_once()
        assert index.upsert_collection_item.call_args[1]["mode"] == "remote"

    def test_skips_local_mode_items(self, tmp_path: Path) -> None:
        """Items already mode='local' are not overwritten with mode='remote'."""
        items = [
            _item(1, "Downloaded Band", "Downloaded Album"),
            _item(2, "New Band", "New Album"),
        ]
        # Item 1 was already downloaded; item 2 is new.
        (album_count, _), index = self._run(
            tmp_path,
            items,
            existing_state={"1": "local"},
        )
        assert album_count == 1  # only the new item counted
        calls = index.upsert_collection_item.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "2"  # only item 2 upserted

    def test_no_files_downloaded(self, tmp_path: Path) -> None:
        """Stream sync must not touch the watch folder."""
        items = [_item(5), _item(6)]
        self._run(tmp_path, items)
        assert not (tmp_path / "watch").exists()

    def test_warns_and_continues_on_fetch_error(self, tmp_path: Path) -> None:
        """A failed fetch_album_tracks is logged as a warning; other albums continue."""
        items = [_item(1), _item(2)]
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}
        index.has_remote_album_tracks.return_value = False

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch(
                "kamp_daemon.bandcamp.fetch_album_tracks",
                side_effect=BandcampAPIError("rate limited"),
            ),
        ):
            album_count, track_count = sync_collection_stream(
                config, watch_folder, index
            )

        assert album_count == 2  # both albums counted in bandcamp_collection
        assert track_count == 0  # no tracks indexed (fetch failed)

    def test_batch_indexed_callback_fires_per_new_batch(self, tmp_path: Path) -> None:
        """batch_indexed_callback fires once per album batch that has new tracks."""
        from kamp_core.library import Track
        from pathlib import Path as _Path

        def _fake_track(n: int) -> Track:
            return Track(
                file_path=_Path(f"bandcamp://1/{n}"),
                title=f"Track {n}",
                artist="A",
                album_artist="A",
                album="Album",
                year="2020",
                track_number=n,
                disc_number=1,
                ext="mp3",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
                source="bandcamp",
            )

        items = [_item(1), _item(2), _item(3)]
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}
        index.has_remote_album_tracks.return_value = False

        fired: list[bool] = []

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch(
                "kamp_daemon.bandcamp.fetch_album_tracks",
                side_effect=[
                    [_fake_track(1)],
                    [_fake_track(2)],
                    [_fake_track(3)],
                ],
            ),
        ):
            sync_collection_stream(
                config,
                watch_folder,
                index,
                batch_indexed_callback=lambda: fired.append(True),
            )

        assert len(fired) == 3  # one call per album with new tracks

    def test_batch_indexed_callback_not_called_when_no_new_tracks(
        self, tmp_path: Path
    ) -> None:
        """batch_indexed_callback is not called when albums already have tracks."""
        items = [_item(1), _item(2)]
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}
        # Tracks already exist — fetch_album_tracks path is skipped entirely.
        index.has_remote_album_tracks.return_value = True

        fired: list[bool] = []

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp.fetch_album_tracks", return_value=[]),
        ):
            sync_collection_stream(
                config,
                watch_folder,
                index,
                batch_indexed_callback=lambda: fired.append(True),
            )

        assert fired == []

    def test_passes_purchased_as_added_at(self, tmp_path: Path) -> None:
        """sync_collection_stream passes the parsed purchase date as added_at."""
        purchased_str = "10 Jun 2023 08:00:00 GMT"
        items = [_item(1, purchased=purchased_str)]
        _result, index = self._run(tmp_path, items)

        call_kwargs = index.upsert_collection_item.call_args[1]
        from kamp_daemon.bandcamp import _parse_purchased

        assert call_kwargs["added_at"] == _parse_purchased(purchased_str)
        assert call_kwargs["added_at"] is not None

    def test_calls_update_remote_track_date_added(self, tmp_path: Path) -> None:
        """sync_collection_stream corrects existing remote tracks' date_added."""
        purchased_str = "10 Jun 2023 08:00:00 GMT"
        items = [_item(1, purchased=purchased_str)]
        _result, index = self._run(tmp_path, items)

        from kamp_daemon.bandcamp import _parse_purchased

        index.update_remote_track_date_added.assert_called_once_with(
            "1", _parse_purchased(purchased_str)
        )

    def test_skips_update_when_purchased_missing(self, tmp_path: Path) -> None:
        """Items without a purchased field don't call update_remote_track_date_added."""
        item = _item(1)
        item.pop("purchased")
        _result, index = self._run(tmp_path, [item])

        index.update_remote_track_date_added.assert_not_called()


class TestStreamSyncArtPrefetch:
    """Art prefetch behaviour in sync_collection_stream."""

    def _run_with_art(
        self,
        tmp_path: Path,
        items: list[dict[str, Any]],
        art_cache_dir: Path | None,
        mock_art: bytes | None = b"\xff\xd8\xff" * 10,
    ) -> MagicMock:
        watch_folder = tmp_path / "watch"
        config = _bc_config(tmp_path)
        mock_session = _make_requests_mock(items)
        index = MagicMock()
        index.get_collection_state.return_value = {}
        index.has_remote_album_tracks.return_value = True

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=mock_session
            ),
            patch("kamp_daemon.bandcamp.fetch_album_tracks", return_value=[]),
            patch(
                "kamp_daemon.bandcamp.fetch_album_art_bytes", return_value=mock_art
            ) as mock_fetch_art,
        ):
            sync_collection_stream(
                config, watch_folder, index, art_cache_dir=art_cache_dir
            )
            return mock_fetch_art

    def test_art_prefetch_writes_cache(self, tmp_path: Path) -> None:
        """When art_cache_dir is provided and no cache file exists, art is fetched and written."""
        art_cache = tmp_path / "art_cache"
        items = [_item(1, tralbum_id=10)]
        mock_fetch = self._run_with_art(tmp_path, items, art_cache_dir=art_cache)
        mock_fetch.assert_called_once()
        assert (art_cache / "10.jpg").exists()

    def test_art_prefetch_skips_cached(self, tmp_path: Path) -> None:
        """Albums whose cache file already exists do not trigger a fetch."""
        art_cache = tmp_path / "art_cache"
        art_cache.mkdir()
        (art_cache / "10.jpg").write_bytes(b"\xff\xd8\xff")
        items = [_item(1, tralbum_id=10)]
        mock_fetch = self._run_with_art(tmp_path, items, art_cache_dir=art_cache)
        mock_fetch.assert_not_called()

    def test_art_prefetch_tolerates_failure(self, tmp_path: Path) -> None:
        """A None return from fetch_album_art_bytes does not raise; sync completes normally."""
        art_cache = tmp_path / "art_cache"
        items = [_item(1, tralbum_id=10), _item(2, tralbum_id=20)]
        mock_fetch = self._run_with_art(
            tmp_path, items, art_cache_dir=art_cache, mock_art=None
        )
        # Both albums attempted; neither succeeded; no files written; no exception.
        assert mock_fetch.call_count == 2
        assert not any(art_cache.glob("*.jpg")) if art_cache.exists() else True

    def test_art_prefetch_skipped_without_cache_dir(self, tmp_path: Path) -> None:
        """When art_cache_dir is None (default), fetch_album_art_bytes is never called."""
        items = [_item(1), _item(2)]
        mock_fetch = self._run_with_art(tmp_path, items, art_cache_dir=None)
        mock_fetch.assert_not_called()

    def test_art_cache_key_uses_sid_fallback(self, tmp_path: Path) -> None:
        """When tralbum_id is empty the cache key falls back to sid_<sale_item_id>."""
        art_cache = tmp_path / "art_cache"
        # Empty tralbum_id (as stored in DB when absent) triggers the fallback.
        item = {
            "sale_item_type": "p",
            "sale_item_id": 7,
            "band_name": "Band",
            "item_title": "Album",
            "item_url": "https://band.bandcamp.com/album/album",
            "tralbum_id": "",
        }
        self._run_with_art(tmp_path, [item], art_cache_dir=art_cache)
        assert (art_cache / "sid_7.jpg").exists()


class TestFetchAlbumTracks:
    """Tests for fetch_album_tracks — parses data-tralbum into Track objects."""

    def _make_session(self, html: str) -> MagicMock:
        session = MagicMock()
        resp = MagicMock()
        resp.text = html
        resp.raise_for_status = MagicMock()
        session.get.return_value = resp
        return session

    def test_returns_tracks_for_each_trackinfo_entry(self) -> None:
        tracks = [
            {"title": "Song One", "track_num": 1, "artist": None, "duration": 200.0},
            {"title": "Song Two", "track_num": 2, "artist": None, "duration": 180.0},
        ]
        html = _stream_album_page_html(tracks=tracks)
        session = self._make_session(html)

        result = fetch_album_tracks(
            "https://band.bandcamp.com/album/x", 12345, "Band", "Album", session
        )

        assert len(result) == 2
        assert result[0].title == "Song One"
        assert result[0].track_number == 1
        assert result[1].title == "Song Two"
        assert result[1].track_number == 2

    def test_source_is_bandcamp(self) -> None:
        html = _stream_album_page_html()
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 1, "B", "A", self._make_session(html)
        )
        assert all(t.source == "bandcamp" for t in result)

    def test_no_stream_url(self) -> None:
        html = _stream_album_page_html()
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 1, "B", "A", self._make_session(html)
        )
        assert all(t.stream_url is None for t in result)

    def test_date_added_defaults_to_current_time(self) -> None:
        """Without an explicit date_added, tracks default to the current time."""
        before = time.time()
        html = _stream_album_page_html()
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 1, "B", "A", self._make_session(html)
        )
        after = time.time()
        assert all(t.date_added is not None for t in result)
        assert all(before <= t.date_added <= after for t in result)  # type: ignore[operator]

    def test_date_added_uses_provided_value(self) -> None:
        """A caller-supplied date_added is used verbatim on every track."""
        html = _stream_album_page_html()
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y",
            1,
            "B",
            "A",
            self._make_session(html),
            date_added=12345.0,
        )
        assert all(t.date_added == 12345.0 for t in result)

    def test_file_path_contains_sale_item_id_and_track_num(self) -> None:
        tracks = [{"title": "T", "track_num": 3, "artist": None}]
        html = _stream_album_page_html(tracks=tracks)
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 999, "B", "A", self._make_session(html)
        )
        assert len(result) == 1
        assert "999" in str(result[0].file_path)
        assert "3" in str(result[0].file_path)

    def test_year_extracted_from_release_date(self) -> None:
        html = _stream_album_page_html(release_date="15 Mar 2018 00:00:00 GMT")
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 1, "B", "A", self._make_session(html)
        )
        assert all(t.year == "2018" for t in result)

    def test_per_track_artist_used_when_present(self) -> None:
        tracks = [{"title": "T", "track_num": 1, "artist": "Guest Artist"}]
        html = _stream_album_page_html(tracks=tracks)
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 1, "Band", "A", self._make_session(html)
        )
        assert result[0].artist == "Guest Artist"
        assert result[0].album_artist == "Band"

    def test_falls_back_to_band_name_when_no_per_track_artist(self) -> None:
        tracks = [{"title": "T", "track_num": 1, "artist": None}]
        html = _stream_album_page_html(tracks=tracks)
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 1, "Band", "A", self._make_session(html)
        )
        assert result[0].artist == "Band"

    def test_raises_on_missing_data_tralbum(self) -> None:
        session = self._make_session("<html><body>no tralbum here</body></html>")
        with pytest.raises(BandcampAPIError, match="no data-tralbum"):
            fetch_album_tracks("https://x.bandcamp.com/album/y", 1, "B", "A", session)

    def test_skips_tracks_without_track_num(self) -> None:
        tracks = [
            {"title": "Good", "track_num": 1, "artist": None},
            {"title": "Hidden", "track_num": None, "artist": None},
        ]
        html = _stream_album_page_html(tracks=tracks)
        result = fetch_album_tracks(
            "https://x.bandcamp.com/album/y", 1, "B", "A", self._make_session(html)
        )
        assert len(result) == 1
        assert result[0].title == "Good"


class TestDownloadSingleAlbum:
    """Tests for download_single_album()."""

    def _collection_item(
        self, sale_item_id: str = "42", with_url: bool = True
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "sale_item_id": int(sale_item_id),
            "sale_item_type": "p",
            "band_name": "Test Band",
            "item_title": "Test Album",
        }
        if with_url:
            item["redownload_url"] = f"https://bandcamp.com/download?id={sale_item_id}"
        return item

    def _run(
        self,
        tmp_path: Path,
        sale_item_id: str = "42",
        collection: list[dict[str, Any]] | None = None,
    ) -> Path:
        config = _bc_config(tmp_path)
        watch_folder = tmp_path / "watch"
        index = MagicMock()

        def fake_download_item(
            item: dict, bc_config: object, watch_dir: Path, session: object
        ) -> Path:
            watch_dir.mkdir(parents=True, exist_ok=True)
            dest = watch_dir / f"{item['sale_item_id']}.zip"
            dest.write_bytes(b"zip")
            return dest

        fake_collection = (
            collection
            if collection is not None
            else [self._collection_item(sale_item_id)]
        )

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=MagicMock()
            ),
            patch("kamp_daemon.bandcamp._get_fan_info", return_value=(123, "testuser")),
            patch(
                "kamp_daemon.bandcamp._fetch_collection", return_value=fake_collection
            ),
            patch(
                "kamp_daemon.bandcamp._download_item", side_effect=fake_download_item
            ),
        ):
            return download_single_album(config, watch_folder, index, sale_item_id)

    def test_downloads_zip_to_watch_folder(self, tmp_path: Path) -> None:
        dest = self._run(tmp_path)
        assert dest.exists()
        assert dest.suffix == ".zip"

    def test_updates_collection_mode_and_track_source(self, tmp_path: Path) -> None:
        config = _bc_config(tmp_path)
        index = MagicMock()
        fake_collection = [self._collection_item("42")]

        with (
            patch(
                "kamp_daemon.bandcamp._ensure_session",
                return_value=_make_session_data(),
            ),
            patch(
                "kamp_daemon.bandcamp._make_requests_session", return_value=MagicMock()
            ),
            patch("kamp_daemon.bandcamp._get_fan_info", return_value=(1, "user")),
            patch(
                "kamp_daemon.bandcamp._fetch_collection", return_value=fake_collection
            ),
            patch(
                "kamp_daemon.bandcamp._download_item", return_value=tmp_path / "a.zip"
            ),
        ):
            download_single_album(config, tmp_path / "watch", index, "42")

        index.set_collection_item_mode.assert_called_once_with("42", "local")
        index.set_track_source_for_item.assert_not_called()

    def test_raises_when_item_not_in_collection(self, tmp_path: Path) -> None:
        with pytest.raises(BandcampAPIError, match="No download link"):
            self._run(tmp_path, collection=[])

    def test_raises_when_redownload_url_missing(self, tmp_path: Path) -> None:
        with pytest.raises(BandcampAPIError, match="No download link"):
            self._run(
                tmp_path, collection=[self._collection_item("42", with_url=False)]
            )
