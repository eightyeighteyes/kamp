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
    _parse_release,
    _read_existing_metadata,
    _search_release,
    _write_flac_tags,
    _write_ogg_tags,
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

    def test_tie_breaks_by_earliest_date(self, tmp_path: Path) -> None:
        """When scores are equal, the earlier-dated release is preferred over a
        later vinyl reissue."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)

        tied_result: dict[str, Any] = {
            "release-list": [
                {
                    "id": "vinyl-reissue",
                    "title": "Album",
                    "date": "2021-04-30",
                    "ext:score": "100",
                    "artist-credit": [{"artist": {"name": "Artist"}}],
                },
                {
                    "id": "original",
                    "title": "Album",
                    "date": "2020-03-27",
                    "ext:score": "100",
                    "artist-credit": [{"artist": {"name": "Artist"}}],
                },
            ]
        }

        with patch("musicbrainzngs.search_releases", return_value=tied_result):
            with patch(
                "musicbrainzngs.get_release_by_id", return_value=SAMPLE_RELEASE_DETAIL
            ) as mock_get:
                tag_directory(tmp_path, [mp3])

        # Earlier-dated release must be tried first
        assert mock_get.call_args_list[0][0][0] == "original"

    def test_falls_back_to_next_candidate_on_parse_error(self, tmp_path: Path) -> None:
        """If the top candidate cannot be parsed (e.g. vinyl track numbers),
        the next candidate is tried automatically."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, track=1)

        # First candidate has a vinyl track number ("A1") that fails int()
        vinyl_detail: dict[str, Any] = {
            "release": {
                "id": "vinyl-id",
                "title": "Album",
                "date": "2021-04-30",
                "status": "Official",
                "country": "US",
                "barcode": "",
                "asin": "",
                "text-representation": {},
                "artist-credit": [
                    {"artist": {"id": "a1", "name": "Artist", "sort-name": "Artist"}}
                ],
                "release-group": {
                    "id": "rg-1",
                    "primary-type": "Album",
                    "first-release-date": "",
                },
                "label-info-list": [],
                "medium-list": [
                    {
                        "position": "1",
                        "track-list": [
                            {
                                "number": "A1",
                                "position": "1",
                                "recording": {"id": "rec-111", "title": "Track One"},
                            }
                        ],
                    }
                ],
            }
        }

        def _get_release(mbid: str, **_kwargs: Any) -> dict[str, Any]:
            if mbid == "vinyl-id":
                return vinyl_detail
            return SAMPLE_RELEASE_DETAIL

        two_candidates: dict[str, Any] = {
            "release-list": [
                {
                    "id": "vinyl-id",
                    "date": "2021",
                    "ext:score": "100",
                    "artist-credit": [{"artist": {"name": "Artist"}}],
                },
                {
                    "id": "abc-123",
                    "date": "2020",
                    "ext:score": "100",
                    "artist-credit": [{"artist": {"name": "Artist"}}],
                },
            ]
        }

        with patch("musicbrainzngs.search_releases", return_value=two_candidates):
            with patch("musicbrainzngs.get_release_by_id", side_effect=_get_release):
                release = tag_directory(tmp_path, [mp3])

        assert release.mbid == "abc-123"


