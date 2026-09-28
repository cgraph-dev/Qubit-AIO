# Qubit Dashboard Control Matrix — 2026-09-21

**Status:** hermetic backend-contract evidence. **NOT_READY. No control is
`IMPLEMENTED`.** No real provider, model, network, hardware, or browser E2E was
run. `backend-verified` in this document means only the hermetic backend chain
described in §2; it is **not** a completion, parity, `VERIFIED`, 100 %, or 1:1
claim (see `docs/QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md`).

**Workstream:** dashboard control-matrix pass over
`docs/QUBIT_PAGE_BY_PAGE_PARITY_EXECUTION_PLAN_2026-09-21.md` (28 page
sections), driven through the real canonical dispatcher
`voicebridge.qubit_compat.dispatch`.

**Machine-readable companion:** `tests/test_dashboard_matrix9u.py` (214 cases).

---

## 1. What was actually done

For each page section, the backend routes/operations that back its controls were
enumerated from the plan plus the page sources (`frontend/qubit-web/src/pages/*`)
and the canonical route table (`voicebridge/qubit_compat.ROUTES`), then driven
in-process with a hermetic `voicebridge.config.Config` (temp `XDG_*` +
`QUBIT_HOME`; `logging.jsonl: false`; `webui.token: t`). Each dispatch asserts a
real registered handler answered with the documented wire shape — never a
dispatcher 404, `NO_HANDLER`, or `STREAM_REQUIRES_SSE` stub.

**No browser/E2E run. No real provider/model call. No live credential. No
hardware action.** The generated coverage artifact
`docs/QUBIT_PAGE_COVERAGE_2026-09-21.json` still records 3199 OPEN because that
artifact is source-level and no browser walk has run; this document does not
change that artifact.

## 2. Disposition vocabulary (this document)

| Term | Meaning here |
|---|---|
| `backend-verified` | The hermetic chain ran: a real handler returned a documented 2xx payload, **or** a mutation staged/settled through the real confirmation authority with persisted read-back. |
| `OPEN` | Everything else: typed refusal (gate or boolean-confirm), SSE stream, needs a live provider / model / credential / hardware / browser, or no hermetic 2xx proof. |

`backend-verified` is deliberately weaker than the plan's `IMPLEMENTED`. The plan
requires `input → auth → canonical op → policy/broker/e-stop → real
runtime/provider → persistent state → rendered result → error/retry/cancel/
audit`. This pass proves the middle of that chain hermetically, not the ends.

## 3. Focused test evidence

```text
$ .venv/bin/python -m pytest tests/test_dashboard_matrix9u.py -q -p no:cacheprovider
214 passed, 1 warning in 36.82s
```

Case groups (all in `tests/test_dashboard_matrix9u.py`):

| Case group | Count | Proves |
|---|---|---|
| `test_read_control_is_backend_verified[...]` | 155 | real 2xx handler + documented wire shape |
| `test_safe_mutation_is_backend_verified[...]` | 13 | real 2xx hermetic mutation |
| `test_journal_confirmation_stages_a_real_id[...]` | 10 | 409 + real settleable `confirmation_id` |
| `test_boolean_confirm_mutation_is_typed[...]` | 27 | 409 typed re-issue confirmation contract |
| `test_gated_routes_are_typed_refusals` | 1 (71 routes) | every gate returns its documented typed refusal + counterpart |
| `test_every_registered_route_resolves_to_a_real_handler` | 1 (411 routes) | no route points at a missing handler |
| `test_every_implemented_route_is_not_a_dispatcher_stub` | 1 (339 routes) | no implemented route falls through to the dispatcher 404/`NO_HANDLER` |
| `test_cron_confirmation_settles_and_persists` | 1 | stage → settle → read-back in the canonical cron store |
| `test_mcp_confirmation_settles_and_persists` | 1 | stage → settle → read-back in the canonical MCP registry |
| `test_webhook_confirmation_settles_and_persists` | 1 | stage → settle → delete persisted in the canonical webhook store |
| `test_compat_dispatch_invokes_the_audit_callsite` | 1 | served/gated call the single audit writer (journal write defect, §8) |
| `test_estop_latch_refuses_turn_and_lifts` | 1 | shared e-stop latch refuses the canonical turn path, then lifts |
| `test_every_plan_area_with_backend_routes_is_probed` | 1 | all 26 backend-owning plan areas are probed |

