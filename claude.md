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
- Prefer running single tests, and not the whole test suite, for performance
- Update documentation (README.md) after new features are validated
- Document rationale in comments: succinctly explain *why* decisions are made