class TestParseReleaseVinyl:
    def test_non_numeric_track_number_uses_position(self) -> None:
        """Vinyl track numbers like 'A1' fall back to the medium position."""
        raw: dict[str, Any] = {
            "id": "vinyl-id",
            "title": "Album",
            "date": "2021",
            "status": "Official",
            "country": "US",
            "barcode": "",
            "asin": "",
            "text-representation": {},
            "artist-credit": [
                {"artist": {"id": "a1", "name": "Artist", "sort-name": "Artist"}}
            ],
            "release-group": {
                "id": "rg-1",
                "primary-type": "Album",
                "first-release-date": "",
            },
            "label-info-list": [],
            "medium-list": [
                {
                    "position": "1",
                    "track-list": [
                        {
                            "number": "A1",
                            "position": "1",
                            "recording": {"id": "rec-1", "title": "Side A Track 1"},
                        },
                        {
                            "number": "A2",
                            "position": "2",
                            "recording": {"id": "rec-2", "title": "Side A Track 2"},
                        },
                    ],
                },
                {
                    "position": "2",
                    "track-list": [
                        {
                            "number": "B1",
                            "position": "1",
                            "recording": {"id": "rec-3", "title": "Side B Track 1"},
                        },
                    ],
                },
            ],
        }
        release = _parse_release(raw)

        # Non-numeric "A1" falls back to position=1 on disc 1
        assert "1-1" in release.tracks
        assert release.tracks["1-1"].title == "Side A Track 1"
        assert "1-2" in release.tracks
        assert "2-1" in release.tracks
        assert release.tracks["2-1"].title == "Side B Track 1"


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
        """_write_tags logs a warning for unsupported formats and does not raise."""
        wav = tmp_path / "track.wav"
        wav.write_bytes(b"RIFF")
        _write_tags(wav, self._make_release())  # must not raise

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
        mock_mp4.tags = {"----:com.apple.iTunes:MusicBrainz Release Id": [b"abc-123"]}

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

    def test_flac_returns_correct_mbids(self, tmp_path: Path) -> None:
        """read_release_mbids extracts both MBIDs from FLAC Vorbis comments."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.tags = {
            "MUSICBRAINZ_ALBUMID": ["rel-flac"],
            "MUSICBRAINZ_RELEASEGROUPID": ["rg-flac"],
        }

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            rel, rg = read_release_mbids(flac)

        assert rel == "rel-flac"
        assert rg == "rg-flac"


class TestIsTaggedFlac:
    def test_flac_with_mbid_returns_true(self, tmp_path: Path) -> None:
        """is_tagged returns True when the FLAC has a MUSICBRAINZ_ALBUMID tag."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.tags = {"MUSICBRAINZ_ALBUMID": ["rel-123"]}

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            assert is_tagged(flac) is True

    def test_flac_without_mbid_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the FLAC has no MUSICBRAINZ_ALBUMID tag."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.tags = {}

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            assert is_tagged(flac) is False

    def test_flac_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the FLAC tags object is None."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.tags = None

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            assert is_tagged(flac) is False


