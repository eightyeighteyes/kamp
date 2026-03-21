"""Entry point for the tune-shifter daemon.

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

_SERVICE_LABEL = "com.tune-shifter"
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
        return importlib.metadata.version("tune-shifter")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def main() -> None:
    # Rename the process so `ps` output shows "tune-shifter" instead of
    # "Python".  setproctitle updates argv[0] which is sufficient on Linux;
    # on macOS it also helps ps, but Activity Monitor reads the kernel-level
    # p_comm (set from the executable path at exec time and not writable from
    # userspace), so the menu-bar icon name requires a compiled binary launcher
    # — tracked separately in the backlog.
    try:
        import setproctitle

        setproctitle.setproctitle("tune-shifter")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="tune-shifter",
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
        "--mark-synced",
        action="store_true",
        help=(
            "Record your entire Bandcamp collection as already downloaded "
            "without fetching any files. Run this once if you have already "
            "downloaded everything manually, so future syncs only pick up "
            "new purchases."
        ),
    )

    # service subcommands (macOS launchd)
    install_parser = subparsers.add_parser(
        "install-service",
        help="Register tune-shifter as a launchd user agent (macOS). Starts at login, runs in background.",
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
    subparsers.add_parser("stop", help="Stop the tune-shifter service.")
    subparsers.add_parser("play", help="Start the tune-shifter service.")
    subparsers.add_parser("status", help="Show whether tune-shifter is running.")

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

    # Default to daemon when no subcommand given
    command = args.command or "daemon"

    # Service commands don't require a config file.
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
                "then re-run tune-shifter.",
                file=sys.stderr,
            )
            sys.exit(1)

    # app_name and app_version are not user-configurable — hardcoded here so the
    # User-Agent we send to MusicBrainz always accurately identifies the software.
    musicbrainzngs.set_useragent(
        "tune-shifter",
        _get_version(),
        config.musicbrainz.contact,
    )

    if command == "sync":
        _cmd_sync(config, args.config, mark_synced=getattr(args, "mark_synced", False))
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


def _yn_prompt(question: str, default: bool = True) -> bool:
    """Print a yes/no prompt and return the user's answer as a bool."""
    hint = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"  {question} {hint}: ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw.startswith("y")


def _cmd_sync(config: Config, config_path: Path, mark_synced: bool = False) -> None:
    first_run = False
    if config.bandcamp is None:
        if sys.stdin.isatty():
            config = Config.bandcamp_setup(config_path)
            first_run = True
        else:
            print(
                f"No [bandcamp] section in {config_path}. "
                "Add one manually or run tune-shifter sync interactively.",
                file=sys.stderr,
            )
            sys.exit(1)

    if first_run:
        # Ask whether the collection is already downloaded so we don't re-download
        # everything for users who have already fetched their Bandcamp purchases manually.
        # Default to 'y' — most first-time users have prior downloads.
        mark_synced = _yn_prompt(
            "Have you already downloaded your Bandcamp collection?", default=True
        )

    syncer = Syncer(config)
    if mark_synced:
        syncer.mark_synced()
    else:
        syncer.sync_once()


def _cmd_daemon(config: Config, config_path: Path, menu_bar: bool = False) -> None:
    _logger = logging.getLogger(__name__)
    pkg_version = _get_version()
    install_path = Path(__file__).resolve().parent
    _logger.info(
        "tune-shifter %s (Python %s, %s)",
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
        print("No running tune-shifter daemon found.", file=sys.stderr)
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
#     "Label" = "com.tune-shifter";
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
    """Return True if tune-shifter is registered in the launchd namespace.

    A non-zero exit from `launchctl list` means the label is unknown to launchd.
    Zero exit means the service is registered (running or stopped).
    """
    return _launchctl_list().returncode == 0


def _service_pid() -> int | None:
    """Return the PID of the running tune-shifter service, or None if not running.

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
        print(
            "tune-shifter is not installed as a service. Run tune-shifter install-service first."
        )
        return
    if _service_pid() is None:
        print("tune-shifter is already stopped.")
        return
    # bootout stops and unregisters the service; use check=False so a
    # non-zero exit (e.g. already unloaded) doesn't raise.
    subprocess.run(
        ["launchctl", "bootout", _launchd_domain(), str(_PLIST_PATH)], check=False
    )
    print("tune-shifter stopped.")


def _cmd_play() -> None:
    if not _PLIST_PATH.exists():
        print(
            "tune-shifter is not installed as a service. Run tune-shifter install-service first."
        )
        return
    if _service_pid() is not None:
        print("tune-shifter is already running.")
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
    print("tune-shifter started.")


def _cmd_status() -> None:
    if not _PLIST_PATH.exists():
        print("tune-shifter is not installed as a service.")
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
                print(f"tune-shifter is not running (crashed, last exit: {last_exit})")
                print(f"  Logs → {_LOG_PATH}")
                return
        print("tune-shifter is not running.")
        return
    ps = subprocess.run(
        ["ps", "-p", str(pid), "-o", "etime="],
        capture_output=True,
        text=True,
    )
    uptime = ps.stdout.strip() if ps.returncode == 0 else "unknown"
    print(f"tune-shifter is running (pid {pid}, uptime {uptime})")


def _cmd_install_service(config_path: Path, menu_bar: bool = False) -> None:
    exec_path = shutil.which("tune-shifter") or sys.argv[0]
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
    subprocess.run(
        ["launchctl", "bootstrap", _launchd_domain(), str(_PLIST_PATH)], check=True
    )
    print("tune-shifter installed and started.")
    print(f"  Logs → {_LOG_PATH}")
    print("\nUseful commands:")
    print("  tune-shifter stop             # pause the service")
    print("  tune-shifter play             # resume the service")
    print("  tune-shifter status           # check if it's running")
    print("  tune-shifter uninstall-service  # remove it permanently")


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
