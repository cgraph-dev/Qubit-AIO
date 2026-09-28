# Qubit Local Runtime Audit — 2026-09-21

**Status:** read-only audit + implementation record for the current worktree.
NOT completion evidence and NOT a verification claim. Read alongside
[QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md).

**Method:** direct inspection of the live host and worktree (processes,
listeners, systemd user units, config/db paths, catalog counts) plus focused
code reads. No prior report was trusted. No file in the local Hermes install
was modified or deleted.

---

## A. Runtime homes and paths (measured 2026-09-21)

| Item | Path | Notes |
| --- | --- | --- |
| Qubit VoiceBridge config | `~/.config/qubit-voicebridge/voice.yaml` | `PRODUCT_NAME=qubit-voicebridge`; `VOICE_BRIDGE_CONFIG` points here |
| Qubit VoiceBridge state | `~/.local/state/qubit-voicebridge/` | `state.db`, `mcp_servers.json`, `profiles.json`, `chat_sessions.json`, `approvals.jsonl`, `events.jsonl` |
| Qubit runtime home (canonical) | `~/.local/state/qubit-voicebridge/runtime/` | `voicebridge.qubit_runtime.default_home()`; holds `skills/`, `plugins/`, `state.db`, `config.yaml` |
| Qubit data / cache | `~/.local/share/qubit-voicebridge/`, `~/.cache/qubit-voicebridge/` | XDG |
| Local Hermes home | `~/.hermes/` | live operator install; **read-only migration source** |
| Local Hermes runtime | `~/.hermes/hermes-agent/` (venv, `hermes-agent` 0.21.3, commit `db39ee3f28`) | upstream `hermes` package |
| Local Hermes state DBs | `~/.hermes/state.db` (247 MB), `projects.db`, `shared-state.db`, `kanban.db` | |
| Local Hermes config | `~/.hermes/config.yaml`, `~/.hermes/.env` (24 KB) | `.env` holds secret **names/values**; never copied |
| Qubit service unit | `~/.config/systemd/user/qubit-voicebridge.service` | `ExecStart=.venv/bin/python -m voicebridge run`, `Restart=on-failure` |

> Note: the vendored fork's platform default home is `~/.hermes`
> (`qubit_constants.get_qubit_home()`), but Qubit **must not** use it. The
> dashboard bridge sets `QUBIT_HOME=<state_dir>/runtime`
> (`voicebridge/qubit_asgi.py:148`, `voicebridge/webui.py`), and
> `voicebridge/qubit_runtime.py` treats `~/.hermes` as an external default to
> avoid. The local Hermes home is therefore a migration source, not an
> authority.

## Running processes and listeners (measured)

| Process | Detail |
| --- | --- |
| Qubit VoiceBridge | `python -m voicebridge run` (pid 2379), listener `0.0.0.0:8890` (systemd user unit `qubit-voicebridge.service`) |
| Hermes dashboard | `~/.hermes/hermes-agent/venv/bin/hermes dashboard --port 9119 --host 127.0.0.1` (pid 2975908) — **second live authority / stray listener** |
| Hermes gateway restart | `python -m hermes_cli.main gateway restart` (pid 2976204); `~/.hermes/gateway.sock`, `gateway_state.json` |
| Hermes update in progress | `.hermes-update-in-progress`, `fleet_restart_pending` present |
| ollama | `ollama serve` + `llama-server` on `127.0.0.1:11434` / `:41127` |
| systemd user units | `hermes-dashboard.service` (active), `openclaw-dashboard-stress.service` (activating/auto-restart), `openclaw-voice-bridge.service` (inactive), `qubit-voicebridge.service` (active) |
| Stale verification leftovers | `qubit --config /tmp/opencode/qubit-verify/... run` (pid 1681632, :8973); `tui_gateway.entry` processes |

Operator-only host hygiene (not code-side): stop the stray `:9119` listener,
run a single Qubit instance, remove the stale unit.

## Catalog inventory (source vs Qubit destination)

