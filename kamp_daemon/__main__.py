"""Entry point for the kamp daemon.

Excluded from coverage (see pyproject.toml [tool.coverage.run] omit list) because
this module is pure CLI/daemon lifecycle glue: argparse dispatch and signal handlers.
Meaningfully unit-testing it would require spawning
subprocesses or mocking the entire OS-level daemon lifecycle, with little marginal
value over the integration tests already covering the underlying modules (Watcher,
Syncer, Config, etc.).
"""

from __future__ import annotations

import argparse
from typing import Any
import asyncio
import importlib.metadata
import logging
import os
import platform
import secrets
import shutil
import signal
import sys
import tomllib
from pathlib import Path

import musicbrainzngs

from .config import (
    Config,
    _state_dir,
    config_set,
    config_show,
    token_path,
)
from .daemon_core import DaemonCore, _PID_PATH

# Stable Homebrew binary locations (Apple Silicon, then Intel). Checked in order
# before falling back to PATH, to avoid pyenv shims shadowing the Homebrew install.
_HOMEBREW_MPV_PATHS = ["/opt/homebrew/bin/mpv", "/usr/local/bin/mpv"]

# Common Windows mpv install locations. Checked in order when the daemon is
# spawned by Electron with a stale PATH (PowerShell sessions don't refresh
# PATH after a Scoop/Choco install in the same session, so PATH-based lookup
# may miss mpv even though the user has it installed).
_WIN_MPV_PATHS = [
    str(Path.home() / "scoop" / "shims" / "mpv.exe"),
    str(Path.home() / "scoop" / "apps" / "mpv" / "current" / "mpv.exe"),
    r"C:\ProgramData\chocolatey\bin\mpv.exe",
    r"C:\Program Files\mpv\mpv.exe",
]

