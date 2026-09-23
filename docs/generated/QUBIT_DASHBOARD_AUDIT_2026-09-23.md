# Qubit dashboard audit + fix — 2026-09-23

Machine-readable evidence: `docs/generated/QUBIT_DASHBOARD_AUDIT_2026-09-23.json`
(32 pages, 277 controls, 101 defects, 20 fixes). Raw agent reports and browser
screenshots: `/tmp/opencode/qubit-dashboard-audit-2026-09-23/`.

**Method.** 10 parallel read-only agents drove the live dashboard
(`http://127.0.0.1:8890`) with a real browser plus authenticated HTTP
(`X-Hermes-Session-Token` scraped from the served HTML) and source tracing.
Every fix ports/adapts the reference install (`~/.hermes/hermes-agent` 0.21.4)
and/or the vendored `qubit_runtime/`, then routes through the canonical
broker/policy/audit/confirmation/e-stop. No stubs, no fake success. The
reference `:9119` and `~/.hermes` were never modified.

## Measured totals (from the audit)

| Metric | Value |
|---|---|
| Pages tested | 32 |
| Controls tested | 277 |
| IMPLEMENTED | 174 |
| OPEN | 34 |
| FAIL | 46 |
| OPERATOR_ONLY | 8 |
| SAFE_EQUIVALENT | 7 |
| PARTIAL / NOT_REACHABLE / MISSING | 8 |
| Defects found | 101 (29 high / 37 med / 35 low) |
| Fixes applied this session | 20 |

## Fixes applied and verified live

| Area | Fix | Live proof |
|---|---|---|
| Gateway | Start/Stop/Restart stage a real confirmation (were silent 409) | browser dialog; confirm 200 |
| /qubit-chat | invalid reasoning returned 200 empty body (NameError) | typed `REASONING_INVALID` SSE frame |
| Cron | pause/resume require confirmation; trigger uses the job id | 409 `CONFIRM_REQUIRED` |
| Webhooks | create/enable/disable confirmation-gated + audited | 409 `CONFIRM_REQUIRED` |
| MCP | capabilities retained, `enabled` preserved on edit, real test, `/api/mcp` list | create/PUT round-trip `invocable:true` |
| Toolsets | enable no longer false-success; read path reports true; cancellable | enable → read `enabled:true` (restored) |
| Sessions | filters honored; mutations e-stop+audit; tool/reasoning rows; restore | live order/source filters |
| Files | upload JSON contract; broker delete (reversible archive); create confirm | broker capability + tests |
| Kanban | native writes on one authoritative store (were all 403) | staged 409 → confirm 200 |
| Channels | real 33-platform catalog + enable/disable + secret refusal | live count=33; 403 `CHANNEL_ENV_REFERENCE_ONLY` |
| Profiles | list/create/delete target one registry; no fabricated `ollama-local` | tests + live |
| Pairing | GET served from the store mutations write; typed errors; confirm+e-stop | live `{"ok":true,...}` |
| Devices | enroll confirmation + audit (dashboard + CLI receipt) | 409 `CONFIRM_REQUIRED` |
| Env | `GET /agent/api/env` 500 (missing `import os`) + real `is_set` | 200; `is_set` from runtime `.env` |
| Plugins | real catalog (70 real entries) + honest operator-gated remote install | catalog 200 count=70; browser |
| /chat | slash worker `ModuleNotFoundError: tui_gateway` fixed | real `/tools` renders |
| Models | default reconciled across config/model-info/qubit-chat | all `deepseek-v4-flash` |
| Docs | legacy group/paths collapsed into compat aliases | 0 legacy occurrences in openapi |
| Frontend | dead Config raw-YAML / Env Reveal removed; channel refs; cron epoch | typecheck + vitest + build |
| MoA | fake default providers removed (honest unconfigured) | empty defaults + tests |

Regression evidence: focused suites across every wave — 31 + 326 + 91 + 95
backend tests pass; frontend `tsc` clean, vitest 415 passed, bundle rebuilt;
`graft build` refreshed.

## Post-fix live re-verification corrections

Several audit `FAIL`/`OPEN` rows were already real when re-checked live after the
fix waves, so they are not defects:

- Sessions `import`/`prune`/`delete-empty`/`bulk-delete` are real, typed,
  confirm-gated handlers (not 501).
- Webhooks `rotate`/`test`/`deliveries`/`retry` are real **id-scoped** routes
  (`/api/webhooks/{id}/...`); the audit searched the un-scoped path. Live-proven:
  rotate 200, test/retry return an honest `verdict:FAILED` with the real
  connection error, deliveries GET 200, delete 200, store clean.
- `GET /api/mcp/catalog` returns real entries; `GET /api/pairing` returns
  `request_id`; the logs backend honors `?search=`; webhook URLs are allowlisted
  and secrets must be `env:`/`file:` refs.

## Remaining (not done — not claimed)

- **Remote plugin index** (reference 240-entry) not ported; Qubit policy refuses
  remote fetch. The local 70-entry catalog is real.
- **Binary file upload** is text-only; needs a brokered binary store.
- **Cross-file wiring** for cron timezone/retries, memory update/pagination, and
  boards pagination: implemented in the stores, not yet reachable from the
  dashboard/voice routes.
- `qubit_cli/profiles.py::create_wrapper_script` writes `hermes -p` while
  `_is_our_wrapper` checks `qubit -p` (legacy brand + wrapper never cleaned up).
- `agent/plan_prompt.py` + the `plan` skill reference a stale `.hermes/plans/` path.
- The retired `openclaw-migration` script is newer than the canonical
  `qubit-migration` copy — needs an owner ruling on which is canonical.
- MCP resources/prompts/templates list routes absent; logs pagination/detail
  backend contract absent (reference has neither either).

### Resolved in the retry wave
Skills de-brand finished (0 rewritable prose brand; 146 strict-identifier files
remain = preserved wire/provenance ids; live 197 skills resolve) · `/chat` slash
`/toolsets /whoami /version /context` confirmed working · profiles reconciled
onto the real reference `qubit_cli/profiles.py` registry · env
`env:/vault:/file:` refs round-trip with `is_set` · channel secret-scope verified
· gateway restart action status honest `completed/exit 0` · cron/memory/boards
store support added.

No claim of `1:1`, `100%`, `CODE_READY`, `VERIFIED`, `M1`, or `COMPLETE`.
