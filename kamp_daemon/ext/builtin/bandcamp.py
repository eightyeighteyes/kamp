"""First-party syncer: Bandcamp collection downloader.

Wraps kamp_daemon.syncer.Syncer using the public BaseSyncer API so the
Bandcamp downloader is registered and discovered as an extension like any
other, rather than being hard-coded into DaemonCore.

Architecture note
-----------------
The underlying Syncer already provides its own subprocess isolation via
_spawn_worker: Playwright and the bandcamp module are loaded only inside a
child process, which the OS reclaims entirely on exit.  There is therefore
no reason to nest a second subprocess (via invoke_extension()) inside the
daemon.  KampBandcampSyncer calls the inner Syncer in-process, mirroring
the pattern used by KampMusicBrainzTagger and KampCoverArtArchive.

Callbacks
---------
DaemonCore and the menu bar wire status_callback and error_callback on the
syncer *before* start() is called (to avoid a race where the first automatic
sync fires before the UI has wired its callback).  These attributes are
delegated transparently to the inner Syncer via properties.

reload() and pause()/resume()
------------------------------
reload() is an internal daemon lifecycle method (takes a Config object) that
is not part of the BaseSyncer ABC.  DaemonCore calls it directly on
KampBandcampSyncer after type-narrowing.  pause() and resume() override the
BaseSyncer defaults to use the inner Syncer's richer pause/resume semantics
(event-based suspension rather than full stop/start).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from kamp_daemon.ext.abc import BaseSyncer
from kamp_daemon.ext.context import KampGround

logger = logging.getLogger(__name__)


class KampBandcampSyncer(BaseSyncer):
    """Sync Bandcamp purchases to the watch folder.

    Thin extension wrapper around ``kamp_daemon.syncer.Syncer``.  All public
    methods delegate to the inner syncer; this class exists to register the
    Bandcamp syncer in the extension registry so it is discovered and pinned
    alongside third-party extensions.
    """

    # stage() deposits downloaded archives into the watch folder.
    kampground_permissions = ["library.write"]
    # Needs filesystem writes (session/state files, /tmp) and subprocess spawn
    # (Playwright launches Chromium).  See project/sandbox-profiles.md.
    _sandbox_tier = "syncer"

    def __init__(self, ctx: KampGround) -> None:
        # Lazy import: probe runs at module import time; keep the module-level
        # clean so the probe does not fire on bandcamp/playwright side-effects.
        from kamp_daemon.config import Config
        from kamp_daemon.syncer import Syncer

        # KampGround carries no Config reference — the daemon constructs
        # KampBandcampSyncer directly (in-process) and passes config via
        # _configure(), which DaemonCore calls immediately after construction.
        self._ctx = ctx
        self._inner: Syncer | None = None
        self._Config = Config
        self._Syncer = Syncer

    def _configure(self, config: object) -> None:
        """Initialise the inner Syncer with a Config object.

        Called by DaemonCore immediately after construction, before start().
        Separated from __init__ because KampGround carries no Config reference
        — Config is an internal daemon type, not an extension type.
        """
        from kamp_daemon.syncer import Syncer

        self._inner = Syncer(config)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # BaseSyncer interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start background polling (no-op when poll_interval_minutes is 0)."""
        assert self._inner is not None, "call _configure() before start()"
        self._inner.start()

    def stop(self) -> None:
        """Signal the polling thread to exit and wait for it."""
        assert self._inner is not None, "call _configure() before stop()"
        self._inner.stop()

    def pause(self) -> None:
        """Suspend polling without discarding thread state (event-based)."""
        assert self._inner is not None, "call _configure() before pause()"
        self._inner.pause()

    def resume(self) -> None:
        """Resume polling after pause()."""
        assert self._inner is not None, "call _configure() before resume()"
        self._inner.resume()

    def sync_once(self, *, skip_auto_mark: bool = False) -> None:
        """Download new purchases in an isolated subprocess."""
        assert self._inner is not None, "call _configure() before sync_once()"
        self._inner.sync_once(skip_auto_mark=skip_auto_mark)

    def mark_synced(self) -> None:
        """Mark the entire collection as already synced without downloading."""
        assert self._inner is not None, "call _configure() before mark_synced()"
        self._inner.mark_synced()

    # ------------------------------------------------------------------
    # Internal daemon API (not part of BaseSyncer)
    # ------------------------------------------------------------------

    def reload(self, config: object) -> None:
        """Apply a new Config live (called by DaemonCore on config reload)."""
        assert self._inner is not None, "call _configure() before reload()"
        self._inner.reload(config)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Callback delegation
    # ------------------------------------------------------------------

    @property
    def status_callback(self) -> Callable[[str], None] | None:
        """Status string callback wired by the menu bar / DaemonCore."""
        if self._inner is None:
            return None
        return self._inner.status_callback

    @status_callback.setter
    def status_callback(self, cb: Callable[[str], None] | None) -> None:
        assert (
            self._inner is not None
        ), "call _configure() before setting status_callback"
        self._inner.status_callback = cb

    @property
    def error_callback(self) -> Callable[[str, str, str], None] | None:
        """Error notification callback wired by the menu bar."""
        if self._inner is None:
            return None
        return self._inner.error_callback

    @error_callback.setter
    def error_callback(self, cb: Callable[[str, str, str], None] | None) -> None:
        assert (
            self._inner is not None
        ), "call _configure() before setting error_callback"
        self._inner.error_callback = cb
