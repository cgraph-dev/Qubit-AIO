# Packet 8S — Qubit VoiceBridge identity and release migration (operations)

Canonical public identity: **Qubit VoiceBridge**.
Canonical installed/repository identity: `qubit-voicebridge`.
Internal Python import package (unchanged): `voicebridge`.
Legacy compatibility identities: `openclaw-voice-bridge` (original) and
`qubit-voice-agent` (7D intermediate). Both resolve to the canonical tree
through explicit compat aliases; the runtime never reads one tree while
writing another.

## 1. Identity map

| Surface | Canonical (8S) | Legacy (compat only) |
|---|---|---|
| Product title | Qubit VoiceBridge | Qubit Voice Agent / OpenClaw Voice Bridge (historical text only) |
| Installed paths | `~/.config/qubit-voicebridge`, `~/.local/share/qubit-voicebridge`, `~/.local/state/qubit-voicebridge`, `~/.cache/qubit-voicebridge` | `.../openclaw-voice-bridge`, `.../qubit-voice-agent` as symlinks to canonical |
| systemd unit | `qubit-voicebridge.service` | `openclaw-voice-bridge.service`, `qubit-voice-agent.service` (stale/duplicate detection only) |
| Desktop file | `qubit-voicebridge.desktop` (`Name=Qubit VoiceBridge`) | removed on fresh install; never duplicated |
| Package | `qubit-voicebridge` (pyproject/distribution/release) | old names rejected by install checks |
| Import package | `voicebridge` (unchanged — never renamed) | n/a |
| Repo folder | `qubit-voicebridge` | `openclaw-voice-bridge` checkout stays readable during migration |
| Legacy systemd template | `systemd/qubit-voice-agent.service` kept as a readable legacy reference (never installed alongside the canonical unit) | installing both units reports `DUPLICATE` |

## 2. Migration (`voicebridge product migrate`)

- Dry-run first: `voicebridge product migrate` (no writes).
- Apply: `voicebridge product migrate --apply [--backup-dir DIR]`.
- The migration backs up every legacy config/state/data tree first
  (credential-shaped files are NEVER copied into the backup; the live move
  preserves all bytes), writes a validated backup manifest, moves each real
  legacy directory to its canonical path, and leaves a symlink compat alias.
- Idempotent: re-running resumes interrupted migrations (a canonical tree
  without its alias is completed, never duplicated).
- Rollback: `voicebridge product migrate-rollback --apply --backup-dir DIR`
  removes the aliases, moves canonical trees back, and restores missing files
  from the validated backup through a staged, fsynced, atomic-rename
  pipeline. Corrupt/newer backups fail closed with exact paths.
- Exactly one service must own one listener: if both canonical and legacy
  units exist the service diagnostic reports `DUPLICATE`; if only a legacy
  unit exists it reports `STALE`. Migration never enables, restarts, or
  edits a live unit.

## 3. Repository-folder rename rehearsal (never move the active checkout)

The physical rename `openclaw-voice-bridge` -> `qubit-voicebridge` is part of
this packet but MUST NOT move the active dirty checkout under a running
agent or service. Operator procedure:

1. From a clean clone or release directory (not the live checkout):
   `cp -a openclaw-voice-bridge qubit-voicebridge` (or `git clone` into a
   `qubit-voicebridge` directory).
2. Inside `qubit-voicebridge`: build/install into a clean HOME, run
   `voicebridge product install` (dry-run), the 8S hermetic suite, and one
   launch (`voicebridge webui --open` resolves; no second listener).
3. Prove packaging (`pyproject.toml` name `qubit-voicebridge`), scripts, the
   `systemd/qubit-voicebridge.service` template, and docs resolve from the
   new path, and upgrade/rollback work from it.
4. Only then move the existing checkout manually with all services stopped:
   `systemctl --user stop qubit-voicebridge.service`,
   `mv openclaw-voice-bridge qubit-voicebridge`, re-run install + tests.
5. An existing `openclaw-voice-bridge` checkout remains safely readable
   during migration; nothing in the migration deletes user data.

## 4. What is never done

- Never renames the `voicebridge` import package.
- Never changes capability policy, browser/account/audio/model/timer
  behavior, or Packet 8R execution boundaries.
- Never adds a dependency, listener, second service, or MCP bypass.
- Never overwrites a real legacy directory, follows an unsafe symlink,
  deletes user data, or copies secrets into logs, URLs, shortcuts, reports,
  artifacts, or metadata.
- Never uses `git reset --hard`, `git clean`, or broad checkout; rollback
  uses `git revert` or reviewed file-specific patches.

## 5. Verification matrix (hermetic + isolated)

Fresh install, canonical-only, legacy-only (each legacy family), mixed/split
trees, partial/interrupted migration + resume, repeated migration, upgrade,
rollback/downgrade, uninstall plan (state preserved), read-only and disk-full
failures, symlink/path-traversal attempts, service restart states, shortcut
launch (dry-run), preservation of configs/state/memory/reports/browser/audio
settings, exactly-one-effective-tree proof, and no-secret-in-backup/logs.
See `tests/test_packet8s.py`.
