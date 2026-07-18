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
from kamp_core.library import _read_mp3_tags

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            watch_folder=tmp_path / "watch",
            library=tmp_path / "library",
        ),
        musicbrainz=MusicBrainzConfig(),
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
        release_date="2020",
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
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        zip_path = config.paths.watch_folder / "great-album.zip"
        _make_zip(zip_path, ["01 - First Track.mp3", "02 - Second Track.mp3"])

        # Write valid ID3 headers so mutagen can read/write tags
        extracted = config.paths.watch_folder / "great-album"
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
        config.paths.watch_folder.mkdir(parents=True)

        bad_zip = config.paths.watch_folder / "bad.zip"
        bad_zip.write_bytes(b"not a zip")

        run(bad_zip, config)

        errors_dir = config.paths.watch_folder / "errors"
        assert errors_dir.exists()
        quarantined = list(errors_dir.iterdir())
        assert len(quarantined) == 1

    def test_quarantine_on_empty_directory(
        self, tmp_path: Path, config: Config
    ) -> None:
        """An extracted directory with no audio files is quarantined."""
        config.paths.watch_folder.mkdir(parents=True)
        album_dir = config.paths.watch_folder / "empty-album"
        album_dir.mkdir()
        (album_dir / "cover.jpg").write_bytes(b"fake image")

        run(album_dir, config)

        assert (config.paths.watch_folder / "errors" / "empty-album").exists()

    def test_artwork_failure_is_nonfatal(self, tmp_path: Path, config: Config) -> None:
        """An ArtworkError is logged as a warning and the pipeline continues."""
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        album_dir = config.paths.watch_folder / "great-album"
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

    def test_cover_file_mode_writes_cover_jpg_to_library(
        self, tmp_path: Path, config: Config
    ) -> None:
        """With save_format='cover-file', pipeline writes cover.jpg to library dir."""
        config.artwork = ArtworkConfig(
            min_dimension=1000, max_bytes=5_000_000, save_format="cover-file"
        )
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        album_dir = config.paths.watch_folder / "great-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags["TPE1"] = id3.TPE1(encoding=3, text="Cool Artist")
        tags["TALB"] = id3.TALB(encoding=3, text="Great Album")
        tags.save(str(mp3))

        image_data = b"\xff\xd8\xff" + b"\x00" * 200

        with (
            patch("musicbrainzngs.search_releases", return_value=MB_SEARCH_RESULT),
            patch("musicbrainzngs.get_release_by_id", return_value=MB_RELEASE_DETAIL),
            patch("kamp_daemon.pipeline_impl.find_local_artwork", return_value=None),
            patch("kamp_daemon.artwork.has_embedded_art", return_value=False),
            patch.object(
                KampCoverArtArchive,
                "fetch",
                return_value=ArtworkResult(
                    image_bytes=image_data, mime_type="image/jpeg"
                ),
            ),
        ):
            run(album_dir, config)

        library_mp3s = list(config.paths.library.rglob("*.mp3"))
        assert len(library_mp3s) == 1

        cover = library_mp3s[0].parent / "cover.jpg"
        assert cover.is_file()
        assert cover.read_bytes() == image_data

        # Art must not be embedded in the file
        from mutagen.id3 import ID3

        saved_tags = ID3(str(library_mp3s[0]))
        assert not any(k.startswith("APIC") for k in saved_tags)

    def test_quarantine_on_move_failure(self, tmp_path: Path, config: Config) -> None:
        """A MoveError causes the directory to be quarantined."""
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        album_dir = config.paths.watch_folder / "great-album"
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

        assert (config.paths.watch_folder / "errors" / "great-album").exists()

    def test_quarantine_tagging_failure(self, tmp_path: Path, config: Config) -> None:
        """A quarantine that itself fails logs an error rather than raising."""
        config.paths.watch_folder.mkdir(parents=True)
        item = config.paths.watch_folder / "bad-album"
        item.mkdir()

        with patch(
            "kamp_daemon.pipeline_impl.shutil.move", side_effect=OSError("no space")
        ):
            _quarantine(item, config.paths.watch_folder)
        # Should not raise; errors/ dir was created even if move failed

    def test_quarantine_on_tagging_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        config.paths.watch_folder.mkdir(parents=True)

        album_dir = config.paths.watch_folder / "mystery-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        tags = id3.ID3()
        tags.save(str(mp3))

        with patch("musicbrainzngs.search_releases", return_value={"release-list": []}):
            run(album_dir, config)

        errors_dir = config.paths.watch_folder / "errors"
        assert errors_dir.exists()