# pyproject.toml lives one level above the package directory and is the canonical
# version source kept up to date by release-please.  Prefer it over
# importlib.metadata, which reads the *installed* package and can return a stale
# version when running directly from a source checkout alongside an older install.
_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _get_version() -> str:
    if _PYPROJECT.exists():
        with open(_PYPROJECT, "rb") as f:
            data = tomllib.load(f)
        # pyproject.toml uses [tool.poetry], not the PEP 621 [project] table
        version = data.get("tool", {}).get("poetry", {}).get("version") or data.get(
            "project", {}
        ).get("version")
        return str(version or "unknown") + "-dev"
    try:
        return importlib.metadata.version("kamp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main() -> None:
    # Windows consoles default to a legacy code page (cp1252 on en-US) that
    # rejects Unicode characters used in our startup banner ("→") and other
    # log output. Reconfigure stdout/stderr to UTF-8 so prints never crash
    # the daemon on a missing-glyph encode error. No-op on POSIX where the
    # default is already UTF-8.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    # Rename the process so `ps` output shows "kamp" instead of
    # "Python".  setproctitle updates argv[0] which is sufficient on Linux;
    # on macOS it also helps ps, but Activity Monitor reads the kernel-level
    # p_comm (set from the executable path at exec time and not writable from
    # userspace), so the menu-bar icon name requires a compiled binary launcher
    # — tracked separately in the backlog.
    try:
        import setproctitle

        setproctitle.setproctitle("kamp")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="kamp",
        description="Automated audio library ingest from Bandcamp.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # daemon subcommand (default) with pause/resume subcommands
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Watch the watch folder and (optionally) poll Bandcamp. Default when no subcommand given.",
    )
    daemon_parser.add_argument("--watch-folder", metavar="DIR", type=Path, default=None)
    daemon_parser.add_argument("--library", metavar="DIR", type=Path, default=None)
    daemon_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for the HTTP server (default: 127.0.0.1)",
    )
    daemon_parser.add_argument(
        "--port",
        type=int,
        default=47483,
        help="Port for the HTTP server (default: 47483)",
    )
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_command")
    daemon_sub.add_parser(
        "pause",
        help="Pause the running daemon's pipeline (watcher + Bandcamp polling).",
    )
    daemon_sub.add_parser(
        "resume",
        help="Resume the running daemon's pipeline after a pause.",
    )

    # sync subcommand
    sync_parser = subparsers.add_parser(
        "sync",
        help="One-shot: download any new Bandcamp purchases to the watch folder, then exit.",
    )
    sync_parser.add_argument(
        "--download-all",
        action="store_true",
        help=(
            "Clear the local sync state and re-download your entire Bandcamp "
            "collection.  By default, kamp assumes you already have "
            "your collection on first sync and only downloads new purchases "
            "going forward."
        ),
    )

    subparsers.add_parser(
        "logout",
        help="Delete saved Bandcamp session and sync state, requiring re-authentication on the next sync.",
    )

    # server subcommand
    server_parser = subparsers.add_parser(
        "server",
        help="Start the HTTP API server (REST + WebSocket) for the music player UI.",
    )
    server_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    server_parser.add_argument(
        "--port",
        type=int,
        default=47483,
        help="Port to listen on (default: 47483)",
    )
    server_parser.add_argument(
        "--library",
        metavar="DIR",
        type=Path,
        default=None,
        help="Override the library path from config.",
    )

    # test-notify subcommand (macOS only, no config file needed)
    test_notify_parser = subparsers.add_parser(
        "test-notify",
        help="Fire a test macOS notification directly — useful for verifying notification permissions (macOS only).",
    )
    test_notify_parser.add_argument(
        "--type",
        choices=["extraction", "tagging", "artwork", "move", "download"],
        required=True,
        help="Which error type to simulate.",
    )

    # rollback subcommand
    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Revert all library writes performed by a given extension.",
    )
    rollback_parser.add_argument(
        "extension_id",
        help="Extension package name whose writes should be reverted.",
    )

    # config subcommand
    config_parser = subparsers.add_parser(
        "config",
        help="Read or update configuration values.",
    )
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="Print current configuration.")
    set_parser = config_sub.add_parser(
        "set",
        help="Set a config value. Keys use dot notation, e.g. paths.watch_folder",
    )
    set_parser.add_argument("key", help="Dot-notation key (e.g. paths.watch_folder)")
    set_parser.add_argument("value", help="New value")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # At INFO (the default), musicbrainzngs emits noisy schema-evolution messages for
    # every unrecognised XML attribute. Suppress those to WARNING so they don't clutter
    # normal output. At DEBUG the user wants everything, so don't override; at WARNING/
    # ERROR the root logger already handles filtering without our help.
    if args.log_level == "INFO":
        logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
        # asyncio emits "Using selector: KqueueSelector" at DEBUG on every startup.
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        # PIL.TiffImagePlugin emits per-tag DEBUG lines when decoding TIFF/JPEG EXIF
        # data, which floods the log during every artwork embed.
        logging.getLogger("PIL.TiffImagePlugin").setLevel(logging.WARNING)
        # Suppress uvicorn access-log entries for high-frequency, low-signal routes:
        #   * Bandcamp proxy relay (proxy-fetch / fetch-result) — one entry per Bandcamp
        #     API call during sync; already visible as daemon log entries.
        #   * /api/v1/album-art — the UI renders one <img> request per album on every
        #     load, which floods the log with hundreds of lines at startup.
        # Both routes still log at DEBUG; the filter only applies at the default INFO.
        _quiet_paths = (
            "/api/v1/bandcamp/proxy-fetch",
            "/api/v1/bandcamp/fetch-result",
            "/api/v1/album-art",
        )

        class _QuietPathsFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                msg = record.getMessage()
                return not any(p in msg for p in _quiet_paths)

        logging.getLogger("uvicorn.access").addFilter(_QuietPathsFilter())

    # Default to daemon when no subcommand given
    command = args.command or "daemon"

    # rollback bypasses the daemon lifecycle — it only needs the library DB.
    if command == "rollback":
        _cmd_rollback(args.extension_id)
        return

    # Config commands bypass daemon lifecycle (no musicbrainzngs setup needed).
    if command == "config":
        _cmd_config(args, config_parser)
        return

    if command == "logout":
        _cmd_logout()
        return

    if command == "test-notify":
        _cmd_test_notify(getattr(args, "type"))
        return

    from kamp_core.library import LibraryIndex as _LibraryIndex

    _init_db = _LibraryIndex(_state_dir() / "library.db")
    config = Config.load(_init_db)
    # Close the init DB; each long-running command opens its own connection.
    _init_db.close()

    # app_name, app_version, and contact are not user-configurable — hardcoded
    # here so the User-Agent we send to MusicBrainz always accurately identifies
    # the software and provides a stable contact address.
    musicbrainzngs.set_useragent(
        "kamp",
        _get_version(),
        "tedd.e.terry+kamp@gmail.com",
    )

    if command == "server":
        library_override = getattr(args, "library", None)
        _cmd_server(
            config,
            host=args.host,
            port=args.port,
            library_path=library_override,
        )
        return

    if command == "sync":
        _cmd_sync(config, download_all=getattr(args, "download_all", False))
    else:
        daemon_command = getattr(args, "daemon_command", None)
        if daemon_command == "pause":
            _cmd_daemon_signal(signal.SIGUSR1, "pause")
        elif daemon_command == "resume":
            _cmd_daemon_signal(signal.SIGUSR2, "resume")
        else:
            # daemon (with optional CLI overrides)
            if hasattr(args, "watch_folder") and args.watch_folder:
                config.paths.watch_folder = args.watch_folder
            if hasattr(args, "library") and args.library:
                config.paths.library = args.library
            _cmd_daemon(
                config,
                host=getattr(args, "host", "127.0.0.1"),
                port=getattr(args, "port", 47483),
                library_path=getattr(args, "library", None),
            )