| Catalog | Source (`~/.hermes`) | Qubit runtime home | Read paths |
| --- | --- | --- | --- |
| Skills | **162** `SKILL.md` (33 categories) | **60** `SKILL.md` | dashboard `h_skills`, CLI `qubit skills list`, voice `skills list` |
| Plugins | 1 dir (`hermes-achievements`) | 0 (overlay/fleet separate) | dashboard `GET /api/dashboard/plugins`, CLI `qubit plugins`, voice `plugins` |
| MCP servers | **2** (`cgraph-graft`, `openclaw-voice-bridge-graft`) | **0** | dashboard `GET /api/mcp/servers`, CLI `qubit mcp status`, voice `mcp` |
| Providers/models | Hermes `model` + `auxiliary` (14 sections) | migrated profiles in `profiles.json` | resolver |
| Schedules | `~/.hermes/cron/jobs.json` | merged by id | cron page/CLI |

Root causes found:

1. **Skills count mismatch (162 vs 60).** The dashboard/CLI/voice read
   different sources; the Qubit runtime home only held a partial copy. Fixed by
   unifying the read path (`voicebridge/skill_catalog.py`).
2. **MCP never migrated.** `qubit_migration._import_assets` copied
   `skills/plugins/hooks/...` but never the source `mcp_servers:` block. Fixed.
3. **Secret handling.** Source `mcp_servers` env values may be raw secrets; the
   importer converts them to `env:`/`file:` references and drops unresolvable
   raw values (never copied).

## Gateway control root cause

The Gateway card was **hard-coded off** and the controls were permanently
disabled:

- `h_status` returned `gateway_running=False`, `gateway_state="not-supervised"`
  (`voicebridge/qubit_compat.py`).
- `GET /api/gateway` returned an unconditional `supervision="GATEWAY_NO_SUPERVISION…"`,
  which the SPA uses to disable Start/Stop/Restart.
- `POST /api/gateway/restart` was registered as a typed gate (`_NO_SUPERVISION`).

No real process supervision existed anywhere on the Qubit side.

---

## Changes made this session (worktree, uncommitted)

### B — catalog import + one read authority

- `voicebridge/qubit_migration.py`
  - `_source_mcp_servers`, `_env_ref_for`, `_import_mcp_servers`: read-only,
    idempotent, reference-only import of source `mcp_servers:` into the
    canonical `voicebridge.mcp_servers` registry (conflict report; never
    overwrites; `enabled` applied via the canonical transition).
  - `catalog_report`: source-vs-destination counts for skills/plugins/MCP.
  - wired into `_import_assets` / `plan` / `apply` journal (with backup +
    rollback of a created `mcp_servers.json`).
- `voicebridge/skill_catalog.py` (new): ONE merged skills-catalog read
  authority (`catalog_entries`, `merged_store_view`).
- `voicebridge/qubit_compat.py::h_skills`, `voicebridge/cli.py::_cmd_skills`,
  `voicebridge/voice_commands.py::_b_skills` now all use it, so dashboard, CLI,
  and voice report the same catalog count.

### C — real gateway/service supervision

- `voicebridge/gateway_supervisor.py` (new): systemd-based status
  (`ActiveState`/`SubState`/`MainPID`) + real port probe; confirmed, audited,
  idempotent `start`/`stop`/`restart`; detached restart/stop; typed failures;
  fail-closed test guard so a test process never touches the host service.
- `voicebridge/qubit_compat.py`: `h_status` and `h_gateway` report the real
  supervisor state; `h_gateway_supervision` controls the Qubit-owned unit when
  no `gateway_id` is given (record path unchanged with an id); new
  `h_gateway_restart`; `/api/gateway/restart` removed from the typed-gate table.

### D — dynamic Brain4 context

- `voicebridge/context_policy.py` (new): deterministic fast/expanded/escalated
  decision, rolling window, deterministic older-turn summary, instruction
  retention, typed escalation (no silent fallback), model-window ceiling.
- `voicebridge/chat_transport.py`: turn/stream/retry now use the policy, count
  the in-flight user turn, attach `context` + routing to the receipt, and audit
  the decision.
- `voicebridge/config.py`: `agent.context` defaults (min/default/max tokens,
  message windows, `escalation_model`, summary size).

