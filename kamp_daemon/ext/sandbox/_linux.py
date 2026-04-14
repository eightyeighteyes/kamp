"""Linux landlock + seccomp integration for extension worker subprocesses.

Applies two complementary kernel-level restrictions:

**seccomp** (syscall filtering)
    Blocks ``execve`` for the minimal tier, preventing extensions from
    spawning child processes.  Implemented via ctypes calls to libseccomp.
    If libseccomp is unavailable, a warning is logged and seccomp is skipped.

**landlock** (filesystem path restriction)
    Restricts which filesystem paths the process can write to.  Requires
    Linux kernel ≥ 5.13 (landlock v1).  On older kernels a warning is logged
    and landlock is skipped — the process runs without filesystem restriction.

Both mechanisms are non-fatal: if a restriction cannot be applied, a warning
is logged and the extension runs with reduced (or no) sandbox protection.

Design notes
------------
- The "minimal" tier (taggers, artwork sources) blocks execve and denies all
  filesystem writes.
- The "syncer" tier (Bandcamp + Playwright) allows execve and allows writes
  to the kamp state directory and /tmp.
- seccomp is applied first (simpler to set up), then landlock.
- landlock_restrict_self() requires PR_SET_NO_NEW_PRIVS=1 first.
- All syscall numbers are for x86-64 Linux.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import platform
from collections.abc import Callable
from pathlib import Path

from . import TIER_MINIMAL, TIER_SYNCER

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kernel version detection
# ---------------------------------------------------------------------------


def _kernel_version() -> tuple[int, int]:
    """Return (major, minor) of the running kernel."""
    try:
        release = platform.release()
        parts = release.split(".")
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return (0, 0)


def _landlock_available() -> bool:
    """True if the kernel supports landlock (≥ 5.13)."""
    return _kernel_version() >= (5, 13)


# ---------------------------------------------------------------------------
# seccomp: block execve via libseccomp
# ---------------------------------------------------------------------------

# libseccomp action constants
_SCMP_ACT_ALLOW = 0x7FFF0000
# SCMP_ACT_ERRNO(EPERM): return EPERM to the caller instead of killing the
# process.  KILL_PROCESS (0x80000000) requires the container to have
# CAP_SYS_ADMIN or an unconfined seccomp policy — GitHub Actions runners
# restrict this.  ERRNO(EPERM) works universally and causes subprocess.run()
# to raise PermissionError, which is equally observable in tests.
_SCMP_ACT_ERRNO_EPERM = 0x00050001  # 0x00050000 | EPERM(1)

# x86-64 syscall numbers for process execution.
# glibc >= 2.24 uses execveat(AT_FDCWD, ...) rather than execve, so both
# must be blocked to prevent subprocess spawning.
_NR_EXECVE = 59
_NR_EXECVEAT = 322


def _apply_seccomp_block_execve() -> None:
    """Add a seccomp rule that kills the process if it calls execve.

    Uses libseccomp (default-allow + block execve).  Non-fatal if unavailable.
    """
    libname = ctypes.util.find_library("seccomp")
    if libname is None:
        _logger.warning(
            "kamp sandbox: libseccomp not found — "
            "seccomp restriction skipped; extension may spawn subprocesses"
        )
        return

    try:
        lib = ctypes.CDLL(libname, use_errno=True)
    except OSError as exc:
        _logger.warning(
            "kamp sandbox: could not load libseccomp (%s) — "
            "seccomp restriction skipped",
            exc,
        )
        return

    # Set explicit restype/argtypes before any call.  Without restype,
    # ctypes defaults to c_int (32-bit) — on 64-bit Linux scmp_filter_ctx
    # is a pointer (8 bytes) so the upper 32 bits are silently truncated,
    # producing a garbage pointer.  Passing that garbage to seccomp_load()
    # installs a malformed BPF filter whose default action becomes
    # KILL_PROCESS rather than ALLOW, killing the process on every syscall.
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,  # ctx
        ctypes.c_uint32,  # action
        ctypes.c_int,  # syscall
        ctypes.c_uint,  # arg_cnt
    ]
    lib.seccomp_load.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.restype = None
    lib.seccomp_release.argtypes = [ctypes.c_void_p]

    # Build a default-ALLOW filter and add a KILL rule for execve.
    # This allows all syscalls except execve, which is sufficient to prevent
    # subprocess spawning.  A full allowlist will be derived from empirical
    # profiling (see project/sandbox-profiles.md) and applied in a
    # follow-up hardening pass.
    ctx = lib.seccomp_init(_SCMP_ACT_ALLOW)
    if not ctx:
        _logger.warning(
            "kamp sandbox: seccomp_init returned NULL — " "seccomp restriction skipped"
        )
        return

    try:
        for nr in (_NR_EXECVE, _NR_EXECVEAT):
            ret = lib.seccomp_rule_add(ctx, _SCMP_ACT_ERRNO_EPERM, nr, 0)
            if ret != 0:
                _logger.warning(
                    "kamp sandbox: seccomp_rule_add failed for syscall %d (%d) — "
                    "seccomp restriction skipped",
                    nr,
                    ret,
                )
                return

        ret = lib.seccomp_load(ctx)
        if ret != 0:
            _logger.warning(
                "kamp sandbox: seccomp_load failed (%d) — "
                "seccomp restriction skipped",
                ret,
            )
    finally:
        lib.seccomp_release(ctx)


# ---------------------------------------------------------------------------
# landlock: filesystem write restriction
# ---------------------------------------------------------------------------

# landlock syscall numbers (x86-64, kernel 5.13+)
_NR_LANDLOCK_CREATE_RULESET = 444
_NR_LANDLOCK_ADD_RULE = 445
_NR_LANDLOCK_RESTRICT_SELF = 446

# prctl constants
_PR_SET_NO_NEW_PRIVS = 38

# landlock rule type
_LANDLOCK_RULE_PATH_BENEATH = 1

# landlock filesystem access flags (kernel 5.13 v1)
_FS_WRITE_FILE = 1 << 1
_FS_MAKE_REG = 1 << 8
_FS_MAKE_DIR = 1 << 7
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_SYM = 1 << 12
_FS_MAKE_SOCK = 1 << 9
_FS_REFER = 1 << 13

# The set of filesystem write operations we handle (restrict).
_HANDLED_WRITE_ACCESS = (
    _FS_WRITE_FILE
    | _FS_MAKE_REG
    | _FS_MAKE_DIR
    | _FS_REMOVE_DIR
    | _FS_REMOVE_FILE
    | _FS_MAKE_SYM
    | _FS_MAKE_SOCK
    | _FS_REFER
)


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _syscall(number: int, *args: int) -> int:
    """Invoke a Linux syscall via ctypes.CDLL(None).syscall."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    # Build argtypes dynamically based on arg count
    libc.syscall.argtypes = [ctypes.c_long] + [ctypes.c_long] * len(args)
    result: int = libc.syscall(number, *args)
    return result


