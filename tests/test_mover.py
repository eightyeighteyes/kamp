"""Tests for tune_shifter.mover."""

from pathlib import Path

import mutagen.id3 as id3
import pytest

from tune_shifter.mover import _cleanup_staging, _destination

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
