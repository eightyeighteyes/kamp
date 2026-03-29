"""Tests for error notification wiring (pipeline errors + Bandcamp sync failures)."""

from __future__ import annotations

import json
import queue as _queue_module
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.config import (
    ArtworkConfig,
    BandcampConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from kamp_daemon.pipeline import (
    _NOTIFY_SENTINEL,
    _handle_stage_msg,
    run_in_subprocess,
)
from kamp_daemon.syncer import Syncer


def _make_config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(staging=tmp_path / "staging", library=tmp_path / "library"),
        musicbrainz=MusicBrainzConfig(contact="t@t.com"),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
        bandcamp=BandcampConfig(
            username="u", cookie_file=None, format="flac", poll_interval_minutes=0
        ),
    )


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeProc:
    exitcode = 0

    def join(self, timeout: object = None) -> None:
        pass

    def is_alive(self) -> bool:
        return False


def _inline_worker(target: Any, args: tuple[Any, ...]) -> tuple[Any, Any, Any, Any]:
    """Run the worker synchronously in-process so pipeline_impl patches apply."""
    stage_q: _queue_module.Queue[str] = _queue_module.Queue()
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    target(*args, stage_q, log_q, result_q)
    return _FakeProc(), stage_q, log_q, result_q


def _noop_worker_ok(target: Any, args: tuple[Any, ...]) -> tuple[Any, Any, Any, Any]:
    stage_q: _queue_module.Queue[str] = _queue_module.Queue()
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q.put(("ok", []))
    return _FakeProc(), stage_q, log_q, result_q


def _noop_worker_error(target: Any, args: tuple[Any, ...]) -> tuple[Any, Any, Any, Any]:
    stage_q: _queue_module.Queue[str] = _queue_module.Queue()
    log_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q: _queue_module.Queue[Any] = _queue_module.Queue()
    result_q.put(("error", "login failed"))
    return _FakeProc(), stage_q, log_q, result_q


# ---------------------------------------------------------------------------
# _handle_stage_msg sentinel routing
# ---------------------------------------------------------------------------


class TestNotifySentinel:
    def _make_notify_msg(
        self, subtitle: str = "Tagging failed", message: str = "album"
    ) -> str:
        payload = json.dumps(
            {"title": "Kamp", "subtitle": subtitle, "message": message}
        )
        return f"{_NOTIFY_SENTINEL}{payload}"

    def test_notify_sentinel_calls_notification_callback(self) -> None:
        received: list[tuple[str, str, str]] = []
        _handle_stage_msg(
            self._make_notify_msg("Tagging failed", "my-album"),
            stage_callback=None,
            on_directory=None,
            notification_callback=lambda t, s, m: received.append((t, s, m)),
        )
        assert received == [("Kamp", "Tagging failed", "my-album")]

    def test_notify_sentinel_not_forwarded_to_stage_callback(self) -> None:
        stage_received: list[str] = []
        _handle_stage_msg(
            self._make_notify_msg(),
            stage_callback=stage_received.append,
            on_directory=None,
            notification_callback=lambda *a: None,
        )
        assert stage_received == []

    def test_malformed_notify_sentinel_does_not_raise(self) -> None:
        # Bad JSON payload — must log a warning and not propagate an exception.
        _handle_stage_msg(
            f"{_NOTIFY_SENTINEL}not-valid-json",
            stage_callback=None,
            on_directory=None,
            notification_callback=lambda *a: None,
        )

    def test_notify_sentinel_with_no_callback_is_safe(self) -> None:
        _handle_stage_msg(
            self._make_notify_msg(),
            stage_callback=None,
            on_directory=None,
            notification_callback=None,
        )


# ---------------------------------------------------------------------------
# pipeline_impl error sites emit notifications
# ---------------------------------------------------------------------------