class TestOnDirectoryCallback:
    def test_callback_called_with_extracted_directory(
        self, tmp_path: Path, config: Config
    ) -> None:
        """run() calls _on_directory with the staging directory immediately after
        extraction so the watcher can cancel any pending timer for that directory."""
        import zipfile

        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)

        zip_path = config.paths.watch_folder / "artist-album.zip"
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
        assert claimed[0].parent == config.paths.watch_folder
        assert claimed[0].name == "artist-album"

    def test_callback_not_called_on_extraction_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        """_on_directory must not fire when extraction fails."""
        config.paths.watch_folder.mkdir(parents=True)
        bad_zip = config.paths.watch_folder / "bad.zip"
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
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.watch_folder / "great-album"
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
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.watch_folder / "partial-album"
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
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.watch_folder / "test-album"
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
            run(
                album_dir,
                config,
                stage_callback=lambda s, _sid, _c, _alb: calls.append(s),
            )

        assert calls == ["Extracting", "Tagging", "Updating artwork", "Moving", ""]

    def test_finally_clears_on_extraction_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        """stage_callback('') fires even when extraction fails."""
        config.paths.watch_folder.mkdir(parents=True)
        bad_zip = config.paths.watch_folder / "bad.zip"
        bad_zip.write_bytes(b"not a zip")
        calls: list[str] = []

        run(bad_zip, config, stage_callback=lambda s, _sid, _c, _alb: calls.append(s))

        assert calls[-1] == ""

    def test_finally_clears_on_tagging_failure(
        self, tmp_path: Path, config: Config
    ) -> None:
        """stage_callback('') fires even when tagging fails."""
        album_dir = self._setup_dir(config)
        calls: list[str] = []

        with patch("musicbrainzngs.search_releases", return_value={"release-list": []}):
            run(
                album_dir,
                config,
                stage_callback=lambda s, _sid, _c, _alb: calls.append(s),
            )

        assert calls[-1] == ""

    # -- KAMP-562: stage payload carries sale_item_id + committed ------------

    def _seed_pending(self, db_path: Path, artifact: Path, sid: str) -> None:
        """Seed a collection item + pending_ingest row so run() resolves
        provenance and emits the album's sale_item_id."""
        from kamp_core.library import LibraryIndex

        idx = LibraryIndex(db_path)
        idx.upsert_collection_item(sid, mode="local")
        idx.add_pending_ingest(str(artifact), sid, "T1")
        idx.close()

    def test_stage_payload_carries_sale_item_id_and_committed_on_success(
        self, tmp_path: Path, config: Config
    ) -> None:
        """Every stage carries the album's sale_item_id; the terminal reset
        reports committed=True once the item reached the library (KAMP-562)."""
        album_dir = self._setup_dir(config)
        db = tmp_path / "lib.db"
        self._seed_pending(db, album_dir, "S1")
        stages: list[tuple[str, str | None, bool]] = []

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ),
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch(
                "kamp_daemon.pipeline_impl.move_to_library",
                return_value=[config.paths.library / "x.mp3"],
            ),
        ):
            run(
                album_dir,
                config,
                stage_callback=lambda s, sid, c, _alb: stages.append((s, sid, c)),
                index_path=db,
            )

        # sale_item_id is present on every emission and equals the album row's id.
        assert {sid for _s, sid, _c in stages} == {"S1"}
        # committed flips True only after the successful move, so the terminal
        # reset is the one that reports it.
        assert stages[-1] == ("", "S1", True)
        # In-flight stages are not yet committed.
        assert all(not c for s, _sid, c in stages if s != "")

    def test_stage_payload_committed_false_on_quarantine(
        self, tmp_path: Path, config: Config
    ) -> None:
        """A pipeline that quarantines never commits, so the terminal reset
        reports committed=False — the UI clears the badge with no rescan to wait
        for (KAMP-562)."""
        album_dir = self._setup_dir(config)
        db = tmp_path / "lib.db"
        self._seed_pending(db, album_dir, "S1")
        stages: list[tuple[str, str | None, bool]] = []

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
            run(
                album_dir,
                config,
                stage_callback=lambda s, sid, c, _alb: stages.append((s, sid, c)),
                index_path=db,
            )

        assert stages[-1] == ("", "S1", False)

    def test_stage_payload_sid_none_without_provenance(
        self, tmp_path: Path, config: Config
    ) -> None:
        """A manual (non-download) drop has no pending_ingest, so sale_item_id is
        None and the card shows no tag badge (KAMP-562)."""
        album_dir = self._setup_dir(config)
        stages: list[tuple[str, str | None, bool]] = []

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ),
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[]),
        ):
            run(
                album_dir,
                config,
                stage_callback=lambda s, sid, c, _alb: stages.append((s, sid, c)),
            )  # no index_path → no provenance

        assert {sid for _s, sid, _c in stages} == {None}

    # -- KAMP-558: stage payload carries the album display label --------------

    def test_stage_payload_carries_album_after_extraction(
        self, tmp_path: Path, config: Config
    ) -> None:
        """The album label is empty before extraction (so the Extracting tooltip
        stays generic) and becomes the on-disk album folder name for every stage
        after extraction succeeds (KAMP-558)."""
        album_dir = self._setup_dir(config)  # created as watch_folder/"test-album"
        stages: list[tuple[str, str]] = []

        with (
            patch.object(
                KampMusicBrainzTagger, "tag_release", return_value=MOCK_TRACKS
            ),
            patch("kamp_daemon.pipeline_impl._fetch_and_embed_via_extension"),
            patch("kamp_daemon.pipeline_impl.move_to_library", return_value=[]),
        ):
            run(
                album_dir,
                config,
                stage_callback=lambda s, _sid, _c, alb: stages.append((s, alb)),
            )

        by_stage = dict(stages)
        # Extracting fires before we know the album folder → generic (empty).
        assert by_stage["Extracting"] == ""
        # Post-extraction stages carry the album folder name.
        assert by_stage["Tagging"] == "test-album"
        assert by_stage["Moving"] == "test-album"
        # The terminal reset also carries it (the closure keeps the resolved name).
        assert by_stage[""] == "test-album"


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
            release_date="2024",
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


