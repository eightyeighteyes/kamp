"""Platform-agnostic tests for the extension sandbox module.

Tests here do not spawn real subprocesses and run on all platforms.
They verify:
- Default _sandbox_tier on each base class
- get_initializer() returns the right type per platform
- get_initializer() raises for unknown tiers
- _spawn_extension_worker passes the sandbox initializer to mp_ctx.Process
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kamp_daemon.ext.abc import BaseArtworkSource, BaseSyncer, BaseTagger
from kamp_daemon.ext.builtin.bandcamp import KampBandcampSyncer
from kamp_daemon.ext.builtin.coverart import KampCoverArtArchive
from kamp_daemon.ext.builtin.musicbrainz import KampMusicBrainzTagger
from kamp_daemon.ext.context import KampGround
from kamp_daemon.ext.sandbox import TIER_MINIMAL, TIER_SYNCER, get_initializer
from kamp_daemon.ext.worker import _spawn_extension_worker

# ---------------------------------------------------------------------------
# Sandbox tier defaults
# ---------------------------------------------------------------------------


class TestSandboxTierDefaults:
    def test_base_tagger_default(self) -> None:
        assert BaseTagger._sandbox_tier == TIER_MINIMAL

    def test_base_artwork_source_default(self) -> None:
        assert BaseArtworkSource._sandbox_tier == TIER_MINIMAL

    def test_base_syncer_default(self) -> None:
        assert BaseSyncer._sandbox_tier == TIER_MINIMAL

    def test_musicbrainz_tagger_minimal(self) -> None:
        assert KampMusicBrainzTagger._sandbox_tier == TIER_MINIMAL

    def test_coverart_archive_minimal(self) -> None:
        assert KampCoverArtArchive._sandbox_tier == TIER_MINIMAL

    def test_bandcamp_syncer_is_syncer_tier(self) -> None:
        # Bandcamp needs filesystem writes (state/session) and subprocess
        # spawning (Playwright/Chromium).
        assert KampBandcampSyncer._sandbox_tier == TIER_SYNCER


# ---------------------------------------------------------------------------
# get_initializer()
# ---------------------------------------------------------------------------


class TestGetInitializer:
    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown sandbox tier"):
            get_initializer("totally_fake_tier")

    def test_tier_constants_are_stable(self) -> None:
        # Tier strings are part of the extension author's API surface — must
        # not change silently.
        assert TIER_MINIMAL == "minimal"
        assert TIER_SYNCER == "syncer"

    def test_unsupported_platform_returns_none(self) -> None:
        # Platforms other than darwin/linux have no sandbox implementation.
        with patch.object(sys, "platform", "win32"):
            result = get_initializer(TIER_MINIMAL)
        assert result is None

    def test_darwin_returns_callable(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            result = get_initializer(TIER_MINIMAL)
        assert callable(result)

    def test_linux_returns_callable(self) -> None:
        with patch.object(sys, "platform", "linux"):
            result = get_initializer(TIER_MINIMAL)
        assert callable(result)

    def test_both_tiers_return_callable_on_darwin(self) -> None:
        with patch.object(sys, "platform", "darwin"):
            assert callable(get_initializer(TIER_MINIMAL))
            assert callable(get_initializer(TIER_SYNCER))

    def test_both_tiers_return_callable_on_linux(self) -> None:
        with patch.object(sys, "platform", "linux"):
            assert callable(get_initializer(TIER_MINIMAL))
            assert callable(get_initializer(TIER_SYNCER))


# ---------------------------------------------------------------------------
# _spawn_extension_worker — initializer is wired to Process
# ---------------------------------------------------------------------------

# Minimal concrete extension classes used to verify tier propagation.


class _TaggerMinimal(BaseTagger):
    # _sandbox_tier inherits "minimal" from BaseTagger
    def tag(self, track: Any) -> Any:
        return track


class _TaggerSyncer(BaseTagger):
    _sandbox_tier = TIER_SYNCER

    def tag(self, track: Any) -> Any:
        return track


class TestSpawnWorkerInitializer:
    """Verify _spawn_extension_worker passes the sandbox initializer to Process.

    We mock multiprocessing.get_context so no real subprocess is spawned —
    this is purely a wiring test.
    """

    def _capture_process_kwargs(
        self, cls: type, ctx: KampGround | None = None
    ) -> dict[str, Any]:
        """Call _spawn_extension_worker with a mocked mp_ctx and return the
        kwargs that were passed to mp_ctx.Process()."""
        captured: dict[str, Any] = {}

        class _FakeProc:
            def start(self) -> None:
                pass

        class _FakeQueue:
            pass

        class _FakeCtx:
            def Queue(self) -> _FakeQueue:
                return _FakeQueue()

            def Process(self, **kwargs: Any) -> _FakeProc:
                captured.update(kwargs)
                return _FakeProc()

        ctx = ctx or KampGround()
        with patch(
            "kamp_daemon.ext.worker.multiprocessing.get_context",
            return_value=_FakeCtx(),
        ):
            _spawn_extension_worker(cls, "tag", (), ctx)

        return captured

    def test_minimal_tier_passes_initializer(self) -> None:
        kwargs = self._capture_process_kwargs(_TaggerMinimal)
        # On supported platforms the initializer is a callable; on unsupported
        # platforms (Windows) it is None.  Both are valid values for the
        # multiprocessing.Process `initializer` parameter.
        initializer = kwargs.get("initializer")
        if sys.platform in ("darwin", "linux"):
            assert callable(
                initializer
            ), f"Expected a callable initializer on {sys.platform}, got {initializer!r}"
        else:
            assert initializer is None

    def test_syncer_tier_passes_initializer(self) -> None:
        kwargs = self._capture_process_kwargs(_TaggerSyncer)
        initializer = kwargs.get("initializer")
        if sys.platform in ("darwin", "linux"):
            # Syncer initializer must be a different callable from minimal.
            assert callable(initializer)
        else:
            assert initializer is None

    def test_minimal_and_syncer_initializers_differ(self) -> None:
        if sys.platform not in ("darwin", "linux"):
            pytest.skip("initializers are None on unsupported platforms")
        kwargs_min = self._capture_process_kwargs(_TaggerMinimal)
        kwargs_syn = self._capture_process_kwargs(_TaggerSyncer)
        assert kwargs_min["initializer"] is not kwargs_syn["initializer"]

    def test_class_without_sandbox_tier_defaults_to_minimal(self) -> None:
        """A class that doesn't declare _sandbox_tier gets TIER_MINIMAL."""

        class _NoTierTagger(BaseTagger):
            def tag(self, track: Any) -> Any:
                return track

        # _sandbox_tier is inherited from BaseTagger == "minimal"
        assert _NoTierTagger._sandbox_tier == TIER_MINIMAL
        kwargs = self._capture_process_kwargs(_NoTierTagger)
        if sys.platform in ("darwin", "linux"):
            assert callable(kwargs.get("initializer"))