class TestPipelineImplNotifications:
    """Verify that each failure site in pipeline_impl.run() fires a notification."""

    def _run(self, tmp_path: Path) -> tuple[Path, Config]:
        config = _make_config(tmp_path)
        config.paths.staging.mkdir(parents=True)
        path = config.paths.staging / "my-album"
        path.mkdir()
        return path, config

    def _patches(self) -> list[Any]:
        """Common patches needed to run _pipeline_worker in-process."""
        return [
            patch("kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker),
            patch("musicbrainzngs.set_useragent"),
        ]

    def test_extraction_error_emits_notify(self, tmp_path: Path) -> None:
        from kamp_daemon.extractor import ExtractionError

        path, config = self._run(tmp_path)
        received: list[tuple[str, str, str]] = []

        with patch("kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker):
            with patch("musicbrainzngs.set_useragent"):
                with patch(
                    "kamp_daemon.pipeline_impl.extract",
                    side_effect=ExtractionError("bad zip"),
                ):
                    run_in_subprocess(
                        path,
                        config,
                        notification_callback=lambda t, s, m: received.append(
                            (t, s, m)
                        ),
                    )

        assert len(received) == 1
        assert received[0][1] == "Extraction failed"

    def test_tagging_error_emits_notify(self, tmp_path: Path) -> None:
        from kamp_daemon.tagger import TaggingError

        path, config = self._run(tmp_path)
        received: list[tuple[str, str, str]] = []

        release = MagicMock()
        release.mbid = "mbid-1"
        release.release_group_mbid = "rg-1"
        release.title = "Test Album"

        with patch("kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker):
            with patch("musicbrainzngs.set_useragent"):
                with patch("kamp_daemon.pipeline_impl.extract", return_value=path):
                    with patch(
                        "kamp_daemon.pipeline_impl.find_audio_files",
                        return_value=[path / "track.mp3"],
                    ):
                        with patch(
                            "kamp_daemon.pipeline_impl.is_tagged", return_value=False
                        ):
                            with patch(
                                "kamp_daemon.pipeline_impl.tag_directory",
                                side_effect=TaggingError("no match"),
                            ):
                                run_in_subprocess(
                                    path,
                                    config,
                                    notification_callback=lambda t, s, m: received.append(
                                        (t, s, m)
                                    ),
                                )

        assert len(received) == 1
        assert received[0][1] == "Tagging failed"

    def test_artwork_error_emits_notify(self, tmp_path: Path) -> None:
        from kamp_daemon.artwork import ArtworkError

        path, config = self._run(tmp_path)
        received: list[tuple[str, str, str]] = []

        release = MagicMock()
        release.mbid = "mbid-1"
        release.release_group_mbid = "rg-1"
        release.title = "Test Album"

        with patch("kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker):
            with patch("musicbrainzngs.set_useragent"):
                with patch("kamp_daemon.pipeline_impl.extract", return_value=path):
                    with patch(
                        "kamp_daemon.pipeline_impl.find_audio_files",
                        return_value=[path / "track.mp3"],
                    ):
                        with patch(
                            "kamp_daemon.pipeline_impl.is_tagged", return_value=False
                        ):
                            with patch(
                                "kamp_daemon.pipeline_impl.tag_directory",
                                return_value=release,
                            ):
                                with patch(
                                    "kamp_daemon.pipeline_impl.fetch_and_embed",
                                    side_effect=ArtworkError("no image"),
                                ):
                                    with patch(
                                        "kamp_daemon.pipeline_impl.move_to_library",
                                        return_value=[],
                                    ):
                                        run_in_subprocess(
                                            path,
                                            config,
                                            notification_callback=lambda t, s, m: received.append(
                                                (t, s, m)
                                            ),
                                        )

        # Artwork failure is non-fatal — pipeline continues and still notifies.
        assert len(received) == 1
        assert received[0][1] == "Artwork warning"

    def test_move_error_emits_notify(self, tmp_path: Path) -> None:
        from kamp_daemon.mover import MoveError

        path, config = self._run(tmp_path)
        received: list[tuple[str, str, str]] = []

        release = MagicMock()
        release.mbid = "mbid-1"
        release.release_group_mbid = "rg-1"
        release.title = "Test Album"

        with patch("kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker):
            with patch("musicbrainzngs.set_useragent"):
                with patch("kamp_daemon.pipeline_impl.extract", return_value=path):
                    with patch(
                        "kamp_daemon.pipeline_impl.find_audio_files",
                        return_value=[path / "track.mp3"],
                    ):
                        with patch(
                            "kamp_daemon.pipeline_impl.is_tagged", return_value=False
                        ):
                            with patch(
                                "kamp_daemon.pipeline_impl.tag_directory",
                                return_value=release,
                            ):
                                with patch("kamp_daemon.pipeline_impl.fetch_and_embed"):
                                    with patch(
                                        "kamp_daemon.pipeline_impl.move_to_library",
                                        side_effect=MoveError("no dest"),
                                    ):
                                        run_in_subprocess(
                                            path,
                                            config,
                                            notification_callback=lambda t, s, m: received.append(
                                                (t, s, m)
                                            ),
                                        )

        assert len(received) == 1
        assert received[0][1] == "Move failed"

    def test_success_does_not_emit_notify(self, tmp_path: Path) -> None:
        path, config = self._run(tmp_path)
        received: list[tuple[str, str, str]] = []

        release = MagicMock()
        release.mbid = "mbid-1"
        release.release_group_mbid = "rg-1"
        release.title = "Test Album"

        with patch("kamp_daemon.pipeline._spawn_worker", side_effect=_inline_worker):
            with patch("musicbrainzngs.set_useragent"):
                with patch("kamp_daemon.pipeline_impl.extract", return_value=path):
                    with patch(
                        "kamp_daemon.pipeline_impl.find_audio_files",
                        return_value=[path / "track.mp3"],
                    ):
                        with patch(
                            "kamp_daemon.pipeline_impl.is_tagged", return_value=False
                        ):
                            with patch(
                                "kamp_daemon.pipeline_impl.tag_directory",
                                return_value=release,
                            ):
                                with patch("kamp_daemon.pipeline_impl.fetch_and_embed"):
                                    with patch(
                                        "kamp_daemon.pipeline_impl.move_to_library",
                                        return_value=[],
                                    ):
                                        run_in_subprocess(
                                            path,
                                            config,
                                            notification_callback=lambda t, s, m: received.append(
                                                (t, s, m)
                                            ),
                                        )

        assert received == []


# ---------------------------------------------------------------------------
# Syncer.error_callback
# ---------------------------------------------------------------------------


class TestSyncerErrorCallback:
    def test_error_callback_called_on_sync_failure(self, tmp_path: Path) -> None:
        """error_callback is called by _run() when sync_once() raises."""
        received: list[tuple[str, str, str]] = []
        syncer = Syncer(_make_config(tmp_path))

        # Have the callback set the stop event so _run() exits after one iteration.
        def _cb(t: str, s: str, m: str) -> None:
            received.append((t, s, m))
            syncer._stop_event.set()

        syncer.error_callback = _cb

        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker_error):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                syncer._run()

        assert len(received) == 1
        assert received[0][0] == "Kamp"
        assert received[0][1] == "Bandcamp sync failed"

    def test_error_callback_not_required(self, tmp_path: Path) -> None:
        """sync_once() raises without error_callback — no AttributeError."""
        syncer = Syncer(_make_config(tmp_path))
        assert syncer.error_callback is None

        with patch("kamp_daemon.syncer._spawn_worker", side_effect=_noop_worker_error):
            with patch("kamp_daemon.syncer._state_dir", return_value=tmp_path):
                with pytest.raises(RuntimeError):
                    syncer.sync_once()
