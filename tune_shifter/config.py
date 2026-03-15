"""Configuration loading and defaults for tune-shifter."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _state_dir() -> Path:
    """Return a platform-appropriate directory for persistent runtime state."""
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
        return base / "tune-shifter"
    return Path("~/.local/share/tune-shifter").expanduser()


def _default_config_path() -> Path:
    """Return a platform-appropriate default config file path."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "tune-shifter" / "config.toml"
    return Path("~/.config/tune-shifter/config.toml").expanduser()


DEFAULT_CONFIG_PATH = _default_config_path()

DEFAULT_CONFIG_CONTENT = """\
[paths]
staging = "~/Music/staging"
library = "~/Music"

[musicbrainz]
app_name = "tune-shifter"
app_version = "0.1.0"
contact = "user@example.com"  # Update with your contact email

[artwork]
min_dimension = 1000   # minimum width and height in pixels
max_bytes = 1_000_000  # 1 MB

[library]
# Available variables: {artist}, {album_artist}, {album}, {year}, {track}, {title}, {ext}
path_template = "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
"""


@dataclass
class PathsConfig:
    staging: Path
    library: Path


@dataclass
class MusicBrainzConfig:
    app_name: str
    app_version: str
    contact: str


@dataclass
class ArtworkConfig:
    min_dimension: int
    max_bytes: int


@dataclass
class LibraryConfig:
    path_template: str


@dataclass
class BandcampConfig:
    username: str
    cookie_file: Path | None  # if set, bypasses interactive login
    format: str  # e.g. "mp3-v0", "mp3-320", "flac"
    poll_interval_minutes: int  # 0 = manual only


def _prompt(label: str, default: str) -> str:
    """Print a prompt and return the user's input, or *default* if blank."""
    try:
        value = input(f"  {label} [{default}]: ").strip()
    except EOFError:
        value = ""
    return value if value else default


@dataclass
class Config:
    paths: PathsConfig
    musicbrainz: MusicBrainzConfig
    artwork: ArtworkConfig
    library: LibraryConfig
    bandcamp: BandcampConfig | None = None

    @classmethod
    def first_run_setup(cls, path: Path) -> "Config":
        """Interactively collect key config values, write *path*, and return Config.

        Prompts for the three fields with no sensible universal default —
        staging dir, library dir, and MusicBrainz contact email — then writes
        the TOML file (substituting into DEFAULT_CONFIG_CONTENT to preserve
        comments and formatting) and returns the ready-to-use Config.
        """
        print("\nWelcome to tune-shifter! Let's set up your configuration.")
        print(f"(Config will be saved to {path})\n")

        staging = Path(
            _prompt("Staging directory (drop ZIPs/folders here)", "~/Music/staging")
        ).expanduser()
        library = Path(
            _prompt("Library directory (finished files land here)", "~/Music")
        ).expanduser()
        contact = _prompt(
            "Your email (sent in MusicBrainz User-Agent; required by their policy)",
            "user@example.com",
        )

        config = cls(
            paths=PathsConfig(staging=staging, library=library),
            musicbrainz=MusicBrainzConfig(
                app_name="tune-shifter",
                app_version="0.1.0",
                contact=contact,
            ),
            artwork=ArtworkConfig(min_dimension=1000, max_bytes=1_000_000),
            library=LibraryConfig(
                path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
            ),
        )

        path.parent.mkdir(parents=True, exist_ok=True)
        # Substitute user values into the canonical TOML template so the file
        # retains its comments and familiar structure rather than being
        # machine-generated.  Order matters: staging is a prefix of library, so
        # replace the longer string first.
        toml_content = (
            DEFAULT_CONFIG_CONTENT.replace('"~/Music/staging"', f'"{staging}"')
            .replace('"~/Music"', f'"{library}"')
            .replace('"user@example.com"', f'"{contact}"')
        )
        path.write_text(toml_content)
        print(f"\nConfiguration saved to {path}\n")
        return config

    @classmethod
    def bandcamp_setup(cls, path: Path) -> "Config":
        """Interactively collect Bandcamp credentials, append to config, and return Config.

        Called when sync is run without a [bandcamp] section present.
        Appends the new section to the existing file so comments and other
        settings are preserved.
        """
        print("\nNo Bandcamp section found in config. Let's set it up.")
        print(f"(Adding [bandcamp] to {path})\n")

        # username is required — loop until non-empty
        username = ""
        while not username:
            username = _prompt("Bandcamp username", "")
            if not username:
                print("  Username is required.")

        cookie_file_str = _prompt(
            "Cookie file path (leave blank to use interactive login)", ""
        )
        fmt = _prompt("Download format (mp3-v0, mp3-320, flac, …)", "mp3-v0")
        poll_str = _prompt("Poll interval in minutes (0 = manual sync only)", "0")
        poll_interval = int(poll_str) if poll_str.isdigit() else 0

        # Build the TOML snippet — omit cookie_file when not provided
        lines = [
            "\n[bandcamp]",
            f'username = "{username}"',
        ]
        if cookie_file_str:
            lines.append(f'cookie_file = "{cookie_file_str}"')
        lines += [
            f'format = "{fmt}"',
            f"poll_interval_minutes = {poll_interval}",
            "",  # trailing newline
        ]
        with open(path, "a") as f:
            f.write("\n".join(lines))

        print(f"\nBandcamp config saved to {path}\n")
        return cls.load(path)

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "Config":
        """Load config from a TOML file, creating it with defaults if absent."""
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_CONFIG_CONTENT)
            raise FileNotFoundError(
                f"Config file created at {path}. "
                "Please edit it with your staging/library paths and contact email, "
                "then re-run tune-shifter."
            )

        with open(path, "rb") as f:
            raw = tomllib.load(f)

        p = raw["paths"]
        mb = raw["musicbrainz"]
        art = raw["artwork"]
        lib = raw["library"]

        bc_raw = raw.get("bandcamp")
        bandcamp: BandcampConfig | None = None
        if bc_raw:
            cf = bc_raw.get("cookie_file")
            bandcamp = BandcampConfig(
                username=bc_raw["username"],
                cookie_file=Path(cf).expanduser() if cf else None,
                format=bc_raw.get("format", "mp3-v0"),
                poll_interval_minutes=int(bc_raw.get("poll_interval_minutes", 0)),
            )

        return cls(
            paths=PathsConfig(
                staging=Path(p["staging"]).expanduser(),
                library=Path(p["library"]).expanduser(),
            ),
            musicbrainz=MusicBrainzConfig(
                app_name=mb["app_name"],
                app_version=mb["app_version"],
                contact=mb["contact"],
            ),
            artwork=ArtworkConfig(
                min_dimension=int(art["min_dimension"]),
                max_bytes=int(art["max_bytes"]),
            ),
            library=LibraryConfig(
                path_template=lib["path_template"],
            ),
            bandcamp=bandcamp,
        )
