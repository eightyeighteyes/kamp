"""Linux landlock + seccomp integration tests.

These tests spawn real subprocesses to verify that the Linux sandbox
(seccomp + landlock) is applied correctly and enforces the expected
restrictions.

Tests are skipped on non-Linux platforms and where kernel support is absent.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux sandbox tests only run on linux"
)


def _kernel_supports_landlock() -> bool:
    """True if the running kernel is >= 5.13 (landlock v1)."""
    try:
        release = platform.release()
        major, minor = int(release.split(".")[0]), int(release.split(".")[1])
        return (major, minor) >= (5, 13)
    except (IndexError, ValueError):
        return False


def _run_in_sandbox(tier: str, code: str, timeout: int = 30) -> str:
    """Apply the given sandbox tier in a fresh subprocess, run *code*, and
    return the combined stdout+stderr output."""
    script = (
        "from kamp_daemon.ext.sandbox._linux import get_initializer\n"
        f"init = get_initializer({tier!r})\n"
        "init()\n"
        "\n"
        f"{code}\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Minimal tier: seccomp blocks execve (subprocess spawning)
# ---------------------------------------------------------------------------


class TestLinuxMinimalSubprocessBlocked:
    def test_execve_is_blocked(self) -> None:
        """Minimal sandbox blocks execve — extensions cannot spawn children."""
        code = textwrap.dedent("""\
            import subprocess as sp
            try:
                sp.run(["/bin/echo", "hello"], capture_output=True, timeout=5)
                print("EXEC_SUCCEEDED")
            except Exception as e:
                print(f"EXEC_BLOCKED: {type(e).__name__}: {e}")
        """)
        output = _run_in_sandbox("minimal", code)
        if "libseccomp not found" in output or "seccomp_init returned NULL" in output:
            pytest.skip("libseccomp unavailable — seccomp restriction skipped")
        # The process may be killed by SIGSYS (seccomp kills it), which means
        # the subprocess.run() above raises SubprocessError, or the child
        # process exits non-zero.  Either way, "EXEC_SUCCEEDED" must not appear.
        assert (
            "EXEC_SUCCEEDED" not in output
        ), f"Expected execve to be blocked under minimal sandbox, got: {output!r}"


# ---------------------------------------------------------------------------
# Minimal tier: landlock blocks filesystem writes
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _kernel_supports_landlock(),
    reason="Landlock requires kernel >= 5.13",
)
class TestLinuxMinimalLandlockWriteBlocked:
    def test_write_to_tmp_is_blocked(self) -> None:
        """Minimal landlock (empty write paths) denies all writes, including /tmp."""
        code = textwrap.dedent("""\
            import os
            path = "/tmp/kamp_sandbox_evil_test_87"
            try:
                with open(path, "w") as f:
                    f.write("evil")
                print("WRITE_SUCCEEDED")
            except (PermissionError, OSError) as e:
                print(f"WRITE_BLOCKED: {e}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        """)
        output = _run_in_sandbox("minimal", code)
        if (
            "landlock_create_ruleset failed" in output
            or "landlock_restrict_self failed" in output
        ):
            pytest.skip(
                "Landlock syscalls failed (permissions or kernel support issue)"
            )
        assert (
            "WRITE_BLOCKED" in output
        ), f"Expected /tmp write to be blocked by landlock, got: {output!r}"

    def test_write_to_home_is_blocked(self) -> None:
        """Writes to home directory are denied by minimal landlock."""
        code = textwrap.dedent("""\
            from pathlib import Path
            path = Path.home() / "kamp_sandbox_evil_test_87.txt"
            try:
                path.write_text("evil")
                print("WRITE_SUCCEEDED")
            except (PermissionError, OSError) as e:
                print(f"WRITE_BLOCKED: {e}")
            finally:
                try:
                    path.unlink()
                except OSError:
                    pass
        """)
        output = _run_in_sandbox("minimal", code)
        if (
            "landlock_create_ruleset failed" in output
            or "landlock_restrict_self failed" in output
        ):
            pytest.skip("Landlock syscalls failed")
        assert (
            "WRITE_BLOCKED" in output
        ), f"Expected home write to be blocked by landlock, got: {output!r}"


# ---------------------------------------------------------------------------
# Minimal tier: allowed operations work
# ---------------------------------------------------------------------------


