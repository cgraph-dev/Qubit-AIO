# Qubit

> Nobody knows what it will become.

Qubit is a personal AI agent — an all-in-one assistant that works as your coding
agent, caretaker, teacher, personal bot, and business organizer. It runs on your
machine and gives you one agent for text, terminal, browser, files, coding tasks,
and optional voice.

## Features

- Local-first agent runtime with tools for files, terminal, browser, and coding work.
- One agent and one home for multiple roles; text and voice use the same backend.
- Web dashboard for chat, sessions, files, models, plugins, and system status.
- Optional push-to-talk voice: wake word, barge-in, spoken replies, and voice identity.
- Confirmation-gated actions for destructive or privileged work.
- Optional local model provisioning; cloud providers are only used when configured.
- Extensible tools, skills, and plugins.

## Supported platforms

| Platform | Status |
|---|---|
| Linux (Ubuntu x86_64) | Supported |
| macOS | Core install supported; voice bridge/orb/typing not yet supported |
| Windows | Installer provided; Windows install E2E still pending (no Windows release runner) |

Linux is the reference platform. macOS installs the core runtime, CLI, dashboard,
and launchd gateway; voice/audio is not supported there yet. Windows has a
PowerShell installer but has not been validated on a Windows release runner.

## Install

One line (Linux, full install including voice):

```bash
git clone https://github.com/cgraph-dev/Qubit.git && cd Qubit && ./scripts/install.sh
```

A source install builds the dashboard bundle with Vite 8, so it needs
Node.js 22.12+ and `npm`. Pass `--without-dashboard` for a headless install,
or use a release archive that already contains `frontend/qubit-dist`.

macOS core install (runtime, CLI, dashboard and the gateway LaunchAgent; the
voice bridge/orb/typing stay unsupported):

```bash
git clone https://github.com/cgraph-dev/Qubit.git && cd Qubit && ./scripts/install.sh --with-launchd
```

For options, prerequisites, profiles, and offline notes, see
[INSTALL.md](INSTALL.md).

Release-asset bootstrap (no checkout, verified source archive):

```bash
curl -fsSL https://github.com/cgraph-dev/Qubit/releases/latest/download/bootstrap.sh \
  -o /tmp/qubit-bootstrap.sh
bash /tmp/qubit-bootstrap.sh https://github.com/cgraph-dev/Qubit/releases/latest/download
```

The bootstrap downloads `install.sh` and `qubit-release.tar.gz`, verifies both
SHA-256 values against `SHA256SUMS`, extracts the archive, and runs the verified
installer from that extracted tree. See
[INSTALL.md](INSTALL.md#bootstrap-installer-release-assets).

Windows has the same contract with `bootstrap.ps1` / `bootstrap.cmd`,
`install.ps1`, and `qubit-release.zip` (ZIP so Windows PowerShell 5.1 does not
need an external `tar`):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -Help
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -DryRun C:\releases\v0.0.6
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -QubitHome "$env:LOCALAPPDATA\qubit" C:\releases\v0.0.6
```

`-Prefix DIR` is accepted as the same root as `-QubitHome`; `-Yes` becomes
`-NonInteractive`. `-WithSystemd` is refused with a typed error because Windows
has no systemd/launchd equivalent.

Verify a published release without installing (read-only):

```bash
python3 scripts/verify_public_release.py --repo cgraph-dev/Qubit --tag v0.0.6
```

It checks the release identity, every required asset and checksum, the archive
installer match, and the source archive for operator paths, `node_modules`, and
secrets. See [INSTALL.md](INSTALL.md#verify-a-published-release).

New installs keep runtime state and voice under `$QUBIT_HOME`
(`~/.qubit` on Linux/macOS, `%LOCALAPPDATA%\qubit` on Windows), with voice
config, data, state, cache and models under `$QUBIT_HOME/voice`. An existing
`qubit-voicebridge` XDG install is read in place and is never migrated,
moved, or deleted by the installer.

Windows entry (Qubit-owned; never clones the upstream tree):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Help
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -QubitHome "%LOCALAPPDATA%\qubit" -Channel stable
```

To install from a published release base without a checkout, use the verified
Windows bootstrap instead:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -QubitHome "%LOCALAPPDATA%\qubit" https://github.com/cgraph-dev/Qubit/releases/latest/download
```

Flags: `-QubitHome DIR`, `-Channel stable|main`, `-NoModels`, `-NoDashboard`.
`scripts\install.cmd` is the CMD wrapper for the same entry. Windows CI parses
the installer, runs `-Help`/`-DryRun`, and performs a no-model/no-dashboard
checkout install; the pinned model downloads, dashboard build, and
update/uninstall paths remain unverified on Windows.

## Quickstart

```bash
./scripts/install.sh --dry-run   # optional preflight
./scripts/install.sh
qubit doctor run
qubit status
qubit run                        # push-to-talk loop
```

Open the dashboard at <http://127.0.0.1:8890/> (served by the bridge started by
`qubit run`). It binds to loopback by default.
For voice identity, enroll your voice once before the first voice turn:

```bash
qubit enroll
```

## Update

A source checkout updates in place; the updater finds the checkout a fresh
`scripts/install.sh` install was built from:

```bash
qubit update --check      # read-only status and available update
qubit update --apply      # snapshot, fast-forward, reinstall, restart
qubit update --rollback   # restore the previous revision
```

`--channel stable` follows the newest `vX.Y.Z` release tag; the default
follows the configured branch (`main`). See [docs/UPDATE.md](docs/UPDATE.md)
for channels, tags, install shapes and limitations.

## What is optional

- Local model assets: skip with `--without-models`.
- Dashboard bundle build: skip with `--without-dashboard` (a source checkout
  does not include the prebuilt bundle).
- Auto-start user services: add `--with-systemd`.
- Voice hardware and voice identity: the text and dashboard paths work without them.
- Cloud provider keys: see `.env.example`; the core local path does not require them.

## Documentation

- [INSTALL.md](INSTALL.md) — install, prerequisites, options, offline notes.
- [docs/UPDATE.md](docs/UPDATE.md) — update channels, tags, apply/rollback, install shapes.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — dashboard, models, local brain (VRAM), audio, dependencies, uninstall.
- [SECURITY.md](SECURITY.md) — vulnerability reporting and scope.
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and pull requests.

## License

Original Qubit material is covered by the terms in [LICENSE](LICENSE): a
personal, non-commercial educational license, not an OSI-approved open-source
license. Third-party and upstream-derived material keeps its original license;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
