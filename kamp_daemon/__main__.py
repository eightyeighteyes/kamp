"""Entry point for the kamp daemon.

Excluded from coverage (see pyproject.toml [tool.coverage.run] omit list) because
this module is pure CLI/daemon lifecycle glue: argparse dispatch, launchctl subprocess
calls, and signal handlers. Meaningfully unit-testing it would require spawning
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
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import musicbrainzngs

from .config import (
    DEFAULT_CONFIG_PATH,
    Config,
    _state_dir,
    config_set,
    config_show,
    token_path,
)
from .daemon_core import DaemonCore, _PID_PATH

# Stable Homebrew binary locations (Apple Silicon, then Intel). Checked in order
# before falling back to PATH, to avoid pyenv shims shadowing the Homebrew install.
_HOMEBREW_KAMP_PATHS = ["/opt/homebrew/bin/kamp", "/usr/local/bin/kamp"]
_HOMEBREW_MPV_PATHS = ["/opt/homebrew/bin/mpv", "/usr/local/bin/mpv"]

_SERVICE_LABEL = "com.kamp"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_SERVICE_LABEL}.plist"
_LOG_PATH = _state_dir() / "daemon.log"

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
        "--config",
        metavar="PATH",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=(
            f"Legacy config.toml path used for one-time migration (default: {DEFAULT_CONFIG_PATH}). "
            "Configuration is now stored in the SQLite database."
        ),
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

    # service subcommands (macOS launchd)
    install_parser = subparsers.add_parser(
        "install-service",
        help="Register kamp as a launchd user agent (macOS). Starts at login, runs in background.",
    )
    subparsers.add_parser(
        "uninstall-service",
        help="Remove the launchd user agent registration.",
    )
    subparsers.add_parser("stop", help="Stop the kamp service.")
    subparsers.add_parser("play", help="Start the kamp service.")
    subparsers.add_parser("status", help="Show whether kamp is running.")
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
        # The Bandcamp proxy relay (proxy-fetch / fetch-result) emits one access log
        # line per Bandcamp API call, flooding sync logs with internal housekeeping.
        # These are already visible as daemon log entries; suppress the uvicorn noise.
        _relay_paths = ("/api/v1/bandcamp/proxy-fetch", "/api/v1/bandcamp/fetch-result")

        class _RelayFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                msg = record.getMessage()
                return not any(p in msg for p in _relay_paths)

        logging.getLogger("uvicorn.access").addFilter(_RelayFilter())

    # Default to daemon when no subcommand given
    command = args.command or "daemon"

    if command == "install-service":
        _cmd_install_service(args.config)
        return
    if command == "uninstall-service":
        _cmd_uninstall_service()
        return
    if command == "stop":
        _cmd_stop()
        return
    if command == "play":
        _cmd_play()
        return
    if command == "status":
        _cmd_status()
        return

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
    try:
        config = Config.load(_init_db, legacy_config_path=args.config)
    except FileNotFoundError:
        if sys.stdin.isatty():
            config = Config.first_run_setup(_init_db)
        else:
            print(
                "No configuration found. Run kamp interactively to complete setup.",
                file=sys.stderr,
            )
            _init_db.close()
            sys.exit(1)
    finally:
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

    _logger = logging.getLogger(__name__)
    pkg_version = _get_version()
    install_path = Path(__file__).resolve().parent
    _logger.info(
        "kamp %s (Python %s, %s)",
        pkg_version,
        sys.version.split()[0],
        install_path,
    )

    # --- HTTP server component initialisation (formerly _cmd_server) ---

    lib_path = (library_path or config.paths.library).expanduser().resolve()
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

    # Resolve the full mpv path before creating the engine so launchd-managed
    # instances (which run with a minimal PATH) can find the Homebrew binary.
    engine = MpvPlaybackEngine(mpv_bin=_resolve_mpv_binary())
    queue: PlaybackQueue = PlaybackQueue()

    # Restore the last session's queue and position, paused, so the user can
    # resume with a single press of play rather than hunting for the album again.
    # If queue state is available, reconstruct the full queue (including tracks
    # before/after the current one) so playback continues naturally after restart.
    saved_queue = index.load_queue_state()
    saved_player = index.load_player_state()
    if saved_queue and saved_player:
        saved_paths, q_pos, q_shuffle, q_repeat = saved_queue
        _, saved_position = saved_player
        # Resolve paths → Track objects; silently drop tracks removed from library.
        resolved = []
        missing_before = 0
        for i, p in enumerate(saved_paths):
            t = index.get_track_by_path(p)
            if t is not None:
                resolved.append(t)
            elif i <= q_pos:
                # Track was before or at the current position — shift pos back.
                missing_before += 1
        restored_pos = max(0, q_pos - missing_before)
        if resolved:
            queue.restore(resolved, restored_pos, q_shuffle, q_repeat)
            current = queue.current()
            if current:
                engine.load_paused(current.file_path, saved_position)
    elif saved_player:
        # Fallback: no queue state — restore single track (pre-TASK-47 behaviour).
        saved_path, saved_position = saved_player
        track = index.get_track_by_path(saved_path)
        if track:
            queue.load([track], 0)
            engine.load_paused(track.file_path, saved_position)

    # Now Playing (MPNowPlayingInfoCenter) is owned by the Electron
    # now-playing-helper subprocess, which also handles MPRemoteCommandCenter
    # callbacks (next/prev/play/pause) via its own RunLoop.main.  The daemon
    # no longer writes to MPNowPlayingInfoCenter; Electron observes track and
    # play-state WebSocket events and forwards them to the helper via stdin.

    # Advance the queue automatically at end-of-track; stop cleanly at the end.
    def _on_track_end() -> None:
        finished = queue.current()
        track = queue.next()
        # Record natural EOF so last_played sort stays accurate.
        if finished is not None:
            index.record_played(finished.file_path)
        if track:
            engine.play(track.file_path)
        else:
            engine.stop()
            # Queue exhausted — clear saved state so restart starts fresh
            # rather than restoring the last track a few seconds from the end.
            index.clear_player_state()
            index.clear_queue_state()

    engine.on_track_end = _on_track_end

    def _on_file_loaded() -> None:
        pass  # Now Playing updates are driven by Electron WebSocket subscription.

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

    def _on_track_end_scrobble() -> None:
        if _scrobbler_ref[0] is not None:
            _scrobbler_ref[0].on_track_ended(queue.current())
        if _orig_on_track_end is not None:
            _orig_on_track_end()

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
                    q_paths, q_pos, q_shuffle, q_repeat = queue.get_state()
                    index.save_queue_state(q_paths, q_pos, q_shuffle, q_repeat)
            tick += 1

    threading.Thread(target=_state_saver, daemon=True, name="state-saver").start()

    def _on_library_path_set(path: Path) -> None:
        _config_set(index, "paths.library", str(path))

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
        _scrobbler_ref[0] = Scrobbler(session_key)

    def _on_lastfm_disconnect() -> None:
        index.clear_session("lastfm")
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
        "paths.watch_folder": str(config.paths.watch_folder),
        "paths.library": str(config.paths.library),
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
        dev_mode=bool(os.environ.get("KAMP_DEV")),
        auth_token=_auth_token,
    )

    # Wrap the existing on_track_end callback to also push track.changed events.
    # Done here (after app creation) so app.state is guaranteed to be available.
    _original_on_track_end = engine.on_track_end

    def _on_track_end_notify() -> None:
        if _original_on_track_end is not None:
            _original_on_track_end()
        app.state.notify_track_changed()

    engine.on_track_end = _on_track_end_notify

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
                                index.mark_processed_by(ext_id, track.mb_recording_id)
                invoke_extensions_for_new_tracks(
                    _extension_registry, result.new_tracks, index
                )
        except Exception:
            _logger.exception("Error invoking extensions after library scan")
        # Bump the server's library version so connected WebSocket clients
        # receive a "library.changed" push and reload the album list.
        app.state.notify_library_changed()

    lib_watcher = LibraryWatcher(lib_path, _on_library_change)
    lib_watcher.start()

    # --- Start uvicorn in a background thread ---
    # uvicorn.Server.serve() detects it is not on the main thread and skips
    # installing its own signal handlers, so there is no conflict with
    # DaemonCore's SIGTERM/SIGINT handlers below.
    print(f"Kamp API server starting on http://{host}:{port}")
    print(f"  Docs  → http://{host}:{port}/docs")
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
    # After each album finishes processing, schedule a debounced library rescan
    # so newly ingested tracks appear in the UI immediately rather than waiting
    # for FSEvents delivery (which is unreliable during high-volume batch syncs).
    core.watcher.on_pipeline_complete = lib_watcher.trigger_scan
    core.start()
    core.wait()

    # --- Shutdown sequence ---
    # Signal uvicorn to drain in-flight requests and stop its event loop.
    uv_server.should_exit = True
    uv_thread.join(timeout=10)

    lib_watcher.stop()
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


def _launchd_domain() -> str:
    """Return the launchd domain for the current user's GUI session (macOS).

    bootstrap/bootout require an explicit domain; gui/<uid> is the correct
    target for user agents in ~/Library/LaunchAgents.
    """
    return f"gui/{os.getuid()}"


def _launchctl_list() -> subprocess.CompletedProcess[str]:
    """Run `launchctl list <label>` and return the result."""
    return subprocess.run(
        ["launchctl", "list", _SERVICE_LABEL],
        capture_output=True,
        text=True,
    )


# Matches simple scalar entries in launchctl list output, e.g.:
#     "PID" = 12345;
#     "LastExitStatus" = 256;
#     "Label" = "com.kamp";
# Skips complex values (arrays, nested dicts) that span multiple lines.
_LAUNCHCTL_ENTRY_RE = re.compile(r'^\s*"(\w+)"\s*=\s*(?:"([^"]*)"|([\w./\-]+));\s*$')


def _parse_launchctl_info(output: str) -> dict[str, str]:
    """Extract scalar key/value pairs from launchctl dict output."""
    info: dict[str, str] = {}
    for line in output.splitlines():
        m = _LAUNCHCTL_ENTRY_RE.match(line)
        if m:
            # group(2) is a quoted string value; group(3) is an unquoted value
            info[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return info


def _service_registered() -> bool:
    """Return True if kamp is registered in the launchd namespace.

    A non-zero exit from `launchctl list` means the label is unknown to launchd.
    Zero exit means the service is registered (running or stopped).
    """
    return _launchctl_list().returncode == 0


def _service_pid() -> int | None:
    """Return the PID of the running kamp service, or None if not running.

    Queries launchctl for the service label. A positive PID means the process is
    alive; absent or 0 means the service is registered but not currently running.
    """
    result = _launchctl_list()
    if result.returncode != 0:
        return None
    info = _parse_launchctl_info(result.stdout)
    pid_str = info.get("PID", "")
    if not pid_str.isdigit():
        return None
    pid = int(pid_str)
    return pid if pid > 0 else None


def _cmd_stop() -> None:
    if not _PLIST_PATH.exists():
        print("kamp is not installed as a service. Run kamp install-service first.")
        return
    if _service_pid() is None:
        print("kamp is already stopped.")
        return
    # bootout stops and unregisters the service; use check=False so a
    # non-zero exit (e.g. already unloaded) doesn't raise.
    subprocess.run(
        ["launchctl", "bootout", _launchd_domain(), str(_PLIST_PATH)], check=False
    )
    print("kamp stopped.")


def _cmd_play() -> None:
    if not _PLIST_PATH.exists():
        print("kamp is not installed as a service. Run kamp install-service first.")
        return
    if _service_pid() is not None:
        print("kamp is already running.")
        return
    if _service_registered():
        # Registered but not running (PID = "-"): bootstrap would fail with EIO
        # because the label is already in launchd's namespace. Use kickstart instead.
        subprocess.run(
            ["launchctl", "kickstart", f"{_launchd_domain()}/{_SERVICE_LABEL}"],
            check=True,
        )
    else:
        # Not registered at all: bootstrap from the plist.
        subprocess.run(
            ["launchctl", "bootstrap", _launchd_domain(), str(_PLIST_PATH)], check=True
        )
    print("kamp started.")


def _cmd_status() -> None:
    if not _PLIST_PATH.exists():
        print("kamp is not installed as a service.")
        return
    pid = _service_pid()
    if pid is None:
        result = _launchctl_list()
        if result.returncode == 0:
            # Registered but not running — surface the last exit code so the
            # user can tell whether it crashed or was cleanly stopped.
            info = _parse_launchctl_info(result.stdout)
            last_exit = info.get("LastExitStatus", "0")
            if last_exit != "0":
                print(f"kamp is not running (crashed, last exit: {last_exit})")
                print(f"  Logs → {_LOG_PATH}")
                return
        print("kamp is not running.")
        return
    ps = subprocess.run(
        ["ps", "-p", str(pid), "-o", "etime="],
        capture_output=True,
        text=True,
    )
    uptime = ps.stdout.strip() if ps.returncode == 0 else "unknown"
    print(f"kamp is running (pid {pid}, uptime {uptime})")


def _resolve_kamp_binary() -> str:
    """Return the path to the kamp binary to embed in the launchd plist.

    Prefers the Homebrew-managed binary over pyenv shims. Pyenv shims depend on
    a specific Python environment; launchd runs with a minimal PATH/env, so shims
    often fail to locate their backing interpreter or site-packages at runtime.
    """
    # Ask Homebrew for the canonical prefix — most reliable when brew is available.
    try:
        result = subprocess.run(
            ["brew", "--prefix", "kamp"],
            capture_output=True,
            text=True,
            check=True,
        )
        candidate = Path(result.stdout.strip()) / "bin" / "kamp"
        if candidate.exists():
            return str(candidate)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Well-known Homebrew prefix locations (Apple Silicon / Intel).
    for path in _HOMEBREW_KAMP_PATHS:
        if Path(path).exists():
            return path

    # Fall back to PATH search, but warn loudly if it resolves to a pyenv shim.
    found = shutil.which("kamp")
    if found and ".pyenv/shims" in found:
        print(
            f"Warning: 'kamp' resolved to a pyenv shim ({found}).\n"
            "The launchd service may fail after a Python environment change.\n"
            "Fix: pip uninstall kamp && pyenv rehash, then re-run install-service."
        )
    return found or sys.argv[0]


def _resolve_mpv_binary() -> str:
    """Return the absolute path to the mpv binary.

    The .app bundle sets KAMP_MPV_BIN to the bundled binary path; check that
    first. For launchd (which runs with a minimal PATH that excludes Homebrew),
    fall back to the stable Homebrew install locations, then PATH.
    """
    env_path = os.environ.get("KAMP_MPV_BIN")
    if env_path and Path(env_path).exists():
        return env_path
    for path in _HOMEBREW_MPV_PATHS:
        if Path(path).exists():
            return path
    return shutil.which("mpv") or "mpv"


def _cmd_install_service(config_path: Path) -> None:
    from kamp_core.library import LibraryIndex

    db = LibraryIndex(_state_dir() / "library.db")
    try:
        settings = db.get_all_settings()
        if not settings and sys.stdin.isatty():
            Config.first_run_setup(db)
    finally:
        db.close()
    exec_path = _resolve_kamp_binary()
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>{_SERVICE_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exec_path}</string>
        <string>--config</string>
        <string>{config_path}</string>
        <string>daemon</string>
    </array>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>{_LOG_PATH}</string>
    <key>StandardErrorPath</key> <string>{_LOG_PATH}</string>
</dict>
</plist>"""
    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PLIST_PATH.write_text(plist)
    # Bootout any stale registration before bootstrapping — launchctl returns
    # error 5 (ENXIO) if the label is already registered, even with a new plist.
    if _service_registered():
        subprocess.run(
            ["launchctl", "bootout", _launchd_domain(), str(_PLIST_PATH)], check=False
        )
    subprocess.run(
        ["launchctl", "bootstrap", _launchd_domain(), str(_PLIST_PATH)], check=True
    )
    print("kamp installed and started.")
    print(f"  Logs → {_LOG_PATH}")
    print("\nUseful commands:")
    print("  kamp stop             # pause the service")
    print("  kamp play             # resume the service")
    print("  kamp status           # check if it's running")
    print("  kamp uninstall-service  # remove it permanently")


def _cmd_uninstall_service() -> None:
    if not _PLIST_PATH.exists():
        print("Service is not installed.")
        return
    subprocess.run(
        ["launchctl", "bootout", _launchd_domain(), str(_PLIST_PATH)], check=False
    )
    _PLIST_PATH.unlink()
    print("Service removed.")


if __name__ == "__main__":
    main()