## Tests added (focused)

- `tests/test_dynamic_context9u.py` (7): fast tier, compaction + instruction
  retention, escalation, config normalization, session continuity across a
  model switch, typed fail-closed escalation, no secret fields.
- `tests/test_catalog_sync9u.py` (3): reference-only/idempotent/non-overwriting
  MCP import, catalog counts, three-surface skill-catalog agreement.
- `tests/test_gateway_supervisor9u.py` (6): status parsing, idempotent start,
  detached restart, confirmation/e-stop, test guard.

Updated to the new policy: `tests/test_qubit_routes_agent2b_wiring.py`,
`tests/test_qubit_vendor8tf.py`, `tests/test_dashboard_e2e_phase4.py`.

## Deterministic evidence

- `graft build`; `graft check` → OK (wiring graph in sync).
- `scripts/page_coverage.py` regenerated; two runs byte-identical;
  `--check` OK. Counts moved 156→155 SAFE_EQUIVALENT, 3199→3198 OPEN because
  `POST /api/gateway/restart` moved SAFE_EQUIVALENT→OPEN (now a real handler,
  not yet E2E-verified — the fail-closed default).
- `scripts/route_ownership_matrix.py` regenerated; two runs byte-identical;
  `--check` current (`/api/gateway/restart` gated→implemented).
- `scripts/identifier_rename.py --check` OK (33 renames).

## Still open (not done this session)

- **Update Qubit** apply path (`POST /api/qubit/update` remains `_NO_UPDATE`):
  needs a real, reversible update with rollback; only the check path is real.
- Skills/plugins **enabled-state** import (skills copied, `skills.disabled`
  state not yet mirrored); plugin enable/disable/reload parity.
- Gateway start/stop/restart is not yet exercised live (unit-tested only); the
  live `/api/gateway` read path is real.
- Dashboard/control E2E coverage (3198 OPEN rows), `/chat` real-provider E2E,
  `/qubit-chat` Brain4 voice E2E, Right-Ctrl/STT E2E.
- The 95 OpenClaw `OPERATOR_ONLY` rows and the branding allowlist/policy
  conflict still require the owner rulings.
- No commit/push (implementation-first amendment; final matrix not run).

## Rollback instructions (do not use `git checkout`)

The worktree is mid deep-rebrand and several touched files already carried
uncommitted user changes, so `git checkout -- <file>` would destroy unrelated
work. Roll back per change instead:

- **New files** (delete to revert): `voicebridge/context_policy.py`,
  `voicebridge/skill_catalog.py`, `voicebridge/gateway_supervisor.py`,
  `tests/test_dynamic_context9u.py`, `tests/test_catalog_sync9u.py`,
  `tests/test_gateway_supervisor9u.py`.
- **Modified tracked files** (`voicebridge/chat_transport.py`,
  `voicebridge/cli.py`, `voicebridge/config.py`,
  `voicebridge/voice_commands.py`): reverse only the session edits described
  above (context-policy wiring; skill-catalog read; `agent.context` defaults).
- **Generated artifacts**: regenerate rather than hand-edit —
  `scripts/page_coverage.py` and `scripts/route_ownership_matrix.py`.
- The local Hermes install was never written to; no rollback is required there.

---

## Delegated continuation (5 agents) + integration

Five agents were deployed with disjoint file ownership; their work was then
integrated and re-verified here.

### Update Qubit (C remainder)
- `voicebridge/update_manager.py` (new): real `check` (install method, current
  revision, bounded `git ls-remote`, typed DEGRADED on failure, `can_apply`
  only on a clean git checkout) and `apply` (confirm + e-stop + dirty-tree +
  test guard; the only executed command is `git pull --ff-only`; pre/post
  revision receipt + non-executed `rollback_plan`; audited).
- `voicebridge/qubit_compat.py`: `h_qubit_update_check` now real; new
  `h_qubit_update_apply` wired to `POST /api/qubit/update` (removed that
  `_NO_UPDATE` gate; the `/api/hermes/update.*` aliases stay gated).
- `tests/test_update_manager9u.py` (10).