## 4. Route-level tally (canonical `qubit_compat.ROUTES`)

| Bucket | Count |
|---|---|
| Total registered routes | 411 |
| `implemented` | 339 |
| `gated` (typed refusal) | 71 |
| `stream` (SSE, JSON dispatch refuses) | 1 |
| **`backend-verified` routes** | **168** |
| **`OPEN` routes** | **243** (= 71 gated + 1 stream + 171 implemented without a hermetic 2xx proof) |

## 5. Per-page summary (28 plan sections)

`verified` / `OPEN` are route-level counts for the backend operations backing
that page. Evidence test ids are in `tests/test_dashboard_matrix9u.py`.

| # | Page section | Backend area | verified | OPEN | Evidence |
|---|---|---|---:|---:|---|
| 1 | Chat surfaces (`/chat`, `/qubit-chat`) | chat | 11 | 19 | read + safe-mutation + e-stop cases |
| 2 | Sessions | sessions | 5 | 15 | read + boolean-confirm cases |
| 3 | Files | files | 18 | 25 | read cases; write/upload/git-review staged |
| 4 | Models & Providers | models | 12 | 21 | read + boolean-confirm cases |
| 5 | Logs & Reports | logs_reports | 4 | 0 | read cases |
| 6 | Cron | cron | 4 | 9 | read + journal-confirm + settle case |
| 7 | Skills | skills | 4 | 11 | read cases; hub install gated |
| 8 | Plugins | plugins | 5 | 8 | read + visibility mutation + confirm cases |
| 9 | MCP | mcp | 3 | 13 | read + journal-confirm + settle case |
| 10 | Channels | channels | 2 | 4 | read + test-connection; onboarding gated |
| 11 | Webhooks | webhooks | 2 | 5 | read + enable + journal-confirm + settle case |
| 12 | Pairing | pairing | 2 | 3 | read + create |
| 13 | Profiles | profiles | 6 | 14 | read + journal-confirm + boolean-confirm cases |
| 14 | Config | config | 3 | 3 | read cases; raw/secret writes gated |
| 15 | Keys | keys | 2 | 10 | read cases; secret surfaces gated |
| 16 | System | system | 17 | 13 | read + theme/font mutations + update/egress gates |
| 17 | Agents | agents | 1 | 0 | read case (read-only page) |
| 18 | Documentation | docs | 1 | 0 | `/openapi.json` read case |
| 19 | Qubit Chat plugin | (none) | 0 | 0 | **OPEN by design** — no plugin exists (removed 2026-09-17); `/qubit-chat` is built-in |
| 20 | Models & Providers plugin | models (shared) | (see #4) | (see #4) | plugin and built-in share `/api/model*`; single registry |
| 21 | Qubit Boards | kanban (shared) | (see #22) | (see #22) | plugin reads `/api/plugins/kanban` |
| 22 | Kanban | kanban | 13 | 10 | read cases; writes gated `KANBAN_WRITE_BROKERED` |
| 23 | Gateway | gateway | 4 | 4 | read + drain; start/stop/restart covered by `test_gateway_control_integration9u.py` |
| 24 | Memory | memory | 7 | 11 | read + provider mutation; reset/OAuth staged |
| 25 | Runtime Inventory | runtime | 8 | 0 | read cases |
| 26 | Operations | operations | 17 | 25 | read + debug-share + confirm/boolean-confirm cases |
| 27 | Achievements | achievements | 5 | 1 | read + rescan; reset-state staged |
| 28 | Devices | devices | 5 | 4 | read cases; pip/present staged |
| — | Global / unmapped (`/api/tools/*`, `/api/events`, `/api/pub`) | unmapped | 7 | 15 | read + pub mutation |

## 6. `backend-verified` control appendix

Each entry is a real handler returning the documented wire shape in the hermetic
chain (test id `test_read_control_is_backend_verified[...]` or
`test_safe_mutation_is_backend_verified[...]`).

**achievements** (5): `GET /api/plugins/hermes-achievements/achievements`, `GET /api/plugins/hermes-achievements/recent-unlocks`, `GET /api/plugins/hermes-achievements/scan-status`, `GET /api/plugins/hermes-achievements/sessions/probe/badges`, `POST /api/plugins/hermes-achievements/rescan`

**agents** (1): `GET /api/qubit/agents`

**channels** (2): `GET /api/messaging/platforms`, `POST /api/messaging/platforms/probe/test`

**chat** (11): `GET /api/audio/devices`, `GET /api/audio/list`, `GET /api/audio/status`, `GET /api/audio/voice-config`, `GET /api/qubit/chat/sessions`, `GET /api/qubit/chat/sessions/probe/activity`, `GET /api/qubit/chat/sessions/probe/ci`, `GET /api/qubit/chat/sessions/probe/comments`, `GET /api/qubit/chat/sessions/probe/drafts`, `POST /api/qubit/chat/pty`, `POST /api/qubit/chat/sessions`

**config** (3): `GET /api/config`, `GET /api/config/defaults`, `GET /api/config/schema`

**cron** (4): `GET /api/cron/blueprints`, `GET /api/cron/delivery-targets`, `GET /api/cron/jobs`, `GET /api/cron/jobs/probe/runs`

**devices** (5): `GET /api/qubit/browser/favicon`, `GET /api/qubit/browser/favicon-cache`, `GET /api/qubit/browser/profiles`, `GET /api/qubit/desktop/ops`, `GET /api/qubit/desktop/presentation`

**docs** (1): `GET /openapi.json`

**files** (18): `GET /api/files`, `GET /api/fs/default-cwd`, `GET /api/fs/git-root`, `GET /api/fs/list`, `GET /api/fs/stat`, `GET /api/fs/tree`, `GET /api/git/base-branches`, `GET /api/git/branches`, `GET /api/git/diff`, `GET /api/git/file-diff`, `GET /api/git/log`, `GET /api/git/review/commit-context`, `GET /api/git/review/diff`, `GET /api/git/review/list`, `GET /api/git/review/rev-parse`, `GET /api/git/show`, `GET /api/git/status`, `GET /api/git/worktrees`

**gateway** (4): `GET /api/gateway`, `GET /api/gateway/rpc`, `GET /api/qubit/gateway/discovery`, `POST /api/gateway/drain`

**kanban** (13): `GET /api/plugins/kanban/assignees`, `GET /api/plugins/kanban/board`, `GET /api/plugins/kanban/boards`, `GET /api/plugins/kanban/config`, `GET /api/plugins/kanban/diagnostics`, `GET /api/plugins/kanban/events`, `GET /api/plugins/kanban/home-channels`, `GET /api/plugins/kanban/model-options`, `GET /api/plugins/kanban/orchestration`, `GET /api/plugins/kanban/profiles`, `GET /api/plugins/kanban/projects`, `GET /api/plugins/kanban/stats`, `GET /api/plugins/kanban/workers/active`

**keys** (2): `GET /api/env`, `GET /api/qubit/credentials/pool`

**logs_reports** (4): `GET /api/analytics`, `GET /api/analytics/models`, `GET /api/analytics/usage`, `GET /api/logs`

**mcp** (3): `GET /api/mcp`, `GET /api/mcp/catalog`, `GET /api/mcp/servers`

**memory** (7): `GET /api/curator`, `GET /api/learning/graph`, `GET /api/learning/node`, `GET /api/memory`, `GET /api/memory/provider`, `GET /api/memory/providers/probe`, `PUT /api/memory/provider`

**models** (12): `GET /api/local-models/catalog`, `GET /api/local-models/hardware`, `GET /api/local-models/jobs`, `GET /api/local-models/jobs/probe`, `GET /api/local-models/status`, `GET /api/model/auxiliary`, `GET /api/model/info`, `GET /api/model/moa`, `GET /api/model/options`, `GET /api/model/recommended-default`, `GET /api/providers/custom-endpoints`, `GET /api/qubit/local-models/fallback`

**operations** (17): `GET /api/actions/probe`, `GET /api/ask/pending`, `GET /api/ask/status`, `GET /api/documents/kinds`, `GET /api/documents/status`, `GET /api/ops`, `GET /api/ops/approvals`, `GET /api/ops/checkpoints`, `GET /api/ops/hooks`, `GET /api/qubit/hooks/doctor`, `GET /api/qubit/logbook`, `GET /api/qubit/usage`, `GET /api/recall/policy`, `GET /api/recall/status`, `GET /api/shares/status`, `GET /api/transfers/status`, `POST /api/ops/debug-share`

**pairing** (2): `GET /api/pairing`, `POST /api/pairing`

**plugins** (5): `GET /api/dashboard/plugins`, `GET /api/dashboard/plugins/fleet`, `GET /api/dashboard/plugins/hub`, `GET /api/dashboard/plugins/rescan`, `POST /api/dashboard/plugins/probe/visibility`

**profiles** (6): `GET /api/profiles`, `GET /api/profiles/active`, `GET /api/profiles/probe/setup-command`, `GET /api/profiles/projects/tree`, `GET /api/profiles/sessions`, `GET /api/profiles/sessions/sidebar`

**runtime** (8): `GET /api/runtime`, `GET /api/qubit/runtime/discovery`, `GET /api/qubit/runtime/doctor`, `GET /api/qubit/runtime/history`, `GET /api/qubit/runtime/images`, `GET /api/qubit/runtime/marketplace`, `GET /api/qubit/runtime/status`, `GET /api/qubit/runtime/update-report`

**sessions** (5): `GET /api/sessions`, `GET /api/sessions/empty/count`, `GET /api/sessions/search`, `GET /api/sessions/shares`, `GET /api/sessions/stats`

**skills** (4): `GET /api/skills`, `GET /api/skills/hub/official`, `GET /api/skills/hub/search`, `GET /api/skills/hub/sources`

**system** (17): `GET /api/auth/me`, `GET /api/auth/providers`, `GET /api/dashboard/font`, `GET /api/dashboard/themes`, `GET /api/egress/status`, `GET /api/health`, `GET /api/power`, `GET /api/qubit/egress/providers`, `GET /api/qubit/egress/status`, `GET /api/qubit/policy/attest`, `GET /api/qubit/policy/conformance`, `GET /api/qubit/update/check`, `GET /api/status`, `GET /api/system/stats`, `GET /login`, `PUT /api/dashboard/font`, `PUT /api/dashboard/theme`

**unmapped** (7): `GET /api/events`, `GET /api/tools/computer-use/status`, `GET /api/tools/terminal/backends`, `GET /api/tools/toolsets`, `GET /api/tools/toolsets/probe/config`, `GET /api/tools/toolsets/probe/models`, `POST /api/pub`

**webhooks** (2): `GET /api/webhooks`, `POST /api/webhooks/enable`

## 7. Confirmation, policy, e-stop evidence

* **Journal-staged confirmation** (real settleable id, `test_journal_confirmation_stages_a_real_id`): profiles create, cron create/delete, MCP delete/enable, webhook delete, desktop `pip`/`present`, toolset update. Settle + persisted read-back proven for cron, MCP, and webhooks.
* **Boolean re-issue confirmation** (`test_boolean_confirm_mutation_is_typed`): profiles soul/description/model/delete/export, env put/delete, transfers fetch/plan, recall policy/escalate, documents extract, ask create, session share purge/revoke/publish, update apply, custom-endpoint activate/delete, terminal backend, computer-use grant, memory reset, achievements reset-state, agent-plugin enable/disable/remove.
* **Gated typed refusals** (`test_gated_routes_are_typed_refusals`): all 71 gates return their documented HTTP status, `ok: false`, the gate `code`, and a `qubit_counterpart`. Examples: `SECRET_NEVER_SERVED` (config raw, env reveal, credentials pool), `FILES_BINARY_STREAM`, `KANBAN_WRITE_BROKERED`, `PTY_NO_SHELL`, `OAUTH_MANUAL_HANDOFF`, `UPDATE_OPERATOR_OWNED`, `CRON_FIRE_EXTERNAL`, `GIT_WRITE_BROKERED`, `BLOCKED_REMOTE_SOURCE`.
* **E-stop** (`test_estop_latch_refuses_turn_and_lifts`): the shared latch (`voicebridge.power.power_control_store`) makes `POST /api/qubit/chat` return `403 EMERGENCY_STOP`; lifting the latch clears the refusal.

## 8. Top 10 OPEN controls (with file:line)

| # | OPEN control | Evidence / file:line |
|---|---|---|
| 1 | **Compat dispatch audit journal is not written.** `_audit` passes the detail dict positionally to `log_event(event, **fields)`, raising a swallowed `TypeError`; no `qubit_compat.*` event reaches `events.jsonl`. Affects every page. | `voicebridge/qubit_compat.py:47-53` (call at `:50-51`); repro in `test_compat_dispatch_invokes_the_audit_callsite` |
| 2 | **E-stop is not enforced on direct mutations.** Only ~8 handlers call `_estop(cfg)`; direct webhook/cron/MCP/profile creates succeeded while the latch was engaged. | `voicebridge/qubit_compat.py:61` (`_estop`); unguarded creates at `:6587` (`cron_create`), `:6773` (`mcp_create`), `:1931` (`h_webhooks_mutate`), `:4561` (`h_profile_create`) |
| 3 | `/api/qubit/chat/stream` — SSE turn; JSON dispatch returns `STREAM_REQUIRES_SSE`, no hermetic turn proof. | `voicebridge/qubit_compat.py:3107` (`h_chat_stream`) |
| 4 | Files binary download/stream refused (`FILES_BINARY_STREAM`); no binary transfer E2E. | `voicebridge/qubit_compat.py:5302`; routes `GET /api/files/download`, `/api/files/stream`, `/api/fs/download` |
| 5 | Kanban writes refused (`KANBAN_WRITE_BROKERED`); board mutations not hermetically verified. | `voicebridge/qubit_compat.py:5377`; `POST/PUT/PATCH/DELETE /api/plugins/kanban/*` |
| 6 | In-place self-update refused (`UPDATE_OPERATOR_OWNED`); apply path operator-owned. | `voicebridge/qubit_compat.py:5257`; `POST /api/qubit/update` |
| 7 | OAuth flows are manual handoff (`OAUTH_MANUAL_HANDOFF`) for MCP, memory, and providers. | `voicebridge/qubit_compat.py:5253`; `/api/mcp/oauth/*`, `/api/memory/providers/*/oauth/*`, `/api/providers/oauth*` |
| 8 | Raw secret surfaces refused (`SECRET_NEVER_SERVED`); no secret read/write E2E. | `voicebridge/qubit_compat.py:5242`; `/api/config/raw`, `/api/env/reveal`, `/api/credentials/pool*` |
| 9 | `/qubit-chat` agent turn needs a live model/provider; hermetic dispatch only reaches validation/e-stop, not a real completion. | `voicebridge/qubit_compat.py:2675` (`h_chat_turn`); `voicebridge/chat_transport.py:42` (`DEFAULT_MODEL`) |
| 10 | Dashboard/control browser E2E not run — 3199 OPEN rows remain source-level. | `docs/QUBIT_PAGE_COVERAGE_2026-09-21.json` (generated; not edited by this pass) |

Additional OPEN: the Qubit Boards/Kanban dual UI over one kanban store
(`frontend/qubit-app/plugins/qubit-boards/qubit-boards.js` vs
`frontend/qubit-web/public/plugins/kanban/dist/index.js`); and the 6
`SECRET_NEVER_SERVED` credential-pool routes under Keys.

> **Status update (2026-09-21, dashboard-backend workstream):** items **#1**
> (compat audit journal) and **#2** (e-stop on direct mutations) are repaired in
> the worktree: `_audit` now passes keyword fields, `_compat_confirm` enforces
> the shared e-stop before staging/consuming an approval, and the priority
> controls were extended with e-stop gates (see §9A). The remaining items
> (#3–#10) stay OPEN as described.

## 9. Raw commands and results

```text
$ .venv/bin/python -m pytest tests/test_dashboard_matrix9u.py -q -p no:cacheprovider
214 passed, 1 warning in 36.82s
```

Route enumeration used by this pass (read-only, no LLM):

```text
$ .venv/bin/python - <<'PY'
from voicebridge import qubit_compat as Q
print(len(Q.ROUTES), len(Q.HANDLERS))
# 411 324
PY
```

Hermetic config used by every case: temp `XDG_CONFIG_HOME`/`XDG_STATE_HOME`/
`XDG_DATA_HOME`, temp `QUBIT_HOME`, `logging.jsonl: false`, `webui.token: t`.

## 9A. Backend-closure increment (2026-09-21, dashboard-backend workstream)

This increment implements REAL backends for the highest-value OPEN controls
that a canonical Qubit operation can support, each through the shared
confirmation journal, the policy/e-stop latch, and the audit writer, with
persisted state. It does **not** rewrite the §4/§5 route-level counts (those
were generated before this increment); it records per-control before/after.

| Control | Before | After | Real backend / persisted state |
|---|---|---|---|
| `GET /api/curator` | read-only; `paused` hard-coded false | real pause/run read-back | `curator_control.json` |
| `PUT /api/curator/paused` | gated `CURATOR_ON_DEMAND` 403 | implemented | persisted `paused` flag; e-stop-gated; audited |
| `POST /api/curator/run` | gated `CURATOR_ON_DEMAND` 403 | implemented | on-demand pass; records `last_run_at`/`run_count`; refuses `CURATOR_PAUSED`; e-stop-gated; audited |
| `POST`/`GET /api/memory/reset` | typed refusal `MEMORY_RESET_OPERATOR_SURFACE` after confirm | implemented | canonical memory-store tombstone + markdown-workspace forget; real `deleted` list; journal-staged; e-stop-gated; audited |
| `POST /api/ops/hooks` | real, boolean-confirm (empty id) | real + settleable | journal-staged real `confirmation_id`; e-stop-gated; canonical hooks registry |
| `DELETE /api/ops/hooks` | real, boolean-confirm | real + settleable | journal-staged real id; e-stop-gated |
| `PUT /api/ops/hooks` | unrouted | implemented | real remove + re-declare update; e-stop-gated |
| `GET /api/config` | served raw secret leaves | redacted read | `webui.token` etc. served as `[REDACTED]`; write side unchanged |
| `PUT /api/config` | real confirm-gated write | unchanged + e-stop | persisted overrides |
| `PUT`/`DELETE /api/env` | real refs, no e-stop | real + e-stop-gated | vault-reference registry (refs only) |

**Test evidence:**

```text
$ .venv/bin/python -m pytest tests/test_dashboard_backend_closure9u.py -q -p no:cacheprovider
16 passed in 1.84s
```

Every case drives `voicebridge.qubit_compat.dispatch` and asserts a persisted
read-back (or a staged → settled journal confirmation) plus an e-stop refusal.
These controls remain **`backend-verified` at most**: no real
provider/model/network/credential/browser E2E ran, and no
`IMPLEMENTED`/`VERIFIED`/100 %/1:1 claim is made.

**Still typed (genuinely operator-gated):** files binary download/stream
(`FILES_BINARY_STREAM`), raw secret surfaces (`SECRET_NEVER_SERVED`), in-place
update (`UPDATE_OPERATOR_OWNED`), OAuth (`OAUTH_MANUAL_HANDOFF`), kanban writes
(`KANBAN_WRITE_BROKERED`), sessions bulk/prune/import (`SESSIONS_RECEIPT_STORE`),
and the upstream credential-pool routes.

**Pre-existing failure note:** `tests/test_ledger_hermes_build.py` still fails
on the frozen ledger section because `scripts/rebuild_ledger_hermes.py` and
`scripts/gap_ledger.py` are mid-edit by another workstream (the regenerated
section differs for `POST /api/qubit/update` and `POST /api/gateway/restart`
independently of this increment). This workstream did not edit the ledger.

---

## 10. Explicit non-claims

* No control is `IMPLEMENTED`; `backend-verified` is a hermetic backend-chain
  proof only.
* No real provider, model, network egress, credential, hardware, or browser was
  exercised.
* No `VERIFIED`, 100 %, 1:1, or completion claim is made.
* The generated coverage artifact was **not** edited; its 3199 OPEN rows stand.
* Findings #1 and #2 in §8 are product defects recorded for their owners; this
  workstream owns only tests and docs and did not modify product code.
