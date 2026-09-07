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

## 3. Repository-folder migration (performed 2026-09-07)

The canonical folder is `/home/trick/Projects/Repos/qubit-voicebridge`.
The migration was performed WITHOUT `git reset --hard`, `git clean`, broad
checkout, mass deletion, or destructive overwrite:

1. Recorded `git status` plus SHA-256 of every tracked-dirty and untracked
   file (baseline kept outside the repo).
2. Copied the tree with `rsync -a` (excluding only rebuildable `.venv/`,
   `graft/`, `__pycache__/`, `.pytest_cache/`), then proved the copy
   byte-identical (`diff -r`, per-file hashes).
3. Recreated the venv in the new folder (`uv venv`, `uv pip install
   -e ".[test]"`) — the editable install is now `qubit_voicebridge-1.0.0`.
4. Renamed the old checkout aside to
   `openclaw-voicebridge.legacy-backup-<date>` (recoverable, NOT a second
   active checkout: no service, test, or runtime resolves it).
5. All verification below ran from the canonical folder.

## 4. Rollback procedures

- Runtime migration rollback: `voicebridge product migrate-rollback --apply
  --backup-dir <manifest-backup>` (locked, staged, fsynced, atomic;
  corrupt/newer backups fail closed; partial restores report exact paths
  and block resume).
- Repository-folder rollback: stop services, `mv qubit-voicebridge
  qubit-voicebridge.failed-8s`, `mv
  openclaw-voicebridge.legacy-backup-<date> openclaw-voice-bridge`,
  re-create or re-point the venv, re-run the hermetic suite. Keep the
  backup until an independent verifier returns VERIFIED.
- Code rollback: `git revert <8S-commit>` (never `reset --hard`).

## 5. What is never done

- Never renames the `voicebridge` import package.
- Never changes capability policy, browser/account/audio/model/timer
  behavior, or Packet 8R execution boundaries.
- Never adds a dependency, listener, second service, or MCP bypass.
- Never overwrites a real legacy directory, follows an unsafe symlink,
  deletes user data, or copies secrets into logs, URLs, shortcuts, reports,
  artifacts, or metadata.
- Never uses `git reset --hard`, `git clean`, or broad checkout; rollback
  uses `git revert` or reviewed file-specific patches.

## 6. Verification matrix (hermetic + isolated)

Fresh install, canonical-only, legacy-only (each legacy family), mixed/split
trees, partial/interrupted migration + resume, repeated migration, upgrade,
rollback/downgrade, uninstall plan (state preserved), read-only and disk-full
failures, symlink/path-traversal attempts, service restart states, shortcut
launch (dry-run), preservation of configs/state/memory/reports/browser/audio
settings, exactly-one-effective-tree proof, and no-secret-in-backup/logs.
See `tests/test_packet8s.py`.
