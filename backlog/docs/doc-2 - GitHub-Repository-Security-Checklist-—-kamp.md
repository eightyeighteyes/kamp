---
id: doc-2
title: GitHub Repository Security Checklist — kamp
type: other
created_date: '2026-04-19 02:57'
---
# GitHub Repository Security Checklist — kamp

**Project**: kamp — personal open-source music player (Python FastAPI + Electron/React)
**Threat model**: Single developer, public open-source repo, personal data (local music library, Last.fm session tokens, AcoustID key, Apple Developer signing certificate). No multi-user RBAC, no financial data, no HIPAA.
**Assessed**: 2026-04-18

---

## Priority Summary (do these first)

| # | Item | Severity | Effort |
|---|------|----------|--------|
| 1 | AcoustID key XOR bytes committed to `main` | High | Single |
| 2 | Action versions not SHA-pinned | Medium | Side |
| 3 | `ci.yml` has no `permissions:` block | Medium | Single |
| 4 | Enable Dependabot for Python + npm | Medium | Single |
| 5 | Enable GitHub secret scanning + push protection | Medium | Single |
| 6 | Enable 2FA on GitHub account | High | Single |
| 7 | Require status checks on `main` branch | Medium | Single |

---

## 1. Branch Protection

`Settings → Branches → Add rule → Branch name pattern: main`

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| Require PR before merging | **Must** | Enable "Require a pull request before merging"; set "Required approvals: 0" (solo project — you still get the PR review step) | Branch protection rule | Prevents direct pushes to `main` that bypass CI; already enforced by workflow but protection makes it a hard block at the GitHub layer |
| Require status checks | **Must** | Enable "Require status checks to pass before merging"; add `python`, `ui`, `sandbox-macos` (the three jobs in `ci.yml`) | Branch protection rule → status checks | Stops merging a commit where type checks or tests are broken |
| Restrict force-push | **Must** | Enable "Restrict who can force push" with no exceptions | Branch protection rule | Prevents accidental or coerced rewrite of `main` history, including deletion of security-relevant commits |
| Require linear history | **Should** | Enable "Require linear history" | Branch protection rule | Keeps blame and bisect clean; makes it harder to obscure when a vulnerable commit was introduced |
| Require signed commits | **Consider** | Enable "Require signed commits" only after setting up GPG or SSH signing in your git config | Branch protection rule | Provides commit attribution integrity; requires GPG key setup first |

---

## 2. Secret Scanning & Push Protection

`Settings → Security → Code security → Secret scanning`

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| Enable secret scanning | **Must** | Toggle "Secret scanning" on | Code security tab | Continuously scans the repository history and new pushes for known secret patterns from 100+ providers |
| Enable push protection | **Must** | Toggle "Push protection" on (requires secret scanning first) | Code security tab | Blocks a `git push` at the GitHub layer if a known secret pattern is detected in the commit content |
| **AcoustID key — HIGH** | **Must** | The XOR-obfuscated key in `kamp_daemon/acoustid.py` lines 24–25 decodes to a real API key. The design intent (placeholder `b""` in source, CI substitutes at build time) is correct but current `HEAD` contains real key material. Rotate the key at acoustid.org → My Applications, then restore the file to `_KEY: bytes = b""` and `_SALT: bytes = b""` | `kamp_daemon/acoustid.py` | XOR with a 4-byte salt is trivially reversible — any reader of the source has the key |
| Last.fm key — accepted risk | **Info** | `LASTFM_API_KEY` / `LASTFM_API_SECRET` in `kamp_core/scrobbler.py` are intentionally public per Last.fm desktop client terms (same as beets, Picard, Rhythmbox). No action needed | `kamp_core/scrobbler.py` | None required |
| `.gitignore` hygiene | **Must** | Add `*.p12` and `*.keychain-db` explicitly since the build workflow handles Apple Developer certs | `.gitignore` | Prevents certificate files from being accidentally staged |
| Pre-commit secrets hook | **Should** | Add `gitleaks` or `detect-secrets` as a step in `.githooks/pre-commit` (currently only runs black + mypy) | `.githooks/pre-commit` | Catches secrets before they ever reach GitHub |

