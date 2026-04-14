"""Extract ZIP archives and resolve already-extracted folders from the watch folder."""

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

    if path.suffix.lower() in AUDIO_EXTENSIONS:
        # A lone audio file: move it into a sibling directory so the rest of
        # the pipeline (find_audio_files, tagger, mover) operates on a folder
        # as expected.
        dest = path.parent / path.stem
        dest.mkdir(exist_ok=True)
        shutil.move(str(path), dest / path.name)
        logger.info("Moved single audio file %s → %s/", path.name, dest)
        return dest

    if not path.suffix.lower() == ".zip":
        raise ExtractionError(
            f"Unsupported watch folder item (not a ZIP or folder): {path}"
        )

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
            f"ZIP {path} contained no supported audio files (.mp3, .m4a, .flac, .ogg)"
        )

    path.unlink()
    logger.info("Deleted original ZIP %s", path)
    return dest


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".ogg"}

# Keep the private alias so internal helpers don't need updating
_AUDIO_EXTENSIONS = AUDIO_EXTENSIONS


def _has_audio(directory: Path) -> bool:
    """Return True if *directory* contains at least one supported audio file."""
    return any(
        f.suffix.lower() in _AUDIO_EXTENSIONS
        for f in directory.rglob("*")
        if f.is_file()
    )


def find_audio_files(directory: Path) -> list[Path]:
    """Return a sorted list of supported audio files (.mp3, .m4a, .flac, .ogg) under *directory*."""
    files = [
        f
        for f in directory.rglob("*")
        if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS
    ]
    return sorted(files)
