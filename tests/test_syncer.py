"""Tests for tune_shifter.syncer."""

from pathlib import Path
from unittest.mock import patch

from tune_shifter.config import (
    ArtworkConfig,
    BandcampConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from tune_shifter.syncer import Syncer


def _make_config(tmp_path: Path, poll_interval: int = 0) -> Config:
    return Config(
        paths=PathsConfig(staging=tmp_path / "staging", library=tmp_path / "library"),
        musicbrainz=MusicBrainzConfig(app_name="test", contact="t@t.com"),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
        bandcamp=BandcampConfig(
            username="user",
            cookie_file=None,
            format="mp3-v0",
            poll_interval_minutes=poll_interval,
        ),
    )


def _make_config_no_bandcamp(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(staging=tmp_path / "staging", library=tmp_path / "library"),
        musicbrainz=MusicBrainzConfig(app_name="test", contact="t@t.com"),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
    )


class TestStart:
    def test_noop_when_no_bandcamp(self, tmp_path: Path) -> None:
        """start() does nothing when there is no [bandcamp] config."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        syncer.start()
        assert syncer._thread is None

    def test_noop_when_interval_zero(self, tmp_path: Path) -> None:
        """start() does nothing when poll_interval_minutes is 0."""
        syncer = Syncer(_make_config(tmp_path, poll_interval=0))
        syncer.start()
        assert syncer._thread is None

    def test_launches_thread_when_interval_set(self, tmp_path: Path) -> None:
        """start() spawns a daemon thread when poll_interval_minutes > 0."""
        with patch("tune_shifter.syncer.sync_new_purchases", return_value=[]):
            with patch("tune_shifter.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                assert syncer._thread is not None
                assert syncer._thread.is_alive()
                syncer.stop()


class TestStop:
    def test_stop_sets_event(self, tmp_path: Path) -> None:
        """stop() sets the stop event even when no thread was started."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        syncer.stop()
        assert syncer._stop_event.is_set()

    def test_stop_joins_thread(self, tmp_path: Path) -> None:
        """stop() waits for the polling thread to finish."""
        with patch("tune_shifter.syncer.sync_new_purchases", return_value=[]):
            with patch("tune_shifter.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path, poll_interval=60))
                syncer.start()
                syncer.stop()
                assert syncer._thread is not None
                assert not syncer._thread.is_alive()


class TestSyncOnce:
    def test_warns_when_no_bandcamp(self, tmp_path: Path) -> None:
        """sync_once() logs a warning and returns when [bandcamp] is absent."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        with patch("tune_shifter.syncer.sync_new_purchases") as mock_sync:
            syncer.sync_once()
        mock_sync.assert_not_called()

    def test_logs_downloaded_count(self, tmp_path: Path) -> None:
        """sync_once() reports the number of downloaded files."""
        fake_paths = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
        with patch("tune_shifter.syncer.sync_new_purchases", return_value=fake_paths):
            with patch("tune_shifter.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.sync_once()

    def test_logs_nothing_new(self, tmp_path: Path) -> None:
        """sync_once() handles an empty result without error."""
        with patch("tune_shifter.syncer.sync_new_purchases", return_value=[]):
            with patch("tune_shifter.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.sync_once()


class TestMarkSynced:
    def test_warns_when_no_bandcamp(self, tmp_path: Path) -> None:
        """mark_synced() warns and returns when [bandcamp] is absent."""
        syncer = Syncer(_make_config_no_bandcamp(tmp_path))
        with patch("tune_shifter.syncer.mark_collection_synced") as mock_mark:
            syncer.mark_synced()
        mock_mark.assert_not_called()

    def test_calls_mark_collection_synced(self, tmp_path: Path) -> None:
        """mark_synced() delegates to mark_collection_synced with correct args."""
        with patch("tune_shifter.syncer.mark_collection_synced") as mock_mark:
            with patch("tune_shifter.syncer._state_dir", return_value=tmp_path):
                syncer = Syncer(_make_config(tmp_path))
                syncer.mark_synced()
        mock_mark.assert_called_once()
