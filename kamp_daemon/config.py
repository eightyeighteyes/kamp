"""Configuration loading and defaults for kamp.

All application configuration is stored in the SQLite ``settings`` table
(see TASK-132).  ``config.toml``, if present from a prior install, is read
once on startup and migrated; afterwards the file is left in place but never
written again.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib  # stdlib since 3.11; used only for one-time TOML migration
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kamp_core.library import LibraryIndex

logger = logging.getLogger(__name__)


def _state_dir() -> Path:
    """Return a platform-appropriate directory for persistent runtime state."""
    if sys.platform == "win32":  # pragma: no cover
        localappdata = os.environ.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
        return base / "kamp"
    return Path("~/.local/share/kamp").expanduser()


def _default_config_path() -> Path:
    """Return the legacy config.toml path (used only for one-time TOML migration)."""
    if sys.platform == "win32":  # pragma: no cover
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "kamp" / "config.toml"
    return Path("~/.config/kamp/config.toml").expanduser()


# Retained for one-time TOML migration and backwards-compat with existing plists.
DEFAULT_CONFIG_PATH = _default_config_path()


@dataclass
class PathsConfig:
    watch_folder: Path
    library: Path


@dataclass
class MusicBrainzConfig:
    # When False: skip ID3 writes when MB artist/album differs from existing tags.
    trust_musicbrainz_when_tags_conflict: bool = True


@dataclass
class ArtworkConfig:
    min_dimension: int
    max_bytes: int


@dataclass
class LibraryConfig:
    path_template: str


@dataclass
class UiConfig:
    active_view: str = "library"
    sort_order: str = "album_artist"
    queue_panel_open: int = 0


@dataclass
class LastfmConfig:
    username: str
    session_key: str  # obtained via auth.getMobileSession; password never stored


@dataclass
class BandcampConfig:
    format: str  # e.g. "mp3-v0", "mp3-320", "flac"
    poll_interval_minutes: int  # 0 = manual only


def _prompt(label: str, default: str) -> str:
    """Print a prompt and return the user's input, or *default* if blank."""
    try:
        value = input(f"  {label} [{default}]: ").strip()
    except EOFError:
        value = ""
    return value if value else default


# Default values for all 11 active config keys (stored as text in the DB).
# Last.fm credentials are stored in the OS keychain via the sessions table, not here.
_CONFIG_DEFAULTS: dict[str, str] = {
    "paths.watch_folder": "~/Music/staging",
    "paths.library": "~/Music",
    "musicbrainz.trust-musicbrainz-when-tags-conflict": "true",
    "artwork.min_dimension": "1000",
    "artwork.max_bytes": "1000000",
    "library.path_template": "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}",
    "bandcamp.format": "mp3-v0",
    "bandcamp.poll_interval_minutes": "0",
    "ui.active_view": "library",
    "ui.sort_order": "album_artist",
    "ui.queue_panel_open": "0",
}

# Explicit allowlist of every settable key with its expected Python type.
# Drives validation and type coercion in config_set().
_CONFIG_KEY_TYPES: dict[str, type] = {
    "paths.watch_folder": str,
    "paths.library": str,
    "musicbrainz.trust-musicbrainz-when-tags-conflict": bool,
    "artwork.min_dimension": int,
    "artwork.max_bytes": int,
    "library.path_template": str,
    "bandcamp.format": str,
    "bandcamp.poll_interval_minutes": int,
    "ui.active_view": str,
    "ui.sort_order": str,
    "ui.queue_panel_open": int,
}

# Config keys that accept filesystem paths — validated on write.
_PATH_CONFIG_KEYS: frozenset[str] = frozenset({"paths.watch_folder", "paths.library"})

# Filesystem roots that must never be accepted as a config path value.
_FORBIDDEN_PATH_ROOTS: frozenset[Path] = frozenset(
    Path(p).resolve()
    for p in (
        "/",
        "/System",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/etc",
        "/private/etc",
        "/var",
        "/private/var",
        "/Library",
        "/Applications",
        "/dev",
        "/proc",
        "/sys",
    )
)

