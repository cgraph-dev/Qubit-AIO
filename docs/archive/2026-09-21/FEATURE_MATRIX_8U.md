# Packet 8U preparation — upstream audit, identity, dashboard consolidation

Status: preparation only. Packet 8U capability implementation is NOT started.
Machine-readable matrix: `docs/FEATURE_MATRIX_8U.json` (authoritative for
verdicts; this file is the human-readable companion — every JSON item id
below appears in both).

Fresh read-only clones (outside the Qubit checkout, outside the operator's
live `~/.hermes` / `~/.openclaw` installs, never modified):

- Hermes Agent `v2026.9.7` @ `2237be355906fbe6065ce1815711eee52b2d646e`
  (2026-09-07), MIT © 2025 Nous Research — `/tmp/upstream-8u/hermes-agent`
- OpenClaw `v2026.9.2` @ `3928bad9badfcb6c7d140530435e806fb8092190`
  (2026-09-05), MIT © 2026 OpenClaw Foundation — `/tmp/upstream-8u/openclaw`

No production code was copied from either clone in this task. Provenance
records: `docs/UPSTREAM_REUSE.md` R29/R30.

## 1. Product identity (defined before any file move)

- Public identity: **Qubit VoiceBridge**; product/runtime identity: **Qubit**.
- Voice subsystem: **Qubit VoiceBridge**.
- Canonical repository/install identity: **qubit-voicebridge** (unchanged;
  no migration to another name was proven safe in this task).
- Internal Python import package: **voicebridge** (unchanged).
- `openclaw-voice-bridge`: legacy compatibility identity only.
- Authority: Qubit broker, permissions, audit, emergency stop, session
  identity, state/config/release rules remain authoritative. No second agent
  core, router, memory, tool registry, permission system, browser authority,
  scheduler, or audit store was created.

## 2. Feature matrix summary

Counts from `FEATURE_MATRIX_8U.json` (35 items):

- REUSE (7): hermes SSE stream loop; openclaw session-key + chat.abort;
  openclaw cron runner + delivery validation; openclaw exec approvals;
  openclaw audit events + receipts; hermes ESTOP sentinel + receipts;
  openclaw model registry + server-side SecretRef.
- ADAPT (14): hermes STT/TTS providers; hermes wake-word (openWakeWord/sherpa;
  Porcupine excluded until licensed); hermes a11y browser snapshot +
  supervisor; openclaw browser bridge auth/profiles/doctor; hermes
  file/terminal tools; hermes ACP adapter (protocol MIT); hermes SQLite
  session schema ideas; openclaw memory files + turn store; openclaw board
  APIs/sandbox; openclaw onboarding flow; hermes gateway/cron/batch;
  hermes smart approvals; openclaw Dockerfile digest-pinning pattern.
- MISSING (8, Qubit-owned): voice stack (mic/ASR/TTS/playback/hotkeys);
  Right Ctrl + web PTT; 8B loopback CDP browser authority; 8V desktop
  adapter; Qubit MemoryStore + verified skills; Qubit provider gateway;
  Qubit audit/e-stop (single enforcement point); Qubit packaging/rollback/
  uninstall.
- REJECTED (6): hermes Camofox cloud browser (exfiltration); unpinned
  cua-driver binary + Comet/Firefox roles (need separate authorization);
  hermes Electron desktop (duplicate surface); hermes cloud memory/Hub
  sync (foreign ToS); ClawHub third-party auto-install (unreviewed);
  hermes FastAPI dashboard/TUI as UI (second event authority; vendor
  installer likewise stays Qubit-owned).

Primary spike runtime: the **Hermes CLI contract** (`hermes chat -q/-Q/
--continue`), already adapted in `voicebridge/hermes/backend.py`, proven
hermetically against an isolated stub (no live binary, no operator
sessions). OpenClaw contributes gateway-protocol patterns only; no live
gateway connection was made.

## 3. Dashboard consolidation

Implemented (existing authenticated typed backend, reused unchanged):
chat/events stream, permissions, actions, power/e-stop, setup/health/
checklist/diagnostics/run/export, talk/PTT intake.

Unimplemented (8U follow-ups, NOT claimed): streaming tool-output cells,
voice/wake-word status tiles, browser/desktop context panes, file/project
workspace views, IDE/OpenCode diff previews, memory/skills/task/schedule
views, provider/key-reference status, approval inbox UI, adapter-health
board, mobile/accessibility pass. Any new surface must call the existing
backend; frontend-only permissions/state/execution remain forbidden.

## 4. 8U task capsule inputs

- REUSE rows give 8U its streaming, cancellation (session-key + abort),
  scheduling (cron runner), approvals (exec lifecycle), audit (receipt
  schema), secrets (SecretRef), and e-stop (sentinel) patterns.
- ADAPT rows each need a Qubit broker/session/policy wrapper ticket with
  synthetic fixtures + negative/security tests before any live use.
- MISSING rows are Qubit implementation work inside 8U–8Z scope.
- REJECTED rows must not re-enter through 8U; any exception needs a new
  license/security review and explicit operator authorization.

## 5. Migration rehearsal note

No physical repository migration was performed. Identity is documented
above; any future rename rehearses from a clean clone with backup,
rollback, alias handling, config/state preservation, service/shortcut
migration, and installer verification — as an explicitly authorized task.

## 6. Matrix id index (parity with FEATURE_MATRIX_8U.json)

voice-stt, voice-tts, voice-stream, voice-stack, wake-word, ptt,
browser-read, browser-a11y, browser-cloud, chromium, comet-firefox,
desktop-use, desktop-8v, files, terminal, ide-acp, memory-local,
memory-cloud, skills-market, models-registry, models-qubit, dashboard-ui,
dashboard-board, onboarding, sessions, sessions-sqlite, cron, cron-hermes,
approvals, approvals-hermes, audit, estop, audit-qubit, packaging,
packaging-ref.
