# Qubit STATUS — 2026-09-22

**Living document. Evidence-backed. Reviewed every session.** No claim below is
`VERIFIED`, `M1`, `M2`, `CODE_READY`, or `COMPLETE` unless its evidence says so.

> **CONTINUE HERE (handoff pointer, 2026-09-23 wave 16).** The full orchestrator
> session log is **§10–§28** below — read §10–§28 before doing anything. In
> short: **Phase 0 PASS**; **all 13 M1 items PASS** — items 2/12 via the
> operator-installed gateway daemon (§25), items 3/4 via direct browser evidence
> (§27); **no `M1` claimed until owner ratification**. Owner Decisions #10–#18
> implemented. W9 pin `d337b736` + worktree **frozen at `42d64b84`** then
> **checkpoint `32fd917a`**; W9 slices ported: `qubit_state*`, `tools/` (+28),
> `gateway/` (+23), `agent/` (+24), `cron/`+`web/` (+30) —
> `missing_from_local` 3362→3257. OpenClaw re-pinned to
> `da03996ab7` (v2026.6.8); route parity **MISSING = 0** (1249 PORTED / 129
> GATED); voice real 594; N17 `.env` fallback removed; `.hermes_history`
> sanitizer implemented (review only); N26/N29/N32/N33 fixed; branding
> manifest-hash tests now pass.
> **Open owner/operator gates:** (1) ratify `M1`; (2) the `.hermes_history`
> apply (approve the review); (3) deep `qubit_runtime/` rename of the remaining
> modules; (4) final M4 verification.
> Handoff artifacts: backups `/tmp/opencode/qubit-phase1-backups/`; the Hermes
> pin is recreatable read-only from the reference git DB (`d337b736`); OpenClaw
> source `/home/trick/Projects/Repos/Qubit Agent/srcodes/openclaw`; commits
> `42d64b84` (freeze) / `32fd917a` (checkpoint).
> **Critical fix to not regress:** `write_file`/`patch` were broken by the N4
> rename (`file_tools_write_guards.py` KeyError) and are now fixed (§21).

## 1. What Qubit is built on

- `qubit_runtime/` **is** the real vendored upstream source, renamed. Not a stub
  foundation. Evidence: `QUBIT_VENDOR_DELTA_2026-09-22.md`; 30,920 files /
  5,819 `.py`; same subsystem tree as the working install.
- Version: `qubit-agent` **0.21.1**. Working local upstream install:
  `/home/trick/.hermes/hermes-agent` at **0.21.3** (git `db39ee3f`). Qubit is
  **~2 patch releases behind**; the gap is upstream decomposition, not stripped
  code (nothing Qubit imports is missing).

## 2. Migration from the reference home — **PARTIAL**

Qubit's runtime home is `~/.local/state/qubit-voicebridge/runtime/`. The port
from the read-only reference home is **incomplete**, and it created legacy-named
homes. See [MIGRATION.md](MIGRATION.md).

| Item | Reference | Qubit | Gap |
|---|---|---|---|
| sessions | 122 | 180 (mixed) | not a clean port |
| skills | 33 dirs / 77 MB | 32 dirs | ~1 missing |
| cron | 7 | 3 | 4 missing |
| memories | 4 | **0** | not ported |
| `config.yaml` | 16,724 B | 549 B | not mapped |
| `auth.json` | 4,468 B | 1,847 B | credentials partial |

Legacy-named dirs/files in Qubit's own state: `hermes/`, `hermes-runtime/`,
`hermes-migration/`, `hermes_config_overrides.json`, `hermes_persona.json`
(and **four** homes with separate `state.db`s).

## 3. Does it run? — **PARTIAL; core works, M1 not fully proven**

- **Core runtime works:** `qubit agent -z "…"` returns a real model turn
  (`QUBIT_OK`, rc=0; qubit-brain4). Service active; dashboard on `0.0.0.0:8890`
  (HTTP 302); `qubit doctor` green except `browser_cdp`.
- **Not yet proven:** the `/chat` terminal TUI, `/qubit-chat` voice turn,
  session persistence, and dashboard controls end-to-end.
- **Voicebridge blocker:** `qubit task` segfaults (native, no Python frame) in
  the audio/ASR path (`sherpa_onnx` / `sounddevice`).
- **Corrections:** the `:9119` listener is the managed reference
  (`hermes-dashboard.service`), not a stray and not a Qubit conflict; the 18
  restarts are explicit (tooling), not crashes; TUI build/install succeed now
  (earlier failures were transient). See `FINDINGS_2026-09-22.md`.
- **Config/safety/audio** (`FINDINGS` §G): **FIXED this session** — removed the
  orphan `safety.denylist: ['x','y']` key (the real enforcer was already
  correct); `asr-test` honors `asr.provider` (`[sherpa-parakeet/cpu]`); renamed
  the MCP key `openclaw-voice-bridge-graft` → `voicebridge-graft`; added a
  cross-process **audio-owner lock** — `qubit task` no longer segfaults (5/5);
  removed the `~/.hermes` model fallback. **OPEN:** config drift, MCP
  single-source, the `~/.hermes/.env` key fallback (migration). **DEFERRED:**
  `hermes-cli` toolset, `/api/hermes/update` route. **ACTION:** rotate the
  reference's plaintext API key.

## 4. Adapter contractual-pin method surface (regenerated, byte-identical ×2)

| Class | Count | Terminal |
|---|---:|---|
| IMPLEMENTED | 252 | yes |
| SAFE_EQUIVALENT | 87 | yes |
| OPERATOR_ONLY_EXTERNAL | 75 | yes (once prerequisite named) |
| OPERATOR_ONLY_ARCHITECTURE | 20 | **NO — OPEN until individually approved** |
| OPEN (unbacked) | 0 | — |
| **Total** | **434** | **414 terminal, 20 OPEN** |

The old "434/434 disposed, 0 OPEN" was wrong: a typed refusal is not a working
capability. The 20 OPEN rows are `agents.*`, `cron.scratch.*`, `mentions.*`,
`skills.proposals.*`, `skills.upload.*`, `taskSuggestions.*`, `tasks.dismiss`,
`users.{setDisplayName,setRole}`, `voicewake.*`.

## 5. Ledgers (regenerated)

- Adapter capability ledger: 104 = 94 IMPLEMENTED, 2 SAFE_EQUIVALENT,
  7 OPERATOR_ONLY_EXTERNAL, 1 OPERATOR_ONLY_ARCHITECTURE (OC-48, OPEN).
- Merged ledger: 1423 = **1266 IMPLEMENTED, 146 SAFE_EQUIVALENT, 10 external,
  1 architecture (OPEN)**; code-side open **1**. The older 1262/150/open-0 was
  stale (pre-existing drift, not caused by any recent change).

## 6. Parity target — the local reference dashboard

- Reference: the live **reference dashboard** at `http://127.0.0.1:9119/?profile=default`
  (HTTP 200), served by the working install `hermes-agent` 0.21.3. See
  [PARITY.md](PARITY.md).
- Requirement: **everything that works there must work in Qubit** — every route,
  page, button, control, skill, MCP, browser, terminal, and IDE action — and
  **every one of them must be commandable by voice** through the Qubit voice
  bridge.
- Current gap: Qubit has `qubit_compat.ROUTES` 415 with **94 `GATED`** (typed
  refusals = not done), `dashboard_ops` catalog 562 + 35, voice registry 510 +
  18 guards, and **3199 OPEN** controls with no E2E run. **Parity is not achieved.**

## 7. Legacy brand

- Requirement: **zero legacy brand on any shipped/user-facing surface** (not
  `NousPortal`, not `Hermes`, not `OpenClaw`, not `Nous Research`). See
  [BRAND.md](BRAND.md).
- **Required exception:** `LICENSE` and `THIRD_PARTY_NOTICES.md` keep the
  upstream copyright/license attribution — removing it is a license violation.
- Current footprint: **1,638 live files** still contain a legacy token
  (`qubit_runtime/` 890 `Hermes` / 363 `NousResearch`; `tests/` 211; `docs/` 112;
  `scripts/` 22; `frontend/` 11; `voicebridge/` 27 `OpenClaw`). The shipped bundle
  still embeds the upstream docs URL and the `@NousResearch` handle.
- Owner Ruling 1 → **Option A** (de-brand product surfaces; keep legal/provenance)
  — **not yet applied**.

## 8. Focused tests (baseline worktree, not a full run)

- 70 passed, **4 pre-existing failures** (committed ledger section, ledger
  ingestion, route-ownership matrix, extension map — all stale artifacts).
- Branding: 1 known-conflict failure (`test_branding_bundle_scan9u.py`); 18 pass.

## 9. Docs

- Pre-reset docs archived to `docs/archive/2026-09-22-pre-reset/` (31 files);
  every reference repointed (49 total); zero dangling `docs/QUBIT_*` paths.
- Frozen records (`docs/archive/2026-09-21/`) and `docs/differential/INTERFACE.md`
  untouched.

## 10. Orchestrator session log — 2026-09-22 (Phase 0 wave)

Evidence only; no milestone claimed. No git writes (no stage/commit/push).

**Done this session**

- **0.1 One instance / reference intact — PASS.** `ss -ltnp`: `0.0.0.0:8890` =
  Qubit python (pid 664056); `127.0.0.1:9119` = reference `hermes` (pid 492159).
  Reference untouched.
- **0.6 Generator patches applied + determinism proven.** The 5 code patches
  from `/tmp/opencode/qubit-staged-gates-2026-09-22/diffs/` were applied
  (`git apply`; originals backed up to
  `/tmp/opencode/qubit-phase0-backups/20260922-214650/`). The 7 doc patches were
  **not** applied (they target archived files; superseded by the fresh set).
  Regenerated to a temp dir (frozen archive untouched) and proven byte-identical
  ×2: manifest `e47d7b0…`, openclaw ledger `9e5e068…`, merged ledger `f4cd0f9…`.
  Merged counts match the projection: **1423 rows; 1266 IMPLEMENTED / 146
  SAFE_EQUIVALENT / 10 OPERATOR_ONLY_EXTERNAL / 1 OPERATOR_ONLY_ARCHITECTURE;
  code-side open 1 (OC-48); code-ready False.**
- **Core runtime still real (re-verified):** `qubit agent -z "…"` → `QUBIT_OK`,
  rc=0.

**Open / blocked (Phase 0 exit NOT met)**

- **0.2 Config authority — UNRESOLVED (owner decision).** Canonical loader reads
  `~/.config/qubit-voicebridge/voice.yaml` (3,446 B); repo `config/voice.yaml`
  is 8,392 B and differs; `config/voice.local.yaml` is dead code (only
  `voice.yaml` is read). Reconciling touches live audio hardware config.
- **0.3 Duplicate unit — CONFIRMED stale, not yet retired.** `qubit-voice-agent
  .service` is disabled+inactive and its `ExecStart` points at a **nonexistent**
  repo (`/home/trick/Projects/Repos/openclaw-voice-bridge/…`). Retirement is a
  systemd-user change; held for owner confirmation.
- **0.4 SQLite WAL-reset — BLOCKED, no working repair path.** Journal still shows
  `linked SQLite 3.45.1 … using journal_mode=DELETE`. `qubit update` is a typed
  refusal (`QUBIT_NATIVE_UPDATE`); its suggested alternatives `qubit product
  upgrade` / `qubit package upgrade` are **voice** commands (config/hotkey/
  dry-run/mute), not package/DB upgrades; `qubit runtime upgrade` only swaps a
  pinned opencode image. FINDINGS B1's prescribed fix (`qubit update`) does not
  exist as a capability. **New finding N1.**
- **0.5 Console scripts — NOT installed.** `.venv/bin` has `qubit`, `qubit-dev`,
  `voicebridge`; `pip show qubit-agent` empty. `pyproject.toml:93` still declares
  the legacy `qubit-hermes` entrypoint.
- **0.7 Ruling 1 — NOT applied.** `test_branding_bundle_scan9u.py` (forbids
  `Hermes`/`Nous`/`OpenClaw`/`@NousResearch` in the shipped bundle) directly
  conflicts with `test_branding_leaks_fixes.py` (requires
  `https://hermes-agent.nousresearch.com/docs/`). Applying Option A needs both a
  test edit and a `frontend/qubit-dist` rebuild.

**New findings this session**

- **N1** — no working package/SQLite upgrade command (above). §0.4 blocker.
- **N2** — brand footprint is **larger** than documented. Excluding `.git/`,
  any `archive/`, `node_modules/`, `.venv/`, `qubit-dist/`: `Hermes` 2,322,
  `NousResearch` 487, `NousPortal` 53, `OpenClaw` 268 files (BRAND.md states
  1,638 total and 890 `qubit_runtime/`). BRAND.md §current-footprint undercounts.
- **N3** — `page_coverage.py` reads its manifest from a hardcoded path inside the
  **frozen** `docs/archive/2026-09-21/`, so regeneration cannot be proven without
  either writing into the frozen archive or a canonical-location decision
  (DECISIONS lists that location as undecided).
- **N4** — the working tree is mid-flight: **14,164 files staged** (a
  `hermes→qubit` path-rename wave) plus **968 unstaged** modified files. No
  commit; flagging so the next writer does not build on a half-applied rename.

## 11. Phase 0 gate — 2026-09-22 (owner-resolved wave)

Owner resolutions received (0.2–0.7 + N4). No git writes. Evidence only.

- **N4 — FROZEN (intended).** Read-only analysis: `qubit_hermes/` (10,514 files)
  → `qubit_runtime/` (10,514) + `frontend/` (167); old dirs absent, new dirs
  populated, `qubit agent -z` works. Treated as the base. (Only `dist/` and a
  few scripts/docs still name old paths.)
- **0.2 Config authority — DONE.** Installed `~/.config/qubit-voicebridge/
  voice.yaml` remains the sole read authority. Repo `config/voice.yaml` renamed
  → `config/voice.example.yaml` (the old example's unique `terminal:` block —
  `/chat` `min_context_tokens: 65536` — preserved; header corrected);
  `config/voice.local.yaml` marked DEAD. Backups:
  `/tmp/opencode/qubit-phase0-backups/20260922-215734-config/`.
- **0.3 One unit — DONE.** `qubit-voice-agent.service` (disabled, `ExecStart`
  → nonexistent `/home/trick/Projects/Repos/openclaw-voice-bridge/`) retired
  (file moved to backup + `daemon-reload`). Only `qubit-voicebridge.service`
  remains (plus the reference `hermes-dashboard.service`, untouched).
- **0.4 SQLite — ACCEPTED (deferred).** Qubit safely uses `journal_mode=DELETE`;
  no working repair command exists (`qubit update` refuses; `qubit product
  upgrade`/`qubit package upgrade` are voice commands). Recorded as accepted.
- **0.5 Console scripts — DONE.** Removed `qubit-hermes` from `pyproject.toml`;
  `uv pip install -e . --no-deps` → `.venv/bin/{qubit,qubit-agent,qubit-dev}`
  (was missing `qubit-agent`); `qubit-agent --help` rc=0; `qubit-hermes` gone.
- **0.6 Canonical location + determinism — DONE.** Created `docs/generated/`;
  repointed the generators (`openclaw_upgrade_manifest`, `rebuild_ledger_{hermes,
  openclaw,qubit}`, `page_coverage`, `openclaw_method_inventory`,
  `openclaw_operator_only_review`, `gap_ledger`) and the consuming tests; the
  frozen `docs/archive/2026-09-21/` is never written (gap_ledger reads
  generated-first, archive-fallback for the frozen hermes/qubit sections).
  Applied the 5 code patches. Regenerated to `docs/generated/`, each
  **byte-identical ×2**: manifest `e47d7b0…`, openclaw ledger `9e5e068…`,
  merged ledger `f4cd0f9…`→`af519ae…` (provenance text now names
  `docs/generated/`; counts unchanged: **1423 rows; 1266/146/10/1; code-side
  open 1 (OC-48); code-ready False**), page coverage `11c5eb0…`, method
  inventory `7930dcc…`, operator-only review (95 rows: 68 external / 27
  architecture).
- **0.7 Ruling 1 → Option A — DONE.** De-branded the rendered upstream docs URL
  (`frontend/qubit-web/src/pages/DocsPage.tsx` → Qubit-owned `/agent/openapi.json`)
  and the `@NousResearch` handle + kanban upstream docs URLs in the in-tree
  plugin bundles (`frontend/qubit-web/public/plugins/{kanban,hermes-achievements}`
  **and** `qubit_runtime/plugins/*/dashboard/dist`). Updated
  `tests/test_branding_leaks_fixes.py` + `test_branding_frontend_classification9u.py`
  + the `TerminalChatPage` comment leaks. Rebuilt `frontend/qubit-dist`
  (`DASHBOARD_BUILT`, rc=0; backup kept). **Branding suite: 67 passed**
  (bundle scan, leaks-fixes, classification, CLI, dashboard, runtime, final).

**Focused-check residuals (not Phase-0 regressions)**

- `tests/test_ledger_hermes_build.py::test_output_is_deterministic_across_hash_seeds`
  and `test_safe_equivalent_evidence.py::test_every_safe_equivalent_row_has_proving_test_on_disk`
  — the **frozen** hermes/qubit sections name proving tests that the N4 rename
  moved/deleted (e.g. `test_memory_reset_is_a_typed_operator_surface_refusal`);
  deterministic but stale vs the base tree. Reconcile the ledger baselines.
