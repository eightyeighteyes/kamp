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
    fallback_mpv = tmp_path / "fallback_mpv"
    fallback_mpv.touch()
    # Patch both fallback lists so the assertion holds on any platform — the
    # resolver picks one list based on sys.platform.
    with (
        patch.dict("os.environ", {"KAMP_MPV_BIN": nonexistent}),
        patch("kamp_daemon.__main__._HOMEBREW_MPV_PATHS", [str(fallback_mpv)]),
        patch("kamp_daemon.__main__._WIN_MPV_PATHS", [str(fallback_mpv)]),
    ):
        assert _resolve_mpv_binary() == str(fallback_mpv)


def test_env_var_not_set_falls_back_to_platform_paths(tmp_path):
    """Without KAMP_MPV_BIN, the platform's known install paths are checked."""
    fallback_mpv = tmp_path / "mpv"
    fallback_mpv.touch()
    without_kamp_mpv_bin = {
        k: v for k, v in __import__("os").environ.items() if k != "KAMP_MPV_BIN"
    }
    with (
        patch.dict("os.environ", without_kamp_mpv_bin, clear=True),
        patch("kamp_daemon.__main__._HOMEBREW_MPV_PATHS", [str(fallback_mpv)]),
        patch("kamp_daemon.__main__._WIN_MPV_PATHS", [str(fallback_mpv)]),
    ):
        assert _resolve_mpv_binary() == str(fallback_mpv)


def test_windows_uses_win_paths_not_homebrew(tmp_path):
    """On win32 the resolver must check _WIN_MPV_PATHS, not _HOMEBREW_MPV_PATHS.

    A daemon spawned by Electron with a stale PATH cannot rely on shutil.which,
    so the fallback list must include Scoop / Chocolatey / Program Files
    locations to find a user-installed mpv.
    """
    win_mpv = tmp_path / "win_mpv.exe"
    win_mpv.touch()
    homebrew_mpv = tmp_path / "homebrew_mpv"
    homebrew_mpv.touch()
    without_kamp_mpv_bin = {
        k: v for k, v in __import__("os").environ.items() if k != "KAMP_MPV_BIN"
    }
    with (
        patch.dict("os.environ", without_kamp_mpv_bin, clear=True),
        patch("kamp_daemon.__main__.sys.platform", "win32"),
        patch("kamp_daemon.__main__._WIN_MPV_PATHS", [str(win_mpv)]),
        patch("kamp_daemon.__main__._HOMEBREW_MPV_PATHS", [str(homebrew_mpv)]),
    ):
        assert _resolve_mpv_binary() == str(win_mpv)