### Skill/plugin enabled-state import (B remainder)
- `voicebridge/qubit_migration.py`: `_import_skill_state` union-merges source
  `skills.disabled`/`platform_disabled` into the Qubit runtime-home
  `config.yaml` (never removes; atomic; typed skip if PyYAML absent);
  `_import_plugin_state` returns a typed `UNSUPPORTED` because no public
  plugin-state API exists (no second store invented). Wired into the journal
  and rollback.
- `tests/test_catalog_sync9u.py` extended (+6).

### MCP read parity (B/E)
- `voicebridge/integration_status.py::mcp_status` now exposes the
  `records` (name/enabled/source/connected/status) alongside names; dashboard,
  CLI, and voice all resolve to `voicebridge/mcp_servers.py::list_servers`.
- `tests/test_mcp_read_parity9u.py` (5).

### Chat/voice E2E tests (E)
- `tests/test_chat_voice_e2e9u.py` (16 pass, 1 xfail): `/api/qubit/chat`
  persistence + session continuity, streamed events + dynamic-context receipt,
  terminal ticket/origin/auth gates and refused upstream WS dialects,
  Right-Ctrl shares the `chat_transport` route table, `voice.input_engine`
  normalization. The xfail is browser-only Right-Ctrl automation.
- Finding: the desktop `VoiceOrchestrator` PTT path calls
  `voicebridge.agent.run_session`, not `chat_transport.turn`; convergence to
  one session store on that path is unproven (OPEN, reported).

### Dashboard control audit (E)
- `tests/test_dashboard_controls9u.py` (22) and
  `docs/QUBIT_DASHBOARD_CONTROL_AUDIT_2026-09-21.md` (per-page evidence;
  many controls still OPEN — backup/restore/curator/hooks/skills/MCP/plugins).

### Integration fixes applied here
- Frontend gateway refusal token: `SystemPage.tsx` and `App.tsx` now treat any
  non-empty `supervision` (including `GATEWAY_SUPERVISION_UNAVAILABLE`) as a
  disabled control — previously only `GATEWAY_NO_SUPERVISION` disabled them, so
  a systemd-less host rendered enabled buttons that failed on click.
- `frontend/qubit-web/src/lib/api.ts::updateQubit` now posts
  `{confirm: true}` after the confirm dialog (the backend refuses with 409
  otherwise).
- Frontend rebuilt (`frontend/qubit-dist`), typecheck 0, eslint 0 errors /
  29 warnings, vitest 367 passed.
- Regenerated deterministic artifacts after all code landed: `page_coverage`
  (3199 OPEN / 154 SAFE_EQUIVALENT / 0 IMPLEMENTED, 3353 rows; two runs
  byte-identical), `route_ownership_matrix`, `compat_handler_reachability`;
  all `--check` current. `graft build` + `graft check` OK.

### Host side effect (disclosed)
An agent's exploratory (non-pytest) call to `POST /api/gateway/restart`
actually restarted `qubit-voicebridge.service` once (the test guard only
applies inside a test process). The unit is active now. No product or Hermes
data was modified.

### Known failure (pre-existing, unrelated)
`tests/test_qubit_routes_parity8u.py::test_gates_never_touch_filesystem_or_network`
fails because `POST /api/profiles` stages a confirmation that writes
`approvals.jsonl`. That route is untouched by this session; not fixed here.

### Still open
Update-apply is not exercised live; the 3199 OPEN dashboard/control rows; the
desktop orchestrator vs `chat_transport` convergence; `/chat` real-provider E2E
(needs the operator credential); live gateway start/stop/restart; the 95
OpenClaw OPERATOR_ONLY rows and branding rulings. No commit/push; final matrix
not run.

---

## Second delegated round (5 agents) + integration fixes

### Voice/desktop session-store convergence
- Real divergence found and fixed: `voicebridge/agent.py::run_session` (the
  desktop `VoiceOrchestrator` PTT path, called from `orchestrator.py:1693,1829,2926`)
  persisted **no transcript at all** (audit only). It now persists the user task
  and assistant reply through the SAME `voicebridge.chat_transport.store(cfg)`
  (`QubitSessionStore`) as `/qubit-chat`, signature/return contract unchanged.
  New `tests/test_orchestrator_session_authority9u.py` (3).