class TestTagDirectoryFlac:
    def test_flac_tags_written(self, tmp_path: Path) -> None:
        """MusicBrainz metadata is written to FLAC Vorbis comment fields."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        initial_tags: dict[str, Any] = {
            "ARTIST": ["Old Artist"],
            "ALBUM": ["Old Album"],
            "TRACKNUMBER": ["1"],
        }
        mock_flac = MagicMock()
        mock_flac.tags = initial_tags

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            with patch("musicbrainzngs.search_releases", return_value=SAMPLE_RELEASE):
                with patch(
                    "musicbrainzngs.get_release_by_id",
                    return_value=SAMPLE_RELEASE_DETAIL,
                ):
                    release = tag_directory(tmp_path, [flac])

        assert release.mbid == "abc-123"
        assert initial_tags["ARTIST"] == ["Cool Artist"]
        assert initial_tags["ALBUM"] == ["Great Album"]
        assert initial_tags["DATE"] == ["2020"]
        assert initial_tags["MUSICBRAINZ_ALBUMID"] == ["abc-123"]
        assert initial_tags["MUSICBRAINZ_RELEASEGROUPID"] == ["rg-456"]
        # ORIGINALYEAR is the year-only companion to ORIGINALDATE
        assert initial_tags["ORIGINALDATE"] == ["2020-04-01"]
        assert initial_tags["ORIGINALYEAR"] == ["2020"]
        # Track 1 of 2 on disc 1 of 1
        assert initial_tags["TRACKNUMBER"] == ["1"]
        assert initial_tags["TOTALTRACKS"] == ["2"]
        assert initial_tags["DISCNUMBER"] == ["1"]
        assert initial_tags["TOTALDISCS"] == ["1"]
        mock_flac.save.assert_called()

    def test_flac_match_track_reads_tracknumber(self, tmp_path: Path) -> None:
        """_match_track correctly parses TRACKNUMBER from FLAC Vorbis comments."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.tags = {
            "ARTIST": ["Artist"],
            "ALBUM": ["Album"],
            "TRACKNUMBER": ["2/10"],
            "DISCNUMBER": ["1"],
        }

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            with patch("musicbrainzngs.search_releases", return_value=SAMPLE_RELEASE):
                with patch(
                    "musicbrainzngs.get_release_by_id",
                    return_value=SAMPLE_RELEASE_DETAIL,
                ):
                    tag_directory(tmp_path, [flac])

        # Track 2 is "Second Track" in SAMPLE_RELEASE_DETAIL
        assert mock_flac.tags.get("TITLE") == ["Second Track"]

    def test_flac_add_tags_called_when_tags_none(self, tmp_path: Path) -> None:
        """_write_flac_tags calls add_tags() when audio.tags is None."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        tags: dict[str, Any] = {}
        mock_flac = MagicMock()
        mock_flac.tags = None  # simulate file with no tag block

        # add_tags() must actually install the tag dict so the assert passes
        def _install_tags() -> None:
            mock_flac.tags = tags

        mock_flac.add_tags.side_effect = _install_tags

        release = ReleaseInfo(
            mbid="abc-123",
            release_group_mbid="rg-456",
            title="Album",
            artist="Artist",
            album_artist="Artist",
            year="2020",
            tracks={},
        )

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            _write_flac_tags(flac, release, None)

        mock_flac.add_tags.assert_called_once()
        assert tags["ARTIST"] == ["Artist"]
        mock_flac.save.assert_called()

    def test_flac_minimal_release_skips_optional_fields(self, tmp_path: Path) -> None:
        """_write_flac_tags does not write optional tag keys when release fields are empty."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        tags: dict[str, Any] = {}
        mock_flac = MagicMock()
        mock_flac.tags = tags

        release = ReleaseInfo(
            mbid="abc-123",
            release_group_mbid="",
            title="Album",
            artist="Artist",
            album_artist="Artist",
            year="2020",
            tracks={},
            # all optional fields left at defaults (empty strings / empty lists)
        )

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            _write_flac_tags(flac, release, None)

        # Core fields are always written
        assert tags["ARTIST"] == ["Artist"]
        assert tags["ALBUM"] == ["Album"]
        # Optional fields must be absent when the release has no data for them
        assert "ARTISTSORT" not in tags
        assert "ALBUMARTISTSORT" not in tags
        assert "ARTISTS" not in tags
        assert "MUSICBRAINZ_ARTISTID" not in tags
        assert "MUSICBRAINZ_ALBUMARTISTID" not in tags
        assert "MUSICBRAINZ_RELEASEGROUPID" not in tags
        assert "MUSICBRAINZ_ALBUMTYPE" not in tags
        assert "MUSICBRAINZ_ALBUMSTATUS" not in tags
        assert "RELEASECOUNTRY" not in tags
        assert "ORIGINALDATE" not in tags
        assert "ORIGINALYEAR" not in tags
        assert "LABEL" not in tags
        assert "CATALOGNUMBER" not in tags
        assert "BARCODE" not in tags
        assert "ASIN" not in tags
        assert "SCRIPT" not in tags
        # track is None → TITLE / TRACKNUMBER / DISCNUMBER must not be set
        assert "TITLE" not in tags
        assert "TRACKNUMBER" not in tags
        mock_flac.save.assert_called()

    def test_flac_track_without_recording_mbid(self, tmp_path: Path) -> None:
        """_write_flac_tags omits MUSICBRAINZ_TRACKID when recording_mbid is empty."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        tags: dict[str, Any] = {}
        mock_flac = MagicMock()
        mock_flac.tags = tags

        release = ReleaseInfo(
            mbid="abc-123",
            release_group_mbid="rg-456",
            title="Album",
            artist="Artist",
            album_artist="Artist",
            year="2020",
            tracks={},
        )
        track = TrackInfo(number=1, disc=1, title="Track One", recording_mbid="")

        with patch("tune_shifter.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            _write_flac_tags(flac, release, track)

        assert tags["TITLE"] == ["Track One"]
        assert tags["TRACKNUMBER"] == ["1"]
        assert tags["TOTALDISCS"] == ["1"]
        assert "TOTALTRACKS" not in tags  # total_tracks=0 → omitted
        assert "MUSICBRAINZ_TRACKID" not in tags


class TestIsTaggedOgg:
    def test_ogg_with_mbid_returns_true(self, tmp_path: Path) -> None:
        """is_tagged returns True when the OGG has a MUSICBRAINZ_ALBUMID tag."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = {"MUSICBRAINZ_ALBUMID": ["rel-123"]}

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            assert is_tagged(ogg) is True

    def test_ogg_without_mbid_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the OGG has no MUSICBRAINZ_ALBUMID tag."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = {}

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            assert is_tagged(ogg) is False

    def test_ogg_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the OGG tags object is None."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = None

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            assert is_tagged(ogg) is False