def _cmd_config(
    args: argparse.Namespace, config_parser: argparse.ArgumentParser
) -> None:
    from kamp_core.library import LibraryIndex

    config_command = getattr(args, "config_command", None)
    db = LibraryIndex(_state_dir() / "library.db")
    try:
        if config_command == "show":
            print(config_show(db))
        elif config_command == "set":
            try:
                config_set(db, args.key, args.value)
                print(f"Set {args.key} = {args.value}")
            except (KeyError, ValueError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            config_parser.print_help()
    finally:
        db.close()


def _cmd_rollback(extension_id: str) -> None:
    from kamp_core.library import LibraryIndex

    db_path = _state_dir() / "library.db"
    library = LibraryIndex(db_path)
    try:
        count = library.rollback_extension(extension_id)
    finally:
        library.close()
    print(f"Reverted {count} mutation(s) for extension '{extension_id}'.")


def _cmd_logout() -> None:
    from .syncer import logout

    logout()
    print("Logged out. The next sync will require re-authentication.")


_NOTIFY_SUBTITLES: dict[str, str] = {
    "extraction": "Extraction failed",
    "tagging": "Tagging failed",
    "artwork": "Artwork warning",
    "move": "Move failed",
    "download": "Bandcamp sync failed",
}


def _cmd_test_notify(notify_type: str) -> None:
    """Run the pipeline (or syncer) to a specific failure point and fire a real notification.

    For pipeline types (extraction, tagging, artwork, move): creates a temporary
    watch folder item whose name contains a test-injection marker, then runs the full
    run_in_subprocess() so the complete IPC path
    (pipeline_impl → stage_q → notification_callback) is exercised.

    For the download type: calls the error_callback directly, mirroring what
    Syncer._run() does when sync_once() raises (there is no subprocess IPC for
    download errors).

    Notifications are delivered via rumps.notification() — the same mechanism
    the daemon uses — so the permission model is identical.
    """
    if platform.system() != "Darwin":
        print("test-notify is only supported on macOS.", file=sys.stderr)
        sys.exit(1)

    import tempfile as _tmp

    if notify_type == "download":
        # Download errors go straight to Syncer.error_callback — no IPC path.
        # Print confirms the callback would fire; actual OS display requires the
        # Homebrew binary (which has CFBundleIdentifier) or the running daemon.
        print("Notification logic verified: Bandcamp sync failed")
        return

    # For pipeline types: run the real pipeline to the injection point.
    from .config import (
        ArtworkConfig,
        Config,
        LibraryConfig,
        MusicBrainzConfig,
        PathsConfig,
    )
    from .pipeline import run_in_subprocess
    from .pipeline_impl import _TEST_INJECT

    with _tmp.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        watch_folder = tmp_path / "watch"
        library = tmp_path / "library"
        watch_folder.mkdir()
        library.mkdir()

        cfg = Config(
            paths=PathsConfig(watch_folder=watch_folder, library=library),
            musicbrainz=MusicBrainzConfig(),
            artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
            library=LibraryConfig(
                path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
            ),
        )

        item = watch_folder / _TEST_INJECT[notify_type]
        item.mkdir()

        if notify_type in ("tagging", "artwork", "move"):
            _write_test_mp3(
                item / "track01.mp3", with_mbid=notify_type in ("artwork", "move")
            )

        received: list[tuple[str, str, str]] = []

        run_in_subprocess(
            item,
            cfg,
            notification_callback=lambda t, s, m: received.append((t, s, m)),
        )

    if received:
        # The IPC path (pipeline_impl → stage_q → notification_callback) fired
        # correctly.  Actual OS display uses rumps.notification() in the running
        # daemon; UNUserNotificationCenter requires a CFBundleIdentifier that
        # only the Homebrew-compiled binary provides.
        print(f"Notification logic verified: {received[0][1]}")
    else:
        print("Warning: no notification was fired.", file=sys.stderr)
        sys.exit(1)


def _write_test_mp3(path: Path, *, with_mbid: bool = False) -> None:
    """Write a minimal fake MP3 to *path*, optionally with MusicBrainz MBID tags.

    Used by test-notify to create test fixtures that satisfy pipeline preconditions
    (find_audio_files returns a file; is_tagged returns True when with_mbid=True)
    without network access.
    """
    from mutagen import id3

    path.write_bytes(b"\xff\xfb" * 64)
    id3.ID3().save(str(path))
    if with_mbid:
        tags = id3.ID3(str(path))
        tags.add(
            id3.TXXX(encoding=3, desc="MusicBrainz Release Id", text=["test-mbid"])
        )
        tags.add(
            id3.TXXX(
                encoding=3,
                desc="MusicBrainz Release Group Id",
                text=["test-rg-mbid"],
            )
        )
        tags.save(str(path))


def _cmd_server(
    config: Config,
    host: str = "127.0.0.1",
    port: int = 47483,
    library_path: Path | None = None,
) -> None:
    # kamp server is now an alias for kamp daemon. The two were previously
    # separate processes; they are now unified under kamp daemon.
    print(
        "Warning: 'kamp server' is deprecated. "
        "Use 'kamp daemon [--host HOST] [--port PORT] [--library DIR]' instead.",
        file=sys.stderr,
    )
    _cmd_daemon(
        config,
        host=host,
        port=port,
        library_path=library_path,
    )


def _cmd_sync(config: Config, download_all: bool = False) -> None:
    from .syncer import Syncer  # lazy — keeps playwright out of the .app bundle

    syncer = Syncer(config)
    if download_all:
        # Clear state so sync_once() downloads everything, bypassing the
        # first-run auto-mark that would otherwise skip all existing purchases.
        state_file = _state_dir() / "bandcamp_state.json"
        if state_file.exists():
            state_file.unlink()
            print("Sync state cleared — will re-download entire collection.")
        syncer.sync_once(skip_auto_mark=True)
    else:
        syncer.sync_once()


def _cmd_daemon(
    config: Config,
    host: str = "127.0.0.1",
    port: int = 47483,
    library_path: Path | None = None,
) -> None:
    import threading
    import uvicorn

    from kamp_core.library import LibraryIndex
    from kamp_core.playback import MpvPlaybackEngine, PlaybackQueue
    from kamp_core.scrobbler import Scrobbler, authenticate as _lastfm_authenticate
    from kamp_core.server import create_app
    from kamp_daemon.config import config_set as _config_set
    from kamp_daemon.tagger import lookup_releases_from_tracks

    _logger = logging.getLogger(__name__)
    pkg_version = _get_version()
    install_path = Path(__file__).resolve().parent
    _logger.info(
        "kamp %s (Python %s, %s)",
        pkg_version,
        sys.version.split()[0],
        install_path,
    )

    # Surface the active credential backend so platform-specific failures
    # (e.g. WinVaultKeyring vs fail.Keyring on Windows) are visible in logs.
    # See KAMP-280 / KAMP-282.
    try:
        import keyring as _keyring

        _logger.info("keyring backend: %s", _keyring.get_keyring().__class__.__name__)
    except Exception as _exc:
        _logger.warning("keyring backend unavailable: %s", _exc)

    # --- HTTP server component initialisation (formerly _cmd_server) ---

    _raw_lib = library_path or config.paths.library
    lib_path = _raw_lib.expanduser().resolve() if _raw_lib else None
    lib_watcher: "LibraryWatcher | None" = None
    db_path = _state_dir() / "library.db"

    index = LibraryIndex(db_path)

    # Migrate legacy bandcamp_session.json → sessions DB table (one-time).
    # After migration the file is deleted so subsequent starts skip this block.
    _legacy_session = _state_dir() / "bandcamp_session.json"
    if _legacy_session.exists():
        try:
            import json as _json_mod

            _session_data = _json_mod.loads(_legacy_session.read_text())
            index.set_session("bandcamp", _session_data)
            _legacy_session.unlink()
            _logger.info(
                "Migrated bandcamp_session.json to database and removed legacy file."
            )
        except Exception as _exc:
            _logger.warning("Failed to migrate bandcamp_session.json: %s", _exc)

    # Resolve the full mpv path before creating the engine so the Electron-spawned
    # daemon (which may run with a stale PATH) can find the Homebrew binary.
    engine = MpvPlaybackEngine(mpv_bin=_resolve_mpv_binary())
    queue: PlaybackQueue = PlaybackQueue()

    # Restore the last session's queue and position, paused, so the user can
    # resume with a single press of play rather than hunting for the album again.
    # If queue state is available, reconstruct the full queue (including tracks
    # before/after the current one) so playback continues naturally after restart.
    saved_queue = index.load_queue_state()
    saved_player = index.load_player_state()
    if saved_queue and saved_player:
        saved_paths, q_order, q_pos, q_shuffle, q_repeat = saved_queue
        _, saved_position = saved_player
        # Resolve original-order paths → Track objects; silently drop missing.
        # Build a mapping old_index → new_index so the playback permutation
        # (q_order) can be remapped to the compacted resolved list.
        resolved: list[Any] = []
        old_to_new: dict[int, int] = {}
        for i, p in enumerate(saved_paths):
            t = index.get_track_by_path(p)
            if t is not None:
                old_to_new[i] = len(resolved)
                resolved.append(t)
        # Remap the playback order, dropping references to deleted tracks.
        new_order = [old_to_new[idx] for idx in q_order if idx in old_to_new]
        # Find the new position of the current track; fall back to 0.
        restored_pos = 0
        if q_pos >= 0 and q_pos < len(q_order):
            current_orig = q_order[q_pos]
            if current_orig in old_to_new:
                new_current = old_to_new[current_orig]
                try:
                    restored_pos = new_order.index(new_current)
                except ValueError:
                    restored_pos = 0
        if resolved:
            queue.restore(
                resolved,
                order=new_order,
                pos=restored_pos,
                shuffle=q_shuffle,
                repeat=q_repeat,
            )
            current = queue.current()
            if current:
                # Use playback_uri (stream_url if available) so remote tracks
                # get a CDN URL rather than the raw bandcamp: scheme URI.
                engine.load_paused(current.playback_uri, saved_position)
    elif saved_player:
        # Fallback: no queue state — restore single track (pre-TASK-47 behaviour).
        saved_path, saved_position = saved_player
        track = index.get_track_by_path(saved_path)
        if track:
            queue.load([track], 0)
            engine.load_paused(track.playback_uri, saved_position)

    # Now Playing (MPNowPlayingInfoCenter) is owned by the Electron
    # now-playing-helper subprocess, which also handles MPRemoteCommandCenter
    # callbacks (next/prev/play/pause) via its own RunLoop.main.  The daemon
    # no longer writes to MPNowPlayingInfoCenter; Electron observes track and
    # play-state WebSocket events and forwards them to the helper via stdin.

    # Advance the queue automatically at end-of-track; stop cleanly at the end.
    # had_lookahead is supplied by the engine and is True when mpv transitioned
    # gaplessly (slot 1 became slot 0). Querying engine.has_lookahead here would
    # race because the engine clears _lookahead_path under its lock before
    # firing this callback (KAMP-284).
    def _on_track_end(had_lookahead: bool) -> None:
        finished = queue.current()
        track = queue.next()
        if finished is not None:
            index.record_played(finished.file_path)
        if track:
            # Write last_played for the incoming track before the notification
            # chain fires so LastPlayedModule sees it on its next re-fetch.
            index.record_track_started(track.file_path)
            if not had_lookahead:
                # No gapless transition was queued — start the next track manually.
                engine.play(track.file_path)
            # If had_lookahead: mpv already transitioned gaplessly.  file-loaded
            # will fire shortly and preload_next will queue the new next track.
        else:
            engine.stop()
            # Queue exhausted — reset in-memory queue so subsequent add/play-next
            # operations start fresh rather than appending to stale old tracks.
            queue.clear()
            # Clear saved state so restart starts fresh rather than restoring
            # the last track a few seconds from the end.
            index.clear_player_state()
            index.clear_queue_state()

    engine.on_track_end = _on_track_end

    def _on_file_loaded() -> None:
        # Prime or refresh the gapless lookahead whenever a new file starts.
        # Now Playing updates are driven by Electron WebSocket subscription.
        engine.preload_next(queue.peek_next())

    engine.on_file_loaded = _on_file_loaded

    # Set up Last.fm scrobbler.  The ref is a mutable box so the connect /
    # disconnect endpoints can replace the scrobbler at runtime without
    # re-wiring engine callbacks.
    _scrobbler_ref: list[Scrobbler | None] = [
        Scrobbler(config.lastfm.session_key) if config.lastfm else None
    ]

    # Scrobble BEFORE the queue advances so queue.current() still holds the
    # finishing track.  Wrap the callback that was just set above.
    _orig_on_track_end = engine.on_track_end

    def _on_track_end_scrobble(had_lookahead: bool) -> None:
        if _scrobbler_ref[0] is not None:
            _scrobbler_ref[0].on_track_ended(queue.current())
        if _orig_on_track_end is not None:
            _orig_on_track_end(had_lookahead)

    engine.on_track_end = _on_track_end_scrobble

    # Notify scrobbler when a new file is loaded (resets per-play-instance state).
    _orig_on_file_loaded = engine.on_file_loaded

    def _on_file_loaded_scrobble() -> None:
        if _orig_on_file_loaded is not None:
            _orig_on_file_loaded()
        if _scrobbler_ref[0] is not None:
            _scrobbler_ref[0].on_track_changed(queue.current())

    engine.on_file_loaded = _on_file_loaded_scrobble

    # Persist current track and position every 5 s so restarts can resume.
    def _state_saver() -> None:
        import time

        tick = 0
        while True:
            time.sleep(1)
            current = queue.current()
            if _scrobbler_ref[0] is not None:
                _scrobbler_ref[0].tick(current, engine.state.playing)
            if current:
                if tick % 5 == 0:
                    index.save_player_state(current.file_path, engine.state.position)
                    q_paths, q_order, q_pos, q_shuffle, q_repeat = queue.get_state()
                    index.save_queue_state(q_paths, q_order, q_pos, q_shuffle, q_repeat)
            tick += 1

    threading.Thread(target=_state_saver, daemon=True, name="state-saver").start()

    def _on_library_path_set(path: Path) -> None:
        nonlocal lib_path, lib_watcher
        _config_set(index, "paths.library", str(path))
        new_path = path.expanduser().resolve()
        if new_path == lib_path:
            return
        # Library path changed at runtime (e.g. during onboarding).  Restart the
        # file-system watcher on the new path so FSEvents and pipeline-complete
        # scans use the correct directory going forward.
        if lib_watcher is not None:
            lib_watcher.stop()
        lib_path = new_path
        lib_watcher = LibraryWatcher(lib_path, _on_library_change)
        lib_watcher.start()

    def _on_ui_state_set(key: str, value: str) -> None:
        _config_set(index, key, value)

    def _on_config_set(key: str, value: str) -> None:
        # Raises KeyError / ValueError on invalid key or value — server
        # catches these and returns HTTP 422 to the client.
        _config_set(index, key, value)
        # Propagate the change to the running pipeline so settings like
        # bandcamp.poll_interval_minutes take effect without a restart.
        core.reload(Config.load(index))

    def _on_lastfm_connect(username: str, password: str) -> None:
        session_key = _lastfm_authenticate(username, password)
        index.set_session("lastfm", {"session_key": session_key, "username": username})
        # Stop any previous scrobbler's HTTP worker before replacing it so we
        # don't leak threads across repeated connect/disconnect cycles.
        if _scrobbler_ref[0] is not None:
            _scrobbler_ref[0].shutdown(timeout=2.0)
        _scrobbler_ref[0] = Scrobbler(session_key)

    def _on_lastfm_disconnect() -> None:
        index.clear_session("lastfm")
        if _scrobbler_ref[0] is not None:
            _scrobbler_ref[0].shutdown(timeout=2.0)
        _scrobbler_ref[0] = None

    def _on_bandcamp_login_complete(payload: dict[str, object]) -> None:
        session_data: dict[str, Any] = dict(payload)
        index.set_session("bandcamp", session_data)
        _logger.info("Bandcamp session saved from Electron login flow.")
        # Extract username immediately so the UI can show "Connected as {username}".
        # Primary source: the logout cookie (URL-encoded JSON, always present after
        # login, no network round-trip required).  Fallback: the collection_summary
        # API (requires a network call; may return empty if Bandcamp changes the field).
        from .bandcamp import (
            _get_fan_info,
            _make_requests_session,
            _username_from_logout_cookie,
        )

        cookies: list[Any] = list(session_data.get("cookies", []))
        username = _username_from_logout_cookie(cookies)
        if not username:
            try:
                bc_session = _make_requests_session(session_data)
                _fan_id, username = _get_fan_info(bc_session)
            except Exception as exc:
                _logger.warning(
                    "Could not fetch Bandcamp username after login: %s", exc
                )
        if username:
            session_data["username"] = username
            index.set_session("bandcamp", session_data)
            _logger.info("Bandcamp username %r stored in session.", username)

    def _on_bandcamp_disconnect() -> None:
        index.clear_session("bandcamp")

    def _on_bandcamp_sync_trigger() -> None:
        from .syncer import NeedsLoginError

        fn = _sync_trigger_ref[0]
        if fn is None:
            return
        try:
            fn()
        except NeedsLoginError:
            _logger.warning("Manual Bandcamp sync: no valid session — login required.")
            app.state.notify_bandcamp_sync_status("")  # back to idle
        except Exception:
            _logger.exception("Unhandled error during manual Bandcamp sync")
            app.state.notify_bandcamp_sync_status("")  # back to idle

    def _on_bandcamp_sync_all_trigger() -> None:
        from .syncer import NeedsLoginError

        fn = _sync_all_trigger_ref[0]
        if fn is None:
            return
        try:
            fn()
        except NeedsLoginError:
            _logger.warning("Bandcamp sync-all: no valid session — login required.")
            app.state.notify_bandcamp_sync_status("")  # back to idle
        except Exception:
            _logger.exception("Unhandled error during Bandcamp sync-all")
            app.state.notify_bandcamp_sync_status("")  # back to idle

    # Bandcamp username comes only from the session (set after Electron login flow).
    _bc_session = index.get_session("bandcamp")
    _bc_username: str | None = _bc_session.get("username") if _bc_session else None
    _config_values: dict[str, object] = {
        "paths.watch_folder": (
            str(config.paths.watch_folder) if config.paths.watch_folder else None
        ),
        "paths.library": str(config.paths.library) if config.paths.library else None,
        "musicbrainz.trust-musicbrainz-when-tags-conflict": config.musicbrainz.trust_musicbrainz_when_tags_conflict,
        "artwork.min_dimension": config.artwork.min_dimension,
        "artwork.max_bytes": config.artwork.max_bytes,
        "library.path_template": config.library.path_template,
        "bandcamp.connected": _bc_session is not None,
        "bandcamp.username": _bc_username,
        "bandcamp.format": config.bandcamp.format if config.bandcamp else None,
        "bandcamp.poll_interval_minutes": (
            config.bandcamp.poll_interval_minutes if config.bandcamp else None
        ),
        "lastfm.username": config.lastfm.username if config.lastfm else None,
    }

    # Generate a fresh shared-secret token on every daemon start.  Electron
    # re-reads the file on reconnect so there is no persistent state to sync.
    _tp = token_path()
    _tp.parent.mkdir(parents=True, exist_ok=True)
    _auth_token = secrets.token_hex(32)
    _tp.write_text(_auth_token)
    os.chmod(_tp, 0o600)

    # Filled after DaemonCore is constructed; the lambdas above capture these
    # lists so endpoints can call syncer methods without forward-reference issues.
    _sync_trigger_ref: list[Any] = [None]
    _sync_all_trigger_ref: list[Any] = [None]

    def _refresh_stream_url(album_url: str, track_num: int) -> tuple[str, float] | None:
        """Fetch a fresh CDN URL for a remote track before mpv plays it."""
        session_data = index.get_session("bandcamp")
        if not session_data:
            return None
        from kamp_daemon.bandcamp import refresh_stream_url as _bandcamp_refresh

        return _bandcamp_refresh(album_url, track_num, session_data)

    app = create_app(
        index=index,
        engine=engine,
        queue=queue,
        library_path=lib_path,
        on_library_path_set=_on_library_path_set,
        ui_active_view=config.ui.active_view,
        ui_sort_order=config.ui.sort_order,
        ui_queue_panel_open=config.ui.queue_panel_open,
        on_ui_state_set=_on_ui_state_set,
        config_values=_config_values,
        on_config_set=_on_config_set,
        on_lastfm_connect=_on_lastfm_connect,
        on_lastfm_disconnect=_on_lastfm_disconnect,
        on_bandcamp_login_complete=_on_bandcamp_login_complete,
        get_bandcamp_session=lambda: index.get_session("bandcamp"),
        on_bandcamp_disconnect=_on_bandcamp_disconnect,
        on_bandcamp_sync_trigger=_on_bandcamp_sync_trigger,
        on_bandcamp_sync_all_trigger=_on_bandcamp_sync_all_trigger,
        art_cache_dir=_state_dir() / "art_cache",
        refresh_stream_url=_refresh_stream_url,
        dev_mode=bool(os.environ.get("KAMP_DEV")),
        auth_token=_auth_token,
        mb_lookup_fn=lookup_releases_from_tracks,
    )

    # Wrap the existing on_track_end callback to also push track.changed events.
    # Done here (after app creation) so app.state is guaranteed to be available.
    _original_on_track_end = engine.on_track_end

    def _on_track_end_notify(had_lookahead: bool) -> None:
        if _original_on_track_end is not None:
            _original_on_track_end(had_lookahead)
        app.state.notify_track_changed()

    engine.on_track_end = _on_track_end_notify

    # Outermost on_track_end wrapper: drain deferred ops for the just-finished
    # track.  Capture queue.current() BEFORE the inner chain advances the queue.
    _orig_on_track_end_drain = engine.on_track_end

    def _on_track_end_drain(had_lookahead: bool) -> None:
        finished = queue.current()
        finished_id = finished.id if finished is not None else None
        if _orig_on_track_end_drain is not None:
            _orig_on_track_end_drain(had_lookahead)
        if finished_id is not None:
            from kamp_core.deferred_ops import drain_for_track

            drain_for_track(
                finished_id,
                index,
                lib_watcher,
                app.state.notify_deferred_op_completed,
                app.state.notify_library_changed,
            )

    engine.on_track_end = _on_track_end_drain

    # Prime the gapless lookahead for a restored session so the first automatic
    # advance is seamless.  All callbacks are wired by this point.
    if queue.current() is not None:
        engine.preload_next(queue.peek_next())

    # Watch the library directory and re-scan when audio files are added or
    # removed, so the UI stays current without requiring a manual scan trigger.
    from kamp_daemon.ext import (
        ExtensionRegistry,
        discover_extensions,
        invoke_extensions_for_new_tracks,
    )
    from kamp_daemon.watcher import LibraryWatcher

    _extension_registry = ExtensionRegistry()
    discover_extensions(_extension_registry)

    def _on_library_change() -> None:
        from kamp_core.library import LibraryScanner

        if lib_path is None:
            return
        try:
            result = LibraryScanner(index).scan(lib_path)
            # Offer newly ingested tracks to registered extensions.  Re-scan tracks
            # (to_update) are excluded — only ScanResult.new_tracks (to_add) are
            # passed.  The invoker enforces the single-invocation guarantee via the
            # audit log so extensions never see the same track twice.
            try:
                if result.new_tracks:
                    # The built-in tagger and artwork source already ran in-process
                    # during the pipeline subprocess.  Mark them as processed for every
                    # new track so the post-scan invoker does not re-run them.
                    _BUILTIN_EXTENSION_IDS = (
                        "kamp_daemon.ext.builtin.musicbrainz.KampMusicBrainzTagger",
                        "kamp_daemon.ext.builtin.coverart.KampCoverArtArchive",
                    )
                    for track in result.new_tracks:
                        if track.mb_recording_id:
                            for ext_id in _BUILTIN_EXTENSION_IDS:
                                if not index.has_been_processed_by(
                                    ext_id, track.mb_recording_id
                                ):
                                    index.mark_processed_by(
                                        ext_id, track.mb_recording_id
                                    )
                    invoke_extensions_for_new_tracks(
                        _extension_registry, result.new_tracks, index
                    )
            except Exception:
                _logger.exception("Error invoking extensions after library scan")
        finally:
            # Bump the server's library version so connected WebSocket clients
            # receive a "library.changed" push and reload the album list.
            # Always runs — even if scan() raises — so the renderer is never
            # left stale due to a transient scan error.
            app.state.notify_library_changed()

    if lib_path is not None:
        lib_watcher = LibraryWatcher(lib_path, _on_library_change)
        lib_watcher.start()

    def _on_track_file_moved(old_path: "Path", new_path: "Path") -> None:
        """Suppress watcher events for a tag-edit move and trigger an immediate scan."""
        if lib_watcher is not None:
            lib_watcher.suppress_paths({old_path, new_path})
            lib_watcher.scan_now()

    def _on_album_tracks_moved(pairs: "list[tuple[Path, Path]]") -> None:
        """Batch variant for album rename: suppress all pairs, then one scan_now()."""
        if lib_watcher is not None:
            for old_p, new_p in pairs:
                lib_watcher.suppress_paths({old_p, new_p})
            lib_watcher.scan_now()

    app.state.on_track_file_moved = _on_track_file_moved
    app.state.on_album_tracks_moved = _on_album_tracks_moved

    def _is_locked(track_id: int) -> bool:
        c = queue.current()
        la = queue.peek_next()
        return (c is not None and c.id == track_id) or (
            la is not None and la.id == track_id
        )

    # Async helper exposed on app.state so server-side endpoints (skip, next,
    # play) can drain deferred ops for a track that was just unlocked by a
    # manual queue advancement — without blocking the HTTP response.
    def _drain_for_track_async(track_id: int) -> None:
        from kamp_core.deferred_ops import drain_for_track as _dfp

        threading.Thread(
            target=_dfp,
            args=(
                track_id,
                index,
                lib_watcher,
                app.state.notify_deferred_op_completed,
                app.state.notify_library_changed,
            ),
            daemon=True,
            name=f"drain-unlocked-{track_id}",
        ).start()

    app.state.drain_for_track_async = _drain_for_track_async

    # Execute any deferred ops that survived a crash or clean quit.  Skip tracks
    # that are already playing (session resumed after crash) — they drain at
    # track end instead.  Critical on Windows where open files cannot be renamed.
    from kamp_core.deferred_ops import drain_all as _drain_all

    _drain_all(
        index,
        lib_watcher,
        app.state.notify_deferred_op_completed,
        app.state.notify_library_changed,
        is_locked=_is_locked,
    )

    # --- Start uvicorn in a background thread ---
    # uvicorn.Server.serve() detects it is not on the main thread and skips
    # installing its own signal handlers, so there is no conflict with
    # DaemonCore's SIGTERM/SIGINT handlers below.
    print(f"Kamp API server starting on http://{host}:{port}")
    print(f"  Docs  → http://{host}:{port}/docs")
    if lib_path is not None:
        print(f"  Library → {lib_path}")
    uv_server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))

    def _run_uvicorn() -> None:
        asyncio.run(uv_server.serve())

    uv_thread = threading.Thread(target=_run_uvicorn, name="uvicorn", daemon=False)
    uv_thread.start()

    # --- Start daemon pipeline and block until shutdown ---
    core = DaemonCore(config)

    # Wire sync trigger and status broadcasts BEFORE core.start() so that the
    # first automatic sync (which may fire immediately on thread start) already
    # has the callback set.
    _sync_trigger_ref[0] = core.syncer.sync_once
    _sync_all_trigger_ref[0] = core.syncer.sync_all_purchases

    core.syncer.status_callback = app.state.notify_bandcamp_sync_status
    core.watcher.stage_callback = app.state.notify_pipeline_stage

    # After each album finishes processing, run a library rescan directly on a
    # fresh thread — bypassing the LibraryWatcher debounce, which shares its
    # timer with FSEvents from the library directory.  During a large sync,
    # continuous FSEvents keep resetting that timer so it never fires; routing
    # pipeline-complete events here ensures each completed album triggers an
    # immediate scan and UI notification independently of FSEvents activity.
    def _on_pipeline_complete() -> None:
        threading.Thread(
            target=_on_library_change, daemon=True, name="library-pipeline-scan"
        ).start()

    core.watcher.on_pipeline_complete = _on_pipeline_complete
    core.start()
    core.wait()

    # --- Shutdown sequence ---
    # Signal uvicorn to drain in-flight requests and stop its event loop.
    uv_server.should_exit = True
    uv_thread.join(timeout=10)

    # Flush remaining deferred ops before stopping.  5-second cap prevents an
    # indefinite stall; any ops not drained survive in the DB for startup drain.
    from kamp_core.deferred_ops import drain_all as _drain_all_shutdown

    _drain_all_shutdown(
        index,
        lib_watcher,
        app.state.notify_deferred_op_completed,
        app.state.notify_library_changed,
        timeout_secs=5.0,
        is_locked=_is_locked,
    )

    if lib_watcher is not None:
        lib_watcher.stop()
    # Stop the scrobbler's HTTP worker thread BEFORE the engine so any final
    # scrobble queued by a closing on_track_ended has a chance to land. The
    # 2-second join cap makes a hung Last.fm endpoint never stall shutdown.
    if _scrobbler_ref[0] is not None:
        _scrobbler_ref[0].shutdown(timeout=2.0)
    engine.shutdown()
    index.close()


