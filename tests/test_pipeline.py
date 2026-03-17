"""End-to-end pipeline tests with all network calls mocked."""

import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import mutagen.id3 as id3
import pytest

from tune_shifter.config import (
    ArtworkConfig,
    Config,
    LibraryConfig,
    MusicBrainzConfig,
    PathsConfig,
)
from tune_shifter.artwork import ArtworkError
from tune_shifter.mover import MoveError
from tune_shifter.pipeline import _quarantine, run
from tune_shifter.tagger import ReleaseInfo, TrackInfo

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


MOCK_RELEASE = ReleaseInfo(
    mbid="release-abc",
    release_group_mbid="rg-abc",
    title="Great Album",
    artist="Cool Artist",
    album_artist="Cool Artist",
    year="2020",
    tracks={
        "1-1": TrackInfo(number=1, disc=1, title="First Track"),
        "1-2": TrackInfo(number=2, disc=1, title="Second Track"),
    },
)

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
            patch("tune_shifter.artwork.requests.get") as mock_get,
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
                "tune_shifter.pipeline.fetch_and_embed",
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
            patch("tune_shifter.pipeline.tag_directory", return_value=MOCK_RELEASE),
            patch("tune_shifter.pipeline.fetch_and_embed"),
            patch(
                "tune_shifter.pipeline.move_to_library",
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
            "tune_shifter.pipeline.shutil.move", side_effect=OSError("no space")
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
            patch("tune_shifter.pipeline.tag_directory", return_value=MOCK_RELEASE),
            patch("tune_shifter.pipeline.fetch_and_embed"),
            patch(
                "tune_shifter.pipeline.move_to_library",
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
            patch("tune_shifter.pipeline.is_tagged", return_value=True),
            patch(
                "tune_shifter.pipeline.read_release_mbids",
                return_value=("rel-abc", "rg-abc"),
            ),
            patch("tune_shifter.pipeline.tag_directory") as mock_tag,
            patch("tune_shifter.pipeline.fetch_and_embed") as mock_art,
            patch("tune_shifter.pipeline.move_to_library", return_value=[mp3]),
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
            patch("tune_shifter.pipeline.is_tagged", return_value=False),
            patch(
                "tune_shifter.pipeline.tag_directory", return_value=MOCK_RELEASE
            ) as mock_tag,
            patch("tune_shifter.pipeline.fetch_and_embed") as mock_art,
            patch("tune_shifter.pipeline.move_to_library", return_value=[mp3]),
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
                "tune_shifter.pipeline.is_tagged",
                side_effect=is_tagged_side_effect,
            ),
            patch(
                "tune_shifter.pipeline.tag_directory", return_value=MOCK_RELEASE
            ) as mock_tag,
            patch("tune_shifter.pipeline.fetch_and_embed"),
            patch(
                "tune_shifter.pipeline.move_to_library",
                return_value=[mp3_a, mp3_b],
            ),
        ):
            run(album_dir, config)

        mock_tag.assert_called_once()
