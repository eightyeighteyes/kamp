"""MusicBrainz lookup and audio tag writing via mutagen."""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import musicbrainzngs
import mutagen
import mutagen.flac
import mutagen.id3 as id3
import mutagen.mp4
import mutagen.oggvorbis

logger = logging.getLogger(__name__)

# Matches iTunes-style edition suffixes in album names, e.g. "(Deluxe Edition)",
# "[Remastered]", "(Super Deluxe Version)".  Stripped before retrying a failed
# MusicBrainz search so that "Album (Deluxe Edition)" can still match "Album".
_EDITION_RE = re.compile(
    r"\s*[\(\[]"
    r"(deluxe|expanded|remastered|anniversary|special|super deluxe|"
    r"bonus track|explicit|clean|international)"
    r"[^\)\]]*[\)\]]",
    re.IGNORECASE,
)


class TaggingError(Exception):
    pass


@dataclass
class ReleaseInfo:
    """Canonical metadata resolved from MusicBrainz."""

    mbid: str
    release_group_mbid: str
    title: str
    artist: str
    album_artist: str
    year: str
    tracks: dict[str, TrackInfo]  # keyed by filename stem (best-effort) or track number
    # Extended MusicBrainz fields (default to empty so existing callers don't break)
    artist_sort: str = ""
    album_artist_sort: str = ""
    artists: list[str] = field(default_factory=list)
    artist_mbids: list[str] = field(default_factory=list)
    album_artist_mbid: str = ""
    label: str = ""
    catalog_number: str = ""
    barcode: str = ""
    asin: str = ""
    release_type: str = ""
    release_status: str = ""
    release_country: str = ""
    original_date: str = ""
    script: str = ""
    total_discs: int = 1


@dataclass
class TrackInfo:
    number: int
    disc: int
    title: str
    recording_mbid: str = ""
    total_tracks: int = 0


def is_tagged(path: Path) -> bool:
    """Return True if *path* already has a MusicBrainz Release ID tag.

    The MBID is written only after a successful full tag pass, so its presence
    implies all other tags were written in the same run.  Returns False on any
    read error so callers can safely fall through to the full tagging path.
    """
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            tags = id3.ID3(str(path))
            frame = tags.get("TXXX:MusicBrainz Release Id")
            return bool(frame and str(frame))
        if suffix == ".m4a":
            audio = mutagen.mp4.MP4(str(path))
            if audio.tags is None:
                return False
            vals = audio.tags.get("----:com.apple.iTunes:MusicBrainz Release Id")
            return bool(vals)
        if suffix == ".flac":
            audio = mutagen.flac.FLAC(str(path))
            if audio.tags is None:
                return False
            vals = audio.tags.get("MUSICBRAINZ_ALBUMID")
            return bool(vals and vals[0])
        if suffix == ".ogg":
            ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if ogg.tags is None:
                return False
            vals = ogg.tags.get("MUSICBRAINZ_ALBUMID")
            return bool(vals and vals[0])
    except Exception:
        pass
    return False


def read_release_mbids(path: Path) -> tuple[str, str]:
    """Return (release_mbid, release_group_mbid) from an already-tagged file.

    Used by the pipeline skip path to recover the MBIDs needed for artwork
    without re-querying MusicBrainz.  Returns ("", "") on any read error.
    """
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            tags = id3.ID3(str(path))
            rel = tags.get("TXXX:MusicBrainz Release Id")
            rg = tags.get("TXXX:MusicBrainz Release Group Id")
            return str(rel) if rel else "", str(rg) if rg else ""
        if suffix == ".m4a":
            audio = mutagen.mp4.MP4(str(path))
            if audio.tags is None:
                return "", ""
            rel_vals = audio.tags.get("----:com.apple.iTunes:MusicBrainz Release Id")
            rg_vals = audio.tags.get(
                "----:com.apple.iTunes:MusicBrainz Release Group Id"
            )
            rel_mbid = rel_vals[0].decode() if rel_vals else ""  # type: ignore[union-attr]
            rg_mbid = rg_vals[0].decode() if rg_vals else ""  # type: ignore[union-attr]
            return rel_mbid, rg_mbid
        if suffix == ".flac":
            audio = mutagen.flac.FLAC(str(path))
            if audio.tags is None:
                return "", ""
            rel_vals = audio.tags.get("MUSICBRAINZ_ALBUMID")
            rg_vals = audio.tags.get("MUSICBRAINZ_RELEASEGROUPID")
            return (rel_vals[0] if rel_vals else ""), (rg_vals[0] if rg_vals else "")
        if suffix == ".ogg":
            ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if ogg.tags is None:
                return "", ""
            rel_vals = ogg.tags.get("MUSICBRAINZ_ALBUMID")
            rg_vals = ogg.tags.get("MUSICBRAINZ_RELEASEGROUPID")
            return (rel_vals[0] if rel_vals else ""), (rg_vals[0] if rg_vals else "")
    except Exception:
        pass
    return "", ""


