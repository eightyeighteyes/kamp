"""Tests for kamp_daemon.config."""

from pathlib import Path

import pytest

from kamp_daemon.config import (
    DEFAULT_CONFIG_CONTENT,
    Config,
    LastfmConfig,
    config_set,
    config_show,
)


class TestFirstRunSetup:
    def test_creates_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """first_run_setup writes a TOML file at the given path."""
        inputs = iter(["~/staging", "~/music"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        path = tmp_path / "config.toml"
        Config.first_run_setup(path)
        assert path.exists()
        assert "watch_folder" in path.read_text()

    def test_returns_config_with_user_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned Config reflects the prompted values."""
        inputs = iter(["~/staging", "~/lib"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        config = Config.first_run_setup(tmp_path / "config.toml")
        assert "staging" in str(config.paths.watch_folder)  # user typed ~/staging
        assert "lib" in str(config.paths.library)

    def test_blank_input_uses_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pressing Enter at a prompt accepts the shown default."""
        monkeypatch.setattr("builtins.input", lambda _: "")
        config = Config.first_run_setup(tmp_path / "config.toml")
        assert config.paths.watch_folder == Path("~/Music/staging").expanduser()
        assert config.paths.library == Path("~/Music").expanduser()

    def test_written_toml_is_valid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The written TOML file can be loaded back by Config.load()."""
        inputs = iter(["~/staging", "~/music"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        path = tmp_path / "config.toml"
        Config.first_run_setup(path)
        # Round-trip: load should succeed without error
        loaded = Config.load(path)
        assert "staging" in str(loaded.paths.watch_folder)

    def test_creates_parent_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """first_run_setup creates intermediate directories if needed."""
        monkeypatch.setattr("builtins.input", lambda _: "")
        path = tmp_path / "nested" / "dir" / "config.toml"
        Config.first_run_setup(path)
        assert path.exists()


def _base_config(tmp_path: Path) -> Path:
    """Write a minimal valid config (without [bandcamp]) and return its path."""
    path = tmp_path / "config.toml"
    path.write_text(
        DEFAULT_CONFIG_CONTENT.replace('"~/Music/staging"', '"/staging"').replace(
            '"~/Music"', '"/library"'
        )
    )
    return path


class TestBandcampSetup:
    def test_appends_bandcamp_section_to_existing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """bandcamp_setup appends [bandcamp] without clobbering existing content."""
        path = _base_config(tmp_path)
        inputs = iter(["bcuser", "", "mp3-320", "60"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        config = Config.bandcamp_setup(path)

        assert config.bandcamp is not None
        assert config.bandcamp.username == "bcuser"
        assert config.bandcamp.format == "mp3-320"
        assert config.bandcamp.poll_interval_minutes == 60
        assert config.bandcamp.cookie_file is None
        assert "[paths]" in path.read_text()

    def test_returns_config_with_cookie_file_when_provided(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cookie_file is written and parsed when the user provides a path."""
        path = _base_config(tmp_path)
        inputs = iter(["bcuser", "/tmp/cookie", "mp3-v0", "0"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        config = Config.bandcamp_setup(path)

        assert config.bandcamp is not None
        assert config.bandcamp.cookie_file == Path("/tmp/cookie")

    def test_blank_username_reprompts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Entering a blank username re-prompts rather than saving an empty string."""
        path = _base_config(tmp_path)
        # first two responses are blank (username reprompt), then a valid name
        inputs = iter(["", "", "realuser", "", "mp3-v0", "0"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        config = Config.bandcamp_setup(path)

        assert config.bandcamp is not None
        assert config.bandcamp.username == "realuser"


# ---------------------------------------------------------------------------
# config_show / config_set tests
# ---------------------------------------------------------------------------

_TOML_WITH_BANDCAMP = """\
[paths]
watch_folder = "~/Music/staging"
library = "~/Music"

[musicbrainz]
contact = "user@example.com"  # Update with your contact email

[artwork]
min_dimension = 1000   # minimum width and height in pixels
max_bytes = 1000000

[library]
path_template = "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"

[bandcamp]
username = "myuser"
format = "mp3-v0"
poll_interval_minutes = 60
"""


class TestConfigShow:
    def test_show_includes_section_headers(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_BANDCAMP)
        output = config_show(path)
        assert "[paths]" in output
        assert "[musicbrainz]" in output
        assert "[artwork]" in output

    def test_show_includes_key_value_pairs(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_BANDCAMP)
        output = config_show(path)
        assert "staging" in output
        assert "user@example.com" in output

    def test_show_includes_bandcamp_when_present(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_BANDCAMP)
        output = config_show(path)
        assert "[bandcamp]" in output
        assert "myuser" in output

    def test_show_omits_bandcamp_when_absent(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        output = config_show(path)
        assert "bandcamp" not in output


class TestConfigSet:
    def test_set_string_key_updates_value(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config_set(path, "paths.watch_folder", "~/new/staging")
        assert "~/new/staging" in path.read_text()

    def test_set_int_key_updates_value(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config_set(path, "artwork.min_dimension", "500")
        text = path.read_text()
        assert "min_dimension = 500" in text

    def test_set_preserves_other_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config_set(path, "paths.watch_folder", "~/new/staging")
        text = path.read_text()
        assert "library" in text
        assert "path_template" in text

    def test_set_preserves_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config_set(path, "paths.watch_folder", "~/new/staging")
        # Comments on other lines must survive
        assert "# Available variables" in path.read_text()

    def test_set_unknown_key_raises_key_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        with pytest.raises(KeyError, match="Unknown config key"):
            config_set(path, "paths.nonexistent", "x")

    def test_set_wrong_type_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        with pytest.raises(ValueError, match="requires an integer"):
            config_set(path, "artwork.min_dimension", "not-an-int")

    def test_set_bandcamp_key_on_missing_section_raises(self, tmp_path: Path) -> None:
        """config_set raises KeyError when [bandcamp] section is not in the file."""
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        with pytest.raises(KeyError, match="bandcamp"):
            config_set(path, "bandcamp.username", "foo")

    def test_set_ui_key_on_missing_section_appends_section(
        self, tmp_path: Path
    ) -> None:
        """config_set appends [ui] when absent instead of raising (existing configs)."""
        path = tmp_path / "config.toml"
        # Simulate a config file that predates the [ui] section.
        path.write_text(DEFAULT_CONFIG_CONTENT.split("\n[ui]")[0])
        config_set(path, "ui.active_view", "now-playing")
        loaded = Config.load(path)
        assert loaded.ui.active_view == "now-playing"

    def test_round_trip_load_after_set(self, tmp_path: Path) -> None:
        """Config.load() succeeds and reflects the new value after config_set."""
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config_set(path, "paths.watch_folder", "~/round/trip")
        loaded = Config.load(path)
        assert "round/trip" in str(loaded.paths.watch_folder)

    def test_set_path_value_round_trips(self, tmp_path: Path) -> None:
        """A path set via config_set is written as a string and loaded back correctly."""
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config_set(path, "paths.watch_folder", "~/new/staging")
        loaded = Config.load(path)
        assert "new/staging" in str(loaded.paths.watch_folder)

    def test_set_field_missing_from_section_appends_key(self, tmp_path: Path) -> None:
        """config_set appends a missing key into an existing non-optional section.

        This handles configs that predate a new field (e.g. ui.sort_order added
        after the user's config was created).
        """
        path = tmp_path / "config.toml"
        path.write_text("[artwork]\nmax_bytes = 1000000\n")
        config_set(path, "artwork.min_dimension", "500")
        assert "min_dimension = 500" in path.read_text()

    def test_set_ui_sort_order_on_config_missing_the_key(self, tmp_path: Path) -> None:
        """config_set appends ui.sort_order when [ui] exists but lacks the key."""
        path = tmp_path / "config.toml"
        # Simulate a config that has [ui] with only active_view (pre-TASK-58)
        path.write_text('[ui]\nactive_view = "library"\n')
        config_set(path, "ui.sort_order", "last_played")
        loaded_text = path.read_text()
        assert 'sort_order = "last_played"' in loaded_text
        # active_view must be untouched
        assert 'active_view = "library"' in loaded_text

    def test_set_does_not_clobber_same_fieldname_in_other_section(
        self, tmp_path: Path
    ) -> None:
        """Setting a key only modifies the correct section."""
        # Verify that setting paths.library doesn't touch library.path_template.
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config_set(path, "paths.library", "~/NewLib")
        text = path.read_text()
        assert "path_template" in text  # library section untouched
        assert "~/NewLib" in text

    def test_set_bandcamp_format_valid_value_succeeds(self, tmp_path: Path) -> None:
        """config_set accepts a valid bandcamp.format value."""
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_BANDCAMP)
        config_set(path, "bandcamp.format", "flac")
        assert 'format = "flac"' in path.read_text()

    def test_set_bandcamp_format_invalid_value_raises(self, tmp_path: Path) -> None:
        """config_set rejects an unrecognised bandcamp.format value."""
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_BANDCAMP)
        with pytest.raises(ValueError, match="Invalid value 'm5q'"):
            config_set(path, "bandcamp.format", "m5q")

    def test_set_bandcamp_format_error_lists_valid_choices(
        self, tmp_path: Path
    ) -> None:
        """The ValueError message includes the supported formats."""
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_BANDCAMP)
        with pytest.raises(ValueError, match="mp3-v0"):
            config_set(path, "bandcamp.format", "bad")

    def test_set_lastfm_key_on_missing_section_raises(self, tmp_path: Path) -> None:
        """config_set raises when [lastfm] section is absent (optional section)."""
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        with pytest.raises(KeyError):
            config_set(path, "lastfm.session_key", "abc123")


_TOML_WITH_LASTFM = """\
[paths]
watch_folder = "~/Music/staging"
library = "~/Music"

[musicbrainz]
contact = "user@example.com"

[artwork]
min_dimension = 1000
max_bytes = 1000000

[library]
path_template = "{album_artist}/{year} - {album}/{track:02d} - {title}.{ext}"

[lastfm]
username = "myuser"
session_key = "abc123sessionkey"
"""


class TestLastfmConfig:
    def test_load_parses_lastfm_section(self, tmp_path: Path) -> None:
        """Config.load() populates lastfm when the [lastfm] section is present."""
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_LASTFM)
        config = Config.load(path)
        assert config.lastfm is not None
        assert isinstance(config.lastfm, LastfmConfig)
        assert config.lastfm.username == "myuser"
        assert config.lastfm.session_key == "abc123sessionkey"

    def test_load_lastfm_none_when_section_absent(self, tmp_path: Path) -> None:
        """Config.load() returns lastfm=None when [lastfm] section is missing."""
        path = tmp_path / "config.toml"
        path.write_text(DEFAULT_CONFIG_CONTENT)
        config = Config.load(path)
        assert config.lastfm is None

    def test_load_lastfm_none_when_session_key_empty(self, tmp_path: Path) -> None:
        """Config.load() returns lastfm=None when session_key is empty string."""
        path = tmp_path / "config.toml"
        path.write_text(
            _TOML_WITH_LASTFM.replace(
                'session_key = "abc123sessionkey"', 'session_key = ""'
            )
        )
        config = Config.load(path)
        assert config.lastfm is None

    def test_config_set_lastfm_username(self, tmp_path: Path) -> None:
        """config_set can update lastfm.username when [lastfm] section exists."""
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_LASTFM)
        config_set(path, "lastfm.username", "newuser")
        assert 'username = "newuser"' in path.read_text()

    def test_config_set_lastfm_session_key(self, tmp_path: Path) -> None:
        """config_set can update lastfm.session_key when [lastfm] section exists."""
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_LASTFM)
        config_set(path, "lastfm.session_key", "newkey")
        assert 'session_key = "newkey"' in path.read_text()


class TestLegacyConfig:
    def test_load_succeeds_with_legacy_contact_key(self, tmp_path: Path) -> None:
        """Existing config.toml files with musicbrainz.contact load without error."""
        path = tmp_path / "config.toml"
        path.write_text(_TOML_WITH_BANDCAMP)  # _TOML_WITH_BANDCAMP has contact = "..."
        config = Config.load(path)
        assert config.musicbrainz is not None

    def test_load_accepts_legacy_staging_key(self, tmp_path: Path) -> None:
        """Existing config.toml files with paths.staging load without modification."""
        path = tmp_path / "config.toml"
        path.write_text(
            _TOML_WITH_BANDCAMP.replace(
                'watch_folder = "~/Music/staging"',
                'staging = "~/Music/staging"',
            )
        )
        config = Config.load(path)
        assert config.paths.watch_folder == Path("~/Music/staging").expanduser()

    def test_load_watch_folder_takes_precedence_over_legacy_staging(
        self, tmp_path: Path
    ) -> None:
        """When both keys are present, watch_folder wins over the legacy staging key."""
        path = tmp_path / "config.toml"
        path.write_text(
            _TOML_WITH_BANDCAMP.replace(
                'watch_folder = "~/Music/staging"',
                'watch_folder = "~/Music/watch"\nstaging = "~/Music/staging"',
            )
        )
        config = Config.load(path)
        assert config.paths.watch_folder == Path("~/Music/watch").expanduser()
