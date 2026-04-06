"""First-party tagger: MusicBrainz metadata lookup.

Wraps the lookup logic in kamp_daemon.tagger using the public BaseTagger API.
Writing tags to audio files stays with the pipeline host (pipeline_impl.py)
— this extension only resolves metadata and returns enriched TrackMetadata.

AcoustID fingerprinting (tagger.py tier-0) is skipped because it requires
reading audio file data, which extensions do not have access to.  Tier-1
(per-track recording search) and tier-2 (album-level search) are used instead
via the public lookup_release_from_tracks() function.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from kamp_daemon.ext.abc import BaseTagger
from kamp_daemon.ext.context import KampGround
from kamp_daemon.ext.types import TrackMetadata

if TYPE_CHECKING:
    # Only imported for type-checking; at runtime we use lazy imports inside
    # methods so the extension probe does not fire on musicbrainzngs side-effects.
    from kamp_daemon.tagger import ReleaseInfo

logger = logging.getLogger(__name__)


class KampMusicBrainzTagger(BaseTagger):
    """Resolve track metadata via the MusicBrainz API.

    Album-level tagger: overrides tag_release() to resolve the whole release
    in a single MusicBrainz round-trip rather than N per-track queries.
    """

    def __init__(self, ctx: KampGround) -> None:
        self._ctx = ctx

    def tag(self, track: TrackMetadata) -> TrackMetadata:
        """Tag a single track — delegates to tag_release for album lookup."""
        results = self.tag_release([track])
        return results[0]

    def tag_release(self, tracks: list[TrackMetadata]) -> list[TrackMetadata]:
        """Resolve metadata for all tracks in a release via MusicBrainz.

        Uses lazy imports so module-level import does not trigger the extension
        probe's dangerous-builtin stubs (musicbrainzngs is loaded at call time).

        Args:
            tracks: All tracks belonging to the same release, in track-number
                order. title/artist/album fields are used as search hints.

        Returns:
            Updated TrackMetadata objects with title, artist, album, album_artist,
            year, mbid (recording MBID), and release_mbid populated from
            MusicBrainz.  Raises TaggingError on lookup failure (propagated to host).
        """
        # Lazy imports: probe runs at module import time; keep module-level clean.
        from kamp_daemon.tagger import lookup_release_from_tracks

        track_data = [(t.artist, t.title, t.album) for t in tracks]

        # Let TaggingError propagate — the host (pipeline_impl.py) is responsible
        # for deciding whether to quarantine on lookup failure.
        release = lookup_release_from_tracks(track_data)
        return [_apply_release(t, release) for t in tracks]


def _apply_release(original: TrackMetadata, release: ReleaseInfo) -> TrackMetadata:
    """Map a ReleaseInfo onto a TrackMetadata, preserving unmatched fields.

    ReleaseInfo.tracks is keyed by "{disc}-{track_number}".  We try disc 1
    first; if not found we scan all keys for a matching track number so single-
    disc releases with a non-standard disc tag still match.
    """
    # Locate the per-track entry by track number.
    disc_key = f"1-{original.track_number}"
    track_info = release.tracks.get(disc_key)
    if track_info is None:
        # Fallback: any disc, matching track number
        for info in release.tracks.values():
            if info.number == original.track_number:
                track_info = info
                break

    return dataclasses.replace(
        original,
        title=track_info.title if track_info and track_info.title else original.title,
        artist=release.artist,
        album=release.title,
        album_artist=release.album_artist,
        year=release.year,
        track_number=track_info.number if track_info else original.track_number,
        mbid=track_info.recording_mbid if track_info else "",
        release_mbid=release.mbid,
        release_group_mbid=release.release_group_mbid,
    )
