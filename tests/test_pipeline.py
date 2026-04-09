"""End-to-end pipeline tests with all network calls mocked."""

import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import mutagen.id3 as id3
import pytest

from kamp_daemon.config import (
    ArtworkConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from kamp_daemon.artwork import ArtworkError
from kamp_daemon.ext.builtin.coverart import KampCoverArtArchive
from kamp_daemon.ext.builtin.musicbrainz import KampMusicBrainzTagger
from kamp_daemon.ext.context import KampGround, PlaybackSnapshot
from kamp_daemon.ext.types import ArtworkResult, TrackMetadata
from kamp_daemon.mover import MoveError
from kamp_daemon.pipeline_impl import (
    _fetch_and_embed_via_extension,
    _mb_tags_conflict,
    _quarantine,
    run,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            staging=tmp_path / "staging",
            library=tmp_path / "library",
        ),
        musicbrainz=MusicBrainzConfig(contact="test@example.com"),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=5_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
    )


def _make_zip(path: Path, tracks: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name in tracks:
            zf.writestr(name, b"\xff\xfb" * 64)  # fake MP3 bytes
    return path


MOCK_TRACKS = [
    TrackMetadata(
        title="First Track",
        artist="Cool Artist",
        album="Great Album",
        album_artist="Cool Artist",
        year="2020",
        track_number=1,
        mbid="",
        release_mbid="release-abc",
        release_group_mbid="rg-abc",
    )
]

MB_SEARCH_RESULT: dict[str, Any] = {
    "release-list": [
        {
            "id": "release-abc",
            "title": "Great Album",
            "date": "2020",
            "ext:score": "100",
            "artist-credit": [{"artist": {"name": "Cool Artist"}}],
            "medium-list": [],
        }
    ]
}

MB_RELEASE_DETAIL: dict[str, Any] = {
    "release": {
        "id": "release-abc",
        "title": "Great Album",
        "date": "2020",
        "artist-credit": [{"artist": {"name": "Cool Artist"}}],
        "release-group": {"id": "rg-abc"},
        "medium-list": [
            {
                "position": "1",
                "track-list": [
                    {
                        "number": "1",
                        "position": "1",
                        "recording": {"title": "First Track"},
                    },
                    {
                        "number": "2",
                        "position": "2",
                        "recording": {"title": "Second Track"},
                    },
                ],
            }
        ],
    }
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineRun:
    def test_zip_lands_in_library(self, tmp_path: Path, config: Config) -> None:
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        zip_path = config.paths.staging / "great-album.zip"
        _make_zip(zip_path, ["01 - First Track.mp3", "02 - Second Track.mp3"])

        # Write valid ID3 headers so mutagen can read/write tags
        extracted = config.paths.staging / "great-album"
        extracted.mkdir()
        for name in ["01 - First Track.mp3", "02 - Second Track.mp3"]:
            f = extracted / name
            f.write_bytes(b"\xff\xfb" * 64)
            tags = id3.ID3()
            tags["TPE1"] = id3.TPE1(encoding=3, text="Cool Artist")
            tags["TALB"] = id3.TALB(encoding=3, text="Great Album")
            tags.save(str(f))

        # Patch network calls
        with (
            patch("musicbrainzngs.search_releases", return_value=MB_SEARCH_RESULT),
            patch("musicbrainzngs.get_release_by_id", return_value=MB_RELEASE_DETAIL),
            patch("kamp_daemon.artwork.requests.get") as mock_get,
        ):
            # Make artwork fetch return an empty listing (no art — that's fine)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {"images": []}
            mock_get.return_value = resp

            # Run pipeline on the already-extracted directory (skip ZIP step)
            run(extracted, config)

        library_files = list(config.paths.library.rglob("*.mp3"))
        assert len(library_files) == 2

    def test_quarantine_on_extraction_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        config.paths.staging.mkdir(parents=True)

        bad_zip = config.paths.staging / "bad.zip"
        bad_zip.write_bytes(b"not a zip")

        run(bad_zip, config)

        errors_dir = config.paths.staging / "errors"
        assert errors_dir.exists()
        quarantined = list(errors_dir.iterdir())
        assert len(quarantined) == 1

    def test_quarantine_on_empty_directory(
        self, tmp_path: Path, config: Config
    ) -> None:
        """An extracted directory with no audio files is quarantined."""
        config.paths.staging.mkdir(parents=True)
        album_dir = config.paths.staging / "empty-album"
        album_dir.mkdir()
        (album_dir / "cover.jpg").write_bytes(b"fake image")

        run(album_dir, config)

        assert (config.paths.staging / "errors" / "empty-album").exists()

    def test_artwork_failure_is_nonfatal(self, tmp_path: Path, config: Config) -> None:
        """An ArtworkError is logged as a warning and the pipeline continues."""
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        album_dir = config.paths.staging / "great-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        import mutagen.id3 as id3

        tags = id3.ID3()
        tags["TPE1"] = id3.TPE1(encoding=3, text="Artist")
        tags["TALB"] = id3.TALB(encoding=3, text="Album")
        tags.save(str(mp3))

        with (
            patch("musicbrainzngs.search_releases", return_value=MB_SEARCH_RESULT),
            patch("musicbrainzngs.get_release_by_id", return_value=MB_RELEASE_DETAIL),
            patch(
                "kamp_daemon.pipeline_impl._fetch_and_embed_via_extension",
                side_effect=ArtworkError("no art"),
            ),
        ):
            run(album_dir, config)

        # File should have been moved to library despite artwork failure
        assert list(config.paths.library.rglob("*.mp3"))

    def test_quarantine_on_move_failure(self, tmp_path: Path, config: Config) -> None:
        """A MoveError causes the directory to be quarantined."""
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        album_dir = config.paths.staging / "great-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        import mutagen.id3 as id3

        tags = id3.ID3()
        tags.save(str(mp3))

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ),
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch(
                "kamp_daemon.pipeline_impl.move_to_library",
                side_effect=MoveError("disk full"),
            ),
        ):
            run(album_dir, config)

        assert (config.paths.staging / "errors" / "great-album").exists()

    def test_quarantine_tagging_failure(self, tmp_path: Path, config: Config) -> None:
        """A quarantine that itself fails logs an error rather than raising."""
        config.paths.staging.mkdir(parents=True)
        item = config.paths.staging / "bad-album"
        item.mkdir()

        with patch(
            "kamp_daemon.pipeline_impl.shutil.move", side_effect=OSError("no space")
        ):
            _quarantine(item, config.paths.staging)
        # Should not raise; errors/ dir was created even if move failed

    def test_quarantine_on_tagging_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        config.paths.staging.mkdir(parents=True)

        album_dir = config.paths.staging / "mystery-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags.save(str(mp3))

        with patch("musicbrainzngs.search_releases", return_value={"release-list": []}):
            run(album_dir, config)

        errors_dir = config.paths.staging / "errors"
        assert errors_dir.exists()


