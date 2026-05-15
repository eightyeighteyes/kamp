"""Shared path-rendering utilities for the library path template.

Used by both the ingest pipeline (kamp_daemon/mover.py) and the tag-edit
endpoint (kamp_core/server.py) so that file destinations are computed
identically in both code paths.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_path_component(name: str) -> str:
    """Strip characters that are unsafe in file/directory names."""
    sanitized = _UNSAFE_CHARS.sub("_", name)
    # Trim trailing dots and spaces (Windows compatibility)
    return sanitized.strip(". ")


def make_path_vars(
    artist: str,
    album_artist: str,
    album: str,
    year: str,
    track: int,
    disc: int,
    title: str,
    ext: str,
) -> dict[str, object]:
    return {
        "artist": artist or "Unknown Artist",
        "album_artist": album_artist or artist or "Unknown Artist",
        "album": album or "Unknown Album",
        "year": year or "0000",
        "track": track,
        "disc": disc,
        "title": title or "Unknown Title",
        "ext": ext,
    }


def render_destination(
    tags: dict[str, object],
    library_root: Path,
    path_template: str,
) -> Path:
    """Compute destination path from explicit tag values without reading the file.

    *tags* must contain the keys produced by make_path_vars().  Sanitizes
    string values before rendering so that unsafe characters in tag fields
    (especially '/') are not interpreted as path separators.
    """
    safe_tags = {
        k: sanitize_path_component(v) if isinstance(v, str) else v
        for k, v in tags.items()
    }
    try:
        rendered = path_template.format(**safe_tags)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Path template error: {exc}") from exc

    # Sanitize each component again as a second layer of defence.
    parts = Path(rendered).parts
    safe_parts = [sanitize_path_component(p) for p in parts]
    return library_root.joinpath(*safe_parts)
