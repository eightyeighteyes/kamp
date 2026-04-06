"""First-party artwork source: MusicBrainz Cover Art Archive.

Wraps the fetch logic in kamp_daemon.artwork using the public BaseArtworkSource
API.  Embedding stays with the pipeline host (pipeline_impl.py) — this extension
only fetches bytes and returns them.

Local-file artwork (find_local_artwork) also stays with the pipeline host because
it requires a directory path, which extensions do not have access to.
"""

from __future__ import annotations

import logging

from kamp_daemon.ext.abc import BaseArtworkSource
from kamp_daemon.ext.context import KampGround
from kamp_daemon.ext.types import ArtworkQuery, ArtworkResult

logger = logging.getLogger(__name__)


class KampCoverArtArchive(BaseArtworkSource):
    """Fetch front cover art from the MusicBrainz Cover Art Archive.

    Tries the release MBID first; falls back to the release-group MBID when
    no art is found for the specific release.  Image quality constraints
    (min_dimension, max_bytes) are read from the ArtworkQuery.
    """

    def __init__(self, ctx: KampGround) -> None:
        self._ctx = ctx

    def fetch(self, query: ArtworkQuery) -> ArtworkResult | None:
        """Return cover art for *query*, or None if no qualifying image exists.

        Uses lazy imports so module-level import does not trigger the extension
        probe's dangerous-builtin stubs (PIL/requests are loaded at call time,
        not at module import time).
        """
        # Lazy imports: probe runs at import time; keep module-level clean.
        from kamp_daemon.artwork import (
            COVER_ART_ARCHIVE_URL,
            RELEASE_GROUP_ART_URL,
            _detect_mime,
            _fetch_cover,
        )

        min_dim = query.min_dimension
        max_b = query.max_bytes

        image_bytes = _fetch_cover(
            COVER_ART_ARCHIVE_URL.format(mbid=query.mbid), min_dim, max_b
        )

        if image_bytes is None and query.release_group_mbid:
            logger.debug(
                "No art for release %s; trying release-group %s",
                query.mbid,
                query.release_group_mbid,
            )
            image_bytes = _fetch_cover(
                RELEASE_GROUP_ART_URL.format(mbid=query.release_group_mbid),
                min_dim,
                max_b,
            )

        if image_bytes is None:
            return None

        return ArtworkResult(
            image_bytes=image_bytes, mime_type=_detect_mime(image_bytes)
        )
