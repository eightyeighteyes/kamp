"""macOS sandbox_init integration tests.

These tests spawn real subprocesses to verify that the macOS sandbox profile
is applied correctly and enforces the expected restrictions.

Tests are skipped on non-macOS platforms.

Design notes
------------
We use subprocess.Popen (not multiprocessing) to avoid pickling constraints:
the test scripts are passed as Python -c strings and can call sandbox
internals directly.  This is intentional — the goal is to verify that the
sandbox primitives work, not to test the full invoke_extension() path (which
requires the extension class to be importable from the child).

The full invoke_extension() path is validated via manual testing described
in project/sandbox-profiles.md.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS sandbox tests only run on darwin"
)


def _run_in_sandbox(tier: str, code: str, timeout: int = 30) -> str:
    """Apply the given sandbox tier in a fresh subprocess, run *code*, and
    return the combined stdout+stderr output.

    The subprocess applies the sandbox initializer before running *code*
    so the sandbox is active for every statement in *code*.
    """
    # The script must have zero leading indent so Python parses it cleanly.
    # textwrap.dedent is NOT used here because {code} substitution would
    # produce 0-space lines that break the common-indent calculation.
    script = (
        "from kamp_daemon.ext.sandbox._macos import get_initializer\n"
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
# sandbox_init application
# ---------------------------------------------------------------------------


class TestMacOSSandboxInit:
    def test_sandbox_init_does_not_crash(self) -> None:
        """sandbox_init completes without raising — MDM/EDR may downgrade to
        a warning but must never crash the worker."""
        output = _run_in_sandbox("minimal", "print('OK')")
        assert "OK" in output
        # A warning about sandbox_init failure is acceptable (MDM/EDR),
        # but an unhandled exception is not.
        assert "Traceback" not in output

    def test_syncer_sandbox_init_does_not_crash(self) -> None:
        output = _run_in_sandbox("syncer", "print('OK')")
        assert "OK" in output
        assert "Traceback" not in output


# ---------------------------------------------------------------------------
# Minimal tier: filesystem write restriction (AC#4)
# ---------------------------------------------------------------------------


class TestMacOSMinimalFilesystemRestriction:
    def test_write_to_tmp_is_blocked(self, tmp_path: object) -> None:
        """Minimal sandbox denies writes to /tmp (resolves to /private/tmp on macOS)."""
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
                # Clean up if write somehow succeeded (e.g. MDM bypasses sandbox)
                try:
                    os.unlink(path)
                except OSError:
                    pass
        """)
        output = _run_in_sandbox("minimal", code)
        if "kamp sandbox: sandbox_init failed" in output:
            pytest.skip(
                "sandbox_init failed (likely MDM/EDR in managed environment) — "
                "cannot validate restrictions"
            )
        assert (
            "WRITE_BLOCKED" in output
        ), f"Expected write to be blocked under minimal sandbox, got: {output!r}"

    def test_write_to_home_subdir_is_blocked(self) -> None:
        """Minimal sandbox denies writes outside explicitly allowed paths."""
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
        if "kamp sandbox: sandbox_init failed" in output:
            pytest.skip("sandbox_init failed (MDM/EDR)")
        assert (
            "WRITE_BLOCKED" in output
        ), f"Expected write to home to be blocked, got: {output!r}"


# ---------------------------------------------------------------------------
# Minimal tier: read operations are allowed
# ---------------------------------------------------------------------------


class TestMacOSMinimalReadAllowed:
    def test_python_stdlib_readable(self) -> None:
        """Extensions must be able to import stdlib modules after sandbox is applied."""
        code = textwrap.dedent("""\
            import json, hashlib, ssl, urllib.parse
            print("IMPORTS_OK")
        """)
        output = _run_in_sandbox("minimal", code)
        if "kamp sandbox: sandbox_init failed" in output:
            pytest.skip("sandbox_init failed (MDM/EDR)")
        assert (
            "IMPORTS_OK" in output
        ), f"stdlib imports failed under minimal sandbox: {output!r}"

    def test_dev_null_writable(self) -> None:
        """Writing to /dev/null must be allowed (used by logging sinks)."""
        code = textwrap.dedent("""\
            with open("/dev/null", "w") as f:
                f.write("discard")
            print("DEV_NULL_OK")
        """)
        output = _run_in_sandbox("minimal", code)
        if "kamp sandbox: sandbox_init failed" in output:
            pytest.skip("sandbox_init failed (MDM/EDR)")
        assert (
            "DEV_NULL_OK" in output
        ), f"Expected /dev/null to be writable, got: {output!r}"


# ---------------------------------------------------------------------------
# Minimal tier: subprocess spawning is blocked
# ---------------------------------------------------------------------------


class TestMacOSMinimalSubprocessBlocked:
    def test_subprocess_exec_is_blocked(self) -> None:
        """Minimal sandbox denies process-exec* — extensions cannot spawn children."""
        code = textwrap.dedent("""\
            import subprocess as sp
            try:
                sp.run(["/bin/echo", "hello"], capture_output=True, timeout=5)
                print("EXEC_SUCCEEDED")
            except (PermissionError, OSError, Exception) as e:
                print(f"EXEC_BLOCKED: {e}")
        """)
        output = _run_in_sandbox("minimal", code)
        if "kamp sandbox: sandbox_init failed" in output:
            pytest.skip("sandbox_init failed (MDM/EDR)")
        assert "EXEC_BLOCKED" in output, (
            f"Expected subprocess exec to be blocked under minimal sandbox, "
            f"got: {output!r}"
        )


# ---------------------------------------------------------------------------
# Syncer tier: subprocess spawning is allowed
# ---------------------------------------------------------------------------


class TestMacOSSyncerSubprocessAllowed:
    def test_subprocess_exec_is_allowed(self) -> None:
        """Syncer sandbox permits process-exec* (needed for Playwright/Chromium).

        We verify that the exec() call is *not* denied by the sandbox (no
        PermissionError raised from subprocess.run).  Child processes inherit
        the parent's sandbox profile; a child crash (SIGABRT) after exec
        succeeds is a separate sandbox inheritance issue and does not mean
        process-exec* was denied.
        """
        code = textwrap.dedent("""\
            import subprocess as sp
            try:
                # Use sys.executable so the child binary is in the allowed
                # file-read* paths (venv prefix).  /bin/echo inherits the
                # sandbox profile and may SIGABRT on pipe writes; Python
                # can at least import and exit cleanly.
                import sys
                r = sp.run(
                    [sys.executable, "-c", "pass"],
                    capture_output=True, timeout=5, text=True,
                )
                # exec succeeded (no PermissionError) regardless of returncode
                print("EXEC_NOT_BLOCKED")
            except (PermissionError, OSError) as e:
                print(f"EXEC_BLOCKED: {e}")
        """)
        output = _run_in_sandbox("syncer", code)
        if "kamp sandbox: sandbox_init failed" in output:
            pytest.skip("sandbox_init failed (MDM/EDR)")
        assert "EXEC_BLOCKED" not in output, (
            f"Expected exec to be allowed under syncer sandbox (no PermissionError), "
            f"got: {output!r}"
        )
        assert (
            "EXEC_NOT_BLOCKED" in output
        ), f"Expected EXEC_NOT_BLOCKED marker, got: {output!r}"
