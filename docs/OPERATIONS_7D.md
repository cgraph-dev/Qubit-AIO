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
- latency;
- quality and safety evidence references;
- rollback target.

A downloaded file or model name is never trusted automatically: install/select/
upgrade run manifest, artifact (digest/checksum), hardware/language, and
resource gates. A failed gate leaves the previous profile active (no config
write). **One approved resident generative model by default**; any replacement
that exceeds the 5 GB VRAM ceiling or fails a gate is rejected.

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
  approvals, reports/audit, export/import, upgrade, reset, remove, rollback.

Every control calls a real authenticated backend endpoint (`/api/profiles/*`,
`/api/audio/*`, `/api/permissions`, `/api/device/*`, `/api/export-state`,
`/api/branding`, `/api/status`, `/api/approvals`, `/api/reports`). No
frontend-only localStorage is used as a source of truth (only the existing
`vdb_token` auth token is read from localStorage). Success is never shown when
the backend returns unavailable/degraded/denied/partial/failed; each control
renders the typed state. Untrusted text is escaped (`esc()`); no secrets,
credentials, prompts, raw filesystem paths, or CDP URLs are exposed.

## Qubit branding migration

`voicebridge branding plan|verify|rollback` prepares the
`openclaw-voice-bridge -> qubit-voice-agent` rename as a **separate reversible
migration** (not incidental cleanup):

- `branding plan` (dry-run) lists every reference change: package name, service
  unit, config/state/data paths, launchers, fixtures, compatibility aliases.
- `branding plan --apply --backup-dir <dir>` copies the current config/state/
  data to a timestamped backup without moving or deleting live state.
- `branding verify` is a read-only consistency check (package, service,
  config_dir, state_dir refs).
- `branding rollback --backup-dir <dir>` restores the backups.

The repository is never physically renamed as incidental cleanup; existing
memory, reports, permissions, device settings, and model metadata are
preserved.

## Tests

`tests/test_packet7d.py` (39): registry safe defaults + required metadata,
manifest validation (invalid/checksum/VRAM/hardware), profile check gates
(typed), dry-run no-write, confirmation DENIED, unknown profile MISSING, apply
+ backup, reset, rollback (backup + default), remove default/active DENIED,
restart persistence, independent component changes, branding plan/apply/
verify/rollback, CLI list/show/check/install/select/reset/rollback/upgrade,
dashboard auth (401 on wrong token), profiles list/show/check/apply/reset/
rollback, audio/permissions/device endpoints, branding + export-state DENIED,
settings page served, no secret/path leakage, and no-frontend-only-state /
escaping assertions. Full suite 804 passed / 0 failed / 0 skipped.

## Rollback

Revert the Packet 7D commit; remove `voicebridge/profiles.py`, the `profile`/
`branding` CLI, the `/api/profiles*`/`/api/audio*`/`/api/permissions`/
`/api/device*`/`/api/export-state`/`/api/branding` routes, `frontend/
settings.html`, the `/settings` route, and `tests/test_packet7d.py`. No live
config, service, browser, model, or audio state is changed by any 7D command;
profile operations keep `.profile.*.bak` backups, and branding apply keeps a
backup dir for rollback.