def _make_conflict_config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            watch_folder=tmp_path / "watch",
            library=tmp_path / "library",
        ),
        musicbrainz=MusicBrainzConfig(),
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
        release_date="2024",
        track_number=1,
        mbid="rec-123",
        release_mbid="rel-123",
        release_group_mbid="rg-123",
    )
]


class TestMbConflictFallback:
    """When MB returns mismatched tags, ID3 writes are skipped (KAMP-589: always;
    the trust-MB override was removed) but artwork still uses the MB MBID."""

    def _setup_album(self, config: Config) -> tuple[Path, Path]:
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.watch_folder / "file-album"
        album_dir.mkdir()
        mp3 = album_dir / "01.mp3"
        _make_mp3_with_tags(mp3, artist="File Artist", album="File Album")
        return album_dir, mp3

    def test_skips_id3_write_on_conflict(self, tmp_path: Path) -> None:
        """When tags conflict, write_tags_from_track_metadata is not called (KAMP-589)."""
        config = _make_conflict_config(tmp_path)
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

    def test_artwork_still_runs_on_conflict(self, tmp_path: Path) -> None:
        """Artwork step always runs even when ID3 tags are skipped due to conflict."""
        config = _make_conflict_config(tmp_path)
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
        # MBIDs come from the enriched tracks even on conflict — artwork fetch
        # needs a valid MBID; only ID3 tag writes are skipped.
        call_kwargs = mock_art.call_args
        assert call_kwargs.kwargs["release_mbid"] == "rel-123"
        assert call_kwargs.kwargs["release_group_mbid"] == "rg-123"

    def test_writes_id3_when_no_conflict(self, tmp_path: Path) -> None:
        """When tags agree with MB, ID3 is written normally (no conflict)."""
        config = _make_conflict_config(tmp_path)
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        album_dir = config.paths.watch_folder / "mb-album"
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

    def test_cover_file_mode_local_art_returns_bytes_does_not_embed(
        self, tmp_path: Path
    ) -> None:
        """With save_format='cover-file', local art is returned, not embedded."""
        mp3 = tmp_path / "01.mp3"
        mp3.write_bytes(b"\xff\xfb" * 64)
        id3.ID3().save(str(mp3))
        image_data = b"\xff\xd8\xff" + b"\x00" * 100

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
            result = _fetch_and_embed_via_extension(
                ctx=self._ctx(),
                audio_files=[mp3],
                release_mbid="rel-1",
                release_group_mbid="rg-1",
                directory=tmp_path,
                min_dimension=500,
                max_bytes=5_000_000,
                save_format="cover-file",
            )

        assert result == (image_data, "image/jpeg")
        mock_embed.assert_not_called()
        mock_fetch.assert_not_called()

    def test_cover_file_mode_caa_returns_bytes_does_not_embed(
        self, tmp_path: Path
    ) -> None:
        """With save_format='cover-file', CAA result is returned, not embedded."""
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
            result = _fetch_and_embed_via_extension(
                ctx=self._ctx(),
                audio_files=[mp3],
                release_mbid="rel-1",
                release_group_mbid="rg-1",
                directory=tmp_path,
                min_dimension=500,
                max_bytes=5_000_000,
                save_format="cover-file",
            )

        assert result == (image_data, "image/jpeg")
        mock_embed.assert_not_called()

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