# Keys that are no longer settable via config_set(); each maps to a user-facing hint.
_DEPRECATED_KEY_MESSAGES: dict[str, str] = {
    # Deprecated in TASK-132: Bandcamp credentials moved to session store.
    "bandcamp.username": "Bandcamp credentials are managed via 'kamp login'.",
    "bandcamp.cookie_file": "Bandcamp credentials are managed via 'kamp login'.",
    # Deprecated in TASK-151: Last.fm credentials moved to OS keychain.
    "lastfm.session_key": "Last.fm credentials are managed via the Last.fm connect flow.",
    "lastfm.username": "Last.fm credentials are managed via the Last.fm connect flow.",
}
_DEPRECATED_KEYS: frozenset[str] = frozenset(_DEPRECATED_KEY_MESSAGES)

# Keys whose values must come from a fixed set of choices.
_CONFIG_KEY_CHOICES: dict[str, frozenset[str]] = {
    "bandcamp.format": frozenset(
        {"mp3-v0", "mp3-320", "flac", "aac-hi", "vorbis", "alac", "wav"}
    ),
    "ui.active_view": frozenset({"library", "now-playing"}),
    "ui.sort_order": frozenset({"album_artist", "album", "date_added", "last_played"}),
}


@dataclass
class Config:
    paths: PathsConfig
    musicbrainz: MusicBrainzConfig
    artwork: ArtworkConfig
    library: LibraryConfig
    bandcamp: BandcampConfig | None = None
    lastfm: LastfmConfig | None = None
    ui: UiConfig = None  # type: ignore[assignment]  # set in __post_init__

    def __post_init__(self) -> None:
        if self.ui is None:
            self.ui = UiConfig()

    @classmethod
    def first_run_setup(cls, db: "LibraryIndex") -> "Config":
        """Interactively collect watch folder and library path, write to DB, return Config."""
        print("\nWelcome to kamp! Let's set up your configuration.\n")

        watch_folder = _prompt(
            "Watch folder (drop ZIPs/folders here)", "~/Music/staging"
        )
        library = _prompt("Library directory (finished files land here)", "~/Music")

        cls.write_defaults(db)
        db.set_setting("paths.watch_folder", watch_folder)
        db.set_setting("paths.library", library)

        print("\nConfiguration saved.\n")
        return cls.load(db)

    @classmethod
    def bandcamp_setup(cls, db: "LibraryIndex") -> "Config":
        """Interactively collect Bandcamp preferences, write to DB, return Config."""
        print("\nLet's set up your Bandcamp preferences.\n")

        fmt = _prompt("Download format (mp3-v0, mp3-320, flac, …)", "mp3-v0")
        poll_str = _prompt("Poll interval in minutes (0 = manual sync only)", "0")
        poll_interval = int(poll_str) if poll_str.isdigit() else 0

        db.set_setting("bandcamp.format", fmt)
        db.set_setting("bandcamp.poll_interval_minutes", str(poll_interval))

        print("\nBandcamp configuration saved.\n")
        return cls.load(db)

    @classmethod
    def write_defaults(cls, db: "LibraryIndex") -> None:
        """Write default values for any keys not yet present in the settings table."""
        existing = db.get_all_settings()
        for key, default in _CONFIG_DEFAULTS.items():
            if key not in existing:
                db.set_setting(key, default)

    @classmethod
    def load(
        cls, db: "LibraryIndex", legacy_config_path: "Path | None" = None
    ) -> "Config":
        """Load config from the DB settings table.

        On the first call after a TOML install, if the settings table is empty
        and the legacy config.toml exists, the file is read once and its values
        are written to the DB (deprecated keys silently dropped).

        Raises FileNotFoundError when there are no settings and no TOML to migrate from.
        """
        existing = db.get_all_settings()

        if not existing:
            toml_path = (
                legacy_config_path
                if legacy_config_path is not None
                else DEFAULT_CONFIG_PATH
            )
            if toml_path.exists():
                existing = _migrate_from_toml(db, toml_path)

            if not existing:
                raise FileNotFoundError("No configuration found.")

        # One-time migration: move Last.fm session key from the settings table
        # (where it was stored as plaintext) to the OS keychain via the sessions
        # table.  Runs once on first load after upgrading; afterwards the settings
        # rows are empty and the session lives in the keychain.
        _session_key = existing.get("lastfm.session_key", "")
        if _session_key:
            _username = existing.get("lastfm.username", "")
            db.set_session(
                "lastfm", {"session_key": _session_key, "username": _username}
            )
            db.set_setting("lastfm.session_key", "")
            db.set_setting("lastfm.username", "")
            existing["lastfm.session_key"] = ""
            existing["lastfm.username"] = ""
            logger.info(
                "Migrated Last.fm session key from settings table to session store."
            )

        # Back-fill defaults for any keys added after the initial setup
        # (e.g. when a new config key is introduced in a later release).
        for key, default in _CONFIG_DEFAULTS.items():
            if key not in existing:
                db.set_setting(key, default)
                existing[key] = default

        return cls._from_settings(existing, db)

    @classmethod
    def _from_settings(cls, settings: dict[str, str], db: "LibraryIndex") -> "Config":
        """Build a Config from a flat key→str settings dict and live DB (for sessions)."""

        def _get(key: str) -> str:
            return settings.get(key, _CONFIG_DEFAULTS.get(key, ""))

        def _bool(key: str) -> bool:
            return _get(key).lower() == "true"

        def _int(key: str) -> int:
            try:
                return int(_get(key))
            except (ValueError, TypeError):
                return int(_CONFIG_DEFAULTS[key])

        lastfm: LastfmConfig | None = None
        _lastfm_session = db.get_session("lastfm")
        if _lastfm_session and _lastfm_session.get("session_key"):
            lastfm = LastfmConfig(
                username=_lastfm_session.get("username", ""),
                session_key=_lastfm_session["session_key"],
            )

        return cls(
            paths=PathsConfig(
                watch_folder=Path(_get("paths.watch_folder")).expanduser(),
                library=Path(_get("paths.library")).expanduser(),
            ),
            musicbrainz=MusicBrainzConfig(
                trust_musicbrainz_when_tags_conflict=_bool(
                    "musicbrainz.trust-musicbrainz-when-tags-conflict"
                ),
            ),
            artwork=ArtworkConfig(
                min_dimension=_int("artwork.min_dimension"),
                max_bytes=_int("artwork.max_bytes"),
            ),
            library=LibraryConfig(
                path_template=_get("library.path_template"),
            ),
            bandcamp=BandcampConfig(
                format=_get("bandcamp.format"),
                poll_interval_minutes=_int("bandcamp.poll_interval_minutes"),
            ),
            lastfm=lastfm,
            ui=UiConfig(
                active_view=_get("ui.active_view"),
                sort_order=_get("ui.sort_order"),
                queue_panel_open=_int("ui.queue_panel_open"),
            ),
        )


