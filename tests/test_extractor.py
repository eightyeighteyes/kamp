"""Tests for tune_shifter.extractor."""

import zipfile
from pathlib import Path

import pytest

from tune_shifter.extractor import ExtractionError, extract, find_audio_files


def _make_zip(dest: Path, members: dict[str, bytes]) -> Path:
    """Write a ZIP file at *dest* containing *members* (name → bytes)."""
    with zipfile.ZipFile(dest, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return dest


class TestExtract:
    def test_directory_returned_as_is(self, tmp_path: Path) -> None:
        d = tmp_path / "album"
        d.mkdir()
        (d / "01.mp3").write_bytes(b"fake mp3")
        assert extract(d) == d

    def test_zip_is_extracted(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "album.zip"
        _make_zip(zip_path, {"01 - Track.mp3": b"fake mp3"})

        result = extract(zip_path)

        assert result == tmp_path / "album"
        assert (result / "01 - Track.mp3").exists()

    def test_zip_is_deleted_after_extraction(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "album.zip"
        _make_zip(zip_path, {"01.mp3": b"fake"})
        extract(zip_path)
        assert not zip_path.exists()

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "album.tar.gz"
        bad.write_bytes(b"not a zip")
        with pytest.raises(ExtractionError, match="not a ZIP or folder"):
            extract(bad)

    def test_zip_with_no_audio_raises(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "empty.zip"
        _make_zip(zip_path, {"cover.jpg": b"fake image"})
        with pytest.raises(ExtractionError, match="no supported audio files"):
            extract(zip_path)

    def test_zip_with_flac_is_extracted(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "album.zip"
        _make_zip(zip_path, {"01 - Track.flac": b"fLaC fake"})

        result = extract(zip_path)

        assert result == tmp_path / "album"
        assert (result / "01 - Track.flac").exists()

    def test_bad_zip_raises(self, tmp_path: Path) -> None:
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"this is not a zip file")
        with pytest.raises(ExtractionError, match="Failed to open ZIP"):
            extract(bad_zip)

    def test_zip_with_ogg_is_extracted(self, tmp_path: Path) -> None:
        zip_path = tmp_path / "album.zip"
        _make_zip(zip_path, {"01 - Track.ogg": b"OggS"})

        result = extract(zip_path)

        assert result == tmp_path / "album"
        assert (result / "01 - Track.ogg").exists()


class TestFindAudioFiles:
    def test_finds_mp3_and_m4a(self, tmp_path: Path) -> None:
        (tmp_path / "01.mp3").write_bytes(b"")
        (tmp_path / "02.m4a").write_bytes(b"")
        (tmp_path / "cover.jpg").write_bytes(b"")
        files = find_audio_files(tmp_path)
        assert len(files) == 2
        assert all(f.suffix in {".mp3", ".m4a"} for f in files)

    def test_finds_flac(self, tmp_path: Path) -> None:
        (tmp_path / "01.flac").write_bytes(b"")
        (tmp_path / "02.mp3").write_bytes(b"")
        (tmp_path / "cover.jpg").write_bytes(b"")
        files = find_audio_files(tmp_path)
        assert len(files) == 2
        assert any(f.suffix == ".flac" for f in files)

    def test_returns_sorted_list(self, tmp_path: Path) -> None:
        (tmp_path / "03.mp3").write_bytes(b"")
        (tmp_path / "01.mp3").write_bytes(b"")
        (tmp_path / "02.mp3").write_bytes(b"")
        files = find_audio_files(tmp_path)
        names = [f.name for f in files]
        assert names == sorted(names)

    def test_finds_ogg(self, tmp_path: Path) -> None:
        (tmp_path / "01.ogg").write_bytes(b"")
        (tmp_path / "02.mp3").write_bytes(b"")
        (tmp_path / "cover.jpg").write_bytes(b"")
        files = find_audio_files(tmp_path)
        assert len(files) == 2
        assert any(f.suffix == ".ogg" for f in files)

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert find_audio_files(tmp_path) == []
