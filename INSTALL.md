# Install Qubit

Qubit installs from source on Linux and macOS. Linux (**Ubuntu x86_64**) is
the reference platform and includes the full voice stack. macOS supports the
**core** install only: runtime, CLI, dashboard and the messaging-gateway
LaunchAgent. The voice bridge, push-to-talk hotkey, orb and voice-typing are
**not supported on macOS** and are reported as `UNSUPPORTED`; no voice models
are downloaded there. Windows has a PowerShell installer but is not validated
on a Windows release runner yet.

## Prerequisites

Required:

- Linux (Ubuntu x86_64) or macOS. On macOS the core runtime/CLI/dashboard and
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

Optional:

- `ollama` for local model provisioning.
- `tar` plus `sha256sum`, `shasum`, or `openssl` when model assets are
  downloaded; the installer verifies pinned checksums. These assets are
  Linux-only: macOS never downloads the voice models.
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

On macOS, install the core and its gateway LaunchAgent with:

```bash
./scripts/install.sh --with-launchd
```

`--with-systemd` is accepted on macOS as a compatibility alias for the
LaunchAgent. The installer is idempotent and can be re-run to repair an
installation.

## Options

| Option | Meaning |
|---|---|
| `--help` | Show options without changing the machine. |
| `--dry-run` | Run preflight and print the install layout; make no changes. |
| `--with-systemd` | Install the user units, then enable and start them. On macOS this is a compatibility alias for `--with-launchd`. |
| `--with-launchd` | Install the macOS gateway LaunchAgent (`ai.qubit.gateway.plist`); macOS only. |
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

On macOS the core install uses the same `~/.qubit` runtime home and writes
`~/Library/LaunchAgents/ai.qubit.gateway.plist` (or
`ai.qubit.gateway-<profile>.plist` for a named profile). Pre-rebrand
`ai.hermes.gateway*` plists are read-old and never deleted by the installer.
The voice bridge/orb/typing services and their model assets do not exist on
macOS.

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
