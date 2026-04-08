"""Extension invocation policy: offer new tracks to registered extensions at ingest time.

Invocation policy (answers the design questions from TASK-90):

1. **Trigger point**: ingest-time only.  Extensions are offered tracks that have
   just been added to the library index by LibraryScanner.  Re-scan events do
   NOT trigger re-invocation because the audit log already has entries for those
   tracks.  On-demand re-processing is a separate, future feature.

2. **Already-processed detection**: the host queries extension_audit_log before
   each invocation.  If any row exists for (extension_id, mb_recording_id), the
   track is skipped.  Extension skip logic is explicitly NOT relied upon — it is
   unreliable and not under host control (AC#3).

3. **Extension version changes**: not re-processed automatically.  The audit log
   key is (extension_id, mb_recording_id) without a version component, so a new
   version of an installed extension will not reprocess existing tracks.
   Deliberate reprocessing requires an explicit rollback + re-trigger, which is
   out of scope for this task.

4. **Ordering**: taggers run first (in registration order), then artwork sources.
   All community extensions run *after* the first-party MusicBrainz / Cover Art
   Archive steps, which completed during the pipeline subprocess.  Community
   extensions supplement first-party results — they do not replace them.

Re-scan invocation is explicitly excluded because the audit log is append-only
and never pruned.  Invoking extensions on every scan would cause the log to grow
O(library size × number of scans), reaching hundreds of MB within a year on a
modest library.  Ingest-only invocation keeps growth O(library size).
"""

from __future__ import annotations

import logging

from kamp_core.library import LibraryIndex, Track

from .context import KampGround, PlaybackSnapshot
from .registry import ExtensionRegistry
from .types import ArtworkQuery, TrackMetadata
from .worker import invoke_extension
from .write_log import apply_mutations

_logger = logging.getLogger(__name__)


def _extension_id(cls: type) -> str:
    """Derive a stable string identifier for an extension class.

    Uses the fully-qualified class name (module + qualname) so the ID is
    consistent across invocations as long as the extension's module path
    and class name are unchanged.
    """
    return f"{cls.__module__}.{cls.__qualname__}"


def _track_to_metadata(track: Track) -> TrackMetadata:
    """Convert a library Track to the TrackMetadata type expected by BaseTagger."""
    return TrackMetadata(
        title=track.title,
        artist=track.artist,
        album=track.album,
        album_artist=track.album_artist,
        year=track.year,
        track_number=track.track_number,
        mbid=track.mb_recording_id,
        release_mbid=track.mb_release_id,
        # release_group_mbid is not stored in the library index; extensions
        # that require it should handle an empty string gracefully.
        release_group_mbid="",
    )


def invoke_extensions_for_new_tracks(
    registry: ExtensionRegistry,
    tracks: list[Track],
    library: LibraryIndex,
) -> None:
    """Invoke registered tagger and artwork-source extensions on newly ingested tracks.

    Called by the server's library-change callback after LibraryScanner.scan()
    has added new tracks to the index.  Only tracks from ScanResult.new_tracks
    (the to_add set) are passed here — updated tracks on re-scan are explicitly
    excluded to prevent unbounded audit log growth.

    For each extension, each track is offered at most once.  The host enforces
    this guarantee by checking extension_audit_log before each invocation (AC#3).
    Tracks without a resolved mb_recording_id are skipped because mutations are
    keyed by that identifier; offering them would produce unapplicable mutations.

    Args:
        registry: The populated extension registry (taggers and artwork sources).
        tracks: Newly added Track objects from ScanResult.new_tracks.
        library: LibraryIndex to read audit log state and apply mutations to.
    """
    # Only tracks with a resolved recording MBID can be offered to extensions.
    # Without an MBID the host cannot apply mutations (the audit log and mutation
    # dispatch both key on mb_recording_id).
    tagged = [t for t in tracks if t.mb_recording_id]
    if not tagged:
        return

    taggers = registry.taggers
    artwork_sources = registry.artwork_sources
    if not taggers and not artwork_sources:
        return

    # Taggers run first so artwork sources can act on enriched metadata if needed.
    for cls in taggers:
        ext_id = _extension_id(cls)
        ctx = KampGround(playback=PlaybackSnapshot(), library_tracks=[])
        for track in tagged:
            mbid = track.mb_recording_id
            # Host-enforced single-invocation guarantee: skip if the audit log
            # already has an entry for this (extension, track) pair.  This covers
            # the re-scan case and any prior partial-ingest run.
            if library.has_been_processed_by(ext_id, mbid):
                _logger.debug(
                    "Skipping tagger %s for %s — already in audit log", ext_id, mbid
                )
                continue
            result = invoke_extension(cls, "tag", _track_to_metadata(track), ctx=ctx)
            if result is False:
                _logger.warning(
                    "Tagger extension %s failed for track %s — skipping", ext_id, mbid
                )
                continue
            try:
                apply_mutations(ext_id, result, library)
            except ValueError:
                _logger.exception(
                    "Tagger extension %s produced an invalid mutation for track %s",
                    ext_id,
                    mbid,
                )

    for art_cls in artwork_sources:
        ext_id = _extension_id(art_cls)
        ctx = KampGround(playback=PlaybackSnapshot(), library_tracks=[])
        for track in tagged:
            mbid = track.mb_recording_id
            if library.has_been_processed_by(ext_id, mbid):
                _logger.debug(
                    "Skipping artwork source %s for %s — already in audit log",
                    ext_id,
                    mbid,
                )
                continue
            query = ArtworkQuery(
                mbid=track.mb_release_id,
                # release_group_mbid is not stored in the library index; the
                # extension must handle an empty string (e.g. skip the fallback
                # look-up or use album/artist fields instead).
                release_group_mbid="",
                album=track.album,
                artist=track.album_artist or track.artist,
                # No quality floor from host side: extensions decide their own
                # minimum dimension and size limits.
                min_dimension=0,
                max_bytes=0,
            )
            result = invoke_extension(art_cls, "fetch", query, ctx=ctx)
            if result is False:
                _logger.warning(
                    "Artwork source extension %s failed for track %s — skipping",
                    ext_id,
                    mbid,
                )
                continue
            try:
                apply_mutations(ext_id, result, library)
            except ValueError:
                _logger.exception(
                    "Artwork source extension %s produced an invalid mutation for track %s",
                    ext_id,
                    mbid,
                )