def _migrate_from_toml(db: "LibraryIndex", toml_path: Path) -> dict[str, str]:
    """Read legacy config.toml and populate the settings table.

    Deprecated keys (bandcamp.username, bandcamp.cookie_file) are silently
    dropped.  Returns the migrated settings dict so the caller can build a
    Config without a second DB round-trip.
    """
    try:
        with open(toml_path, "rb") as f:
            raw = tomllib.load(f)
    except Exception:
        logger.warning(
            "Could not read legacy config.toml at %s — skipping migration.", toml_path
        )
        return {}

    settings: dict[str, str] = dict(_CONFIG_DEFAULTS)

    p = raw.get("paths", {})
    if "watch_folder" in p:
        settings["paths.watch_folder"] = str(p["watch_folder"])
    elif "staging" in p:  # legacy key name
        settings["paths.watch_folder"] = str(p["staging"])
    if "library" in p:
        settings["paths.library"] = str(p["library"])

    mb = raw.get("musicbrainz", {})
    if "trust-musicbrainz-when-tags-conflict" in mb:
        val = mb["trust-musicbrainz-when-tags-conflict"]
        settings["musicbrainz.trust-musicbrainz-when-tags-conflict"] = (
            "true" if val else "false"
        )

    art = raw.get("artwork", {})
    if "min_dimension" in art:
        settings["artwork.min_dimension"] = str(art["min_dimension"])
    if "max_bytes" in art:
        settings["artwork.max_bytes"] = str(art["max_bytes"])

    lib = raw.get("library", {})
    if "path_template" in lib:
        settings["library.path_template"] = str(lib["path_template"])

    bc = raw.get("bandcamp", {})
    if "format" in bc:
        settings["bandcamp.format"] = str(bc["format"])
    if "poll_interval_minutes" in bc:
        settings["bandcamp.poll_interval_minutes"] = str(
            int(bc["poll_interval_minutes"])
        )
    # bandcamp.username and bandcamp.cookie_file are intentionally not migrated.

    lf = raw.get("lastfm", {})
    if "session_key" in lf and lf["session_key"]:
        # Write directly to the session store rather than the settings table so
        # the session key is never stored as plaintext config.
        db.set_session(
            "lastfm",
            {
                "session_key": str(lf["session_key"]),
                "username": str(lf.get("username", "")),
            },
        )

    ui = raw.get("ui", {})
    if "active_view" in ui:
        settings["ui.active_view"] = str(ui["active_view"])
    if "sort_order" in ui:
        settings["ui.sort_order"] = str(ui["sort_order"])
    if "queue_panel_open" in ui:
        settings["ui.queue_panel_open"] = str(int(ui["queue_panel_open"]))

    for key, value in settings.items():
        db.set_setting(key, value)

    logger.info(
        "Migrated config.toml → database (deprecated keys dropped); "
        "file left in place as backup."
    )
    return settings