class TestOnDirectoryCallback:
    def test_callback_called_with_extracted_directory(
        self, tmp_path: Path, config: Config
    ) -> None:
        """run() calls _on_directory with the staging directory immediately after
        extraction so the watcher can cancel any pending timer for that directory."""
        import zipfile

        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        zip_path = config.paths.staging / "artist-album.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("01 - Track.mp3", b"\xff\xfb" * 64)

        claimed: list[Path] = []

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ),
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch(
                "kamp_daemon.pipeline_impl.move_to_library",
                return_value=[],
            ),
        ):
            run(zip_path, config, _on_directory=claimed.append)

        assert len(claimed) == 1
        assert claimed[0].parent == config.paths.staging
        assert claimed[0].name == "artist-album"

    def test_callback_not_called_on_extraction_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        """_on_directory must not fire when extraction fails."""
        config.paths.staging.mkdir(parents=True)
        bad_zip = config.paths.staging / "bad.zip"
        bad_zip.write_bytes(b"not a zip")

        claimed: list[Path] = []
        run(bad_zip, config, _on_directory=claimed.append)

        assert claimed == []


class TestSkipAlreadyTagged:
    """Pipeline skips the MusicBrainz lookup when all files already have an MBID.
    Artwork always runs — better art may be available (bundled in ZIP, or online).
    """

    def _setup_dir(self, config: Config) -> tuple[Path, Path]:
        """Create staging + library dirs and return (album_dir, mp3)."""
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.staging / "great-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))
        return album_dir, mp3

    def test_skips_tagging_and_runs_artwork_when_already_tagged(
        self, tmp_path: Path, config: Config
    ) -> None:
        """When all files are tagged, MB lookup is skipped but artwork always runs."""
        album_dir, mp3 = self._setup_dir(config)

        with (
            patch("kamp_daemon.pipeline_impl.is_tagged", return_value=True),
            patch(
                "kamp_daemon.pipeline_impl.read_release_mbids",
                return_value=("rel-abc", "rg-abc"),
            ),
            patch.object(KampMusicBrainzTagger, "tag_release") as mock_tag,
            patch(
                "kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"
            ) as mock_art,
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[mp3]),
        ):
            run(album_dir, config)

        mock_tag.assert_not_called()
        mock_art.assert_called_once()

    def test_runs_tagging_and_artwork_for_fresh_files(
        self, tmp_path: Path, config: Config
    ) -> None:
        """Fresh files (no tags) run both the MB lookup and artwork steps."""
        album_dir, mp3 = self._setup_dir(config)

        with (
            patch("kamp_daemon.pipeline_impl.is_tagged", return_value=False),
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ) as mock_tag,
            patch(
                "kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"
            ) as mock_art,
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[mp3]),
        ):
            run(album_dir, config)

        mock_tag.assert_called_once()
        mock_art.assert_called_once()

    def test_heterogeneous_directory_runs_tagging(
        self, tmp_path: Path, config: Config
    ) -> None:
        """If any file is untagged, the full MB lookup runs for the whole directory."""
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.staging / "partial-album"
        album_dir.mkdir()

        mp3_a = album_dir / "01.mp3"
        mp3_b = album_dir / "02.mp3"
        for mp3 in (mp3_a, mp3_b):
            mp3.write_bytes(b"\xff\xfb" * 64)
            id3.ID3().save(str(mp3))

        # 01 is tagged; 02 is not — the all() check must fail
        def is_tagged_side_effect(path: Path) -> bool:
            return path.name == "01.mp3"

        with (
            patch(
                "kamp_daemon.pipeline_impl.is_tagged",
                side_effect=is_tagged_side_effect,
            ),
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ) as mock_tag,
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch(
                "kamp_daemon.pipeline_impl.move_to_library",
                return_value=[mp3_a, mp3_b],
            ),
        ):
            run(album_dir, config)

        mock_tag.assert_called_once()