- Note: `orchestrator` default session ids (`qubit:voice…`) still differ from
  `/qubit-chat`'s `dashboard-chat`; they converge only on the same session id.
  Adopting one canonical id is an open product decision.

### SessionDB write authority
- Audit `docs/QUBIT_SESSIONDB_WRITE_AUTHORITY_2026-09-21.md`: no Qubit-side
  bypass of the canonical chat store. Added typed guards
  (`voicebridge/qubit_session_store.py`: `refuse_external_state_db`,
  `assert_canonical_state_db`, single-writer claim; `voicebridge/pty_ws.py`
  terminal-share guard) + `tests/test_sessiondb_write_authority9u.py` (8).
  Flagged upgrade-fragility: the adapter uses vendored private
  `_execute_write`/`_write_sql`/`_read_all` (same WAL, not a second writer).

### Capability invocation matrix
- `voicebridge/invocation_matrix.py` (read-only) + `tests/test_invocation_matrix9u.py` (5).
  Findings: configured MCP servers are `unsupported` by design (no client-side
  connect/spawn); plugins without an in-process handler are `unsupported`
  (`PLUGIN_RUNTIME_UNAVAILABLE`); bundled skills all `reachable`.
- **Bug fixed here:** `voicebridge/skill_catalog.py` reported
  `enabled = status == "enabled"`, but the permissioned store uses `active`
  (`permissions.enable_skill` sets `active`), so operator-enabled skills showed
  disabled on dashboard/CLI/voice. Now `active`/`enabled` both map to enabled;
  regression added to `tests/test_catalog_sync9u.py`.

### Update apply rehearsal
- `tests/test_update_apply_rehearsal9u.py` (5) proves fast-forward + receipt +
  dirty-tree refusal + test guard + no destructive flag on a temp git repo.
- **Bug fixed here:** `voicebridge/update_manager.py::apply` ignored the
  configured `update.remote`/`update.branch` (a bare `git pull --ff-only` could
  fetch a different tracking upstream than `check()` reported). It now runs
  `git pull --ff-only <remote> <branch>` and verifies the post revision matches
  the remote branch head (typed mismatch otherwise).

### Gateway control integration + runbook
- `tests/test_gateway_control_integration9u.py` (7) proves the full
  `HC.dispatch` path with an injected fake `systemctl`; `docs/QUBIT_GATEWAY_OPERATOR_RUNBOOK_2026-09-21.md`.
- Agent-reported observation that `OPS.run_op(cfg,"gateway")` resolves to
  `UNKNOWN_OP` was **checked and is incorrect** — it returns
  `state=executed` with a real gateway list; no change made.

### Integrated evidence (round 2)
- Focused consolidated run: **271 passed, 1 xfailed, 1 deselected**.
- Deterministic artifacts all `--check` current; `graft build` + `graft check` OK.
- Pre-existing unrelated failures observed: `test_qubit_routes_parity8u::test_gates_never_touch_filesystem_or_network`
  and `test_parity_qubit.py::test_completion_matches_parser` (cron bash-completion).

---

## Third round — migration applied + catalog/plugin/MCP/session/gateway/dashboard

### 1. Runtime re-audit (live)
Qubit config `~/.config/qubit-voicebridge/voice.yaml`; state
`~/.local/state/qubit-voicebridge`; canonical runtime home
`~/.local/state/qubit-voicebridge/runtime`; migration source `~/.hermes`.
Live gateway: `qubit-voicebridge.service` active, pid present, port 8890 in use.

### 2. Migration dry-run + apply (Qubit-owned storage only)
- Dry run: source 122 sessions / 22,047 messages; catalog skills 162→60,
  plugins 1→0, MCP 2→0.
- Apply (`confirm=True`): **imported 122 sessions, 0 skipped, 22,047 messages,
  0 skipped roles, errors []**, 969 files added (skills 963, plugins 3, cron 3),
  MCP imported 2 (`cgraph-graft`, `openclaw-voice-bridge-graft`), conflicts **0**,
  unsupported **0**. Backup dir
  `~/.local/state/qubit-voicebridge/hermes-migration/20260921-232324`
  (source/ + destination/ + journal.json).
