"""OS-level sandboxing for extension worker subprocesses.

Provides kernel-enforced restrictions for the spawn-context subprocesses
created by invoke_extension().  The sandbox is applied via a
multiprocessing.Process ``initializer`` function that runs in the child
before extension code loads — no structural changes to the worker model
are required.

Two profile tiers are defined:

``TIER_MINIMAL``
    Intended for taggers and artwork sources.  Restricts filesystem writes
    to nothing outside /dev; blocks subprocess spawning (macOS: deny
    process-exec; Linux: seccomp blocks execve).  Network access is allowed
    for DNS and HTTPS — domain-level enforcement remains the responsibility
    of KampGround.fetch().

``TIER_SYNCER``
    Intended for syncers (e.g. Bandcamp/Playwright) that legitimately need
    to write to the staging directory, maintain state files, and spawn
    Chromium subprocesses.  Filesystem writes are restricted to the staging
    and state directories; subprocess spawning is permitted.

On platforms without sandbox support the initializer is None (no-op).
The caller (worker.py) passes None directly to multiprocessing.Process —
this is explicitly supported by the multiprocessing API.

See ``project/sandbox-profiles.md`` for the empirical profiling data that
informed the allow-rules in each tier.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

# Profile tier constants — extensions declare _sandbox_tier on their class.
TIER_MINIMAL: str = "minimal"
TIER_SYNCER: str = "syncer"

_SUPPORTED_TIERS = {TIER_MINIMAL, TIER_SYNCER}


def get_initializer(tier: str) -> Callable[[], None] | None:
    """Return a sandbox initializer callable for *tier*, or None.

    The returned callable is suitable for the ``initializer`` parameter of
    ``multiprocessing.Process``.  It takes no arguments; dynamic values
    such as ``sys.prefix`` are computed inside the child process at call time.

    Returns None on platforms where sandboxing is not implemented (Windows,
    or platforms where the required kernel/library support is absent).

    Args:
        tier: One of ``TIER_MINIMAL`` or ``TIER_SYNCER``.

    Raises:
        ValueError: If *tier* is not a recognised tier name.
    """
    if tier not in _SUPPORTED_TIERS:
        raise ValueError(
            f"Unknown sandbox tier {tier!r}. "
            f"Valid values: {sorted(_SUPPORTED_TIERS)}"
        )

    if sys.platform == "darwin":
        from ._macos import get_initializer as _get

        return _get(tier)

    if sys.platform == "linux":
        from ._linux import get_initializer as _get

        return _get(tier)

    # Unsupported platform (Windows, etc.) — no sandbox applied.
    return None
