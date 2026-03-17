"""Configuration loading and defaults for tune-shifter."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _state_dir() -> Path:
    """Return a platform-appropriate directory for persistent runtime state."""
    if sys.platform == "win32":  # pragma: no cover
        localappdata = os.environ.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
        return base / "tune-shifter"
    return Path("~/.local/share/tune-shifter").expanduser()


def _default_config_path() -> Path:
    """Return a platform-appropriate default config file path."""
    if sys.platform == "win32":  # pragma: no cover
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
            musicbrainz=MusicBrainzConfig(contact=contact),
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
            musicbrainz=MusicBrainzConfig(contact=mb["contact"]),
            artwork=ArtworkConfig(
                min_dimension=int(art["min_dimension"]),
                max_bytes=int(art["max_bytes"]),
            ),
            library=LibraryConfig(
                path_template=lib["path_template"],
            ),
            bandcamp=bandcamp,
        )


# ---------------------------------------------------------------------------
# CLI config management helpers
# ---------------------------------------------------------------------------

# Explicit allowlist of every settable key with its expected Python/TOML type.
# Drives validation and type coercion in config_set(), and ordering in config_show().
_CONFIG_KEY_TYPES: dict[str, type] = {
    "paths.staging": str,
    "paths.library": str,
    "musicbrainz.contact": str,
    "artwork.min_dimension": int,
    "artwork.max_bytes": int,
    "library.path_template": str,
    "bandcamp.username": str,
    "bandcamp.cookie_file": str,
    "bandcamp.format": str,
    "bandcamp.poll_interval_minutes": int,
}

# Keys whose values must come from a fixed set of choices.
_CONFIG_KEY_CHOICES: dict[str, frozenset[str]] = {
    "bandcamp.format": frozenset(
        {"mp3-v0", "mp3-320", "flac", "aac-hi", "vorbis", "alac", "wav"}
    ),
}


def config_show(path: Path) -> str:
    """Return a formatted, human-readable representation of the config file.

    Reads the raw TOML so values reflect the file exactly (paths not expanded).
    """
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    lines: list[str] = []
    for section, values in raw.items():
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"  {key} = {value}")
    return "\n".join(lines)


def config_set(path: Path, key: str, value: str) -> None:
    """Update a single key in the TOML config file.

    *key* must be a dot-notation string like ``paths.staging`` or
    ``artwork.min_dimension``.  *value* is always provided as a string and
    coerced to the appropriate type.  Raises KeyError for unknown or missing
    keys, ValueError for type mismatches.
    """
    if key not in _CONFIG_KEY_TYPES:
        valid = ", ".join(sorted(_CONFIG_KEY_TYPES))
        raise KeyError(f"Unknown config key {key!r}. Valid keys: {valid}")

    target_type = _CONFIG_KEY_TYPES[key]
    if target_type == int:
        try:
            int_value = int(value)
        except ValueError:
            raise ValueError(f"Key {key!r} requires an integer value, got {value!r}")
        toml_value = str(int_value)
    else:
        if key in _CONFIG_KEY_CHOICES:
            valid_choices = sorted(_CONFIG_KEY_CHOICES[key])
            if value not in _CONFIG_KEY_CHOICES[key]:
                raise ValueError(
                    f"Invalid value {value!r} for {key!r}. "
                    f"Supported values: {', '.join(valid_choices)}"
                )
        toml_value = f'"{_toml_escape(value)}"'

    section, field = key.split(".", 1)
    text = path.read_text()
    new_text = _replace_in_section(text, section, field, toml_value)
    path.write_text(new_text)


def _toml_escape(s: str) -> str:
    """Escape a string for embedding in a TOML double-quoted string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _replace_in_section(text: str, section: str, field: str, toml_value: str) -> str:
    """Replace *field*'s value inside *section* in raw TOML text.

    Raises KeyError if the section or field is not found — callers can
    surface a helpful message pointing users to the right setup command.
    """
    # Locate the target [section] header
    section_re = re.compile(r"^\[" + re.escape(section) + r"\]", re.MULTILINE)
    section_match = section_re.search(text)
    if not section_match:
        raise KeyError(
            f"Section [{section}] not found in config. "
            f"Run 'tune-shifter sync' to add [bandcamp], or check your config file."
        )

    # Find where this section ends: the next [header] or EOF
    next_section_re = re.compile(r"^\[[\w-]+\]", re.MULTILINE)
    next_match = next_section_re.search(text, section_match.end())
    section_end = next_match.start() if next_match else len(text)

    section_slice = text[section_match.end() : section_end]

    # Replace the field's value line within this section only.
    # Use a lambda to avoid regex backreference interpretation of toml_value.
    field_re = re.compile(r"^(" + re.escape(field) + r"\s*=\s*).*$", re.MULTILINE)
    replacement_count = 0

    def _replacer(m: re.Match[str]) -> str:
        nonlocal replacement_count
        replacement_count += 1
        return m.group(1) + toml_value

    new_slice = field_re.sub(_replacer, section_slice, count=1)

    if replacement_count == 0:
        raise KeyError(
            f"Key {field!r} not found in [{section}] section. "
            f"It may need to be added to the config file manually."
        )

    return text[: section_match.end()] + new_slice + text[section_end:]
