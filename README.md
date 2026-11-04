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
| macOS | Not supported in this release (future target) |
| Windows | Not supported in this release (future target) |

The installer, service integration, and voice/audio path are Linux-first. Do not
expect macOS or Windows to work in this release.

## Install

One line (Linux):

```bash
git clone https://github.com/cgraph-dev/Qubit-AIO.git && cd Qubit-AIO && ./scripts/install.sh
```

Use the Qubit download URL if it differs from the repository above. For options,
prerequisites, profiles, and offline notes, see [INSTALL.md](INSTALL.md).

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
