"""Tests for KampBandcampSyncer and BaseSyncer ABC."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.ext.abc import BaseSyncer
from kamp_daemon.ext.builtin.bandcamp import KampBandcampSyncer
from kamp_daemon.ext.context import KampGround, PlaybackSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx() -> KampGround:
    return KampGround(playback=PlaybackSnapshot(), library_tracks=[])


def _make_syncer() -> KampBandcampSyncer:
    """Create a KampBandcampSyncer with a pre-wired mock inner Syncer.

    Bypasses _configure() by directly setting _inner — used for delegation
    tests that just need to verify method forwarding, not construction.
    """
    syncer = KampBandcampSyncer(_ctx())
    syncer._inner = MagicMock()  # type: ignore[assignment]
    return syncer


# ---------------------------------------------------------------------------
# BaseSyncer ABC
# ---------------------------------------------------------------------------


class TestBaseSyncerABC:
    def test_cannot_instantiate_directly(self) -> None:
        """`BaseSyncer` is abstract and cannot be instantiated without implementing start/stop."""
        with pytest.raises(TypeError):
            BaseSyncer()  # type: ignore[abstract]

    def test_pause_default_delegates_to_stop(self) -> None:
        """Default pause() calls stop() — subclasses that don't override get stop semantics."""

        class MinimalSyncer(BaseSyncer):
            def __init__(self) -> None:
                self.calls: list[str] = []

            def start(self) -> None:
                self.calls.append("start")

            def stop(self) -> None:
                self.calls.append("stop")

        s = MinimalSyncer()
        s.pause()
        assert s.calls == ["stop"]

    def test_resume_default_delegates_to_start(self) -> None:
        """Default resume() calls start()."""

        class MinimalSyncer(BaseSyncer):
            def __init__(self) -> None:
                self.calls: list[str] = []

            def start(self) -> None:
                self.calls.append("start")

            def stop(self) -> None:
                self.calls.append("stop")

        s = MinimalSyncer()
        s.resume()
        assert s.calls == ["start"]

    def test_sync_once_default_raises(self) -> None:
        """Default sync_once() raises NotImplementedError."""

        class MinimalSyncer(BaseSyncer):
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        s = MinimalSyncer()
        with pytest.raises(NotImplementedError):
            s.sync_once()

    def test_mark_synced_default_raises(self) -> None:
        """Default mark_synced() raises NotImplementedError."""

        class MinimalSyncer(BaseSyncer):
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        s = MinimalSyncer()
        with pytest.raises(NotImplementedError):
            s.mark_synced()

    def test_kamp_bandcamp_syncer_is_base_syncer(self) -> None:
        """KampBandcampSyncer is a concrete BaseSyncer implementation."""
        assert issubclass(KampBandcampSyncer, BaseSyncer)


# ---------------------------------------------------------------------------
# KampBandcampSyncer lifecycle delegation
# ---------------------------------------------------------------------------


class TestKampBandcampSyncer:
    def test_configure_creates_inner_syncer(self) -> None:
        """_configure() instantiates the inner Syncer with the given config."""
        config = MagicMock()
        syncer = KampBandcampSyncer(_ctx())

        # Patch the Syncer class at its module source so the lazy import picks it up.
        with patch("kamp_daemon.syncer.Syncer") as MockSyncer:
            syncer._configure(config)

        MockSyncer.assert_called_once_with(config)
        assert syncer._inner is MockSyncer.return_value

    def test_start_delegates_to_inner(self) -> None:
        syncer = _make_syncer()
        syncer.start()
        syncer._inner.start.assert_called_once()

    def test_stop_delegates_to_inner(self) -> None:
        syncer = _make_syncer()
        syncer.stop()
        syncer._inner.stop.assert_called_once()

    def test_pause_delegates_to_inner(self) -> None:
        syncer = _make_syncer()
        syncer.pause()
        syncer._inner.pause.assert_called_once()

    def test_resume_delegates_to_inner(self) -> None:
        syncer = _make_syncer()
        syncer.resume()
        syncer._inner.resume.assert_called_once()

    def test_sync_once_delegates_to_inner(self) -> None:
        syncer = _make_syncer()
        syncer.sync_once(skip_auto_mark=True)
        syncer._inner.sync_once.assert_called_once_with(skip_auto_mark=True)

    def test_mark_synced_delegates_to_inner(self) -> None:
        syncer = _make_syncer()
        syncer.mark_synced()
        syncer._inner.mark_synced.assert_called_once()

    def test_reload_delegates_to_inner(self) -> None:
        syncer = _make_syncer()
        new_config = MagicMock()
        syncer.reload(new_config)
        syncer._inner.reload.assert_called_once_with(new_config)

    def test_status_callback_getter_delegates(self) -> None:
        """status_callback getter returns the inner syncer's value."""
        syncer = _make_syncer()
        cb = MagicMock()
        syncer._inner.status_callback = cb
        assert syncer.status_callback is cb

    def test_status_callback_setter_delegates(self) -> None:
        """status_callback setter writes through to the inner syncer."""
        syncer = _make_syncer()
        cb = MagicMock()
        syncer.status_callback = cb
        assert syncer._inner.status_callback is cb

    def test_error_callback_getter_delegates(self) -> None:
        syncer = _make_syncer()
        cb = MagicMock()
        syncer._inner.error_callback = cb
        assert syncer.error_callback is cb

    def test_error_callback_setter_delegates(self) -> None:
        syncer = _make_syncer()
        cb = MagicMock()
        syncer.error_callback = cb
        assert syncer._inner.error_callback is cb

    def test_status_callback_none_before_configure(self) -> None:
        """status_callback returns None when _inner has not been configured."""
        syncer = KampBandcampSyncer(_ctx())
        assert syncer.status_callback is None

    def test_error_callback_none_before_configure(self) -> None:
        """error_callback returns None when _inner has not been configured."""
        syncer = KampBandcampSyncer(_ctx())
        assert syncer.error_callback is None

    def test_on_tracks_indexed_getter_delegates(self) -> None:
        """on_tracks_indexed getter returns the inner syncer's value."""
        syncer = _make_syncer()
        cb = MagicMock()
        syncer._inner.on_tracks_indexed = cb
        assert syncer.on_tracks_indexed is cb

    def test_on_tracks_indexed_setter_delegates(self) -> None:
        """on_tracks_indexed setter writes through to the inner syncer."""
        syncer = _make_syncer()
        cb = MagicMock()
        syncer.on_tracks_indexed = cb
        assert syncer._inner.on_tracks_indexed is cb

    def test_on_tracks_indexed_none_before_configure(self) -> None:
        """on_tracks_indexed returns None when _inner has not been configured."""
        syncer = KampBandcampSyncer(_ctx())
        assert syncer.on_tracks_indexed is None

    def test_methods_assert_before_configure(self) -> None:
        """Calling lifecycle methods before _configure() raises AssertionError."""
        syncer = KampBandcampSyncer(_ctx())
        with pytest.raises(AssertionError):
            syncer.start()
        with pytest.raises(AssertionError):
            syncer.stop()
