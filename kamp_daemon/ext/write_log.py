"""Host-side application of extension library.write mutations.

After invoke_extension() returns a list of Mutation objects, the host calls
apply_mutations() to log and apply each one to the library database.  This
module is the only code path through which extension mutations reach the DB
— the worker subprocess never writes directly.

Only UpdateMetadataMutation and SetArtworkMutation are permitted; any other
Mutation subtype raises ValueError so unlisted operations are rejected at
the point of application (AC #5).
"""

from __future__ import annotations

from kamp_core.library import LibraryIndex

from .context import Mutation, SetArtworkMutation, UpdateMetadataMutation


def apply_mutations(
    extension_id: str,
    mutations: list[Mutation],
    library: LibraryIndex,
) -> None:
    """Log and apply a list of mutations from an extension to *library*.

    Each mutation is dispatched to the appropriate LibraryIndex method,
    which logs the old and new values to extension_audit_log before
    applying the change.  Processing stops and raises ValueError if an
    unknown mutation type is encountered so callers can decide whether
    to abort or log and continue.

    Args:
        extension_id: Identifier for the extension that produced the
            mutations (used as the audit log's extension_id column).
        mutations: Ordered list of mutations returned by invoke_extension.
        library: LibraryIndex instance to write to.

    Raises:
        ValueError: If a mutation of an unrecognised type is encountered.
            Only UpdateMetadataMutation and SetArtworkMutation are valid.
    """
    for mutation in mutations:
        if isinstance(mutation, UpdateMetadataMutation):
            library.apply_metadata_update(extension_id, mutation.mbid, mutation.fields)
        elif isinstance(mutation, SetArtworkMutation):
            library.apply_set_artwork(
                extension_id, mutation.mbid, mutation.artwork.mime_type
            )
        else:
            raise ValueError(
                f"Unknown mutation type {type(mutation).__qualname__!r} from "
                f"extension {extension_id!r} — only UpdateMetadataMutation and "
                f"SetArtworkMutation are permitted"
            )
