"""Tests for tune_shifter.tagger."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import musicbrainzngs
import mutagen.id3 as id3
import mutagen.mp4
import pytest

from tune_shifter.tagger import (
    ReleaseInfo,
    TaggingError,
    TrackInfo,
    _read_existing_metadata,
    _search_release,
    _write_tags,
    configure_musicbrainz,
    is_tagged,
    read_release_mbids,
    tag_directory,
)

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

# Full release detail returned by get_release_by_id — includes all extended fields.
SAMPLE_RELEASE_DETAIL: dict[str, Any] = {
    "release": {
        "id": "abc-123",
        "title": "Great Album",
        "date": "2020-04-01",
        "status": "Official",
        "country": "US",
        "barcode": "123456789",
        "asin": "B08XYZ",
        "text-representation": {"script": "Latn"},
        "artist-credit": [
            {
                "name": "Cool Artist",
                "artist": {
                    "id": "artist-mbid-1",
                    "name": "Cool Artist",
                    "sort-name": "Artist, Cool",
                },
            }
        ],
        "release-group": {
            "id": "rg-456",
            "primary-type": "Album",
            "first-release-date": "2020-04-01",
        },
        "label-info-list": [
            {
                "label": {"name": "Great Label"},
                "catalog-number": "GRL-001",
            }
        ],
        "medium-list": [
            {
                "position": "1",
                "track-list": [
                    {
                        "number": "1",
                        "position": "1",
                        "recording": {"id": "rec-111", "title": "First Track"},
                    },
                    {
                        "number": "2",
                        "position": "2",
                        "recording": {"id": "rec-222", "title": "Second Track"},
                    },
                ],
            }
        ],
    }
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

        with patch.object(
            musicbrainzngs,
            "search_releases",
            side_effect=musicbrainzngs.WebServiceError("network error"),
        ):
            with patch("tune_shifter.tagger.time.sleep"):
                with pytest.raises(TaggingError, match="after 3 retries"):
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
            with patch(
                "musicbrainzngs.get_release_by_id", return_value=SAMPLE_RELEASE_DETAIL
            ):
                release = tag_directory(tmp_path, [mp3])

        assert release.mbid == "abc-123"
        assert release.release_group_mbid == "rg-456"
        assert release.title == "Great Album"
        assert release.year == "2020"

        tags = id3.ID3(str(mp3))
        assert str(tags["TALB"]) == "Great Album"
        assert str(tags["TPE1"]) == "Cool Artist"
        assert str(tags["TDRC"]) == "2020"
        assert str(tags["TXXX:MusicBrainz Release Id"]) == "abc-123"
        # Extended tags
        assert str(tags["TSOP"]) == "Artist, Cool"
        assert str(tags["TXXX:MusicBrainz Artist Id"]) == "artist-mbid-1"
        assert str(tags["TXXX:MusicBrainz Release Group Id"]) == "rg-456"
        assert str(tags["TXXX:MusicBrainz Album Type"]) == "Album"
        assert str(tags["TXXX:MusicBrainz Album Status"]) == "Official"
        assert str(tags["TXXX:MusicBrainz Album Release Country"]) == "US"
        assert str(tags["TPUB"]) == "Great Label"
        assert str(tags["TXXX:CATALOGNUMBER"]) == "GRL-001"
        assert str(tags["TXXX:BARCODE"]) == "123456789"
        assert str(tags["TDOR"]) == "2020-04-01"
        assert str(tags["TRCK"]) == "1/2"
        assert str(tags["TXXX:MusicBrainz Track Id"]) == "rec-111"

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
            with patch(
                "musicbrainzngs.get_release_by_id", return_value=SAMPLE_RELEASE_DETAIL
            ) as mock_get:
                tag_directory(tmp_path, [mp3])

        # The winning MBID ("high") must be passed to get_release_by_id
        assert mock_get.call_args[0][0] == "high"


class TestEditionSuffixRetry:
    def test_retries_with_cleaned_album_name(self, tmp_path: Path) -> None:
        """First search with "(Deluxe Edition)" returns nothing; retry with stripped
        name succeeds and tags are written."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, album="Great Album (Deluxe Edition)", track=1)

        empty: dict[str, Any] = {"release-list": []}

        with patch(
            "musicbrainzngs.search_releases", side_effect=[empty, SAMPLE_RELEASE]
        ) as mock_search:
            with patch(
                "musicbrainzngs.get_release_by_id", return_value=SAMPLE_RELEASE_DETAIL
            ):
                release = tag_directory(tmp_path, [mp3])

        assert release.mbid == "abc-123"
        # First call used the full name; second used the stripped name
        assert mock_search.call_count == 2
        first_call_album = mock_search.call_args_list[0].kwargs["release"]
        second_call_album = mock_search.call_args_list[1].kwargs["release"]
        assert "Deluxe Edition" in first_call_album
        assert "Deluxe Edition" not in second_call_album

    def test_no_retry_when_album_has_no_suffix(self, tmp_path: Path) -> None:
        """Album names without an edition suffix do not trigger a second search."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, album="Great Album", track=1)

        with patch(
            "musicbrainzngs.search_releases", return_value={"release-list": []}
        ) as mock_search:
            with pytest.raises(TaggingError):
                tag_directory(tmp_path, [mp3])

        assert mock_search.call_count == 1

    def test_raises_when_retry_also_finds_nothing(self, tmp_path: Path) -> None:
        """If both the original and cleaned name return no results, TaggingError is raised."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, album="Obscure Album (Remastered)", track=1)

        empty: dict[str, Any] = {"release-list": []}

        with patch("musicbrainzngs.search_releases", return_value=empty):
            with pytest.raises(TaggingError, match="No MusicBrainz results"):
                tag_directory(tmp_path, [mp3])