def configure_musicbrainz(app_name: str, app_version: str, contact: str) -> None:
    musicbrainzngs.set_useragent(app_name, app_version, contact)


def _lookup_release_by_acoustid(
    audio_files: list[Path],
) -> tuple[ReleaseInfo, dict[Path, str]]:
    """Fingerprint each file via fpcalc, query AcoustID, vote on release MBID.

    Returns (ReleaseInfo, {file: acoustid_id}) so callers can write the
    AcoustID fingerprint ID back to each file.  Raises TaggingError if
    fpcalc is unavailable, the key is absent (dev build), or no matching
    releases are found.
    """
    from .acoustid import fingerprint_file, lookup_matches

    votes: Counter[str] = Counter()
    file_acoustid_ids: dict[Path, str] = {}

    for path in audio_files:
        fp = fingerprint_file(path)
        if fp is None:
            continue
        duration, fingerprint = fp
        matches = lookup_matches(duration, fingerprint)
        if matches:
            # Best match (highest score) → the AcoustID ID for this file
            file_acoustid_ids[path] = matches[0][0]
        for _acoustid_id, rec_mbids in matches[:3]:
            for rec_mbid in rec_mbids:
                result = _mb_call(
                    musicbrainzngs.get_recording_by_id, rec_mbid, includes=["releases"]
                )
                for rel in result["recording"].get("release-list", []):
                    if rel_id := rel.get("id"):
                        votes[rel_id] += 1

    if not votes:
        raise TaggingError("AcoustID found no matching releases")

    winning_mbid = votes.most_common(1)[0][0]
    logger.debug(
        "AcoustID reconciliation: winning release=%s (votes=%s)",
        winning_mbid,
        dict(votes.most_common(5)),
    )
    detail = _mb_call(
        musicbrainzngs.get_release_by_id,
        winning_mbid,
        includes=["artists", "recordings", "release-groups", "labels"],
    )
    return _parse_release(detail["release"]), file_acoustid_ids


def _write_acoustid_id(path: Path, acoustid_id: str) -> None:
    """Write the AcoustID fingerprint ID to the file's tags.

    Uses the standard ACOUSTID_ID field name for each format, matching
    the convention used by MusicBrainz Picard.
    """
    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            try:
                tags = id3.ID3(str(path))
            except Exception:
                tags = id3.ID3()
            tags["TXXX:Acoustid Id"] = id3.TXXX(
                encoding=3, desc="Acoustid Id", text=acoustid_id
            )
            tags.save(str(path))
        elif suffix == ".m4a":
            audio = mutagen.mp4.MP4(str(path))
            if audio.tags is None:
                return
            audio.tags["----:com.apple.iTunes:Acoustid Id"] = [
                mutagen.mp4.MP4FreeForm(acoustid_id.encode())
            ]
            audio.save()
        elif suffix == ".flac":
            audio_flac = mutagen.flac.FLAC(str(path))
            if audio_flac.tags is None:
                return
            audio_flac.tags["ACOUSTID_ID"] = [acoustid_id]
            audio_flac.save()
        elif suffix == ".ogg":
            audio_ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if audio_ogg.tags is None:
                return
            audio_ogg.tags["ACOUSTID_ID"] = [acoustid_id]
            audio_ogg.save()
    except Exception:
        logger.debug("Could not write ACOUSTID_ID to %s", path)