# ---------------------------------------------------------------------------
# CLI config management helpers
# ---------------------------------------------------------------------------


def config_show(db: "LibraryIndex") -> str:
    """Return a formatted, human-readable representation of all config settings."""
    settings = db.get_all_settings()

    sections: dict[str, dict[str, str]] = {}
    for key in _CONFIG_KEY_TYPES:
        section, field = key.split(".", 1)
        value = settings.get(key, _CONFIG_DEFAULTS.get(key, ""))
        if section not in sections:
            sections[section] = {}
        sections[section][field] = value

    lines: list[str] = []
    for section, fields in sections.items():
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for field, value in fields.items():
            lines.append(f"  {field} = {value}")
    return "\n".join(lines)


def config_set(db: "LibraryIndex", key: str, value: str) -> None:
    """Update a single config key in the database.

    *key* must be a dot-notation string like ``paths.watch_folder`` or
    ``artwork.min_dimension``.  *value* is always provided as a string and
    coerced to the appropriate type.  Raises KeyError for unknown or deprecated
    keys, ValueError for type mismatches or invalid choices.
    """
    if key in _DEPRECATED_KEYS:
        hint = _DEPRECATED_KEY_MESSAGES[key]
        raise KeyError(
            f"Key {key!r} has been deprecated and is no longer supported. {hint}"
        )

    if key not in _CONFIG_KEY_TYPES:
        valid = ", ".join(sorted(_CONFIG_KEY_TYPES))
        raise KeyError(f"Unknown config key {key!r}. Valid keys: {valid}")

    target_type = _CONFIG_KEY_TYPES[key]
    if target_type == bool:
        if value.lower() not in ("true", "false"):
            raise ValueError(f"Key {key!r} requires true or false, got {value!r}")
        db_value = value.lower()
    elif target_type == int:
        try:
            db_value = str(int(value))
        except ValueError:
            raise ValueError(f"Key {key!r} requires an integer value, got {value!r}")
    else:
        normalized_value = value
        if key in _PATH_CONFIG_KEYS:
            if not value.startswith("/") and not value.startswith("~"):
                raise ValueError(
                    f"Key {key!r} requires an absolute path, got {value!r}"
                )
            # nosec: py/path-injection — absolute-path requirement above rejects
            # traversal; deny-list below blocks system roots and their subtrees.
            resolved = Path(value).expanduser().resolve()  # noqa: S603
            if resolved in _FORBIDDEN_PATH_ROOTS:
                raise ValueError(
                    f"Path {value!r} is not allowed as a value for {key!r}"
                )
        if key in _CONFIG_KEY_CHOICES:
            valid_choices = sorted(_CONFIG_KEY_CHOICES[key])
            if value not in _CONFIG_KEY_CHOICES[key]:
                raise ValueError(
                    f"Invalid value {value!r} for {key!r}. "
                    f"Supported values: {', '.join(valid_choices)}"
                )
        db_value = value

    db.set_setting(key, db_value)
