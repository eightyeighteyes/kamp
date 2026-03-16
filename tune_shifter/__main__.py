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
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

import musicbrainzngs

from .config import DEFAULT_CONFIG_PATH, Config, _state_dir
from .syncer import Syncer
from .watcher import Watcher

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

    # daemon subcommand (default)
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Watch staging directory and (optionally) poll Bandcamp. Default when no subcommand given.",
    )
    daemon_parser.add_argument("--staging", metavar="DIR", type=Path, default=None)
    daemon_parser.add_argument("--library", metavar="DIR", type=Path, default=None)

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
    subparsers.add_parser(
        "install-service",
        help="Register tune-shifter as a launchd user agent (macOS). Starts at login, runs in background.",
    )
    subparsers.add_parser(
        "uninstall-service",
        help="Remove the launchd user agent registration.",
    )

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
        _cmd_install_service(args.config)
        return
    if command == "uninstall-service":
        _cmd_uninstall_service()
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

    musicbrainzngs.set_useragent(
        config.musicbrainz.app_name,
        config.musicbrainz.app_version,
        config.musicbrainz.contact,
    )

    if command == "sync":
        _cmd_sync(config, args.config, mark_synced=getattr(args, "mark_synced", False))
    else:
        # daemon (with optional CLI overrides)
        if hasattr(args, "staging") and args.staging:
            config.paths.staging = args.staging
        if hasattr(args, "library") and args.library:
            config.paths.library = args.library
        _cmd_daemon(config)


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


def _cmd_daemon(config: Config) -> None:
    _logger = logging.getLogger(__name__)
    pkg_version = _get_version()
    install_path = Path(__file__).resolve().parent
    _logger.info(
        "tune-shifter %s (Python %s, %s)",
        pkg_version,
        sys.version.split()[0],
        install_path,
    )

    watcher = Watcher(config)
    syncer = Syncer(config)

    watcher.start()
    syncer.start()

    def _shutdown(signum: int, frame: object) -> None:
        logging.getLogger(__name__).info("Shutting down…")
        syncer.stop()
        watcher.stop()

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)  # not available on Windows

    watcher.join()


def _cmd_install_service(config_path: Path) -> None:
    exec_path = shutil.which("tune-shifter") or sys.argv[0]
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
    subprocess.run(["launchctl", "load", str(_PLIST_PATH)], check=True)
    print(f"Service installed and started.")
    print(f"  Logs  → {_LOG_PATH}")
    print(f"  Plist → {_PLIST_PATH}")
    print(f"\nTo stop the service temporarily:")
    print(f"  launchctl unload {_PLIST_PATH}")
    print(f"To remove it permanently:")
    print(f"  tune-shifter uninstall-service")


def _cmd_uninstall_service() -> None:
    if not _PLIST_PATH.exists():
        print("Service is not installed.")
        return
    subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], check=False)
    _PLIST_PATH.unlink()
    print("Service removed.")


if __name__ == "__main__":
    main()