class TestReadExistingMetadata:
    def test_raises_when_no_tags_and_directory_not_artist_album(
        self, tmp_path: Path
    ) -> None:
        """No readable tags + directory name not 'Artist - Album' → TaggingError
        raised before any MusicBrainz call is made."""
        album_dir = tmp_path / "Psalm 69 The Way to Succeed"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        # Write a valid MP3 with no artist or album tag
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        with patch("musicbrainzngs.search_releases") as mock_search:
            with pytest.raises(
                TaggingError, match="Could not determine artist or album"
            ):
                tag_directory(album_dir, [mp3])

        mock_search.assert_not_called()


class TestTagDirectoryM4a:
    def test_m4a_tags_written(self, tmp_path: Path) -> None:
        """MusicBrainz metadata is written to M4A tag fields."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 64)

        # Simulate an iTunes M4A that already has artist/album/track tags
        initial_tags: dict[str, Any] = {
            "\xa9ART": ["Old Artist"],
            "\xa9alb": ["Old Album"],
            "trkn": [(1, 0)],
        }
        mock_mp4 = MagicMock()
        mock_mp4.tags = initial_tags

        with patch("tune_shifter.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            with patch("musicbrainzngs.search_releases", return_value=SAMPLE_RELEASE):
                with patch(
                    "musicbrainzngs.get_release_by_id",
                    return_value=SAMPLE_RELEASE_DETAIL,
                ):
                    release = tag_directory(tmp_path, [m4a])

        assert release.mbid == "abc-123"
        assert initial_tags["\xa9ART"] == ["Cool Artist"]
        assert initial_tags["\xa9alb"] == ["Great Album"]
        assert initial_tags["\xa9day"] == ["2020"]
        assert "----:com.apple.iTunes:MusicBrainz Release Id" in initial_tags
        mock_mp4.save.assert_called()


class TestRetry:
    def test_retries_on_web_service_error(self, tmp_path: Path) -> None:
        """_mb_call retries up to 3 times before raising TaggingError."""
        call_count = 0

        def flaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise musicbrainzngs.WebServiceError("503")
            return SAMPLE_RELEASE

        with patch.object(musicbrainzngs, "search_releases", side_effect=flaky):
            with patch(
                "musicbrainzngs.get_release_by_id", return_value=SAMPLE_RELEASE_DETAIL
            ):
                with patch("tune_shifter.tagger.time.sleep"):
                    result = _search_release("Cool Artist", "Great Album")

        assert call_count == 3
        assert result.mbid == "abc-123"

    def test_raises_after_max_retries(self, tmp_path: Path) -> None:
        """TaggingError is raised when all retries are exhausted."""

        def always_fail(*args: Any, **kwargs: Any) -> None:
            raise musicbrainzngs.WebServiceError("503")

        with patch.object(musicbrainzngs, "search_releases", side_effect=always_fail):
            with patch("tune_shifter.tagger.time.sleep"):
                with pytest.raises(TaggingError, match="after 3 retries"):
                    _search_release("Cool Artist", "Great Album")


class TestConfigureMusicbrainz:
    def test_sets_useragent(self) -> None:
        """configure_musicbrainz delegates to musicbrainzngs.set_useragent."""
        with patch("tune_shifter.tagger.musicbrainzngs.set_useragent") as mock_ua:
            configure_musicbrainz("my-app", "1.0", "test@example.com")
        mock_ua.assert_called_once_with("my-app", "1.0", "test@example.com")


class TestReadExistingMetadataEdgeCases:
    def test_directory_name_heuristic_with_dash_format(self, tmp_path: Path) -> None:
        """'Artist - Album' directory name is used when the file has no tags."""
        album_dir = tmp_path / "Cool Artist - Great Album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        artist, album = _read_existing_metadata(mp3)
        assert artist == "Cool Artist"
        assert album == "Great Album"

    def test_m4a_with_none_tags_falls_back(self, tmp_path: Path) -> None:
        """_read_existing_metadata handles M4A with tags=None by falling back."""
        album_dir = tmp_path / "Artist - Album"
        album_dir.mkdir()
        m4a = album_dir / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = None

        with patch("tune_shifter.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            artist, album = _read_existing_metadata(m4a)

        assert artist == "Artist"
        assert album == "Album"

    def test_read_exception_falls_back_to_directory(self, tmp_path: Path) -> None:
        """An exception during tag reading falls back to directory name heuristic."""
        album_dir = tmp_path / "Fallback Artist - Fallback Album"
        album_dir.mkdir()
        m4a = album_dir / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        with patch(
            "tune_shifter.tagger.mutagen.mp4.MP4", side_effect=Exception("corrupt")
        ):
            artist, album = _read_existing_metadata(m4a)

        assert artist == "Fallback Artist"
        assert album == "Fallback Album"


class TestWriteTagsEdgeCases:
    def _make_release(self) -> ReleaseInfo:
        return ReleaseInfo(
            mbid="mbid-1",
            release_group_mbid="rg-1",
            title="Album",
            artist="Artist",
            album_artist="Artist",
            year="2020",
            tracks={"1-1": TrackInfo(number=1, disc=1, title="Track One")},
        )

    def test_unsupported_format_logs_warning(self, tmp_path: Path) -> None:
        """_write_tags logs a warning for .flac files and does not raise."""
        flac = tmp_path / "track.flac"
        flac.write_bytes(b"fLaC")
        _write_tags(flac, self._make_release())  # must not raise

    def test_mp3_without_id3_header_gets_fresh_tags(self, tmp_path: Path) -> None:
        """_write_tags on an MP3 with no ID3 header creates a fresh tag set."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)  # no ID3 header

        _write_tags(mp3, self._make_release())

        tags = id3.ID3(str(mp3))
        assert str(tags["TALB"]) == "Album"