---

## 3. Dependabot

`Settings → Security → Code security → Dependabot`

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| Enable Dependabot alerts — Python | **Must** | Verify "Dependabot alerts" is active (on by default for public repos) | Code security tab | Flags CVEs in `poetry.lock` dependencies |
| Enable Dependabot alerts — npm | **Must** | Same toggle — covers both ecosystems | Code security tab | Flags CVEs in `kamp_ui/package-lock.json` |
| Dependabot security PRs | **Must** | Enable "Dependabot security updates" | Code security tab | Reduces the window between CVE published and dependency patched |
| `dependabot.yml` config | **Should** | Create `.github/dependabot.yml` for weekly version update PRs for Python, npm, and GitHub Actions | `.github/dependabot.yml` | Prevents slow dependency rot |

Recommended `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    labels: ["dependencies"]

  - package-ecosystem: "npm"
    directory: "/kamp_ui"
    schedule:
      interval: "weekly"
    labels: ["dependencies"]

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    labels: ["dependencies"]
```

---

## 4. Code Scanning / SAST

`Settings → Security → Code scanning`

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| Enable CodeQL — Python | **Should** | Use "Set up code scanning" → "Default setup" for Python | Code scanning tab | Catches injection vulnerabilities and insecure API patterns in the Python backend |
| Enable CodeQL — JavaScript | **Should** | Include JavaScript/TypeScript in the same CodeQL setup | Code scanning tab | Flags DOM XSS, prototype pollution, and insecure IPC patterns in Electron code |
| Alert triage policy | **Should** | Dismiss alerts as "False positive" or "Won't fix" with a written reason — creates an audit trail | Code scanning alerts UI | Ensures risk is consciously accepted rather than silently ignored |

---

## 5. GitHub Actions Security

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| Add `permissions: {}` default to `ci.yml` | **Must** | `ci.yml` has no `permissions:` block; add `permissions: {}` at the top level to deny all write access by default | `.github/workflows/ci.yml` | Prevents a compromised dependency from pushing to the repo during CI |
| Pin actions to commit SHAs | **Should** | All workflows use floating tags (`@v6`, `@v4`, etc.). Pin to full SHA with tag as comment | All workflow files | Eliminates supply-chain risk where a mutable action tag is redirected to malicious code that can exfiltrate secrets |
| `HOMEBREW_TAP_TOKEN` scoping | **Should** | Replace classic PAT with a fine-grained PAT scoped to only the `homebrew-kamp` repo with `contents: write` | GitHub Settings → Developer settings → Fine-grained tokens | A leaked classic PAT can write to any repo you own |
| Workflow file review checklist | **Should** | Before merging any workflow change: (1) no new `permissions:` scopes without justification, (2) new `uses:` references are SHA-pinned, (3) secrets never echoed to logs | PR review | Prevents incremental permission creep |

SHA-pinning example:
```yaml
# Before
- uses: actions/checkout@v6
# After — get SHA with: gh api repos/actions/checkout/git/refs/tags/v6 --jq '.object.sha'
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v6
```

---

## 6. Access Control & Account Security

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| 2FA on GitHub account | **Must** | Enable a hardware key (YubiKey) or TOTP app; avoid SMS-based 2FA | GitHub Settings → Password and authentication | Account takeover via credential stuffing or phishing |
| SSH key hygiene | **Must** | Each machine gets its own SSH key with a passphrase. Audit and revoke keys from machines no longer in use | GitHub Settings → SSH and GPG keys | A stolen private key without a passphrase gives immediate push access |
| PAT audit | **Should** | Revoke any classic PATs; replace with fine-grained PATs scoped per repo | GitHub Settings → Developer settings | Leaked broad-scope classic PATs can write to all your repos |
| OAuth app audit | **Should** | Revoke any apps you no longer use at Settings → Applications → Authorized OAuth Apps | GitHub Settings | Third-party OAuth apps with `repo` scope can read and write your private repos |

