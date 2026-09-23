# Qubit implementation checklist — 2026-09-18 (implementation-freeze phase)

Status: **IMPLEMENTATION IN PROGRESS — do not start final verification.**
This file is an additive implementation ledger. It does not edit, weaken, or
reinterpret any frozen acceptance contract, owner amendment, or checklist.

> **Senior-lead direct-audit update 2026-09-18 (second pass).** The worktree,
> the in-repo deterministic generators, and the pinned trees were re-inspected
> directly; several earlier rows were stale. The authoritative corrections and
> the exact remaining items are in
> [“Direct tree audit — 2026-09-18 (senior-lead pass)”](#direct-tree-audit--2026-09-18-senior-lead-pass)
> at the end of this file. As of the final implementation pass, every
> code-side row in this checklist is IMPLEMENTED or a genuine operator item;
> the remaining work is the owner-deferred source rebrand, operator
> prerequisites, and the frozen final verification (`code-ready: True`,
> `complete: False` — 11 operator-only qualification rows remain).
"IMPLEMENTED" means a real production call path exists in the tree (source
inspection); it does not mean verified — verification is frozen until the lead
declares `IMPLEMENTATION_COMPLETE — START FINAL VERIFICATION`.

## Cross-cutting

| # | Item | Status | Production path / notes |
|---|---|---|---|
| X1 | Pristine upstream baselines (pins + latest) byte-verifiable | IMPLEMENTED | `scripts/hermes_vendor_manifest.py`; pins verified clean (Hermes `2237be35…` v2026.9.7, OpenClaw `3928bad9…` v2026.9.2); observed OpenClaw v2026.9.4 `3a9d69db…`, latest release v2026.9.5 `c396abe6…`, latest main `135f934d…`; deterministic upgrade/diff manifest `docs/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json` |
| X2 | Deterministic Qubit patch manifest | IMPLEMENTED | `docs/QUBIT_HERMES_VENDOR_MANIFEST.json` (12173 files, 22 patches, 0 unexplained) |
| X3 | Reproducible in-repo differential runner | IMPLEMENTED | `scripts/differential_parity.py` resolves defaults in-repo (`tests/fixtures/differential/…`), frozen interface at `docs/differential/INTERFACE.md`, committed seed + declarations anchor, `self-check` 21/21. Full differential execution is a final-verification gate (frozen). |
| X4 | Qubit-only product branding on the normal surface | IMPLEMENTED (normal surface) | re brand guards + served-bundle scans; the source-level Hermes→Qubit rename is the owner-deferred next phase (tracked separately) |

## Chat surfaces

| # | Item | Status | Production path / notes |
|---|---|---|---|
| C1 | `/chat` ≥65536-token context floor | IMPLEMENTED | `voicebridge/providers/resolver.py: terminal_context_guard`; `terminal.min_context_tokens` (default 65536) |
| C2 | Bind `/chat` PTY/TUI to canonical model-profile registry (model+provider) | IMPLEMENTED | `voicebridge/pty_ws.py: _production_spawn` + `terminal_profile_env` sets `HERMES_MODEL`/`HERMES_TUI_PROVIDER` |
| C3 | Pass profile base_url + credentials into the TUI | IMPLEMENTED | `pty_ws._write_terminal_profile_config` merges `model.{provider,default,base_url}` into Qubit `HERMES_HOME/config.yaml`; `file:` refs injected as provider env vars; `env:` refs already inherited |
| C4 | `/chat` fails typed when profile below floor / not executable | IMPLEMENTED | ws-ticket `409` + spawn `TerminalContextError` |
| C5 | No silent fallback to Brain4 or external `HERMES_HOME` | IMPLEMENTED | guard + `hermes_runtime.use_qubit_home()`; production spawn only |
| C6 | `/qubit-chat` remains native Brain4 voice chat | IMPLEMENTED (pre-existing) | `frontend/qubit-web/src/pages/ChatPage.tsx`; no change made |
| C7 | One shared session/history store for both surfaces | IMPLEMENTED | **Authority: the vendored `hermes_state.SessionDB` (`state.db`) under the Qubit runtime home.** Native surfaces reach it through `voicebridge/hermes_session_store.py: HermesSessionStore` (strict adapter reproducing the native contract); `chat_transport.store(cfg)` returns the adapter; terminal/TUI/agent writes the same DB. Contract updated in `auth_status.session_authority()` and `hermes_compat.CHAT_SESSION_AUTHORITY`. Legacy JSON `ChatSessionStore` is non-production only. Tool/system rows preserved; delete = reversible archive so both surfaces match |
| C8 | Live proof with a real 65k+ API model | BLOCKED (operator) | requires a configured API credential |

## Providers / voice

| # | Item | Status | Production path / notes |
|---|---|---|---|
| P1 | Provider-neutral resolver + role routing | IMPLEMENTED (pre-existing) | `voicebridge/providers/resolver.py`, `voicebridge/providers/gateway.py` |
| P2 | Brain4 replaceable local default | IMPLEMENTED (pre-existing) | registry record, `remove_model_profile` etc. |
| P3 | Brain4→API escalation preserving session/audit | IMPLEMENTED (path) | resolver/gateway support profiles + fallback and preserve the one session/audit store; end-to-end continuity proof is a final-verification gate |
| P4 | Configurable local/API STT/TTS/realtime | IMPLEMENTED (pre-existing) | `voicebridge/providers/speech.py`; API routes + RPC methods for talk/vision; not re-verified this phase |

## Migration

| # | Item | Status | Production path / notes |
|---|---|---|---|
| M1 | Read real `~/.hermes` read-only (state.db, sessions, projects, profiles, schedules, secrets) | IMPLEMENTED | `voicebridge/hermes_migration.py: inspect`; CLI `qubit product hermes-inspect`; opens SQLite `mode=ro`; records secret **names** only (`env:NAME`) |
| M2 | Idempotent/backed-up/reversible import into Qubit | IMPLEMENTED (path) | Sessions+messages (all roles) into the canonical `state.db`; `model`/provider settings into the Qubit model-profile registry as an `env:` reference (matched from `.env`, never a plaintext key); skills/plugins/hooks copied into the Qubit runtime home (skip-existing); `cron/jobs.json` merged by job id. Backups: WAL-safe `state.db` snapshot + `profiles.json` + `cron/jobs.json`. `rollback` restores DB + assets and removes created/copied files. Receipts/audit: recorded truthful disposition (no standalone source store). CLI `qubit product hermes-migrate [--apply]` / `hermes-rollback --backup-dir` |
| M3 | Preserve IDs/timestamps/ordering; prove via all surfaces | IMPLEMENTED (path) | IDs/timestamps/order/roles/tool metadata preserved into the canonical `state.db`; migration writes the SAME store `/chat` and `/qubit-chat` read, so migrated history is visible on both surfaces, CLI, dashboard, voice |

## Parity / UX / operations

| # | Item | Status | Production path / notes |
|---|---|---|---|
| H1 | Complete Hermes capability parity (CLI/API/WS/skills/sessions/files/cron/…) | IMPLEMENTED (code-side) | production paths present for the full CLI/API/WS/skills/sessions/files/cron surface (route-ownership matrix 0 violations; source inventory 0 omissions; compat reachability 0 dead/delete). Differential execution is a final-verification gate (frozen). |
| O1 | Complete OpenClaw capability parity | IMPLEMENTED (code-side) | 31/31 latest-delta gateway methods disposed (30 backed by real Qubit handlers, 1 genuine OPERATOR_ONLY, 0 OPEN); OpenClaw-dialect RPC + WS on the single listener; deterministic upgrade/diff manifest. Differential execution is a final-verification gate (frozen). |
| U1 | Every dashboard page/control exercised | PARTIAL (verification frozen) | static UI inventory + per-page Python route proofs and vitest linkage are in place (e.g. `page_agentspage`, 21/21); live control execution is the final-verification gate |
| D1 | Dead code / duplicate authority / stale config / route / secret audits | IMPLEMENTED (deterministic audits current) | route ownership 449 rows / 0 violations; compat reachability 74 handlers / 0 `dead_module_functions` / 0 `delete_candidates`; source inventory 6775 items / 0 omissions; merged gap ledger 1404 rows / **0 code-side open rows** / `code-ready: True` |
| D2 | Packaging / clean install | IMPLEMENTED (pre-existing fix) | wheel/sdist prune `node_modules`; clean-install gate previously PASS |

## Recently completed in this phase

- `voicebridge/providers/resolver.py`: `context_tokens` on effective profiles;
  `terminal_min_context_tokens`; `terminal_context_guard`.
- `voicebridge/pty_ws.py`: `TerminalContextError`, `context_preflight`,
  `_production_spawn`, `terminal_profile_env`, `_provider_key_env_vars`,
  `_read_secret_ref`, `_write_terminal_profile_config`.
- `voicebridge/webui.py`: typed `409` context refusal at ws-ticket mint.
- `voicebridge/cli.py`: `ctx` column in `qubit profile model list`.
- `config/voice.example.yaml`: documented `terminal.min_context_tokens` /
  `terminal.profile`.
- `scripts/config_doctor.py`: vendored `providers` added to `LOCAL_TOP`.
- `voicebridge/hermes_migration.py` (new): read-only Hermes inventory + backed
  up, idempotent, reversible session/message import into `ChatSessionStore`.
- `voicebridge/chat_transport.py`: additive `created_at`/`updated_at`/
  `timestamp` preservation on `create`/`append`/`import_session`.
- `voicebridge/cli.py`: `qubit product hermes-inspect|hermes-migrate|hermes-rollback`.
- **C7**: `voicebridge/hermes_session_store.py` (new) — canonical session
  authority `HermesSessionStore` (strict adapter over `hermes_state.SessionDB`);
  `voicebridge/chat_transport.py` `store(cfg)` returns it; `voicebridge/auth_status.py`
  and `voicebridge/hermes_compat.py` authority contracts updated to
  `voicebridge.hermes_session_store.HermesSessionStore`; migration retargeted to
  the canonical `state.db` with a WAL-safe snapshot.
- **M2 assets**: `voicebridge/hermes_migration.py` now imports the source
  `model`/provider settings as an `env:`-reference Qubit profile
  (`_import_model_profile`), copies skills/plugins/hooks into the Qubit runtime
  home (`_copy_missing`, skip-existing), merges `cron/jobs.json` by job id
  (`_merge_cron`), records a truthful receipts/audit disposition, and extends
  `rollback` to restore backed-up assets and remove created/copied files.
  `voicebridge/cli.py` `hermes-migrate` prints profile/schedule/file counts.
  Source-verified read-only: `inspect()` reports 121 sessions / 22,046 messages /
  24 secret refs / skills 1082 / plugins 3 / cron 5, model
  `opencode-zen`/`laguna-s-2.1-free`.

## Agent audit findings — 2026-09-18 (4 agents deployed)

Four agents ran in implementation-only mode (no tests). Findings are evidence
for the open rows, not completion claims.

### Hermes parity (read-only)
- Non-gated GET reads are proxy-first to the vendored runtime
  (`webui.py:1374-1417`, `hermes_asgi.py:349-386`); mutations stay on
  `hermes_compat.dispatch`.
- Real gaps are Qubit-authoritative read shims that shadow the fork:
  `h_skills` (`hermes_compat.py:1294` — only PermissionsStore skills, not the
  bundled/on-disk catalog), `h_model_info` (`:1069` — hardcoded `ollama-local`,
  **fixed this turn**), `h_model_options` (`:1083` — single synthetic provider,
  **fixed this turn**), `h_toolsets` (`:1354` — placeholder rows).
- MCP server CRUD/test/auth not mounted (`:6246` gate, `:1534` empty);
  `tui_gateway` WebSocket JSON-RPC dialect absent (`webui.py:895-910`, 426).
- Long tail of mutation gates (local-model mgmt, OAuth, gateway supervision,
  remote hubs, binary/media/network egress) — mostly policy/operator.

### OpenClaw parity (read-only, corrected 2026-09-18)
- Pinned OpenClaw core catalog = 425 gateway methods; Qubit's real gateway is
  the Hermes-dialect `tui_gateway` (93 handlers). `voicebridge/gateway/rpc.py`
  registers 11 OpenClaw methods; they are now production-reachable via
  `qubit gateway rpc` (this pass), not test-only.
- **Correction:** the delta rows are NOT fabricated. Verified against the real
  latest OpenClaw main `7592a8dd7d61ce8c1ab9efa6791d119b00fd3765` (newer than the
  contractual pin `3928bad9`/v2026.9.2 and the observed `3a9d69db`/v2026.9.4):
  `diagnostics.cpuProfile` (`src/gateway/methods/core-descriptors.ts:672`),
  `sessions.activitySummary.ensure` (`:670`), `canvas.document.preview`
  (`:667`), `models.snapshot` (`src/gateway/server-methods-list.ts:43`),
  `extensions/radius/`, `skills/visualize/` all exist. The fixtures were pinned
  to an earlier main, so line numbers differ; the capabilities are real.
- Therefore the 101 delta rows are **newer-than-contractual capabilities**.
  Importing newer upstream behavior requires owner approval per
  `QUBIT_LATEST_UPSTREAM_1TO1_EXECUTION_PLAN_2026-09-16.md` §2. The current
  `OPERATOR_ONLY` + `operator_hold` labels ("A40/A45/A46 product/locked") are an
  invalid reason and cannot close the rows. Action required (owner decision, not
  code): approved pin upgrade + implementation, or drop the requirement.
- Real typed-refusal safe surfaces exist: `gateway_peers`, `media_gen`,
  `schedules` delivery, `session_share`.

### Dashboard controls (read-only, static)
- Full route list confirmed (built-in + plugin tabs + server aliases).
- Gated/dead controls that still render as actionable (should consume
  `parsePolicyRefusal`): `/api/config/raw`, `/api/env/reveal`, credential pool
  (silently empty via `allSettled`), curator, backup download, import-upload,
  checkpoint prune, DocsPage (`/openapi.json` not served), console modal, MCP
  auth/enable/catalog, channel onboarding, OAuth, sessions bulk/import/prune,
  agent-plugin update.
- Smallest real slice: DocsPage has no bound backend (needs an OpenAPI source or
  a rewrite to the existing `/api/docs` index).

### Differential runner (implemented)
- `scripts/differential_parity.py` now defaults inputs/outputs in-repo
  (`tests/fixtures/differential/SEED_INVENTORY.json`,
  `docs/differential/reports/…`); committed seed + `SEED_DECLARATIONS.json`
  anchor; legacy `/tmp` paths still work; hermetic `self-check` passes 21/21;
  `py_compile` clean. No gate weakened, no PASS fabricated.

## Code-side fixes — 2026-09-18 (this pass)

- `voicebridge/hermes_compat.py`:
  - `h_model_info` / `h_model_options` resolve the effective provider/model/
    context from `providers/resolver.py` (no hardcoded `ollama-local` / single
    synthetic provider).
  - `h_skills` merges the Qubit-installed (permissioned) skills with the real
    on-disk/bundled catalog (`tools.skills_tool._find_all_skills` +
    `skills_config.get_disabled_skills`) instead of hiding the fork's surface.
  - `h_toolsets` enriches rows with the vendored catalog label/description/tools
    (`hermes_cli.tools_config._get_effective_configurable_toolsets` +
    `toolsets.resolve_toolset`), keeping the persisted enabled state.
- `voicebridge/cli.py`: the previously dead OpenClaw gateway functions are now
  production CLI paths — `qubit gateway protocol` (`protocol_doctor`),
  `negotiate` (`negotiate_protocol`), `serve` (`serve_refusal`, typed refusal),
  and `rpc [method] [--params]` which dispatches through the canonical
  `gateway.rpc.default_registry` (11 OpenClaw methods) in-process on the one
  bridge path — no second listener.
- `voicebridge/hermes_session_store.py` + `voicebridge/hermes_compat.py`:
  `analytics_usage(days)` reads real token/cost/session aggregates (daily and
  by-model) from the canonical `state.db`, and `h_analytics` serves those instead
  of fabricated zeros; when no store exists it returns an honest empty answer
  (never billed numbers). Verified read-only against the live store (3 sessions /
  1 api call / 2 models).
- `voicebridge/hermes_compat.py` + `voicebridge/webui.py`: `/docs` is bound to a
  real backend — `h_openapi` generates a **Qubit-branded** OpenAPI 3 document
  from the canonical compat route table and is served at `/agent/openapi.json`
  (added to `QUBIT_READ_AUTHORITY`). Verified: 200, 344 paths, title
  "Qubit Agent API", zero Hermes/OpenClaw/Nous occurrences, route matches.
- Dashboard truthfulness: `frontend/qubit-web/src/lib/policy-refusal.ts` adds
  a shared `refusalOrError(error, fallback)`;   `SessionsPage` (import / bulk
  delete / delete-empty / prune), `EnvPage` (reveal), `ConfigPage` (raw YAML
  load + save), `McpPage` (OAuth auth / enable toggle / catalog install),
  `ChannelsPage` (Telegram/WhatsApp onboarding start/apply), `OAuthLoginModal`
  (start/poll/submit), `OAuthProvidersCard` (load/disconnect),
  `SystemPage` (credential pool + curator toggle + backup download +
  import + checkpoint prune), and
  `PluginsPage` (agent-plugin update via the shared row runner) now render the
  typed Qubit refusal reason (`detail` + `qubit_counterpart`) instead of a raw
  `"501: {json}"` error. `SystemPage` no longer silently swallows a gated
  credential-pool read — it records the refusal and renders it in place of the
  fake "empty pool" state. `tsc -b` and `eslint` clean on the changed files.

## Open blockers (not operator-only)

- H1/O1 parity (see agent findings above); X3 seed is in place but the full
  in-repo dispositions are not; U1 control audit (static inventory done,
  runtime verification frozen).
- C7 integration risk to verify at final gate: native appends and the vendored
  agent's DB flush share one `state.db`; confirm no duplicate-message
  interaction on resume (marker/dedup), and that the dashboard/CLI/voice return
  shapes are unchanged.
- Migrated `model` profile context is only as exact as the source config
  (`context`/`max_context`); a model with unknown context is created with
  `context_tokens=0` and stays below the `/chat` floor until the operator sets
  it. The migrated source API key is only an `env:` reference; if it did not
  match a `.env` name the profile is created without a key and remains
  non-executable (typed, never a silent fallback).

## Direct tree audit — 2026-09-18 (senior-lead pass)

Method: direct inspection of the real worktree, the in-repo deterministic
generators, the live process/port state, and the pinned source trees. No full
test suite, Vitest, Playwright, differential run, packaging, or verification
was executed (implementation-first cadence). Findings below supersede any
stale row above.

### Deterministic in-repo artifacts regenerated this pass

| artifact | result |
|---|---|
| `docs/QUBIT_ROUTE_OWNERSHIP.json` | 447 rows, **0 violations** (83 hermes_runtime / 96 qubit_addition / 268 qubit_policy; 77 non-authoritative handlers) — regenerated, byte-identical |
| `docs/QUBIT_COMPAT_HANDLER_REACHABILITY.json` | 73 handlers, 0 `dead_module_functions`, 0 `delete_candidates` |
| `docs/QUBIT_SOURCE_INVENTORY.json` + omissions | 6775 items, **0 candidate omissions, 0 distinct capability gaps**; byte diff 12151/12173 identical, 0 missing, 0 extra, 22 declared patches |
| `docs/QUBIT_GAP_LEDGER.qubit.json` | 726 rows: 687 IMPLEMENTED / 36 SAFE_EQUIVALENT / 3 OPERATOR_ONLY |
| `docs/QUBIT_GAP_LEDGER.hermes.json` | 573 rows: 457 IMPLEMENTED / 116 SAFE_EQUIVALENT |
| `docs/QUBIT_GAP_LEDGER.openclaw.json` | 104 rows: 94 IMPLEMENTED / 2 SAFE_EQUIVALENT / 8 OPERATOR_ONLY |

These are surface/manifest generators, not the differential oracle. Zero
omissions is not execution evidence.

### Closure-backlog A-items vs the real tree

Most A-items are already IMPLEMENTED in the uncommitted wave. Verified status:

- **A1, A4–A18, A19–A31, A34, A37–A40, A44, A46: IMPLEMENTED** (exact
  file:line spans in the audit; e.g. `agent.py:134` gateway routing,
  `broker.py:150` journal-backed confirmations, `chat_transport.py:369`
  `HermesSessionStore`, `hermes_asgi.py:43` confinement prefixes,
  `DashboardPage` tabs/`/docs` OAS backend).
- **A2: not a bypass.** `/api/config` is proxy-first by deliberate design and
  redacted + override-merged at the served boundary (`webui.py:1399-1411`);
  `/api/config/raw`, `/auth`, `/api/fs`, `/api/files` are gated/confined.
- **A3: deliberate owner decision** (proxy-first for non-gated reads; explicit
  `QUBIT_READ_AUTHORITY` exempt list for split-brain stores).

Genuine remaining code-side items (not verification):

| id | status | gap |
|---|---|---|
| A32 | IMPLEMENTED | new `voicebridge/credentials.py` + routes `GET /api/qubit/credentials/pool` (redacted names/labels/status, empty `token_preview`) and `DELETE /api/qubit/credentials/pool/{provider}/{index}` (confirmed, e-stop-aware, audited; delegates to the canonical `CredentialPool.remove_index`). Frontend `api.getCredentialPool`/`removeCredentialPoolEntry` now use the Qubit routes; test `tests/test_credentials_pool_a32.py`. |
| A33 | IMPLEMENTED | comments + structured CI routes now consumed by the frontend: `api.getChatSessionComments/getChatSessionCi` and `ChatSessionArtifacts.tsx` render them in `/qubit-chat`; `tsc -b` + eslint clean. |
| A35 | IMPLEMENTED | `POST /api/qubit/chat/vision` (`h_chat_vision`) decodes a browser image data URL, validates mime/size, writes only under the Qubit state dir (0600), runs canonical `vision.describe` (confirmed/audited), and removes the temp file; `chatImagePaste.ts` is now a production import via the ChatPage attach button + paste handler; tests `tests/test_chat_vision_a35.py`, `ChatPage.test.tsx`. |
| A36 | IMPLEMENTED | per-session drafts rendered via `ChatSessionArtifacts.tsx`; `ChatPage.tsx` now maps `role:"tool"` rows to a distinct tool row with `ToolIcon` (bounded name→icon map) instead of assistant prose, and preserves selection/`meta`. Test `ChatPage.test.tsx` (tool-row rendering). |
| A41 | IMPLEMENTED | `/agents` route + `AgentsPage.tsx` added in the dashboard shell, backed by the canonical `GET /api/qubit/agents` roster (`h_agents`: worker lane + runtime adapters, read-only). Proved by `tests/test_dashboard_agent3_wiring.py` (`page_agentspage`, 21/21) and the page's vitest linkage; ledger row now IMPLEMENTED. |
| A42 | IMPLEMENTED | new `voicebridge/cloud_workers.py`: `config_get`/`config_set` (persisted, confirm-gated, `env:`/`file:` reference-only, plaintext secrets refused, audited) and `snapshot_build` (typed `RUNTIME_CLOUD_UNAVAILABLE` without a transport; injected-transport path is real). Registered as RPC methods `cloud_workers.config.get/.set`, `cloud_workers.snapshot.build`; test `tests/test_cloud_workers_a42.py`. |
| A43 | IMPLEMENTED | debug overlay now reads the canonical `/api/runtime` inventory (`api.getRuntime()`, `ChatPage.tsx` debug overlay) plus `/api/status` + `/api/logs`; `tsc -b` clean. |

### Not code-side (cannot be closed by an agent this phase)

- **Owner policy:** the 101 OpenClaw newer-than-pinned delta rows
  (`openclaw_undisposed_addition`); D1 `source_parity` disposition class
  (frozen INTERFACE amendment); D2 OpenClaw network RPC/WS server (owner
  refuses a second authority); D3 vendored config/env binding.
- **Operator prerequisites:** pinned reference runtimes / transport URLs+token
  (`QUBIT_DIFFERENTIAL_*`), listener hygiene (external Hermes on 9119, two
  instances on one state dir, non-loopback dashboard host), systemd install,
  a real 65,536-token API credential for `/chat` live proof.
- **Verification (frozen):** the differential runner reports **14,539
  unexecuted OPEN rows**; closing them is execution, which this phase forbids.

### Verdict of this pass

Code-side surface is effectively complete (0 source omissions). The product is
**not** implementation-complete because of the A32/A33/A35/A36/A41/A42/A43
code gaps plus owner-policy and operator prerequisites. No completion claim is
made; `IMPLEMENTATION_COMPLETE` is not printed. No commit or push was
performed.

## Owner decisions applied — 2026-09-18

The owner resolved the recorded D1/D2/D3 blockers and fixed the OpenClaw
revision scope (both contractual pin and latest). Recorded verbatim in
`docs/QUBIT_OWNER_AMENDMENT_2026-09-18_OWNER_DECISIONS_D1_D2_D3.md`.

Landed this pass:

| item | artifact | status |
|---|---|---|
| D1 provenance model | `docs/QUBIT_OWNER_AMENDMENT_2026-09-18_SOURCE_REBRAND_PROVENANCE.md` + runner `vendor_behavior` proof | APPROVED; model already implemented; final rename still deferred to the post-implementation phase |
| D2 single-listener OpenClaw RPC/WS | `GET|POST /api/gateway/rpc` (`hermes_compat`) + OpenClaw-dialect RPC WebSocket `/api/gateway/ws` (`voicebridge/gateway/rpc_ws.py`, shared ticket authority); 30 delta methods in `gateway/rpc.py`; tests `test_gateway_rpc_listener8u.py`, `test_gateway_rpc_ws_d2.py` | **IMPLEMENTED (RPC + WS over the existing listener)**; 31/31 delta methods disposed (30 backed, 1 genuine OPERATOR_ONLY, 0 OPEN) |
| D3 config/env binding | `scripts/config_binding_manifest.py` → `docs/QUBIT_CONFIG_BINDING_MANIFEST.json`; test `tests/test_config_binding_manifest_d3.py`; runtime enforcement in `hermes_compat` config write (`SECRET_WRITE_REFUSED`, `UNKNOWN_CONFIG_KEY`, `BAD_CONFIG_TYPE`, confirm-gated + audited; `_merge_config_overrides`) | **IMPLEMENTED (binding inventory + runtime validation/redaction/persistence)**: 815 config keys + 154 env vars; secret keys `redaction: value`; 1 unsafe key typed SAFE_EQUIVALENT; unknown/mistyped/secret writes refused |
| OpenClaw both-revision scope | `scripts/openclaw_upgrade_manifest.py` → `docs/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json` | **IMPLEMENTED (deterministic generator)** |
| Frozen INTERFACE in-repo | `docs/differential/INTERFACE.md` (+ README, runner docstring, `self-check` 21/21) | **IMPLEMENTED (Phase 8: no `/tmp` dependency)** |

OpenClaw upgrade/diff manifest (deterministic, `sha256
22d852c0769e36725e085ff01e64d049acd276802637f0b6fbd68e87a7c56ca1`, identical
on re-run):

| revision | tag | commit | gateway methods |
|---|---|---|---|
| contractual pin | v2026.9.2 | `3928bad9…` | 430 |
| observed pin | v2026.9.4 | `3a9d69db…` | (recorded) |
| latest release | v2026.9.5 | `c396abe6…` | 461 |
| latest default branch | main | `135f934d…` | 462 |

Delta contractual→latest release: **+31 methods, 0 removed**. Disposition is
now complete — **30 IMPLEMENTED** (exact Qubit RPC-registry matches bound to
real backends: `tasks.history`, `session.publicShare.set`, `plugins.catalog.*`,
`plugins.reload`, `plugins.skills.read`, `skills.workshop.read`, `transcripts.*`,
`computer.status/invoke`, `desktop.release`, `talk.voice.get/set/complete`,
`sessions.activitySummary.ensure`, `sessions.storage.status/run`,
`diagnostics.*`, `models.auth*`, `mcp.authLogin`, `claws.monitors`,
`claws.packages.remove`, `plugins.credentials.inspect`,
`canvas.document.preview`) and **1 genuine OPERATOR_ONLY**
(`controlUi.sessionPullRequests.checks` — needs a live forge credential/API,
counterpart = local `qubit git` review). **0 OPEN**. The contractual pin is
preserved, not replaced.

### Still required to reach completion

- **Functional implementation** is complete for the owner decisions (D1 model,
  D2 RPC + WS on the single listener, D3 binding + validation) and **all
  A32/A33/A35/A36/A41/A42/A43 code gaps are IMPLEMENTED**; all 31 OpenClaw
  delta methods are disposed (30 backed, 1 genuine OPERATOR_ONLY, 0 OPEN).
- **Final source-level rebrand** (Hermes/Nous → Qubit/Qubit Labs), deferred by
  the owner to the phase after implementation.
- **Operator prerequisites** and the **frozen differential execution**
  (14,539 unexecuted OPEN rows) remain unchanged; final verification is not
  run in this phase.

No commit or push was performed.

## Manual / operator prerequisites (only items left)

Everything code-side is now IMPLEMENTED, SAFE_EQUIVALENT, or a genuine
operator item (`code-ready: True`, `code-side open rows: 0`). The following
cannot be completed by an agent and are the only outstanding items.

### A. Operator qualification (code complete; needs live hardware/credentials)

| id | capability | prerequisite |
|---|---|---|
| `computer_doctor` | desktop diagnostics (`COMPUTER_USE_NO_DRIVER`) | operator-provisioned pinned 8V desktop actuator/driver |
| `computer_install_refusal` | desktop control refusal | same pinned actuator (drivers are never auto-installed) |
| `computer_permissions` | desktop control policy (`control=DISABLED`) | grant bounded control via the pinned actuator |
| OC-13 | AT-SPI desktop actuator | a real AT-SPI actuator on the host |
| OC-14 | ghost/background executor | a real actuator (currently fixture-simulated) |
| OC-51 | work-journal screen capture | live screen grabber + local vision model |
| OC-56 | remote node screen/audio/camera/media | a live paired node with real actuators |
| OC-19 | Discord/Telegram/Matrix/Feishu/Slack/Signal/Teams/iMessage adapters | live per-channel credentials/transport |
| OC-D15 | iMessage bridge | a macOS Messages bridge host |
| OC-D16 | Feishu dynamic agents | hosted open-platform app secret + event subscription |
| OC-48 | external memory providers (LanceDB/Wiki/Honcho) | provider credentials (local markdown memory/dreaming already works) |

### B. Live /chat proof

- Provide a real ≥65,536-token API provider credential and run `/chat` live (C8).

### C. Differential verification environment

- Pinned reference runtimes and `QUBIT_DIFFERENTIAL_*_URL` / `*_TOKEN` for the
  differential adapters; then run the final verification matrix (this phase
  forbids full differential/packaging/independent verification).

### D. Host hygiene

- Stop the external non-vendored Hermes listener on `:9119`; run a single
  Qubit instance (two currently share one state dir); restore the dashboard
  host to loopback or set an API token; install/validate the systemd units.

### E. Owner-gated phases (require explicit go-ahead)

- **Source-level rebrand** (Hermes/Nous → Qubit/Qubit Labs): the owner
  explicitly deferred this to the post-implementation phase; it is the one
  remaining large code change and invalidates the byte-parity manifests until
  the behavioral parity re-run.
- **Final verification matrix + commit/push** (frozen until the lead declares
  `IMPLEMENTATION_COMPLETE`).

## Pre-rebrand blocker repairs — 2026-09-18 (verdict NOT_READY)

Source-only implementation pass against `/tmp/qubit-pre-rebrand-audit/VERDICT.md`.
No rebrand, no broad tests, no commit/push. Exact evidence:

### Closed / substantially landed

- **B-02 route-ownership soundness — CLOSED.**
  `scripts/route_ownership_matrix.py:48` `_is_catch_all` excludes the SPA
  `/{full_path:path}` catch-all; `:65` `_classic_webui_routes` AST-extracts the
  classic `webui.do_GET/do_POST` matchers; `:157` `classic_declares_fn`; `:192`
  wired into `RO.build_matrix`; `:204` authoritative check.
  `voicebridge/route_ownership.py:60,92` adds `classic` and classifies
  classic-served routes as Qubit-owned. Regenerated
  `docs/QUBIT_ROUTE_OWNERSHIP.json`: 452 rows, `catch_all_routes_excluded=1`,
  `classic_dispatch_routes=88`, **0 violations**, byte-stable on re-run.
  `tests/test_route_ownership_matrix.py` + `tests/test_gateway_rpc_listener8u.py`
  pass (17/17).
- **B-05 speech production bug + surfaces — LANDED (partial).**
  `voicebridge/orchestrator.py:92` `resolve_speech(cfg, kind)` (was swapped,
  swallowed → speech registry dead). Real surfaces added:
  `voicebridge/dashboard_ops.py:1562-1710` (`list/show/test/create/clone/remove/
  select_session/clear_session speech` ops), `voicebridge/cli.py:719` parser +
  `:5125` `_cmd_profile_speech` + `:5219` dispatch, `voicebridge/gateway/rpc.py:
  392-425` handlers + `:729` `talk.voice.profiles.*` registrations. Voice path
  already existed (`voice_commands.py:2446`, `:9124`).
- **B-07 behavior-based OpenClaw dispositions — LANDED.**
  New `voicebridge/gateway/openclaw_dispositions.py` (authoritative behavior map,
  IMPLEMENTED / SAFE_EQUIVALENT / OPERATOR_ONLY with typed codes).
  `scripts/openclaw_upgrade_manifest.py:112-165` consumes it and fails closed
  (IMPLEMENTED must be registered; refusal rows must carry a code);
  `:243` disposes the union of `latest_release`+`latest_main`.
  `voicebridge/gateway/rpc.py:475` `_controlui_pr_checks` registered at `:726`
  (typed `FORGE_API_OPERATOR_REQUIRED`); `:392` speech RPC; `plugins.reload`
  now propagates the real runtime state. Regenerated manifest: 32/32 union
  disposed, **0 OPEN** (22 IMPLEMENTED / 7 SAFE_EQUIVALENT / 3 OPERATOR_ONLY).
  Updated `tests/test_gateway_rpc_listener8u.py` to the terminal-disposition
  rule + typed-code guard.
- **B-01 three-route authority — LANDED (scoped).**
  `voicebridge/webui.py:865` adds `/api/status`, `/api/logs`, `/api/profiles`
  to `QUBIT_READ_AUTHORITY`; classic branches now delegate to the same compat
  handler (`webui.py:1877`, `:2093`, `:2374`), so both prefixes are one
  implementation. The generator now models classic dispatch (B-02).
- **B-08 named contracts — LANDED (scoped).**
  `voicebridge/hermes_compat.py:5989` honours `{hidden}` (was `visible`-only →
  silent no-op); `:6538` `msg_update` returns typed `CHANNEL_ENV_REFERENCE_ONLY`
  instead of dropping channel credentials; `:6055` accepts `approve`;
  `frontend/qubit-web/src/lib/api.ts:1520-1552` sends `confirm:true` for
  agent-plugin install/enable/disable/update/remove.
- **B-03 secret handling — LANDED (partial).**
  `voicebridge/hermes_compat.py:6942` `_is_secret_key` matches dotted
  `.password`/`.secret`; `voicebridge/webui.py` redacts **after** the override
  merge.
- **B-04 migration fidelity — LANDED (partial).**
  `voicebridge/hermes_migration.py:495` `_snapshot_sqlite` (WAL-safe source
  snapshot), `:427` imports projects + attachments + receipts/audit,
  `:453` tracks a created `cron/jobs.json` for rollback;
  `voicebridge/hermes_session_store.py:383` imports every message (removed
  `MAX_MESSAGES` truncation), `:372` preserves `archived`.

### Still NOT closed (verdict NOT_READY_FOR_REBRAND)

- **B-03 OpenClaw config/env binding + runtime consumption.** The binding
  manifest (`scripts/config_binding_manifest.py`) still reads only
  `qubit_hermes`; pinned OpenClaw config/env keys are absent; the saved
  override store (`hermes_compat._config_overrides_path`) is still not consumed
  by the vendored runtime/CLI/voice; provider env keys remain unbound.
- **B-04 remaining fidelity.** Source message IDs are not preserved
  (`append_message` has no id parameter); import is not transactional/resumable;
  source `profiles`/model-provider sections beyond `model` (fallback/auxiliary/
  voice) are not imported; native CLI/voice still read the receipt store for
  migrated history.
- **B-06 Hermes `tui_gateway` WS JSON-RPC dialect.** Still deliberately gated
  (`voicebridge/hermes_compat.py:5619-5622` `WS_SINGLE_PATH`); the vendored
  dialect (`qubit_hermes/tui_gateway/server.py`, `ws.py`) is not served on the
  single listener. No safe-equivalent registered.
- **B-08 remaining control contracts.** SystemPage backup-create/restore body
  shapes, credential-pool add, memory reset `target`, checkpoint prune, hook
  delete, and plugin context-engine save still need contract alignment or an
  explicit typed refusal rendered in the UI (no success toast without a
  confirmed state change).
- **B-01 broader duality.** Only the three named routes were unified; the SPA
  `/agent/api/*` fork proxy vs classic classic-branch duality for other
  overlapping routes remains (now modeled, not all unified).

## Pre-rebrand blocker repairs — 2026-09-18 (second pass)

Additional landed work (same read-only/implementation-first constraints):

- **B-06 — CLOSED as a registered SAFE_EQUIVALENT** (allowed by the owner
  instruction). `docs/QUBIT_WS_DIALECT_DISPOSITION.json` explicitly registers
  the vendored tui_gateway WS JSON-RPC dialect as `WS_SINGLE_PATH` with the
  real counterpart (`GET /api/events` SSE + `POST /api/qubit/chat`);
  `tests/test_ws_dialect_disposition.py` fails if a surface drifts from the
  live `hermes_compat.GATED` gate table. Served on the one listener.
- **B-03 runtime consumption — LANDED.** `voicebridge/hermes_compat.py:
  _persist_overrides_to_home` merges validated non-secret overrides into the
  Qubit runtime home `config.yaml`, so the vendored runtime/CLI/voice actually
  consume applied settings (the display store was not a runtime consumer).
  `config_save` now reports `applied_to_home`.
- **B-08 — additional controls LANDED.** Credential-pool add now has a real
  Qubit path: `voicebridge/credentials.py:add_reference` (env:/file: reference
  resolved server-side, confirmed, e-stop-aware, audited, no token returned),
  route `POST /api/qubit/credentials/pool`
  (`hermes_compat.py:5520`, handler `h_credentials_pool_add`), frontend
  `addCredentialPoolEntry` repointed and labelled as a reference.
  `h_ops` now returns `name`/`action` so the Operations action-log viewer
  mounts; `h_debug_share` returns the `urls`/`redacted`/`auto_delete_seconds`/
  `failures` shape (honest empty share, no client TypeError); `h_mem_reset`
  returns a typed `MEMORY_RESET_OPERATOR_SURFACE` refusal instead of running an
  unrelated sweep. Frontend now confirms env writes, memory-provider
  config/setup, memory reset, and hook delete.
  `scripts/config_doctor.py:45` adds the vendored top-level modules
  `agent`/`tools`/`hermes_state` to `LOCAL_TOP` (fixes a false
  `deps.declared` failure). Focused suites pass.

### Still NOT closed (verdict NOT_READY_FOR_REBRAND)

- **B-03 OpenClaw binding.** `scripts/config_binding_manifest.py` still reads
  only `qubit_hermes`; pinned OpenClaw config/env keys (498 `OPENCLAW_*` names,
  10,177 config paths) are absent; missing Hermes provider env vars (e.g.
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPINFRA_API_KEY`, `GH_TOKEN`) and
  14 top-level config keys remain unbound; unsafe-setting dispositions are
  incomplete.
- **B-04 message identity/transaction.** Source message IDs are still not
  preserved (`append_message` autoincrements); `import_session` is not a single
  transaction and has no resume marker; model-provider sections beyond `model`
  (`fallback_providers`, `auxiliary`, `voice`) are not imported; native
  `qubit sessions` and voice `_b_sessions` still read the receipt store rather
  than the canonical chat store for migrated history.
- **B-08 residual.** SystemPage backup-create/restore remain typed refusals
  (encrypted backup requires an operator passphrase the UI does not yet
  collect); checkpoint prune and a few long-tail controls still need
  contract alignment or a rendered typed refusal.
- **B-01 broader duality.** Overlapping `/agent/api/*` (fork proxy) vs classic
  Qubit branches for routes other than the three unified remain; now modeled in
  the matrix, not all unified.

## Pre-rebrand blocker repairs — 2026-09-18 (third pass)

- **B-03 OpenClaw/Hermes env binding — LANDED (substantial).**
  `scripts/config_binding_manifest.py` now also emits:
  the pinned provider-registry credential/base-URL env vars from
  `qubit_hermes/hermes_cli/auth.py` (closing the 45-name gap),
  the open user-populated top-level config keys from
  `qubit_hermes/hermes_cli/config.py` (847 total keys vs 815), and
  `openclaw_env_vars` — a deterministic 1313-name inventory sourced from the
  committed `docs/QUBIT_OPENCLAW_ENV_NAMES.json`, each with type/default/
  validation/persistence/runtime-consumer/CLI/error/redaction and a typed
  `SAFE_EQUIVALENT` disposition. `tests/test_config_binding_manifest_d3.py`
  guards the new section. Regenerated `docs/QUBIT_CONFIG_BINDING_MANIFEST.json`.
- **B-04 message identity — LANDED.** `hermes_migration.py` selects the source
  message `id`; `hermes_session_store.import_session` forwards it as the
  vendored `platform_message_id`, preserving source identity. (JSONL/NDJSON
  archives already preserve ids via their own store; no truncation.)
- **B-08 backup/restore — LANDED.** The dashboard now collects a passphrase
  (`SystemPage.tsx`), `api.runBackup(passphrase)`/`api.runImport(archive, force,
  passphrase)` send `confirm:true`, and `h_ops` settles the staged
  confirmation with the withheld secret so `backup_create`/`backup_restore`
  actually execute and return the real `{path,bytes}`/plan. `h_ops` also maps
  the dashboard `{archive,force}` shape to the canonical
  `{bundle,passphrase,apply}` op args. `tsc -b` clean.

### Still NOT closed (verdict NOT_READY_FOR_REBRAND)

- **B-03 OpenClaw config paths.** The OpenClaw *env* surface is now bound, but
  the pinned repo's ~10k config paths are not enumerated (only counts are
  committed upstream). Per-path binding/disposition remains.
- **B-04 transaction/model-provider.** `import_session` is still not a single
  transaction with a resume marker; model-provider sections beyond `model`
  (`fallback_providers`, `auxiliary`, `voice`) are not imported; voice
  `_b_sessions` still reads the receipt store (CLI canonical reads exist via
  the forwarded `qubit sessions browse/stats/pinned`).
- **B-01 broader duality.** Overlapping `/agent/api/*` (fork proxy) vs classic
  Qubit branches other than the three unified remain; modeled, not all unified.

## Pre-rebrand blocker repairs — 2026-09-18 (fourth pass)

- **B-01 corrected to the fork authority.** The first pass inverted the
  direction for `/api/status`, `/api/logs`, `/api/profiles`. Per the owner
  decision (fork canonical for Hermes-compatible reads) the classic Qubit
  branches were the shadow, so they now delegate to the fork
  (`voicebridge/webui.py` classic branches call `_hermes_api("GET")`) and the
  three paths are removed from `QUBIT_READ_AUTHORITY`. `route_ownership.py`
  now treats a classic branch as Qubit-authoritative only when the fork does
  NOT declare the route; `scripts/route_ownership_matrix.py` mirrors that.
  Regenerated `docs/QUBIT_ROUTE_OWNERSHIP.json`: 453 rows, 0 violations.
- **B-04 voice canonical history — LANDED.** New voice registry entries
  `chat_sessions` / `chat_session_show` / `chat_session_search`
  (`voicebridge/voice_commands.py` `_b_chat_sessions`, `_E(...)`) read the ONE
  canonical chat store (`chat_transport.store` → `HermesSessionStore`), so
  migrated Hermes history is visible by voice (list/show/search/messages).
- **B-04 model/provider sections — LANDED.**
  `hermes_migration._import_model_profile` now imports `model` +
  `fallback_providers` + `auxiliary` (per-task) provider/model/base_url
  sections as canonical Qubit profiles (env: key refs only), returning the
  imported profile list and auxiliary section names.

### Landed in the fifth pass

- **B-04 transactional/resumable import — LANDED.** `import_session` now:
  preserves source message identity (`platform_message_id`), imports every
  message with no truncation via the batched single-txn
  `append_messages_batch(chunk_rows=500)`, carries an `expected_messages`
  resume marker from `hermes_migration.apply`, and repairs a short/aborted
  session on re-run instead of skipping it. `show` honours the reversible
  delete contract (archived = MISSING until `restore`).
- **B-04 voice canonical history — LANDED** (previous pass).

### Closed in the fifth pass

- **B-03 — CLOSED.** All supported Hermes config keys (847, incl. open
  user-populated maps) and env vars (176, incl. the provider registry) are
  bound with full metadata; the OpenClaw env surface (1313 names) is
  enumerated with typed `SAFE_EQUIVALENT` dispositions; and the out-of-runtime
  OpenClaw config schema (core 2444 / channel 3740 / plugin 4215 = 10399
  paths; upstream commits only counts) is explicitly disposed by category in
  `docs/QUBIT_CONFIG_BINDING_MANIFEST.json` (`openclaw_config_surface`) with a
  typed disposition, counterpart, and rationale — D3 requires a typed
  Qubit-safe disposition for unsupported settings, never a silent ignore.
  `docs/QUBIT_OPENCLAW_CONFIG_SURFACE.json` + `tests/test_config_binding_manifest_d3.py`
  guard it.
- **B-01 — CLOSED.** One authority per route: the fork is canonical for
  Hermes-compatible reads (all three named routes delegate to it), Qubit owns
  its additions/policy routes, and fork-owned compat handlers are recorded as
  non-authoritative dead code by the matrix
  (`docs/QUBIT_ROUTE_OWNERSHIP.json`, 0 violations,
  `tests/test_route_ownership_matrix.py`).

## Deep source rebrand — 2026-09-19 (owner amendment `..._DEEP_REBRAND.md`)

Status: **implementation in progress — no `DEEP_REBRAND_COMPLETE` emitted.**
No commit/push. Pristine upstream trees untouched. Recoverable snapshots:
`/tmp/qubit-pre-rebrand-backup-20260919-184342.tar.gz` (pre-rename) and
`/tmp/qubit-deep-rebrand-precontent-20260919-192329.tar.gz` (pre-content).

### Landed this wave

| Stage | Item | Evidence |
|---|---|---|
| 1 | Package/namespace rename | `qubit_hermes/` -> `qubit_runtime/`; 36 top-level `hermes_*` -> `qubit_*`; 8 `voicebridge/hermes_*` -> `qubit_*`; `HERMES_HOME` -> `QUBIT_HOME`; `scripts/qubit_rebrand.py` + `docs/QUBIT_REBRAND_MANIFEST.json` |
| 1 | Classified content de-branding | `scripts/qubit_debrand.py` -> `docs/QUBIT_DEBRAND_MANIFEST.json`; standalone-word only (identifiers/wire/URLs/legal preserved); 1646 files |
| 1 | Full occurrence classification | `scripts/qubit_debrand.py --classify` -> `docs/QUBIT_DEBRAND_CLASSIFICATION.json` (env_name 2333, identifier 6541, path_literal 2195, wire_dotted_or_hyphen 1378, prose_word 511 [provider ids + MCP examples], legal_attribution 17, other 1727 incl. `synchronously` substring noise) |
| 2 | Frontend rename | `frontend/qubit-{web,shared,app,dist}`; `@hermes/shared` -> `@qubit/shared`; Vite/TS/package/import/vite build config updated; `frontend/qubit-dist` rebuilt (`scripts/build_dashboard.sh`, tsc+vite clean) |
| 3 | Packaging/platform | `/opt/hermes` -> `/opt/qubit`, `/etc/hermes` -> `/etc/qubit`, `hermes-agent` -> `qubit-runtime`; Dockerfile/compose/docker/nix/PowerShell/setup; `qubit-exec-shim.sh`, `nix/qubit-runtime.nix`; `.gitignore`; pyproject comments/artifacts |
| 4 | Compatibility | `voicebridge/qubit_legacy.py` narrow adapter (`HERMES_HOME`->`QUBIT_HOME`, external `~/.hermes` refused, canonical wins), wired into `cli.main` + `qubit_runtime.use_qubit_home`; schemas/DB/API/CLI compat preserved |
| 5 | Entry points | `qubit-agent` primary vendored-runtime launcher; `qubit-hermes` retained as hidden compat alias; pyproject metadata de-branded |
| 6 | Artifacts | route ownership (451 rows, 0 violations), handler reachability (44 handlers, 0 dead/delete), source inventory (5403 items, 0 omissions), gap ledgers (1423 rows, 0 code-side open), completions (9 files), frontend bundle, TUI `ui-tui/dist/entry.js`, config binding manifest |
| 7 | Scans | `compileall` clean; imports + entry points + `/chat` + `/qubit-chat` + gateway RPC/WS + voice/providers/sessions verified; legacy adapter behaviour verified; public-surface guards 15/15 + branding leaks 5/5 |

### Remaining items (block `DEEP_REBRAND_COMPLETE`)

RR2 repair wave status update (2026-09-19) — see
`docs/QUBIT_DEEP_REBRAND_2026-09-19_HANDOFF.md` `## RR2 — repair wave status`.
RR2 closed the frontend repair class, reconciled the generated artifacts and
the differential fixtures, reconciled stale test/comment refs, and completed
`path_renames`; all 13 generated artifacts were regenerated and produced
byte-identical output across two consecutive runs. Item statuses below are
updated in place; the original text is retained for history and no acceptance
criterion is weakened.

1. **Vendor/provenance manifest regeneration (Stage 6). — CLOSED (RR2
   2026-09-19).** RR2 resolved the recorded precondition: the 27
   `git_index_gaps` were force-added to the git index, so the generator
   (`scripts/hermes_vendor_manifest.py`) now exits 0 with `git_index_gaps=0`,
   and `GIT_INDEX_GAP_COUNT` was updated to 0. The comparison `upstream` prefix
   and regression-test refs were corrected; `scripts/hermes_frontend_map.py`
   canonicalizes pre-rebrand Qubit paths; `scripts/source_inventory.py`
   extractors are layout-aware (`hermes_cli` upstream vs `qubit_cli` fork) with
   the upstream `/api/hermes/update*` -> `/api/qubit/update*` coverage mapping
   added; and `docs/QUBIT_REBRAND_MANIFEST.json` `path_renames` was completed
   (test-file renames added). `docs/QUBIT_HERMES_VENDOR_MANIFEST.json` was
   regenerated. Retained history: the generator was made path-rename-aware
   (segment map, `path_renames`, deep-rebrand-owned patches), mapping every
   upstream path to its shipped Qubit path (module/dir/file renames, including
   nested `tests/hermes_cli` and `agent/transports/*`), with a per-file patch
   entry; a `/tmp` run reported `extra=0`, `missing=4`, `unexplained=0`,
   `patched=4499`. `EOL_NORMALIZED_COUNT` reconciliation is tracked under item
   7 / the frozen final verification, not weakened here.
2. **Internal vendored module-graph identifiers (Stage 1, partial). — OPEN
   (not addressed by RR2).**
   Non-wire internal identifiers that the guard suite keys on
   (`get_hermes_home`, `HermesCLI`, `HERMES_*` env vars beyond `HERMES_HOME`)
   are preserved in this wave as documented internal vendor source; renaming
   the module graph is a separate, independently verifiable migration.
3. **Desktop / TUI product identity (Stage 3). — PARTIAL (config DONE,
   Electron build still OPEN, environmental).**
   **DONE (config) this wave:**
   - `apps/desktop/package.json`: `productName`/`executableName` -> `Qubit`,
     `artifactName` -> `Qubit-${version}-...`, `appId` ->
     `com.qubitlabs.qubit`, description and usage strings -> Qubit.
   - `Hermes.app`/`Hermes.exe` -> `Qubit.app`/`Qubit.exe` across
     `apps/desktop`, `apps/bootstrap-installer`, `scripts/install.sh`,
     `scripts/desktop-update`, `qubit_cli/main_desktop.py`,
     `qubit_cli/doctor_platform.py`, `qubit_cli/gui_uninstall.py` and the
     fork's `tests`/`tests-js` (only `website/` upstream docs remain).
   - Linux desktop entry `hermes.desktop`/`hermes.png` ->
     `qubit.desktop`/`qubit.png`; uninstall cleans both new and legacy names.
   - TUI workspace package `@hermes/ink` -> `@qubit/ink` and
     `ui-tui/packages/hermes-ink` -> `packages/qubit-ink` (107 files,
     `src/types/qubit-ink.d.ts`); `ui-tui/dist/entry.js` rebuilt and the
     vendored-launcher guard passes.
   **Remaining (still OPEN — environmental):** a desktop build to verify the
   renamed Electron artifacts end-to-end (not built in this environment). RR2
   did not change this; it is recorded as environmental in the handoff's
   `### Remaining failures / blockers`.
4. **Wire identifiers preserved with adapters (Stage 4). — OPEN (partial; RR2
   added generator coverage mapping only).**
   `X-Hermes-Session-Token`, `__HERMES_*__`, dotted metric/event/object-type
   names, `refs/hermes` checkpoint namespace, `hermes-*` toolset/plugin ids and
   `hermes-flow` are preserved; each must be referenced through an explicit
   Qubit constant/adapter in the final pass. The `/api/hermes/update` route was
   renamed to `/api/qubit/update` consistently on both sides; external callers
   of the old path are not aliased. RR2 added an upstream
   `/api/hermes/update*` -> `/api/qubit/update*` coverage mapping in
   `scripts/source_inventory.py`; the explicit adapter pass itself remains.
   **DONE this wave:** `QUBIT_*` is now an accepted runtime env namespace.
   `voicebridge/qubit_legacy.py` mirrors `QUBIT_<SUFFIX>` <-> `HERMES_<SUFFIX>`
   for every matching name (never overwriting; external `~/.hermes` refused;
   canonical wins), so a caller/config that sets `QUBIT_*` works and legacy
   `HERMES_*` config migrates without becoming the active identity. The
   vendored runtime's internal reads remain `HERMES_*` (documented internal
   vendor source, item 2); the adapter is the only generic legacy-prefix
   consumer.
5. **Route-ownership classification delta to confirm. — OPEN (verification
   item; not addressed by RR2).** fork-owned rows moved
   83 -> 46 (Qubit-addition 96 -> 137) after the rename; matrix is 0-violation
   and deterministic, but the shift is a verification item.
6. **Non-shipped fork material and fork-test staleness. — PARTIAL (RR2
   reconciled stale Qubit test/comment refs; website material still open).**
   RR2 reconciled stale test/comment refs and updated
   `tests/test_dashboard_control_fixes.py` to canonical `canUpdateQubit`.
   `qubit_runtime/website/**` still carries upstream docs branding (excluded
   from the shipped package). The fork's own `tests/` were re-synced where the
   rebrand changed source behaviour: the Linux desktop-entry suite is now green
   (46 passed, 2 skipped) and `_known_wrapper_candidates` accepts the legacy
   `hermes` wrapper names after the primary `qubit` names for migration. Other
   fork tests may still encode pre-rebrand expectations; they are non-shipped
   and not run by the Qubit suite.
7. **Frozen final verification. — OPEN (frozen; unchanged and still pending).**
   Full suite, differential parity, packaging, clean install, and independent
   verifier remain frozen until the lead declares `IMPLEMENTATION_COMPLETE`.
   RR2 did not run or unfreeze any of these; the differential fixture
   reconciliation (`tests/test_differential_parity8u.py`, `docs/PARITY_HERMES.json`,
   135 passed) is fixture-side only and is not an execution/differential run.

No `VERIFIED`/`COMPLETE` claim. No commit or push.
