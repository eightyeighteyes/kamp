"""Tests for _resolve_mpv_binary() in kamp_daemon.__main__."""

from unittest.mock import patch

from kamp_daemon.__main__ import _resolve_mpv_binary


def test_env_var_takes_priority(tmp_path):
    """KAMP_MPV_BIN env var is used when set and the path exists."""
    bundled = tmp_path / "mpv"
    bundled.touch()
    with patch.dict("os.environ", {"KAMP_MPV_BIN": str(bundled)}):
        assert _resolve_mpv_binary() == str(bundled)


def test_env_var_ignored_when_path_missing(tmp_path):
    """KAMP_MPV_BIN is ignored when the file does not exist; falls through to next check."""
    nonexistent = str(tmp_path / "mpv_does_not_exist")
    homebrew_mpv = tmp_path / "homebrew_mpv"
    homebrew_mpv.touch()
    with (
        patch.dict("os.environ", {"KAMP_MPV_BIN": nonexistent}),
        patch("kamp_daemon.__main__._HOMEBREW_MPV_PATHS", [str(homebrew_mpv)]),
    ):
        assert _resolve_mpv_binary() == str(homebrew_mpv)


def test_env_var_not_set_falls_back_to_homebrew(tmp_path):
    """Without KAMP_MPV_BIN, Homebrew paths are checked."""
    homebrew_mpv = tmp_path / "mpv"
    homebrew_mpv.touch()
    without_kamp_mpv_bin = {
        k: v for k, v in __import__("os").environ.items() if k != "KAMP_MPV_BIN"
    }
    with (
        patch.dict("os.environ", without_kamp_mpv_bin, clear=True),
        patch("kamp_daemon.__main__._HOMEBREW_MPV_PATHS", [str(homebrew_mpv)]),
    ):
        assert _resolve_mpv_binary() == str(homebrew_mpv)