- `tests/test_ledger_ingestion_wave3.py::test_implemented_op_ids_are_linkable_to_a_dispatch`
  — `web_fetch`/`web_search`/`websites_show` lost dispatcher linkage after the
  mid-flight `voicebridge/websites.py` change (3 `UNVERIFIED` in a fresh qubit
  ledger). **New finding N5.**
- `tests/test_safe_equivalent_evidence.py::test_audit_doc_lists_every_distinct_refusal_code`
  — the patched generator surfaces a `MALFORMED` code the frozen
  `QUBIT_SAFE_EQUIVALENTS.md` cannot list. **New finding N6.**
- `tests/test_branding_parity_wave3.py::{test_vendor_manifest_hashes_are_valid,
  test_overlay_manifest_hashes_are_valid}` — stale hash manifests on files the
  rename touched (`SidebarStatusStrip.tsx`, `ChatPage.voice9u.test.tsx`);
  pre-existing.

**Phase 0 gate: 0.2–0.7 resolved (0.4 accepted), N4 frozen, generators
byte-identical → PASS (with the residuals above recorded as Phase-1 items).**
No M1/M2/M3/M4/VERIFIED claimed. Phase 1 (W1/W-MIG/W-BRAND) not started.

## 12. W1 — M1 boot baseline — 2026-09-22 (evidence; **M1 NOT passed**)

Command + output per item. UI items driven live against `http://127.0.0.1:8890/chat`.

| # | Boot item | Verdict | Evidence |
|---|---|---|---|
| 1 | Service starts | **PASS** | `systemctl --user is-active qubit-voicebridge` = `active`; `ExecMainStatus=0`, `NRestarts=0`. |
| 2 | Gateway starts and stays alive | **FAIL** | No Qubit gateway process: service cgroup = `python -m voicebridge run` + `xinput test-xi2` only. The only `tui_gateway` (pid 499439) is the **reference** `~/.hermes/hermes-agent` install. |
| 3 | `/chat` opens the real terminal runtime | **PASS** | `/chat` (browser) mounts region `Qubit terminal` + `Terminal input`; `agent.log`: `platform=tui`. |
| 4 | `/chat` loads the real model/provider | **PASS** | Panel: `opencode-go/deepseek-v4-flash · 200,000 ctx`; log `provider=opencode-go model=deepseek-v4.1-flash`. |
| 5 | `/chat` shows real tools/skills/plugins/MCP/sessions | **PASS** | `qubit agent -z` tool list (`terminal`,`read_file`,`web_search`,`skill_manage`,`kanban_*`…); `/chat` sessions panel populated; `qubit plugins list` catalog. |
| 6 | `/chat` accepts a prompt, returns a response | **PASS** | typed via browser → `agent.log`: `conversation turn: session=dashboard-chat … msg='Reply with exactly: BOOT_CHECK_13'`. |
| 7 | Reference-compatible commands/options | **PASS** | `qubit agent --help` exposes `chat/model/gateway/skills/plugins/mcp/sessions/…` + `-z/-m/--provider/--reasoning/-t/--resume/--skills`. |
| 8 | Skills execute | **UNPROVEN** | only `qubit skills list` (install records); no execution receipt. |
| 9 | Plugins load + execute | **UNPROVEN** | `qubit plugins list` catalog only. |
| 10 | MCP servers/tools load + execute | **PASS (config-closed)** | legacy dead server (`openclaw-voice-bridge-graft`, path nonexistent) removed; `cgraph-graft` allowlisted → `qubit mcp status` `invocable:true MCP_INVOCATION_READY`; `qubit mcp invoke cgraph-graft --tool graft_find_code --confirm` → real result `duration_ms≈1405`. |
| 11 | Sessions/history persist | **PASS** | runtime `state.db`: **184 sessions / 22,289 messages**; `/chat` turn written there. |
| 12 | Gateway status/start/restart work | **FAIL** | `qubit gateway status` = "not running"; `start/stop/restart` are typed refusals ("operator supervises gateway processes"). |
| 13 | Dashboard controls operate vs real backend | **PARTIAL** | `/chat` model/session controls render and the chat turn dispatches to the real backend; full control E2E not swept. |

**M1 verdict: NOT PASSED** (items 2, 10, 12 fail; 8, 9 unproven; 13 partial).
The reference-parity model does run through `/chat` on the one Qubit backend —
the gap is the gateway-daemon items and tool-execution proof. **New findings:**
N7 (MCP not connected + legacy key), plus the gateway-daemon policy gap (items
2/12) and the `.hermes_history`/`hermes-*` names still inside Qubit's own runtime
home (W-MIG/W-BRAND).

## 13. Phase 1 wave — 2026-09-22 (parallel agents + W-MIG)

Four read-only agents were deployed; findings below. **No M1/M2 claim.**

- **W2 parity inventory (agent) — DONE.** `docs/generated/parity_inventory.json`
  (schema `qubit-parity-inventory/1`, **1380 rows**): routes 272 (198 PORTED / 60
  GATED / 14 MISSING), CLI 384 (356/11/17), controls 599 (548/51/0), tools 125
  (113/10/2). **1215 PORTED / 132 GATED / 33 MISSING.** PORTED = counterpart
  registered-with-handler; `e2e_verified:false` for all rows (behavioral E2E not
  yet run). Top MISSING: `/api/portal`, `/api/providers/oauth*`,
  `/api/audio/voice-live/*`, `codex-runtime`, `vault`, `usage`, skill `product`,
  optional-MCP `n8n-official`.
- **M1 item 10 — config-closed.** See §12 row 10. Fix: legacy dead MCP server
  removed; `cgraph-graft` allowlisted (6 real graft tools). Backups:
  `/tmp/opencode/qubit-phase1-backups/20260922-231401-mcp/`.
- **M1 items 2/12 — dispositioned (naming mismatch).** The dashboard "Gateway
  Status: Running" is the Qubit service (`qubit_compat.py:360 h_status` →
  `gateway_supervisor.status` → `qubit-voicebridge.service`, `running:true`).
  `qubit gateway status` forwards to the **vendored messaging gateway** (not
  running; operator-owned); `qubit gateway start/stop/restart` is a
  `GATEWAY_NO_SUPERVISION` refusal with a **real working counterpart**
  (`qubit gateway supervision-request --confirm` and dashboard
  `POST /api/gateway/{start,stop,restart}` → `gateway_supervisor.control`). No
  Qubit `tui_gateway` exists by design (WS dialect gated: `WS_SINGLE_PATH` /
  `PTY_NO_SHELL`). Boot items 2/12 are a **measurement/naming** issue, not a
  missing control; a Qubit-owned messaging-gateway daemon remains operator-owned.
- **M1 items 8/9 — working paths with receipts.** `qubit plugins invoke
  builtin.echo --text … --confirm` (plugin_runtime.py:1904 invoke; events.jsonl
  `plugin.invoke` receipts 2026-09-22); skill execution = the agent tool path
  (`skill_view`/`skills_list`, state.db `tool_name` counts: `skill_manage` 118,
  `skill_view` 77).
- **W-MIG — PARTIAL.**
  - Memories ported: `~/.hermes/memories/{MEMORY.md,USER.md}` →
    `runtime/memories/` (was 0/4).
  - Cron jobs already migrated (same 2 ids, `b8d60fe64add` / `3e396bb6e6a5`).
  - **3 dead legacy homes retired** (moved out of the state tree to backup):
    `hermes/`, `hermes-runtime/`, `qubit/` — dead per `qubit_runtime.py:261
    _LEGACY_HOME_NAMES` + `migrate_legacy_homes` (canonical state.db/kanban.db/
    projects.db all exist → sources never opened). Backup:
    `/tmp/opencode/qubit-phase1-backups/20260922-232726-w-mig/` (99 MB;
    includes `qubit/auth.json` — credentials, owner-gated, preserved not read).
  - **LIVE legacy-named stores must NOT be deleted** (rename+migrate only):
    `hermes_config_overrides.json`, `hermes_persona.json` (dashboard Settings +
    active-profile), `hermes-migration/` (rollback bundles), and
    `runtime/.hermes_history`. These are W-BRAND code-rename work.
  - **Owner-gated:** config mapping (agent table: ~40 keys safe to auto-map, ~15
    owner decisions; **provider/model `opencode-zen/laguna-s-2.1-free` and all
    secrets must NOT be imported**); `auth.json`/`.env` secrets; reference key
    rotation.
  - Post-change verification: service `active`, `qubit agent -z` → `QUBIT_OK`,
    `qubit mcp status` → `cgraph-graft` invocable, memories present.
- **No `~/.hermes` rename-fallback test run:** renaming the reference home
  violates the read-only guardrail; prove no-fallback instead with a temp
  `QUBIT_HOME`/`HOME` or strace (TODO).

## 14. Multi-agent wave — 2026-09-22 (7 agents, parallel)

Seven read-only/writer agents were deployed in two batches. No milestone
claimed. All fixes verified live.

- **W2 parity inventory — DONE.** `docs/generated/parity_inventory.json`
  (schema `qubit-parity-inventory/1`): **1380 rows** — routes 272 (198/60/14),
  CLI 384 (356/11/17), controls 599 (548/51/0), tools 125 (113/10/2) →
  **1215 PORTED / 132 GATED / 33 MISSING**. All `e2e_verified:false`.
- **W4 disposition — DONE (report).** 132 GATED + 33 MISSING dispositioned:
  **125 legitimate-refusal-with-working-counterpart**, **40 genuine gaps**
  (33 MISSING + 7 flagged), **7 operator-owned**. Real W4 work: `hermes update`
  upgrade path (N1), register vendored handlers `GET /api/portal` +
  `/dashboard-plugins/{name}/{path}`, `gateway_supervision` voice wiring, and
  the 0.21.1→0.21.3 CLI ports (`vault`, `codex-runtime`, `usage`,
  `sessions set-journal-mode`, `plugins browse/validate`, …).
- **M1 item 10 — CLOSED.** Legacy dead MCP server removed; `cgraph-graft`
  allowlisted (6 real graft tools); `qubit mcp invoke cgraph-graft --tool
  graft_find_code --confirm` → real result `duration_ms≈1405`.
- **M1 items 2/12 — DISPOSITIONED (naming).** Dashboard "Gateway Status:
  Running" = the Qubit service (`gateway_supervisor.status` → pid 664056);
  `qubit gateway start/stop/restart` = `GATEWAY_NO_SUPERVISION` refusal whose
  real counterpart is the dashboard `POST /api/gateway/restart`
  (`gateway_supervisor.control`) — **verified live: it restarted the service
  (pid change, recovered in ~6 s)**. No Qubit `tui_gateway` by design.
- **M1 items 8/9 — working paths with receipts.** `qubit plugins invoke
  builtin.echo … --confirm` (`plugin.invoke` events); skill execution =
  `skill_view`/`skill_manage` agent-tool rows (state.db: 77/118).
- **M1 item 13 — PARTIAL real-backend (verified).** `/chat`, `/sessions`,
  `/plugins`, `/docs`, `/qubit-boards`, `/qubit-gateway` all hit real 200s;
  terminal profile persistence is confirmation-gated (409→200, net-zero);
  WS session resume works. Defects: `/agent/api/actions/gateway-restart/status`
  **404** and the theme switch is client-side only.
- **Ruling 3 — FIXED LIVE.** `/` was redirecting to `/sessions` (violating
  Ruling 3 Option A); changed `voicebridge/webui.py:1867` + `App.tsx`
  `RootRedirect` + `tests/test_webui.py:343`; rebuilt `frontend/qubit-dist`.
  Live: `curl http://127.0.0.1:8890/` → `302 Location: /chat`.
- **W-MIG — config + memories + homes.**
  - 40 additive non-secret reference config keys ported into
    `runtime/config.yaml` (backup `/tmp/opencode/qubit-phase1-backups/
    20260922-232901-configmap/`); provider/model/secrets/hardware excluded;
    `qubit agent -z` → `QUBIT_OK`.
  - Memories ported (4→present); cron jobs already migrated (same 2 ids).
  - 3 dead legacy homes retired (backup `…232726-w-mig/`).
- **W-BRAND Lane B — DONE.** Renamed Qubit-owned live state stores with a
  non-destructive read-old/write-new migration: `hermes_config_overrides.json`
  → `qubit_config_overrides.json`, `hermes_persona.json` → `qubit_persona.json`,
  `hermes_env_refs.json` → `qubit_env_refs.json` (`voicebridge/qubit_compat.py`,
  `voicebridge/env_store.py`). `QUBIT_OK`; focused tests 32 passed / 1
  pre-existing failure (`test_config_secret_skip.py::test_full_tree_save_
  skips_unchanged_served_secret_leaves` — verified pre-existing by diff).
  Backup `…233511-brand-b/`.
- **W-BRAND scope re-baselined (agent).** BRAND.md's 1,634 is stale: exact
  current counts are **1,621 strict / 5,381 case-insensitive** files (the gap is
  lowercase `hermes_*`/`openclaw_*` identifiers). Dominant literals: `hermes-`
  1569 files, `HERMES_` 1380, `hermes-agent` 822; `qubit_runtime/` dominates
  (4,801 ci files → W9 vendor sync). Parallelization lanes documented.

**Residual defects / new findings:** N8 (`/agent/api/actions/gateway-restart/
status` 404); N9 (theme switch client-side only); N10 (`hermes update` has no
working upgrade counterpart — 3 flagged genuine gaps); remaining Qubit-owned
`hermes_*.json` stores (`hidden`, `dashboard_auth`, `dashboard_prefs`,
`custom_endpoints`, `memory_provider`) still legacy-named.

## 15. Owner Decisions #10–#13 implemented — 2026-09-23

Owner resolutions: real Qubit-owned gateway; sanctioned `qubit update
--check|--apply|--rollback`; fix gateway status + restart-status routes. All
implemented and behaviorally tested. **No M1/M2/CODE_READY/VERIFIED claimed.**

- **Qubit-owned gateway — real control through the one authority path.**
  `voicebridge/gateway_supervisor.py` now: returns full status (`pid`,
  `active_state`, `sub_state`, `port_in_use`, `exec_main_status`, `n_restarts`,
  `failed`, `failure`, bounded `health`/`health_latency_ms`); routes
  `control()`/`recover()` through e-stop → confirmation (`broker.confirmation_
  store_for` + `WorkerGuard.authorize`, single-use target-bound) → audit
  (Decision id + typed receipt); adds `failure()`/`recover()` (reset-failed +
  start). The vendored messaging gateway and external OpenClaw peers stay
  operator-owned.
  - `qubit gateway status` → **real** (`{"pid": 1002629, "running": true,
    "health": "OK", "n_restarts": 0, "exec_main_status": 0}`), rc 0.
  - `qubit gateway start` (no `--confirm`) → `GATEWAY_CONFIRM_REQUIRED`, rc 3,
    **nothing executed**.
  - `qubit gateway restart --confirm` → `ok:true`, `state:RESTARTING`, receipt
    `decision_id=1c02ab81904b4109aa…`; **real restart** (service stopped/started,
    new pid 1002629, recovered ~6 s).
  - N8 **fixed**: `GET /agent/api/actions/gateway-restart/status` → **HTTP 200**
    with the honest `state:"unknown"` payload (was 404).
- **`qubit update` — native sanctioned surface.** Removed the `QUBIT_NATIVE_
  UPDATE` refusal (`cli_qubit_forward.py`); added the `update` parser family in
  `cli.py` (`--check|--apply|--rollback`, mutually exclusive) + `_cmd_update`;
  `update_manager` gained a real timestamped `snapshot()` (`manifest.json` +
  `git bundle`), a real `rollback()` (refuse `UPDATE_NO_BACKUP` / backup
  pre-rollback snapshot first), and a public `estop_engaged()`.
  - `qubit update --check` → real report (`product 1.0.0`, install `git-checkout`,
    revision `f984911c…`, runtime image `OK`), rc 0; bare `qubit update` = check.
- **Branding:** `hermes-update` → `qubit-update` (`qubit_compat.py` action set,
  `SystemActions.tsx`, `SystemPage.tsx`); `qubit_compat.h_gateway_supervision`
  confirmation hole fixed (`confirm` no longer defaults to `true`).
- **Evidence:** 154 focused tests pass (`test_gateway_control_authority9u.py`
  11, `test_cli_update_native9u.py` 15, CLI family parity/forward, gateway peers,
  update manager); branding 9 pass; `frontend/qubit-dist` rebuilt.

**Remaining for a future behavioral gate:** `--apply`/`--rollback` are
implemented + unit-tested against temp checkouts but **not exercised against the
live repo** (the working tree is mid-flight/dirty by design); the service-running
policy for `--apply` is advisory (recorded for review). No milestone claimed.

## 16. Multi-agent wave 6 — 2026-09-23 (4 agents)

Owner decisions #10–#13 were implemented (STATUS §15). This wave closed
follow-ups and ports. **No M1/M2/CODE_READY/VERIFIED claimed.**