def tag_directory(
    directory: Path,
    audio_files: list[Path],
) -> ReleaseInfo:
    """Look up the release on MusicBrainz and write tags to all audio files.

    Tier 0 — AcoustID fingerprint: waveform-based identification; works even
    with no tags.  Requires fpcalc (ships via the chromaprint Homebrew dep)
    and an embedded application key.  Silently skipped in dev builds.

    Tier 1 — per-track recording search: each file's existing artist/title/album
    tags are used to query MusicBrainz recordings; the most common release MBID
    across all tracks is selected.

    Tier 2 — album-level search: artist + album from the first file (or directory
    name heuristic).

    Returns the ReleaseInfo (including MBID) for the artwork step.
    Raises TaggingError on lookup failure or no audio files.
    """
    if not audio_files:
        raise TaggingError(f"No audio files found in {directory}")

    # Tier 0: AcoustID fingerprint
    try:
        release, acoustid_ids = _lookup_release_by_acoustid(audio_files)
        logger.info("AcoustID matched: %r (mbid=%s)", release.title, release.mbid)
        for audio_file in audio_files:
            _write_tags(audio_file, release)
            if acoustid_id := acoustid_ids.get(audio_file):
                _write_acoustid_id(audio_file, acoustid_id)
        return release
    except TaggingError as exc:
        logger.debug(
            "AcoustID lookup failed (%s); falling back to tag-based search", exc
        )

    # Tier 1: per-track MusicBrainz recording search
    try:
        release = _lookup_release_by_recordings(audio_files)
        logger.info(
            "Per-track reconciliation matched: %r (mbid=%s)",
            release.title,
            release.mbid,
        )
    except TaggingError as exc:
        logger.debug(
            "Per-track lookup failed (%s); falling back to album-level search", exc
        )
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
        elif suffix == ".flac":
            flac = mutagen.flac.FLAC(str(path))
            if flac.tags is not None:
                art_vals = flac.tags.get("ARTIST")
                alb_vals = flac.tags.get("ALBUM")
                artist = art_vals[0] if art_vals else ""
                album = alb_vals[0] if alb_vals else ""
        elif suffix == ".ogg":
            ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if ogg.tags is not None:
                art_vals = ogg.tags.get("ARTIST")
                alb_vals = ogg.tags.get("ALBUM")
                artist = art_vals[0] if art_vals else ""
                album = alb_vals[0] if alb_vals else ""
    except Exception as exc:
        logger.debug("Could not read tags from %s: %s", path, exc)

    if not artist and not album:
        # Fall back to filename heuristics: "Artist - Album/01 - Title.mp3"
        logger.debug("Falling back to directory name heuristic for %s", path)
        parts = path.parent.name.split(" - ", 1)
        if len(parts) == 2:
            artist, album = parts[0].strip(), parts[1].strip()

    logger.debug("Read metadata from %s: artist=%r album=%r", path, artist, album)

    if not artist and not album:
        raise TaggingError(
            f"Could not determine artist or album from {path} or its directory name"
        )

    return artist, album


def _read_track_metadata(path: Path) -> tuple[str, str, str]:
    """Extract artist, title, and album from existing file tags.

    Returns ("", "", "") on any read error so callers can safely skip the file.
    """
    artist = ""
    title = ""
    album = ""

    try:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            mp3_tags = id3.ID3(str(path))
            tpe1 = mp3_tags.get("TPE1")
            tit2 = mp3_tags.get("TIT2")
            talb = mp3_tags.get("TALB")
            artist = str(tpe1) if tpe1 else ""
            title = str(tit2) if tit2 else ""
            album = str(talb) if talb else ""
        elif suffix == ".m4a":
            mp4 = mutagen.mp4.MP4(str(path))
            if mp4.tags is not None:
                art_vals = mp4.tags.get("\xa9ART")
                nam_vals = mp4.tags.get("\xa9nam")
                alb_vals = mp4.tags.get("\xa9alb")
                artist = str(art_vals[0]) if art_vals else ""
                title = str(nam_vals[0]) if nam_vals else ""
                album = str(alb_vals[0]) if alb_vals else ""
        elif suffix == ".flac":
            flac = mutagen.flac.FLAC(str(path))
            if flac.tags is not None:
                art_vals = flac.tags.get("ARTIST")
                tit_vals = flac.tags.get("TITLE")
                alb_vals = flac.tags.get("ALBUM")
                artist = art_vals[0] if art_vals else ""
                title = tit_vals[0] if tit_vals else ""
                album = alb_vals[0] if alb_vals else ""
        elif suffix == ".ogg":
            ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if ogg.tags is not None:
                art_vals = ogg.tags.get("ARTIST")
                tit_vals = ogg.tags.get("TITLE")
                alb_vals = ogg.tags.get("ALBUM")
                artist = art_vals[0] if art_vals else ""
                title = tit_vals[0] if tit_vals else ""
                album = alb_vals[0] if alb_vals else ""
    except Exception as exc:
        logger.debug("Could not read track metadata from %s: %s", path, exc)
        return "", "", ""

    return artist, title, album


