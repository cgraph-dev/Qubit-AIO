# Changelog

All notable changes to Qubit VoiceBridge are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [1.0.0] - 2026-11-03

### Added

- Local push-to-talk voice interface with wake-word, barge-in, and spoken responses.
- Qubit agent routing for chat, coding tasks, tools, and local actions.
- Live Office surface with rooms, agents, permissions, messages, and confirmation-gated writes.
- Dashboard routes for chat, models, plugins, webhooks, memory, files, and system status.
- Reference behavioral parity for the frozen upstream target.

### Verified

- Full release matrix: 73,250 tests passed, 1 loaded-host timing flake (green alone),
  1,064 skipped.
- Live voice weather, open-website, and stop/barge-in checks passed.
- Post-reboot dashboard chat and Office smoke checks passed.
