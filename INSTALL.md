# Install Qubit

Qubit installs from source on Linux and macOS, and through a PowerShell
installer on Windows. Linux is the reference platform and includes the full
voice stack. macOS supports the core runtime, CLI, dashboard and the
messaging-gateway LaunchAgent; the voice bridge, hotkey, orb and voice-typing
are Linux-only. The Windows PowerShell installer is provided but has not been
validated on a Windows release runner.

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
git clone https://github.com/cgraph-dev/Qubit-AIO.git
cd Qubit-AIO

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

## Bootstrap installer (release assets)

`scripts/bootstrap.sh` is the release-asset entry point for a published
release. It downloads the release installer (`install.sh`) and its checksum
into a temporary directory, verifies the SHA-256, and executes **only** the
verified copy. It never reads a source checkout to install.

```bash
./scripts/bootstrap.sh --help
./scripts/bootstrap.sh --dry-run --prefix "$HOME/qubit" <release-base>
./scripts/bootstrap.sh --prefix "$HOME/qubit" <release-base>
```

`<release-base>` is an HTTPS URL, a `file://` URL, or a local directory that
contains `install.sh` and `SHA256SUMS` (or `install.sh.sha256` /
`install.sh.sha256sum`).
`--checksum FILE` overrides checksum discovery. `--profile NAME` is exported
to the verified installer as `QUBIT_PROFILE`; `--prefix DIR` is forwarded as
`--prefix DIR`.

The bootstrap fails closed on a missing download, missing/invalid checksum, or
a checksum mismatch. `--dry-run` downloads nothing, executes nothing, and does
not touch `QUBIT_HOME`. Linux is supported now; macOS and Windows are typed
refusals until their bootstrap paths exist.

The published `install.sh` asset must be **self-contained**. The bootstrap runs
only that downloaded file from a temporary directory, so a checkout-relative
installer — this repository's `scripts/install.sh`, which builds its venv and
installs the package from the source tree beside it (`PROJECT_DIR`) — is not a
valid standalone release asset. Publish a release installer that fetches the
release payload itself, or extend the bootstrap to download a release archive.

**Remaining public-hosting prerequisite:** the release must publish the
bootstrap script itself plus a self-contained `install.sh` asset and its
SHA-256 checksum at the release base. This repository does not yet host a
public one-line URL.

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
