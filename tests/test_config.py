"""Tests for tune_shifter.config."""

from pathlib import Path

import pytest

from tune_shifter.config import DEFAULT_CONFIG_CONTENT, Config


class TestFirstRunSetup:
    def test_creates_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """first_run_setup writes a TOML file at the given path."""
        inputs = iter(["~/staging", "~/music", "me@example.com"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        path = tmp_path / "config.toml"
        Config.first_run_setup(path)
        assert path.exists()
        assert "me@example.com" in path.read_text()

    def test_returns_config_with_user_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returned Config reflects the prompted values."""
        inputs = iter(["~/staging", "~/lib", "test@test.com"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        config = Config.first_run_setup(tmp_path / "config.toml")
        assert config.musicbrainz.contact == "test@test.com"
        assert "staging" in str(config.paths.staging)

    def test_blank_input_uses_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pressing Enter at a prompt accepts the shown default."""
        monkeypatch.setattr("builtins.input", lambda _: "")
        config = Config.first_run_setup(tmp_path / "config.toml")
        assert config.musicbrainz.contact == "user@example.com"
        assert config.paths.staging == Path("~/Music/staging").expanduser()
        assert config.paths.library == Path("~/Music").expanduser()

    def test_written_toml_is_valid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The written TOML file can be loaded back by Config.load()."""
        inputs = iter(["~/staging", "~/music", "me@example.com"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        path = tmp_path / "config.toml"
        Config.first_run_setup(path)
        # Round-trip: load should succeed without error
        loaded = Config.load(path)
        assert loaded.musicbrainz.contact == "me@example.com"

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
        DEFAULT_CONFIG_CONTENT.replace('"~/Music/staging"', '"/staging"')
        .replace('"~/Music"', '"/library"')
        .replace('"user@example.com"', '"me@example.com"')
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
