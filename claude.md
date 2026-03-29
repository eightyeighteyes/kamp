# Language
- Python 3
- Poetry for dependency management & packaging

# Code Style
- Follow PEP8 and use black for formatting
- Use typing declarations
- Write meaningful behavioral tests
- The API should be expressive

# Workflow
- After cloning, run `git config core.hooksPath .githooks` to enable the pre-commit hook (black + mypy).
- Start work with a new branch created from a clean, updated main branch. Do not create files before creating a new branch.
- One fix or feature per branch / PR.
- Use red/green TDD
- Before opening a PR, run all CI steps (testing, linting, type checks, etc) locally
- Before opening a PR, scan through README.md to make sure it's still valid (nothing it says has drifted from what the application does)
- When merging a branch, squash commits.
- Task lifecycle: when work begins on a task, move it to In Progress. When the user confirms it is tested and complete, squash merge the PR and move the task to Done.
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
- **QueueHandler re-emission loop:** When a subprocess worker adds a `QueueHandler` to the root logger and an inline test helper runs the worker in-process, the handler stays attached. If `_replay_log_queue` re-emits records via the logger hierarchy, those records loop back through the root's QueueHandler indefinitely. Fix: remove the QueueHandler in the worker's `finally` block so it's gone before replay runs.

## Subprocess isolation
- Any global state set in the parent process (e.g. `musicbrainzngs.set_useragent`) is NOT inherited by `spawn`-mode subprocesses — re-apply it inside the worker function.
- Worker functions must clean up any handlers/state they add to shared objects (logging, signals) so the parent process is unaffected when the worker runs inline in tests.

## Skip/optimization logic
Before implementing "skip if already done," define precisely what *correct* means for the skip condition. "Present ≠ best available" — skipping based on presence alone can degrade quality (e.g., skipping artwork fetch because art is embedded, when embedded art is lower quality than what the Archive would return). Validate skip conditions against the regression case explicitly.

## Memory optimization
When the goal is a runtime property (memory released, latency reduced), write a test that measures that property *before* implementing the mechanism. Mechanism tests (e.g. "modules removed from sys.modules") can pass while the property test fails — this is exactly what happened with the `sys.modules` eviction approach, which passed all tests but left pymalloc-held pages resident. A property test (measuring process RSS before and after) would have caught this immediately and driven the correct approach (subprocess isolation) from the start.

## macOS system integration
Budget at least a Side for any feature touching osacompile, Spotlight registration, or macOS app bundles. Corporate MDM/EDR (Falcon, Jamf) can silently block registration in ways that are hard to diagnose.

## macOS notifications
`NSUserNotificationCenter` (used by `rumps.notification()`) is a no-op on macOS 14+. Use `UNUserNotificationCenter` instead. It requires `CFBundleIdentifier` — embed it in `launcher/main.c` via `__TEXT,__info_plist`. Without the compiled launcher (e.g. dev venv), `UNUserNotificationCenter.currentNotificationCenter()` crashes; wrap it in `try/except` and fall back to `rumps.notification()`.

## Scope discipline
If the same sub-problem fails twice in a row, stop and check in before attempting a third approach. Two failures signal a wrong level of abstraction or an environment constraint — not a fixable bug. This applies especially to test fixtures and dev-environment workarounds, which have no user value on their own.

<!-- BACKLOG.MD MCP GUIDELINES START -->

<CRITICAL_INSTRUCTION>

## BACKLOG WORKFLOW INSTRUCTIONS

This project uses Backlog.md MCP for all task and project management activities.

**CRITICAL GUIDANCE**

- If your client supports MCP resources, read `backlog://workflow/overview` to understand when and how to use Backlog for this project.
- If your client only supports tools or the above request fails, call `backlog.get_backlog_instructions()` to load the tool-oriented overview. Use the `instruction` selector when you need `task-creation`, `task-execution`, or `task-finalization`.

- **First time working here?** Read the overview resource IMMEDIATELY to learn the workflow
- **Already familiar?** You should have the overview cached ("## Backlog.md Overview (MCP)")
- **When to read it**: BEFORE creating tasks, or when you're unsure whether to track work

These guides cover:
- Decision framework for when to create tasks
- Search-first workflow to avoid duplicates
- Links to detailed guides for task creation, execution, and finalization
- MCP tools reference

You MUST read the overview resource to understand the complete workflow. The information is NOT summarized here.

</CRITICAL_INSTRUCTION>

<!-- BACKLOG.MD MCP GUIDELINES END -->