class TestStageCallback:
    """stage_callback receives stage labels in order and is cleared in finally."""

    def _setup_dir(self, config: Config) -> Path:
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.staging / "test-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))
        return album_dir

    def test_stages_called_in_order_on_success(
        self, tmp_path: Path, config: Config
    ) -> None:
        """stage_callback receives Tagging→Updating artwork→Moving→'' in order."""
        album_dir = self._setup_dir(config)
        calls: list[str] = []

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ),
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[]),
        ):
            run(album_dir, config, stage_callback=calls.append)

        assert calls == ["Extracting", "Tagging", "Updating artwork", "Moving", ""]

    def test_finally_clears_on_extraction_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        """stage_callback('') fires even when extraction fails."""
        config.paths.staging.mkdir(parents=True)
        bad_zip = config.paths.staging / "bad.zip"
        bad_zip.write_bytes(b"not a zip")
        calls: list[str] = []

        run(bad_zip, config, stage_callback=calls.append)

        assert calls[-1] == ""

    def test_finally_clears_on_tagging_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        """stage_callback('') fires even when tagging fails."""
        album_dir = self._setup_dir(config)
        calls: list[str] = []

        with patch("musicbrainzngs.search_releases", return_value={"release-list": []}):
            run(album_dir, config, stage_callback=calls.append)

        assert calls[-1] == ""


