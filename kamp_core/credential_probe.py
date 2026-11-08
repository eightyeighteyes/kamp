"""Secret-free vocabulary for reporting *where* a credential is stored.

KAMP-743: a session credential can land in any of four stores — the macOS Data
Protection Keychain, the macOS Login Keychain, a DPAPI-wrapped SQLite row on
Windows, or a plaintext SQLite row anywhere — and the warning ``set_session``
logs when it falls back to plaintext never said which path had run.  Two
plaintext rows were found on disk that consequently could not be attributed
after the fact.

Everything here is deliberately lossy: a caller gets a status, a length and a
one-way digest, never a credential value.  This is a leaf module importing
nothing from ``kamp_core`` so that both :mod:`kamp_core.library` and
:mod:`kamp_core.macos_keychain` can use it without an import cycle.
"""

from __future__ import annotations

import hashlib
from typing import Final

#: A credential was found in this store.
PRESENT: Final[str] = "present"

#: The store answered and holds nothing for this service.
ABSENT: Final[str] = "absent"

#: The store is reachable but locked, so presence could not be determined.
LOCKED: Final[str] = "locked"

#: The store could not be consulted; the reason is reported alongside.
ERROR: Final[str] = "error"

#: Presence is unknowable from this process.
#:
#: The Data Protection Keychain is only enumerable by a binary carrying the
#: ``keychain-access-groups`` entitlement.  An unentitled read that reports
#: "item not found" is therefore indistinguishable from "no access" — and
#: recording that as :data:`ABSENT` is the precise mistake that misdirected the
#: first pass at KAMP-743 into believing a credential had vanished.  Only a
#: positive hit, or an answer from the entitled helper, is trustworthy.
UNKNOWN_NO_ENTITLEMENT: Final[str] = "unknown (no entitlement)"

#: Enough digest to make a collision implausible, while remaining useless as a
#: credential and short enough to paste into a ticket.
_DIGEST_CHARS: Final[int] = 12

#: Fixed, non-secret domain separator.  A *random* salt is not an option: the
#: whole purpose of the digest is that the same credential yields the same
#: value in two different stores, and across two different runs — comparing a
#: dev run against a packaged run is how the Login Keychain fallback was
#: confirmed in KAMP-749.  Determinism is the requirement; the salt only keeps
#: these digests from colliding with any other PBKDF2 output.
_DIGEST_SALT: Final[bytes] = b"kamp.credential_probe.v1"

#: Deliberately slow.  ~6 calls per `doctor-credentials` run, so the cost is
#: unnoticeable there, and it means the digest resists guessing even if some
#: future caller passes a low-entropy value.
_DIGEST_ITERATIONS: Final[int] = 200_000


def credential_digest(value: str) -> str:
    """Return a short one-way digest of *value*, for equality comparison only.

    This is what lets a report say "the Login Keychain and the SQLite row hold
    *different* values" — the signal that a stale keychain item is shadowing a
    newer plaintext row — without either value reaching a log file or a ticket.

    Uses PBKDF2-HMAC-SHA256 rather than a bare SHA-256.  Not because this is
    password storage — the output is never used to verify anything, and the
    input is a high-entropy credential blob — but because a plain digest of a
    secret is indistinguishable from password hashing to a reader or a static
    analyser (CodeQL ``py/weak-sensitive-data-hashing`` flagged exactly that),
    and a slow KDF is strictly the more conservative choice at no practical
    cost in a diagnostic command.  The truncation is what keeps the output
    unusable for reconstruction.
    """
    return hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        _DIGEST_SALT,
        _DIGEST_ITERATIONS,
    ).hex()[:_DIGEST_CHARS]


def describe(status: str, **extra: object) -> dict[str, object]:
    """Build a store-report dict with *status* and any extra secret-free fields."""
    report: dict[str, object] = {"status": status}
    report.update(extra)
    return report