# ---------------------------------------------------------------------------
# KAMP-523: known-Bandcamp ingest branch (provenance)
# ---------------------------------------------------------------------------


def _make_bandcamp_mp3(
    path: Path, artist: str, album: str, title: str, track: int
) -> None:
    path.write_bytes(b"\xff\xfb" * 64)
    tags = id3.ID3()
    tags["TPE1"] = id3.TPE1(encoding=3, text=artist)
    tags["TPE2"] = id3.TPE2(encoding=3, text=artist)
    tags["TALB"] = id3.TALB(encoding=3, text=album)
    tags["TIT2"] = id3.TIT2(encoding=3, text=title)
    tags["TRCK"] = id3.TRCK(encoding=3, text=str(track))
    tags.save(str(path))


class TestKnownBandcampBranch:
    def _seed_db(self, db_path: Path) -> None:
        from kamp_core.library import LibraryIndex, Track

        idx = LibraryIndex(db_path)
        idx.upsert_collection_item(
            "S1", mode="local", band_name="Artist X ", item_title="Album Y"
        )
        # Streaming rows for the album (so display overrides have somewhere to live).
        streaming = [
            Track(
                file_path=Path(f"bandcamp://S1/{n}"),
                title=f"Track {n}",
                artist="Artist X ",
                album_artist="Artist X ",
                album="Album Y",
                release_date="",
                track_number=n,
                disc_number=1,
                ext="",
                embedded_art=False,
                mb_release_id="",
                mb_recording_id="",
                source="bandcamp",
            )
            for n in (1, 2)
        ]
        idx.upsert_many(streaming)
        # User edits on the streaming version.
        idx.update_album_display(
            "Artist X ", "Album Y", "Display Album", "Display Artist"
        )
        t1 = idx.get_track_by_path("bandcamp://S1/1")
        assert t1 is not None
        idx.update_track_display_title(t1.id, "Renamed Track 1")
        idx.update_track_display_artist(t1.id, "Renamed Artist 1")
        idx.close()

    def test_writes_known_metadata_and_provenance_without_musicbrainz(
        self, tmp_path: Path, config: Config
    ) -> None:
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        db = tmp_path / "lib.db"
        self._seed_db(db)

        extracted = config.paths.watch_folder / "album"
        extracted.mkdir()
        f1 = extracted / "01.mp3"
        f2 = extracted / "02.mp3"
        _make_bandcamp_mp3(f1, "Artist X", "Album Y", "Track 1", 1)
        _make_bandcamp_mp3(f2, "Artist X", "Album Y", "Track 2", 2)

        from kamp_core.library import LibraryIndex

        idx = LibraryIndex(db)
        idx.add_pending_ingest(str(extracted), "S1", "T1")
        idx.close()

        # MusicBrainz must never be required — make any lookup blow up and prove
        # the download still ingests (best-effort MBID, non-fatal).
        with patch("kamp_daemon.pipeline_impl.KampMusicBrainzTagger") as mock_tagger:
            mock_tagger.return_value.tag_release.side_effect = Exception("no network")
            run(extracted, config, index_path=db)

        moved = list(config.paths.library.rglob("*.mp3"))
        assert len(moved) == 2
        # Track 1 carries the user's edits + the provenance stamp; MB names never
        # applied.
        track1 = next(p for p in moved if p.name.startswith("01"))
        tags = id3.ID3(str(track1))
        assert str(tags["TPE2"]) == "Display Artist"
        assert str(tags["TALB"]) == "Display Album"
        assert str(tags["TIT2"]) == "Renamed Track 1"
        # KAMP-582: per-track artist override carries into the downloaded file.
        assert str(tags["TPE1"]) == "Renamed Artist 1"
        assert str(tags["TXXX:KAMP_SALE_ITEM_ID"]) == "S1"

        # Provenance handoff consumed.
        idx = LibraryIndex(db)
        assert idx.pending_ingest_for_path(str(extracted)) is None
        idx.close()

    def test_records_mbid_and_keeps_bandcamp_names_without_overrides(
        self, tmp_path: Path, config: Config
    ) -> None:
        # No user edits synced (empty overrides): keep the file's Bandcamp names,
        # but still record the release MBID a successful lookup returns.
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        db = tmp_path / "lib.db"

        from kamp_core.library import LibraryIndex

        idx = LibraryIndex(db)
        idx.upsert_collection_item(
            "S7", mode="local", band_name="Bandcamp Artist", item_title="Bandcamp Album"
        )
        extracted = config.paths.watch_folder / "album"
        extracted.mkdir()
        f1 = extracted / "01.mp3"
        _make_bandcamp_mp3(f1, "Bandcamp Artist", "Bandcamp Album", "Real Title", 1)
        idx.add_pending_ingest(str(extracted), "S7", "T7")
        idx.close()

        enriched = [
            TrackMetadata(
                title="MB Title",  # deliberately different — must NOT be applied
                artist="MB Artist",
                album="MB Album",
                album_artist="MB Artist",
                release_date="1999",
                track_number=1,
                mbid="rec-1",
                release_mbid="rel-77",
                release_group_mbid="rg-77",
            )
        ]
        with patch("kamp_daemon.pipeline_impl.KampMusicBrainzTagger") as mock_tagger:
            mock_tagger.return_value.tag_release.return_value = enriched
            run(extracted, config, index_path=db)

        moved = list(config.paths.library.rglob("*.mp3"))
        assert len(moved) == 1
        tags = id3.ID3(str(moved[0]))
        # Bandcamp names kept; MB names ignored.
        assert str(tags["TPE2"]) == "Bandcamp Artist"
        assert str(tags["TALB"]) == "Bandcamp Album"
        assert str(tags["TIT2"]) == "Real Title"
        # MBID recorded, provenance stamped.
        assert str(tags["TXXX:MusicBrainz Album Id"]) == "rel-77"
        assert str(tags["TXXX:KAMP_SALE_ITEM_ID"]) == "S7"

    def test_nameless_single_gets_album_name_and_provenance(
        self, tmp_path: Path, config: Config
    ) -> None:
        # A Bandcamp single arrives with no album tag; the pipeline must stamp the
        # known album name (= the item title) so it doesn't ingest album-less and
        # fork off a second card. Ohm Foam "Gush" regression.
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        db = tmp_path / "lib.db"

        from kamp_core.library import LibraryIndex, Track

        idx = LibraryIndex(db)
        idx.upsert_collection_item(
            "SG", mode="local", band_name="Ohm Foam", item_title="Gush"
        )
        idx.upsert_many(
            [
                Track(
                    file_path=Path("bandcamp://SG/1"),
                    title="Gush",
                    artist="Ohm Foam",
                    album_artist="Ohm Foam",
                    album="Gush",
                    release_date="",
                    track_number=1,
                    disc_number=1,
                    ext="",
                    embedded_art=False,
                    mb_release_id="",
                    mb_recording_id="",
                    source="bandcamp",
                )
            ]
        )
        extracted = config.paths.watch_folder / "single"
        extracted.mkdir()
        gush = extracted / "Gush.mp3"
        _make_bandcamp_mp3(gush, "Ohm Foam", "", "Gush", 1)  # no album tag
        idx.add_pending_ingest(str(extracted), "SG", "TG")
        idx.close()

        with patch("kamp_daemon.pipeline_impl.KampMusicBrainzTagger") as mock_tagger:
            mock_tagger.return_value.tag_release.side_effect = Exception("no network")
            run(extracted, config, index_path=db)

        moved = list(config.paths.library.rglob("*.mp3"))
        assert len(moved) == 1
        tags = id3.ID3(str(moved[0]))
        assert str(tags["TALB"]) == "Gush"  # album name filled from known metadata
        assert str(tags["TPE2"]) == "Ohm Foam"
        assert str(tags["TXXX:KAMP_SALE_ITEM_ID"]) == "SG"
        # Aligned to the streaming single's track number (1) so favorite /
        # play-count inheritance matches on (album_id, track_number, disc_number).
        # Written "1/1" (track/total); the scanner parses the leading number.
        assert str(tags["TRCK"]) == "1/1"
        assert _read_mp3_tags(moved[0]).track_number == 1

    def test_pending_ingest_cleared_on_quarantine(
        self, tmp_path: Path, config: Config
    ) -> None:
        config.paths.watch_folder.mkdir(parents=True)
        config.paths.library.mkdir(parents=True)
        db = tmp_path / "lib.db"
        self._seed_db(db)

        # A directory with no audio files quarantines during extraction.
        empty = config.paths.watch_folder / "album"
        empty.mkdir()
        (empty / "notes.txt").write_text("no audio here")

        from kamp_core.library import LibraryIndex

        idx = LibraryIndex(db)
        idx.add_pending_ingest(str(empty), "S1", "T1")
        idx.close()

        run(empty, config, index_path=db)

        idx = LibraryIndex(db)
        assert idx.pending_ingest_for_path(str(empty)) is None
        idx.close()
