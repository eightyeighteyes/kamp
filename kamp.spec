# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the kamp server bundle.
#
# Bundles kamp_core + kamp_daemon (minus Bandcamp/Playwright) into a onedir
# executable placed at kamp_ui/resources/kamp/kamp. electron-builder then
# copies that directory into Kamp.app/Contents/Resources/kamp/ via extraResources.
#
# Build:
#   poetry run pyinstaller \
#     --distpath kamp_ui/resources \
#     --workpath /tmp/pyinstaller-work \
#     --clean -y kamp.spec

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
    # watchdog macOS FSEvents backend
    "watchdog.observers.fsevents",
    # macOS tray (rumps) — included for menu-bar mode parity
    "rumps",
    # explicit submodule collection for kamp packages
    *collect_submodules("kamp_core"),
    *collect_submodules("kamp_daemon"),
]

# ---------------------------------------------------------------------------
# Excludes — Playwright + Bandcamp sync are out of scope for the .app bundle.
# kamp_daemon.syncer is now imported lazily inside _cmd_sync() so it won't
# be pulled in by static analysis, but list it here as belt-and-suspenders.
# ---------------------------------------------------------------------------
excludes = [
    "playwright",
    "playwright.sync_api",
    "playwright._impl",
    "kamp_daemon.syncer",
    "kamp_daemon.ext.builtin.bandcamp",  # requires syncer + Playwright
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
    *collect_data_files("certifi"),   # TLS certs for requests / MusicBrainz
    # pyproject.toml is used by _get_version() as the canonical version source;
    # include it so the frozen app reports the correct version string.
    ("pyproject.toml", "."),
]

a = Analysis(
    ["_kamp_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    console=True,  # server process — no GUI window
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