# ---------------------------------------------------------------------------
# _mb_tags_conflict
# ---------------------------------------------------------------------------


class TestMbTagsConflict:
    """Unit tests for the _mb_tags_conflict helper."""

    def _track(self, artist: str = "", album: str = "") -> TrackMetadata:
        return TrackMetadata(
            title="T",
            artist=artist,
            album=album,
            album_artist=artist,
            year="2024",
            track_number=1,
            mbid="",
        )

    def test_no_conflict_when_tags_match(self) -> None:
        orig = [self._track(artist="Artist", album="Album")]
        enr = [self._track(artist="Artist", album="Album")]
        assert not _mb_tags_conflict(orig, enr)

    def test_artist_mismatch_is_conflict(self) -> None:
        orig = [self._track(artist="Real Artist", album="Album")]
        enr = [self._track(artist="Wrong Artist", album="Album")]
        assert _mb_tags_conflict(orig, enr)

    def test_album_mismatch_is_conflict(self) -> None:
        orig = [self._track(artist="Artist", album="Real Album")]
        enr = [self._track(artist="Artist", album="Wrong Album")]
        assert _mb_tags_conflict(orig, enr)

    def test_comparison_is_case_insensitive(self) -> None:
        orig = [self._track(artist="cool artist", album="great album")]
        enr = [self._track(artist="Cool Artist", album="Great Album")]
        assert not _mb_tags_conflict(orig, enr)

    def test_no_conflict_when_original_artist_empty(self) -> None:
        """Empty original tags can't conflict — MB is just filling them in."""
        orig = [self._track(artist="", album="Album")]
        enr = [self._track(artist="Any Artist", album="Album")]
        assert not _mb_tags_conflict(orig, enr)

    def test_no_conflict_when_original_album_empty(self) -> None:
        orig = [self._track(artist="Artist", album="")]
        enr = [self._track(artist="Artist", album="Any Album")]
        assert not _mb_tags_conflict(orig, enr)

    def test_no_conflict_on_empty_lists(self) -> None:
        assert not _mb_tags_conflict([], [])


# ---------------------------------------------------------------------------
# MusicBrainz conflict fallback behaviour in pipeline run()
# ---------------------------------------------------------------------------


def _make_conflict_config(tmp_path: Path, trust: bool) -> Config:
    return Config(
        paths=PathsConfig(
            staging=tmp_path / "staging",
            library=tmp_path / "library",
        ),
        musicbrainz=MusicBrainzConfig(
            contact="test@example.com",
            trust_musicbrainz_when_tags_conflict=trust,
        ),
        artwork=ArtworkConfig(min_dimension=1000, max_bytes=5_000_000),
        library=LibraryConfig(
            path_template="{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"
        ),
    )


def _make_mp3_with_tags(path: Path, artist: str, album: str) -> None:
    """Write a fake MP3 with ID3 artist/album tags."""
    path.write_bytes(b"\xff\xfb" * 64)
    tags = id3.ID3()
    tags["TPE1"] = id3.TPE1(encoding=3, text=artist)
    tags["TALB"] = id3.TALB(encoding=3, text=album)
    tags.save(str(path))


MB_CONFLICTING_TRACKS = [
    TrackMetadata(
        title="MB Track",
        artist="MB Artist",  # differs from file tags ("File Artist")
        album="MB Album",  # differs from file tags ("File Album")
        album_artist="MB Artist",
        year="2024",
        track_number=1,
        mbid="rec-123",
        release_mbid="rel-123",
        release_group_mbid="rg-123",
    )
]