- Idempotency: 2nd apply imported 0 / skipped 122 / 0 added; a `profiles.json`
  mtime-only churn was fixed in `_import_model_profile`, after which a 3rd apply
  is byte-identical (content + mtime).
- Source `~/.hermes` was opened read-only; the only source churn is the live
  Hermes daemon's own WAL/heartbeat writes (not the migration).
- Rollback: `qubit product legacy-rollback --backup-dir <backup> --apply`.

### 3. Skills visibility + enabled mapping (all surfaces)
- Source 162 frontmatter skill names; 5 are environment/platform-incompatible
  in this host (`macos-computer-use` [macos], `hermes-s6-container-supervision`
  [s6], `kanban-orchestrator`/`kanban-worker` [kanban], `sdlc-review`
  [kanban toolset]) → **157 compatible, all 157 visible**. Destination catalog
  shows 158 (one Qubit-owned extra). Dashboard/CLI/voice share
  `voicebridge/skill_catalog.py` (158 entries; 157 enabled).
- `~/.hermes` had no `skills.disabled`; the imported state is NOOP.

### 4. Plugins
- Source `~/.hermes/plugins/hermes-achievements/` holds only legacy state JSON
  (no manifest/code) → **unsupported** (exact reason: legacy data dir).
- The vendored `hermes-achievements` engine is invocable via `qubit_plugins`;
  a `builtin.achievements` adapter was added to `plugin_runtime.invoke`
  (snapshot/status/rescan) with e-stop + journal confirmation + audit.
  `tests/test_plugin_invocation9u.py` (8).

### 5. MCP
- Imported 2 server definitions reference-only (no secret values). One read
  authority `mcp_servers.list_servers` confirmed for dashboard, CLI, voice,
  `/qubit-chat`, and the gateway handler.
- Client invocation is **operator-only** by design (no sanctioned external MCP
  transport; `MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED`, never connects/spawns).
  `/chat`'s vendored MCP tool layer does not read `voicebridge.mcp_servers`
  (owner-gated). `tests/test_mcp_invocation9u.py` (10).

### 6. Session continuity
- `agent.run_session` now resolves a canonical session id (aliases
  `voice`/`qubit:voice`/`qubit-voice` → `dashboard-chat`, configurable via
  `agent.session_id`) and folds bounded prior transcript (via
  `context_policy`) into the prompt; desktop PTT and `/qubit-chat` now share
  one conversation/transcript. `tests/test_session_continuity9u.py` (6).
- Terminal `/chat` (vendored TUI) and plain voice `_handle_conversation`
  remain out of scope (documented).

### 7. Gateway + update isolated qualification
- `tests/test_gateway_qualification9u.py` (26) + `tests/test_update_qualification9u.py` (15)
  cover status/start/stop/restart/failure/timeout/confirmation/e-stop/audit/
  recovery/stale-port, and update check/apply/rollback in a temp repo.
- Bugs found and fixed: gateway guard bypass (partial injection could reach the
  real spawner), un-redacted gateway output, raising runner escaping
  status/control (now typed timeout/failure), `update_manager.check` ignoring an
  injected runner, and `apply` ignoring configured remote/branch.
- Live host service was NOT mutated (fake systemctl + injected runner/spawner).

### 8. Dashboard matrix
- `tests/test_dashboard_matrix9u.py` (214 hermetic dispatch cases):
  411 routes (339 implemented / 71 gated / 1 stream); **168 backend-verified,
  243 OPEN**; 0 IMPLEMENTED (no real provider/browser E2E).
- Two systemic defects found and **fixed here**:
  - compat `_audit` called `log_event(event, {dict})` against
    `log_event(event, **fields)`, so every compat audit write raised and was
    swallowed → the journal was never written. Fixed; regression
    `tests/test_compat_audit_journal9u.py`.
  - `_compat_confirm` did not enforce e-stop, so confirm-gated mutations
    (cron/mcp/webhooks/profiles/...) could proceed while latched. Now refused
    with typed `EMERGENCY_STOP` from the one gate.

