"""Move tagged audio files into the library using a configurable path template."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import mutagen.flac
import mutagen.id3 as _id3
import mutagen.mp4
import mutagen.oggvorbis

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class MoveError(Exception):
    pass


def move_to_library(
    audio_files: list[Path],
    staging_dir: Path,
    library_root: Path,
    path_template: str,
) -> list[Path]:
    """Move all *audio_files* into *library_root* using *path_template*.

    Returns a list of destination paths.  Raises MoveError if any individual
    move fails after the others have already completed.
    """
    destinations: list[Path] = []
    errors: list[str] = []

    for src in audio_files:
        try:
            dest = _destination(src, library_root, path_template)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), dest)
            logger.info("Moved %s → %s", src, dest)
            destinations.append(dest)
        except Exception as exc:
            errors.append(f"{src}: {exc}")

    if errors:
        raise MoveError("Some files could not be moved:\n" + "\n".join(errors))

    _cleanup_staging(staging_dir)
    return destinations


def _destination(src: Path, library_root: Path, path_template: str) -> Path:
    tags = _read_tags(src)
    # Sanitize string values before rendering so that unsafe characters (especially '/')
    # in tag fields like title don't get interpreted as path separators.
    safe_tags = {k: _sanitize(v) if isinstance(v, str) else v for k, v in tags.items()}
    try:
        rendered = path_template.format(**safe_tags)
    except (KeyError, ValueError) as exc:
        raise MoveError(f"Path template error for {src}: {exc}") from exc

    # Sanitize each path component as a second layer of defence.
    parts = Path(rendered).parts
    safe_parts = [_sanitize(p) for p in parts]
    return library_root.joinpath(*safe_parts)


def _sanitize(name: str) -> str:
    """Strip characters that are unsafe in file/directory names."""
    sanitized = _UNSAFE_CHARS.sub("_", name)
    # Trim trailing dots and spaces (Windows compatibility)
    return sanitized.strip(". ")


def _read_tags(path: Path) -> dict[str, object]:
    """Extract template variables from the file's current tags."""
    ext = path.suffix.lstrip(".")
    artist = ""
    album_artist = ""
    album = ""
    year = ""
    track = 0
    disc = 1
    title = path.stem

    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            t = _id3.ID3(str(path))
            artist = str(t.get("TPE1")) if t.get("TPE1") else ""
            album_artist = str(t.get("TPE2")) if t.get("TPE2") else artist
            album = str(t.get("TALB")) if t.get("TALB") else ""
            year = str(t.get("TDRC"))[:4] if t.get("TDRC") else ""
            trck_tag = t.get("TRCK")
            trck = str(trck_tag).split("/")[0] if trck_tag else "0"
            track = int(trck) if trck.isdigit() else 0
            tpos_tag = t.get("TPOS")
            tpos = str(tpos_tag).split("/")[0] if tpos_tag else "1"
            disc = int(tpos) if tpos.isdigit() else 1
            title = str(t.get("TIT2")) if t.get("TIT2") else path.stem
        elif suffix == ".m4a":
            mp4 = mutagen.mp4.MP4(str(path))
            if mp4.tags is not None:
                t4 = mp4.tags
                art_v = t4.get("\xa9ART")
                artist = str(art_v[0]) if art_v else ""
                aar_v = t4.get("aART")
                album_artist = str(aar_v[0]) if aar_v else artist
                alb_v = t4.get("\xa9alb")
                album = str(alb_v[0]) if alb_v else ""
                day_v = t4.get("\xa9day")
                year = str(day_v[0])[:4] if day_v else ""
                trkn_v = t4.get("trkn")
                track = trkn_v[0][0] if trkn_v else 0  # type: ignore[index]
                disk_v = t4.get("disk")
                disc = disk_v[0][0] if disk_v else 1  # type: ignore[index]
                nam_v = t4.get("\xa9nam")
                title = str(nam_v[0]) if nam_v else path.stem
        elif suffix == ".flac":
            flac = mutagen.flac.FLAC(str(path))
            if flac.tags is not None:

                def _get(key: str) -> str:
                    vals = flac.tags.get(key)  # type: ignore[union-attr]
                    return vals[0] if vals else ""

                artist = _get("ARTIST")
                album_artist = _get("ALBUMARTIST") or artist
                album = _get("ALBUM")
                year = _get("DATE")[:4] if _get("DATE") else ""
                trck_s = _get("TRACKNUMBER").split("/")[0]
                track = int(trck_s) if trck_s.isdigit() else 0
                disc_s = _get("DISCNUMBER").split("/")[0]
                disc = int(disc_s) if disc_s.isdigit() else 1
                title = _get("TITLE") or path.stem
        elif suffix == ".ogg":
            ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if ogg.tags is not None:

                def _oget(key: str) -> str:
                    vals = ogg.tags.get(key)  # type: ignore[union-attr]
                    return vals[0] if vals else ""

                artist = _oget("ARTIST")
                album_artist = _oget("ALBUMARTIST") or artist
                album = _oget("ALBUM")
                year = _oget("DATE")[:4] if _oget("DATE") else ""
                trck_s = _oget("TRACKNUMBER").split("/")[0]
                track = int(trck_s) if trck_s.isdigit() else 0
                disc_s = _oget("DISCNUMBER").split("/")[0]
                disc = int(disc_s) if disc_s.isdigit() else 1
                title = _oget("TITLE") or path.stem
    except Exception:
        pass

    return _make_vars(artist, album_artist, album, year, track, disc, title, ext)


def _make_vars(
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


def _cleanup_staging(staging_dir: Path) -> None:
    """Remove the staging subdirectory and any remaining non-audio files."""
    try:
        shutil.rmtree(staging_dir)
        logger.info("Removed staging directory %s", staging_dir)
    except OSError:
        logger.debug("Could not remove staging directory: %s", staging_dir)
