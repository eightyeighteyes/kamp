"""macOS sandbox_init integration for extension worker subprocesses.

Applies an Apple Sandbox Profile Language profile via the private
``sandbox_init()`` API (libsandbox.dylib).  The profile is applied as a
multiprocessing.Process initializer — it runs in the child process before
extension code loads.

Design notes
------------
- ``sandbox_init`` is technically deprecated (since macOS 10.12) but remains
  functional as of macOS 15.  The ``sandbox-exec`` CLI tool calls the same
  API internally.
- The profile is a string rendered from a template; dynamic values such as
  sys.prefix (the venv path) are substituted at call time inside the child
  process.
- MDM/EDR tools (Falcon, Jamf) may silently block sandbox_init in managed
  environments.  Failure is therefore non-fatal: a warning is logged and the
  extension runs unsandboxed.  This is the correct rollout-phase trade-off;
  see lessons-learned in CLAUDE.md.
- ``process-fork`` is intentionally NOT denied in the minimal profile because
  macOS Python uses fork internally for some operations.  ``process-exec*``
  blocks launching new executables, which is the meaningful restriction.
  The syncer profile allows process-exec* for Playwright/Chromium.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from . import TIER_MINIMAL, TIER_SYNCER

_logger = logging.getLogger(__name__)

# sandbox_init flags: 0 = custom profile string (not a named built-in profile)
_SANDBOX_CUSTOM_PROFILE = ctypes.c_uint64(0)

# ---------------------------------------------------------------------------
# Profile templates (Apple Sandbox Profile Language)
# ---------------------------------------------------------------------------

# Variables substituted at call time:
#   {venv}   – sys.prefix (Poetry virtualenv or system Python prefix)
#   {home}   – str(Path.home())
#
# Written conservatively — allow what's required, deny everything else.
# Paths are refined against empirical strace/dtruss data in
# project/sandbox-profiles.md.

_MINIMAL_PROFILE_TEMPLATE = """\
(version 1)
(deny default)

;; Python venv and standard library (dynamic imports after sandbox_init)
(allow file-read* (subpath "{venv}"))
(allow file-read* (subpath "/usr/lib"))
(allow file-read* (subpath "/usr/local/lib"))
(allow file-read* (subpath "/opt/homebrew"))

;; macOS system frameworks and dyld shared cache
(allow file-read* (subpath "/System/Library/Frameworks"))
(allow file-read* (subpath "/System/Library/PrivateFrameworks"))
(allow file-read* (subpath "/usr/lib/system"))
(allow file-read* (subpath "/private/var/db/dyld"))
(allow file-read* (subpath "/System/Volumes/Preboot"))

;; TLS certificate bundle (used by ssl / certifi)
(allow file-read* (literal "/etc/ssl/cert.pem"))
(allow file-read* (literal "/private/etc/ssl/cert.pem"))
(allow file-read* (subpath "/System/Library/Security"))

;; System config
(allow file-read* (literal "/etc/localtime"))
(allow file-read* (literal "/private/etc/localtime"))
(allow file-read* (literal "/private/etc/hosts"))
(allow file-read* (literal "/private/etc/resolv.conf"))

;; /dev — stdin/stdout/stderr + null + urandom
(allow file-read* (subpath "/dev"))
(allow file-write-data (literal "/dev/null"))

;; Mach-O bootstrap (Python runtime IPC)
(allow mach-lookup)
(allow mach-bootstrap)

;; POSIX shared memory (Python multiprocessing semaphores)
(allow ipc-posix-sem)
(allow ipc-posix-shm)

;; Signals to self only
(allow signal (target self))

;; Network: DNS (UDP 53) and outbound HTTPS/HTTP
;; Domain-level allow-listing is enforced by KampGround.fetch() at the API
;; layer; the sandbox allows outbound TCP to prevent double-gating.
(allow network-outbound (remote udp "*:53"))
(allow network-outbound (remote tcp "*:80"))
(allow network-outbound (remote tcp "*:443"))

;; Deny subprocess execution — prevents extensions spawning child processes
(deny process-exec*)
"""

_SYNCER_PROFILE_TEMPLATE = """\
(version 1)
(deny default)

;; Python venv and standard library
(allow file-read* (subpath "{venv}"))
(allow file-read* (subpath "/usr/lib"))
(allow file-read* (subpath "/usr/local/lib"))
(allow file-read* (subpath "/opt/homebrew"))

