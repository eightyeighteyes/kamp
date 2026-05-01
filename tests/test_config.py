"""Tests for kamp_daemon.config (DB-backed configuration)."""

from pathlib import Path

import keyring.errors
import pytest
from pytest_mock import MockerFixture

from kamp_core.library import LibraryIndex
from kamp_daemon.config import (
    _CONFIG_DEFAULTS,
    Config,
    LastfmConfig,
    BandcampConfig,
    config_set,
    config_show,
)


@pytest.fixture()
def db(tmp_path: Path, mocker: MockerFixture) -> LibraryIndex:
    """Return a fresh LibraryIndex backed by a temp DB, with keyring mocked out."""
    mocker.patch("kamp_core.library._mac_kc", None)
    err = keyring.errors.NoKeyringError()
    mocker.patch("kamp_core.library.keyring.get_password", side_effect=err)
    mocker.patch("kamp_core.library.keyring.set_password", side_effect=err)
    mocker.patch("kamp_core.library.keyring.delete_password", side_effect=err)
    index = LibraryIndex(tmp_path / "library.db")
    yield index
    index.close()


# ---------------------------------------------------------------------------
# Config.write_defaults / Config.load
# ---------------------------------------------------------------------------


class TestWriteDefaults:
    def test_writes_all_11_keys(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        settings = db.get_all_settings()
        assert len(settings) == len(_CONFIG_DEFAULTS)
        for key in _CONFIG_DEFAULTS:
            assert key in settings

    def test_does_not_overwrite_existing_keys(self, db: LibraryIndex) -> None:
        db.set_setting("paths.watch_folder", "/custom/staging")
        Config.write_defaults(db)
        assert db.get_setting("paths.watch_folder") == "/custom/staging"


class TestLoad:
    def test_load_with_defaults(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config = Config.load(db)
        # Paths have no default — they are None until the user sets them via onboarding.
        assert config.paths.watch_folder is None
        assert config.paths.library is None
        assert config.musicbrainz.trust_musicbrainz_when_tags_conflict is False
        assert config.artwork.min_dimension == 1000
        assert config.artwork.max_bytes == 1_000_000
        assert config.bandcamp is not None
        assert config.bandcamp.format == "mp3-v0"
        assert config.bandcamp.poll_interval_minutes == 0
        assert config.lastfm is None
        assert config.ui.active_view == "library"
        assert config.ui.sort_order == "album_artist"
        assert config.ui.queue_panel_open == 0

    def test_load_seeds_defaults_on_fresh_install(self, db: LibraryIndex) -> None:
        config = Config.load(db)
        assert config.bandcamp is not None
        assert config.bandcamp.poll_interval_minutes == 0

    def test_load_respects_custom_values(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        db.set_setting("paths.watch_folder", "/my/staging")
        db.set_setting("artwork.min_dimension", "500")
        config = Config.load(db)
        assert str(config.paths.watch_folder) == "/my/staging"
        assert config.artwork.min_dimension == 500

    def test_load_backfills_new_keys(self, db: LibraryIndex) -> None:
        # Simulate a DB from an older release missing a key.
        db.set_setting("paths.watch_folder", "/staging")
        db.set_setting("paths.library", "/library")
        # Missing all other keys — load() should back-fill defaults.
        config = Config.load(db)
        assert config.artwork.min_dimension == 1000

    def test_load_lastfm_when_session_present(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        db.set_session("lastfm", {"session_key": "mysecret", "username": "myuser"})
        config = Config.load(db)
        assert config.lastfm is not None
        assert isinstance(config.lastfm, LastfmConfig)
        assert config.lastfm.username == "myuser"
        assert config.lastfm.session_key == "mysecret"

    def test_load_lastfm_none_when_no_session(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config = Config.load(db)
        assert config.lastfm is None

    def test_load_migrates_lastfm_from_settings_table(self, db: LibraryIndex) -> None:
        """One-time migration: session_key in settings → moved to session store."""
        Config.write_defaults(db)
        db.set_setting("lastfm.username", "olduser")
        db.set_setting("lastfm.session_key", "oldkey")
        config = Config.load(db)
        assert config.lastfm is not None
        assert config.lastfm.session_key == "oldkey"
        assert config.lastfm.username == "olduser"
        # Credentials must be cleared from the settings table after migration.
        assert db.get_setting("lastfm.session_key") == ""
        assert db.get_setting("lastfm.username") == ""
        # Session store must hold the migrated data.
        session = db.get_session("lastfm")
        assert session is not None
        assert session["session_key"] == "oldkey"


# ---------------------------------------------------------------------------
# first_run_setup / bandcamp_setup
# ---------------------------------------------------------------------------


class TestFirstRunSetup:
    def test_returns_config_with_user_values(
        self, db: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inputs = iter(["~/staging", "~/lib"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        config = Config.first_run_setup(db)
        assert "staging" in str(config.paths.watch_folder)
        assert "lib" in str(config.paths.library)

    def test_blank_input_uses_default(
        self, db: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        config = Config.first_run_setup(db)
        assert config.paths.watch_folder == Path("~/Music/staging").expanduser()
        assert config.paths.library == Path("~/Music").expanduser()

    def test_writes_all_defaults_to_db(
        self, db: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "")
        Config.first_run_setup(db)
        settings = db.get_all_settings()
        # first_run_setup writes 2 path keys explicitly + all non-path defaults.
        assert len(settings) == len(_CONFIG_DEFAULTS) + 2

    def test_setup_then_load_round_trips(
        self, db: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inputs = iter(["~/staging2", "~/lib2"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        Config.first_run_setup(db)
        config = Config.load(db)
        assert "staging2" in str(config.paths.watch_folder)
        assert "lib2" in str(config.paths.library)


class TestBandcampSetup:
    def test_writes_format_and_poll_interval(
        self, db: LibraryIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Config.write_defaults(db)
        inputs = iter(["mp3-320", "60"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        config = Config.bandcamp_setup(db)
        assert config.bandcamp is not None
        assert config.bandcamp.format == "mp3-320"
        assert config.bandcamp.poll_interval_minutes == 60


# ---------------------------------------------------------------------------
# config_show
# ---------------------------------------------------------------------------


class TestConfigShow:
    def test_includes_all_section_headers(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        output = config_show(db)
        assert "[paths]" in output
        assert "[musicbrainz]" in output
        assert "[artwork]" in output
        assert "[library]" in output
        assert "[bandcamp]" in output
        assert "[ui]" in output
        # Last.fm credentials are in the session store, not config — no [lastfm] section.
        assert "[lastfm]" not in output

    def test_includes_key_value_pairs(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        output = config_show(db)
        assert "watch_folder" in output
        assert "min_dimension" in output

    def test_shows_updated_value_after_set(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "artwork.min_dimension", "500")
        output = config_show(db)
        assert "500" in output


# ---------------------------------------------------------------------------
# config_set
# ---------------------------------------------------------------------------


class TestConfigSet:
    def test_set_string_key(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "paths.watch_folder", "/new/staging")
        assert db.get_setting("paths.watch_folder") == "/new/staging"

    def test_set_int_key(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "artwork.min_dimension", "500")
        assert db.get_setting("artwork.min_dimension") == "500"

    def test_set_bool_key(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "musicbrainz.trust-musicbrainz-when-tags-conflict", "false")
        assert (
            db.get_setting("musicbrainz.trust-musicbrainz-when-tags-conflict")
            == "false"
        )

    def test_round_trip_load_after_set(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "paths.watch_folder", "~/round/trip")
        config = Config.load(db)
        assert "round/trip" in str(config.paths.watch_folder)

    def test_unknown_key_raises_key_error(self, db: LibraryIndex) -> None:
        with pytest.raises(KeyError, match="Unknown config key"):
            config_set(db, "paths.nonexistent", "x")

    def test_wrong_type_raises_value_error(self, db: LibraryIndex) -> None:
        with pytest.raises(ValueError, match="requires an integer"):
            config_set(db, "artwork.min_dimension", "not-an-int")

    def test_bool_wrong_value_raises_value_error(self, db: LibraryIndex) -> None:
        with pytest.raises(ValueError, match="requires true or false"):
            config_set(db, "musicbrainz.trust-musicbrainz-when-tags-conflict", "yes")

    def test_bandcamp_format_valid_value_succeeds(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "bandcamp.format", "flac")
        assert db.get_setting("bandcamp.format") == "flac"

    def test_bandcamp_format_invalid_value_raises(self, db: LibraryIndex) -> None:
        with pytest.raises(ValueError, match="Invalid value 'm5q'"):
            config_set(db, "bandcamp.format", "m5q")

    def test_bandcamp_format_error_lists_valid_choices(self, db: LibraryIndex) -> None:
        with pytest.raises(ValueError, match="mp3-v0"):
            config_set(db, "bandcamp.format", "bad")

    def test_ui_active_view_valid(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "ui.active_view", "now-playing")
        config = Config.load(db)
        assert config.ui.active_view == "now-playing"

    def test_ui_sort_order_valid(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "ui.sort_order", "last_played")
        config = Config.load(db)
        assert config.ui.sort_order == "last_played"

    def test_deprecated_username_raises(self, db: LibraryIndex) -> None:
        with pytest.raises(KeyError, match="deprecated"):
            config_set(db, "bandcamp.username", "foo")

    def test_deprecated_cookie_file_raises(self, db: LibraryIndex) -> None:
        with pytest.raises(KeyError, match="deprecated"):
            config_set(db, "bandcamp.cookie_file", "/tmp/cookies.txt")

    def test_set_lastfm_username_raises_deprecated(self, db: LibraryIndex) -> None:
        with pytest.raises(KeyError, match="deprecated"):
            config_set(db, "lastfm.username", "newuser")

    def test_set_lastfm_session_key_raises_deprecated(self, db: LibraryIndex) -> None:
        with pytest.raises(KeyError, match="deprecated"):
            config_set(db, "lastfm.session_key", "newkey")

    def test_path_key_relative_raises(self, db: LibraryIndex) -> None:
        with pytest.raises(ValueError, match="requires an absolute path"):
            config_set(db, "paths.watch_folder", "relative/path")

    def test_path_key_dotdot_raises(self, db: LibraryIndex) -> None:
        with pytest.raises(ValueError, match="requires an absolute path"):
            config_set(db, "paths.watch_folder", "../escape")

    @pytest.mark.parametrize(
        "forbidden",
        [
            "/",
            "/etc",
            "/private/etc",
            "/System",
            "/usr",
            "/bin",
            "/Library",
            "/Applications",
        ],
    )
    def test_path_key_forbidden_root_raises(
        self, db: LibraryIndex, forbidden: str
    ) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            config_set(db, "paths.watch_folder", forbidden)

    def test_path_key_tilde_accepted(self, db: LibraryIndex) -> None:
        Config.write_defaults(db)
        config_set(db, "paths.watch_folder", "~/Music/staging")
        assert db.get_setting("paths.watch_folder") == "~/Music/staging"