# O_PATH is Linux-specific (not in Python's os module on all platforms).
# It opens a file descriptor for path operations without requiring read/write
# permission — needed by landlock_add_rule which takes a directory fd.
_O_PATH: int = getattr(os, "O_PATH", 0x200000)


def _apply_landlock(allowed_write_paths: list[str]) -> None:
    """Restrict filesystem writes to *allowed_write_paths* via landlock.

    Paths not in the list will be read-only.  If a path does not exist it is
    silently skipped (e.g. watch folder may not be created yet).

    Non-fatal: logs a warning and returns if any step fails.
    """
    if not _landlock_available():
        _logger.warning(
            "kamp sandbox: kernel < 5.13 — landlock unavailable; "
            "filesystem write restriction skipped"
        )
        return

    # Step 1: Create ruleset covering the write access types we handle.
    attr = _LandlockRulesetAttr(handled_access_fs=_HANDLED_WRITE_ACCESS)
    ruleset_fd = _syscall(
        _NR_LANDLOCK_CREATE_RULESET,
        ctypes.addressof(attr),
        ctypes.sizeof(attr),
        0,
    )
    if ruleset_fd < 0:
        errno = ctypes.get_errno()
        _logger.warning(
            "kamp sandbox: landlock_create_ruleset failed (errno %d) — "
            "filesystem restriction skipped",
            errno,
        )
        return

    try:
        # Step 2: Add allow rules for each permitted write path.
        for path_str in allowed_write_paths:
            path = Path(path_str)
            if not path.exists():
                continue  # skip non-existent paths (e.g. watch folder not yet created)
            try:
                fd = os.open(str(path), _O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            try:
                rule = _LandlockPathBeneathAttr(
                    allowed_access=_HANDLED_WRITE_ACCESS,
                    parent_fd=fd,
                )
                ret = _syscall(
                    _NR_LANDLOCK_ADD_RULE,
                    ruleset_fd,
                    _LANDLOCK_RULE_PATH_BENEATH,
                    ctypes.addressof(rule),
                    0,
                )
                if ret != 0:
                    errno = ctypes.get_errno()
                    _logger.warning(
                        "kamp sandbox: landlock_add_rule failed for %s "
                        "(errno %d) — path skipped",
                        path_str,
                        errno,
                    )
            finally:
                os.close(fd)

        # Step 3: Require PR_SET_NO_NEW_PRIVS before restrict_self.
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.restype = ctypes.c_int
        libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        ret = libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
        if ret != 0:
            errno = ctypes.get_errno()
            _logger.warning(
                "kamp sandbox: prctl(PR_SET_NO_NEW_PRIVS) failed (errno %d) — "
                "landlock restriction skipped",
                errno,
            )
            return

        # Step 4: Enforce the ruleset.
        ret = _syscall(_NR_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)
        if ret != 0:
            errno = ctypes.get_errno()
            _logger.warning(
                "kamp sandbox: landlock_restrict_self failed (errno %d) — "
                "filesystem restriction skipped",
                errno,
            )
    finally:
        os.close(ruleset_fd)


# ---------------------------------------------------------------------------
# Module-level initializer functions (must be top-level to be picklable)
# ---------------------------------------------------------------------------


def _init_minimal() -> None:  # pragma: no cover
    """Process initializer: apply minimal sandbox (seccomp block execve + landlock no-write)."""
    # Block subprocess spawning.
    _apply_seccomp_block_execve()
    # Restrict all filesystem writes (empty allowed list = no writes permitted).
    _apply_landlock(allowed_write_paths=[])


def _init_syncer() -> None:  # pragma: no cover
    """Process initializer: apply syncer sandbox (allow execve + landlock state/tmp writes)."""
    # Allow subprocess execution (Playwright/Chromium) — no execve block.
    # Restrict writes to kamp state dir + /tmp.
    state_dir = str(Path.home() / ".local" / "share" / "kamp")
    _apply_landlock(allowed_write_paths=[state_dir, "/tmp"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_INITIALIZERS: dict[str, Callable[[], None]] = {
    TIER_MINIMAL: _init_minimal,
    TIER_SYNCER: _init_syncer,
}


def get_initializer(tier: str) -> Callable[[], None]:
    """Return the Linux sandbox initializer for *tier*."""
    return _INITIALIZERS[tier]
