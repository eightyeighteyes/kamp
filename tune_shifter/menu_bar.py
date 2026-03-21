"""macOS menu bar application for the tune-shifter daemon.

Excluded from coverage: requires AppKit/rumps runtime (macOS only).
Meaningfully unit-testing this module would require mocking all AppKit
and rumps internals, providing little value over manual testing on macOS.

Must only be imported on macOS — raises ImportError otherwise.
"""

from __future__ import annotations

import logging
import platform
import signal
import subprocess
import threading

_logger = logging.getLogger(__name__)

if platform.system() != "Darwin":
    raise ImportError("tune_shifter.menu_bar is only available on macOS")

import rumps  # noqa: E402 — guarded above

from .daemon_core import DaemonCore, _PID_PATH

logger = logging.getLogger(__name__)

_ABOUT_URL = "https://github.com/eightyeighteyes/tune-shifter"
_SYMBOL_NAME = "music.note.list"  # music.note.square.stack does not exist in SF Symbols

# Valid format keys and their display labels (alphabetical).
_FORMAT_LABELS: list[tuple[str, str]] = [
    ("aac-hi", "AAC-HI"),
    ("alac", "ALAC"),
    ("flac", "FLAC"),
    ("mp3-320", "MP3-320"),
    ("mp3-v0", "MP3-V0"),
    ("vorbis", "Ogg Vorbis"),
    ("wav", "WAV"),
]