class TestMbConflictFallback:
    """When trust=False and MB returns mismatched tags, ID3 writes are skipped."""

    def _setup_album(self, config: Config) -> tuple[Path, Path]:
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.staging / "file-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        _make_mp3_with_tags(mp3, artist="File Artist", album="File Album")
        return album_dir, mp3

    def test_skips_id3_write_on_conflict_when_not_trusted(self, tmp_path: Path) -> None:
        """When trust=False and tags conflict, write_tags_from_track_metadata is not called."""
        config = _make_conflict_config(tmp_path, trust=False)
        album_dir, mp3 = self._setup_album(config)

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MB_CONFLICTING_TRACKS
            ),
            patch(
                "kamp_daemon.pipeline_impl.write_tags_from_track_metadata"
            ) as mock_write,
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[mp3]),
        ):
            run(album_dir, config)

        mock_write.assert_not_called()

    def test_artwork_still_runs_on_conflict_when_not_trusted(
        self, tmp_path: Path
    ) -> None:
        """Artwork step always runs even when ID3 tags are skipped due to conflict."""
        config = _make_conflict_config(tmp_path, trust=False)
        album_dir, mp3 = self._setup_album(config)

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MB_CONFLICTING_TRACKS
            ),
            patch("kamp_daemon.pipeline_impl.write_tags_from_track_metadata"),
            patch(
                "kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"
            ) as mock_art,
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[mp3]),
        ):
            run(album_dir, config)

        mock_art.assert_called_once()
        # MBIDs must be empty — we don't trust the conflicting lookup result
        call_kwargs = mock_art.call_args
        assert call_kwargs.kwargs["release_mbid"] == ""
        assert call_kwargs.kwargs["release_group_mbid"] == ""

    def test_writes_id3_when_trusted_despite_conflict(self, tmp_path: Path) -> None:
        """When trust=True (default), MB tags are written even if they differ."""
        config = _make_conflict_config(tmp_path, trust=True)
        album_dir, mp3 = self._setup_album(config)

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MB_CONFLICTING_TRACKS
            ),
            patch(
                "kamp_daemon.pipeline_impl.write_tags_from_track_metadata"
            ) as mock_write,
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[mp3]),
        ):
            run(album_dir, config)

        mock_write.assert_called_once()

    def test_writes_id3_when_no_conflict(self, tmp_path: Path) -> None:
        """When tags agree with MB, ID3 is written normally even with trust=False."""
        config = _make_conflict_config(tmp_path, trust=False)
        config.paths.staging.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.staging / "mb-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        # File tags match the MB result
        _make_mp3_with_tags(mp3, artist="MB Artist", album="MB Album")

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MB_CONFLICTING_TRACKS
            ),
            patch(
                "kamp_daemon.pipeline_impl.write_tags_from_track_metadata"
            ) as mock_write,
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[mp3]),
        ):
            run(album_dir, config)

        mock_write.assert_called_once()


# ---------------------------------------------------------------------------
# _fetch_and_embed_via_extension
# ---------------------------------------------------------------------------


