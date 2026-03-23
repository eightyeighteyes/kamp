"""Tests for kamp_daemon.tagger."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import musicbrainzngs
import mutagen.id3 as id3
import mutagen.mp4
import pytest

from kamp_daemon.tagger import (
    ReleaseInfo,
    TaggingError,
    TrackInfo,
    _lookup_release_by_acoustid,
    _lookup_release_by_recordings,
    _parse_release,
    _read_existing_metadata,
    _read_track_metadata,
    _search_release,
    _write_acoustid_id,
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
    path: Path,
    artist: str = "Old Artist",
    album: str = "Old Album",
    track: int = 1,
    title: str = "",
) -> None:
    """Write a minimal ID3-tagged MP3 stub."""
    tags = id3.ID3()
    tags["TPE1"] = id3.TPE1(encoding=3, text=artist)
    tags["TALB"] = id3.TALB(encoding=3, text=album)
    tags["TRCK"] = id3.TRCK(encoding=3, text=str(track))
    if title:
        tags["TIT2"] = id3.TIT2(encoding=3, text=title)
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
            with patch("kamp_daemon.tagger.time.sleep"):
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


class TestParseReleaseMultiArtist:
    def test_joinphrase_used_in_artist_string(self) -> None:
        """joinphrase connectors from MB artist-credit are preserved in the artist tag."""
        raw: dict[str, Any] = {
            "id": "multi-artist-id",
            "title": "Tirakat",
            "date": "2026-03-20",
            "status": "Official",
            "country": "XW",
            "barcode": "",
            "asin": "",
            "text-representation": {"script": "Latn"},
            # musicbrainzngs returns a flat list: name-credit dicts interleaved with
            # joinphrase strings (not nested inside the dict as in the raw API JSON).
            "artist-credit": [
                {
                    "name": "Ali",
                    "artist": {
                        "id": "00a3ced4-16f5-469e-a7b0-c73c2581a42c",
                        "name": "Ali",
                        "sort-name": "Ali",
                    },
                },
                " & ",
                {
                    "name": "Charif Megarbane",
                    "artist": {
                        "id": "bb18173b-25b4-443f-adcf-bcd74df831b5",
                        "name": "Charif Megarbane",
                        "sort-name": "Megarbane, Charif",
                    },
                },
            ],
            "release-group": {
                "id": "rg-multi",
                "primary-type": "Album",
                "first-release-date": "2026-03-20",
            },
            "label-info-list": [],
            "medium-list": [
                {
                    "position": "1",
                    "track-list": [
                        {
                            "number": "1",
                            "position": "1",
                            "recording": {"id": "rec-1", "title": "Silk End"},
                        }
                    ],
                }
            ],
        }
        release = _parse_release(raw)

        assert release.artist == "Ali & Charif Megarbane"
        assert release.album_artist == "Ali & Charif Megarbane"


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

        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
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
                with patch("kamp_daemon.tagger.time.sleep"):
                    result = _search_release("Cool Artist", "Great Album")

        assert call_count == 3
        assert result.mbid == "abc-123"

    def test_raises_after_max_retries(self, tmp_path: Path) -> None:
        """TaggingError is raised when all retries are exhausted."""

        def always_fail(*args: Any, **kwargs: Any) -> None:
            raise musicbrainzngs.WebServiceError("503")

        with patch.object(musicbrainzngs, "search_releases", side_effect=always_fail):
            with patch("kamp_daemon.tagger.time.sleep"):
                with pytest.raises(TaggingError, match="after 3 retries"):
                    _search_release("Cool Artist", "Great Album")


class TestConfigureMusicbrainz:
    def test_sets_useragent(self) -> None:
        """configure_musicbrainz delegates to musicbrainzngs.set_useragent."""
        with patch("kamp_daemon.tagger.musicbrainzngs.set_useragent") as mock_ua:
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

        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
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
            "kamp_daemon.tagger.mutagen.mp4.MP4", side_effect=Exception("corrupt")
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

        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            assert is_tagged(m4a) is True

    def test_m4a_without_mbid_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the M4A has no MusicBrainz Release Id tag."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = {}

        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            assert is_tagged(m4a) is False

    def test_m4a_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when M4A tags object is None."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"\x00" * 32)

        mock_mp4 = MagicMock()
        mock_mp4.tags = None

        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
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

        with patch("kamp_daemon.tagger.id3.ID3", side_effect=Exception("corrupt")):
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

        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            rel, rg = read_release_mbids(m4a)

        assert rel == "rel-111"
        assert rg == "rg-222"

    def test_read_error_returns_empty_tuple(self, tmp_path: Path) -> None:
        """read_release_mbids returns ('', '') when reading raises an exception."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)

        with patch("kamp_daemon.tagger.id3.ID3", side_effect=Exception("io error")):
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

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
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

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            assert is_tagged(flac) is True

    def test_flac_without_mbid_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the FLAC has no MUSICBRAINZ_ALBUMID tag."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.tags = {}

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            assert is_tagged(flac) is False

    def test_flac_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the FLAC tags object is None."""
        flac = tmp_path / "01.flac"
        flac.write_bytes(b"fLaC")

        mock_flac = MagicMock()
        mock_flac.tags = None

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
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

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
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

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
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

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
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

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
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

        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
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
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            assert is_tagged(ogg) is True

    def test_ogg_without_mbid_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the OGG has no MUSICBRAINZ_ALBUMID tag."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = {}

        with patch(
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            assert is_tagged(ogg) is False

    def test_ogg_with_none_tags_returns_false(self, tmp_path: Path) -> None:
        """is_tagged returns False when the OGG tags object is None."""
        ogg = tmp_path / "01.ogg"
        ogg.write_bytes(b"OggS")

        mock_ogg = MagicMock()
        mock_ogg.tags = None

        with patch(
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
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
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
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
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
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
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
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
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
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
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            _write_ogg_tags(ogg, release, None)

        assert tags["ARTIST"] == ["Artist"]
        assert tags["ALBUM"] == ["Album"]
        assert tags["MUSICBRAINZ_ALBUMID"] == ["abc-123"]
        mock_ogg.save.assert_called_once()


# ---------------------------------------------------------------------------
# Per-track lookup fixtures and tests
# ---------------------------------------------------------------------------

SAMPLE_RECORDING_RESULT: dict[str, Any] = {
    "recording-list": [
        {
            "id": "rec-abc",
            "title": "First Track",
            "release-list": [
                {"id": "abc-123"},
                {"id": "other-release"},
            ],
        }
    ]
}


class TestReadTrackMetadata:
    def test_mp3_reads_artist_title_album(self, tmp_path: Path) -> None:
        """_read_track_metadata returns (artist, title, album) from an MP3."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, artist="De La Soul", album="Stakes Is High", title="Supa Emcees")

        artist, title, album = _read_track_metadata(mp3)

        assert artist == "De La Soul"
        assert title == "Supa Emcees"
        assert album == "Stakes Is High"

    def test_mp3_missing_title_returns_empty_title(self, tmp_path: Path) -> None:
        """_read_track_metadata returns empty title when TIT2 is absent."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, artist="Artist", album="Album")  # no title

        artist, title, album = _read_track_metadata(mp3)

        assert artist == "Artist"
        assert title == ""
        assert album == "Album"

    def test_unreadable_file_returns_empty_tuple(self, tmp_path: Path) -> None:
        """_read_track_metadata returns ('', '', '') on any read error."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)

        with patch("kamp_daemon.tagger.id3.ID3", side_effect=Exception("corrupt")):
            artist, title, album = _read_track_metadata(mp3)

        assert artist == ""
        assert title == ""
        assert album == ""


