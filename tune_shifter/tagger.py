"""MusicBrainz lookup and audio tag writing via mutagen."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import musicbrainzngs
import mutagen
import mutagen.id3 as id3
import mutagen.mp4

logger = logging.getLogger(__name__)


class TaggingError(Exception):
    pass


@dataclass
class ReleaseInfo:
    """Canonical metadata resolved from MusicBrainz."""

    mbid: str
    title: str
    artist: str
    album_artist: str
    year: str
    tracks: dict[str, TrackInfo]  # keyed by filename stem (best-effort) or track number


@dataclass
class TrackInfo:
    number: int
    disc: int
    title: str


def configure_musicbrainz(app_name: str, app_version: str, contact: str) -> None:
    musicbrainzngs.set_useragent(app_name, app_version, contact)


def tag_directory(
    directory: Path,
    audio_files: list[Path],
) -> ReleaseInfo:
    """Look up the release on MusicBrainz and write tags to all audio files.

    Returns the ReleaseInfo (including MBID) for the artwork step.
    Raises TaggingError on lookup failure or no audio files.
    """
    if not audio_files:
        raise TaggingError(f"No audio files found in {directory}")

    artist, album = _read_existing_metadata(audio_files[0])
    logger.info("Searching MusicBrainz for artist=%r album=%r", artist, album)

    release = _search_release(artist, album)
    logger.info("Matched release: %r (mbid=%s)", release.title, release.mbid)

    for audio_file in audio_files:
        _write_tags(audio_file, release)

    return release


def _read_existing_metadata(path: Path) -> tuple[str, str]:
    """Extract artist and album from existing file tags.

    Uses format-specific tag readers to avoid parsing the audio stream,
    which can fail on files with minimal or synthetic audio data.
    """
    artist = ""
    album = ""

    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            mp3_tags = id3.ID3(str(path))
            tpe1 = mp3_tags.get("TPE1")
            talb = mp3_tags.get("TALB")
            artist = str(tpe1) if tpe1 else ""
            album = str(talb) if talb else ""
        elif suffix == ".m4a":
            mp4 = mutagen.mp4.MP4(str(path))
            if mp4.tags is not None:
                art_vals = mp4.tags.get("\xa9ART")
                alb_vals = mp4.tags.get("\xa9alb")
                artist = str(art_vals[0]) if art_vals else ""
                album = str(alb_vals[0]) if alb_vals else ""
    except Exception:
        pass

    if not artist and not album:
        # Fall back to filename heuristics: "Artist - Album/01 - Title.mp3"
        parts = path.parent.name.split(" - ", 1)
        if len(parts) == 2:
            artist, album = parts[0].strip(), parts[1].strip()

    return artist, album


def _search_release(artist: str, album: str) -> ReleaseInfo:
    try:
        result = musicbrainzngs.search_releases(
            artist=artist,
            release=album,
            limit=5,
        )
    except musicbrainzngs.WebServiceError as exc:
        raise TaggingError(f"MusicBrainz search failed: {exc}") from exc

    releases: list[dict[str, Any]] = result.get("release-list", [])
    if not releases:
        raise TaggingError(
            f"No MusicBrainz results for artist={artist!r} album={album!r}"
        )

    best = max(releases, key=lambda r: int(r.get("ext:score", 0)))
    return _parse_release(best)


def _parse_release(raw: dict[str, Any]) -> ReleaseInfo:
    mbid: str = raw["id"]
    title: str = raw.get("title", "")
    year: str = raw.get("date", "")[:4]

    artist_credit = raw.get("artist-credit", [])
    artist = ""
    album_artist = ""
    if artist_credit:
        first = artist_credit[0]
        if isinstance(first, dict):
            artist = first.get("artist", {}).get("name", "")
            album_artist = artist

    tracks: dict[str, TrackInfo] = {}
    medium_list = raw.get("medium-list", [])
    for medium in medium_list:
        disc_num = int(medium.get("position", 1))
        for track_raw in medium.get("track-list", []):
            track_num = int(track_raw.get("number", track_raw.get("position", 0)))
            track_title = track_raw.get("recording", {}).get("title", "")
            key = f"{disc_num}-{track_num}"
            tracks[key] = TrackInfo(number=track_num, disc=disc_num, title=track_title)

    return ReleaseInfo(
        mbid=mbid,
        title=title,
        artist=artist,
        album_artist=album_artist,
        year=year,
        tracks=tracks,
    )


def _write_tags(path: Path, release: ReleaseInfo) -> None:
    """Write MusicBrainz-sourced tags to a single file."""
    track_info = _match_track(path, release)

    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_tags(path, release, track_info)
    elif suffix == ".m4a":
        _write_m4a_tags(path, release, track_info)
    else:
        logger.warning("Unsupported format for tagging: %s", path)


def _match_track(path: Path, release: ReleaseInfo) -> TrackInfo | None:
    """Attempt to match a file to a TrackInfo by its existing track number tag."""
    disc = 1
    track_num = 0

    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            mp3_tags = id3.ID3(str(path))
            trck = mp3_tags.get("TRCK")
            if trck:
                parts = str(trck).split("/")
                track_num = int(parts[0]) if parts[0].isdigit() else 0
            tpos = mp3_tags.get("TPOS")
            if tpos:
                parts = str(tpos).split("/")
                disc = int(parts[0]) if parts[0].isdigit() else 1
        elif suffix == ".m4a":
            mp4 = mutagen.mp4.MP4(str(path))
            if mp4.tags is not None:
                trkn_vals = mp4.tags.get("trkn")
                if trkn_vals:
                    track_num = trkn_vals[0][0]  # type: ignore[index]
                disk_vals = mp4.tags.get("disk")
                if disk_vals:
                    disc = disk_vals[0][0]  # type: ignore[index]
    except Exception:
        return None

    key = f"{disc}-{track_num}"
    return release.tracks.get(key)


def _write_mp3_tags(path: Path, release: ReleaseInfo, track: TrackInfo | None) -> None:
    try:
        tags = id3.ID3(str(path))
    except id3.ID3NoHeaderError:
        tags = id3.ID3()

    tags["TPE1"] = id3.TPE1(encoding=3, text=release.artist)
    tags["TPE2"] = id3.TPE2(encoding=3, text=release.album_artist)
    tags["TALB"] = id3.TALB(encoding=3, text=release.title)
    tags["TDRC"] = id3.TDRC(encoding=3, text=release.year)
    tags["TXXX:MusicBrainz Release Id"] = id3.TXXX(
        encoding=3, desc="MusicBrainz Release Id", text=release.mbid
    )

    if track is not None:
        tags["TRCK"] = id3.TRCK(encoding=3, text=str(track.number))
        tags["TPOS"] = id3.TPOS(encoding=3, text=str(track.disc))
        tags["TIT2"] = id3.TIT2(encoding=3, text=track.title)

    tags.save(str(path))
    logger.debug("Wrote MP3 tags to %s", path)


def _write_m4a_tags(path: Path, release: ReleaseInfo, track: TrackInfo | None) -> None:
    audio = mutagen.mp4.MP4(str(path))
    if audio.tags is None:
        audio.add_tags()

    assert audio.tags is not None
    audio.tags["\xa9ART"] = [release.artist]
    audio.tags["aART"] = [release.album_artist]
    audio.tags["\xa9alb"] = [release.title]
    audio.tags["\xa9day"] = [release.year]
    audio.tags["----:com.apple.iTunes:MusicBrainz Release Id"] = [
        mutagen.mp4.MP4FreeForm(release.mbid.encode())
    ]

    if track is not None:
        audio.tags["trkn"] = [(track.number, 0)]
        audio.tags["disk"] = [(track.disc, 0)]
        audio.tags["\xa9nam"] = [track.title]

    audio.save()
    logger.debug("Wrote M4A tags to %s", path)