class TestIsTagged:
    def test_mp3_with_mbid_returns_true(self, tmp_path: Path) -> None:
        """is_tagged returns True when the MP3 has a MusicBrainz Release Id frame."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TXXX:MusicBrainz Release Id"] = id3.TXXX(
            encoding=3, desc="MusicBrainz Release Id", text="abc-123"
        )
        tags.save(str(mp3))

        assert is_tagged(mp3) is True

    def test_mp3_without_mbid_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the MP3 has no MusicBrainz Release Id."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        assert is_tagged(mp3) is False

    def test_m4a_with_mbid_returns_true(self, tmp_path: Path) -> None:
        """is_tagged returns True when the M4A has a MusicBrainz Release Id tag."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = {
            "----:com.apple.iTunes:MusicBrainz Release Id": [b"abc-123"]
        }

        with patch("tune_shifter.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            assert is_tagged(m4a) is True

    def test_m4a_without_mbid_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the M4A has no MusicBrainz Release Id tag."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = {}

        with patch("tune_shifter.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            assert is_tagged(m4a) is False

    def test_m4a_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when M4A tags object is None."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = None

        with patch("tune_shifter.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            assert is_tagged(m4a) is False

    def test_unsupported_format_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False for unsupported file formats."""
        flac = tmp_path / "track.flac"
        flac.write_bytes(b"fLaC")

        assert is_tagged(flac) is False

    def test_read_error_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when reading the file raises an exception."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)

        with patch("tune_shifter.tagger.id3.ID3", side_effect=Exception("corrupt")):
            assert is_tagged(mp3) is False


class TestReadReleaseMbids:
    def test_mp3_returns_correct_mbids(self, tmp_path: Path) -> None:
        """read_release_mbids extracts both release and release-group IDs from MP3."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TXXX:MusicBrainz Release Id"] = id3.TXXX(
            encoding=3, desc="MusicBrainz Release Id", text="rel-111"
        )
        tags["TXXX:MusicBrainz Release Group Id"] = id3.TXXX(
            encoding=3, desc="MusicBrainz Release Group Id", text="rg-222"
        )
        tags.save(str(mp3))

        rel, rg = read_release_mbids(mp3)
        assert rel == "rel-111"
        assert rg == "rg-222"

    def test_mp3_missing_frames_returns_empty_strings(self, tmp_path: Path) -> None:
        """read_release_mbids returns empty strings for MP3 with no MBID frames."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))

        rel, rg = read_release_mbids(mp3)
        assert rel == ""
        assert rg == ""

    def test_m4a_returns_correct_mbids(self, tmp_path: Path) -> None:
        """read_release_mbids extracts both MBIDs from M4A tags."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = {
            "----:com.apple.iTunes:MusicBrainz Release Id": [b"rel-111"],
            "----:com.apple.iTunes:MusicBrainz Release Group Id": [b"rg-222"],
        }

        with patch("tune_shifter.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            rel, rg = read_release_mbids(m4a)

        assert rel == "rel-111"
        assert rg == "rg-222"

    def test_read_error_returns_empty_tuple(self, tmp_path: Path) -> None:
        """read_release_mbids returns ('', '') when reading raises an exception."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)

        with patch("tune_shifter.tagger.id3.ID3", side_effect=Exception("io error")):
            rel, rg = read_release_mbids(mp3)

        assert rel == ""
        assert rg == ""