class TestLinuxMinimalAllowedOps:
    def test_stdlib_imports_work(self) -> None:
        """Python stdlib imports must succeed after sandbox is applied."""
        code = textwrap.dedent("""\
            import json, hashlib, ssl, urllib.parse, collections
            print("IMPORTS_OK")
        """)
        output = _run_in_sandbox("minimal", code)
        assert (
            "IMPORTS_OK" in output
        ), f"stdlib imports failed under minimal sandbox: {output!r}"

    def test_file_reads_still_work(self) -> None:
        """Landlock restricts writes but not reads — reading files must still work."""
        code = textwrap.dedent("""\
            # Read a file that definitely exists
            with open("/etc/hostname") as f:
                content = f.read()
            print(f"READ_OK: {len(content)} bytes")
        """)
        output = _run_in_sandbox("minimal", code)
        assert (
            "READ_OK" in output
        ), f"File read failed under minimal sandbox: {output!r}"


# ---------------------------------------------------------------------------
# Syncer tier: execve is allowed
# ---------------------------------------------------------------------------


class TestLinuxSyncerExecveAllowed:
    def test_subprocess_exec_is_allowed(self) -> None:
        """Syncer tier does not block execve — Playwright/Chromium can be spawned."""
        code = textwrap.dedent("""\
            import subprocess as sp
            try:
                r = sp.run(["/bin/echo", "hello"], capture_output=True,
                           timeout=5, text=True)
                if r.returncode == 0:
                    print("EXEC_SUCCEEDED")
                else:
                    print(f"EXEC_FAILED rc={r.returncode}")
            except Exception as e:
                print(f"EXEC_BLOCKED: {e}")
        """)
        output = _run_in_sandbox("syncer", code)
        assert (
            "EXEC_SUCCEEDED" in output
        ), f"Expected execve to be allowed under syncer sandbox, got: {output!r}"


# ---------------------------------------------------------------------------
# Syncer tier: landlock allows writes to state dir and /tmp
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _kernel_supports_landlock(),
    reason="Landlock requires kernel >= 5.13",
)
class TestLinuxSyncerLandlockWriteAllowed:
    def test_write_to_tmp_is_allowed(self) -> None:
        """Syncer tier allows writes to /tmp for Playwright temp files."""
        code = textwrap.dedent("""\
            import os
            path = "/tmp/kamp_sandbox_syncer_test_87"
            try:
                with open(path, "w") as f:
                    f.write("syncer test")
                print("WRITE_SUCCEEDED")
            except (PermissionError, OSError) as e:
                print(f"WRITE_BLOCKED: {e}")
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        """)
        output = _run_in_sandbox("syncer", code)
        if (
            "landlock_create_ruleset failed" in output
            or "landlock_restrict_self failed" in output
        ):
            pytest.skip("Landlock syscalls failed")
        assert (
            "WRITE_SUCCEEDED" in output
        ), f"Expected /tmp write to be allowed under syncer sandbox, got: {output!r}"

    def test_write_to_kamp_state_dir_is_allowed(self) -> None:
        """Syncer tier allows writes to ~/.local/share/kamp/.

        The state directory must exist before the sandbox is applied because:
        1. landlock rules are only added for paths that exist at ruleset
           creation time (non-existent paths are skipped in _apply_landlock).
        2. Creating parent directories inside the sandbox would require write
           access to paths not yet in the ruleset.

        The script therefore creates the directory before init() so that
        _apply_landlock can register it as an allowed write path.
        """
        # Build the script manually so the mkdir runs before sandbox init.
        script = (
            "from pathlib import Path\n"
            "state_dir = Path.home() / '.local' / 'share' / 'kamp'\n"
            "state_dir.mkdir(parents=True, exist_ok=True)\n"
            "from kamp_daemon.ext.sandbox._linux import get_initializer\n"
            "init = get_initializer('syncer')\n"
            "init()\n"
            "path = state_dir / 'kamp_sandbox_syncer_test_87.txt'\n"
            "try:\n"
            "    path.write_text('syncer test')\n"
            "    print('WRITE_SUCCEEDED')\n"
            "except (PermissionError, OSError) as e:\n"
            "    print(f'WRITE_BLOCKED: {e}')\n"
            "finally:\n"
            "    try:\n"
            "        path.unlink()\n"
            "    except OSError:\n"
            "        pass\n"
        )
        import subprocess as _sp

        result = _sp.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        if (
            "landlock_create_ruleset failed" in output
            or "landlock_restrict_self failed" in output
        ):
            pytest.skip("Landlock syscalls failed")
        assert "WRITE_SUCCEEDED" in output, (
            f"Expected kamp state dir write to be allowed under syncer sandbox, "
            f"got: {output!r}"
        )
