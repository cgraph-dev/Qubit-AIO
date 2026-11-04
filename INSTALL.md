# Install Qubit

Qubit is installed from source on Linux. The supported target is **Ubuntu
x86_64**. macOS and Windows are documented future targets and are **not
supported in this release**.

## Prerequisites

Required:

- Linux (Ubuntu x86_64).
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
| `--prefix DIR` | Put data, config, state, and the launcher under `DIR` instead of the XDG defaults. |
| `-y`, `--yes` | Do not prompt before enabling the user services. |
| `--launcher-only` | Repair the `qubit` launcher and exit. |

Environment variables:

- `QUBIT_HOME` — runtime and agent home. Default: `~/.qubit`, or `DIR` when
  `--prefix` is used.
- `QUBIT_PREFIX` — same as `--prefix` when the flag is absent.
- `QUBIT_NO_LAUNCHER=1` — do not install the `qubit` user launcher.

## Profiles and paths

By default the installer uses XDG paths:

- config: `~/.config/qubit-voicebridge/`
- data and models: `~/.local/share/qubit-voicebridge/`
- state: `~/.local/state/qubit-voicebridge/`
- launcher: `~/.local/bin/qubit`
- runtime home: `~/.qubit`

With `--prefix DIR`, those data, config, state, and launcher paths move under
`DIR`, and the installer prints the exact layout. If `QUBIT_HOME` is set
explicitly, runtime state stays at that path even when it differs from the
prefix.

## Offline note

A normal install needs network access for Python packages, model assets, and a
dashboard build when the bundle is missing. On an offline machine, prepare the
Python packages and environment in advance, pass `--without-models` and
`--without-dashboard` to skip the network-dependent steps, and provide any model
assets separately. Model downloads that do run are verified against pinned
checksums and fail closed on a mismatch.
