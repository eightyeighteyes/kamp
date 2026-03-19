# Language
- Python 3
- Poetry for dependency management & packaging

# Code Style
- Follow PEP8 and use black for formatting
- Use typing declarations
- Write meaningful behavioral tests
- The API should be expressive

# Workflow
- Start work with a new branch created from a clean, updated main branch. Do not create files before creating a new branch.
- Use red/green TDD
- Before opening a PR, run all CI steps (testing, linting, type checks, etc) locally
- Before opening a PR, scan through README.md to make sure it's still valid (nothing it says has drifted from what the application does)
- Prefer running single tests, and not the whole test suite, for performance; use `--no-cov` when running a single file to skip the coverage threshold check (e.g. `poetry run pytest tests/test_foo.py -v --no-cov`)
- Update documentation (README.md) after new features are validated
- Document rationale in comments: succinctly explain *why* decisions are made
- After completing a feature, before closing a pull request, retrospect about the development experience and update claude.md with lessons learned.

# Lessons Learned
## Mutagen
- **Never use `mutagen.File(path)` for MP3s** — use `mutagen.id3.ID3(str(path))` directly. `mutagen.File` parses the full MPEG audio stream and raises on files with minimal/fake audio data (common in tests).
- `ID3NoHeaderError` and frame classes (`TPE1`, `TALB`, etc.) exist at runtime but not in type stubs — use `except Exception:` not `except id3.ID3NoHeaderError:`.
- APIC frames are keyed `"APIC:Cover"`, not `"APIC:"` — check with `any(k.startswith("APIC") for k in tags)`.
- `mp4.tags.get(key)` returns `None | value` — always check for None before indexing.

## Testing
- **Fake MP3 files:** write `b"\xff\xfb" * 64` then `id3.ID3().save(str(path))` — valid ID3 header without valid MPEG frames.
- **Fake M4A files:** write `b"\x00" * 32` and patch `mutagen.mp4.MP4` — real MP4 containers aren't needed.
- **Patching `Path.stat` in Python 3.12:** `patch.object(path_instance, "stat", ...)` fails (C-implemented method). Patch at class level: `patch("pathlib.Path.stat", fn)` where `fn` takes `self` as first arg.

## Skip/optimization logic
Before implementing "skip if already done," define precisely what *correct* means for the skip condition. "Present ≠ best available" — skipping based on presence alone can degrade quality (e.g., skipping artwork fetch because art is embedded, when embedded art is lower quality than what the Archive would return). Validate skip conditions against the regression case explicitly.

## macOS system integration
Budget at least a Side for any feature touching osacompile, Spotlight registration, or macOS app bundles. Corporate MDM/EDR (Falcon, Jamf) can silently block registration in ways that are hard to diagnose.