class TestLookupReleaseByRecordings:
    def test_majority_release_wins(self, tmp_path: Path) -> None:
        """The release MBID appearing most across per-track results is selected."""
        mp3_1 = tmp_path / "01.mp3"
        mp3_2 = tmp_path / "02.mp3"
        mp3_3 = tmp_path / "03.mp3"
        _make_mp3(mp3_1, title="Track One")
        _make_mp3(mp3_2, title="Track Two")
        _make_mp3(mp3_3, title="Track Three")

        # Tracks 1 and 2 vote only for "abc-123"; track 3 votes only for "other-release"
        recording_majority: dict[str, Any] = {
            "recording-list": [
                {"id": "rec-1", "title": "Track", "release-list": [{"id": "abc-123"}]}
            ]
        }
        recording_minority: dict[str, Any] = {
            "recording-list": [
                {
                    "id": "rec-3",
                    "title": "Track",
                    "release-list": [{"id": "other-release"}],
                }
            ]
        }

        call_count = 0

        def _mock_search(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return recording_majority if call_count <= 2 else recording_minority

        with patch("musicbrainzngs.search_recordings", side_effect=_mock_search):
            with patch(
                "musicbrainzngs.get_release_by_id",
                return_value=SAMPLE_RELEASE_DETAIL,
            ) as mock_get:
                result = _lookup_release_by_recordings([mp3_1, mp3_2, mp3_3])

        mock_get.assert_called_once_with(
            "abc-123",
            includes=["artists", "recordings", "release-groups", "labels"],
        )
        assert result.mbid == "abc-123"

    def test_no_titles_raises_tagging_error(self, tmp_path: Path) -> None:
        """TaggingError is raised when no files have title tags."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)  # no title

        with pytest.raises(TaggingError, match="no per-track"):
            _lookup_release_by_recordings([mp3])


class TestTagDirectoryPerTrackPath:
    def test_uses_per_track_path_when_titles_present(self, tmp_path: Path) -> None:
        """tag_directory calls search_recordings and skips search_releases when tracks have titles."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, artist="De La Soul", album="Stakes Is High", title="Supa Emcees")

        with patch(
            "musicbrainzngs.search_recordings",
            return_value=SAMPLE_RECORDING_RESULT,
        ) as mock_rec:
            with patch(
                "musicbrainzngs.get_release_by_id",
                return_value=SAMPLE_RELEASE_DETAIL,
            ):
                with patch("musicbrainzngs.search_releases") as mock_search:
                    release = tag_directory(tmp_path, [mp3])

        mock_rec.assert_called_once()
        mock_search.assert_not_called()
        assert release.mbid == "abc-123"

    def test_falls_back_to_album_search_when_no_titles(self, tmp_path: Path) -> None:
        """tag_directory falls back to search_releases when no track has a title tag."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)  # no title tag

        with patch(
            "musicbrainzngs.search_releases", return_value=SAMPLE_RELEASE
        ) as mock_search:
            with patch(
                "musicbrainzngs.get_release_by_id",
                return_value=SAMPLE_RELEASE_DETAIL,
            ):
                release = tag_directory(tmp_path, [mp3])

        mock_search.assert_called_once()
        assert release.mbid == "abc-123"


# AcoustID recording detail returned by get_recording_by_id
SAMPLE_RECORDING_DETAIL: dict[str, Any] = {
    "recording": {
        "id": "rec-aaa",
        "title": "First Track",
        "release-list": [{"id": "abc-123"}, {"id": "other-release"}],
    }
}


class TestLookupReleaseByAcoustid:
    def test_votes_on_releases_and_fetches_winner(self, tmp_path: Path) -> None:
        mp3_1 = tmp_path / "01.mp3"
        mp3_2 = tmp_path / "02.mp3"
        _make_mp3(mp3_1)
        _make_mp3(mp3_2)

        fp = (287.0, "AQADtEq")

        def _mock_mb_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is musicbrainzngs.get_recording_by_id:
                return SAMPLE_RECORDING_DETAIL
            if fn is musicbrainzngs.get_release_by_id:
                return SAMPLE_RELEASE_DETAIL
            raise AssertionError(f"Unexpected call: {fn}")

        with (
            patch("kamp_daemon.acoustid.fingerprint_file", return_value=fp),
            patch(
                "kamp_daemon.acoustid.lookup_matches",
                return_value=[("fp-uuid-1", ["rec-aaa"])],
            ),
            patch("kamp_daemon.tagger._mb_call", side_effect=_mock_mb_call),
        ):
            release, acoustid_ids = _lookup_release_by_acoustid([mp3_1, mp3_2])

        assert release.mbid == "abc-123"
        assert acoustid_ids[mp3_1] == "fp-uuid-1"
        assert acoustid_ids[mp3_2] == "fp-uuid-1"

    def test_raises_when_no_fingerprints(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)
        with patch("kamp_daemon.acoustid.fingerprint_file", return_value=None):
            with pytest.raises(
                TaggingError, match="AcoustID found no matching releases"
            ):
                _lookup_release_by_acoustid([mp3])

    def test_raises_when_no_matches(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3)
        with (
            patch("kamp_daemon.acoustid.fingerprint_file", return_value=(180.0, "abc")),
            patch("kamp_daemon.acoustid.lookup_matches", return_value=[]),
        ):
            with pytest.raises(
                TaggingError, match="AcoustID found no matching releases"
            ):
                _lookup_release_by_acoustid([mp3])


class TestTagDirectoryAcoustidTier:
    def test_acoustid_attempted_first(self, tmp_path: Path) -> None:
        """Tier 0 (AcoustID) is tried before per-track recording search."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, title="Some Track")
        fake_release = _parse_release(SAMPLE_RELEASE_DETAIL["release"])

        with (
            patch(
                "kamp_daemon.tagger._lookup_release_by_acoustid",
                return_value=(fake_release, {mp3: "fp-uuid-1"}),
            ) as mock_acoustid,
            patch("musicbrainzngs.search_recordings") as mock_search_rec,
        ):
            result = tag_directory(tmp_path, [mp3])

        mock_acoustid.assert_called_once_with([mp3])
        mock_search_rec.assert_not_called()
        assert result.mbid == "abc-123"

    def test_acoustid_writes_acoustid_id_tag(self, tmp_path: Path) -> None:
        """When AcoustID matches, the AcoustID ID is written as a tag."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, title="Some Track")
        fake_release = _parse_release(SAMPLE_RELEASE_DETAIL["release"])

        with (
            patch(
                "kamp_daemon.tagger._lookup_release_by_acoustid",
                return_value=(fake_release, {mp3: "fp-uuid-1"}),
            ),
            patch("kamp_daemon.tagger._write_acoustid_id") as mock_write_aid,
        ):
            tag_directory(tmp_path, [mp3])

        mock_write_aid.assert_called_once_with(mp3, "fp-uuid-1")

    def test_acoustid_falls_back_to_tier1_on_failure(self, tmp_path: Path) -> None:
        """When AcoustID raises TaggingError, per-track recording search runs."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(mp3, title="Some Track")

        with (
            patch(
                "kamp_daemon.tagger._lookup_release_by_acoustid",
                side_effect=TaggingError("no fingerprint"),
            ),
            patch(
                "musicbrainzngs.search_recordings",
                return_value=SAMPLE_RECORDING_RESULT,
            ) as mock_search_rec,
            patch(
                "musicbrainzngs.get_release_by_id",
                return_value=SAMPLE_RELEASE_DETAIL,
            ),
        ):
            result = tag_directory(tmp_path, [mp3])

        mock_search_rec.assert_called_once()
        assert result.mbid == "abc-123"


class TestWriteAcoustidId:
    def test_writes_txxx_to_mp3(self, tmp_path: Path) -> None:
        mp3 = tmp_path / "track.mp3"
        _make_mp3(mp3)
        _write_acoustid_id(mp3, "fp-uuid-1")
        tags = id3.ID3(str(mp3))
        assert tags.get("TXXX:Acoustid Id") is not None
        assert str(tags["TXXX:Acoustid Id"]) == "fp-uuid-1"

    def test_writes_acoustid_id_to_flac(self, tmp_path: Path) -> None:
        flac = tmp_path / "track.flac"
        flac.write_bytes(b"fLaC")
        tags: dict[str, Any] = {}
        mock_flac = MagicMock()
        mock_flac.tags = tags
        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            _write_acoustid_id(flac, "fp-uuid-2")
        assert tags["ACOUSTID_ID"] == ["fp-uuid-2"]
        mock_flac.save.assert_called_once()

    def test_writes_acoustid_id_to_ogg(self, tmp_path: Path) -> None:
        ogg = tmp_path / "track.ogg"
        ogg.write_bytes(b"OggS")
        tags: dict[str, Any] = {}
        mock_ogg = MagicMock()
        mock_ogg.tags = tags
        with patch(
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            _write_acoustid_id(ogg, "fp-uuid-3")
        assert tags["ACOUSTID_ID"] == ["fp-uuid-3"]
        mock_ogg.save.assert_called_once()

    def test_writes_acoustid_id_to_m4a(self, tmp_path: Path) -> None:
        m4a = tmp_path / "track.m4a"
        m4a.write_bytes(b"\x00" * 32)
        tags: dict[str, Any] = {}
        mock_mp4 = MagicMock()
        mock_mp4.tags = tags
        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            _write_acoustid_id(m4a, "fp-uuid-4")
        assert b"fp-uuid-4" in tags["----:com.apple.iTunes:Acoustid Id"][0]
        mock_mp4.save.assert_called_once()

    def test_skips_flac_with_no_tags(self, tmp_path: Path) -> None:
        flac = tmp_path / "track.flac"
        flac.write_bytes(b"fLaC")
        mock_flac = MagicMock()
        mock_flac.tags = None
        with patch("kamp_daemon.tagger.mutagen.flac.FLAC", return_value=mock_flac):
            _write_acoustid_id(flac, "fp-uuid-5")
        mock_flac.save.assert_not_called()

    def test_skips_ogg_with_no_tags(self, tmp_path: Path) -> None:
        ogg = tmp_path / "track.ogg"
        ogg.write_bytes(b"OggS")
        mock_ogg = MagicMock()
        mock_ogg.tags = None
        with patch(
            "kamp_daemon.tagger.mutagen.oggvorbis.OggVorbis", return_value=mock_ogg
        ):
            _write_acoustid_id(ogg, "fp-uuid-6")
        mock_ogg.save.assert_not_called()

    def test_skips_m4a_with_no_tags(self, tmp_path: Path) -> None:
        m4a = tmp_path / "track.m4a"
        m4a.write_bytes(b"\x00" * 32)
        mock_mp4 = MagicMock()
        mock_mp4.tags = None
        with patch("kamp_daemon.tagger.mutagen.mp4.MP4", return_value=mock_mp4):
            _write_acoustid_id(m4a, "fp-uuid-7")
        mock_mp4.save.assert_not_called()