def _lookup_release_by_recordings(audio_files: list[Path]) -> ReleaseInfo:
    """Look up each track via MusicBrainz recordings search, then reconcile to the
    most common release MBID across all tracks.

    Raises TaggingError if no tracks have title tags or no recording results were found.
    """
    votes: Counter[str] = Counter()

    for path in audio_files:
        artist, title, album = _read_track_metadata(path)
        if not title:
            logger.debug("Skipping per-track vote for %s: no title tag", path)
            continue

        logger.debug(
            "Per-track MB search: recording=%r artist=%r release=%r",
            title,
            artist,
            album,
        )
        result = _mb_call(
            musicbrainzngs.search_recordings,
            recording=title,
            artist=artist,
            release=album,
            limit=3,
        )
        recordings = result.get("recording-list", [])
        if not recordings:
            logger.debug("No recording results for %r by %r", title, artist)
            continue

        # Use only the top result; accumulate votes for every release it appears on
        top = recordings[0]
        for rel in top.get("release-list", []):
            rel_id = rel.get("id", "")
            if rel_id:
                votes[rel_id] += 1

    if not votes:
        raise TaggingError(
            "no per-track recording results found — falling back to album-level search"
        )

    winning_mbid = votes.most_common(1)[0][0]
    logger.debug(
        "Per-track reconciliation: winning release=%s (votes=%s)",
        winning_mbid,
        dict(votes.most_common(5)),
    )

    detail = _mb_call(
        musicbrainzngs.get_release_by_id,
        winning_mbid,
        includes=["artists", "recordings", "release-groups", "labels"],
    )
    return _parse_release(detail["release"])


