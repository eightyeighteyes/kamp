"""Tests for tune_shifter.tagger."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import mutagen.id3 as id3
import mutagen.mp4
import pytest

from tune_shifter.tagger import ReleaseInfo, TaggingError, TrackInfo, tag_directory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_RELEASE: dict[str, Any] = {
    "release-list": [
        {
            "id": "abc-123",
            "title": "Great Album",
            "date": "2020-04-01",
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


def _make_mp3(
    path: Path, artist: str = "Old Artist", album: str = "Old Album", track: int = 1
) -> None:
    """Write a minimal ID3-tagged MP3 stub."""
    tags = id3.ID3()
    tags["TPE1"] = id3.TPE1(encoding=3, text=artist)
    tags["TALB"] = id3.TALB(encoding=3, text=album)
    tags["TRCK"] = id3.TRCK(encoding=3, text=str(track))
    # Write tags to a file that looks like an MP3 (just needs the tag header)
    path.write_bytes(b"\xff\xfb" * 64)  # minimal fake MP3 frame
    tags.save(str(path))


def _make_m4a(
    path: Path, artist: str = "Old Artist", album: str = "Old Album", track: int = 1
) -> None:
    """Write a minimal M4A stub — we can't easily make a real one, so mock mutagen."""
    path.write_bytes(b"\x00" * 32)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTagDirectory:
    def test_raises_on_no_audio_files(self, tmp_path: Path) -> None:
        with pytest.raises(TaggingError, match="No audio files found"):
            tag_directory(tmp_path, [])

    def test_raises_when_musicbrainz_fails(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        import musicbrainzngs

        with patch.object(
            musicbrainzngs,
            "search_releases",
            side_effect=musicbrainzngs.WebServiceError("network error"),
        ):
            with pytest.raises(TaggingError, match="MusicBrainz search failed"):
                tag_directory(tmp_path, [mp3])

    def test_raises_when_no_results(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        with patch("musicbrainzngs.search_releases", return_value={"release-list": []}):
            with pytest.raises(TaggingError, match="No MusicBrainz results"):
                tag_directory(tmp_path, [mp3])

    def test_mp3_tags_written(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, track=1)

        with patch("musicbrainzngs.search_releases", return_value=SAMPLE_RELEASE):
            release = tag_directory(tmp_path, [mp3])

        assert release.mbid == "abc-123"
        assert release.title == "Great Album"
        assert release.year == "2020"

        tags = id3.ID3(str(mp3))
        assert str(tags["TALB"]) == "Great Album"
        assert str(tags["TPE1"]) == "Cool Artist"
        assert str(tags["TDRC"]) == "2020"
        assert str(tags["TXXX:MusicBrainz Release Id"]) == "abc-123"

    def test_selects_highest_score(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        multi_result: dict[str, Any] = {
            "release-list": [
                {
                    "id": "low",
                    "title": "Low Score",
                    "date": "",
                    "ext:score": "50",
                    "artist-credit": [{"artist": {"name": "A"}}],
                    "medium-list": [],
                },
                {
                    "id": "high",
                    "title": "High Score",
                    "date": "2021",
                    "ext:score": "99",
                    "artist-credit": [{"artist": {"name": "B"}}],
                    "medium-list": [],
                },
            ]
        }

        with patch("musicbrainzngs.search_releases", return_value=multi_result):
            release = tag_directory(tmp_path, [mp3])

        assert release.mbid == "high"