class TestReadReleaseMbidsOgg:
    def test_ogg_returns_correct_mbids(self, tmp_path: Path) -> None:
        """read_release_mbids extracts both MBIDs from OGG Vorbis comments."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = {
            "MUSICBRAINZ_ALBUMID": ["rel-abc"],
            "MUSICBRAINZ_RELEASEGROUPID": ["rg-xyz"],
        }

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            rel, rg = read_release_mbids(ogg)

        assert rel == "rel-abc"
        assert rg == "rg-xyz"

    def test_ogg_missing_tags_returns_empty_strings(self, tmp_path: Path) -> None:
        """read_release_mbids returns empty strings for OGG with no MBID tags."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = {}

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            rel, rg = read_release_mbids(ogg)

        assert rel == ""
        assert rg == ""


class TestTagDirectoryOgg:
    def test_ogg_tags_written(self, tmp_path: Path) -> None:
        """MusicBrainz metadata is written to OGG Vorbis comment fields."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        initial_tags: dict[str, Any] = {
            "ARTIST": ["Old Artist"],
            "ALBUM": ["Old Album"],
            "TRACKNUMBER": ["1"],
        }
        mock_ogg = MagicMock()
        mock_ogg.tags = initial_tags

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            with patch("musicbrainzngs.search_releases", return_value=SAMPLE_RELEASE):
                with patch(
                    "musicbrainzngs.get_release_by_id",
                    return_value=SAMPLE_RELEASE_DETAIL,
                ):
                    release = tag_directory(tmp_path, [ogg])

        assert release.mbid == "abc-123"
        assert initial_tags["ARTIST"] == ["Cool Artist"]
        assert initial_tags["ALBUM"] == ["Great Album"]
        assert initial_tags["DATE"] == ["2020"]
        assert initial_tags["MUSICBRAINZ_ALBUMID"] == ["abc-123"]
        assert initial_tags["MUSICBRAINZ_RELEASEGROUPID"] == ["rg-456"]
        assert initial_tags["ORIGINALDATE"] == ["2020-04-01"]
        assert initial_tags["ORIGINALYEAR"] == ["2020"]
        assert initial_tags["TRACKNUMBER"] == ["1"]
        assert initial_tags["TOTALTRACKS"] == ["2"]
        assert initial_tags["DISCNUMBER"] == ["1"]
        assert initial_tags["TOTALDISCS"] == ["1"]
        mock_ogg.save.assert_called()

    def test_ogg_match_track_reads_tracknumber(self, tmp_path: Path) -> None:
        """_match_track correctly parses TRACKNUMBER from OGG Vorbis comments."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = {
            "ARTIST": ["Artist"],
            "ALBUM": ["Album"],
            "TRACKNUMBER": ["2"],
            "DISCNUMBER": ["1"],
        }

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            with patch("musicbrainzngs.search_releases", return_value=SAMPLE_RELEASE):
                with patch(
                    "musicbrainzngs.get_release_by_id",
                    return_value=SAMPLE_RELEASE_DETAIL,
                ):
                    tag_directory(tmp_path, [ogg])

        # Track 2 is "Second Track" in SAMPLE_RELEASE_DETAIL
        assert mock_ogg.tags.get("TITLE") == ["Second Track"]

    def test_write_ogg_tags_writes_core_fields(self, tmp_path: Path) -> None:
        """_write_ogg_tags writes core Vorbis comment fields via _assign_vorbis_tags."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        tags: dict[str, Any] = {}
        mock_ogg = MagicMock()
        mock_ogg.tags = tags

        release = ReleaseInfo(
            mbid="abc-123",
            release_group_mbid="rg-456",
            title="Album",
            artist="Artist",
            album_artist="Artist",
            year="2020",
            tracks={},
        )

        with patch(
            "tune_shifter.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            _write_ogg_tags(ogg, release, None)

        assert tags["ARTIST"] == ["Artist"]
        assert tags["ALBUM"] == ["Album"]
        assert tags["MUSICBRAINZ_ALBUMID"] == ["abc-123"]
        mock_ogg.save.assert_called_once()