- **CLI ports (wiring gaps) — DONE.** Ported `usage`, `codex-runtime migrate`,
  and `plugins browse` from the 0.21.4 reference into the vendored 0.21.1
  parser (`qubit_cli/subcommands/{usage,codex_runtime}.py`, `plugins_cmd.py`,
  `main.py`); handlers were already present. New `tests/test_cli_ports_wave6.py`
  **9 passed**; `codex-runtime migrate --dry-run` non-mutating (sentinel
  unchanged); `plugins browse` renders the real index.
- **W-BRAND state stores — DONE.** Renamed the 5 remaining Qubit-owned
  `hermes_*.json` stores to `qubit_*.json` with the same non-destructive
  read-old/write-new migration (`hidden`, `dashboard_auth`, `dashboard_prefs`,
  `custom_endpoints`, `memory_provider`). No live legacy files existed (verified
  no-op). 402 focused tests pass; 4 failures proven **pre-existing**.
- **W7 voice binding — DONE (additive).** `voicebridge/voice_commands.py`:
  kanban `complete`/`unblock`, `plugins capabilities`, `skills install <name>`,
  and `cron`→`schedules_*` aliases now resolve to existing safe backends; 5
  upstream kanban shapes with no Qubit construct became audited `guard_*`
  refusals naming the CLI counterpart. `REGISTRY` count unchanged (510). New
  `tests/test_voice_inventory_coverage.py` **7 passed**; resolvable inventory
  intents **240 → 268** (+28).
- **`update --apply/--rollback` — behaviorally verified (disposable clone).**
  Real git in `/tmp/opencode`: `check` (incl. DEGRADED rc 2), `snapshot`
  (`manifest.json` + verifiable `code.bundle`), `apply` (fast-forward + backup +
  remote re-verify), `rollback` (**restores the exact pre-apply SHA** + writes a
  pre-rollback snapshot), `WORKTREE_DIRTY` (tree untouched), `UPDATE_ESTOP` rc 3,
  `UPDATE_NO_BACKUP` rc 2. Gateway `status` + confirm-gate re-verified read-only
  (no mutation). 1 non-blocking defect found (stale apply-receipt wording) —
  **fixed** in `update_manager.py`.
- **Test reconciliation for Owner Decisions #10/#12 — DONE.** `codex-runtime`
  and `usage` classified in `cli_qubit_forward.py`; the 4 `test_cli_closure_
  wave5` assertions that expected `gateway status` to be fork-forwarded were
  updated (gateway status/start/stop/restart are now native). `test_cli_family_
  parity9u` + `test_cli_closure_wave5` + `test_cli_ports_wave6` → **128 passed**.

**Evidence:** 48 focused tests pass (update manager/CLI/gateway authority/voice
inventory); QUBIT_OK; service active.

## 17. Multi-agent wave 7 — 2026-09-23 (4 agents)

**No M1/M2/CODE_READY/VERIFIED claimed.**

- **CLI port `sessions set-journal-mode` — DONE.** Offline WAL↔DELETE conversion
  ported from ref 0.21.4 (`qubit_cli/sessions_cmd_journal_mode.py`,
  `sessions_cmd.py`, `qubit_state_wal.py` `_path_on_cross_vm_fs`); real handler.
  Converts on temp DBs; refuses while holders exist; refuses WAL when the linked
  SQLite has the WAL-reset bug (this host). `--help` rc 0; new
  `tests/test_cli_journal_mode_wave7.py` **13 passed**.
- **CLI ports `migrate relay` + `profile migrate-identity|purge-identity` —
  DONE (surface).** Ported from ref 0.21.4 (`qubit_cli/relay_plugin_migrate.py`,
  `profile_identity.py`, `profiles.py` helpers, parser blocks). Behavior proven
  on temp dirs (relay migrates `.env`→`relay-plugins.toml` + is idempotent;
  identity ops validate + are idempotent). New `tests/test_cli_ports_wave7.py`
  **13 passed**. **Open:** the durable cross-store rebind needs two vendored
  deps absent in 0.21.1 (`SessionDB.rekey_profile_state`/`purge_profile_state`,
  `tools/checkpoint_manager_profile_rename.py`) — the port fails **loudly**
  (rc 1), recorded as a follow-up ticket.
- **W7 voice coverage — now a regenerated number.** `scripts/
  voice_inventory_coverage.py` resolves every inventory `voice_intent` via the
  real `voice_commands.resolve()`; deterministic artifact `docs/generated/
  voice_coverage.json` (sha256 `8236393e…`, byte-identical ×2). **1380 rows →
  280 real / 0 guard / 1100 unresolved; dead backends 0** (registry 543).
  `tests/test_voice_coverage_report.py` **4 passed**. This replaces the earlier
  heuristic 66.6% with an honest, test-guarded literal-resolution number — the
  inventory's `voice_intent` candidates are weak and need better mapping.
- **W9 vendor sync (0.21.1 → 0.21.4) — PLANNED, BLOCKED.** Reference is really
  **0.21.4 @ 7ce19044** (0.21.3/`07610317` were stale), a **7,650-commit**
  delta (2,927 A / 3,843 M / 145 D / 664 R; 3,588 upstream files absent from the
  fork, all 0.21.4 additions). **Two hard blockers:** (a) the upstream-sync
  policy §2 requires an **owner amendment to move the pin** `2237be…`; (b) the
  mid-flight `qubit_hermes→qubit_runtime` rename is uncommitted (HEAD still has
  `qubit_hermes/`) + 507 unstaged edits — a sync now is unreviewable/
  unrollbackable. Recommended: freeze the worktree + pin amendment, then
  slice-by-slice starting with `qubit_state*`.
- **Test reconciliation:** classified `relay`, `profile migrate-identity`,
  `profile purge-identity` in `cli_qubit_forward.py`;
  `cli_family_parity9u`+`closure_wave5`+`ports_wave6`+`ports_wave7`+
  `journal_mode_wave7` → **154 passed**.

**Evidence:** 37 new tests pass (ports/journal-mode/voice-coverage); QUBIT_OK;
service active; all four new commands parse. **Owner gates raised:** W9 pin
amendment; worktree freeze.

## 18. Multi-agent wave 8 — 2026-09-23 (2 writers + verifier + frontend)

**No M1/M2/CODE_READY/VERIFIED claimed.**

- **Profile-identity durable deps — DONE (blocker closed).** Ported
  `SessionDB.rekey_profile_state`/`purge_profile_state`
  (`qubit_runtime/qubit_state_gateway.py`) + `tools/checkpoint_manager_profile_
  rename.py` from ref 0.21.4. Real rebind/purge proven on a temp `state.db`
  (session profile_name/key/origin/routing/heartbeat rekeyed; idempotent).
  `tests/test_profile_identity_rebind_wave8.py` **6 passed** + wave7 13 green.
- **`plugins validate` — DONE.** Ported a real admission validator on the
  vendored plugin model (no catalog invented) + parser/dispatch. `--help` rc 0;
  valid temp plugin rc 0; malformed rc 1 with exact error; stable `--json`.
  `tests/test_cli_ports_wave8.py` **11 passed** (1 pre-existing failure proven).
- **W-BRAND Lane C (frontend SPA) — DONE (bundle rebuilt).** 16 product-surface
  tokens de-branded (`hermes-dashboard-theme/font`, `hermes-locale`,
  `data-hermes-plugin`, `hermes_dv`, DOM ids… → `qubit-*`) with one-time
  read-fallback for the localStorage keys; wire tokens/package specifiers left
  as-is. `tsc` rc 0; 50 vitest + 9 branding py pass; `frontend/qubit-dist`
  rebuilt; bundle scan green.
- **M1 full behavioral pass (verifier) — M1 NOT PASSED.** 9 PASS (1,3,4,6,7,8,9,
  10,11), 2 **DISPOSITIONED** (2,12), 2 **PARTIAL** (5,13). Key findings:
  - **Item 5/8:** tools/skills/plugins/MCP are reachable via their own pages,
    not surfaced inside `/chat`; skill-execution receipts are **stale** (Aug).
  - **Items 11/13 — two session authorities:** runtime `state.db` (194 sessions)
    vs `QubitSessionStore` (101) — a single-canonical-store violation.
  - A live Qubit `python -m tui_gateway.entry` **does** run (spawned by the
    `/chat` PTY bridge) — corrects the earlier "no tui_gateway by design" claim;
    items 2/12 remain scope/disposition, not a messaging daemon.
  - `qubit doctor` green except `browser_cdp`.
- **Independence/brand fix — DONE.** The verifier found Qubit's vendored
  dashboard module hardcoding the **reference** unit:
  `qubit_runtime/qubit_cli/main_dashboard.py:63 _DASHBOARD_SYSTEMD_UNIT =
  "hermes-dashboard.service"` (also the `dashboard_procs.py:334` fallback).
  Fixed to `qubit-voicebridge.service` — Qubit no longer targets the reference
  service.

**Evidence:** 52 focused tests pass (rebind/validate/journal/branding); bundle
rebuilt; QUBIT_OK; service active. **New finding:** N12 (two session stores);
N13 (vendored dashboard unit pointed at the reference — fixed).

## 19. Multi-agent wave 9 — 2026-09-23 (3 writers + 1 auditor)

**No M1/M2/CODE_READY/VERIFIED claimed.**

- **N12 — RESOLVED (it was a capped read, not a second store).** Investigation
  showed `QubitSessionStore` (`voicebridge/qubit_session_store.py`) is a strict
  adapter over the SAME runtime `state.db`; the legacy JSON `ChatSessionStore`
  is dead. The `/sessions` page hid history because `_chat_session_rows`
  hardcoded `limit=100`. Fix: cap 100 → 1000 (`qubit_session_store.py` +
  `qubit_compat.py _SESSIONS_CHAT_PAGE_MAX`). Live before→after:
  `/api/sessions` total **101 → 168**, messages **547 → 5489**; previously
  hidden `/chat` sessions now reachable. `qubit agent -z` → QUBIT_OK.
- **W4 port `vault` — DONE.** `vault add|list|rm|sources` ported from ref 0.21.4
  (`qubit_cli/vault.py`, `subcommands/vault.py`, `agent/vault_store.py`,
  `agent/vault_backends/*`); Fernet crypto preserved. Temp-vault behavior proven
  (list shows metadata only — no secret value; wrong key → typed error).
  `tests/test_cli_ports_wave9.py` **13 passed**. Vault classification added to
  `cli_qubit_forward.py`.
- **W4 port `sessions repair-profiles` — DONE.** Ported from ref 0.21.4
  (`qubit_state_profile_repair.py`, `sessions_repair_profiles.py`,
  `sessions_cmd_repair_profiles.py`, `gateway_multiplex_mode.py`) + parser/
  dispatch. Temp-store behavior proven: report-only changes nothing; `--apply`
  refuses while a gateway is live; snapshots + repairs + idempotent.
  `tests/test_cli_ports_wave9_repair.py` **9 passed**.
- **W9 owner-gate prep — DONE (draft).** `docs/QUBIT_OWNER_AMENDMENT_2026-09-23_
  W9_VENDOR_SYNC.md` (draft, empty OWNER SIGN-OFF). **Reference HEAD is now
  `400f11bd` (0.21.4)** — a moving target; `07610317` and the previously
  recorded `7ce19044` are behind it. Drift pin→HEAD = **7,659 commits**
  (2,938 A / 3,847 M / 145 D / 664 R). Proposed new pin = installed HEAD;
  fallback = the v0.21.4 release commit `d337b736…`. **Amendment NOT granted.**
- **M1 items 5/8 — fresh evidence.** Fresh `plugin.invoke` (`builtin.echo`,
  2026-09-23 01:37) and a fresh `skill_view` row (2026-09-23 01:37:58;
  the Aug-2026 staleness concern is resolved for the read path). **Item 5
  confirmed PARTIAL:** tools/skills/plugins/MCP are **not** surfaced inside
  `/chat` — only as left-nav links to their own pages. **New findings:** the
  agent skill registry and the Qubit `skills` registry are **different stores**
  (`qubit-boards` installed but not visible to the agent); agent-visible skills
  still carry legacy names (`hermes-voice-mode`) — N14.

**Evidence:** 141 focused tests pass (vault/repair/CLI-parity); branding 18
pass; QUBIT_OK; service active. **Owner action:** sign the W9 pin amendment
(choose HEAD `400f11bd` vs release `d337b736`) and freeze the worktree.

## 20. Multi-agent wave 10 — 2026-09-23 (3 writers + 1 UI verifier)

**No M1/M2/CODE_READY/VERIFIED claimed.**

- **N14 — RESOLVED.** The skill-registry split was a **phantom record**: `qubit-
  boards` is an overlay dashboard plugin recorded as a permissioned capability
  grant with no `SKILL.md`; `merged_store_view` listed every permissioned row
  unconditionally. Fix: gate permissioned rows on the on-disk catalog set (the
  same gate `catalog_entries` documents). Result: agent / `catalog_entries` /
  `qubit skills list` all **197**; `qubit-boards` absent from all three (still
  manageable). De-branded the Qubit-owned skill `hermes-voice-mode` →
  `qubit-voice-mode` (dir + frontmatter/title/H1; usage key migrated);
  `skill_view("qubit-voice-mode")` resolves. 148 focused tests pass.
- **M1 item 5 — CLOSED (surface).** New read-only `ChatToolsSummary` card in the
  `/chat` side panel renders **tools 49 in 17 toolsets · skills 197 · plugins 10
  · MCP 1** from the real endpoints (`/api/tools/toolsets`, `/api/skills`,
  `/api/dashboard/plugins`, `/api/mcp/servers`); best-effort, cannot block the
  PTY. tsc rc 0; 10 vitest pass; `frontend/qubit-dist` rebuilt; browser counts
  match `curl`.
- **`qubit chat` — DONE (CLI parity).** Removed the `QUBIT_NATIVE_CHAT` refusal;
  `qubit chat` is now a classified forward to the vendored interactive chat
  (`--help` rc 0; one-shot `-q … -Q` rc 0 → real turn), with a typed
  `CHAT_REQUIRES_TTY` (rc 2) gate on a bare non-TTY launch. 151 focused tests
  pass.
- **M1 item 13 — PASS (core controls).** Full sweep: every core control hits a
  real backend — terminal PTY, sessions open/resume/refresh, the new summary,
  all listed pages, sidebar gateway status, theme/font persistence. `Set
  default` and `Restart Gateway` are confirmation-gated by design (left
  unconfirmed, net-zero). **N9 CORRECTED:** the theme switch is **not**
  client-side only — `PUT /api/dashboard/theme` → 200 `{persisted:true}`; only
  one theme exists, so no visible change.
- **Item-13 new defects (brand/consistency):** `/sessions` sends
  `exclude_sources=…hermes_flow…` (a backend session-source data key with no
  Python emitter in voicebridge/qubit_runtime → W9/coordinated rename; not
  caught by the strict scanner); `/docs` renders legacy paths
  (`/api/hermes/update.*`, `/api/plugins/hermes-achievements/...`); the terminal
  banner reports `MCP Servers (0) connected` while the dashboard reports `1`
  (`cgraph-graft`) — an inconsistency to reconcile.

**Evidence:** 159 focused tests pass (chat/ports/repair/skill-catalog/branding);
QUBIT_OK; service active.

## 21. Multi-agent wave 11 — 2026-09-23 (3 writers + verifier)

**No M1/M2/CODE_READY/VERIFIED claimed.**

- **CRITICAL BUG FIXED — `write_file`/`patch` were broken by the N4 rename.** The
  M1 item-8 verifier found `qubit_runtime/tools/file_tools_write_guards.py`
  renamed its module globals to `_qubit_*` but still passed the old string keys
  to `_cached_lookup` → `KeyError: '_hermes_config_resolved_loaded'`, breaking
  **every** `write_file` and `patch` call (last success Aug-2026). Fixed the keys
  + removed the `~/.hermes` fallback in the same guard (independence). Proof:
  real agent turn wrote `/tmp/m1-write-probe.txt` = `WRITE_OK`, then `patch` →
  `PATCH_OK`; 106 vendored file-tool tests pass (2 stale `'Hermes config'`
  assertions updated to `'Qubit config'`).
- **Item 8 — PASS (scoped, after the fix).** A skill was loaded and its
  instructions **acted on**: a documented artifact was produced
  (`/tmp/m1-skill-exec-probe/SKILL.md`, validated frontmatter) and a
  skill-guided `terminal` command produced deterministic output; receipts in
  `state.db` (`skill_view` + tool rows). Not proven: every skill; skills needing
  network/GPU.
- **Item 10 — PASS.** A **live** `qubit mcp invoke cgraph-graft --tool
  graft_find_code --confirm` round-trip (`duration_ms≈1292`) with fresh
  `events.jsonl` receipts (`mcp.confirm` → `mcp.client.invoke` → `mcp.invoke`).
- **Item-13 defects — FIXED.** (1) dead `hermes_flow` session-source entry
  removed from `SessionsPage.tsx` (no backend emits it; filter verified
  unchanged); (2) MCP row now reads `1 configured · 0 connected` (honest, matches
  the terminal banner); (3) `/docs` legacy OpenAPI paths annotated as
  `x-qubit-compat-alias` (routes still resolve). Bundle rebuilt.