class TestFetchAndEmbedViaExtension:
    """Unit tests for the _fetch_and_embed_via_extension helper."""

    def _ctx(self) -> KampGround:
        return KampGround(playback=PlaybackSnapshot(), library_tracks=[])

    def test_uses_local_artwork_when_found(self, tmp_path: Path) -> None:
        """When a qualifying local image is found, it is embedded without a network call."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))
        image_data = b"\xff\xd8\xff" + b"\x00" * 100  # minimal JPEG header

        with (
            patch(
                "kamp_daemon.pipeline_impl.find_local_artwork",
                return_value=tmp_path / "cover.jpg",
            ),
            patch(
                "kamp_daemon.artwork._load_local_artwork",
                return_value=image_data,
            ),
            patch("kamp_daemon.pipeline_impl._embed") as mock_embed,
            patch.object(KampCoverArtArchive, "fetch") as mock_fetch,
        ):
            _fetch_and_embed_via_extension(
                ctx=self._ctx(),
                audio_files=[mp3],
                release_mbid="rel-1",
                release_group_mbid="rg-1",
                directory=tmp_path,
                min_dimension=500,
                max_bytes=5_000_000,
            )

        mock_embed.assert_called_once_with(mp3, image_data)
        mock_fetch.assert_not_called()

    def test_skips_fetch_when_all_have_embedded_art(self, tmp_path: Path) -> None:
        """When all files already have qualifying embedded art, the Archive is not queried."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        with (
            patch("kamp_daemon.pipeline_impl.find_local_artwork", return_value=None),
            patch("kamp_daemon.artwork.has_embedded_art", return_value=True),
            patch.object(KampCoverArtArchive, "fetch") as mock_fetch,
            patch("kamp_daemon.pipeline_impl._embed") as mock_embed,
        ):
            _fetch_and_embed_via_extension(
                ctx=self._ctx(),
                audio_files=[mp3],
                release_mbid="rel-1",
                release_group_mbid="rg-1",
                directory=tmp_path,
                min_dimension=500,
                max_bytes=5_000_000,
            )

        mock_fetch.assert_not_called()
        mock_embed.assert_not_called()

    def test_embeds_artwork_from_cover_art_archive(self, tmp_path: Path) -> None:
        """Cover Art Archive result is embedded when no local art is available."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))
        image_data = b"\xff\xd8\xff" + b"\x00" * 200

        with (
            patch("kamp_daemon.pipeline_impl.find_local_artwork", return_value=None),
            patch("kamp_daemon.artwork.has_embedded_art", return_value=False),
            patch.object(
                KampCoverArtArchive,
                "fetch",
                return_value=ArtworkResult(
                    image_bytes=image_data, mime_type="image/jpeg"
                ),
            ),
            patch("kamp_daemon.pipeline_impl._embed") as mock_embed,
        ):
            _fetch_and_embed_via_extension(
                ctx=self._ctx(),
                audio_files=[mp3],
                release_mbid="rel-1",
                release_group_mbid="rg-1",
                directory=tmp_path,
                min_dimension=500,
                max_bytes=5_000_000,
            )

        mock_embed.assert_called_once_with(mp3, image_data)

    def test_no_art_anywhere_skips_embed(self, tmp_path: Path) -> None:
        """When no art is found anywhere, embed is never called."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        with (
            patch("kamp_daemon.pipeline_impl.find_local_artwork", return_value=None),
            patch("kamp_daemon.artwork.has_embedded_art", return_value=False),
            patch.object(KampCoverArtArchive, "fetch", return_value=None),
            patch("kamp_daemon.pipeline_impl._embed") as mock_embed,
        ):
            _fetch_and_embed_via_extension(
                ctx=self._ctx(),
                audio_files=[mp3],
                release_mbid="rel-1",
                release_group_mbid="rg-1",
                directory=tmp_path,
                min_dimension=500,
                max_bytes=5_000_000,
            )

        mock_embed.assert_not_called()

    def test_local_artwork_fails_quality_check_falls_back_to_archive(
        self, tmp_path: Path
    ) -> None:
        """When local artwork exists but _load_local_artwork returns None (quality fail),
        the pipeline falls through to the Cover Art Archive."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))
        image_data = b"\xff\xd8\xff" + b"\x00" * 100

        with (
            patch(
                "kamp_daemon.pipeline_impl.find_local_artwork",
                return_value=tmp_path / "cover.jpg",
            ),
            # Returns None — local art doesn't meet quality threshold
            patch("kamp_daemon.artwork._load_local_artwork", return_value=None),
            patch("kamp_daemon.artwork.has_embedded_art", return_value=False),
            patch.object(
                KampCoverArtArchive,
                "fetch",
                return_value=ArtworkResult(
                    image_bytes=image_data, mime_type="image/jpeg"
                ),
            ),
            patch("kamp_daemon.pipeline_impl._embed") as mock_embed,
        ):
            _fetch_and_embed_via_extension(
                ctx=self._ctx(),
                audio_files=[mp3],
                release_mbid="rel-1",
                release_group_mbid="rg-1",
                directory=tmp_path,
                min_dimension=500,
                max_bytes=5_000_000,
            )

        mock_embed.assert_called_once_with(mp3, image_data)
