#!/usr/bin/env python3
"""Extension profiling harness for strace/dtruss tracing.

Invokes each built-in extension through invoke_extension() so that the
subprocess worker path (the code path that will be sandboxed) is traced.
Run this under strace (Linux) or dtruss (macOS) to enumerate the syscalls,
filesystem paths, and network connections each extension makes legitimately.

The trace output is then used to derive the allow-rules in the sandbox
profiles (see project/sandbox-profiles.md and
kamp_daemon/ext/sandbox/).

Usage
-----
Linux (must have strace installed)::

    strace -f -e trace=file,network,process \\
      poetry run python scripts/profile_extensions.py musicbrainz \\
      2>strace_mb.txt

macOS (requires sudo for dtruss)::

    sudo dtruss -f \\
      poetry run python scripts/profile_extensions.py musicbrainz \\
      2>dtruss_mb.txt

Extensions
----------
``musicbrainz``
    KampMusicBrainzTagger — HTTP calls to musicbrainz.org.
    Requires real network access; needs MUSICBRAINZ_CONTACT env var set.

``coverart``
    KampCoverArtArchive — HTTP calls to coverartarchive.org.
    Requires a valid release MBID supplied via --mbid.
    Requires real network access.

``bandcamp``
    KampBandcampSyncer — interactive Playwright/Chromium session.
    Requires a valid Bandcamp session file.
    NOTE: The Bandcamp syncer manages its own subprocess isolation via
    _spawn_worker(); its sandbox profile (TIER_SYNCER) must account for
    Playwright/Chromium subprocess launch patterns.

Options
-------
--mbid MBID
    MusicBrainz release MBID to use for the coverart lookup.
    Example: ``b84ee12a-09ef-421b-82de-0441a926375b``  (Radiohead OK Computer)

--tracks N
    Number of dummy tracks to pass to the tagger (default: 1).

Examples
--------
Profile the MusicBrainz tagger::

    MUSICBRAINZ_CONTACT=myapp@example.com \\
    strace -f -e trace=file,network,process \\
      poetry run python scripts/profile_extensions.py musicbrainz 2>mb.txt
    # Then extract paths: grep openat mb.txt | grep -v ENOENT | sort -u

Profile the CoverArt fetcher::

    strace -f -e trace=file,network,process \\
      poetry run python scripts/profile_extensions.py coverart \\
      --mbid b84ee12a-09ef-421b-82de-0441a926375b 2>ca.txt
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure kamp_daemon is importable when running from repo root.
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)


def _profile_musicbrainz(tracks: int) -> None:
    """Invoke KampMusicBrainzTagger.tag_release() through the worker subprocess."""
    from kamp_daemon.ext.builtin.musicbrainz import KampMusicBrainzTagger
    from kamp_daemon.ext.context import KampGround
    from kamp_daemon.ext.types import TrackMetadata
    from kamp_daemon.ext.worker import invoke_extension

    contact = os.environ.get("MUSICBRAINZ_CONTACT", "kamp-profiler@example.com")
    print(f"[profile] MusicBrainz tagger — {tracks} track(s), contact={contact}")

    dummy_tracks = [
        TrackMetadata(
            title="Karma Police",
            artist="Radiohead",
            album="OK Computer",
            track_number=4,
            total_tracks=12,
        )
        for _ in range(tracks)
    ]

    ctx = KampGround(
        permissions=frozenset({"network.fetch"}),
        allowed_domains=frozenset({"musicbrainz.org"}),
    )
    result = invoke_extension(
        KampMusicBrainzTagger,
        "tag_release",
        dummy_tracks,
        ctx=ctx,
    )
    print(f"[profile] result: {result}")


def _profile_coverart(mbid: str) -> None:
    """Invoke KampCoverArtArchive.fetch() through the worker subprocess."""
    from kamp_daemon.ext.builtin.coverart import KampCoverArtArchive
    from kamp_daemon.ext.context import KampGround
    from kamp_daemon.ext.types import ArtworkQuery
    from kamp_daemon.ext.worker import invoke_extension

    print(f"[profile] CoverArt Archive — release_mbid={mbid}")

    query = ArtworkQuery(release_mbid=mbid, min_dimension=300)
    ctx = KampGround(
        permissions=frozenset({"network.fetch"}),
        allowed_domains=frozenset({"coverartarchive.org"}),
    )
    result = invoke_extension(
        KampCoverArtArchive,
        "fetch",
        query,
        ctx=ctx,
    )
    print(f"[profile] result: {'artwork received' if result else result}")


def _profile_bandcamp() -> None:
    """Start KampBandcampSyncer and immediately stop it.

    This exercises the start() path and the Playwright subprocess launch
    without performing a full sync.  A valid session file is required
    (~/.local/share/kamp/bandcamp_session.json).
    """
    from kamp_daemon.ext.builtin.bandcamp import KampBandcampSyncer
    from kamp_daemon.ext.context import KampGround

    print("[profile] Bandcamp syncer — start/stop cycle")
    print("[profile] NOTE: Bandcamp uses its own subprocess isolation (_spawn_worker).")
    print(
        "[profile] Run this under dtruss/strace to capture Playwright + Chromium syscalls."
    )

    ctx = KampGround(
        permissions=frozenset({"network.fetch", "library.write"}),
        allowed_domains=frozenset({"bandcamp.com", "bcbits.com"}),
    )
    syncer = KampBandcampSyncer(ctx)
    try:
        syncer.start()
    finally:
        syncer.stop()
    print("[profile] syncer stopped cleanly")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile kamp built-in extensions under strace/dtruss.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "extension",
        choices=["musicbrainz", "coverart", "bandcamp"],
        help="Which extension to profile.",
    )
    parser.add_argument(
        "--mbid",
        default="b84ee12a-09ef-421b-82de-0441a926375b",
        help="MusicBrainz release MBID for coverart lookup (default: OK Computer).",
    )
    parser.add_argument(
        "--tracks",
        type=int,
        default=1,
        help="Number of dummy tracks for musicbrainz tagger (default: 1).",
    )
    args = parser.parse_args()

    if args.extension == "musicbrainz":
        _profile_musicbrainz(args.tracks)
    elif args.extension == "coverart":
        _profile_coverart(args.mbid)
    elif args.extension == "bandcamp":
        _profile_bandcamp()


if __name__ == "__main__":
    main()
