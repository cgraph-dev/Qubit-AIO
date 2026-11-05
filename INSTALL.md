# Install Qubit

Qubit installs from source on Linux and macOS, and through a PowerShell
installer (from a checkout or a verified release ZIP) on Windows. Linux is the
reference platform and includes the full voice stack. macOS supports the core
runtime, CLI, dashboard and the messaging-gateway LaunchAgent; the voice
bridge, hotkey, orb and voice-typing are Linux-only. The Windows PowerShell
installer and bootstrap are provided but have not been validated on a real
Windows release runner.

## Prerequisites

Required:

- Linux (Ubuntu x86_64) or macOS for the shell installer; Windows PowerShell
  5.1+ for `scripts\install.ps1`. On macOS the core runtime/CLI/dashboard and
  one gateway LaunchAgent are installed.
- `bash`, `curl`, and `uv` (the installer uses `uv` to create the virtual
  environment).
- On macOS, Apple's Command Line Tools (run `xcode-select --install` if `git`
  is missing).
- Python **3.12 or newer, below 3.15**. The installer creates the environment
  with Python 3.12 through `uv`; the project metadata accepts
  `>=3.12,<3.15`.
- At least **4 GB RAM** and **2 GB free disk** for the base install. Model
  assets need additional disk space.
- Node.js **22.12 or newer** and `npm` for a source install that serves the
  dashboard (Vite 8 requires `^20.19` or `>=22.12`): the source tree does not
  include the built bundle (`frontend/qubit-dist`). Skip with
  `--without-dashboard`, or use a release archive that already contains the
  bundle.

Optional:

- `ollama` for local model provisioning.
- `tar` plus `sha256sum`, `shasum`, or `openssl` when model assets are
  downloaded; the installer verifies pinned checksums. These assets are
  Linux-only: macOS never downloads the voice models.

## Install

```bash
git clone https://github.com/cgraph-dev/Qubit.git
cd Qubit

./scripts/install.sh --help       # options; changes nothing
./scripts/install.sh --dry-run    # preflight and resolved layout; no writes
./scripts/install.sh              # venv, dependencies, config, launcher
```

On macOS, install the core and its gateway LaunchAgent with:

```bash
./scripts/install.sh --with-launchd
```

`--with-systemd` is accepted on macOS as a compatibility alias for the
LaunchAgent. The installer is idempotent and can be re-run to repair an
installation.

## Update an installed checkout

After a source install, update the same install in place:

```bash
qubit update --check      # read-only: install method, revision, update available
qubit update --apply      # snapshot, fast-forward, reinstall dependencies, restart
qubit update --rollback   # restore the revision recorded by the last apply
```

With no extra flags the updater follows the checkout's own `origin` and the
configured branch (`main`). Use `--channel stable` for the newest `vX.Y.Z`
release tag, `--channel beta` for pre-releases, or `--tag vX.Y.Z` for one
release. `--apply --channel NAME` persists the channel for later updates under
`$QUBIT_HOME`.

A fresh `scripts/install.sh` install is non-editable, but the updater resolves
the checkout it was installed from, so the first update works without any
editing step. A release-archive/bootstrap install has no git checkout: it
reports `package` and is upgraded by re-running the bootstrap against newer
release assets. The full command and channel reference is
[docs/UPDATE.md](docs/UPDATE.md).

## Bootstrap installer (release assets)

There are two bootstrap entry points with the same contract: a POSIX shell
script for Linux assets and a PowerShell script for Windows assets. Each
downloads a release installer and a release **source archive** plus their
checksums into a temporary directory, verifies the SHA-256 of both, extracts
the archive, verifies that the extracted installer is the same file, and
executes **that extracted copy**. Neither reads a source checkout to install.

```bash
# Linux (POSIX assets)
./scripts/bootstrap.sh --help
./scripts/bootstrap.sh --dry-run --prefix "$HOME/qubit" <release-base>
./scripts/bootstrap.sh --prefix "$HOME/qubit" <release-base>
```

```powershell
# Windows (ZIP assets)
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -Help
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -DryRun C:\releases\v0.0.9
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -QubitHome "$env:LOCALAPPDATA\qubit" C:\releases\v0.0.9
```

`scripts\bootstrap.cmd` is a CMD wrapper that forwards every flag to
`bootstrap.ps1`.

### Release-asset contract

`<release-base>` is an HTTPS URL, a `file://` URL, or a local directory. Both
bootstraps read the same `SHA256SUMS`; each requires only its own asset pair:

