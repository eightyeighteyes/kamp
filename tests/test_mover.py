"""Tests for tune_shifter.mover."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import mutagen.flac
import mutagen.id3 as id3
import mutagen.mp4
import pytest

from tune_shifter.mover import (
    MoveError,
    _cleanup_staging,
    _destination,
    move_to_library,
)

TEMPLATE = "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"


def _make_mp3(path: Path, **tags: str) -> None:
    """Write a minimal ID3-tagged MP3 stub."""
    t = id3.ID3()
    if "album_artist" in tags:
        t["TPE2"] = id3.TPE2(encoding=3, text=tags["album_artist"])
    if "album" in tags:
        t["TALB"] = id3.TALB(encoding=3, text=tags["album"])
    if "year" in tags:
        t["TDRC"] = id3.TDRC(encoding=3, text=tags["year"])
    if "track" in tags:
        t["TRCK"] = id3.TRCK(encoding=3, text=tags["track"])
    if "title" in tags:
        t["TIT2"] = id3.TIT2(encoding=3, text=tags["title"])
    path.write_bytes(b"\xff\xfb" * 64)
    t.save(str(path))


class TestDestination:
    def test_slash_in_title_does_not_create_extra_directory(
        self, tmp_path: Path
    ) -> None:
        """A '/' in a track title must be replaced, not treated as a path separator."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(
            mp3,
            album_artist="Aesop Rock",
            album="Bazooka Tooth",
            year="2003",
            track="1",
            title="N.Y. Electric / Hunter Interlude",
        )

        library = tmp_path / "library"
        result = _destination(mp3, library, TEMPLATE)

        # The title slash must not split into a subdirectory
        assert result.parent == library / "Aesop Rock" / "2003 - Bazooka Tooth"
        assert "/" not in result.name

    def test_sanitizes_colon_and_question_mark_in_tags(self, tmp_path: Path) -> None:
        """Other unsafe chars (: ?) in tag values are also replaced."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album: The Sequel",
            year="2020",
            track="1",
            title="Track?",
        )

        library = tmp_path / "library"
        result = _destination(mp3, library, TEMPLATE)

        assert ":" not in str(result)
        assert "?" not in str(result)


class TestCleanup:
    def test_removes_directory_with_leftover_non_audio_files(
        self, tmp_path: Path
    ) -> None:
        """Staging dir is fully removed even when non-audio files remain."""
        staging = tmp_path / "Artist - Album"
        staging.mkdir()
        (staging / "cover.jpg").write_bytes(b"fake image")
        (staging / "booklet.pdf").write_bytes(b"fake pdf")

        _cleanup_staging(staging)

        assert not staging.exists()

    def test_does_not_raise_when_directory_is_missing(self, tmp_path: Path) -> None:
        """OSError from rmtree is caught; cleanup never raises."""
        _cleanup_staging(tmp_path / "nonexistent")


class TestM4ADestination:
    def _mock_mp4(self, **tags: object) -> MagicMock:
        """Return a mock mutagen.mp4.MP4 with the given tag values."""
        mp4 = MagicMock()
        t4: dict[str, object] = {}
        if "artist" in tags:
            t4["\xa9ART"] = [tags["artist"]]
        if "album_artist" in tags:
            t4["aART"] = [tags["album_artist"]]
        if "album" in tags:
            t4["\xa9alb"] = [tags["album"]]
        if "year" in tags:
            t4["\xa9day"] = [tags["year"]]
        if "track" in tags:
            t4["trkn"] = [(tags["track"], 0)]
        if "disc" in tags:
            t4["disk"] = [(tags["disc"], 0)]
        if "title" in tags:
            t4["\xa9nam"] = [tags["title"]]
        mp4.tags = t4
        return mp4

    def test_m4a_tags_are_read(self, tmp_path: Path) -> None:
        """_destination reads tags from an M4A file via mutagen.mp4."""
        m4a = tmp_path / "01.m4a"
        m4a.write_bytes(b"")  # content doesn't matter — MP4 is mocked

        with patch(
            "tune_shifter.mover.mutagen.mp4.MP4",
            return_value=self._mock_mp4(
                artist="Artist",
                album_artist="Album Artist",
                album="My Album",
                year="2021",
                track=3,
                disc=1,
                title="My Track",
            ),
        ):
            library = tmp_path / "library"
            result = _destination(m4a, library, TEMPLATE)

        assert result.parent == library / "Album Artist" / "2021 - My Album"
        assert result.name == "03 - My Track.m4a"

    def test_m4a_falls_back_to_defaults_when_tags_absent(self, tmp_path: Path) -> None:
        """_destination uses fallback values when M4A tags are empty."""
        m4a = tmp_path / "track.m4a"
        m4a.write_bytes(b"")

        mock_mp4 = MagicMock()
        mock_mp4.tags = {}  # empty tag dict

        with patch("tune_shifter.mover.mutagen.mp4.MP4", return_value=mock_mp4):
            library = tmp_path / "library"
            result = _destination(m4a, library, TEMPLATE)

        assert "Unknown Artist" in str(result)
        assert "Unknown Album" in str(result)

    def test_m4a_no_tags_object_falls_back(self, tmp_path: Path) -> None:
        """_destination handles mp4.tags being None gracefully."""
        m4a = tmp_path / "track.m4a"
        m4a.write_bytes(b"")

        mock_mp4 = MagicMock()
        mock_mp4.tags = None

        with patch("tune_shifter.mover.mutagen.mp4.MP4", return_value=mock_mp4):
            library = tmp_path / "library"
            result = _destination(m4a, library, TEMPLATE)

        assert "Unknown Artist" in str(result)


class TestFlacDestination:
    def _mock_flac(self, **tags: object) -> MagicMock:
        """Return a mock mutagen.flac.FLAC with Vorbis comment dict."""
        flac = MagicMock()
        vorbis: dict[str, list[object]] = {}
        if "artist" in tags:
            vorbis["ARTIST"] = [tags["artist"]]
        if "album_artist" in tags:
            vorbis["ALBUMARTIST"] = [tags["album_artist"]]
        if "album" in tags:
            vorbis["ALBUM"] = [tags["album"]]
        if "year" in tags:
            vorbis["DATE"] = [tags["year"]]
        if "track" in tags:
            vorbis["TRACKNUMBER"] = [tags["track"]]
        if "disc" in tags:
            vorbis["DISCNUMBER"] = [tags["disc"]]
        if "title" in tags:
            vorbis["TITLE"] = [tags["title"]]
        flac.tags = vorbis
        return flac

    def test_flac_tags_are_read(self, tmp_path: Path) -> None:
        """_destination reads Vorbis comments from a FLAC file via mutagen.flac."""
        flac_file = tmp_path / "01.flac"
        flac_file.write_bytes(b"")  # content doesn't matter — FLAC is mocked

        with patch(
            "tune_shifter.mover.mutagen.flac.FLAC",
            return_value=self._mock_flac(
                artist="Artist",
                album_artist="Album Artist",
                album="My Album",
                year="2021",
                track="3",
                disc="1",
                title="My Track",
            ),
        ):
            library = tmp_path / "library"
            result = _destination(flac_file, library, TEMPLATE)

        assert result.parent == library / "Album Artist" / "2021 - My Album"
        assert result.name == "03 - My Track.flac"

    def test_flac_tracknumber_with_slash_is_parsed(self, tmp_path: Path) -> None:
        """TRACKNUMBER in 'N/total' format is parsed correctly."""
        flac_file = tmp_path / "05.flac"
        flac_file.write_bytes(b"")

        with patch(
            "tune_shifter.mover.mutagen.flac.FLAC",
            return_value=self._mock_flac(
                album_artist="Band",
                album="Record",
                year="2019",
                track="5/12",
                title="Fifth",
            ),
        ):
            library = tmp_path / "library"
            result = _destination(flac_file, library, TEMPLATE)

        assert result.name == "05 - Fifth.flac"

    def test_flac_falls_back_to_defaults_when_tags_absent(self, tmp_path: Path) -> None:
        """_destination uses fallback values when FLAC tags dict is empty."""
        flac_file = tmp_path / "track.flac"
        flac_file.write_bytes(b"")

        mock_flac = MagicMock()
        mock_flac.tags = {}  # empty Vorbis comment dict

        with patch("tune_shifter.mover.mutagen.flac.FLAC", return_value=mock_flac):
            library = tmp_path / "library"
            result = _destination(flac_file, library, TEMPLATE)

        assert "Unknown Artist" in str(result)
        assert "Unknown Album" in str(result)

    def test_flac_no_tags_object_falls_back(self, tmp_path: Path) -> None:
        """_destination handles flac.tags being None gracefully."""
        flac_file = tmp_path / "track.flac"
        flac_file.write_bytes(b"")

        mock_flac = MagicMock()
        mock_flac.tags = None

        with patch("tune_shifter.mover.mutagen.flac.FLAC", return_value=mock_flac):
            library = tmp_path / "library"
            result = _destination(flac_file, library, TEMPLATE)

        assert "Unknown Artist" in str(result)


class TestMoveToLibrary:
    def test_moves_files_and_cleans_staging(self, tmp_path: Path) -> None:
        """move_to_library moves all files and removes the staging dir."""
        staging = tmp_path / "Artist - Album"
        staging.mkdir()
        mp3 = staging / "01.mp3"
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2021",
            track="1",
            title="Song",
        )
        library = tmp_path / "library"
        library.mkdir()

        dests = move_to_library([mp3], staging, library, TEMPLATE)

        assert len(dests) == 1
        assert dests[0].exists()
        assert not staging.exists()

    def test_raises_move_error_on_failure(self, tmp_path: Path) -> None:
        """move_to_library raises MoveError when shutil.move fails."""
        staging = tmp_path / "Artist - Album"
        staging.mkdir()
        mp3 = staging / "01.mp3"
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2021",
            track="1",
            title="Song",
        )
        library = tmp_path / "library"

        with patch("tune_shifter.mover.shutil.move", side_effect=OSError("disk full")):
            with pytest.raises(MoveError, match="disk full"):
                move_to_library([mp3], staging, library, TEMPLATE)

    def test_template_error_raises_move_error(self, tmp_path: Path) -> None:
        """_destination raises MoveError when the template references a missing key."""
        mp3 = tmp_path / "01.mp3"
        _make_mp3(
            mp3,
            album_artist="Artist",
            album="Album",
            year="2021",
            track="1",
            title="Song",
        )
        library = tmp_path / "library"
        bad_template = "{nonexistent_key}.{ext}"

        with pytest.raises(MoveError, match="Path template error"):
            _destination(mp3, library, bad_template)