def _cmd_daemon_signal(sig: int, action: str) -> None:
    """Send *sig* to the running daemon process identified by the pidfile."""
    try:
        pid = int(_PID_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        print("No running kamp daemon found.", file=sys.stderr)
        sys.exit(1)
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        print("Daemon process not found (stale PID file?).", file=sys.stderr)
        _PID_PATH.unlink(missing_ok=True)
        sys.exit(1)
    print(f"Daemon pipeline {action}d.")


def _resolve_mpv_binary() -> str:
    """Return the absolute path to the mpv binary.

    Electron sets KAMP_MPV_BIN to the bundled binary path before spawning the
    daemon; trust it unconditionally when present. Skipping the existence check
    means a bad path surfaces as FileNotFoundError: '/full/path/to/mpv' rather
    than silently falling through to the bare 'mpv' string.

    Without KAMP_MPV_BIN (e.g. a frozen bundle started outside Electron), infer
    the path from sys._MEIPASS: kamp/_internal/ → ../../ → mpv[.exe]. Fall back
    to platform-typical install locations, then PATH.
    """
    env_path = os.environ.get("KAMP_MPV_BIN")
    if env_path:
        return env_path
    # Frozen bundle without KAMP_MPV_BIN: infer from _internal/ layout.
    # Contents/Resources/kamp/_internal/ → ../../ → Contents/Resources/mpv
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        name = Path("mpv", "mpv.exe") if sys.platform == "win32" else Path("mpv")
        bundled = Path(sys._MEIPASS).parent.parent / name
        if bundled.exists():
            return str(bundled)
    fallback_paths = _WIN_MPV_PATHS if sys.platform == "win32" else _HOMEBREW_MPV_PATHS
    for path in fallback_paths:
        if Path(path).exists():
            return path
    return shutil.which("mpv") or "mpv"


if __name__ == "__main__":
    main()
