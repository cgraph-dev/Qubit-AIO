# Packet 7D — Qubit Voice Agent settings, provider registry, dashboard/CLI parity, branding

Implemented in `voicebridge/profiles.py`, `voicebridge/cli.py` (`profile`,
`branding`), `voicebridge/webui.py` (`/api/profiles/*`, `/api/audio/*`,
`/api/permissions`, `/api/device/*`, `/api/export-state`, `/api/branding`,
`/settings`), `frontend/settings.html`, `frontend/index.html`, and
`tests/test_packet7d.py`. No 7C behavior is rewritten, deleted, or weakened;
this packet adds a beginner-friendly control center on top of the verified
7C package, portable-state, audio, model-verification, enrollment, security,
and rollback contracts.

## Provider/profile registry

`voicebridge profile ...` (dry-run by default, JSON output):

```bash
voicebridge profile list                # registered profiles + active selection
voicebridge profile show brain          # active profile for one component
voicebridge profile check brain [--profile id]   # run every gate (read-only)
voicebridge profile install manifest.json         # register a validated manifest
voicebridge profile select brain qubit-brain4 [--apply --confirm]
voicebridge profile reset brain [--apply]
voicebridge profile upgrade brain [--apply]
voicebridge profile remove brain <id> [--apply]
voicebridge profile rollback brain [--apply]
```

Components: **brain** (reasoning model), **stt** (engine/model), **tts**
(provider), **voice** (artifact), **audio** (input/output devices). Safe
defaults unchanged: `qubit-brain4`, sherpa CPU STT, Piper, `en_US-amy-medium`,
and OS-default audio.

Every `ProviderManifest` records and validates:

- source + exact revision;
- license;
- checksum/digest (sha256 hex when present);
- supported hardware (cpu/gpu/any) and languages;
- VRAM/RAM usage (5 GB ceiling enforced, fail closed);
- latency (measured: recorded default values or a bounded live synthesis;
  a 0.0/unmeasured value is PARTIAL, never a silent pass);
- quality and safety evidence (the referenced files must exist and be
  non-empty — validated, not just recorded);
- rollback target.

A downloaded file or model name is never trusted automatically: install/select/
upgrade run manifest, artifact (local sha256 or live Ollama digest),
evidence, latency, hardware/language, and resource gates. A failed gate leaves
the previous profile active (no config write). A replacement (non-default)
profile must pass EVERY gate — a PARTIAL gate blocks activation; only the
verified default may activate with a PARTIAL caveat. **One approved resident
generative model by default**; any replacement that exceeds the 5 GB VRAM
ceiling or fails a gate is rejected. Non-brain STT/TTS/voice artifacts are
resolved to their on-disk files and checksum-verified (or reported as
runtime-only / unmeasured).

Operations are dry-run by default, explicitly applied, staged atomically with a
timestamped backup, and reversible via rollback. **Changing one component never
silently changes another** (voice vs TTS vs model vs audio devices vs
permissions), and provider changes never alter memory, reports, or approvals.
No service restart and no recording enablement happen during settings changes.

## Dashboard / CLI parity

`frontend/settings.html` is the authenticated control center (loopback + header
token, preserving Packet 1B). It provides:

- beginner onboarding with plain-language safe defaults;
- an **Advanced** toggle exposing technical details;
- model/brain, STT, TTS provider, voice, and audio-device selection;
- permission review, device enrollment, health/hardware status, task progress,
  approvals, reports/audit, **export and import**, per-component upgrade
  (a real re-verify + manifest-metadata refresh), reset, **non-default profile
  removal**, and rollback.

Every control calls a real authenticated backend endpoint (`/api/profiles/*`,
`/api/audio/*`, `/api/permissions`, `/api/device/*`, `/api/export-state`,
`/api/import-state`, `/api/branding`, `/api/status`, `/api/approvals`,
`/api/reports`). No frontend-only localStorage is used as a source of truth
(only the existing `vdb_token` auth token is read from localStorage). A
**confirmation dialog** gates every side-effecting action (provider change,
reset, rollback, remove, enrollment, export, import restore) and maps to the
backend `confirm` flag — the backend DENIED path is exercised. Success is never
shown when the backend returns unavailable/degraded/denied/partial/failed; a
typed-state interpreter renders each state and marks failures with "no change
made". Untrusted text is escaped (`esc()`); no secrets, credentials, prompts,
raw filesystem paths, or CDP URLs are exposed.