- **W4 ports — DONE/OPERATOR.** `gateway migrate` real read-only plan/preflight
  + operator-owned apply refusal (`GATEWAY_MIGRATE_OPERATOR_OWNED`, rc 3);
  `auth upgrade` typed operator-owned external refusal (`AUTH_UPGRADE_OPERATOR_
  OWNED`, rc 3) — **not ported** (needs a Nous account; absent modules). 15 +
  347 CLI-parity tests pass.
- **W9 precondition prep — DONE (pin NOT changed).** Pristine 0.21.4 source at
  `/tmp/opencode/pinned/hermes-agent-0.21.4/` (SHA `400f11bd…`, **14,966**
  files). Rename maps extended (+9 each: `hermes_platform→qubit_platform`, the
  8 new `hermes_state_*`/`hermes_constants_scratch`) → **0 `hermes_*` module-path
  leaks** across 116 top-level paths. Slice-0 (`qubit_state*`: 7 new + 22
  modified) confirmed. **Pin/`qubit_runtime` untouched.**

**Evidence:** 141 focused tests + 106 vendored file-tool tests + 52 wave-11 tests
pass; write/patch proven live; QUBIT_OK; service active. **Owner action
unchanged:** sign the W9 pin amendment + freeze the worktree.

## 22. N14 — skill registry split + `hermes-voice-mode` de-brand — 2026-09-23

**No M1/M2/CODE_READY/VERIFIED claimed. Not committed.**

- **Registries are the same dirs.** The agent (`qubit_runtime/tools/skills_tool.py`
  `skills_list`/`skill_view` → `_skills_dir()` = `QUBIT_HOME/skills` + external)
  and the dashboard catalog (`voicebridge/skill_catalog.py::catalog_entries` →
  `agent.skill_utils.get_all_skills_dirs`) scan identical roots. The only split
  was `merged_store_view` (CLI/voice `skills list`) merging in permissioned-store
  records unconditionally.
- **Root cause:** `qubit-boards` is an overlay dashboard plugin
  (`frontend/qubit-app/plugins/qubit-boards/`) recorded as a permissioned
  capability grant with no `SKILL.md`; `merged_store_view` listed it as a skill
  while the agent/dashboard could not resolve it (`skill_view` → "not found").
- **Fix:** `merged_store_view` now gates permissioned rows on the on-disk catalog
  set (the same gate `catalog_entries` documents/tests). Live before→after:
  agent 197 / merged 198 (`['qubit-boards']`) → agent 197 / merged 197 /
  `qubit skills list` 197, all equal; `qubit-boards` absent from all three but
  still manageable. `test_dashboard_agent3_wiring`'s stale assertion reconciled.
- **De-brand:** renamed the Qubit-owned, agent-visible skill `hermes-voice-mode`
  → `qubit-voice-mode` (dir + frontmatter/title/H1; usage key migrated);
  `skill_view("qubit-voice-mode")` resolves. The upstream-bundled `hermes-agent`,
  `hermes-agent-skill-authoring`, `inspecting-hermes-desktop-dom`, and optional
  `hermes-s6-container-supervision` remain (repo content → W8/W9 rename map).
- **Evidence:** 73 + 23 + 46 focused tests pass; `test_qubit_parity8tf` 1
  pre-existing failure (`/api/gateway/start` 409 vs stale 403 expectation,
  unrelated); `qubit agent -z "…QUBIT_OK"` → `QUBIT_OK`.

## 23. Multi-agent wave 12 — 2026-09-23 (8 agents, 2 sub-waves)

**No M1/M2/CODE_READY/VERIFIED claimed. Not committed. Owner gates still open.**

- **Evidence is now GENERATED, not hand-written (W0).** `docs/generated/
  parity_inventory.json` had no generator (an agent wrote it by hand and it
  drifted). New `scripts/parity_inventory.py` re-derives it from the live
  reference (`~/.hermes/hermes-agent/hermes_cli/web_routers/*`) + Qubit
  surfaces + the real `qubit_compat.match()`; **byte-identical ×2**
  (`a5f0343a…`). Fresh: **1377 rows — 1240 PORTED / 131 GATED / 6 MISSING**
  (was 1380 / 1215 / 132 / 33). `scripts/voice_inventory_coverage.py` regenerated
  `docs/generated/voice_coverage.json` (**428 real / 0 guard / 949 unresolved /
  0 dead backends**, registry 543; sha `e4c38ea1…`). Two generator bugs fixed:
  route classification now uses `qc.match()` normalization, and skill
  enumeration requires a `SKILL.md` (kills the phantom `skill product` empty
  dir). Hygiene regression fixed: `scripts/parity_inventory.py` no longer
  hardcodes an operator checkout path (discovers `~/.hermes` / `QUBIT_PARITY_
  REF_HOME`); `test_host_hygiene_pra07` green.
- **M1 boot — independent re-audit (§20/§21 reproduced).** 11 PASS, **2
  DISPOSITIONED** (items 2/12 = gateway naming/policy, pending owner
  ratification), items 3/4 **PARTIAL** only because the shared Playwright
  profile was locked (could not re-drive the PTY). Fresh receipts: `skill_view`
  (state.db msg 22389, 03:20:38), `plugin.invoke builtin.echo` (events.jsonl
  03:20:03), live MCP `cgraph-graft/graft_find_code` (03:20:07, 1206 ms).
  `write_file`/`patch` probe → `AUDIT_PATCH_OK` (the §4 fix holds). 76 focused
  tests → 69 pass / 7 fail, all 7 proven pre-existing stale artifacts (since
  reconciled — see below).
- **Route parity — W9-free P0 closed.** Implemented `GET /api/gateway/migrate/
  plan` (real vendored `build_migration_plan()`), `GET /api/host/identity`, and
  `GET /api/providers/oauth` (**redacted**: 0 occurrences of
  `token_preview/access_token/refresh_token/id_token/api_key/client_secret/
  password` in the 3,211 B body). Typed gates added: `POST /api/gateway/migrate`
  → `GATEWAY_MIGRATE_OPERATOR_OWNED`, `GET /api/providers/oauth/{id}/poll/…` →
  `OAUTH_MANUAL_HANDOFF`. Live 200/403 verified. **Security fix:** the fork's
  proxy was serving `portal.nousresearch.com` + a token fragment for
  `/api/providers/oauth`; it is now an authority row (`safe_to_proxy` = False).
  MISSING routes 12 → 6; `tests/test_route_parity_p0_wave13.py` 10 passed.
- **Consistency fixes.** N20: added real read-only `GET /api/dashboard/theme`
  (200 `{theme:"qubit",persisted:true}`) and `GET /api/gateway/status` (200,
  real supervisor status). N19: `QubitSessionStore.list()` total now excludes
  child sessions, matching its rows (`/api/qubit/chat/sessions total=208 ==
  rows`); no data lost. N18 (misnamed `tui_gateway_crash.log`) deferred to W9
  (created inside `qubit_runtime/`).
- **Independence (W-MIG) — real bug found + fixed.** With `QUBIT_HOME` unset,
  importing the vendored `qubit_state` store **created `$HOME/.hermes`** at
  import time (in production, the read-only reference home). Added fail-closed
  `pin_qubit_home()` in `voicebridge/qubit_runtime.py` (rejects the external
  default; runs before the fork import). Proof: scratch-home `strace` shows
  **zero** opens of `/home/trick/.hermes`; `qubit doctor` green; real
  `qubit agent -z` → `QUBIT_OK`. New `tests/test_migration_independence_wave13.py`
  (5 passed) fails on the pristine code and passes with the fix. Coverage
  (read-only source): skills 162→203, cron 2→2, sessions 122→209, memories 4→2
  (partial), `auth.json` present by name only (never read).
- **Tests reconciled (tests/ only).** 7 files, ~12 stale expectations fixed to
  the owner decisions in force — confirm-gate policy (`GATEWAY_CONFIRM_REQUIRED`
  /409) for `test_gateway_control_integration9u`/`test_qubit_parity8tf`/
  `test_dashboard_matrix9u`/`test_qubit_api_parity_wave1`; Ruling 3 `/`→`/chat`
  for `test_qbpv2_rebrand_{routes,public_surface}`; `test_source_inventory`
  repointed to `docs/generated/`. Combined 12 failed → **315 passed / 3
  skipped** across the 7 files.
- **Brand (non-W9 lanes).** Frontend: 10 files de-branded (comments/labels;
  `HermesPluginSDK`→`QubitPluginSDK`), `tsc` rc 0, `frontend/qubit-dist`
  rebuilt, bundle scan green. Scripts/docs: de-branded runtime labels
  (`hatch_build_qubit.py`, `dashboard_stress.py`, `seed_demo_state.py`,
  `clean_install_check.sh`, `release_qualify.py`); wire contracts
  (`X-Hermes-Session-Token`, `window.__HERMES_*`), OpenClaw RPC names, migration
  provenance, and legal attribution deliberately kept.
- **N3 fixed:** `scripts/page_coverage.py` reads its canonical manifest from
  `docs/generated/` (frozen archive is explicit read-only fallback only);
  regenerated; `test_generator_determinism9u` green.
- **Final artifact reconciliation (after the concurrent edits settled).** Three
  regenerable-artifact tests were stale mid-wave; all three now pass after
  regenerating the canonical artifacts: `QUBIT_PAGE_COVERAGE_2026-09-21.json`
  (`scripts/page_coverage.py`), `QUBIT_GAP_LEDGER.{hermes,qubit}.json`
  (`scripts/rebuild_ledger_{hermes,qubit}.py`). The new legitimate refusal code
  `GATEWAY_MIGRATE_OPERATOR_OWNED` is now listed in the **canonical**
  `docs/generated/QUBIT_SAFE_EQUIVALENTS.md` (frozen archive untouched; the test
  prefers canonical, archive as read-only fallback). **Wave-12 focused sweep: 184
  passed / 3 skipped / 0 failed** across 15 files.
- **Still open / deferred:** the **6 remaining MISSING routes** all require
  0.21.4 modules absent from the vendored 0.21.1 (`qubit_state_timeline.py`,
  `tools/voice_live.py`, `web_server_idle_proof.py`, `backend_retirement.py`) →
  **W9-blocked**. The 2 branding manifest-hash tests (`test_branding_parity_
  wave3`) are stale again because the frontend changed after the last manifest
  run — **regenerate at worktree freeze**. `tts/openai_tts.py::_read_key` has a
  SILENT `~/.hermes/.env` fallback (flagged); `orchestrator.py::_env_key`'s
  loud fallback is pinned by `test_keyenv8tf` (owner decision needed).
- **New findings:** N15 (`docs/archive/2026-09-21/` is entirely untracked in
  git); N16 (legacy `__HERMES_*` session-token wire names on the served
  dashboard); N17 (`.env` fallback policy conflict, below); N18–N20 (N18 open
  → W9; N19/N20 fixed); N21 (vendored state import created `~/.hermes` —
  **FIXED**); N22 (proxy leaked `portal.nousresearch.com` + token fragment —
  **FIXED**).
- **Environmental hazard observed:** multiple opencode sessions edited this
  worktree concurrently during the wave (frontend pages, `qubit_compat.py`).
  Merges were clean, but this compounds the freeze gate.

**Wave 12 evidence:** `parity_inventory.json` `a5f0343a…` (×2); `voice_coverage
.json` `e4c38ea1…` (×2); 33 targeted tests pass; `test_route_parity_p0_wave13`
10; `test_migration_independence_wave13` 5; QUBIT_OK; service active.

## 24. Owner rulings executed + W9 slice-0 + gateway daemon — 2026-09-23 (wave 13)

**No M1/M2/CODE_READY/VERIFIED claimed. Owner gates: daemon install (operator).**

