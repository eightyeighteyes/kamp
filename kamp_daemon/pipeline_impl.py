"""Orchestrate the ingest pipeline: extract → tag → artwork → move.

Extension wrappers (KampMusicBrainzTagger, KampCoverArtArchive) are invoked
in-process here rather than via invoke_extension() because the outer
pipeline.py subprocess is already the isolation boundary and return values
need to flow directly back to the host.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kamp_core.library import DownloadOverrides, LibraryIndex, PendingIngest

from .artwork import ArtworkError, _detect_mime, _embed, find_local_artwork
from .config import Config
from .ext.builtin.coverart import KampCoverArtArchive
from .ext.builtin.musicbrainz import KampMusicBrainzTagger
from .ext.context import KampGround, PlaybackSnapshot
from .ext.types import ArtworkQuery, ArtworkResult, TrackMetadata
from .extractor import ExtractionError, extract, find_audio_files
from .mover import MoveError, move_to_library
from .tagger import (
    TaggingError,
    is_tagged,
    read_release_mbids,
    read_track_metadata_from_file,
    write_sale_item_id,
    write_tags_from_track_metadata,
)

logger = logging.getLogger(__name__)

# Marker embedded in a watch folder item's name to inject a failure at a specific
# pipeline stage.  Used exclusively by `kamp test-notify` so the full
# IPC notification path (pipeline_impl → stage_q → notification_callback) can
# be exercised without a real audio file or network access.
_TEST_INJECT = {
    "extraction": "__test_extraction_error",
    "tagging": "__test_tagging_error",
    "artwork": "__test_artwork_error",
    "move": "__test_move_error",
}

# Sentinel prefix must match pipeline.py — imported lazily to avoid a circular
# dependency; duplicated as a string literal here so pipeline_impl stays usable
# standalone (e.g. in tests that call run() directly).
_NOTIFY_SENTINEL = "__notify__:"


def _notify(
    notify_callback: Callable[[str], None] | None,
    subtitle: str,
    message: str,
) -> None:
    """Emit a notification payload via notify_callback using the __notify__: sentinel.

    notify_callback receives an already-serialised sentinel string so
    pipeline_impl stays decoupled from how the parent delivers the notification.
    The caller (pipeline_worker) wires it to stage_q.put, same as stage_callback.
    """
    if notify_callback is None:
        return
    payload = json.dumps({"title": "Kamp", "subtitle": subtitle, "message": message})
    notify_callback(f"{_NOTIFY_SENTINEL}{payload}")


def run(
    path: Path,
    config: Config,
    _on_directory: Callable[[Path], None] | None = None,
    stage_callback: Callable[[str, str | None, bool, str], None] | None = None,
    notify_callback: Callable[[str], None] | None = None,
    index_path: Path | None = None,
) -> None:
    """Process a single watch folder item (ZIP or directory) end-to-end.

    On per-step failure the item is moved to <watch-folder>/errors/ so the watcher
    does not trigger on it again.  *stage_callback* (if provided) is called with
    ``(stage, sale_item_id, committed, album)`` — the current stage name
    ("Extracting", "Tagging", etc.), the Bandcamp ``sale_item_id`` for this
    artifact (KAMP-562; ``None`` for non-download drops), whether the item has
    been committed to the library, and a human-readable album label (KAMP-558;
    the extracted directory name, ``""`` before extraction). It is also called
    with an empty stage string in a finally block so the caller can reset its
    display; that terminal call carries the final *committed* value so a
    per-album UI can tell success (rescan coming → hold) from quarantine (no
    rescan → clear now).
    *notify_callback* (if provided) receives __notify__: sentinel strings that
    the parent process routes to rumps.notification().
    """
    logger.info("Pipeline started for %s", path)
    # Both paths must be set before any pipeline run — the caller (Watcher) is
    # only started after the user completes onboarding.
    assert config.paths.watch_folder is not None
    assert config.paths.library is not None
    watch_folder: Path = config.paths.watch_folder
    library: Path = config.paths.library

    # KAMP-523: if the downloader recorded this artifact's Bandcamp identity, we
    # own its metadata and must re-attach it to its streaming origin. Look the
    # handoff up now, on the original artifact path, before extraction mutates
    # it. Best-effort: any failure falls back to the normal MusicBrainz path.
    # index_path is None for a bare directory drop or in unit tests — provenance
    # only applies to downloads recorded by the daemon, which passes the real DB
    # path. Keeping it a parameter (rather than deriving _state_dir() here) keeps
    # the pipeline from ever touching the live DB under test.
    index: "LibraryIndex | None" = None
    provenance: "PendingIngest | None" = None
    overrides: "DownloadOverrides | None" = None
    if index_path is not None:
        try:
            from kamp_core.library import LibraryIndex  # noqa: PLC0415

            index = LibraryIndex(index_path)
            provenance = index.pending_ingest_for_path(str(path))
            if provenance is not None:
                overrides = index.download_overrides_for_sale_item(
                    provenance.sale_item_id
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Provenance lookup failed (non-fatal): %s", exc)

    # KAMP-562: carry the album identity (and, on the terminal reset, whether the
    # item reached the library) alongside every stage so a per-album UI can show
    # a "processing" badge on the right card. `emit` reads `committed` at call
    # time (Python closure), so the finally-block reset reflects the real outcome.
    sale_item_id: str | None = (
        provenance.sale_item_id if provenance is not None else None
    )
    committed = False
    # KAMP-558: a human-readable album label for the pipeline indicator's
    # tooltip ("Copying {album}…" / "Tagging {album}…"). Empty until extraction
    # gives us the on-disk album folder name; cosmetic only — never a key. `emit`
    # reads it at call time (Python closure) so later stages carry the resolved
    # name while the pre-extraction "Extracting" tooltip stays generic.
    album = ""

    def emit(stage: str) -> None:
        if stage_callback:
            stage_callback(stage, sale_item_id, committed, album)

    try:
        # --- 1. Extract -------------------------------------------------------
        emit("Extracting")
        try:
            if _TEST_INJECT["extraction"] in path.name:
                raise ExtractionError("Injected by test-notify --type extraction")
            directory = extract(path)
        except ExtractionError as exc:
            logger.error("Extraction failed: %s", exc)
            _notify(notify_callback, "Extraction failed", path.name)
            _quarantine(path, watch_folder)
            return

        # KAMP-558: now that extraction succeeded, the on-disk album folder name
        # is the best display label for the indicator tooltip. Subsequent emits
        # (Tagging/Updating artwork/Moving) carry it via the `album` closure var.
        album = directory.name

        # Notify the watcher of the watch folder directory as early as possible so it
        # can cancel any pending debounce timer for this directory.  Without this,
        # extracting a ZIP creates the directory, the watcher schedules it for a
        # second pipeline run, and that run races the first.
        if _on_directory is not None:
            _on_directory(directory)

        audio_files = find_audio_files(directory)
        if not audio_files:
            logger.error("No audio files found in %s", directory)
            _notify(
                notify_callback, "Extraction failed", f"No audio files in {path.name}"
            )
            _quarantine(directory, watch_folder)
            return

        # Build a shared KampGround context for this pipeline invocation.
        # library_tracks is empty because the pipeline acts on watch folder files
        # (not yet in the library); playback snapshot is a default.
        ctx = KampGround(playback=PlaybackSnapshot(), library_tracks=[])

        # --- 2. Tag -----------------------------------------------------------
        # Skip the MusicBrainz lookup (and tag writes) when every file already has
        # an MBID — the most expensive operation in the pipeline.  If even one file
        # is untagged, run the full pass for the whole directory to stay consistent.
        emit("Tagging")
        if provenance is not None:
            # KAMP-523: we already own this release's identity and metadata.
            # Keep the file's Bandcamp tags, overlay the user's display edits,
            # stamp the provenance tag, and record an MBID best-effort — never
            # let MusicBrainz rewrite the names (that divergence is the bug).
            # This branch wins over the is_tagged skip below by design.
            mbid, rg_mbid, title = _tag_known_bandcamp(
                audio_files, provenance, overrides, ctx
            )
        elif all(is_tagged(f) for f in audio_files):
            logger.info("All files already tagged — skipping MusicBrainz lookup")
            mbid, rg_mbid = read_release_mbids(audio_files[0])
            title = "(already tagged)"
        else:
            try:
                if _TEST_INJECT["tagging"] in directory.name:
                    raise TaggingError("Injected by test-notify --type tagging")
                # Build TrackMetadata from existing file tags, invoke the tagger
                # extension in-process, then write enriched metadata back to files.
                tracks = [read_track_metadata_from_file(f) for f in audio_files]
                tagger = KampMusicBrainzTagger(ctx)
                enriched = tagger.tag_release(tracks)
                if (
                    not config.musicbrainz.trust_musicbrainz_when_tags_conflict
                    and _mb_tags_conflict(tracks, enriched)
                ):
                    # MB returned different artist/album than the existing file
                    # tags — likely a mis-match for a release not yet in the DB.
                    # Keep the existing ID3 tags but still use the MB MBID for
                    # artwork: the MBID is valid even if the name strings differ.
                    first = tracks[0] if tracks else None
                    logger.warning(
                        "MusicBrainz tags conflict with existing file tags "
                        "(existing: %r / %r, MB: %r / %r) — skipping ID3 write",
                        first.artist if first else "",
                        first.album if first else "",
                        enriched[0].artist if enriched else "",
                        enriched[0].album if enriched else "",
                    )
                    mbid = enriched[0].release_mbid if enriched else ""
                    rg_mbid = enriched[0].release_group_mbid if enriched else ""
                    title = first.album if first else directory.name
                else:
                    total = len(audio_files)
                    for audio_file, track in zip(audio_files, enriched):
                        write_tags_from_track_metadata(
                            audio_file, track, total_tracks=total
                        )
                    # Use the first enriched track to carry release-level IDs forward.
                    mbid = enriched[0].release_mbid if enriched else ""
                    rg_mbid = enriched[0].release_group_mbid if enriched else ""
                    title = enriched[0].album if enriched else directory.name
            except TaggingError as exc:
                logger.error("Tagging failed: %s", exc)
                _notify(notify_callback, "Tagging failed", path.name)
                _quarantine(directory, watch_folder)
                return

        # --- 3. Artwork -------------------------------------------------------
        # Always run: even if art is already embedded, a higher-quality image may
        # be available (e.g. a bundled cover.jpg in the ZIP that beats the art the
        # original files shipped with).
        emit("Updating artwork")
        cover_art_result: tuple[bytes, str] | None = None
        try:
            if _TEST_INJECT["artwork"] in directory.name:
                raise ArtworkError("Injected by test-notify --type artwork")
            elif _TEST_INJECT["move"] not in directory.name:
                # Skip network artwork fetch when testing the move stage so it
                # doesn't raise its own ArtworkError before we reach the move
                # injection point.
                cover_art_result = _fetch_and_embed_via_extension(
                    ctx=ctx,
                    audio_files=audio_files,
                    release_mbid=mbid,
                    release_group_mbid=rg_mbid,
                    directory=directory,
                    min_dimension=config.artwork.min_dimension,
                    max_bytes=config.artwork.max_bytes,
                    save_format=config.artwork.save_format,
                    # KAMP-523: for a Bandcamp download, prefer the artwork we
                    # already have (the cover bundled in the ZIP) and don't reach
                    # out to the MusicBrainz Cover Art Archive.
                    skip_network=provenance is not None,
                )
        except ArtworkError as exc:
            # Artwork failure is non-fatal: log and continue.
            logger.warning("Artwork step failed: %s", exc)
            _notify(notify_callback, "Artwork warning", str(exc)[:120])

        # --- 4. Move ----------------------------------------------------------
        emit("Moving")
        try:
            if _TEST_INJECT["move"] in directory.name:
                raise MoveError("Injected by test-notify --type move")
            destinations = move_to_library(
                audio_files=audio_files,
                watch_dir=directory,
                library_root=library,
                path_template=config.library.path_template,
            )
            # KAMP-562: the item is now in the library. A rescan will flip its
            # card to local; the terminal reset emits committed=True so the UI
            # holds the badge until then instead of clearing (and flickering).
            committed = True
        except MoveError as exc:
            logger.error("Move failed: %s", exc)
            _notify(notify_callback, "Move failed", path.name)
            _quarantine(directory, watch_folder)
            return

        logger.info(
            "Pipeline complete: %d file(s) moved to library for release %r",
            len(destinations),
            title,
        )

        # Write cover file after a successful move so the path is the final
        # library location, not the transient watch-folder directory.
        if isinstance(cover_art_result, tuple):
            from .artwork import write_cover_file  # noqa: PLC0415

            cover_bytes, cover_mime = cover_art_result
            write_cover_file(cover_bytes, cover_mime, destinations[0].parent)

    finally:
        # Always clear the stage so the caller's display resets on success,
        # quarantine, or unexpected error. The terminal reset carries the final
        # `committed` value (KAMP-562) so the UI distinguishes success from
        # quarantine. `provenance` is an in-memory local, unaffected by the
        # clear_pending_ingest below, so `sale_item_id` is still valid here.
        emit("")
        # KAMP-523: drop the provenance handoff whether we succeeded or
        # quarantined — it has served its purpose and must never be replayed
        # against a later, unrelated file at the same path.
        if index is not None:
            try:
                if provenance is not None:
                    index.clear_pending_ingest(str(path))
            finally:
                index.close()


def _tag_known_bandcamp(
    audio_files: list[Path],
    provenance: "PendingIngest",
    overrides: "DownloadOverrides | None",
    ctx: KampGround,
) -> tuple[str, str, str]:
    """Tag a Bandcamp download from metadata we already own (KAMP-523).

    Keeps each file's Bandcamp-provided tags, overlays the user's display edits
    (renamed album/artist/tracks), stamps the KAMP_SALE_ITEM_ID provenance tag,
    and records a release MBID best-effort. The MusicBrainz name output is never
    applied — that divergence is exactly what forks a downloaded album from its
    streaming origin. Returns (release_mbid, release_group_mbid, title).
    """
    tracks = [read_track_metadata_from_file(f) for f in audio_files]

    # Best-effort MBID only: run the tagger but ignore its names, and never let
    # a lookup failure quarantine the download (Bandcamp self-releases are often
    # absent from MusicBrainz).
    mbid, rg_mbid = "", ""
    try:
        enriched = KampMusicBrainzTagger(ctx).tag_release(tracks)
        if enriched:
            mbid = enriched[0].release_mbid
            rg_mbid = enriched[0].release_group_mbid
    except Exception as exc:
        logger.warning("MBID lookup for Bandcamp download failed (non-fatal): %s", exc)

    total = len(audio_files)
    for audio_file, track in zip(audio_files, tracks):
        # A standalone single ships with no track number (track_number == 0),
        # but KAMP-526 numbers the streaming single as track 1. Align them so the
        # scanner's favorite/play-count inheritance and the title override below —
        # all keyed on (album_id, track_number, disc_number) — actually match.
        if total == 1 and track.track_number == 0:
            track.track_number = 1
        if overrides is not None:
            if overrides.album_artist:
                track.album_artist = overrides.album_artist
            if overrides.album:
                track.album = overrides.album
            edited_title = overrides.titles.get(track.track_number)
            if edited_title:
                track.title = edited_title
            # KAMP-582: user display_artist edits on the streaming version
            # carry into the downloaded files, like title edits.
            edited_artist = overrides.artists.get(track.track_number)
            if edited_artist:
                track.artist = edited_artist
        track.release_mbid = mbid
        track.release_group_mbid = rg_mbid
        write_tags_from_track_metadata(audio_file, track, total_tracks=total)
        write_sale_item_id(audio_file, provenance.sale_item_id)

    if overrides is not None and overrides.album:
        title = overrides.album
    else:
        title = tracks[0].album if tracks else ""
    return mbid, rg_mbid, title


def _fetch_and_embed_via_extension(
    ctx: KampGround,
    audio_files: list[Path],
    release_mbid: str,
    release_group_mbid: str,
    directory: Path,
    min_dimension: int,
    max_bytes: int,
    save_format: str = "embedded",
    skip_network: bool = False,
) -> tuple[bytes, str] | None:
    """Fetch cover art via KampCoverArtArchive extension and embed in audio files.

    Checks *directory* for a bundled image first (local-first; host responsibility
    because it requires file path access).  If no qualifying local image is found,
    delegates to KampCoverArtArchive to fetch from the MusicBrainz Cover Art Archive.
    Embedding is performed by the host — the extension only returns image bytes.

    When *save_format* is ``"cover-file"``, art is not embedded; instead the raw
    ``(image_bytes, mime_type)`` tuple is returned so the caller can write a
    cover file after the audio files have been moved to the library.
    Returns ``None`` when no qualifying art was found or when art was embedded.

    When *skip_network* is True (Bandcamp downloads, KAMP-523), only the bundled
    local image is used — the MusicBrainz Cover Art Archive is never queried, so
    we keep the artwork we already own rather than fetching a possibly-different
    one.
    """
    from .artwork import _load_local_artwork, has_embedded_art

    image_bytes: bytes | None = None
    mime_type = "image/jpeg"

    # Local-first: check for a bundled cover image in the watch folder item directory.
    local = find_local_artwork(directory)
    if local is not None:
        image_bytes = _load_local_artwork(local, min_dimension, max_bytes)
        if image_bytes is not None:
            logger.info("Using bundled artwork from %s", local)
            mime_type = _detect_mime(image_bytes)

    if image_bytes is None and skip_network:
        logger.info(
            "Bandcamp download: no bundled art found — skipping Cover Art Archive"
        )
        return None

    if image_bytes is None:
        # Skip the Cover Art Archive network call when all files already have
        # qualifying embedded art — cheaper to keep what we have.
        if audio_files and all(
            has_embedded_art(f, min_dimension, max_bytes) for f in audio_files
        ):
            logger.info(
                "All %d file(s) have qualifying embedded art — skipping Cover Art Archive fetch",
                len(audio_files),
            )
            return None

        query = ArtworkQuery(
            mbid=release_mbid,
            release_group_mbid=release_group_mbid,
            album="",
            artist="",
            min_dimension=min_dimension,
            max_bytes=max_bytes,
        )
        result: ArtworkResult | None = KampCoverArtArchive(ctx).fetch(query)
        if result is not None:
            image_bytes = result.image_bytes
            mime_type = result.mime_type

    if image_bytes is None:
        logger.warning(
            "No qualifying cover art found for release %s "
            "(min %dpx, max %d bytes) — skipping artwork",
            release_mbid,
            min_dimension,
            max_bytes,
        )
        return None

    if save_format == "cover-file":
        logger.info(
            "Deferring cover art (%d bytes) to cover file after move",
            len(image_bytes),
        )
        return image_bytes, mime_type

    logger.info(
        "Embedding cover art (%d bytes) into %d file(s)",
        len(image_bytes),
        len(audio_files),
    )
    for audio_file in audio_files:
        _embed(audio_file, image_bytes)
    return None


def _mb_tags_conflict(
    original: list[TrackMetadata],
    enriched: list[TrackMetadata],
) -> bool:
    """Return True if MB-enriched artist or album differs from existing file tags.

    Only flags a conflict when the file already has non-empty artist/album tags
    — files with no tags at all can't conflict, only be filled in.
    Comparison is case-insensitive and whitespace-normalised.
    """
    if not original or not enriched:
        return False
    orig = original[0]
    enr = enriched[0]

    def _norm(s: str) -> str:
        return s.strip().lower()

    if orig.artist and enr.artist and _norm(orig.artist) != _norm(enr.artist):
        return True
    if orig.album and enr.album and _norm(orig.album) != _norm(enr.album):
        return True
    return False


def _quarantine(item: Path, watch_root: Path) -> None:
    """Move *item* to <watch-folder>/errors/ to prevent reprocessing."""
    errors_dir = watch_root / "errors"
    errors_dir.mkdir(exist_ok=True)
    dest = errors_dir / item.name
    try:
        shutil.move(str(item), dest)
        logger.info("Quarantined %s → %s", item, dest)
    except Exception as exc:
        logger.error("Failed to quarantine %s: %s", item, exc)
