"""First-party kamp extensions shipped with the kamp package.

These extensions are declared under [project.entry-points."kamp.extensions"] in
pyproject.toml and are discovered by the extension host the same way as any
third-party extension.  They carry no special privileges — the distinction is
ownership, not architecture.

Pipeline-stage extensions (KampMusicBrainzTagger, KampCoverArtArchive) run
in-process within the pipeline subprocess rather than via invoke_extension(),
because the outer pipeline.py subprocess is already the isolation boundary and
return values need to flow back to the host.
"""