Owner answers this session (recorded in `docs/DECISIONS.md` #15–#17):
- **#15 — W9 pin AUTHORIZED + worktree FROZEN.** Moved the vendored pin to the
  stable v0.21.4 release `d337b736…`; froze the worktree at **`42d64b84`** on
  branch **`freeze/wave12-2026-09-23`** (clean tree, **not pushed**). Amendment
  §8 satisfied.
- **#16 — M1 items 2/12: implement a real Qubit-owned messaging-gateway daemon.**
  The naming/disposition resolution was **rejected**; items 2/12 stay OPEN until
  the daemon runs.
- **#17 — remove the `~/.hermes/.env` credential fallback.**

**W9 slice-0 (`qubit_state*`) — ported from `d337b736`.** 7 new + 22 modified
state modules (+2 dependency ports: `qubit_cli/timefmt.py`,
`agent/context_compressor.py`) + `pyproject.toml` force-include; pin updated to
`d337b736` in `hermes_vendor_manifest.py`/`source_inventory.py`/
`rebuild_ledger_hermes.py`; canonical vendor artifacts regenerated. Proof: all 29
state modules import via the product path; `qubit agent -z` → `QUBIT_OK`;
`qubit doctor` green except `browser_cdp`; 48 focused state tests pass. **W9 is
not complete — 3,338 upstream files remain unported (recorded as
`missing_from_local`).**

**Independence #17 — DONE.** Removed the `~/.hermes/.env` candidate from
`orchestrator._env_key` (+ deleted `_warn_legacy_env_once`/`_LEGACY_ENV_WARNED`)
and `tts/openai_tts._read_key`; `tests/test_keyenv8tf.py` now proves the legacy
file is ignored (4 passed). No credential-candidate `~/.hermes/.env` remains.

**Gateway daemon (Owner #16) — IMPLEMENTED, not installed.** Added
`voicebridge/gateway_daemon.py` (`python -m voicebridge.gateway_daemon`; pins
`QUBIT_HOME` before the vendored import; `--check` preflight),
`systemd/qubit-gateway.service` (ExecStart = Qubit wrapper; explicit
`QUBIT_HOME`; `Restart=always`; `ExecReload=SIGUSR1`; no legacy token),
`scripts/install_gateway_unit.sh`, and `scripts/install.sh --with-systemd` wiring.
`gateway_supervisor.py` gained `daemon_service_unit()` + honest daemon runtime
(`gateway_state.json`) / control-socket merge; `qubit gateway run` is no longer a
refusal, `gateway install` is confirm-gated, `status/start/stop/restart` target
the daemon unit. 19 new tests; **no host unit installed and no daemon started**
(operator action). `docs/DECISIONS.md` #16 remains OPEN until the operator
installs+starts it.

**Route/test reconciliation (real behavior changes from earlier waves).**
`tests/test_qubit_routes_parity8u.py`: session `{prune,bulk-delete,import}` and
`DELETE /api/sessions/empty` are real confirm-gated handlers (were stale 501
expectations); `GET /api/memory/reset` is a read-only status (POST stays
confirm-gated); `PATCH /api/profiles/work` validates payload first; MCP server
auth returns `MCP_NOT_FOUND` for unknown servers. New
`tests/test_sessions_mutations_wave18.py` proves the real paths;
`ROUTE_EVIDENCE` updated (memory GET read-only, 4 session mutations, oauth
catalog real-redacted, oauth poll gated) and the ledger regenerated.

**Canonical artifacts / determinism.** Confirmed `rebuild_ledger_hermes.py` is
hash-seed deterministic (0 diffs across seeds — the earlier mismatch was
concurrent writers changing inputs). Regenerated the canonical ledgers +
page coverage (`--check` OK at `42d64b84`). Created canonical
`docs/generated/QUBIT_SAFE_EQUIVALENTS.md` (lists the new
`GATEWAY_MIGRATE_OPERATOR_OWNED` code; frozen archive untouched).
`voicebridge/qubit_runtime.py::manifest_path()` now prefers canonical.
`scripts/{hermes_vendor_manifest,source_inventory}.py` skip `.pytest-cache`
(cache pollution removed).

**Wave-13 focused sweep: 487 passed / 11 skipped / 0 failed across 25 files.**
`qubit agent -z` → `QUBIT_OK`; service active; frozen records untouched.

**New findings:** N23 (the 7 slice-0 `qubit_state*` files are force-included in
the wheel but untracked in git until the next freeze commit — a clean clone's
wheel is incomplete; `git_index_gaps=7`); N24 (`.pytest-cache` pollution in the
canonical manifest — **FIXED**); N25 (`qubit_runtime.manifest_path()` reported
the frozen 0.21.1 manifest while the tree is 0.21.4 slice-0 — **FIXED**).

**Still open:** the daemon host install (operator); the remaining 3,338 W9 files
(next slices); the 6 MISSING routes (now port-able: `qubit_state_timeline` is
vendored — `timeline`/`messages/around` can close without further sync; the
audio/health routes need `tools/voice_live.py`,
`qubit_cli/web_server_idle_proof.py`, `backend_retirement.py`); the deep
`qubit_runtime/` service-name rebrand; 2 branding manifest-hash tests (regen at
freeze).

## 25. Operator install executed — M1 items 2/12 PASS — 2026-09-23

**Owner authorized the operator install in their place.** Evidence:

- `qubit gateway install --confirm` → `installed atomically:
  /home/trick/.config/systemd/user/qubit-gateway.service` (rc 0; never
  enabled/started by the installer).
- `qubit gateway start --confirm` → MainPID **144044**, `ActiveState=active`,
  `SubState=running`; `qubit gateway status` → `service_unit:
  qubit-gateway.service`, `daemon_live: true`, `daemon_state: running`,
  `daemon_pid: 144044`; control socket answers (`protocol 1`, `gateway_state
  running`); `gateway.pid`/`gateway.lock`/`gateway.sock`/`gateway_state.json`
  present in `QUBIT_HOME`.
- **Item 2 (starts and stays alive) — PASS.** `NRestarts=0` and the same
  MainPID held across >4 min; the journal shows the daemon running its cron
  work (an `execute_code` cron action was **blocked by policy**, as designed).
- **Item 12 (status/start/restart) — PASS.** `qubit gateway stop` (no
  `--confirm`) → `GATEWAY_CONFIRM_REQUIRED`, **no mutation** (pid unchanged);
  `qubit gateway restart --confirm` → `ok:true`, `state:RESTARTING`, **real
  restart** MainPID 144044 → **160833**, `active/running`, `NRestarts=0`;
  `qubit gateway status` re-verified (`daemon_live:true`). `qubit agent -z` →
  `QUBIT_OK`.

**Verdict:** a real Qubit-owned messaging-gateway daemon now exists and is
behaviorally proven (status/start/stop/restart through the supervisor's
e-stop→confirmation→audit path). **M1 items 2/12 are PASS**; no `M1` milestone
is claimed until the owner ratifies, and items 3/4 remain PARTIAL (Playwright
profile lock). `docs/DECISIONS.md` #16 is satisfied.

**New finding N26:** the daemon's own runtime payload/logs still carry upstream
names — control socket `kind:"hermes-gateway"`, the `hermes_home` key, and
journal `hermes-snap`/`HERMES_AGENT` — the W9 service-name/wire de-brand (not a
wire-contract exception; the `qubit gateway` control payload is Qubit-owned).

## 26. Wave 14 — MISSING routes closed, voice round 2, W-MIG, service de-brand — 2026-09-23

**No M1/M2/CODE_READY/VERIFIED claimed (M1 pending owner ratification).**

- **Route parity: MISSING = 0.** Every reference route/CLI/control/tool now has
  a Qubit counterpart: `docs/generated/parity_inventory.json` **1377 rows — 1248
  PORTED / 129 GATED / 0 MISSING** (was 6 missing). Closed by porting the real
  0.21.4 modules with the rename map — `qubit_runtime/tools/voice_live.py`,
  `qubit_cli/web_server_idle_proof.py`, `qubit_cli/backend_retirement.py` — and
  wiring `GET /api/audio/voice-live/status` (real read-only readiness),
  `POST /api/audio/voice-live/session` (`VOICE_LIVE_VENDOR_DIRECT` gate),
  `GET /api/health/idle` (read-only), `POST /api/health/retirement`
  (`RETIREMENT_OPERATOR_OWNED` gate), and `GET /api/sessions/{id}/timeline` +
  `/messages/around` over the vendored `qubit_state_timeline`.
  `tests/test_route_parity_w9close_wave17.py` 10 passed. Both new refusal codes
  are listed in the canonical `docs/generated/QUBIT_SAFE_EQUIVALENTS.md`.
- **Voice (W7 round 2).** Real intents **428 → 593 (43.06%)**; new
  `tests/test_voice_cli_family_bridges_wave13.py`. Fixed a shadowing alias so
  `qubit projects` still binds the wave-12 `cli_projects` entry.
- **Service de-brand (N26) — FIXED.** Control payload/state now report
  `kind:"qubit-gateway"` / `qubit_home` (read-old/write-new fallback);
  `_SERVICE_BASE` → `qubit-gateway`; Windows pipe prefix de-branded; every
  reader updated. Live-verified after a real restart. `HERMES_SUPERVISED_CHILD`
  and the shell-snapshot env names are deliberately kept (wire contract).
- **W-MIG safe subset.** Ported memories (byte-identical), checkpoints (23) and
  `channel_directory.json` via `qubit product legacy-migrate --apply --safe`
  (skips the reference provider/model, MCP registry, and credentials). New
  `tests/test_migration_coverage_wave17.py` 3 passed. `.hermes_history` is **not**
  ported — it contains pasted `sk-` API keys (needs a sanitizing import + owner
  ruling).
- **Artifacts.** Vendor manifest + source inventory regenerated (Hermes pin
  recreated read-only from the reference git DB); the OpenClaw section is
  **carried forward read-only** because the ephemeral `/tmp` pins were cleaned —
  `scripts/source_inventory.py` now does this when the OpenClaw pin is absent.
  Determinism re-proven (`parity_inventory.py --check` OK; ledger byte-identical
  across `PYTHONHASHSEED`).
- **Sweep: 455 passed / 5 skipped / 0 failed across 27 files.** `qubit-gateway`
  + `qubit-voicebridge` services active; `qubit agent -z` → `QUBIT_OK`; frozen
  records untouched (mtimes 2026-09-18/20).

**New findings:** N27 (the `/tmp/opencode/pinned/` inputs are not durable;
OpenClaw carry-forward added); N28 (`profiles.json` still records the reference
provider `opencode-zen/laguna-s-2.1-free` from a pre-#9 migration — flagged, not
removed, live service).

**Still open:** the remaining W9 files (now fewer than 3,338 after these ports);
the 2 branding manifest-hash tests (need the frontend/OpenClaw pins at freeze);
the deep `qubit_runtime/` identifier rename; the `.hermes_history` sanitized
import (owner); the next freeze commit (N23); owner ratification of `M1`.

## 27. Wave 15 — items 3/4 PASS, sanitized history importer, W9 tools+gateway slices, N29 — 2026-09-23

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed (M1 pending owner
ratification; branding hashes, deep rename, and final verification still open).**

- **M1 items 3/4 — PASS with direct browser evidence.** The Playwright
  profile-lock was transient (no stale lock now). `/chat` mounts the real
  terminal runtime and spawns a fresh `tui_gateway.entry` child per navigation
  (pid 474657; parent `voicebridge run` → `ui-tui/dist/entry.js` →
  `python -m tui_gateway.entry`); the real provider/model is confirmed by a live
  upstream call (`opencode-go` / `deepseek-v4.1-flash`, `agent.log`);
  prompt round-trip `PTY_EVIDENCE_2026` returned exactly. Evidence screenshot
  `.playwright-mcp/qubit-chat-item3-4-6-evidence.png`. **All 13 M1 items now
  PASS** (item 4 with the N29 defect fixed below).
- **N29 (context authority mismatch) — FIXED.** `/chat` advertised 1,000,000 ctx
  (static `agent/model_metadata.py` registry) while `GET /agent/api/model/info`
  reported 200,000 (config). `voicebridge/providers/resolver.py::
  terminal_context_guard` now reports the effective/config context; the
  advertised value stays in `real_context_tokens` for the floor refusal. Both
  surfaces now report **200000**; new `tests/test_context_authority_n29.py`.
- **`.hermes_history` sanitized importer — implemented (review only, not
  applied).** `voicebridge/qubit_migration.py` + `qubit product history-import`
  /`history-rollback`: dry-run default, secret detection/redaction (typed
  `«REDACTED:SECRET»`), timestamped backup, review diff (counts + line numbers
  only), rollback, fail-closed `HISTORY_SECRET_ASSERT_FAILED`. Dry-run on the
  real source: 1514 lines, **8 redactions** (5 `openai_sk`, 1 high-entropy,
  1 bearer) at lines [33,57,108,600,918,1081,1457] (+1 destination line 39); no
  secret printed; **no raw apply**. New `tests/test_history_import_sanitized.py`
  8 passed.
- **W9 slices (tools, gateway).** `tools/` **+28** files (2 held back —
  `bot_live_delivery`, `browser_vault_tool`: absent 0.21.4 deps); `gateway/`
  **+23** files (1 held back — `slash_commands_login.py`: absent anon-auth
  cluster). Qubit overlays preserved (N26 control names kept); `missing_from_local`
  3362 → **3311**; `git_index_gaps=61` (untracked until the checkpoint commit).
- **Reconciliation.** `describe-auto` is a real ported handler (superseded the
  stale `DESCRIBE_AUTO_MANUAL` gate); new
  `tests/test_profile_describe_auto_wave17.py`; `ROUTE_EVIDENCE` + ledger
  regenerated.
- **Manifests regenerated after the slices** (vendor manifest, source inventory,
  ledgers, page coverage); `parity_inventory.py --check` OK; `graft build`
  refreshed (160,652 nodes / 371,940 edges).
- **Focused sweep: 242 passed / 5 skipped / 0 failed across 27 files.**

**New findings:** N29 (ctx authority mismatch — **FIXED**); N30 (`describe-auto`
real ported handler superseded a stale gate — reconciled); N31 (the Playwright
profile lock is transient).

**Still open:** owner ratification of `M1`; the 2 branding manifest-hash tests
(need the frontend/OpenClaw pins); the deep `qubit_runtime/` identifier rename
(next slices); the `.hermes_history` apply (owner approves the review); the final
M4 gate + independent verifier.

## 28. Wave 16 — OpenClaw re-pin, W9 agent/cron/web slices, deep-rename slice — 2026-09-23

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed.**

- **OpenClaw re-pinned to the latest commit.** `OPENCLAW_PIN =
  da03996ab7203fe6b9b4d8edb2b54726e4394c9c` — the `main` HEAD of
  `/home/trick/Projects/Repos/Qubit Agent/srcodes/openclaw` (version 2026.6.8).
  The tag field is the accurate `git describe` value
  `v2026.4.19-beta.2-28254-gda03996ab7` (the commit is untagged; the old
  `v2026.6.8` tag pointed at a different SHA — finding N32, fixed). OpenClaw
  source count: **138** extension dirs (134 with `openclaw.plugin.json`); the
  v2026.6.8 manifests carry no top-level `categories`, so the aggregate records
  `(uncategorized)` — `tests/test_source_inventory.py` updated accordingly.
  Both `scripts/hermes_vendor_manifest.py` and `scripts/source_inventory.py`
  regenerated; `HERMES_SOURCE_COMPARISON.json` regenerated with the new pin
  (the frontend-comparison abort was fixed: an absent mapped local file is now
  recorded as missing, not a hard failure).
- **W9 slices.** `agent/` **+24** (2 held back — absent 0.21.4 deps),
  `cron/` + `web/` **+30** (0 held back). `missing_from_local` **3311 → 3257**.
  Qubit overlays preserved (N26 `record_home`; `HERMES_CRON_*`/wire names kept).
- **Deep rename slice (`qubit_runtime/qubit_cli/`, existing files).** 38 files:
  local identifiers, temp/dir prefixes, and user-facing strings de-branded;
  wire/contract names preserved (`HERMES_HOME`, `X-Hermes-Session-Token`,
  `HERMES_SUPERVISED_CHILD`, `hermes-gateway*` foreign-service detection,
  toolset names); 3 contract renames reverted (the receipt-pinned
  `hermes-update` action). Counts in `qubit_cli/*.py`: `Hermes` 42→38,
  `hermes_` 124→106, `HERMES_` 701→673, `hermes` 828→761.
- **Ledger de-brand (N33).** `scripts/rebuild_ledger_qubit.py` no longer emits
  `Hermes` strings into the ledger (**43 → 0** occurrences).
- **Artifacts regenerated** (vendor manifest, source inventory, ledgers, page
  coverage, parity inventory, voice coverage). Parity: **1378 rows — 1249
  PORTED / 129 GATED / 0 MISSING**; voice real **594 (43.11%)**.
  **The 2 branding manifest-hash tests now pass** with the new OpenClaw pin.
- **Focused sweep: 455 passed / 2 skipped / 0 failed across 28 files**; graft
  build refreshed.
- **Independent verification (read-only) results:** M1 items 3/4/6 **PASS**
  (process tree + `agent.log` reproduced), N29 **PASS**, gateway daemon
  **PASS**, frozen records **PASS**, git checkpoint **PASS**.
- **N15 correction:** `docs/archive/2026-09-21/` **is** tracked in git (163
  files) — the earlier "entirely untracked" claim (§22) was wrong.

**New findings:** N32 (OpenClaw tag/commit mismatch — **FIXED**); N33
(ledger generator emitted `Hermes` strings — **FIXED**); N34 (concurrent
opencode sessions actively edit the worktree; evidence artifacts drift until a
freeze — the dominant risk).

**Still open:** owner ratification of `M1`; the deep rename of the remaining
`qubit_runtime/` modules; the `.hermes_history` apply (owner approves the
review); the final M4 gate + independent verifier.

## 29. Wave 17 — W9 qubit_cli/tui_gateway/plugins slices + deep rename — 2026-09-23

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed.**

- **W9 slices.** `qubit_cli/` **+57** (4 held back — `anon_auth`,
  `auth_openrouter`, `auth_codex_browser`, `shared_session_attach`: absent
  0.21.4 symbols in `auth_constants`/`auth_device_flow`/`active_sessions`);
  `tui_gateway/` **+31** (0 held back; contracts registry 219 methods / 69
  events); `plugins/` + `scripts/` + `optional-mcps/` **+18** (1 held back —
  `dump_desktop_slash_registry`: absent `desktop_surface_registry`).
  `missing_from_local` **3257 → 3151**.
- **Deep rename slices (existing files only).** `agent/`: strict occurrences
  **317 → 286** (88 → 72 files); a real rename the vendored tests already
  expected was completed (`provider_projection` → 5 failing tests now pass).
  `gateway/` + `tools/`: `Hermes` 23 → 4, `hermes_` 108 → 81 (`HERMES_` wire
  names unchanged); the `url_safety` SSRF-guard idempotency bug
  (`_hermes_ssrf_guarded` vs `_qubit_ssrf_guarded`) was fixed. Service de-brand
  (N26) preserved.
- **Artifacts regenerated** (vendor manifest, source inventory, ledgers, page
  coverage, parity, voice). Parity: **1378 rows — 1249 PORTED / 129 GATED /
  0 MISSING**; voice real **594**.
- **Vendored test sync:** `openai-native` added to the web-provider plugin
  registry expectation (0.21.4).
- **Focused sweep: 489 passed / 2 skipped / 0 failed across 29 files**; graft
  build refreshed; `parity_inventory.py --check` OK. One transient failure
  (`test_committed_inventory_in_sync`) was a concurrent-edit race — regenerated
  and green.
- **New finding N35:** concurrent opencode sessions edit the worktree faster
  than regenerable artifacts can be frozen; the artifact-consistency tests are
  inherently racy until a single-writer freeze (N34 confirmed).

**Still open:** owner ratification of `M1`; the deep rename of the remaining
modules (`cron`/`web`/`tools` remainder, `qubit_cli`, `tui_gateway`, top-level);
the `.hermes_history` apply; the final M4 gate + independent verifier.

## 30. Wave 18 — W9 optional-skills/website/plugin-catalog + deep rename — 2026-09-23

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed.**

- **W9 slices.** `optional-skills/` **+93** (14 new skills; 3 held back — the
  legacy-named `hermes-s6-container-supervision` and `openclaw-migration` would
  clobber the canonical Qubit skills); `website/`+`evals/`+`.github/` **+96**
  (0 held back); `plugin-catalog/` **+230** (229 YAML, 0 held back; `load_catalog`
  → 228 entries). `missing_from_local` **3151 → 2730**.
- **Deep rename slices (existing files only).** `cron/`+`web/`: `hermes` 128 → 26
  in `web/` (all preserved ids/packages/legacy-fallbacks), 17 locale files +
  localStorage/DOM ids de-branded to the served-frontend convention.
  `qubit_cli/`+`tui_gateway/`: `HermesCLI` 33 → 0 (fixed real broken imports
  `from cli import HermesCLI`), `Hermes` 64 → 20; `shutil.which("hermes")`
  resolution in `kanban_db_dispatch` fixed to `qubit` (independence).
- **Vendored test sync:** `metadata.qubit` accepted alongside `metadata.hermes`
  in `test_authoring_standards.py` (the de-branded tree convention; fixes the
  pre-existing 193-skill mismatch); `test_qubit_package_runtime8u` PIN moved to
  `d337b736` + the canonical manifest path, and `pyproject.toml` ships the
  canonical manifest at the archive destination.
- **Artifacts regenerated** (vendor manifest, source inventory, ledgers, page
  coverage, parity, voice). Parity **1378 rows — 1249 PORTED / 129 GATED /
  0 MISSING**. **Voice real 594 → 1009 (73.22%)** (concurrent bindings).
- **Focused sweep: 467 passed / 2 skipped / 0 failed across 29 files**; graft
  build refreshed; `parity_inventory.py --check` OK.
- **New finding N36:** the voice registry gained a large set of real bindings
  from a concurrent session (594 → 1009); voice coverage is not a stable
  artifact while the worktree is multi-writer.

**Still open:** owner ratification of `M1`; the deep rename of the remaining
`qubit_runtime/` modules; the `.hermes_history` apply; the final M4 gate +
independent verifier.

## 31. Wave 19 — W9 vendor sync COMPLETE + defect fixes — 2026-09-24

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed. Environmental blocker below.**

- **W9 vendor sync: `missing_from_local = 0`** — the vendored `qubit_runtime/` is
  now a full superset of the 0.21.4 pin (`d337b736`). Wave-19 slices:
  `contributors/` **+425**, `apps/` **+616**, plus the top-level deep-rename
  slice; the anon-auth held-back cluster was closed (0 remaining); the
  `ui-tui/`+`apps/` brand slice de-branded product prose (**529 → 11**, the 11
  being provenance/legal/generated-contract).
- **D1 (clean-clone incompleteness) — FIXED.** 1608 upstream-tracked vendored
  files were untracked in the git index (vendored `.gitignore` exclusion);
  force-added from the manifest's `git_index_gaps`, then regenerated:
  **`git_index_gaps = 0`**.
- **D2 (test residue) — FIXED.** `qubit_runtime/MagicMock/` (2.1 MB) removed;
  `MagicMock` added to the generator skip sets + `.gitignore`.
- **D3 (gateway down) — FIXED.** The daemon was inactive; restarted via the
  sanctioned confirm path → `qubit-gateway` active, `daemon_live:true`.
- **D5 (`-z` exit code) — FIXED.** `qubit -z` treated a provider-error response
  as success (exit 0). `qubit_runtime/qubit_cli/oneshot.py` now honours
  `result["failed"]` → exit 1 + stderr, never writes the error as the response.
- **D4 (MCP) — verified working** (`mcp invoke cgraph-graft` → `state:OK`, real
  graft result); the verifier's failures were transient.
- **Focused sweep: 465 passed / 5 skipped / 0 failed across 29 files.**
- **Environmental blocker (not code):** the upstream provider returns
  `HTTP 403: Upstream request failed: Model access is disabled` — this blocks a
  *fresh* model-turn proof (M1 items 4/6), which were proven in wave 15
  (screenshot + logs). Plugin/skill/session/MCP/branding/import paths all run.
- **Brand flagged for owner disposition:** `voicebridge/data/plugin_index_pinned.json`
  renders upstream repo URLs + `"maintainer":"NousResearch"` on the Plugins page
  (third-party source provenance; not in the rebrand allowlist).

**Still open:** owner ratification of `M1`; the deep rename of the remaining
modules; the `.hermes_history` apply; the final M4 gate + independent verifier;
the upstream model-access restoration for a fresh `/chat` proof.

## 24. Live dashboard E2E audit + fix — 2026-09-23 (10-agent wave)

**No M1/M2/CODE_READY/VERIFIED claimed. Not committed.** Real browser + live
HTTP audit of the running dashboard (`http://127.0.0.1:8890`), followed by port-
from-upstream fixes. Evidence: `docs/generated/QUBIT_DASHBOARD_AUDIT_2026-09-23.{json,md}`;
raw reports `/tmp/opencode/qubit-dashboard-audit-2026-09-23/`.

- **Audit:** 10 read-only agents, **32 pages / 277 controls / 101 defects**
  (29 high / 37 med / 35 low). Reference `:9119` and `~/.hermes` untouched.
- **Fixes applied + live-verified (20):** gateway restart confirmation dialog
  (was silent 409); `/qubit-chat` invalid-reasoning 200-empty NameError → typed
  SSE frame; cron pause/resume + webhook create/enable confirmation-gated;
  MCP capabilities/`enabled` preserved on edit + real test + `/api/mcp` list;
  toolset enable no longer false-success (read path true, cancellable); sessions
  filters + e-stop/audit + tool/reasoning rows + restore; files upload JSON +
  brokered reversible delete; kanban native writes (were all 403); channels real
  33-platform catalog + enable/disable + secret refusal; profiles one registry;
  pairing GET/write single store; device enroll confirm+audit; `/agent/api/env`
  500 → 200 + real `is_set`; real plugin catalog (70 entries) + honest
  operator-gated remote install; `/chat` slash-worker `tui_gateway` import;
  default-model reconciliation; `/docs` legacy group collapsed; dead Config
  raw-YAML / Env Reveal controls removed; MoA fake providers removed.
- **Regression evidence:** 31 + 326 + 91 + 95 focused backend tests pass;
  frontend `tsc` clean, vitest 415 passed, `frontend/qubit-dist` rebuilt;
  `graft build` refreshed.
- **Remaining (OPEN, not claimed):** remote plugin index (policy-refused);
  binary file upload; ~179 `SKILL.md` + 6 legacy-named skills still carry legacy
  tokens (W8/W9); channel reference-only rule over-broad for allowlist keys;
  gateway restart action status honest `unknown`; `/api/env` ref kinds narrower
  than channels; profiles registry reconciliation still being verified.
- **Concurrent-session hazard:** the worktree was edited by another opencode
  session during the wave (webui/env_store/tests); merges were clean.

- **Continued (5-agent wave blocked by provider "Insufficient account funds";
  fixes done directly).** Live re-verification corrected several stale audit
  rows: sessions `import`/`prune`/`delete-empty`/`bulk-delete` are real
  confirm-gated handlers (not 501); webhooks `rotate`/`test`/`deliveries`/`retry`
  are real **id-scoped** routes (audit searched the un-scoped path) — live-proven
  (rotate 200, test/retry honest FAILED verdict, deliveries 200, delete 200);
  `GET /api/mcp/catalog` returns real entries; pairing GET returns `request_id`;
  logs backend honors `?search=`; webhook URLs allowlisted + secret must be
  `env:`/`file:` ref. Frontend controls confirmed present (Sessions
  filters/import/restore/archive counts, Logs search/export, Webhooks secret ref,
  MCP test). Remaining tracked in the artifact: remote plugin index
  (policy-refused), binary upload, ~179 skill files brand (W8/W9), channel
  secret-scope breadth, cron timezone/retries, memory update/pagination, boards
  pagination, profiles registry live verification.

- **Retry wave (3 agents; the other 2 had completed).** Skills de-brand finished
  (`skill_utils.py` scanner was masking whole URLs — fixed; 0 rewritable prose
  brand in `qubit_runtime/skills`, `optional-skills`, and QUBIT_HOME skills; 146
  strict-identifier files remain = preserved wire/provenance ids; legacy names
  read-old fallback via `skill_view`; live 197 skills resolve). `/chat` slash
  `/toolsets /whoami /version /context` confirmed **working** (D-5 was a
  downstream symptom of the slash-worker PYTHONPATH fix). Profiles **reconciled
  from the invented brain-principal store onto the real reference
  `qubit_cli/profiles.py` registry** (live create/model/description/SOUL/rename/
  active/delete round-trips; `/api/profiles` → `['default']`). Env
  `env:/vault:/file:` refs round-trip with `is_set`; channel secret-scope
  verified; gateway restart action status honest `completed/exit 0`. Cron
  timezone/retries, memory update/pagination, boards pagination implemented in
  the stores (cross-file route wiring pending). 294 focused tests pass.

- **Wave 5 (5 agents, disjoint files).** Cron timezone/retry fields wired to the
  wire shape; memory recall `offset` + `POST /api/recall/update` + voice update
  action; boards/kanban pagination wired; dead `_persona_*` handlers removed;
  binary upload wired through the brokered `local.files_upload` capability
  (path-confined, confirm+audit+e-stop; live returns typed
  `FILE_PATCH_OUTSIDE_ROOT` until an approved project root exists); logs offset
  pagination added (backend additive + `LogsPage` UI); `profiles.py`
  `create_wrapper_script` now writes `qubit -p` (was `hermes -p`, and
  `_is_our_wrapper` never matched); `plan_prompt` + plan skill point at the
  QUBIT_HOME plans path; `uploadFile` sends real multipart bytes (was lossy
  `file.text()`). MCP resources/prompts/resource-templates routes were added to
  the vendored router but remain **shadowed by the compat `/api/mcp/servers`
  prefix** (canonical vs vendored registry) — recorded OPEN. 72 focused tests
  pass; service active.

## 25. W9 deep-rebrand slice — product-surface brand + stale reference paths (2026-09-23)

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed. Not committed. Files backed
up to `/tmp/opencode/qubit-dashboard-audit-2026-09-23/backups/`.**

Owner-scoped slice over six `qubit_runtime/qubit_cli/` files (port/adapt of the
reference 0.21.4 with the hermes→qubit rename map; no invention):

- **`profiles.py` — done.**
  - `_get_default_qubit_home` docstrings (`~/.hermes`, "dir name `.hermes`") →
    "the Qubit-owned default home"; `validate_profile_name` alias comment, the
    `create/delete/import` ValueErrors, `get_active_profile_name`,
    `export/import_profile` comments reconciled (no hardcoded `~/.hermes` prose).
  - Qubit-owned console-script identity: `_QUBIT_CONSOLE_SCRIPT_NAMES`
    `{hermes,hermes-agent,hermes-acp}` → `{qubit,qubit-agent,qubit-acp}`;
    `_QUBIT_ARGV_MARKERS` dropped the dead `hermes-gateway`; the `exe_name`
    gate `startswith("hermes")` → `startswith("qubit")`. These match the shipped
    `[project.scripts]` (`qubit`, `qubit-agent`, `qubit-acp`); the actual
    Electron spawn is `findOnPath('qubit')` (`apps/desktop/electron/main.ts`).
  - Honcho host prefix (Qubit-owned key): `hermes_<profile>` → `qubit_<profile>`
    with **read-old/write-new fallback** — `_migrate_honcho_profile_host` now
    migrates a legacy `hermes_<old>` (and dot-form `qubit.<old>`) host block onto
    `qubit_<new>`, preserving `aiPeer`. Genuine upstream attribution kept
    (`.hermes` platform seam, the reserved name `hermes`, `hermes-agent`).
  - Pre-existing session edit carried: wrapper writes `qubit -p`.
- **`tips.py` — done.** `/plan` tip `.hermes/plans/` → the Qubit home plans
  directory (`$QUBIT_HOME/plans`), consistent with `plan_prompt.py` (already
  `display_qubit_home()/plans`).
- **`commands.py` — done.** `plan` desc `.qubit/plans/` → "the Qubit home plans
  directory"; `reload-skills` desc `~/.qubit/skills/` → "the Qubit home skills
  directory". (Both were fiction: neither `.qubit` nor `.hermes` is the home.)
- **`cli_commands_mixin.py` — done.** `HermesCLI` → `QubitCLI` (the real class in
  `cli.py:2530`); `_handle_plan_command` docstring path; `prefix="hermes_prompt_"`
  → `qubit_prompt_` (pre-existing session edit); the `nous` legacy usage word
  documented as a load-bearing accepted identifier.
- **`main_dashboard.py` — done.** `hermes-serve.service` docstring example →
  `qubit-serve.service`; the OAuth help block no longer prints the upstream docs
  URL (`hermes-agent.nousresearch.com/docs/...`) or `~/.hermes/.env` — points at
  `qubit dashboard register --help` and the Qubit home `.env`; desktop-ssh token
  comment reconciled. **Deliberately kept:** the frozen reference-read handling
  (`HEMES_*`/`HERMES_WEB_DIST`/`HERMES_DESKTOP` env names, the
  `shutil.which("hermes")`-style reference paths) — load-bearing wire/reference
  identifiers, not product copy.
- **`relaunch.py` — done.** `resolve_qubit_bin` PATH fallback
  `shutil.which("hermes")` → `shutil.which("qubit")`.

**Tests reconciled for the renames (test files only, changes product code
requires):** `test_relaunch.py` (pretest-failing: it patched the nonexistent
`resolve_hermes_bin`; now patches `resolve_qubit_bin` + pins argv),
`test_profiles.py` (honcho `hermes_` → `qubit_`; console-script shim cases →
`qubit*`; stale `_get_default_hermes_home` prose), `test_control_socket.py` +
`test_control_socket_windows_live.py` (patched the renamed helper), and
`test_plan_prompt.py` (+1 contract test for the de-branded `/plan` description).

**Evidence:** `python -m py_compile` clean; imports + `resolve_qubit_bin` /
`_is_qubit_argv` / registry descs verified live; focused pytest
**37 branding tests pass** and **158 vendored CLI/gateway tests pass (6 skipped)**;
real `qubit agent -z "Reply with exactly: QUBIT_OK"` → `QUBIT_OK`; branding scan
on the six changed files shows only load-bearing identifiers / `.hermes` platform
seam. No git writes; service not restarted. **Left open with reason:** `tips.py`
and `main_dashboard.py` still contain `~/.hermes/...` path names and `HERMES_*`
env names — the env vars are real wire/config identifiers used across
`qubit_runtime/`, and the tips' path names describe persisted homes (a coordinated
runtime-path rename is out of this slice's six-file scope).

- **Wave 5b (5 agents + direct fixes).** MCP `resources/prompts/resource-templates`
  now have a compat passthrough over `voicebridge.mcp_servers` (live 200, honest
  typed `MCP_PROTOCOL_ERROR` for a tools-only server; unknown server 404; was "no
  compat route"). `qubit_cli` identity de-brand slice (console-script/argv
  identity, honcho `qubit_` prefix with read-old, plan paths, relaunch/PATH,
  OAuth help) with wire/env names preserved. **Two real product bugs fixed:**
  `_rebrand_chat_message` copied top-level `tool_name`/`tool_calls`/`reasoning`
  from the **raw** `meta` instead of the rebranded one (brand leak on the
  CLI/dashboard wire shape); `skills_create` was staged but `_compat_runner` had
  no branch (id-only confirm → 400 `CONFIRM_UNSUPPORTED`) — payload now carries
  the manifest and the runner re-enters `skill_create`. 9 stale tests reconciled
  (Ruling 3 / sessions / e-stop code / profiles registry). Generated artifacts
  regenerated (completions, openclaw ledger, route ownership); tri-parity test
  now reads the canonical `docs/generated/` ledger. Frontend: SystemPage cancel
  honesty + sidebar no-double-confirm. 78 focused tests pass; service active.

- **Owner decisions #19–#24 implemented (2026-09-23).** (1) Curated **pinned
  remote plugin index** approved and served (`voicebridge/data/plugin_index_pinned.json`,
  5 pinned entries; live `pinned_index count=5 curated=true pinned=true`; install
  pin-verified + operator-gated). (2) `/home/trick/Projects/Repos/qubit-voicebridge`
  registered as an approved **binary-upload project root** (`qubit project`
  active); live multipart upload proven **byte-identical** (sha256 `c6742d03…`,
  409→confirm→200, bwrap confinement, traversal/absolute/replay refused). (3)
  **`qubit-migration` canonical**; `openclaw-migration` retained as a
  read-old/write-new compatibility alias with a loud deprecation note; fixed a
  real independence bug where `qubit claw migrate` fell back to `~/.hermes`.
  (4) **Deep identifier rebrand** authorized in W9 slices — slice landed
  (`profiles.py`, `skill_utils.py`, `main_dashboard.py`) with wire/env names
  preserved. (5) **Pinned byte manifest regenerated** from the real pin
  `d337b736…` (`HERMES_BYTE_MANIFEST.json` sha256 `9d8ca061…`, 14,733 rows,
  determinism ×2); differential selftest **96/96**. (6) **`docs/archive/`
  historical-only** — reachability test + generator repointed to
  `docs/generated/` (artifact sha256 `63fe29b4…`, determinism ×2); dead
  `h_logs_*` handlers removed. 227 focused tests pass; services active.

- **Wave 6 (5 agents).** W9 vendor-sync **agent slice** ported
  `agent/turn_failure_copy.py` + `agent/surface_switch.py` (+ `error_classifier`
  enum members; held-back symbol deps documented); gateway/tools slice confirmed
  complete (3 files held back for absent 0.21.4 deps: `anon_auth`,
  `fsync_directory`, vault redaction). **Voice coverage 43.11% → 80.70%**
  (594 → 1112 real, 5 guard, 261 unresolved) by bridging real CLI literals to
  existing backends. Decision #24 follow-up: **6 archive-reading scripts
  repointed to `docs/generated/`**; vendor manifest regenerated from pin
  `d337b736` (`git_index_gaps` 106 → **0**). New behavioral **route E2E auditor**
  (`scripts/route_e2e_audit.py` + `docs/generated/QUBIT_ROUTE_E2E_2026-09-23.json`):
  **98/101 GET PASS, 5/6 mutations PASS**; its one FAIL exposed a real write/read
  split-brain on `/api/providers/custom-endpoints`, **fixed** via
  `QUBIT_READ_AUTHORITY`. 232 focused tests pass; graft refreshed; services
  active.

- **Wave 7 (5 agents).** **Voice coverage 80.70% → 85.20%** (1174 real,
  197 unresolved, 7 guard). **W9 held-back unblocked**: ported
  `utils.fsync_directory` + the `atomic_write_text/bytes/json_write` `mode`/
  `fsync_dir` params, `agent.redact` vault-value registration, and
  `qubit_cli/anon_auth.py`; then ported the 3 previously-held-back files
  (`gateway/slash_commands_login.py`, `tools/bot_live_delivery.py`,
  `tools/browser_vault_tool.py`) — the held-back ledger is now **empty**; fixed a
  real `cron/bot_chat_delivery` `atomic_json_write(fsync_dir/mode)` bug. **W9 cron/
  web slice** ported 10 files (web had nothing missing); **W9 `qubit_cli` slice**
  made `qubit_cli/**` a superset of the pin (0 missing; 3 modules + dependency
  symbols). **Vendor manifest missing 2730 → 1120** (git_index_gaps 1610 = newly
  ported files untracked until a freeze commit, N23). New **real browser controls
  E2E** (`scripts/controls_e2e_audit.py` +
  `docs/generated/QUBIT_CONTROLS_E2E_2026-09-23.json`): 48 controls → **47 PASS**;
  its one FAIL (memory-provider read 404 vs 8 advertised) is **fixed** — `mem_config`
  now returns the same typed `MEMORY_LOCAL_ONLY` gate as the write path. 226
  focused tests pass; services active.

- **Solo browser E2E on /cron + runner fix (2026-09-24).** Drove the real
  browser: New-Cron-Job dialog (fill name/prompt → native confirm → 200,
  rendered) → Pause (staged 409 → banner Confirm → paused) → Resume (banner
  Confirm → enabled) → Delete (row Delete → in-app dialog → native confirm →
  200, gone). Fully cleaned up (only `jobA` remains; pending 0). This exposed a
  **real bug**: the banner's id-only Confirm returned `400 CONFIRM_UNSUPPORTED`
  for `qubit.cron.pause`. **Fixed** by adding `cron.pause`/`cron.resume`/
  `cron.run_due` branches to `_compat_runner` (`dashboard_ops.py`) that re-enter
  `_cron_mutate` with the staged id. New regression test
  `tests/test_cron_banner_settle9u.py` (3 passed); 54 across the runner/mutation
  suites. Confirmed honest Last/Next dates (epoch fix holds).

- **Wave 8 (6 agents + solo fixes).** ui-tui `typecheck` 14 → 0 (ported pinned
  types). **W9 backend product sync COMPLETE** (`missing_from_local` **0**; the
  12 ui-tui files ported). Frontend W9 slice ported 11 product files
  (`frontend/qubit-web`). **Voice 85.20% → 85.99%** (1185 real, 193 audited
  guards, **0 unresolved**). Deep-rebrand **tools/cron slice** (28 files;
  real `skills_hub` dead-alias bug fixed). **Packaging re-baseline**: fixed a real
  **sdist leak of `/home/trick`**, reconciled vendor/packaging tests, proved the
  build. Desktop electron brand leak fixed. Vendor comparison generator fixed
  (worktree-vs-blob EOL) and the frontend coverage test now applies the
  reference-only-test policy. **Browser controls E2E round 2** (14 pages,
  **96 controls, 84 PASS**) found and **fixed two real product bugs**: (1)
  `_files_root` called the nonexistent `projects.active()` so `/files` reported
  no root — now delegates to `_active_folder` (live root resolves); (2) webhook
  create was staged but the banner id-only Confirm returned
  `CONFIRM_UNSUPPORTED` — the staged payload now carries the creation fields and
  `_compat_runner` has the webhook family (live: 409 → confirm 200 → created).
  150 focused tests pass; services active. One MCP-invoke row is environment-only
  (`graft` not on the service `PATH`).

## 32. Update/install lifecycle port — 2026-09-25 (waves 1–2, 10 agents)

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed. Not committed.**

Ported the proven Hermes update lifecycle onto the Qubit-owned backend. The one
canonical manager is `voicebridge/update_manager.py`; every surface (CLI,
dashboard API, voice, gateway action) now routes through it.

- **Core lifecycle (new modules).** `update_manager.full_apply()` orchestrates:
  confirm → e-stop → fail-closed guard → install-method detection → update lock
  → dirty refusal → snapshot → `git pull --ff-only` → hand-off re-exec → deps →
  config/schema migration → compile/import + critical-module + frontend
  validation → owned-service restart → health/listener/version → receipt.
  Post-pull failures auto-restore the pre-apply revision and record
  `rolled_back`. New: `update_lock.py` (pid+timestamp marker, 20-min ceiling,
  hand-off pid/ancestor adoption, `transfer_to`), `update_receipt.py` (schema 1,
  kind `qubit-update`, redacted), `update_validation.py`, 
  `update_config_migration.py` (idempotent, backed-up), `update_handoff.py`.
- **Distribution policy.** `update_channel.py` + `docs/UPDATE.md`: default repo
  identity `cgraph-dev/qubit-voicebridge`, remote `origin`, branch `main`,
  channel `stable` (stable→SemVer tags, beta→rc/beta, canary→branch head),
  anonymous public fetch, explicit-auth private, legacy-URL guard
  (`hermes`/`nousresearch`/`nous` refused as a configured source URL). Container
  → `docker pull`, package/immutable → the exact operator command.
- **Surfaces.** `cli.py` `--check|--apply|--rollback|--channel|--tag|--confirm`
  (canonical resolver; variadic-safe kwargs); `qubit_compat.py`
  GET `/api/qubit/update/check`, POST `/api/qubit/update`,
  POST `/api/qubit/update/rollback` (typed code→HTTP map); `voice_commands.py`
  `update_check`/`update_apply`/`update_rollback` (confirmation + e-stop).
- **Defects fixed.** Resume child re-pulled/re-snapshotted (now skips pre-pull
  steps); lock hand-off race (parent now transfers the marker to the child pid);
  kwargs signature-filter silently dropped `confirm` when the callee was
  variadic (CLI + voice).
- **Fresh-install E2E (hermetic PASS).** Disposable bare remote + clone under
  isolated `HOME`/`QUBIT_HOME`/XDG: install-from-zero check, safe ff-only
  apply→v2, deps+config migration, sentinel preserved, rollback restores the
  exact pre-apply revision with a pre-rollback snapshot, strace shows **0**
  accesses to `/home/trick/.hermes`. Host service restart/health are
  explicitly skipped under the fail-closed guard (no fabricated success).
  Evidence: `/tmp/opencode/qubit-update/fresh_install_report.md`.
- **Tests.** 159 update tests pass (15 files) + 80 route/CLI/voice regression +
  frontend `tsc -b` clean + 53 frontend api tests. Packaging/release 51 pass.
- **Remaining limitations:** live `--apply`/`--rollback` against the host were
  not run (would restart the live service); the hand-off child and service
  restart are unit-proven but only exercised in production outside the guard;
  the compat alias `/api/hermes/update.*` and the vendored desktop updater still
  use the legacy path (W9 deep-rename debt). No secrets copied; `~/.hermes`
  read-only.

## 33. Public-branding closure (owner clarification) — 2026-09-25

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed. Not committed.**

- **Canonical update source (owner ruling).** `QUBIT_UPDATE_URL =
  https://github.com/cgraph-dev/qubit-voicebridge.git`; `resolve_source` prefers
  the canonical URL, overrides a legacy-branded `origin`/`update.remote`, and
  refuses explicit `hermes`/`nousresearch`/`nous`/`openclaw` source URLs
  (`UPDATE_LEGACY_SOURCE_URL`). Default branch `main`, channel `stable`.
  `qubit update --check --json` now reports the canonical URL as the update
  source.
- **Public Hermes routes removed.** `/api/hermes/update.*` deleted; unknown
  canonical `GET/POST /api/qubit/update/*` subpaths answer the typed
  `UPDATE_OPERATOR_OWNED` gate. Public plugin namespace renamed
  `hermes-achievements` → `qubit-achievements` (six routes + fleet.json + engine
  dir + plugin bundle); legacy path 404s with no alias. Route table: 448
  entries, **0** legacy-branded paths.
- **Desktop updater de-branded.** Vendored desktop update API paths →
  `/api/qubit/update*`; about/diagnostics/onboarding/billing/portal/skills-hub
  URLs → canonical repo or removed (no fabricated Qubit service); `package.json`
  repository → canonical. Desktop typecheck/build **BLOCKED** (no
  `node_modules`/Electron toolchain on this host); TS syntax-checked instead.
- **Bundle guard fix.** A frontend de-brand regex shipped contiguous legacy
  tokens into `frontend/qubit-dist`; rebuilt with a non-foldable array-join
  assembly and regenerated the bundle.
- **Public-surface scan: 0 leaks** across cli/routes/voice/bundles/
  source_strings/update_receipt (`scripts/qubit_public_surface_scan.py`,
  deterministic x2). Allowlisted internal wire/provenance identifiers remain
  (classified, never public).
- **Regenerated:** vendor manifest (`missing_from_local=[]`,
  `git_index_gaps=[]`, 14440 files), source inventory, ledgers, route
  ownership, handler reachability, parity/voice/page coverage. Differential
  selftest 96/96; drift fixture repointed.
- **Tests:** 202 update/branding/route tests pass; 237 route/parity/manifest/
  ledger tests pass (5 pinned-source skips). One pre-existing failure unrelated
  to this work: `test_qbpv2_agent3_inventory` (W9 web-sync overlay, fails at
  HEAD).
- **Remaining blocker:** desktop artifact rebuild needs the Electron toolchain
  (`apps/desktop/node_modules`); frontend rebuilt. Generated parity artifacts
  legitimately reference the upstream route names they compare against
  (internal/provenance, not public).

## 34. v0.21.5 rebase — interface contract + generated artifacts regenerated — 2026-09-25

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed. Not pushed.** Work is on
the isolated SDD branch (`sdd/t15`); the live repo and `~/.hermes` are untouched
(the reference home is read-only source of record).

- **Pin re-baselined.** The vendored pin is now
  `69948c005791d4d0e51670ca015238c2c7663749` (Hermes `v0.21.5`,
  v2026.9.24-2096) per owner amendment 2026-09-25 / `docs/DECISIONS.md` #25. The
  generator pin constants were updated in `scripts/hermes_byte_manifest.py`,
  `scripts/hermes_vendor_manifest.py`, `scripts/source_inventory.py`,
  `scripts/differential_parity.py` (`CONTRACTUAL_PINS["hermes"]`), and
  `scripts/rebuild_ledger_hermes.py`.
- **Machine interface contract regenerated** (Task 15). The frozen human-readable
  `docs/differential/INTERFACE.md` and `docs/archive/**` were **not** edited;
  the machine-readable contract was regenerated in place:
  `docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json` — **16,078
  tracked rows, 100% `IDENTICAL`, 30 `eol_normalized`**, contract pin
  `69948c0057`, sha256 `d275beab…`; `manifest.sha256` re-pinned;
  `--check` passes.
- **`docs/generated/*` regenerated (Task 18), each byte-identical ×2:**
  - `QUBIT_HERMES_VENDOR_MANIFEST.json`: revision `69948c0057`, tag `v0.21.5`,
    16,079 files, UNCHANGED 9,941 / QUBIT_PATCHED 6,089 / QUBIT_ADDED 28 /
    EOL_NORMALIZED 21 / INTENTIONALLY_OMITTED 22 / MISSING 5 (the 5 are
    upstream-brand paths intentionally not renamed: `hermes`, `setup-hermes.sh`,
    `apps/desktop/public/hermes.png`, two `_hermes_home.py` scripts);
    `git_index_gaps=[]`, `UNEXPLAINED=0`. `HERMES_SOURCE_COMPARISON.json`
    regenerated.
  - `QUBIT_SOURCE_INVENTORY.json` + `QUBIT_SOURCE_OMISSIONS.md`: 8,058 Hermes /
    138 OpenClaw items; Hermes byte diff identical 9,963 / changed 6,088 /
    missing 27 / extra 28.
  - Ledgers: `hermes` 591 (482 IMPLEMENTED / 105 SAFE_EQUIVALENT / 4 MISSING),
    `openclaw` 104 (unchanged), `qubit` 757 (713 / 36 / 3 OPERATOR_ONLY /
    5 MISSING); merged `QUBIT_GAP_LEDGER.md` 1,452 rows, **code-side open 10,
    code-ready False**.
  - `parity_inventory.json`: **1,363 rows — 1,238 PORTED / 117 GATED /
    8 MISSING**; the 8 MISSING are 7 routes (`GET /api/chat/workspaces`,
    `/api/hermes/update{/check,/receipt,}`, `POST /api/local-models/download/
    {pause,resume}`, `WS /api/display/ws`) + `optional-mcp n8n-official`.
  - `voice_coverage.json`: 1,363 rows — real 1,161 / guard 193 / unresolved 9 /
    dead backends 0.
  - `QUBIT_PAGE_COVERAGE_2026-09-21.json` (3,433 rows, checked at revision
    `737759ad`); `QUBIT_COMPAT_HANDLER_REACHABILITY.json` (68 handlers: 32
    production-referenced / 32 route-test / 2 registration-only / 2
    test-referenced — already current, unchanged).
  - **Determinism env:** throwaway `HOME`/`QUBIT_HOME` with
    `QUBIT_PARITY_REF_HOME` and the product skill set staged; the reference
    working tree is not deterministic (untracked build/i18n noise), so the
    pinned source is read from a pristine checkout of the exact reference
    commit for the vendor/comparison/source-inventory artifacts.
  - **Freeze record** `docs/generated/audit/FREEZE_<sha>.json` regenerated from
    the clean artifact commit (see below).
- **Living docs.** `docs/QUBIT_1TO1_ROOT_CAUSES.md` updated for the
  `69948c0057` pin and the closed Cause-1/B3-R2 items; `docs/QubitVSupstream.md`
  regenerated via `scripts/forensic_audit/run_audit.py` (ledger run 6:
  CRITICAL 1 / HIGH 42 / MEDIUM 133 / LOW 61 / INFO 8; status NEW 7 / OPEN 227 /
  FIXED 11). The open CRITICAL is the known rebase engine gap
  (`hermes_state_health.py` kept its legacy filename while references were
  rewritten to `qubit_state_health`).
- **`--check` status:** byte manifest, parity inventory, voice coverage, page
  coverage, compat reachability, and all three ledger sections pass `--check`;
  vendor manifest / source inventory / comparison were proven byte-identical on
  two independent renders (no `--check` mode).
- **Remaining drift / blockers:** the open CRITICAL + 42 HIGH audit findings
  (rebrand-asymmetry and overlay regressions carried into the rebase); the 8
  MISSING parity rows; the pinned-source checkout lives under `/tmp` (the
  reference tree itself is not byte-deterministic due to untracked files); the
  product `~/.config`/`~/.hermes` independence and the full gate + independent
  verifier remain open.

## 35. 1:1 repair wave — stale service, TUI build, themes, onboarding, plugins — 2026-09-30

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed.** All items below proven
by command + observed output (live service `qubit-voicebridge`, fresh
processes where noted). Full defect ledger:
`docs/reports/QUBIT_VS_HERMES_DIAG_2026-09-30.md` (read-only pass); this
section records the repairs.

- **D1 (stale service) — FIXED by restart.** The live service predated its
  tree: `/api/skills` → `[]` (CLI 189), `/api/chat/workspaces` → 404, while a
  scratch instance on the same tree+data served 189/200. After
  `systemctl --user restart`: skills **192**, workspaces **200**,
  `model/options` **200 with providers** (D2 was the same stale process, not
  code — the `LLAMACPP_ALIASES` symbol exists at
  `qubit_runtime/qubit_cli/providers.py:489`).
- **TUI build (half-rename) — FIXED.** `/chat` PTY died with 503: freshness
  `false` forced a rebuild on every spawn and the build failed — 7 files
  imported `@qubit/*` while the workspace graph was `@hermes/*`. Per owner
  zero-brand direction the rename went FORWARD, not back: `@hermes/ink` →
  `@qubit/ink`, `@hermes/shared` → `@qubit/shared`, `@hermes/plugin-sdk` →
  `@qubit/plugin-sdk` (~510 files, specifiers only),
  `packages/qubit-ink` + `apps/shared` + `bootstrap-installer` + `root-tests`
  package names, `tui.mjs` alias key, `main_tui_launch.py` dev path
  (`packages/hermes-ink` → `packages/qubit-ink`), root `package.json` name +
  repo URLs → canonical. Allowlisted (cannot rename): Meta's `hermes-estree`/
  `hermes-parser` registry packages, `@nous-research/ui` registry dep.
  `qubit_runtime/node_modules/@hermes/` is gone. Rebuilt
  `ui-tui/dist/entry.js`; freshness `true`. PTY probe: **101 + live Qubit
  banner**; longer capture: status **`ready`**, no exit-1. Browser `/chat`:
  terminal **open**, real multi-turn transcript, **zero console errors**.
- **pm ABI crash — FIXED.** The TUI child died with
  `No module named 'pydantic_core._pydantic_core'`: `pm/environments.py`
  `activate_dependencies` prepended the committed **3.14** PM env's
  site-packages onto the **3.12** dev interpreter (ref runs matched 3.14/3.14).
  Added a fail-closed guard: on interpreter mismatch the foreign entries are
  dropped from `sys.path`/`PYTHONPATH` with a stderr warning instead of
  poisoning the process. `import tui_gateway.entry` + registry imports proven;
  fresh service boots with the warning, no loader errors.
- **Themes 1:1 — FIXED.** Backend served only the 1 overlay theme while the
  vendored 8 built-ins (labels already rebranded) and the SPA presets sat
  unused. `themes_response()` now serves 8 + `qubit` (**9 listed**);
  `PUT /api/dashboard/theme` persists any listed theme (midnight round-trip
  proven, bogus → 400, restored to qubit).
- **Channel onboarding (QR) — PORTED.** The blanket `ONBOARDING_GUIDED` gates
  are gone; 8 real routes delegate to the vendored implementation:
  WhatsApp start → real pairing session, poll → real **`qr_payload`**
  (wa.me blob, status waiting), cancel → OK; Telegram start → real
  pairing_id + deep_link + qr_payload + expiry, cancel → OK. **Owner flag:**
  the hosted Telegram bot URL carries the upstream name (`...HermesBot`) —
  it is third-party infrastructure that cannot be rebranded; needs a ruling
  (own bot vs exception vs disable).
- **Plugins 1:1 listing — FIXED.** `qubit plugins list` showed 1 row vs
  ref 61: the registry never enumerated the vendored bundled tree. Now
  appends `plugins_cmd._discover_all_plugins()` rows with vendored
  enable/disable state → **60 rows** (59 bundled + builtin). Plus the missing
  `normalize_scope` ported 1:1 into `qubit_constants.py` (was killing the
  `browser-firecrawl` load with ImportError); registries import clean.
- **MCP verdict (no change).** Ref lists 2 servers, Qubit 1 — both point at
  the same `graft` backend; the dropped `openclaw-voice-bridge-graft` is a
  legacy-branded duplicate alias. Qubit advertises 6 tools + live invocation
  receipts (find_code + find_all with real payloads); ref advertises none.
  Functional parity met.
- **Keys/config/voice-edit/logs — PROVEN live.** Env ref PUT → `is_set:true`
  → DELETE → gone (literal secret values refused by design); config
  16→17→16 round-trip with atomic backup; voice-edit settings
  false→true→false persisted; `/api/logs` lines match `agent.log`
  byte-for-byte.
- **`/qubit-chat` on the shared backend — PROVEN + extended.** Local-brain
  SSE turn → `LOCALBRAIN_OK`, receipt `provider:local,
  identity:local/qubit-brain4`. Store lists terminal sessions (60 tui/cli
  rows of 362) and serves their transcripts (real tui turn read back). New:
  **summarize** button (local brain reports on the session — live 24 s
  report verified in browser) and **open in /chat** deep link
  (`/chat?resume=<id>` — terminal opened resumed on voice-chat, 54 msgs,
  status open). Frontend rebuilt (`tsc` clean after removing a pre-existing
  duplicate `sessionListNonce` declaration; `qubit-dist` rebuilt).
- **Artifacts/tests.** `parity_inventory` regenerated (**1389 rows — 1274
  PORTED / 115 GATED / 0 MISSING**); reachability regenerated (75 handlers);
  voice coverage regenerated (**1142 real / 240 guard / 7 unresolved**);
  new `tests/test_parity_wave22_channels_themes9u.py` **4 passed**;
  `plugin_runtime9u` 13 + route-parity suites green.
- **Still open:** Telegram-bot brand ruling; `HERMES_TUI_*` env contract
  rename (deferred slice — Python launcher + TS must move together);
  desktop/web builds unverified (no Electron toolchain); full M4 matrix +
  independent verifier; `M1` ratification.

## 36. Zero-brand slice — wire identity, JS packages, gateway modules — 2026-09-30

**No milestone claimed.** Goal: no Hermes/OpenClaw trace on product surfaces.

- **Session wire identity renamed.** `X-Hermes-Session-Token` →
  `X-Qubit-Session-Token`, `__HERMES_{SESSION_TOKEN,BASE_PATH,AUTH_REQUIRED}__`
  → `__QUBIT_*` across `voicebridge/`, `frontend/qubit-web`, desktop
  electron, vendored `web/` (93 files). Server emits Qubit-only globals;
  accepts the legacy header as a read-only fallback (old cached bundles keep
  working). Proven: new 200, legacy 200, injected globals all Qubit.
- **Plugin SDK globals renamed** (`__QUBIT_PLUGINS__` / `__QUBIT_PLUGIN_SDK__`
  primary; legacy aliases assigned for third-party plugins), in-repo plugins
  moved to the new names; dead `__HERMES_DASHBOARD_*`/`__HERMES_INITIAL_*`
  reads renamed (never injected — behavior-neutral).
- **Gateway modules renamed** (`openclaw_handlers` → `qubit_handlers`,
  `openclaw_dispositions` → `qubit_dispositions`,
  `scripts/openclaw_upgrade_manifest.py` → `scripts/qubit_upgrade_manifest.py`
  + 28 importing files). Served `openclaw.*` RPC method strings unchanged
  (third-party interop — owner call to rename the wire).
- **Served bundle: zero `Hermes` (case-sensitive).** Case-insensitive
  residuals are compat shims only: legacy localStorage read-fallbacks,
  plugin-global aliases, `HERMES_QWEN_` credential env (deferred env slice).
- **Tests reconciled.** Branding-manifest regenerated
  (`HERMES_SOURCE_COMPARISON.json` rows=5493); wave3 suite green except one
  pre-existing HTTP-boundary failure (proven failing on pristine HEAD too);
  openclaw-family suites 82 passed; `test_qbpv2_reach_deadcode` failure is
  pre-existing (`test_packaging_force_include9u`, untouched).
- **Honest remainder (not product surface or needs ruling):** vendored fork
  internals (~25k identifier lines — W9 deep slices); `HERMES_TUI_*` /
  `HERMES_HOME` / `HERMES_QWEN_*` runtime env contracts (Python+TUI+desktop
  must move together); served `openclaw.*` RPC names (interop); Telegram
  bot URL (third-party infra); `LICENSE`/`THIRD_PARTY_NOTICES.md` + frozen
  archives (legal/historical, must stay); Meta `hermes-estree`/`hermes-parser`
  + `@nous-research/ui` registry deps (cannot rename); dev-only test names;
  git history.

## 37. Voice-edit fantasy-UI fix wave — 2026-09-30 (3 agents + orchestrator follow-up)

**No `M1`/`VERIFIED`/`CODE_READY`/`COMPLETE` claimed. Not committed.** Fixed
every dead/persist-only control from the /voice-edit verification report (§36
audit). No `~/.hermes` touch, `:9119` untouched (hermes pid 19994).

- **Agent 1 (capture backend):** `noise_suppression` now consumed on the wake
  path (`wake.py::_maybe_ns_filter` + call in `_do_transcribe`); `mic_target_peak`
  proven wired (wake mic factory + both normalize sites + `SttClient`, clamped
  0.05–0.95); `auto_send` proven wired (`maybe_auto_send` post-insert only);
  `noise.py::resolve_capture_device` docstring corrected (auto keeps raw source;
  the flag layers a software stage). New/extended
  `tests/test_voice_edit_capture_fix9u.py` **22 passed** (incl. 4 orchestrator
  Right-Ctrl regression tests below).
- **Agent 2 (routing/ask/cache):** `ask()` non-escalated turns pass explicit
  `profile_id=<local_profile>` (default `qubit-brain4`) instead of the resolver
  API fall-through (stock-config `MALFORMED_RESPONSE` fixed); escalated-without-
  target degrades to local (`no_api_profile`); `_sync_live_cfg` invalidates
  `local_llm._STYLE_CACHE` + `runner_contract` caches (temp/think/model apply
  without restart); `PUT per_app:{}` deletes the node (file + live cfg);
  `default_action` gained a real consumer (`_default_action_fallback` on empty-
  transcript clarify); `answer_questions` **removed** from `SETTINGS_KEYS`/view
  (honest-UI invariant; stored keys tolerated). New
  `tests/test_voice_edit_routing_cache_fix9u.py` **29 passed**. Reconciled 2
  `test_voice_edit_integration9u` assertions pinning the old `profile_id is None`
  fall-through (now `== "qubit-brain4"`).
- **Agent 3 (frontend):** audit "0 hits" was stale — wake sub-keys, per_app, and
  most silent knobs were already rendered; added the genuinely missing
  `service_url`/`stt_ws_url` inputs + restart hints (wake, hotkey_mode) and
  rewrote the Advanced intro. `tsc` clean, 7 vitest pass, `qubit-dist` rebuilt;
  stale "browser-grade" Auto label gone from the served bundle (0 hits).
- **Orchestrator follow-up (this session):** closed Agent 1's flagged gap in
  `voicebridge/desktop_helper.py` — Right-Ctrl `_transcribe` wraps the mic in
  `_NsFilterMic` when NS is on (covers streaming + accurate paths), and both
  `_default_mic_factory` + `_stt_factory` pass configured `target_peak`.
- **Live proof (restarted `qubit-voicebridge` + `qubit-voice-typing`):**
  `PUT answer_questions` → `VOICE_EDIT_BAD_SETTINGS` (was accepted);
  `PUT per_app:{}` → `{}` + YAML node deleted (Agent-3 `firefox_firefox` residue
  cleaned); `qubit agent -z` → `QUBIT_OK`; all 3 services active, helper fresh
  (pid 172263, hold/kws/idle).
- **Focused sweeps:** 63 (new+integration) + 22 (capture) + 307 (voice-edit
  family) passed. `test_packet7d` 2 failures triaged: branding
  `pyproject.toml` assertion is pre-existing brand scope (file untouched);
  profiles rollback Timeout is flaky (passes solo, 0.81 s).

## 38. /chat stale-session fix — empty resume is FRESH (Hermes parity) — 2026-09-30

**No milestone claimed. Not committed.** User report: every open of `/chat`
reopened a 219-message fossil transcript (`dashboard-chat`, 06:49).

- **Root cause:** `voicebridge/terminal_bridge.py::canonical_terminal_session`
  mapped an empty `?resume=` onto `dashboard-chat` ("converges on the canonical
  default for continuity"), and `pty_ws._default_spawn` passed that through as
  the TUI resume target. The reference (`hermes_cli/web_routers/chat_ws.py` +
  `web_server_chat._resolve_chat_argv`) does the opposite: no `?resume=` and no
  per-tab active-session file means NO resume env, so the TUI forges a fresh
  session via `session.create`. Two tests pinned the buggy behavior as
  "reference behavior" — wrongly.
- **Fix (backend + comments):** empty/None resume → `{"session_id": "",
  "ensured": False}` (no store write, no RESUME env → fresh TUI); any explicit
  non-empty id kept verbatim + ensured. `pty_ws.py` + `TerminalChatPage.tsx`
  comments updated to match. Voice/`/qubit-chat` canonical default untouched
  (`agent.resolve_session_id` unchanged).
- **Tests:** `test_terminal_bridge9u` + `test_new_chat_session9u` updated to
  the fresh contract; fixed 4 pre-existing stale doubles missing the
  `workspace_cwd` kwarg production already sends (`test_terminal_bridge9u` ×2,
  `test_terminal_context_floor` ×2 — all proven failing on pristine HEAD).
  Focused: **59 passed** (bridge/new-chat/pty-ws/profiles/context-floor).
  `test_chat_terminal_parity9u::test_session_open_audit_records_verified_window`
  fails identically on pristine HEAD (live-provider 503) — unrelated.
- **Hermetic proof:** empty → `resume=None`, zero `*_TUI_RESUME` env;
  explicit id → resume + both env names set. Service restarted,
  `qubit-voicebridge` active, `/chat` HTTP 200, reference `:9119` untouched.
  Browser re-verification blocked (Playwright profile locked by another session).

## 39. /chat model switch now applies (muse live) — 2026-09-30

**No milestone claimed. Not committed.** User report: switched to Muse, /chat
still ran deepseek.

- **Root causes (two, compounded).** (1) `POST /api/model/set` scope=main
  never touched the pinned `terminal.profile` (`qubit-go`), so a MODEL-card
  pick + reload still booted deepseek while the copy promised "Reload now to
  apply it". (2) Fresh endpoint profiles default to a 4096 declared window,
  so the muse profile failed /chat's 64k floor on stale data — while the
  live probe consistently resolves its real window as **1048576** (`resolved`,
  generic-fallback filtered).
- **Fixes (backend).** `providers/endpoints.py`: `resolve_real_context_tokens`
  / `backfill_profile_context` (bounded vendored probe; only replaces the
  4096 creation default; explicit windows untouched) + `ensure_path` before
  probing (raw probe failed with `resolver-unavailable` off the vendored
  path); `activate_endpoint` backfills (both activation paths);
  `select_model` reuse carries the recorded window (re-pick no longer
  clobbers 200k→4096 — found live). `qubit_compat.py`: extracted
  `_write_terminal_profile_locked` (used by both writers);
  `_terminal_follow_main_model` moves the pin when the pick meets the floor,
  keeps it with a naming reason when below; `h_model_set` returns
  `terminal_profile_id`/`terminal_follows`/`terminal_detail`.
- **Frontend (rebuilt).** `ModelAssignmentResponse` + `ChatSidebar`/`ModelsPage`
  show the keep reason instead of a reload prompt that would change nothing;
  `tsc -b` clean; 21 vitest pass; `frontend/qubit-dist` rebuilt.
- **Tests.** New `tests/test_model_set_terminal_follow9u.py` **5 passed**
  (backfill×3, follow-moves, follow-keeps); neighbors green (41 passed /
  5 skipped across follow/repair/bridge/new-chat/ops-confirm).
- **Live proof.** Backups `/tmp/opencode/profiles.json.pre-musectx-*` +
  `voice.yaml.pre-musectx-*`. Muse record backfilled 4096→1048576; guard
  `ok=True verified`; `_terminal_follow_main_model` moved
  `terminal.profile: qubit-go → opencode-go-muse-spark-1.3-contributor`
  (operator's explicit intent); service restarted; ws-ticket grant resolves
  **muse-spark-1.3-contributor, ctx 1048576, verified**; `qubit agent -z`
  → `MUSE_OK` (rc 0). Reference `:9119` untouched.
- **Note:** a below-floor pick still keeps /chat on its pin by design (it
  would otherwise be a typed refusal); the UI now says so.

## 40. Spark local tier in /qubit-chat + zero-skip tests — 2026-09-30

**No milestone claimed.** Follow-up to §39 (model switch) + owner "no skips".

- **Spark tier registered.** Last night's Ollama models (`qubit-spark17` 1.7B
  + `qubit-spark4` 4B, abliterated Spark-X2.5, `ollama show` reports 1M ctx
  for both) are now registered LOCAL resolver profiles (roles `chat`,
  ctx 1048576). Voice already pointed at them (`local_llm.model` /
  `runner.alias` / `summarizer` = spark17, `detail_model` = spark4,
  `chat_model`/`local_profile` = brain4, `allow_api` escalation on).
- **Local picks enabled in the MODEL card.** `POST /api/model/set` refused
  every local model (`MODEL_ENDPOINT_REQUIRED` — the endpoint resolution is
  remote-only). `select_model` now activates a matching registered LOCAL
  profile directly (no endpoint/credential; shipped-default local included).
  Live: spark17/spark4/brain4 all switch via the card; `/chat` pin stays on
  the API model with a naming reason (local tier runs /qubit-chat + voice).
- **Live proof (canonical gateway path):** `agent.turn(model=qubit-spark17)`
  → `SPARK17_OK` (identity `local/qubit-spark17`); spark4 → `SPARK4_OK`.
  Main slot restored to `qubit-brain4` (local default); terminal pin stays
  muse. Both chats, different LLMs, one backend (broker/policy/audit,
  one `state.db`).
- **Zero skips.** `test_models_repair9u.patched_qc` no longer skips on the
  dead `/tmp` patch path — the 2026-09-29 spec lives in-tree, so the fixture
  exercises the live handlers (fails loudly if they ever regress): 15 passed.
  Same stale-double drift as §38 fixed in
  `test_chat_terminal_parity9u` (missing `workspace_cwd` → server answered
  503): 23 passed.
- **Combined: 112 passed / 0 failed / 0 skipped** across follow/repair/
  bridge/new-chat/context-floor/profiles/pty-ws/ops-confirm/parity.
  Service restarted (local-pick path live); `/chat` HTTP 200; `:9119` untouched.

## 41. Ollama in the model picker + /qubit-chat on spark17 — 2026-09-30

**No milestone claimed.** Owner: /qubit-chat must offer the local models;
it runs spark17 (better than brain4), routing up when harder.

- **Picker gap (real).** `GET /api/model/options` listed only API providers
  (moa/copilot/opencode-zen/opencode-go) — the vendored builder injects a
  local row solely for the managed llamacpp runtime's staged GGUFs, so the
  Ollama tier was unpickable. New Qubit-owned `_ollama_options_row`
  (`qubit_compat.py`): registered, enabled, chat-eligible LOCAL profiles
  (brain4 shipped default always qualifies; Spark 1.7B/4B registered this
  wave with `ollama show` ctx 1048576), checkmark from the canonical active
  profile. Live: `ollama | Ollama (local) | [brain4, spark17, spark4] |
  current: True`.
- **Local picks mirrored.** `select_model`'s local branch now mirrors the
  runtime model slot (like the remote path), and a one-time mirror fixed
  the double-checkmark (vendored context still named muse while the
  resolver had moved on) — single `CURRENT: ollama`, top-level agrees.
- **Live state.** Main slot `qubit-spark17` (`/qubit-chat` default turn →
  `local/qubit-spark17`, proven); terminal pin stays muse; voice keeps last
  night's wiring (spark17 brain/runner/summarizer, spark4 detail, API
  escalation). Old sessions/logs checked: store holds 09-19 tests + today's
  runs; TUI log shows today's Muse API turns; no contradicting routing.
- **Tests.** New `tests/test_ollama_options9u.py` **4 passed** (row content,
  checkmark, ineligible-skip, runtime mirror). Combined sweep **116 passed /
  0 failed / 0 skipped**. Service restarted; `/chat` 200; `:9119` untouched.
