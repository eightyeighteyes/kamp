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
from tune_shifter.pipeline import run
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
        musicbrainz=MusicBrainzConfig(
            app_name="tune-shifter-test",
            app_version="0.0.1",
            contact="test@example.com",
        ),
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
    ]
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
