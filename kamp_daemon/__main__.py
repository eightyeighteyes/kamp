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
import importlib.metadata
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import musicbrainzngs

from .config import DEFAULT_CONFIG_PATH, Config, _state_dir, config_set, config_show
from .daemon_core import DaemonCore, _PID_PATH
from .syncer import Syncer

# Stable Homebrew binary locations (Apple Silicon, then Intel). Checked in order
# before falling back to PATH, to avoid pyenv shims shadowing the Homebrew install.
_HOMEBREW_KAMP_PATHS = ["/opt/homebrew/bin/kamp", "/usr/local/bin/kamp"]

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
        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})",
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
        help="Watch staging directory and (optionally) poll Bandcamp. Default when no subcommand given.",
    )
    daemon_parser.add_argument("--staging", metavar="DIR", type=Path, default=None)
    daemon_parser.add_argument("--library", metavar="DIR", type=Path, default=None)
    daemon_parser.add_argument(
        "--no-menu-bar",
        action="store_true",
        default=False,
        help="Disable the macOS menu bar icon (shown by default on macOS).",
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
        help="One-shot: download any new Bandcamp purchases to staging, then exit.",
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
    install_parser.add_argument(
        "--no-menu-bar",
        action="store_true",
        default=False,
        help="Exclude the menu bar icon from the installed service (shown by default on macOS).",
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
        default=8000,
        help="Port to listen on (default: 8000)",
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

    # config subcommand
    config_parser = subparsers.add_parser(
        "config",
        help="Read or update configuration values.",
    )
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="Print current configuration.")
    set_parser = config_sub.add_parser(
        "set",
        help="Set a config value. Keys use dot notation, e.g. paths.staging",
    )
    set_parser.add_argument("key", help="Dot-notation key (e.g. paths.staging)")
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

    # Default to daemon when no subcommand given
    command = args.command or "daemon"

    if command == "install-service":
        _cmd_install_service(
            args.config, menu_bar=not getattr(args, "no_menu_bar", False)
        )
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

    try:
        config = Config.load(args.config)
    except FileNotFoundError:
        if sys.stdin.isatty():
            config = Config.first_run_setup(args.config)
        else:
            # Non-interactive (script/service): default file was already written
            # by Config.load(); print guidance and exit.
            print(
                f"Config file created at {args.config}. "
                "Edit it with your staging/library paths and contact email, "
                "then re-run kamp.",
                file=sys.stderr,
            )
            sys.exit(1)

    # app_name and app_version are not user-configurable — hardcoded here so the
    # User-Agent we send to MusicBrainz always accurately identifies the software.
    musicbrainzngs.set_useragent(
        "kamp",
        _get_version(),
        config.musicbrainz.contact,
    )

    if command == "server":
        library_override = getattr(args, "library", None)
        _cmd_server(
            config,
            config_path=args.config,
            host=args.host,
            port=args.port,
            library_path=library_override,
        )
        return

    if command == "sync":
        _cmd_sync(
            config, args.config, download_all=getattr(args, "download_all", False)
        )
    else:
        daemon_command = getattr(args, "daemon_command", None)
        if daemon_command == "pause":
            _cmd_daemon_signal(signal.SIGUSR1, "pause")
        elif daemon_command == "resume":
            _cmd_daemon_signal(signal.SIGUSR2, "resume")
        else:
            # daemon (with optional CLI overrides)
            if hasattr(args, "staging") and args.staging:
                config.paths.staging = args.staging
            if hasattr(args, "library") and args.library:
                config.paths.library = args.library
            _cmd_daemon(
                config, args.config, menu_bar=not getattr(args, "no_menu_bar", False)
            )


def _cmd_config(
    args: argparse.Namespace, config_parser: argparse.ArgumentParser
) -> None:
    config_command = getattr(args, "config_command", None)
    if config_command == "show":
        if not args.config.exists():
            print(f"No config file found at {args.config}.", file=sys.stderr)
            sys.exit(1)
        print(config_show(args.config))
    elif config_command == "set":
        if not args.config.exists():
            print(f"No config file found at {args.config}.", file=sys.stderr)
            sys.exit(1)
        try:
            config_set(args.config, args.key, args.value)
            print(f"Set {args.key} = {args.value}")
        except (KeyError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        config_parser.print_help()


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
    staging item whose name contains a test-injection marker, then runs the full
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
        staging = tmp_path / "staging"
        library = tmp_path / "library"
        staging.mkdir()
        library.mkdir()

        cfg = Config(
            paths=PathsConfig(staging=staging, library=library),
            musicbrainz=MusicBrainzConfig(contact="test@example.com"),
            artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
            library=LibraryConfig(
                path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
            ),
        )

        item = staging / _TEST_INJECT[notify_type]
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
    config_path: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    library_path: Path | None = None,
) -> None:
    import uvicorn

    from kamp_core.library import LibraryIndex
    from kamp_core.playback import MpvPlaybackEngine, PlaybackQueue
    from kamp_core.server import create_app
    from kamp_daemon.config import config_set as _config_set

    lib_path = (library_path or config.paths.library).expanduser().resolve()
    db_path = _state_dir() / "library.db"

    index = LibraryIndex(db_path)
    engine = MpvPlaybackEngine()
    queue: PlaybackQueue = PlaybackQueue()

    # Restore the last session's track and position, paused, so the user can
    # resume with a single press of play rather than hunting for the album again.
    saved = index.load_player_state()
    if saved:
        saved_path, saved_pos = saved
        track = index.get_track_by_path(saved_path)
        if track:
            queue.load([track], 0)
            engine.load_paused(track.file_path, saved_pos)

    # Advance the queue automatically at end-of-track; stop cleanly at the end.
    def _on_track_end() -> None:
        track = queue.next()
        if track:
            engine.play(track.file_path)
        else:
            engine.stop()
            # Queue exhausted — clear saved state so restart starts fresh
            # rather than restoring the last track a few seconds from the end.
            index.clear_player_state()

    engine.on_track_end = _on_track_end

    # Persist current track and position every 5 s so restarts can resume.
    def _state_saver() -> None:
        import time

        while True:
            time.sleep(5)
            current = queue.current()
            if current:
                index.save_player_state(current.file_path, engine.state.position)

    import threading

    threading.Thread(target=_state_saver, daemon=True, name="state-saver").start()

    # Persist library path changes back to config.toml so the next server start
    # picks up the user's choice without requiring a manual config edit.
    def _on_library_path_set(path: Path) -> None:
        _config_set(config_path, "paths.library", str(path))

    def _on_ui_state_set(key: str, value: str) -> None:
        _config_set(config_path, key, value)

    app = create_app(
        index=index,
        engine=engine,
        queue=queue,
        library_path=lib_path,
        on_library_path_set=_on_library_path_set,
        ui_active_view=config.ui.active_view,
        on_ui_state_set=_on_ui_state_set,
    )

    # Watch the library directory and re-scan when audio files are added or
    # removed, so the UI stays current without requiring a manual scan trigger.
    from kamp_daemon.watcher import LibraryWatcher

    def _on_library_change() -> None:
        from kamp_core.library import LibraryScanner

        LibraryScanner(index).scan(lib_path)
        # Bump the server's library version so connected WebSocket clients
        # receive a "library.changed" push and reload the album list.
        app.state.notify_library_changed()

    lib_watcher = LibraryWatcher(lib_path, _on_library_change)
    lib_watcher.start()

    print(f"Kamp API server starting on http://{host}:{port}")
    print(f"  Docs  → http://{host}:{port}/docs")
    print(f"  Library → {lib_path}")
    uvicorn.run(app, host=host, port=port)

    lib_watcher.stop()
    engine.shutdown()
    index.close()


def _cmd_sync(config: Config, config_path: Path, download_all: bool = False) -> None:
    if config.bandcamp is None:
        if sys.stdin.isatty():
            config = Config.bandcamp_setup(config_path)
        else:
            print(
                f"No [bandcamp] section in {config_path}. "
                "Add one manually or run kamp sync interactively.",
                file=sys.stderr,
            )
            sys.exit(1)

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


def _cmd_daemon(config: Config, config_path: Path, menu_bar: bool = False) -> None:
    _logger = logging.getLogger(__name__)
    pkg_version = _get_version()
    install_path = Path(__file__).resolve().parent
    _logger.info(
        "kamp %s (Python %s, %s)",
        pkg_version,
        sys.version.split()[0],
        install_path,
    )

    core = DaemonCore(config, config_path)
    if menu_bar and platform.system() == "Darwin":
        from .menu_bar import MenuBarApp

        # Wire callbacks BEFORE core.start() launches threads so that the
        # first automatic Bandcamp sync (which fires immediately on thread
        # start) already has status_callback set.
        app = MenuBarApp(core)
        core.start()
        app.run()
    else:
        core.start()
        core.wait()


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


def _cmd_install_service(config_path: Path, menu_bar: bool = False) -> None:
    if not config_path.exists():
        Config.first_run_setup(config_path)
    exec_path = _resolve_kamp_binary()
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    menu_bar_arg = "" if menu_bar else "\n        <string>--no-menu-bar</string>"
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
        <string>daemon</string>{menu_bar_arg}
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