;; macOS system frameworks and dyld shared cache
(allow file-read* (subpath "/System/Library/Frameworks"))
(allow file-read* (subpath "/System/Library/PrivateFrameworks"))
(allow file-read* (subpath "/usr/lib/system"))
(allow file-read* (subpath "/private/var/db/dyld"))
(allow file-read* (subpath "/System/Volumes/Preboot"))

;; TLS certificate bundle
(allow file-read* (literal "/etc/ssl/cert.pem"))
(allow file-read* (literal "/private/etc/ssl/cert.pem"))
(allow file-read* (subpath "/System/Library/Security"))

;; System config
(allow file-read* (literal "/etc/localtime"))
(allow file-read* (literal "/private/etc/localtime"))
(allow file-read* (literal "/private/etc/hosts"))
(allow file-read* (literal "/private/etc/resolv.conf"))

;; /dev
(allow file-read* (subpath "/dev"))
(allow file-write-data (literal "/dev/null"))

;; Mach-O / IPC
(allow mach-lookup)
(allow mach-bootstrap)
(allow ipc-posix-sem)
(allow ipc-posix-shm)
(allow signal (target self))

;; Network (Bandcamp APIs + bcbits.com CDN + DNS)
(allow network-outbound (remote udp "*:53"))
(allow network-outbound (remote tcp "*:80"))
(allow network-outbound (remote tcp "*:443"))

;; Writable paths: kamp state directory + temporary files
;; Staging path is not encoded in the profile — Playwright writes downloads
;; to temp directories chosen by Chromium; restricting those paths requires
;; runtime parameterisation (tracked in TASK-87.2 follow-up).
(allow file-read* (subpath "{home}/.local/share/kamp"))
(allow file-write* (subpath "{home}/.local/share/kamp"))
(allow file-read* (subpath "/private/tmp"))
(allow file-write* (subpath "/private/tmp"))
(allow file-read* (subpath "/var/folders"))
(allow file-write* (subpath "/var/folders"))

;; Allow subprocess execution for Playwright/Chromium
(allow process-exec*)
(allow process-fork)
"""


def _render(template: str, venv: str, home: str) -> str:
    return template.replace("{venv}", venv).replace("{home}", home)


# ---------------------------------------------------------------------------
# Sandbox application
# ---------------------------------------------------------------------------


def _apply_sandbox(profile: str) -> None:
    """Apply *profile* via sandbox_init().  Non-fatal on failure."""
    try:
        lib = ctypes.CDLL("libsandbox.dylib", use_errno=True)
    except OSError:
        _logger.warning(
            "kamp sandbox: libsandbox.dylib not available — "
            "extension will run unsandboxed"
        )
        return

    lib.sandbox_init.restype = ctypes.c_int
    lib.sandbox_init.argtypes = [
        ctypes.c_char_p,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    errmsg = ctypes.c_char_p()
    ret = lib.sandbox_init(
        profile.encode(),
        _SANDBOX_CUSTOM_PROFILE,
        ctypes.byref(errmsg),
    )
    if ret != 0:
        # MDM/EDR tools (Falcon, Jamf) can silently block sandbox_init.
        # Log a warning but do not crash the worker — the extension runs
        # unsandboxed in that case.  See CLAUDE.md lessons-learned.
        msg = errmsg.value.decode() if errmsg.value else "unknown error"
        _logger.warning(
            "kamp sandbox: sandbox_init failed (%s) — "
            "MDM/EDR may be blocking; extension will run unsandboxed",
            msg,
        )


# ---------------------------------------------------------------------------
# Module-level initializer functions (must be top-level to be picklable)
# ---------------------------------------------------------------------------
# These functions are passed as multiprocessing.Process(initializer=...).
# Spawn-mode multiprocessing pickles the initializer by qualified name, so
# they must be defined at module level — not as closures or lambdas.


def _init_minimal() -> None:  # pragma: no cover
    """Process initializer: apply minimal sandbox profile."""
    profile = _render(
        _MINIMAL_PROFILE_TEMPLATE,
        venv=sys.prefix,
        home=str(Path.home()),
    )
    _apply_sandbox(profile)


def _init_syncer() -> None:  # pragma: no cover
    """Process initializer: apply syncer sandbox profile."""
    profile = _render(
        _SYNCER_PROFILE_TEMPLATE,
        venv=sys.prefix,
        home=str(Path.home()),
    )
    _apply_sandbox(profile)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_INITIALIZERS: dict[str, Callable[[], None]] = {
    TIER_MINIMAL: _init_minimal,
    TIER_SYNCER: _init_syncer,
}


def get_initializer(tier: str) -> Callable[[], None]:
    """Return the macOS sandbox initializer for *tier*."""
    return _INITIALIZERS[tier]