| Asset | Platform | Meaning |
|---|---|---|
| `install.sh` | POSIX | The shell installer. It is downloaded and checksum-verified; its hash must equal the `scripts/install.sh` inside the archive. |
| `qubit-release.tar.gz` | POSIX | The release source tree. It must extract to `scripts/install.sh`, either at the archive root or under exactly one top-level directory (for example `Qubit-0.0.1/scripts/install.sh`). Override with `--archive NAME` (or `QUBIT_RELEASE_ARCHIVE`). |
| `install.ps1` | Windows | The PowerShell installer. It is downloaded and checksum-verified; its hash must equal the `scripts/install.ps1` inside the archive. |
| `qubit-release.zip` | Windows | The release source tree as a ZIP. It must extract to `scripts/install.ps1`, either at the archive root or under exactly one top-level directory. Override with `-Archive NAME` (or `QUBIT_RELEASE_ARCHIVE`). |
| `SHA256SUMS` | both | Standard `sha256  filename` lines naming the assets. Each bootstrap ignores entries for the other platform. `--checksum FILE` / `-ChecksumFile FILE` overrides discovery. |

The ZIP format is the Windows contract because Windows PowerShell 5.1 can
extract it without an external `tar` program. The POSIX bootstrap keeps using
`qubit-release.tar.gz`. The Windows ZIP omits tracked symlinks (the extractor
refuses symlink entries; Windows would otherwise need elevated privileges), so
the two archives are not byte-identical payloads even though they carry the
same release tree.

The published Qubit release publishes `SHA256SUMS` entries for `bootstrap.sh`, `bootstrap.ps1`, the wheel, the sdist and the source archive; each bootstrap only requires its own installer/archive pair.

Starting with v0.0.9 the release is gated on the fresh-install contract by
`scripts/release_fresh_install_gate.py`. `scripts/prepare_production_release.py`
builds all six bootstrap payloads (`bootstrap.sh`, `install.sh`,
`qubit-release.tar.gz`, `bootstrap.ps1`, `install.ps1`, `qubit-release.zip`)
plus a `release-gate-report.json`, and refuses to finish unless a throwaway
install creates a root `config.yaml` with the default local Ollama provider and
the packaged dashboard/TUI assets are present:

```bash
python3 scripts/prepare_production_release.py \
  --out /tmp/qubit-production-release --tag v0.0.9 --verify
python3 scripts/release_fresh_install_gate.py \
  --repo . --release-dir /tmp/qubit-production-release \
  --fresh-install --require-fresh-install --json
```

A DEGRADED gate result is never treated as PASS; release preparation runs with
`--require-fresh-install` so an un-runnable fresh install blocks the release.
Real Windows PowerShell 5.1 execution, a real macOS host, signed artifacts and
live-service behavior remain manual checks (see
`docs/V0.0.6-RELEASE-CHECKLIST.md`).

The archive is the install payload: its extracted tree is where `PROJECT_DIR`
resolves, so the installer builds the venv, installs the package, builds/serves
the dashboard, and reads `config/`, `scripts/`, and `qubit_runtime/` from the
extracted release — not from the bootstrap directory. Archives with absolute
paths, drive-qualified paths, or `..` traversal are rejected, and extraction
never writes outside the temporary directory. The Windows extractor also
refuses symlink entries.

`--profile NAME` is exported to the verified installer as `QUBIT_PROFILE`;
`--prefix DIR` is forwarded as `--prefix DIR` on POSIX and as `-QubitHome DIR`
on Windows. `--without-models` / `-NoModels` and `--without-dashboard` /
`-NoDashboard` are forwarded; `--yes` becomes `-NonInteractive` on Windows.
`--with-systemd` is forwarded on POSIX; the Windows bootstrap refuses it with a
typed error because Windows has no systemd/launchd equivalent. A bootstrapped
install puts its venv under the persistent `$QUBIT_HOME`, so the launcher
remains valid after the temporary extraction is removed.

Both bootstraps fail closed on a missing installer, missing archive,
missing/invalid checksum, archive mismatch, extracted-installer mismatch, or an
invalid archive. `--dry-run` / `-DryRun` downloads, extracts, executes, and
writes nothing; it validates the local assets and checksum names. The POSIX
bootstrap supports Linux and refuses macOS/Windows with a typed message; use
`bootstrap.ps1` on Windows. The Windows bootstrap runs under Windows PowerShell
5.1+/PowerShell 7 and the extracted `install.ps1` still enforces its own
platform and prerequisites.

**Public release base:** a release base publishes `bootstrap.sh`, `install.sh`,
`qubit-release.tar.gz`, `bootstrap.ps1`, `install.ps1`, `qubit-release.zip`,
and a `SHA256SUMS` naming all four payloads (`scripts/prepare_release_seed.py`
publishes this set). The stable base is
`https://github.com/cgraph-dev/Qubit/releases/latest/download`; use it with
the matching bootstrap as shown above (download the script first rather than
piping an unverified URL into a shell).

## Verify a published release