class MenuBarApp(rumps.App):
    """rumps-based menu bar application for the tune-shifter daemon.

    Holds a DaemonCore reference and exposes pipeline start/stop and
    Bandcamp sync controls in the macOS menu bar.
    """

    def __init__(self, core: DaemonCore) -> None:
        # Set accessory policy BEFORE the AppKit run loop starts.  A launchd-launched
        # process has no GUI bundle, so this call grants it a Window Server connection
        # and allows the status-bar item to appear.
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )

        # quit_button=None — we supply our own Quit item so we can call
        # DaemonCore.shutdown() before exiting the AppKit run loop.
        super().__init__("tune-shifter", icon=None, quit_button=None)

        self._core = core
        self._sync_in_progress = False
        self._pulse_active = False
        # Written by background threads; read only by the main-thread _refresh timer
        # to avoid touching AppKit objects off the main thread (trace trap risk).
        self._sync_status: str = ""
        self._pipeline_status: str = ""

        # Replace the text title with an SF Symbol icon.
        self._set_sf_symbol_icon()

        # DaemonCore installs SIGTERM/SIGINT handlers that call shutdown() and
        # unblock core.wait(), but in the menu bar path there is no core.wait() call —
        # the AppKit run loop holds the main thread.  Override those signals here so
        # that SIGTERM also exits the run loop via _on_quit.
        def _quit_signal(signum: int, frame: object) -> None:
            self._on_quit(None)

        signal.signal(signal.SIGINT, _quit_signal)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _quit_signal)

        # Build menu items.
        self._toggle_item = rumps.MenuItem("Stop", callback=self._on_toggle)
        self._sync_item = rumps.MenuItem("Bandcamp Sync", callback=self._on_sync)
        self._status_item = rumps.MenuItem("Status: Idle")

        # Build the Download Format submenu.
        self._format_menu = rumps.MenuItem("Download Format")
        self._format_items: dict[str, rumps.MenuItem] = {}
        for fmt, label in _FORMAT_LABELS:
            item = rumps.MenuItem(label, callback=self._on_format)
            self._format_items[fmt] = item
        self._format_menu.update(self._format_items.values())

        self.menu = [
            self._toggle_item,
            None,  # separator
            self._sync_item,
            self._status_item,
            self._format_menu,
            None,  # separator
            rumps.MenuItem("About Tune-Shifter", callback=self._on_about),
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]

        # Apply initial enabled state for bandcamp-dependent items.
        self._refresh_bandcamp_items()

        # Wire pipeline stage and sync status updates.  core.start() is called
        # before MenuBarApp(core).run(), so the watcher and syncer are already
        # running here.  status_callback must be wired at init — not just for
        # manual syncs — so that automatic (scheduled) syncs also update the bar.
        self._core.watcher.stage_callback = self._on_pipeline_stage
        self._core.syncer.status_callback = self._on_sync_status

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    def _on_toggle(self, sender: rumps.MenuItem) -> None:
        if self._core.state == "running":
            self._core.stop()
            self._toggle_item.title = "Play"
        else:
            self._core.resume()
            self._toggle_item.title = "Stop"

    def _on_sync(self, sender: rumps.MenuItem) -> None:
        if self._sync_in_progress:
            return
        syncer = self._core.syncer
        if syncer is None:
            return
        self._sync_in_progress = True
        self._refresh_bandcamp_items()

        def _run() -> None:
            try:
                syncer.sync_once()
            except Exception:
                logger.exception("Unhandled error during manual Bandcamp sync")
            finally:
                self._sync_in_progress = False

        threading.Thread(target=_run, daemon=True).start()

    def _on_sync_status(self, msg: str) -> None:
        """Store the current download target for the main-thread _refresh timer.

        Called from the background sync thread — must not touch AppKit objects
        directly, as AppKit requires all UI mutations on the main thread.
        """
        self._sync_status = msg

    def _on_pipeline_stage(self, stage: str) -> None:
        """Store the current pipeline stage for the main-thread _refresh timer.

        Called from the watcher's pipeline thread — same AppKit threading rule
        applies: write to a plain Python str only.
        """
        self._pipeline_status = stage

    def _on_format(self, sender: rumps.MenuItem) -> None:
        """Write the selected download format to the config file."""
        fmt = next(k for k, v in self._format_items.items() if v is sender)
        try:
            from .config import config_set

            config_set(self._core._config_path, "bandcamp.format", fmt)
        except Exception as exc:
            _logger.warning("Failed to set download format: %s", exc)

    def _on_about(self, sender: rumps.MenuItem) -> None:
        subprocess.run(["open", _ABOUT_URL], check=False)

    def _on_quit(self, sender: object) -> None:
        self._core.shutdown()
        _PID_PATH.unlink(missing_ok=True)
        rumps.quit_application()

    # ------------------------------------------------------------------
    # Timer: refresh status every 5 seconds
    # ------------------------------------------------------------------

    @rumps.timer(5)
    def _refresh(self, sender: object) -> None:
        # Keep toggle label in sync with the actual pipeline state (e.g. if
        # the pipeline was paused/resumed via SIGUSR1/SIGUSR2 externally).
        if self._core.state == "paused":
            self._toggle_item.title = "Play"
        else:
            self._toggle_item.title = "Stop"

        if self._pipeline_status:
            # Pipeline stage (Extracting / Tagging / Updating artwork / Moving)
            # takes priority — it's more specific than the sync label.
            self._status_item.title = f"Status: {self._pipeline_status}"
            self._set_pulse(True)
        elif self._sync_in_progress or self._sync_status:
            label = self._sync_status or "Syncing\u2026"
            self._status_item.title = f"Status: {label}"
            self._set_pulse(True)
        else:
            self._sync_status = ""
            self._status_item.title = "Status: Idle"
            self._set_pulse(False)

        self._refresh_bandcamp_items()

    # ------------------------------------------------------------------
    # AppKit helpers
    # ------------------------------------------------------------------

    def _set_sf_symbol_icon(self) -> None:
        """Pre-load the SF Symbol NSImage into _icon_nsimage before run() starts.

        rumps.App.run() calls initializeStatusBar() → setStatusBarIcon() which
        reads self._icon_nsimage directly from the App's __dict__.  Setting it
        here (before run()) is the correct hook point; the nsstatusitem object
        does not exist yet during __init__.
        """
        try:
            from AppKit import NSImage

            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                _SYMBOL_NAME, "tune-shifter"
            )
            if img:
                img.setTemplate_(True)  # adapts to light/dark menu bar
                # Bypass the rumps icon setter (which only accepts file paths)
                # and write the NSImage straight into the attribute rumps reads.
                self._icon_nsimage = img
        except Exception as exc:
            _logger.warning("SF Symbol icon setup failed: %s", exc)

    def _set_pulse(self, active: bool) -> None:
        """Pulse the status-bar icon opacity while a sync is in progress.

        Uses CABasicAnimation (QuartzCore) since NSSymbolPulseEffect's
        addSymbolEffect:options: is not yet bound in PyObjC 12.x.
        """
        if active == self._pulse_active:
            return
        self._pulse_active = active
        try:
            import objc

            objc.loadBundle(
                "QuartzCore",
                bundle_path="/System/Library/Frameworks/QuartzCore.framework",
                module_globals={},
            )
            CABasicAnimation = objc.lookUpClass("CABasicAnimation")
            btn = self._nsapp.nsstatusitem.button()
            btn.setWantsLayer_(True)
            layer = btn.layer()
            if active:
                anim = CABasicAnimation.animationWithKeyPath_("opacity")
                anim.setFromValue_(1.0)
                anim.setToValue_(0.25)
                anim.setDuration_(0.8)
                anim.setRepeatCount_(1e9)  # effectively infinite
                anim.setAutoreverses_(True)
                layer.addAnimation_forKey_(anim, "syncPulse")
            else:
                layer.removeAnimationForKey_("syncPulse")
                layer.setOpacity_(1.0)
        except Exception as exc:
            _logger.warning("Pulse animation failed: %s", exc)

    def _refresh_bandcamp_items(self) -> None:
        """Disable Bandcamp Sync and Sync Status when config or conditions prevent use."""
        bc = self._core._config.bandcamp
        has_bandcamp = bc is not None
        sync_available = has_bandcamp and not self._sync_in_progress
        current_fmt = bc.format if bc is not None else None

        if sync_available:
            self._sync_item.set_callback(self._on_sync)
        else:
            self._sync_item.set_callback(None)

        # setEnabled_ controls the visual gray-out at the AppKit level;
        # set_callback(None) alone only removes the click handler.
        try:
            self._sync_item._menuitem.setEnabled_(sync_available)
            self._status_item._menuitem.setEnabled_(has_bandcamp)
            self._format_menu._menuitem.setEnabled_(has_bandcamp)
        except Exception:
            pass

        # Update checkmarks on format submenu items.
        for fmt, item in self._format_items.items():
            label_base = next(lbl for k, lbl in _FORMAT_LABELS if k == fmt)
            item.title = f"{label_base} \u2713" if fmt == current_fmt else label_base
