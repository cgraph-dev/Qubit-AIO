# Install Qubit

Qubit installs from source on Linux and macOS, and through a PowerShell
installer on Windows. Linux is the reference platform. On macOS the core
runtime, CLI, dashboard and the messaging-gateway LaunchAgent are supported;
the voice bridge, hotkey, orb and voice-typing are Linux-only. The Windows
installer is provided but has not been validated on a Windows release runner.

## Prerequisites

Required:

- Linux (Ubuntu x86_64) or macOS for the shell installer; Windows PowerShell
  5.1+ for `scripts\install.ps1`.
- `bash`, `curl`, and `uv` (the installer uses `uv` to create the virtual
  environment).
- Python **3.12 or newer, below 3.15**. The installer creates the environment
  with Python 3.12 through `uv`; the project metadata accepts
  `>=3.12,<3.15`.
- At least **4 GB RAM** and **2 GB free disk** for the base install. Model
  assets need additional disk space.

Optional:

- `ollama` for local model provisioning.
- `tar` plus `sha256sum`, `shasum`, or `openssl` when model assets are
  downloaded; the installer verifies pinned checksums.
- Node.js **22 or newer** and `npm` when the dashboard bundle
  (`frontend/qubit-dist`) is absent and must be built.

## Install

```bash
git clone https://github.com/cgraph-dev/Qubit-AIO.git
cd Qubit-AIO

./scripts/install.sh --help       # options; changes nothing
./scripts/install.sh --dry-run    # preflight and resolved layout; no writes
./scripts/install.sh              # venv, dependencies, config, launcher
```

The installer is idempotent and can be re-run to repair an installation.

## Options

| Option | Meaning |
|---|---|
| `--help` | Show options without changing the machine. |
| `--dry-run` | Run preflight and print the install layout; make no changes. |
| `--with-systemd` | Install the user units, then enable and start them. |
| `--without-models` | Skip model asset downloads and local model provisioning. |
| `--without-dashboard` | Skip the dashboard bundle check and build (headless installs). |
| `--prefix DIR` | Install the new `DIR/voice` tree and the launcher under `DIR` (equivalent to setting `QUBIT_HOME=DIR`); a portable install never adopts an old XDG tree. |
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
- voice config: `$QUBIT_HOME/voice/config/`
- voice data and models: `$QUBIT_HOME/voice/data/`, `$QUBIT_HOME/voice/models/`
- voice state: `$QUBIT_HOME/voice/state/`
- launcher: `~/.local/bin/qubit`

An **existing** install that already has `qubit-voicebridge` XDG directories
(`~/.config/qubit-voicebridge/`, `~/.local/share/qubit-voicebridge/`,
`~/.local/state/qubit-voicebridge/`, `~/.cache/qubit-voicebridge/`) is used in
place, read-old. The installer never migrates, moves, or deletes that data and
does not initialize the new tree beside it. On Windows the read-old roots are
under `%LOCALAPPDATA%\qubit\{config,data,state,cache}\qubit-voicebridge`.

With `--prefix DIR`, `QUBIT_HOME` defaults to `DIR`: the voice tree is
`DIR/voice`, and the launcher is `DIR/bin/qubit`. If `QUBIT_HOME` is also set
explicitly, runtime state stays at that path even when it differs from the
prefix; `--dry-run` prints the exact resolved layout before anything changes.

## Offline note

A normal install needs network access for Python packages, model assets, and a
dashboard build when the bundle is missing. On an offline machine, prepare the
Python packages and environment in advance, pass `--without-models` and
`--without-dashboard` to skip the network-dependent steps, and provide any model
assets separately. Model downloads that do run are verified against pinned
checksums and fail closed on a mismatch.