`scripts/verify_public_release.py` is a read-only verifier for a published
release. It checks that the release exists and is published, that the tag,
wheel, sdist and source archive agree on the version, that every required asset
is present, that every `SHA256SUMS` entry matches, that both source archives
contain the same `scripts/install.sh` as the `install.sh` asset, and that the
source archive contains no operator home path, `node_modules`/build
directories, or secret files/content.

```bash
python3 scripts/verify_public_release.py --repo cgraph-dev/Qubit --tag v0.0.9
python3 scripts/verify_public_release.py --repo cgraph-dev/Qubit --latest
python3 scripts/verify_public_release.py \
  --release-base https://github.com/cgraph-dev/Qubit/releases/download/v0.0.9

# v0.0.9+: require the Windows bootstrap assets too
python3 scripts/verify_public_release.py \
  --repo cgraph-dev/Qubit --tag v0.0.9 --require-platform-assets
```

Add `--json` for a machine-readable report. The verifier downloads assets into
a temporary directory and deletes it; it never writes to the release, a Git
checkout, or a live service. Exit code 0 means every check passed, 1 means a
PASS/FAIL check failed, and 2 means the request itself was invalid. It verifies
asset integrity and provenance, not a behavioral release verdict.

## Options

| Option | Meaning |
|---|---|
| `--help` | Show options without changing the machine. |
| `--dry-run` | Run preflight and print the install layout; make no changes. |
| `--with-systemd` | Install the user units, then enable and start them. On macOS this is a compatibility alias for `--with-launchd`. |
| `--with-launchd` | Install the macOS gateway LaunchAgent (`ai.qubit.gateway.plist`); macOS only. |
| `--without-models` | Skip model asset downloads and local model provisioning. |
| `--without-dashboard` | Skip the dashboard bundle check and build (headless installs). |
| `--prefix DIR` | Install the new `DIR/voice` tree and the launcher under `DIR` (the voice tree is equivalent to `QUBIT_HOME=DIR`), with the prefix's own XDG directories; a portable install never adopts an old XDG tree. |
| `-y`, `--yes` | Do not prompt before enabling the user services. |
| `--launcher-only` | Repair the `qubit` launcher and exit. |

Environment variables:

- `QUBIT_HOME` — runtime and agent home. Default: `~/.qubit` on Linux/macOS,
  `%LOCALAPPDATA%\qubit` on Windows, or `DIR` when `--prefix` is used.
- `QUBIT_PREFIX` — same as `--prefix` when the flag is absent.
- `QUBIT_NO_LAUNCHER=1` — do not install the `qubit` user launcher.

## Profiles and paths

A new install keeps everything under the Qubit home:

- runtime home and state: `$QUBIT_HOME` (default `~/.qubit`)
- voice config: `$QUBIT_HOME/voice/config/` (`voice.yaml`, `.env`)
- voice data and models: `$QUBIT_HOME/voice/data/`, `$QUBIT_HOME/voice/models/`
- voice state: `$QUBIT_HOME/voice/state/`
- launcher: `~/.local/bin/qubit`

An **existing** install that already has `qubit-voicebridge` XDG directories
(`~/.config/qubit-voicebridge/`, `~/.local/share/qubit-voicebridge/`,
`~/.local/state/qubit-voicebridge/`, `~/.cache/qubit-voicebridge/`) is used in
place, read-old. The installer never migrates, moves, or deletes that data and
does not initialize the new tree beside it. On Windows the read-old roots are
under `%LOCALAPPDATA%\qubit\{config,data,state,cache}\qubit-voicebridge`.

On macOS the core install writes `~/Library/LaunchAgents/ai.qubit.gateway.plist`
(or `ai.qubit.gateway-<profile>.plist`); pre-rebrand `ai.hermes.gateway*` plists
are read-old and never deleted by the installer.

With `--prefix DIR`, `QUBIT_HOME` defaults to `DIR`: the voice tree is
`DIR/voice`, and the launcher is `DIR/bin/qubit`. The installed launcher pins
that resolved `QUBIT_HOME` (as it does for an explicitly set `QUBIT_HOME`), so
running `DIR/bin/qubit` uses `DIR` rather than the real HOME's `~/.qubit`; an
explicit `QUBIT_HOME` at run time still wins. A default (no `--prefix`, no
explicit `QUBIT_HOME`) install leaves the launcher unpinned so it keeps
resolving `~/.qubit`. If `QUBIT_HOME` is also set explicitly, runtime state
stays at that path even when it differs from the prefix; `--dry-run` prints the
exact resolved layout before anything changes.

## Offline note

A normal install needs network access for Python packages, model assets, and a
dashboard build when the bundle is missing. On an offline machine, prepare the
Python packages and environment in advance, pass `--without-models` and
`--without-dashboard` to skip the network-dependent steps, and provide any model
assets separately. Model downloads that do run are verified against pinned
checksums and fail closed on a mismatch.