## Qubit branding migration

`voicebridge branding plan|verify|rollback` performs the
`openclaw-voice-bridge -> qubit-voice-agent` rename as a **separate reversible
migration** (not incidental cleanup):

- `branding plan` (dry-run) lists every file reference change and the runtime
  path steps.
- `branding plan --apply --backup-dir <dir>` rewrites the repository files
  (package metadata, systemd unit content + **renames the unit file** to
  `qubit-voice-agent.service`, launchers, docs, config template) atomically,
  backing up every touched file. The live config/state/data path migration is
  an **explicit separate step** (`--migrate-paths`): it moves the content to
  the new path and creates a compat alias, and is never triggered by a repo
  apply or a staged test.
- **Applied 2026-08-27**: the LIVE repository is now branded (`branding verify`
  returns **VERIFIED**, 9/9 files) with a complete timestamped backup under
  `packet7d-branding-backup-20260827-174709/` (repo.bundle, worktree tar, and
  runtime config/state/data). The migration was run WITHOUT `--migrate-paths`,
  so the **running bridge and its user config/state remain at
  `openclaw-voice-bridge`** (`~/.config/openclaw-voice-bridge`,
  `~/.local/state/openclaw-voice-bridge`, installed unit
  `openclaw-voice-bridge.service`). The product identity (package name, repo
  systemd template `systemd/qubit-voice-agent.service`, launchers, docs)
  now says `qubit-voice-agent`; the live runtime paths are the documented
  pre-migration compatibility location and were NOT moved. To move the runtime
  to the new paths, run `branding plan --apply --migrate-paths` as a separate,
  explicitly authorized step (backed up and reversible).
- `branding verify` scans every file and path and returns **VERIFIED** when all
  references agree on `qubit-voice-agent`, **PARTIAL** when some remain, or
  **PLANNED** when none are migrated (read-only; exit 0 only when VERIFIED).
  After the 2026-08-27 apply it returns **VERIFIED** on the live repo.
- `branding rollback --backup-dir <dir>` restores every backed-up repo file
  and runtime config/state/data, renames the unit back, and removes compat
  aliases.

The repository is never physically renamed as incidental cleanup; existing
memory, reports, permissions, device settings, and model metadata are
preserved. The real repo stays on `openclaw-voice-bridge` until an operator
runs the migration; verification reports PARTIAL in that state, never VERIFIED.

## Tests

`tests/test_packet7d.py` (44): registry safe defaults + required metadata,
manifest validation (invalid/checksum/VRAM/hardware), profile check gates
(typed, incl. evidence + latency), dry-run no-write, confirmation DENIED,
unknown profile MISSING, apply + backup, reset, rollback (backup + default),
remove default/active DENIED + real non-default removal, restart persistence,
independent component changes, **real branding migration applied/verified/
rolled back on a staged repo** (verify returns VERIFIED after apply, PARTIAL/
PLANNED before), CLI list/show/check/install/select/reset/rollback/upgrade,
**real dashboard import (preview + apply + wrong-passphrase)**, **real
upgrade (refreshes manifest metadata)**, **real removal**, dashboard auth
(401 on wrong token), profiles/audio/permissions/device/export endpoints,
no-leak/no-fake-success, settings page + no-frontend-only-state / confirmation
dialog / no-demo assertions. Full suite 809 passed / 0 failed / 0 skipped.

## Rollback

Revert the Packet 7D commit; remove `voicebridge/profiles.py`, the `profile`/
`branding` CLI, the `/api/profiles*`/`/api/audio*`/`/api/permissions`/
`/api/device*`/`/api/export-state`/`/api/branding` routes, `frontend/
settings.html`, the `/settings` route, and `tests/test_packet7d.py`. No live
config, service, browser, model, or audio state is changed by any 7D command;
profile operations keep `.profile.*.bak` backups, and branding apply keeps a
backup dir for rollback.