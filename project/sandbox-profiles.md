# Sandbox Profile Scoping — TASK-87.1

This document records the empirical profiling of the three built-in
extensions (MusicBrainz tagger, CoverArt Archive fetcher, Bandcamp syncer)
and the derived allow-rules for each OS-level sandbox.

The profiles implemented in `kamp_daemon/ext/sandbox/` are derived from
this analysis.  When the strace/dtruss runs noted below are executed against
a live environment, this document should be updated and any discrepancies
between the predicted and observed profiles should be reflected in the
profile templates.

---

## Profiling methodology

Use `scripts/profile_extensions.py` as the target process:

```bash
# Linux
strace -f -e trace=file,network,process \
  poetry run python scripts/profile_extensions.py musicbrainz \
  2>strace_mb.txt

# macOS
sudo dtruss -f \
  poetry run python scripts/profile_extensions.py musicbrainz \
  2>dtruss_mb.txt
```

From the strace output, extract:

```bash
# Filesystem paths opened (excluding ENOENT):
grep -E 'openat|open\(' strace_mb.txt | grep -v 'ENOENT' | sort -u

# Network connections:
grep 'connect\|socket\b' strace_mb.txt | sort -u

# Subprocess spawning:
grep 'execve' strace_mb.txt | sort -u
```

Profiles in this document are based on **static analysis** of the source
code (as of TASK-87.1, April 2026).  Entries marked ⚠️ have not yet been
validated with a live strace/dtruss run and should be confirmed before the
sandbox is considered complete.

---

## Extension 1: MusicBrainz Tagger (`KampMusicBrainzTagger`)

**Source:** `kamp_daemon/ext/builtin/musicbrainz.py` wrapping `kamp_daemon/tagger.py`

### Filesystem access

| Path | Access | Notes |
|------|--------|-------|
| Python venv (`sys.prefix`) | read | stdlib + musicbrainzngs package imports |
| `/usr/lib`, `/usr/local/lib`, `/opt/homebrew` | read | System dylibs, OpenSSL |
| `/etc/ssl/cert.pem` | read | TLS certificate bundle (via certifi or system) |
| `/etc/localtime` | read | Python datetime module |
| `/dev/urandom` | read | SSL random seed |

**No filesystem writes.** ⚠️ Confirm with strace.

### Network

| Host | Protocol | Port | Purpose |
|------|----------|------|---------|
| `musicbrainz.org` | HTTPS | 443 | Recording search, release lookup |
| DNS resolver | UDP | 53 | Hostname resolution |

`musicbrainzngs` contacts only `musicbrainz.org`.  No CDN or third-party
hosts.  ⚠️ Confirm with strace `connect` calls.

### Subprocess spawning

None.  Single-threaded HTTP client.  ⚠️ Confirm execve calls = 0.

### Derived sandbox tier

`TIER_MINIMAL` — network only, no filesystem writes, no subprocess spawn.

---

## Extension 2: CoverArt Archive Fetcher (`KampCoverArtArchive`)

**Source:** `kamp_daemon/ext/builtin/coverart.py` wrapping `kamp_daemon/artwork.py`

### Filesystem access

| Path | Access | Notes |
|------|--------|-------|
| Python venv (`sys.prefix`) | read | Pillow, requests, urllib3 imports |
| `/usr/lib`, `/usr/local/lib` | read | libz, libjpeg (Pillow C extensions) |
| `/etc/ssl/cert.pem` | read | TLS |
| `/dev/urandom` | read | SSL |

**No filesystem writes.**  Artwork bytes are returned in memory to the host,
which embeds them via `KampGround.set_artwork()`.  ⚠️ Confirm with strace.

### Network

| Host | Protocol | Port | Purpose |
|------|----------|------|---------|
| `coverartarchive.org` | HTTPS | 443 | Artwork listing JSON |
| `*.archive.org` (CDN) | HTTPS | 443 | Image downloads |
| DNS resolver | UDP | 53 | Hostname resolution |

CoverArtArchive serves images from the Internet Archive CDN; the specific
subdomain varies by image.  The initial macOS profile uses `*:443` for all
outbound TCP, which is broader than strictly necessary but avoids
maintaining a CDN subdomain list.  ⚠️ Confirm CDN domains with strace.

### Subprocess spawning

None.  ⚠️ Confirm execve calls = 0.

### Derived sandbox tier

`TIER_MINIMAL` — network only, no filesystem writes, no subprocess spawn.

---

## Extension 3: Bandcamp Syncer (`KampBandcampSyncer`)

**Source:** `kamp_daemon/ext/builtin/bandcamp.py` wrapping `kamp_daemon/bandcamp.py`