### 9. `/chat` and `/qubit-chat` live
- `/qubit-chat` Brain4: real turn against local ollama → reply `pong` in 503 ms,
  persisted 2 messages in the canonical store, receipt carried dynamic-context
  routing (tier `fast`, window 2048), cancellation typed `NO_ACTIVE_TURN`.
- `/chat` real-provider: **operator-gated**. No configured profile satisfies the
  65,536-token floor (brain4=4096; migrated `qubit-model` ctx=0 and not
  executable), and `env:OPENCODE_GO_API_KEY` does not resolve (`resolve_key`
  reads process env only, not the runtime `.env`). Exact prerequisite: an
  allowlisted, policy-enabled profile with ≥65,536 context and a resolvable
  credential.

### 10. Deterministic + tests
- Four generators (`page_coverage`, `route_ownership_matrix`,
  `compat_handler_reachability`, `identifier_rename`) each regenerated twice →
  **byte-identical**; all `--check` current.
- `graft build` + `graft check` OK.
- Consolidated focused run: **389 passed, 1 xfailed, 1 deselected**.
- Final full matrix NOT run (implementation not frozen).





---

## Fourth round — 10 parallel workstreams + integration

- **MCP client**: real brokered stdio/http invocation (`voicebridge/mcp_client.py`),
  allowlist + e-stop + confirmation + timeout + process-group kill, secret
  redaction. Wired to `mcp.app.callTool`; I added the CLI `qubit mcp invoke`
  and voice `mcp_invoke` entry points with typed error handling, and fixed
  `invocation_matrix` to classify MCP servers from the per-record
  `invocable` flag. `tests/test_mcp_client9u.py` (20).
- **Terminal bridge**: `voicebridge/terminal_bridge.py` merges the Qubit MCP
  registry into the runtime `config.yaml` at spawn (reference-only) and maps
  the terminal session to the canonical id; found a real vendored bug
  (`QUBIT_TUI_RESUME` vs `HERMES_TUI_RESUME`). 14 tests.
- **Voice store convergence**: plain voice conversation now persists through the
  canonical store with bidirectional context. 8 tests.
- **Dashboard backend closure**: real curator pause/run, memory reset, hooks
  CRUD+update, env e-stop, config read-side redaction. 16 tests.
- **Credentials**: `env:` refs now resolve from the canonical runtime `.env`
  (never logged) and per-profile `policy_enabled`/`allowlisted`/`context_tokens`
  overrides exist. 12 tests.
- **Branding**: scan + `QUBIT_BRANDING_RULING_REQUEST_2026-09-21.md` (5 shipped
  residuals: 4 allowlist-conflict, 1 uncovered label). No frontend change
  (owner ruling pending). 1 classification test.
- **OpenClaw review**: 95 OPERATOR_ONLY rows classified 68 external-prerequisite
  / 27 architecture-gated, with a deterministic generator and coverage test.
- **Voice E2E**: 16 passed / 6 xfailed (browser-only); 3 product bugs reported
  (speech profile not reaching ASR/TTS seams, whisper model overwritten, compat
  audio ops bypassing the speech registry).
- **Update/packaging**: `update_manager` remote/branch truthfulness hardening +
  `scripts/clean_install_rehearsal.py`; 17 tests.
- **Invocation closure**: permissioned skills resolve to a real management
  invocation; remaining gap (no bundled-payload slash execution) documented.

Integration fixes in this round: CLI/voice MCP entry points + typed errors;
`invocation_matrix` MCP classification; outdated test stub in
`test_provider_migration.py`; regenerated all deterministic artifacts and the
vendor comparison manifest (documented the two Qubit-added frontend test files
in `_WAVE3_ADDED`). Hermes ledger now **465 IMPLEMENTED / 108 SAFE_EQUIVALENT**
(573 rows). Final focused batches: **430 + 177 + 128 passed, 7 xfailed**.
Remaining known failure: `test_branding_bundle_scan9u.py` (owner-gated).
