"""Extract ZIP archives and resolve already-extracted folders in the staging area."""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    pass


def extract(path: Path) -> Path:
    """Given a ZIP file or directory, return the path to the audio folder.

    - If *path* is a directory: return it as-is.
    - If *path* is a ZIP: extract to a sibling directory, delete the ZIP, and
      return the extraction directory.

    Raises ExtractionError if the path is neither a ZIP nor a directory, or if
    the ZIP contains no supported audio files.
    """
    if path.is_dir():
        logger.info("Staging item is already a directory: %s", path)
        return path

    if not path.suffix.lower() == ".zip":
        raise ExtractionError(f"Unsupported staging item (not a ZIP or folder): {path}")

    dest = path.parent / path.stem
    logger.info("Extracting %s → %s", path, dest)

    try:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise ExtractionError(f"Failed to open ZIP {path}: {exc}") from exc

    if not _has_audio(dest):
        shutil.rmtree(dest)
        raise ExtractionError(
            f"ZIP {path} contained no supported audio files (.mp3, .m4a, .flac)"
        )

    path.unlink()
    logger.info("Deleted original ZIP %s", path)
    return dest


_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac"}


def _has_audio(directory: Path) -> bool:
    """Return True if *directory* contains at least one supported audio file."""
    return any(
        f.suffix.lower() in _AUDIO_EXTENSIONS
        for f in directory.rglob("*")
        if f.is_file()
    )


def find_audio_files(directory: Path) -> list[Path]:
    """Return a sorted list of supported audio files (.mp3, .m4a, .flac) under *directory*."""
    files = [
        f
        for f in directory.rglob("*")
        if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS
    ]
    return sorted(files)
