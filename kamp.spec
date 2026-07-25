# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the kamp server bundle.
#
# Bundles kamp_core + kamp_daemon (minus Bandcamp/Playwright) into a onedir
# executable placed at kamp_ui/resources/kamp/kamp (or kamp.exe on Windows).
# electron-builder then copies that directory into Contents/Resources/kamp/
# (mac) or resources/kamp/ (win) via extraResources.
#
# Build:
#   poetry run pyinstaller \
#     --distpath kamp_ui/resources \
#     --workpath /tmp/pyinstaller-work \
#     --clean -y kamp.spec

import sys

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# ---------------------------------------------------------------------------
# Hidden imports — uvicorn and starlette/fastapi use string-based dynamic
# imports that static analysis cannot follow.
# ---------------------------------------------------------------------------
hidden_imports = [
    # uvicorn internal string-dispatched modules
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.uvloop",
    "uvicorn.lifespan",
    "uvicorn.lifespan.off",
    "uvicorn.lifespan.on",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    # starlette / anyio async runtime
    "starlette.routing",
    "starlette.responses",
    "anyio",
    "anyio._backends._asyncio",
    # explicit submodule collection for kamp packages
    *collect_submodules("kamp_core"),
    *collect_submodules("kamp_daemon"),
]

# Platform-specific watchdog backend + macOS tray (rumps).
# rumps is darwin-gated in pyproject.toml, so it isn't installed on Windows;
# listing it unconditionally would emit a noisy PyInstaller warning there.
if sys.platform == "darwin":
    hidden_imports += [
        "watchdog.observers.fsevents",
        "rumps",
    ]
elif sys.platform == "win32":
    hidden_imports += [
        "watchdog.observers.read_directory_changes",
    ]

# ---------------------------------------------------------------------------
# Excludes — dev/test tooling and unused GUI toolkits only.
# kamp_daemon.syncer and kamp_daemon.ext.builtin.bandcamp are now included
# because Bandcamp sync uses plain requests (no Playwright/Chromium required).
# ---------------------------------------------------------------------------
excludes = [
    # dev / test tooling — never needed at runtime
    "pytest",
    "black",
    "mypy",
    # GUI toolkits not used by kamp
    "tkinter",
    "PyQt5",
    "PyQt6",
    "wx",
]

# ---------------------------------------------------------------------------
# Data files — package resources that aren't pure Python
# ---------------------------------------------------------------------------
datas = [
    *collect_data_files("uvicorn"),
    *collect_data_files("fastapi"),
    *collect_data_files("starlette"),
    *collect_data_files("certifi"),  # TLS certs for requests / MusicBrainz
    # pyproject.toml is used by _get_version() as the canonical version source;
    # include it so the frozen app reports the correct version string.
    ("pyproject.toml", "."),
    # kamp_fade.lua drives click-free pause/stop/resume fades inside mpv's event
    # loop; playback.py loads it via --script=Path(__file__).parent/"kamp_fade.lua".
    # collect_submodules only gathers .py modules, so this non-Python sibling must
    # be staged explicitly into kamp_core/ — without it the frozen app passes mpv
    # a nonexistent --script path, the script silently never loads, and the
    # script-message-driven transport controls become no-ops (KAMP-519).
    ("kamp_core/kamp_fade.lua", "kamp_core"),
    # genres.txt is the canonical genre allowlist for KAMP-587 Last.fm enrichment;
    # genre_sources.py loads it via Path(__file__).parent/"data"/"genres.txt".
    # collect_submodules gathers only .py, so this non-Python sibling must be
    # staged explicitly, or the filter silently drops every tag in the frozen app.
    ("kamp_daemon/data/genres.txt", "kamp_daemon/data"),
]

a = Analysis(
    ["_kamp_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["hooks/rthook_ssl_certifi.py"],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kamp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False builds kamp.exe as a Windows GUI-subsystem binary so no
    # console window is allocated when Electron spawns it, and — critically —
    # so the multiprocessing.spawn workers used during sync (which re-launch
    # this same binary, see kamp_daemon/syncer.py and pipeline.py) do not
    # flash a console per worker. CPython's popen_spawn_win32 does not pass
    # CREATE_NO_WINDOW; the only reliable suppression is at the PE subsystem
    # level (KAMP-430). On macOS/Linux this flag has no effect.
    # stdio still works via inherited pipe handles set by the parent.
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="kamp",
    # distpath is supplied via CLI: --distpath kamp_ui/resources
)