### Architecture note

The Bandcamp syncer is architecturally distinct from the tagger/artwork
extensions: it manages its own subprocess isolation via an internal
`_spawn_worker()` method (Playwright and the `bandcamp` module are loaded
only inside that child process).  `DaemonCore` invokes the syncer lifecycle
methods (`start`, `stop`, `pause`, `resume`) in-process; it does NOT call
`invoke_extension()` on it.

**Implication for sandboxing:** the OS sandbox (applied via
`invoke_extension`'s subprocess initializer) does not apply to the Bandcamp
syncer in its normal operational path.  The TIER_SYNCER profile is defined
for correctness (and for third-party syncers that do use `invoke_extension`)
but is not actively applied to `KampBandcampSyncer` in the current
implementation.  TASK-87.2 / TASK-87.3 follow-ups should address Bandcamp
syncer isolation separately if needed.

### Filesystem access

| Path | Access | Notes |
|------|--------|-------|
| `~/.local/share/kamp/bandcamp_session.json` | read/write | Session cookies |
| `~/.local/share/kamp/bandcamp_state.json` | read/write | Sync state (downloaded item IDs) |
| Staging directory (`config.paths.staging`) | write | Downloaded ZIP/audio files |
| `/tmp` (system temp) | read/write | Playwright session files, download staging |
| Chromium binary path (`~/.cache/ms-playwright/`) | read/execute | Playwright-managed Chromium |

### Network

| Host | Protocol | Port | Purpose |
|------|----------|------|---------|
| `bandcamp.com` | HTTPS | 443 | API endpoints, login, collection pages |
| `bcbits.com` | HTTPS | 443 | CDN — download ZIPs and audio |
| DNS resolver | UDP | 53 | Hostname resolution |

### Subprocess spawning

Playwright spawns Chromium.  The `execve` target is the Chromium binary
managed by Playwright (path: `~/.cache/ms-playwright/chromium-*/chrome`).

**TIER_SYNCER profile requirements:**
- `process-exec*` allowed (macOS) / execve not blocked in seccomp (Linux)
- Write access to `~/.local/share/kamp/`, `/tmp`, staging dir
- Read/execute access to Playwright Chromium binary path

⚠️ The Chromium binary path is not currently encoded in the TIER_SYNCER
profile templates.  This must be added before the syncer sandbox is
enforced.  The Playwright cache path can be discovered at runtime via
`playwright._impl._driver.compute_driver_executable()` or by parsing
`~/.cache/ms-playwright/`.

---

## macOS sandbox-exec profile design

Profiles are in `kamp_daemon/ext/sandbox/_macos.py` as string templates.

### `TIER_MINIMAL` — allow rules

```scheme
(version 1)
(deny default)
; Python venv (sys.prefix)
(allow file-read* (subpath "{venv}"))
; System dylibs + frameworks
(allow file-read* (subpath "/usr/lib"))
(allow file-read* (subpath "/usr/local/lib"))
(allow file-read* (subpath "/opt/homebrew"))
(allow file-read* (subpath "/System/Library/Frameworks"))
(allow file-read* (subpath "/System/Library/PrivateFrameworks"))
(allow file-read* (subpath "/usr/lib/system"))
(allow file-read* (subpath "/private/var/db/dyld"))
; TLS + system config
(allow file-read* (literal "/etc/ssl/cert.pem"))
(allow file-read* (literal "/private/etc/localtime"))
(allow file-read* (literal "/private/etc/hosts"))
(allow file-read* (literal "/private/etc/resolv.conf"))
; /dev
(allow file-read* (subpath "/dev"))
(allow file-write-data (literal "/dev/null"))
; Runtime IPC
(allow mach-lookup)
(allow mach-bootstrap)
(allow ipc-posix-sem)
(allow ipc-posix-shm)
(allow signal (target self))
; Network: DNS + outbound HTTPS/HTTP
(allow network-outbound (remote udp "*:53"))
(allow network-outbound (remote tcp "*:80"))
(allow network-outbound (remote tcp "*:443"))
; Block subprocess execution
(deny process-exec*)
```

**Outstanding tightening** (post-profiling):
- Replace `(allow network-outbound (remote tcp "*:443"))` with domain-specific rules
  (requires Apple Sandbox Language domain literals, which are less ergonomic)
- Confirm that `/System/Volumes/Preboot` read is actually needed

### `TIER_SYNCER` — additional allow rules

```scheme
; Writable paths
(allow file-read* (subpath "{home}/.local/share/kamp"))
(allow file-write* (subpath "{home}/.local/share/kamp"))
(allow file-read* (subpath "/private/tmp"))
(allow file-write* (subpath "/private/tmp"))
(allow file-read* (subpath "/var/folders"))
(allow file-write* (subpath "/var/folders"))
; Allow subprocess execution (Playwright/Chromium)
(allow process-exec*)
(allow process-fork)
```

**Outstanding tightening** (post-profiling):
- Add Chromium binary path to `file-read*` / `process-exec*`
- Add staging directory as a writable path (requires runtime parameterisation)

---

## Linux landlock + seccomp profile design

Profiles are implemented in `kamp_daemon/ext/sandbox/_linux.py`.

### seccomp: block-list approach

The current implementation uses a **default-allow + block execve** approach
rather than a strict allowlist.  This is intentional for the initial
implementation:

- A full allowlist requires exhaustive empirical profiling to avoid false
  blocks on legitimate Python syscalls.
- Blocking execve (syscall 59 on x86-64) is the primary security goal:
  preventing extensions from spawning subprocesses.
- Post-profiling, the allowlist should be tightened to a strict default-deny
  filter.  The syscalls observed during strace runs are the basis for that
  list (⚠️ TODO).

### landlock: write restriction

The minimal tier passes an empty `allowed_write_paths` list, which means
the process cannot write to any filesystem path.  This is enforced via the
following landlock-handled access types:

```
LANDLOCK_ACCESS_FS_WRITE_FILE | LANDLOCK_ACCESS_FS_MAKE_REG |
LANDLOCK_ACCESS_FS_MAKE_DIR  | LANDLOCK_ACCESS_FS_REMOVE_DIR |
LANDLOCK_ACCESS_FS_REMOVE_FILE | LANDLOCK_ACCESS_FS_MAKE_SYM |
LANDLOCK_ACCESS_FS_MAKE_SOCK | LANDLOCK_ACCESS_FS_REFER
```

For the syncer tier, writes are allowed to:
- `~/.local/share/kamp/` (state + session files)
- `/tmp` (Playwright temp files)

Staging directory is not currently in the allowed list (requires runtime
parameterisation at sync time).  ⚠️ TODO before syncer sandbox enforcement.

### Minimum kernel version

Landlock v1 requires kernel ≥ 5.13.  The implementation checks the running
kernel version at initializer time and logs a warning + skips landlock on
older kernels.  CI runs on `ubuntu-latest` (kernel ≥ 6.x as of 2026).

### Outstanding items (post-profiling)

- [ ] Run strace against each extension (see methodology above) and update
      this document with observed syscall lists
- [ ] Add strict seccomp allowlist based on observed syscalls
- [ ] Confirm landlock write restriction does not break PIL (image codec
      temp files?) for CoverArt fetcher
- [ ] Add Playwright Chromium binary path to TIER_SYNCER landlock allow rules
- [ ] Add staging directory to TIER_SYNCER landlock allow rules (runtime
      parameterisation needed)

---

## Windows AppContainer requirements (TASK-87.4)

Windows sandboxing is deferred (TASK-87.4) and not required for the
marketplace gate.  For reference:

### Capability requirements (AppContainer)

Based on the static analysis above:

| Extension | Required capabilities |
|-----------|----------------------|
| MusicBrainz tagger | `internetClient` (outbound internet) |
| CoverArt fetcher | `internetClient` (outbound internet) |
| Bandcamp syncer | `internetClient`, `picturesLibrary` (downloads), `<Device> genericUSB` (keyboard for interactive login) |

A restricted token approach (no capabilities) may suffice for tagger/artwork
since they only need outbound HTTPS and no filesystem writes.

⚠️ Windows profiling has not been performed.

---

## MDM/EDR interference risks (macOS)

The `sandbox_init()` API (libsandbox.dylib) may be silently blocked by:

- **CrowdStrike Falcon** — process-level policy enforcement intercepts
  sandbox_init calls; the return value may be 0 (success) while the
  sandbox is not actually applied, OR it may fail with a non-zero return
  code.  In either case, detection requires testing on a managed device.

- **Jamf** — similar to Falcon; Jamf's PPPC (Privacy Preferences Policy
  Control) does not directly block sandbox_init but may interact with the
  sandbox profile's mach-lookup rules.

**Mitigation in implementation:**
- Failure is non-fatal: `_apply_sandbox()` logs a warning and returns.
- A structured log event at WARNING level is emitted, allowing operators to
  detect unprotected workers in their log pipeline.
- The marketplace gate (TASK-87) requires the sandbox to be verified
  functional on at least one non-managed macOS device before release.
