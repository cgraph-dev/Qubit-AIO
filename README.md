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
| Windows | Installer provided; Windows release validation is still pending |

Linux is the reference platform. macOS installs the core runtime, CLI, dashboard,
and launchd gateway; voice/audio is not supported there yet. Windows has a
PowerShell installer but has not been validated on a Windows release runner.

## Install

One line (Linux):

```bash
git clone https://github.com/cgraph-dev/Qubit-AIO.git && cd Qubit-AIO && ./scripts/install.sh
```

Use the Qubit download URL if it differs from the repository above. For options,
prerequisites, profiles, and offline notes, see [INSTALL.md](INSTALL.md).

Checkout-free path (once a release publishes its installer asset):
`scripts/bootstrap.sh <release-base>` downloads the release installer, verifies
its SHA-256, and runs only the verified copy. See
[INSTALL.md](INSTALL.md#bootstrap-installer-release-assets).

Windows entry (Qubit-owned; never clones the upstream tree):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Help
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -QubitHome "%LOCALAPPDATA%\qubit" -Channel stable
```

Flags: `-QubitHome DIR`, `-Channel stable|main`, `-NoModels`, `-NoDashboard`.
`scripts\install.cmd` is the CMD wrapper for the same entry.

## Quickstart

```bash
./scripts/install.sh --dry-run   # optional preflight
./scripts/install.sh
qubit doctor run
qubit status
qubit run                        # push-to-talk loop
```

Open the dashboard at <http://127.0.0.1:8890/>. It binds to loopback by default.
For voice identity, enroll your voice once before the first voice turn:

```bash
qubit enroll
```

## What is optional

- Local model assets: skip with `--without-models`.
- Dashboard bundle: skip with `--without-dashboard`.
- Auto-start user services: add `--with-systemd`.
- Voice hardware and voice identity: the text and dashboard paths work without them.
- Cloud provider keys: see `.env.example`; the core local path does not require them.

## Documentation

- [INSTALL.md](INSTALL.md) — install, prerequisites, options, offline notes.
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — dashboard, models, audio, dependencies, uninstall.
- [SECURITY.md](SECURITY.md) — vulnerability reporting and scope.
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and pull requests.

## License

Original Qubit material is covered by the terms in [LICENSE](LICENSE).
Third-party and upstream-derived material keeps its original license; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