---

## 7. Supply Chain & Release Security

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| SHA-256 checksums on releases | **Must** | ✅ Already implemented — `release.yml` computes and uploads a SHA-256 of the sdist | `release.yml` | Confirms the downloaded tarball was not tampered with |
| npm provenance | **Must** | ✅ Already implemented — `publish-kamp-groover.yml` uses `npm publish --provenance` | `publish-kamp-groover.yml` | Verifiable chain of custody from source commit to published npm package |
| Signed release tags | **Should** | Use `git tag -s v1.x.y -m "..."` for GPG-signed annotated tags (requires GPG key setup first) | Local git | Allows downstream consumers to verify the tag was created by the known developer key |
| Lock file integrity in CI | **Must** | ✅ Already correct — `npm ci` and `poetry install` respect lockfiles; do not use `npm install` or `poetry update` in CI | `ci.yml` | Prevents supply-chain attacks via loose semver ranges |
| `SECURITY.md` | **Consider** | Add a one-paragraph `SECURITY.md` with your email for responsible disclosure | `.github/SECURITY.md` | Gives security researchers a clear reporting path |
| `CODEOWNERS` | **Skip** | No value on a solo project | n/a | n/a |

---

## 8. Monitoring & Audit

| Item | Rating | What to do | Where | Threat mitigated |
|------|--------|------------|-------|-----------------|
| Dependabot alert notifications | **Must** | Verify notifications are enabled at Settings → Notifications → Dependabot alerts | GitHub Settings → Notifications | Ensures you hear about a CVE within hours |
| GitHub security advisories | **Should** | Watch the repo for "Security alerts" event type; also watch upstream repos for key dependencies | Dependabot + repo watch | CVE notification before a patch is available gives time to assess exposure |
| Audit log spot-checks | **Consider** | Quarterly review of: new OAuth app authorizations, new SSH keys, PAT creations, repo visibility changes | GitHub Settings → Audit log | Detects account compromise or accidental public exposure |

---

## 9. Accepted Risks

| Item | Reason to skip |
|------|----------------|
| Required PR approvals (count > 0) | Solo project — self-approval is the only option |
| Branch protection: restrict pushers to a team | Requires GitHub Teams (organization feature) |
| CODEOWNERS auto-review requests | Only useful with multiple contributors |
| SBOM generation | `poetry.lock` and `package-lock.json` fulfill this at this scale |
| Artifact signing (sigstore/cosign) | SHA-256 checksums + npm provenance are sufficient |
| DAST (dynamic scanning) | The FastAPI server is loopback-only; attack surface doesn't justify it |

---

## Specific High-Priority Findings

### 🔴 High — Act now

**1. AcoustID key committed to `main`** (`kamp_daemon/acoustid.py` lines 24–25)
The committed XOR bytes decode to a real API key. Steps to fix:
1. Rotate the key at acoustid.org → My Applications
2. Restore the file: `_KEY: bytes = b""` and `_SALT: bytes = b""`
3. Verify CI still substitutes the new key from GitHub Secrets at build time
4. Enable push protection to prevent recurrence

**2. GitHub account 2FA**
An account takeover bypasses every other control listed here.

### 🟡 Medium — Next sprint

**3. `ci.yml` missing `permissions:` block**
Add `permissions: {}` at the top level; grant only what each job needs.

**4. Action tags not SHA-pinned**
All five workflows use floating tags. A compromised action tag can exfiltrate `ACOUSTID_KEY`, `CSC_LINK`, `APPLE_APP_SPECIFIC_PASSWORD`, and `HOMEBREW_TAP_TOKEN` during a release run.

**5. Enable Dependabot + secret scanning**
Both are free on public repos and take under 5 minutes to enable.