def _mb_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a musicbrainzngs function, retrying on transient errors with exponential backoff."""
    delay = 1.0
    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except (musicbrainzngs.WebServiceError, musicbrainzngs.NetworkError) as exc:
            if attempt == max_retries:
                raise TaggingError(
                    f"MusicBrainz request failed after {max_retries} retries: {exc}"
                ) from exc
            logger.warning(
                "MusicBrainz request failed (attempt %d/%d), retrying in %gs: %s",
                attempt + 1,
                max_retries,
                delay,
                exc,
            )
            time.sleep(delay)
            delay *= 2


def _search_release(artist: str, album: str) -> ReleaseInfo:
    logger.debug("MusicBrainz search: artist=%r release=%r", artist, album)
    result = _mb_call(
        musicbrainzngs.search_releases,
        artist=artist,
        release=album,
        limit=5,
    )

    releases: list[dict[str, Any]] = result.get("release-list", [])
    logger.debug("MusicBrainz returned %d result(s)", len(releases))

    if not releases:
        # Retry once with any iTunes edition suffix stripped from the album name
        # (e.g. "Abbey Road (Super Deluxe Edition)" → "Abbey Road").
        cleaned = _EDITION_RE.sub("", album).strip()
        if cleaned != album:
            logger.debug(
                "No results for %r; retrying with cleaned name %r", album, cleaned
            )
            result = _mb_call(
                musicbrainzngs.search_releases,
                artist=artist,
                release=cleaned,
                limit=5,
            )
            releases = result.get("release-list", [])

    if not releases:
        raise TaggingError(
            f"No MusicBrainz results for artist={artist!r} album={album!r}"
        )

    # Sort candidates: highest score first; break ties by earliest release date so
    # original/digital releases (e.g. 2020-03-27) beat later vinyl reissues (2021-04-30).
    def _sort_key(r: dict[str, Any]) -> tuple[int, str]:
        score = int(r.get("ext:score", 0))
        date = r.get("date", "") or "9999"
        return (-score, date)

    candidates = sorted(releases, key=_sort_key)

    # search_releases returns minimal data (no full date, no track listings).
    # Fetch the full release to get accurate year and track info.  Try each
    # candidate in preference order; skip releases whose metadata cannot be
    # parsed (e.g. vinyl with non-numeric track numbers like "A1").
    last_err: Exception = TaggingError("no candidates")
    for candidate in candidates:
        candidate_mbid: str = candidate["id"]
        logger.debug("Fetching full release details for %s", candidate_mbid)
        detail = _mb_call(
            musicbrainzngs.get_release_by_id,
            candidate_mbid,
            includes=["artists", "recordings", "release-groups", "labels"],
        )
        try:
            return _parse_release(detail["release"])
        except (ValueError, KeyError) as exc:
            logger.debug("Skipping release %s: %s", candidate_mbid, exc)
            last_err = exc
            continue

    raise TaggingError(
        f"No parseable MusicBrainz release for artist={artist!r} album={album!r}: {last_err}"
    )


def _parse_release(raw: dict[str, Any]) -> ReleaseInfo:
    mbid: str = raw["id"]
    title: str = raw.get("title", "")
    year: str = raw.get("date", "")[:4]

    release_group = raw.get("release-group", {})
    release_group_mbid: str = release_group.get("id", "")
    release_type: str = release_group.get("primary-type", "")
    original_date: str = release_group.get("first-release-date", "")

    # Artist credit — musicbrainzngs returns a flat list alternating between name-credit
    # dicts and joinphrase strings (e.g. [{"name": "Ali"}, " & ", {"name": "Charif..."}]).
    artist_credit = raw.get("artist-credit", [])
    credits = [c for c in artist_credit if isinstance(c, dict)]
    artists = [c.get("name") or c.get("artist", {}).get("name", "") for c in credits]
    # Reconstruct the display string by walking the raw list, taking names from dicts
    # and joinphrases from interleaved strings.
    artist = "".join(
        (
            item
            if isinstance(item, str)
            else (item.get("name") or item.get("artist", {}).get("name", ""))
        )
        for item in artist_credit
    ).strip()
    album_artist = artist
    artist_sort = " ".join(c.get("artist", {}).get("sort-name", "") for c in credits)
    album_artist_sort = artist_sort
    artist_mbids = [c.get("artist", {}).get("id", "") for c in credits]
    album_artist_mbid = artist_mbids[0] if artist_mbids else ""

    # Label info — first entry wins; label may be absent (e.g. self-released)
    label_info_list = raw.get("label-info-list", [])
    label = ""
    catalog_number = ""
    if label_info_list:
        first = label_info_list[0]
        label = first.get("label", {}).get("name", "") if first.get("label") else ""
        catalog_number = first.get("catalog-number", "")

    medium_list = raw.get("medium-list", [])
    total_discs = len(medium_list) or 1

    tracks: dict[str, TrackInfo] = {}
    for medium in medium_list:
        disc_num = int(medium.get("position", 1))
        track_list = medium.get("track-list", [])
        total_tracks = len(track_list)
        for track_raw in track_list:
            # Vinyl releases use non-numeric track numbers like "A1", "B2".
            # Fall back to the 1-based position within the medium so the parser
            # does not crash; track matching for non-numeric tracks will rely on
            # position rather than the printed side+number.
            num_str = str(track_raw.get("number", ""))
            if num_str.isdigit():
                track_num = int(num_str)
            else:
                pos = track_raw.get("position", 0)
                track_num = int(pos) if str(pos).isdigit() else 0
            recording = track_raw.get("recording", {})
            key = f"{disc_num}-{track_num}"
            tracks[key] = TrackInfo(
                number=track_num,
                disc=disc_num,
                title=recording.get("title", ""),
                recording_mbid=recording.get("id", ""),
                total_tracks=total_tracks,
            )

    return ReleaseInfo(
        mbid=mbid,
        release_group_mbid=release_group_mbid,
        title=title,
        artist=artist,
        album_artist=album_artist,
        year=year,
        tracks=tracks,
        artist_sort=artist_sort,
        album_artist_sort=album_artist_sort,
        artists=artists,
        artist_mbids=artist_mbids,
        album_artist_mbid=album_artist_mbid,
        label=label,
        catalog_number=catalog_number,
        barcode=raw.get("barcode", ""),
        asin=raw.get("asin", ""),
        release_type=release_type,
        release_status=raw.get("status", ""),
        release_country=raw.get("country", ""),
        original_date=original_date,
        script=raw.get("text-representation", {}).get("script", ""),
        total_discs=total_discs,
    )


def _write_tags(path: Path, release: ReleaseInfo) -> None:
    """Write MusicBrainz-sourced tags to a single file."""
    track_info = _match_track(path, release)

    suffix = path.suffix.lower()
    if suffix == ".mp3":
        _write_mp3_tags(path, release, track_info)
    elif suffix == ".m4a":
        _write_m4a_tags(path, release, track_info)
    elif suffix == ".flac":
        _write_flac_tags(path, release, track_info)
    elif suffix == ".ogg":
        _write_ogg_tags(path, release, track_info)
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
        elif suffix == ".flac":
            flac = mutagen.flac.FLAC(str(path))
            if flac.tags is not None:
                trck_vals = flac.tags.get("TRACKNUMBER")
                if trck_vals:
                    trck_s = trck_vals[0].split("/")[0]
                    track_num = int(trck_s) if trck_s.isdigit() else 0
                disc_vals = flac.tags.get("DISCNUMBER")
                if disc_vals:
                    disc_s = disc_vals[0].split("/")[0]
                    disc = int(disc_s) if disc_s.isdigit() else 1
        elif suffix == ".ogg":
            ogg = mutagen.oggvorbis.OggVorbis(str(path))
            if ogg.tags is not None:
                trck_vals = ogg.tags.get("TRACKNUMBER")
                if trck_vals:
                    trck_s = trck_vals[0].split("/")[0]
                    track_num = int(trck_s) if trck_s.isdigit() else 0
                disc_vals = ogg.tags.get("DISCNUMBER")
                if disc_vals:
                    disc_s = disc_vals[0].split("/")[0]
                    disc = int(disc_s) if disc_s.isdigit() else 1
    except Exception:
        return None

    key = f"{disc}-{track_num}"
    return release.tracks.get(key)


def _write_mp3_tags(path: Path, release: ReleaseInfo, track: TrackInfo | None) -> None:
    try:
        tags = id3.ID3(str(path))
    except id3.ID3NoHeaderError:
        tags = id3.ID3()

    # Core tags
    tags["TPE1"] = id3.TPE1(encoding=3, text=release.artist)
    tags["TPE2"] = id3.TPE2(encoding=3, text=release.album_artist)
    tags["TALB"] = id3.TALB(encoding=3, text=release.title)
    tags["TDRC"] = id3.TDRC(encoding=3, text=release.year)
    tags["TXXX:MusicBrainz Release Id"] = id3.TXXX(
        encoding=3, desc="MusicBrainz Release Id", text=release.mbid
    )

    # Sort names
    if release.artist_sort:
        tags["TSOP"] = id3.TSOP(encoding=3, text=release.artist_sort)
    if release.album_artist_sort:
        tags["TSO2"] = id3.TSO2(encoding=3, text=release.album_artist_sort)

    # Multi-value artists (semicolon-joined per Picard convention)
    if release.artists:
        tags["TXXX:ARTISTS"] = id3.TXXX(
            encoding=3, desc="ARTISTS", text="; ".join(release.artists)
        )

    # MusicBrainz IDs
    if release.artist_mbids:
        tags["TXXX:MusicBrainz Artist Id"] = id3.TXXX(
            encoding=3,
            desc="MusicBrainz Artist Id",
            text="; ".join(release.artist_mbids),
        )
    if release.album_artist_mbid:
        tags["TXXX:MusicBrainz Album Artist Id"] = id3.TXXX(
            encoding=3,
            desc="MusicBrainz Album Artist Id",
            text=release.album_artist_mbid,
        )
    if release.release_group_mbid:
        tags["TXXX:MusicBrainz Release Group Id"] = id3.TXXX(
            encoding=3,
            desc="MusicBrainz Release Group Id",
            text=release.release_group_mbid,
        )

    # Release metadata
    if release.release_type:
        tags["TXXX:MusicBrainz Album Type"] = id3.TXXX(
            encoding=3, desc="MusicBrainz Album Type", text=release.release_type
        )
    if release.release_status:
        tags["TXXX:MusicBrainz Album Status"] = id3.TXXX(
            encoding=3, desc="MusicBrainz Album Status", text=release.release_status
        )
    if release.release_country:
        tags["TXXX:MusicBrainz Album Release Country"] = id3.TXXX(
            encoding=3,
            desc="MusicBrainz Album Release Country",
            text=release.release_country,
        )
    if release.original_date:
        tags["TDOR"] = id3.TDOR(encoding=3, text=release.original_date)
    if release.label:
        tags["TPUB"] = id3.TPUB(encoding=3, text=release.label)
    if release.catalog_number:
        tags["TXXX:CATALOGNUMBER"] = id3.TXXX(
            encoding=3, desc="CATALOGNUMBER", text=release.catalog_number
        )
    if release.barcode:
        tags["TXXX:BARCODE"] = id3.TXXX(
            encoding=3, desc="BARCODE", text=release.barcode
        )
    if release.asin:
        tags["TXXX:ASIN"] = id3.TXXX(encoding=3, desc="ASIN", text=release.asin)
    if release.script:
        tags["TXXX:SCRIPT"] = id3.TXXX(encoding=3, desc="SCRIPT", text=release.script)

    # Track and disc numbers with totals
    if track is not None:
        total_trck = (
            f"{track.number}/{track.total_tracks}"
            if track.total_tracks
            else str(track.number)
        )
        total_tpos = (
            f"{track.disc}/{release.total_discs}"
            if release.total_discs > 1
            else str(track.disc)
        )
        tags["TRCK"] = id3.TRCK(encoding=3, text=total_trck)
        tags["TPOS"] = id3.TPOS(encoding=3, text=total_tpos)
        tags["TIT2"] = id3.TIT2(encoding=3, text=track.title)
        if track.recording_mbid:
            tags["TXXX:MusicBrainz Track Id"] = id3.TXXX(
                encoding=3, desc="MusicBrainz Track Id", text=track.recording_mbid
            )

    tags.save(str(path))
    logger.debug("Wrote MP3 tags to %s", path)


def _write_m4a_tags(path: Path, release: ReleaseInfo, track: TrackInfo | None) -> None:
    audio = mutagen.mp4.MP4(str(path))
    if audio.tags is None:
        audio.add_tags()

    assert audio.tags is not None

    def _ff(value: str) -> list[mutagen.mp4.MP4FreeForm]:
        """Wrap a string as a single-element MP4FreeForm list."""
        return [mutagen.mp4.MP4FreeForm(value.encode())]

    # Core tags
    audio.tags["\xa9ART"] = [release.artist]
    audio.tags["aART"] = [release.album_artist]
    audio.tags["\xa9alb"] = [release.title]
    audio.tags["\xa9day"] = [release.year]
    audio.tags["----:com.apple.iTunes:MusicBrainz Release Id"] = _ff(release.mbid)

    # Sort names
    if release.artist_sort:
        audio.tags["soar"] = [release.artist_sort]
    if release.album_artist_sort:
        audio.tags["soaa"] = [release.album_artist_sort]

    # Multi-value artists
    if release.artists:
        audio.tags["----:com.apple.iTunes:ARTISTS"] = _ff("; ".join(release.artists))

    # MusicBrainz IDs
    if release.artist_mbids:
        audio.tags["----:com.apple.iTunes:MusicBrainz Artist Id"] = _ff(
            "; ".join(release.artist_mbids)
        )
    if release.album_artist_mbid:
        audio.tags["----:com.apple.iTunes:MusicBrainz Album Artist Id"] = _ff(
            release.album_artist_mbid
        )
    if release.release_group_mbid:
        audio.tags["----:com.apple.iTunes:MusicBrainz Release Group Id"] = _ff(
            release.release_group_mbid
        )

    # Release metadata
    if release.release_type:
        audio.tags["----:com.apple.iTunes:MusicBrainz Album Type"] = _ff(
            release.release_type
        )
    if release.release_status:
        audio.tags["----:com.apple.iTunes:MusicBrainz Album Status"] = _ff(
            release.release_status
        )
    if release.release_country:
        audio.tags["----:com.apple.iTunes:MusicBrainz Album Release Country"] = _ff(
            release.release_country
        )
    if release.original_date:
        audio.tags["----:com.apple.iTunes:ORIGINALDATE"] = _ff(release.original_date)
        audio.tags["----:com.apple.iTunes:ORIGINALYEAR"] = _ff(
            release.original_date[:4]
        )
    if release.label:
        audio.tags["----:com.apple.iTunes:LABEL"] = _ff(release.label)
    if release.catalog_number:
        audio.tags["----:com.apple.iTunes:CATALOGNUMBER"] = _ff(release.catalog_number)
    if release.barcode:
        audio.tags["----:com.apple.iTunes:BARCODE"] = _ff(release.barcode)
    if release.asin:
        audio.tags["----:com.apple.iTunes:ASIN"] = _ff(release.asin)
    if release.script:
        audio.tags["----:com.apple.iTunes:SCRIPT"] = _ff(release.script)

    # Track and disc numbers with totals
    if track is not None:
        audio.tags["trkn"] = [(track.number, track.total_tracks)]
        audio.tags["disk"] = [(track.disc, release.total_discs)]
        audio.tags["\xa9nam"] = [track.title]
        if track.recording_mbid:
            audio.tags["----:com.apple.iTunes:MusicBrainz Track Id"] = _ff(
                track.recording_mbid
            )

    audio.save()
    logger.debug("Wrote M4A tags to %s", path)


def _assign_vorbis_tags(
    tags: Any, release: ReleaseInfo, track: TrackInfo | None
) -> None:
    """Write MusicBrainz metadata into a Vorbis comment dict (FLAC or OGG).

    Accepts any object that supports ``tags[key] = [value]`` assignment —
    both ``VCFLACDict`` (FLAC) and ``VComment`` (OGG Vorbis) qualify.
    """

    def _v(value: str) -> list[str]:
        return [value]

    # Core tags
    tags["ARTIST"] = _v(release.artist)
    tags["ALBUMARTIST"] = _v(release.album_artist)
    tags["ALBUM"] = _v(release.title)
    tags["DATE"] = _v(release.year)
    tags["MUSICBRAINZ_ALBUMID"] = _v(release.mbid)

    # Sort names
    if release.artist_sort:
        tags["ARTISTSORT"] = _v(release.artist_sort)
    if release.album_artist_sort:
        tags["ALBUMARTISTSORT"] = _v(release.album_artist_sort)

    # Multi-value artists
    if release.artists:
        tags["ARTISTS"] = _v("; ".join(release.artists))

    # MusicBrainz IDs
    if release.artist_mbids:
        tags["MUSICBRAINZ_ARTISTID"] = _v("; ".join(release.artist_mbids))
    if release.album_artist_mbid:
        tags["MUSICBRAINZ_ALBUMARTISTID"] = _v(release.album_artist_mbid)
    if release.release_group_mbid:
        tags["MUSICBRAINZ_RELEASEGROUPID"] = _v(release.release_group_mbid)

    # Release metadata
    if release.release_type:
        tags["MUSICBRAINZ_ALBUMTYPE"] = _v(release.release_type)
    if release.release_status:
        tags["MUSICBRAINZ_ALBUMSTATUS"] = _v(release.release_status)
    if release.release_country:
        tags["RELEASECOUNTRY"] = _v(release.release_country)
    if release.original_date:
        tags["ORIGINALDATE"] = _v(release.original_date)
        # ORIGINALYEAR is the year-only portion so players that don't parse
        # ORIGINALDATE still display the original year.
        tags["ORIGINALYEAR"] = _v(release.original_date[:4])
    if release.label:
        tags["LABEL"] = _v(release.label)
    if release.catalog_number:
        tags["CATALOGNUMBER"] = _v(release.catalog_number)
    if release.barcode:
        tags["BARCODE"] = _v(release.barcode)
    if release.asin:
        tags["ASIN"] = _v(release.asin)
    if release.script:
        tags["SCRIPT"] = _v(release.script)

    # Track and disc numbers — write number and total as separate tags per
    # the MusicBrainz Picard convention for Vorbis comments.
    if track is not None:
        tags["TRACKNUMBER"] = _v(str(track.number))
        if track.total_tracks:
            tags["TOTALTRACKS"] = _v(str(track.total_tracks))
        tags["DISCNUMBER"] = _v(str(track.disc))
        tags["TOTALDISCS"] = _v(str(release.total_discs))
        tags["TITLE"] = _v(track.title)
        if track.recording_mbid:
            tags["MUSICBRAINZ_TRACKID"] = _v(track.recording_mbid)


def _write_flac_tags(path: Path, release: ReleaseInfo, track: TrackInfo | None) -> None:
    audio = mutagen.flac.FLAC(str(path))
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    _assign_vorbis_tags(audio.tags, release, track)
    audio.save()
    logger.debug("Wrote FLAC tags to %s", path)


def _write_ogg_tags(path: Path, release: ReleaseInfo, track: TrackInfo | None) -> None:
    audio = mutagen.oggvorbis.OggVorbis(str(path))
    # OggVorbis always has a VComment block; no add_tags() call needed.
    if audio.tags is None:  # pragma: no cover — defensive guard
        return
    _assign_vorbis_tags(audio.tags, release, track)
    audio.save()
    logger.debug("Wrote OGG tags to %s", path)
