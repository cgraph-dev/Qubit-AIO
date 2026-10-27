# OPEN_THREADS.md — durable handoff ledger

**Purpose.** This repo's agent sessions repeatedly died mid-work
(`maximum number of tool-calling iterations`, context compaction, "previous
turn was interrupted mid-run") and each new agent started with no knowledge of
what the last one left unfinished — the owner's words: *"agents stop and you
forget to continue work."* This file is the single durable answer to "what was
in flight?". Read it first; update it at the end of every session.

**Source of record.** OpenViking session `20261010_005652_080bf0`
(744 messages, 2026-10-11 04:53 → 2026-10-12 06:00), archives
`history/archive_001..007/messages.jsonl`. Every item below cites the archive
turn it came from and the on-disk state checked **2026-10-12**.

**State-verification legend:** ✅ DONE (verified on disk) · 🟡 PARTIAL /
needs action · 🔴 OPEN · ❓ UNVERIFIED (claim made, not re-checkable).

---

## Wave 57 — 2026-10-27 (Phase 6 slice: local traces + offline eval bench)

- ✅ `qubit_runtime/traces/`: versioned local SQLite store, write-time redaction/bounding, typed missing/corrupt/version errors, list/show/export JSONL/prune. Default OFF (`traces.enabled` / `QUBIT_TRACES_ENABLED`).
- ✅ `qubit_runtime/evals/bench/`: offline JSONL or trace-store eval; accuracy only with labels, latency p50/p95, token totals/means, cost when known, explicit `unavailable` for energy/FLOPs; no model calls, no network.
- ✅ CLI `qubit traces list|show|export|prune`, `qubit evals run --dataset FILE [--json] | --from-traces`; exits 0/2/1. Recipe runner records behind the opt-in gate; `-z` intentionally not wired.
- ✅ Focused tests **41 passed** (traces + eval bench); final combined command with recipes/runtime and parser checks → **169 passed**. Provenance recorded; no upstream code vendored.
- 🟡 Live/operational gaps: no optimizer run (DSPy absent; stated unavailable), no energy/FLOPs sampler, no dashboard surface, no `-z` usage capture.
- 🔴 Not done: no service restart, no full matrix, no `VERIFIED`.

## Wave 60 — 2026-10-27 (HUD + connector/runtime/remediation fleet)

- ✅ Web Voice Edit HUD live: Next.js/Three.js/GSAP static app + loopback WS backend; `panel_backend: web`; Chromium 900×760; Tk fallback.
- ✅ Google Calendar/Dropbox wired; Apple pack platform-gated; `rust_local` registered as optional; token writes atomic; pin TTL; stream-json tools; PII policy; deep-research provider/citation fixes; offline variant selector; P8 inventory.
- ✅ Audit blockers fixed: engine flag precedence, trace WAL snapshot reads, gateway status identity, capture-before-show, stale transcript, contact shapes, recipe terminal/titles.
- ✅ Focused: 733+6 skipped runtime/CLI/connector/recipe/trace/eval set, 134 root voice/HUD, 89 extra, 57 frontend; live HUD screenshot.
- 🟡 Next: exercise one real mic turn through the web HUD (confirm partials/preview/actions), then consider making `web` the default; live connector probes when owner credentials exist; owner sign-off rows from `docs/PHASE8_INVENTORY.md`.
- ℹ️ HUD build: `npm ci && npm run build` in `frontend/voice-edit-hud` (export is gitignored; basePath `/voice-edit`); missing export falls back to Tk with a typed hint.
- 🔴 Not done: full matrix, `VERIFIED`, Tk removal.

## Wave 59 — 2026-10-27 (Phase 4: monitor_operative recipe)

- ✅ Read-only `monitor_operative` over existing surfaces: cron (failed/paused/overdue/next run/ticker), kanban board stats (blocked/oldest-ready/repeated failures; overdue explicitly `not_supported`), memory provider discovery, compression counters via `SessionDB(read_only=True)`. No mutations; each section degrades typed; facts bounded/redacted.
- ✅ `qubit recipe list|run monitor_operative [--engine] [--dry-run] [--json]`; voice `recipe_monitor_operative` ("what's my operative status", "monitor my work"); cron spec `0 */4 * * *`, no job created.
- ✅ Shared recipe section collection moved to `recipes/sections.py`; morning_digest behavior preserved.
- ✅ Tests: recipes + trace integration **72 passed**; monitor voice **5 passed**.
- 🔴 No service restart, no live owner-profile probe, no live cron job, no full matrix, no `VERIFIED`.

---

## Wave 58 — 2026-10-26 (RuntimePort Phase 3 completion: `chat -q`)

- ✅ Native `-Q`/`-q` single-query turns now cross `RuntimePort` via `run_agent_through_port` (thread-local callbacks re-bound on the port worker; raw envelope retained). Non-native `chat -q` skips native agent construction and runs through P2; interactive chat/TUI and native-only flags refuse typed (exit 2).
- ✅ Focused gate: 52 passed (new `test_chat_query_engine.py` 16 + oneshot/single-query/quiet suites).
- 🟡 Non-native resume supplies native-store history but does not persist the new turn (P2 stateless); clarify is bounded by the non-interactive note; no live OpenJarvis turn probe.
- 🔴 No service restart (CLI-only), no full matrix, no `VERIFIED`.

---

## Wave 56 — 2026-10-26 (Phase 5: Google Contacts connector)

- ✅ Read-only `connectors/google_contacts.py` over the existing `$QUBIT_HOME/google_token.json` and google-auth refresh path; `list`/`search` (People API, bounded), typed `missing_credential`/`missing_scope`/`reauthorize_required`/401/403/429/timeout/network/HTTP errors; results bounded and token-safe.
- ✅ Exposed as the read-only `google_contacts` tool/toolset with `search`/`list`; discovery only when the existing token is present. No second OAuth flow/store, no writes.
- ✅ Tests: connectors **45 passed**; focused tool registry/toolset/config set **184 passed, 6 pre-existing skips**.
- 🟡 Live probe blocked on owner Google credentials with Contacts scope; a cold-cache search may be stale immediately after the warmup (documented). Token refresh persists to the same file using the skill's existing pattern.
- 🔴 Not done: Apple Calendar/Contacts/Health/Music, Dropbox; live probe; service restart; full matrix; `VERIFIED`.

## Wave 55 — 2026-10-26 (Phase 4: morning_digest recipe)

- ✅ `qubit_runtime/recipes/` typed `RecipeSpec` + registry + RuntimePort runner; `morning_digest` collects weather/calendar/unread mail deterministically and degrades each section with a stable typed reason (`missing_connector`, `missing_credential`, `connector_not_configured`, `tool_error`). Health is honestly unavailable (no connector). Facts/brief redacted; model only writes from facts.
- ✅ CLI `qubit recipe list|run morning_digest [--engine] [--dry-run] [--json]`; native default, OpenJarvis through P2 with typed refusal (not live-probed). Exit 0/2/1/130.
- ✅ Voice: additive `recipe_morning_digest` bridge with "give me my morning digest" aliases, running the read-only recipe and speaking the redacted brief. **Live:** bridge restarted 00:29; alias resolution confirmed in-process; `/chat` 200.
- ✅ Cron helper builds the job spec; no live job created. Owner command recorded in STATUS.
- ✅ Tests: recipes 43 passed; voice recipe 5 passed; runtime 75 passed in the lane. Merge gate: 48 focused passed.
- 🟡 Degraded: no health connector; calendar/mail need the existing Google Workspace token; weather needs its script/network; no live connector probe; no dashboard recipe surface.
- 🔴 Not done: `monitor_operative`/`deep_research`, full matrix, `VERIFIED`.

## Wave 54 — 2026-10-26 (RuntimePort Phase 3: `qubit -z` through the port)

- ✅ `qubit -z/--oneshot` now runs through `RuntimePort` with the native backend; `--engine` (default `native`) accepted on the top-level/chat parsers. `_run_agent` became a factory + `run_turn`; `_CapturingAgent` preserves the raw envelope, usage file, exit mapping and the historical re-raise.
- ✅ Default native call shape is unchanged (no stream callback, no `None` system/task kwargs; contextvars cross to the worker). `--engine openjarvis` uses P2 with a typed refusal (exit 2) and no fallback; non-native flags are refused, not ignored.
- ✅ Focused `nice -n 10` run: 205 passed in the lane (12 new engine tests + a contextvars pin). Merge gate re-ran 42 focused oneshot/runtime tests → 42 passed.
- 🟡 `qubit chat -q` is still native-only and refuses other engines; OpenJarvis turns do not write the native session store (P2 is stateless).
- 🔴 Not done: no service restart (CLI-only change), no full matrix, no `VERIFIED`.

## Wave 53 — 2026-10-26 (Voice Edit panel + live dictation)

- ✅ `voicebridge/voice_edit/overlay.py` redesigned (status pill, live meter, flat actions, diff counts, compact history, dynamic sizing); 757 focused Voice Edit/STT/overlay tests pass; real-Tk visual check on `:0`.
- ✅ Words stream while speaking: helper shows the panel on the hotkey, forwards partials, and the STT client composes segment-aware cumulative text. Wake path shows its final words immediately.
- 🟡 Not live: `qubit-voice-typing.service` was not restarted (outside the authorized restart set). Run `systemctl --user restart qubit-voice-typing` to load the new panel.

## Wave 52 — 2026-10-26 (OpenJarvis test scenarios ported)

- ✅ 11 missing upstream scenarios ported as Qubit-native tests (7 skills, 4 runtime); no upstream import/network; provenance recorded. `tests/runtime` + git skill sources 88 passed; focused hub 144 passed/1 skipped; merged focused set 111 passed.
- 🔴 Not ported (deliberate): their importer/trust layer, eval telemetry/energy sampler, OpenClaw payload runner, GitHub recursive resolver, commit-drift override, live external suite.

## Wave 51 — 2026-10-26 (Cron notice formatting)

- ✅ `cron/notice_format.py` + delivery integration: one status icon, bounded summary, structured details, local/relative times, configured-link only, per-transport rendering/escaping, plain fallback, redaction before truncation. 23 notice tests + 599-file matrix green in the lane; 111 focused passed on merged main.
- ✅ Live: gateway+bridge restarted 22:47 (identify ready, protocol 1, epoch 25; `/chat` 200; Telegram polling); new notices apply to deliveries after the restart. No scheduling/behaviour change.

## Wave 50 — 2026-10-26 (RuntimePort Phase 2: OpenJarvis engine)

- ✅ `agent/runtime_openjarvis.py` (pinned out-of-process `jarvis ask --json` adapter), registered as a non-default engine; `qubit runtime engines|run --engine openjarvis` work. No in-process import; no cross-engine session store.
- ✅ Typed refusals for missing binary, unverifiable/dirty checkout, commit mismatch, binary `g<sha>` mismatch, interface mismatch, invalid/oversized prompt and unresolvable home. Cancel/timeout/Ctrl-C kill the process group (descendants included) and emit exactly one terminal event. Provenance recorded (`THIRD_PARTY_NOTICES.md`).
- ✅ `tests/runtime` **70 passed**; three adversarial reviews, all findings fixed; live turn through OpenJarvis 0583901 + Ollama returned `PORT_OJ_OK5` with usage.
- 🟡 Next: Phase 3 — route the simpler `qubit` oneshot path through the port with `--engine` default `native`; reconcile streaming; keep focused chat/oneshot tests green.
- 🟡 In flight this session (separate worktrees): cron message polish, Voice Edit UI redesign, and selective port of the upstream OpenJarvis test *scenarios* for the adapted surfaces.
- 🔴 Not done: Phase 4–8; no product caller uses OpenJarvis yet; no service restart (diagnostic only); no `VERIFIED`.

## Wave 49 — 2026-10-26 (RuntimePort Phase 1)

- ✅ `agent/runtime_port.py`, `runtime_native.py`, `runtime_registry.py`; diagnostic `qubit runtime engines|run`; no product caller touched.
- ✅ 22 runtime tests + 18 focused chat checks; live `PORT_OK` on `deepseek-v4.1-flash`. Reviewer's two blockers (factory outside try; uncooperative cancel could hang) fixed and pinned.
- 🟡 Next: Phase 2 OpenJarvis backend (out-of-process, pinned commit); then Phase 3 routes oneshot/chat through the port.
- 🔴 Not done: Phase 2–8; no full-matrix rerun (owner waived).

## Wave 48 — 2026-10-26 (OpenJarvis skill sources; Telegram @QubitBot)

- ✅ Owner waived the single clean full-matrix run; split-pass evidence accepted; `VERIFIED` not claimed; Windows out of scope.
- ✅ Ported OpenJarvis's Hermes/OpenClaw resolver semantics into `tools/skills_hub_git_sources.py`; hub/security/installer unchanged; 7 new tests + 137 focused hub tests green; `hermes:`/`openclaw:` search/install proven on local origins; provenance recorded.
- ✅ Telegram handle: `/setusername` does not exist; new **@QubitBot** created, token swapped, gateway restarted, `getMe`/polling confirmed. Old-bot inbound round-trip proven earlier (`message_id 302`).
- 🟡 @QubitBot inbound needs one owner message; then `/deletebot` the old `hermes...` bot.
- 📋 Next: Phase 1 RuntimePort (native) then the OpenJarvis backend; program/spec updated.

## Wave 47 — 2026-10-26 (W49 sign-off; runtime-boundary program queued)

- ✅ 293-file recheck closed: 248/482 → 249/249 lane files green, then the full matrix's 31 extra files green. 107 commits, 310 files changed from `8443a63b4`.
- ✅ Non-gateway full pass 35,646 tests: 5 files/6 tests + 1 non-zero + 1 no-run, all re-ran green alone. Gateway: earlier full pass + 17 failed files re-ran green alone (second pass stopped at 70% under host load).
- ✅ Frozen-pin **AT TARGET** (2,623/2,623); brand **OK (46)**; frontend **1,166 passed** + `tsc` 0; Docker **12 passed**; E2E **26 passed**; live restarted on `0058fd633`, identify ready, `/chat` 200, Telegram polling connected, browser write-path + reload + 0 console errors.
- 🟡 Not proven this session: one clean full-matrix pass (the host ran a 3D game at ~230% CPU; split passes + per-file reruns instead), and a live Telegram **inbound** owner reply (outbound `sendMessage` 200, `message_id 302`).
- 🟡 `VERIFIED` not claimed for the same two reasons; the owner decision on accepting split-pass evidence is open.
- 📋 Next thread: runtime boundary + OpenJarvis program. Spec `docs/superpowers/specs/2026-10-26-runtime-boundary-and-openjarvis-design.md`, program `docs/superpowers/plans/2026-10-26-runtime-boundary-and-openjarvis-program.md`, Phase 1 `docs/superpowers/plans/2026-10-26-phase-1-runtime-port-native-backend.md`. Execution chosen: **native** for Phase 1, subagent-driven for later bulk phases. Gated on P0 sign-off.


## Wave 45 — 2026-10-24 (CI runner online; GitHub billing gate)

- ✅ `qubit-ci.yml` added (actionlint-clean); self-hosted runner `qubit-local` online.
- ❌ Every run still `startup_failure` 0 s / no jobs / `path: BuildFailed`, even fresh filename and one-line echo; Actions enabled, repo healthy. Account's private-repo Actions minutes exhausted/spending limit — owner must raise billing or make the repo public.
- 🟡 Docker daemon works; Dockerfile paths fixed; harness image build still heavy.
- 🟡 `~/.hermes` moved to `~/.hermes.retired-20261024`; `.pre-retirement` kept.
- 🔴 Remaining: 22 e2e probes; Docker image build; CI billing; `VERIFIED`; no service restart.

## Wave 44 — 2026-10-24 (Docker repaired; CI account-blocked; old home moved)

- ✅ Docker daemon repaired (stale docker0 network DB/interface, buildx installed, enabled); `docker run` smoke passes. Dockerfile rebrand paths fixed; harness image build still heavy.
- ✅ CI workflow committed; GitHub Actions starts but every run is `startup_failure` 0 s/no jobs — account private-repo Actions billing/limit. Owner must raise the limit or add a self-hosted runner.
- ✅ `~/.hermes` (stale lock only) moved to `~/.hermes.retired-20261024`; `.pre-retirement` kept; services/chat unaffected.
- 🔴 Remaining: 22 e2e probes; Docker image build; CI billing; `VERIFIED`; no service restart (another agent owns the tree).

## Wave 43 — 2026-10-24 (B1 retired, AT TARGET)

- ✅ Added the pin's inert `_gateway_lock_handle = None` (no readers/writers) and removed the exclusion; audit now **AT TARGET** (0 missing, 100%).
- ✅ No runtime behavior change; runtime ownership remains `process_ownership`. `test_status.py` + `test_control_listener_fatal.py` 92 passed / 5 skipped.
- ✅ Docs: FROZEN_TARGET numbers/exclusions, DECISIONS ruling 10, STATUS.
- 🔴 Remaining: 22 e2e/upgrade probes; Windows/Docker skips; no `VERIFIED`; services not restarted (another agent owns the tree).

## Wave 42 — 2026-10-24 (all PRODUCT GAP groups closed)

- ✅ Discord turn-prompt parity (14), Discord stale voice binding (11), WhatsApp multiplex (7) fixed and unxfailed; PRODUCT GAP mentions 6 → 0.
- ✅ Focused: Discord slash 28, voice set 112, WhatsApp set 157 — green.
- 🟡 22 e2e/upgrade `wave-25` probes remain the backlog (full managed-env update check hangs here); partial harness work discarded, no red.
- 🔴 Remaining: 22 e2e probes; no `VERIFIED`; B1 exclusion; Windows/daemon/e2e skips; retirement; backend fixes need a gateway restart.

## Wave 41 — 2026-10-24 (149 wave-25 markers closed; parity restored)

- ✅ 171 → 22 `wave-25` markers; 3 wave-20 and the last 2 wave-23 markers also removed.
- ✅ Real fixes: guest-free-tier/dead-claim stamp, desktop free-tier retry, owed-migration error, source `aheadOfRelease`/rollback, late browser-plugin import, async-delegation terminal target, plugin-guard GitLab/catalog cap, cron owner store/corrupt-store/ladder/script, computer-use per-capture vision, gmail `[]`, API `include_unconfigured`, voice role grant.
- ✅ PRODUCT GAP 17 → 6 mentions; 3 real groups remain (Discord prompt parity 12, Discord stale voice binding 7, WhatsApp multiplex 4).
- ✅ 5 upstream test symbols restored with R2/fork bodies; short-port back to 1 (B1), dead 25 test-side, 0 production.
- 🟡 22 e2e/upgrade probes remain xfailed (full managed-env update check hangs on this host); runtime-mirror harness committed.
- 🔴 Remaining: 22 e2e probes; 3 PRODUCT GAP groups; no `VERIFIED`; B1; Windows/daemon/e2e skips; retirement; all backend fixes need a gateway restart.

## Wave 40 — 2026-10-24 (17 wave-30 markers closed)

- ✅ ACP cold/resume wire controls unxfail; frozen editor-MCP schema enforced in production (owner-scoped).
- ✅ 8 daemon-readiness markers cleared by pinning QUBIT_HOME (+2 real TUI command-discovery delegations); 5 local-recovery peer markers cleared; hosted-room `/events` 401 fixed to share the room grant with status.
- ✅ Settings config-set marker removed with harness keepalive/deadline fixes; kanban dispatcher retained non-strict with measured production transport-budget evidence.
- ✅ Verified on merged main: ACP 7, daemon 11, peers 5, hosted room 15, settings 2, kanban 3+5 xpassed — all green.
- 🔴 Remaining: broader xfail backlog outside this metric (155 wave-25 + 11 PRODUCT GAP); no `VERIFIED`; B1 exclusion; Windows/daemon/e2e skips; retirement; all backend fixes need a gateway restart (none done).

## Wave 39 — 2026-10-24 (real gaps from broader regressions fixed)

- ✅ MCP wrapped-401 classification (27+67+20 passed), managed-dir env layering (107), backup suite (94), catalog/providers (27), web health/plugin brand/skill-count/FTS doctor (114), WAL contention + state-file hang (301; 289 twice), and the three wave-36 product gaps (/diff frozen policy, chat endpoint flags, parked served set).
- ✅ Complete-tree `wave-2x` markers are now **0**.
- 🟡 **17 `wave-30` markers remain** in a separate class (ACP cold/resume, daemon readiness, frozen MCP schema, hosted-room events 401, plus two non-strict load flakes) — next wave targets them.
- 🔴 Remaining: the 17 wave-30 markers; no `VERIFIED`; B1 exclusion; Windows/daemon/e2e skips; retirement not executed; no service restart (fixes are on disk only).

## Wave 36 — 2026-10-24 (MCP storm fixed; 10 skips closed; regressions green)

- ✅ MCP: unresolved env refs fail closed; auth-rejected servers bounded (5 probes, 300→3600 s backoff, latch) with explicit revival; discovery cannot bypass; stderr capped at 16 MiB. 7 new tests, 143 focused passed.
- ✅ 10 more hermetic skip removals after pinning `QUBIT_HOME` + bounding reads/WS opens; one re-skipped for in-file state leakage; `test_config_policy_daemon` remains with production evidence.
- 🔁 Dead-symbol cleanup attempted then reverted: it regressed short-port 1→30 by deleting upstream-defined test symbols.
- ✅ Regressions: gateway 41/44 clean, non-gateway 37/41 (1190 passed), voice 455/455, browser 12/12 with 0 idle polling. All deterministic failures found were fixed (turn-runner fakes, write-safety env gate, SOUL migration, Node brand, ACP xfail).
- 🟡 MCP fix is not live (gateway restart needed); `mcp-stderr.log` truncated 214 MiB→0 manually.
- 🔴 Remaining: no `VERIFIED`; B1 exclusion; remaining Windows/daemon/e2e skips; retirement not executed; no service restart.

## Wave 35 — 2026-10-23 (Telegram live; two skips closed)

- ✅ Telegram is configured and connected in polling mode (`@hermes_2ilminaltdmccwkh_bot`, owner chat); `getMe`/`getChat` OK.
- ✅ The two "live Telegram" tests are hermetic (fixture BotAPI). Their blocker was the DELETE-mode `state.db` read lock (5 s default). Bound at 30 s, no assertion change: startup/SIGKILL recovery 1 passed; pause notice 2 passed (`deaed99e7`, re-run on merged `main`).
- ✅ Real round-trip PASS: message 241 → owner replied `TG_E2E_OK_20261023` at 20:40:39 → assistant replied exactly at 20:40:46 (7 s), in the long-lived Telegram session `20261002_073827_abd0a053` (verified in `runtime/state.db`).
- 🔴 Remaining: no `VERIFIED`; B1 exclusion; remaining Windows/daemon/e2e skips; retirement not executed.

## Wave 34 — 2026-10-23 (frontend green; live E2E chat)

- ✅ Backend regression (read-only): 30 files → **166 passed, 0 failed, 5 skipped** across all wave-32 surfaces.
- ✅ Frontend: **147 files / 1166 tests pass**, `tsc -b` clean, eslint 0 errors (108 warnings) after reconciling stale route/Kitchen assertions, using `lib/clipboard.ts` in OmniRoute, and typing two test mocks (`8d18d3c35`); served bundle rebuilt.
- ✅ Live E2E through the real dashboard with the real model: New Chat → prompt → exact reply `E2E_OK_20261023`; session `20261023_194637_dd96c6` persisted (`state.db`, 2 messages, 0 tool calls), API + reload agree, status `ready`, 0 console errors; screenshots `/tmp/opencode/w34/`.
- 🔴 Remaining: no `VERIFIED` (owner gate + full release matrix); B1 exclusion; live/Windows/e2e skips; retirement not executed.

## Wave 33 — 2026-10-23 (dashboard request storm fixed)

- ✅ Root cause measured live: `/api/events/stream` replays ~1,913 historical journal frames on connect; each `session.updated`/`model.changed` frame fired `/api/model/info` + `/api/config`. Page load: 56 config + 67 model-info requests in 6 s (the wave-32 "idle polling" observation).
- ✅ Fix (`b2ee20b3c`, `6231a81ca`): refresh only for our session or an id-less broadcast + 200 ms trailing coalescing; user actions stay immediate.
- ✅ Verified: ChatSidebar 20/20, `tsc -b` clean, eslint 0 errors, build succeeded and the served bundle was rebuilt; live reload now **2 config + 3 model-info** in 6 s, 0 console errors, resumed transcript/status `ready` render. No service restart.
- 🔴 Remaining: the server still replays the full journal on connect (cursor trimming is a separate change); no E2E/`VERIFIED`; B1 exclusion, documented live/Windows/e2e skips, and retirement are unchanged.

## Wave 32 — 2026-10-23 (seven skips closed; browser E2E PASS)

- ✅ Six product gaps fixed and seven skips removed (all re-run on merged `main`): API authority fixture port; hosted-mux canonical single service; cold-profile WS scope key; classic one-shot gateway session store + frozen reasoning/`max_turns`; boot recovery of queued `/v1/runs` + API clarify ACK; top-level `-z` first-run guard.
- ✅ Focused verification: api authority 1+1, crash recovery 1, pending daemon 1, hosted mux 5, cold multiplex 1, secondary local 1, session policy 22, first-run live 3, hosted attachments 24+16, session-store 1, TUI first-run 2, oneshot 2+14, main lifecycle 5 — all passed.
- ✅ Browser E2E (read-only Playwright): 12 checks PASS; resumed 691-message transcript renders, status `ready`, reload rehydrates, responsive/sidebar/search pass, 0 console errors, 0 failed requests; screenshots in `/tmp/opencode/w32-e2e/`. Destructive controls and error states not exercised → no `VERIFIED`.
- 🟡 New observation: idle dashboard polling is heavy (73 config + 102 model-info calls in 59 s). Next investigation.
- 🔴 Remaining: 1 production symbol = declared B1 exclusion; Windows/e2e/live harnesses are documented skips; no E2E release verdict; retirement not executed. Measured absent 0, short-port 1/1, total 1, markers 0, 0 production dead / 32 test-side.

## Wave 31 — 2026-10-23 (absent 0; total missing 1 declared)

- ✅ 7 lanes (4 implementation + 3 read-only) off `369be9e6a`: the last 25 absent files ported (`012af7548`, `4676abfcd`, `e196866df`); live/Windows/e2e harnesses are documented module skips naming the missing subsystem.
- ✅ R2 divergence: the pin's three stable-lookup test names restored with R2 `ChannelRecord` bodies/docstrings; no GitHub resolution; test short-port symbols 3 → 0 (`d648edc9c`).
- ✅ Worker rotation: the wave-30 FIFO skip was a harness misattribution (DELETE-mode read contention + receipt timeout); bounded retries/timeouts, no production change, 3 consecutive 2/2 passes (`82513bd33`).
- ✅ Multiplex: secondary local pick unskipped via `QUBIT_HOME` (2× pass, `bb898b336`); six remaining live skips have precise evidence (`3050876cd`).
- ✅ Measured: absent **25 → 0**; short-port **4 sym / 2 files → 1 sym / 1 file** (declared B1 `_gateway_lock_handle`); total missing **4 → 1**; markers **0**; 0 production dead / 32 test-side; brand OK (46).
- ✅ Final focused matrix (read-only): 33 files → **208 passed, 7 skipped, 1 xfailed, 6 xpassed, 0 failed / 0 timeout** (load 6.3 → 4.3).
- ✅ Live (read-only): four units active; identify ready/protocol 1/`authority_epoch=17`; `/chat` 200 SPA; `/qubit-chat` 200; `/api/health` 200; no voice tracebacks; EG2 enabled and model present. `/v1/models` absent because `platforms.api_server.enabled=false`; `/health` is the SPA catch-all (use `/api/health`).
- ✅ Docs: `docs/FROZEN_TARGET.md` §Numbers updated to the measured state and the retirement gate reworded to "zero apart from declared exclusions".
- 🔴 Remaining: **1 production symbol = the owner-declared B1 exclusion** (verdict `NOT AT TARGET -- 1 production missing`); six live harnesses are documented skips; no E2E/`VERIFIED`; retirement not executed (`~/.hermes`, `~/.hermes.pre-retirement` remain); no service restart (post-reboot units already run the wave-30 tree; orb active at ~one core).

## Wave 30 — 2026-10-23 (25 absent; 0 markers)

- ✅ 7 lanes: strict `wave-2x parity defect` markers **77 → 0**; absent files **87 → 25**; 8 production defects fixed.
- ✅ Root cause of most markers: ported peers set only `HERMES_HOME` (fork resolves `QUBIT_HOME`) → `session.create` refused `storage_unavailable`; setting both homes cleared 17 markers without touching assertions (`984c5119a`).
- ✅ Production: profile-scoped child notices, fail-fast DELETE claim writes, `soul` profile create, service consent admission, free-inference eligibility, HERMES_HOME+QUBIT_HOME child publication, model lock in TurnRunner build, prune `deleted_ids`, explicit `stdin=` on 30 children, completion retry refund, webhook signing provenance, startup-restore author admissions, batched completion scope, local read/setting commands, single-line ensure JSON, and LOCAL `policy.platform` + frozen skills prompt.
- ✅ Measured: markers **0**; absent **25**; total missing **4** = production **1** (declared B1 `_gateway_lock_handle`) + tests **3** (deliberate R2 stable-lookup adaptation); 0 production dead / 34 test-side.
- 🟡 Documented non-strict intermittent under host load (load reached ~16 during the wave): `test_session_kanban` real dispatcher, `test_session_setting_commands` config-set (each passes alone).
- 🟡 Daemon/multiplex/live/Windows/e2e harnesses were skipped with exact reasons; `test_worker_compression_admission` rotation FIFO identity is **unverified, not fixed** (state.db locked under the hermetic host).
- 🔴 Remaining: 25 absent files (mostly live/daemon/Windows/e2e and asset-dependent CLI suites); no service restart (running services still serve pre-wave code); no E2E/`VERIFIED`; retirement checklist not executed.

## Wave 29 — 2026-10-22 (87 absent; 77 markers)

- ✅ 4 lanes: 52 cases fixed, 29 absent files + 26 fixtures ported into the audit-visible tree.
- ✅ Measured: markers **88 → 77**; absent **148 → 87**; short-port **1** (B1); total **1**; production **1**; 0 production dead (34 test-side).
- ✅ Performance: stray orb at ~78% CPU for 2h41m killed; load ~16 → ~1.3. Possible manual orb killed by lane cleanup; orb unit inactive pending WIP.
- 🔴 Remaining: 77 xfail markers; 87 absent test files; orb WIP; no E2E/release verdict.

## Wave 28 — 2026-10-22 (148 missing; 88 markers)

- ✅ 4 lanes: 24 more markers fixed, 30 absent files ported into the audit-visible tree, stale bot-delivery test reconciled.
- ✅ Measured: markers **112 → 88**; absent **178 → 148**; short-port **1** (B1); total **1**; production **1**; 0 production dead (33 test-side).
- ✅ Crash follow-up: new report was a combined orb test run (known Qt teardown), removed; notifier masked; managed-worker fix remains.
- 🔴 Remaining: 88 xfail markers; 148 absent test files; orb inactive pending WIP; no E2E/release verdict.

## Wave 27 — 2026-10-22 (crash fix; 112 markers)

- ✅ 4 lanes: 14 gateway defect cases, 23 absent gateway files, profile/CI fixes; markers 126 → 112.
- ✅ Crash investigation: test-induced `agent.managed_worker` SIGABRT at Python finalization; fixed with flush + `os._exit`; 11 worker tests pass; crash file removed.
- ✅ Notifier: `update-notifier-crash.path` masked (GUI notifier failed without DBUS); reversible.
- ✅ Measured: absent **201 → 178**; short-port **1** (B1); total **1**; production **1**; 0 production dead (33 test-side).
- 🔴 Remaining: 112 xfail markers; 178 absent test files; orb inactive pending its uncommitted WIP; no E2E/release verdict.

## Wave 25 — 2026-10-22 (short-port 1; code parity at pin)

- ✅ 8 lanes: 50+ defect cases fixed, **92/101 short-port symbols closed**, 38 absent files ported, release/install long tail, detector split, EG2 template docs.
- ✅ **Short-port 101 → 1** (only the owner-declared B1 exclusion); **total missing symbols 101 → 1**; 0 production dead (33 test-side).
- 🟡 Absent files remain 201 by the audit because the 38 new files live in the root `tests/` tree; effective ported files +38.
- 🔴 Remaining: 126 strict xfail markers; 201 absent test files (non-blocking for retirement); no global E2E/release verdict.
- ✅ Live: applied; bridge/gateway/orb verified.

## Wave 24 — 2026-10-22 (101 missing; 50 defects fixed)

- ✅ 50 more cases fixed (gateway/CLI/tools), 42 absent + 23 short-port files ported, source R2 tests adapted to the fork's contract, relay admission guard fixed, 15 dead test helpers removed.
- ✅ Measured: absent **243 → 201**; short-port **117 → 101**; total **117 → 101**; production **1** (declared B1); **0 production dead** (5 test-side).
- 🔴 Remaining: 122 xfail markers; 301 test symbols (201 absent + 100 short-port); no E2E/release verdict.
- ✅ Live: applied; bridge/gateway/orb verified.

## Wave 23 — 2026-10-22 (117 missing; EG2 augmenter live)

- ✅ EG2 L1.5 voice augmenter added (`b27d555bc`), flag enabled live: max-sim, UNKNOWN-only, pending/risk veto; 26 tests, real-model probe; 0 executable high-risk routes across 39 probes + 710 utterances.
- ✅ 3 defect lanes fixed 78 cases; short-port lane closed ~137 symbols in 27 files; absent lane ported 39 files (72 new strict xfails).
- ✅ Measured: absent **283 → 243**; short-port **253 → 117**; total **253 → 117**; production **1** (declared B1); **0 production dead** (13 test helpers).
- 🔴 Remaining: 120 xfail markers; 359 test symbols; no global E2E/release verdict.
- ✅ Live: bridge/orb restarted, `/chat` 200, augmenter active.

## Wave 22 — 2026-10-22 (253 missing; 103 wave-20 xfails)

- ✅ 4 continuation lanes: 69 more wave-20 defects fixed (172 → 103), 40 more test files (27 new wave-22 xfails), shared/relay metrics, plugin FIFO, legacy routes, update stop reasons, TUI model/profile scope.
- ✅ EG2 voice benchmark: EG2 q4 94.5% top-1 vs EG1 93.2%, nomic 90.4%, deterministic 92.5%; `det→EG2 on UNKNOWN` 97.3%. Not safe as a replacement (1 high-risk misroute, missed e-stop). Opt-in L1.5 only, with risk veto.
- ✅ Measured: absent **286 → 283**; short-port **155/309 → 116/253**; total **309 → 253**; production **1** (declared B1); dead **0**.
- 🔴 Remaining: 103 wave-20 + 27 wave-22 xfails; 283 absent + 253 short-port test symbols.
- ✅ Live applied. No global E2E/release verdict.

## Wave 21 — 2026-10-22 (83 defects fixed)

- ✅ Fixed 83 of the 255 wave-20 defects across gateway, CLI/agent, and TUI/hosted/ACP; wave-20 xfails **255 → 172**, 0 new.
- ✅ Regression set (notifications/local automation/completion delivery): **94 passed, 1 xfailed**. Dead **0**; production **1** (declared B1); total missing **309**; absent **286**.
- 🔴 Remaining: 172 wave-20 xfails (daemon/native-HTTP groups, plugin admission FIFO, kanban timestamps, relay subprocess↔authority conflict, release/source-channel R2 divergence); 286 absent + 309 short-port test symbols.
- ✅ Live: production fixes applied (restart + identify/chat verification). No global E2E/release verdict.

## Wave 20 — 2026-10-21 (309 missing; 0 dead)

- ✅ Ported ~280 upstream test files across 8 disjoint lanes (gateway A/B/C, qubit_cli A/B, agent, tools, tui/acp/rest).
- ✅ Measured: absent **361 → 286**; short-port **328/638 → 155/309**; total **638 → 309**; production **1** (declared B1); dead **0**.
- 🟡 255 newly exposed production defects are strict xfails (`wave-20 parity defect: …`): gateway authority/settlement/watchdog, API idempotency/steer, managed-worker drain, CLI update/doctor/session, agent provider/pricing, MCP/browser/connectors, hosted-room/ACP onboarding.
- 🔴 Remaining: 286 absent + 309 short-port test symbols; 255 xfails.
- 🟡 No service restart for this test-only wave; no global E2E/release verdict.

## Wave 19 — 2026-10-21 (0 dead; 0 wave-18 xfails)

- ✅ Fixed 118 parity-exposed defects across reasoning, compression, safe-worker/MCP, gateway lifecycle, state/session, connectors/files, onboarding/config, TUI/hosted/approval/browser; the last (cron manual-run exit code) fixed separately.
- ✅ Measured: dead **0** (12 benign exclusions); production **1** (declared B1); total missing **638**; absent **361**; wave-18 xfails **0**; 43-file consolidated run **349 passed**; brand guard OK.
- ✅ Live: four units restarted; identify ready; `/chat` 200; orb waiter ready. No global E2E/release verdict.
- 🔴 Remaining: 361 absent + 638 short-port test symbols.

## Wave 18 — 2026-10-21 (0 dead)

- ✅ Landed: bot-chat authority FIFO + cron parking, hot-serve reserve/serve + single-application `one_turn_route`, local `review.summary` + reset-tip barrier + title parent revision, non-mutating export + duck-typed fanout drain, ACP launch flags + `gpt-6.1-sol`, detector FP classes, 58 parity files.
- ✅ Measured: dead **17 → 0** (13 benign exclusions); production **1** (declared B1); total missing **642 → 638**; absent **420 → 361**; wave-17 xfails **0**; brand guard OK.
- 🟡 119 parity-exposed defects are strict xfails (signed-thinking replay, compression telemetry/rollback, Gemini decode ceiling, MCP deadline, safe-worker policy, onboarding half-rebrand, queue/stop/settlement races, connector identity/files, session ledger/compaction, TUI onboarding/fanout).
- 🔴 Remaining: 361 absent + 638 short-port test symbols; 119 xfailed defects.
- ✅ Live: four units restarted; identify ready/epoch 7; `/chat` 200; orb waiter ready. No global E2E/release verdict.

## Wave 17 — 2026-10-21 (17 dead)

- ✅ Landed: write-file baseline (9 xfails→pass), shared metrics connector/tool_search + recommendation floor (7 xfails→pass), compaction events + auxiliary relay (call_role split fixed), inline bootstrap shape + left-core, agent plugins + skills-guard v10/plugin-guard v11, release admission (10 dead resolved) + `select_apply_target`, 28 test files ported.
- ✅ Measured on merged `main`: dead **27 → 17** (10 resolved, 0 new); production **1** (declared B1); total missing **766 → 642**; absent **448 → 420**; brand guard OK.
- 🔴 New defects xfailed strict (unported): ACP launch flags dropped; ultrafast catalog omits `gpt-6.1-sol`; bot-chat owner-read not parked/receipted; local viewer misses `review.summary`; resume reset-tip unknown admission; read-only export mutates `state.db`; title transfer parent revision fence; fanout drain concrete-`WSTransport` only.
- 🔴 Remaining: 17 dead (bot-chat authority/FIFO, hot-serve pair, `one_turn_route`, detector FPs); 420 absent + 642 short-port test symbols.
- 🟡 Wave 16 applied live; wave 17 restart follows the commit. No global E2E/release verdict.

## Wave 16 — 2026-10-21 (F5 ported; 27 dead)

- ✅ Owner rulings: B1 `_gateway_lock_handle` declared an exclusion (upstream-dead at the pin); F4 kept fatal (pin behaviour) and documented; F5 rejected — all 21 exclusion rows ported literally.
- ✅ Landed: f5-small (housekeeping, backup 3, ACP fingerprint, bot_relay 2, llamacpp, cron newlines, `legacy_digests`; `_tool_prefix` was already live), f5-release (source_releases 4, loop/heartbeat managers, resume refusal, classic launch policy), authority `adopt_agent` + its ported test, API canonical projection + clarify/resolve routes, 26-file test-parity batch, orb `ExecStartPre` display wait.
- ✅ Measured on merged `main`: dead **29 → 27** (2 resolved, 0 new); production missing **20 → 1** (declared B1); total missing **922 → 766**; absent **451 → 448**; brand guard OK.
- 🔴 Residuals: 27 dead (deferred groups in `TRIAGE.md` §Wave 15); 448 absent + 766 short-port test symbols; parity defects triaged but unported (write_file baseline, shared metrics connector rows, recommendation floor, compaction events, auxiliary relay, inline bootstrap identity, left-core, agent plugins, skills guard); orb installed unit not refreshed and no service restarted; frontend bundle still stale.
- 🟡 No service restart, nothing applied live, no global E2E/release verdict.

## Wave 15 — 2026-10-21 (29 dead symbols)

- ✅ Landed: API caller system/instructions + canonical stream; shutdown PID/lock ordering + admitted completion; compressed receipt + hosted work evidence; snapshot restore + frozen config credentials; shared approval snapshot + connectors guest identity; long-home control pointer/loop-tick witness/session reset/dashboard issuer/coding posture; inbound plugin-injection + turn-hygiene mixin inheritance.
- ✅ Dead **32 → 29** merged (0 new). All wave-15 branches merged; worktrees removed.
- 🔴 Residuals: `test_session_authority_cancel_settlement` needs `authority.adopt_agent` in `run_turn_runner.py`; `test_api_canonical_projection` API-lane-owned; 16 pre-existing connector-operation/managed failures; 29 dead = deferred architectural/release groups.
- 🟡 Live services were not restarted this wave; existing gateway is on the authority boot path.

---

## Wave 14 — 2026-10-20 (32 dead symbols)

- ✅ Landed: relay metadata handoff; coding-worktree note; Codex rotating ids; backup-cron keyword; per-skill plugin notice; launcher legacy alias; `request`/`request.cancel` order; cross-process skills index; HX013 sleep rule; four gateway-gap tests (production already conformed).
- ✅ Dead **34 → 32** merged (0 new). Test gap **525 → 492** absent (30 files/81 tests).
- 🔴 New defects: API caller system/instructions dropped; shutdown releases PID/lock before drain; compression receipt transcript; hosted work-evidence capture; snapshot restore drops gateway session; config credential recovery; admitted completion dropped; shared approval snapshot; canonical stream events; `tools.connectors.ensure_guest_identity`.
- 🔴 Remaining: 32 dead, 492 absent + 924 short-port tests, B1/F4/F5, retirement checklist.

---

## Wave 13 — 2026-10-20 (34 dead symbols)

- ✅ Landed: API admission/interrupted-run/`/chat`; overflow detach + attachment Path; `route_runtime` fallback + `@file` cwd; MSGraph admission + ACP no-MCP; HTTP lifespan/WS auth; `_compression_tip_on`, `SessionEntry.yolo`, terminal-retirement gaps.
- ✅ Dead **46 → 34** merged (12 resolved, 0 new; 6 deleted with exclusions). Test gap **566 → 536** absent (30 files/60 tests).
- 🟡 Combined-process-only isolation leak: `test_ingress_admission_off_loop` fails with leaked profile scope when run beside other files; passes alone and under `scripts/run_tests.sh` per-file isolation.
- 🔴 New defects: relay metadata handoff; coding-worktree prompt note; Codex rotated item ids; `backup_cron_prompts` `hermes_home`; per-skill plugin notice; launcher legacy alias; `request.cancel` ordering; cross-process skills index; 56 gateway behavior gaps.
- 🔴 Remaining: 34 dead, 536 absent + 914 short-port tests, B1/F4/F5, retirement checklist.

---

## Wave 12 — 2026-10-20 (56 → 46 dead symbols)

- ✅ Landed (`e79624c65`, `a0d165157`, `6e4f36cd9`, `a23e4621f`, `11fe1ce99`): api/runs helper dedup; hosted-room work-evidence test port; cron-health and profile-export test callers restored for `_metric`/`_seed_stores`; terminal-retirement test port; clarify interrupt withdrawal for unlimited waits + its `has_pending` test.
- ✅ Dead **56 → 46** (10 resolved, 0 new); all 46 remaining triaged at the pin in `TRIAGE.md` §Wave 12.
- ✅ Deletion recorded: `legacy_digests` (test helper) in `docs/FROZEN_TARGET.md`.
- 🔴 Newly exposed, xfailed: retired-row backfill via `update_token_counts`/`ensure_session`; reset-child delete leaves the logical parent; `prune_sessions` does not reclaim past-horizon retirement tombstones.
- 🔴 Blockers unchanged: hosted-room attachments/`input_context`; bot-chat authority/FIFO family; durable run admission + clarify/resolve-unknown routes; hot-serve (`serve_profile_runtime` durable recovery can double-admit); inbound/turn mixins; `completion_admission`; `one_turn_route`; release admission; HX013.

---

## Wave 12 — 2026-10-20 (46 dead symbols; brand strings fixed)

- ✅ Landed: reasoning-field rejection recovery; codex compaction attempt records; hygiene `trigger="gateway_hygiene"`; dead **56 → 46** (0 new); parity 25 files/48 tests (absent 596 → 571); user-facing `~/.hermes` guidance replaced with Qubit wording (`check_brand_half_rename.py` OK, 46 globals).
- 🔴 New defects: API admission alias, `prompt_attachments.Path`, `_compression_tip_on`, `SessionEntry.yolo`, `route_runtime` fallback, model alias, `@file` cwd, interrupted-run status, MSGraph admit, ACP MCP discovery, HTTP lifespan/WS, event overflow; plus three dead-12 xfails (late backfill resurrects retired session, reset-child delete leaves parent, tombstone prune).
- 🟡 Load-bearing `hermes` names remain and are allowed: `HERMES_*` env vars, the legacy home fallback (`_LEGACY_HOME_DIR`), wire-protocol keys (`hermes.status`, `hermes.run`, ACP `hermes` meta), upstream attribution/provenance, and tests.
- 🔴 Remaining: 46 dead, 571 absent + 897 short-port tests, B1/F4/F5 owner items, retirement checklist.

---

## Wave 11 — 2026-10-20 (56 dead symbols; retirement audit)

- ✅ Landed: API chat `_handle_message` seam + keyed Responses durable replay; strict web models; session-delete retirement; DELETE-mode `_read_ctx` deadlock; watch completion identity; HMWA signature; worker usage projection; `/stop`/`/new` durable follow-up cancel.
- ✅ Dead **73 → 56** merged (0 new); test gap **639 → 610** absent (29 files/67 tests).
- 📋 `docs/RETIREMENT-READINESS.md`: production gap = 10 symbols (9 declared exclusions + undeclared `_gateway_lock_handle`); audits use `~/upstream/hermes-agent`, not `.pre-retirement`; live identify is ready; frontend bundle stale.
- 🔴 Next wave: reasoning-field rejection recovery; codex compaction attempt record; hygiene `trigger="gateway_hygiene"`; B1 exclusion declaration (owner); remaining dead/test parity.
- 🔴 Owner: F4 control-socket fatal vs non-fatal; F5 exclusion sign-off; retirement checklist unassigned.

---

## Wave 10 — 2026-10-20 (73 dead symbols)

- ✅ Landed: cron transport-only + queued delivery (with the upstream owner-seam test port), internal-wake anchor, A2A `input_id`, `machine.facts`, notification-poller stop, memory-target contract, state transactional worker methods, compression `trigger=`, reasoning replay/carriers, skills-hub raw fetch, connector wire identity.
- ✅ Dead **85 → 73** merged (13 resolved; 1 new is the `cron_owner` pytest fixture, known FP). Test gap **683 → 660** absent (23 files/61 tests).
- 🔴 Next wave: `_ResponsesStream` durable replay; `web_models` strict coercion; session-delete admission/worker retirement; `_completion_delivery_identity` watch branches; `_hmwa_first_contact_notes` signature; API chat `_handle_message` seam; legacy desktop `_read_ctx` hang; managed-worker usage keys; messaging stop timeouts.
- 🔴 Remaining: 73 dead, 660 absent + 911 short-port tests, retirement, F4/F5.

---

## Wave 9 — 2026-10-20 (security parity + 85 dead symbols)

- ✅ Landed: cron give-up/pause, approval warnings crash, safe-worker guards, parked-auth retry, `/reload-mcp` content-aware, startup delegation restore, gateway SPA-token withholding, `retire_runtime_sessions`, native ingress trust, Discord role reauth, ACP edit-approval cwd/boundary.
- ✅ Dead **103 → 85** merged (0 new); test parity 25 files/79 tests (absent 730 → 704 on the branch).
- 🟡 Not merged: cron agent transport-only (`726144831`) regresses 18 pre-pin `test_scheduler.py` bodies; port the upstream owner-seam test first.
- 🔴 Next wave: internal-wake reply anchor; A2A `input_id`; `machine.facts`; TUI notification poller; `_memory_target_error` signature; cron queued delivery; `SessionDB` transactional worker methods; compression `trigger=`; reasoning replay/carriers; skills-hub raw fetch; connector wire identity.
- 🔴 Remaining: 85 dead, 704 absent tests, retirement, F4/F5.

---

## Wave 8 — 2026-10-20 (live boot applied; 103 dead symbols)

Full evidence in `docs/STATUS.md` (top entry).

- ✅ Live: gateway restarted onto the merged boot path; identify publishes `runtime_protocol=1`, `state=ready`, `session-authority-v1`; `/chat` session survived.
- ✅ Closed: list_sessions mixed-clock TypeError; ACP pre-editor credential binding; three bot-delivery gaps (profile scope, no fallback, stale-owner refusal). Dead **128 → 103** (0 new); test gap **756 → 736** absent.
- 🔴 New defects for the next wave: cron agent prompt preparation (should be transport-only); cron `CronExecutionUnknown` pause/give-up; approval warnings-shape unpack crash; safe-worker MCP spawn + plugin-load guards; `/reload-mcp` content-aware schema (3 call sites); gateway startup restore of undelivered delegation completions; parked MCP auth retry loop.
- 🟡 Bot delivery's full authority-only mailbox/relay rewrite is deferred (architectural; local mailbox queue and tests still in place).
- 🔴 Owner: F4 fatal vs non-fatal control-socket bind; F5 exclusions without sign-off.

---

## Wave 7 — 2026-10-20 (10 lanes; defects closed, 128 dead symbols)

Full evidence in `docs/STATUS.md` (top entry). Durable state:

- ✅ Closed: the 9 parity defects (memory `patch`, Alibaba refusal classes, TUI history identity, ACP read-only catalog, session-search read path, bot receipt lock, `mark_job_run(execution_id=)`, session-key namespace) and F2 (duplicate `_start_gateway_*` helpers). Theme callbacks were already correct on HEAD; the upstream test was ported.
- ✅ Measured on merged main: dead symbols **167 → 128** (0 new); absent test files **783 → 764**; `test_source_channel_integration.py` **23 failed → 33 passed**; 158 focused tests green.
- 🟡 Not live: boot/worker changes remain merged-but-not-running (no service restart).
- 🔴 New defects exposed by the parity lane, unmodified: (1) `gateway/session.py:1297::list_sessions` raises `TypeError` comparing naive vs aware `updated_at` when recovered UTC routes meet legacy local-time routes; (2) ACP credential binding is replaced after the editor path (`tests/acp_adapter/test_policy_identity_compat.py`).
- 🔴 Owner decisions still open: F4 control-socket bind failure is fatal in `run_bootstrap.py:855` (old body and `control_socket.py:191` say non-fatal); F5 exclusions added to `docs/FROZEN_TARGET.md` without sign-off.
- 🔴 Remaining: 128 dead symbols (deferred blockers in `TRIAGE.md` §Wave 7), 764 absent + 894 short-port test symbols, retirement.

---

## Wave 6 — 2026-10-20 (residuals + dead symbols + test parity + adversarial verify)

Full evidence in `docs/STATUS.md` (top entry). Durable state:

- ✅ Landed: `same_create_retry` + contract test (`5c578fcdd`); source-release test-stub sync (`b9e1a3b3b`); dead symbols **216 → 167** on merged main (0 new) incl. worker output-log port (`023b9e479`); 20 test files ported, absent tests **804 → 784** (`8f0e0fafe`); connectors-only reader + brand copy fixed (`74ecc8410`).
- 🟡 **Not applied live:** the boot/worker changes are still merged-but-not-running (no service restart by design).
- 🔴 **Owner decisions needed:** F4 — a control-socket bind failure is now fatal in `run_bootstrap.py:855`, while the old `run.py` body and `control_socket.py:191` call it non-fatal. F5 — the dead-symbol wave added exclusions to `docs/FROZEN_TARGET.md:51-55` (owner-declared table) without sign-off.
- 🔴 **Next structural lane:** F2 — seven `_start_gateway_*` helpers exist twice (`run.py` vs `run_bootstrap.py`) with divergent bodies; two tests import the stale copies (`test_multiplex_hot_serve.py:167`, `test_startup_restart_race.py:382`). De-dup carefully; `_start_gateway_replace_existing_instance` is live in both contexts.
- 🔴 **9 production defects found by the test-parity lane, unmodified:** memory single-op `patch` unreachable (`tools/memory_tool.py:104,136`; `qubit_cli/write_approval_commands.py:105`); Alibaba content-policy refusal classes (`agent/error_classifier.py:359`); repeated-answer history reconcile by `(role,text)` instead of row identity (`tui_gateway/server.py:3153`); theme callbacks absent (`tui_gateway/agent_callbacks.py:146`); ACP `list_sessions` creates storage / ignores read-only catalog (`acp_adapter/session.py:210`); session-search read path creates the DB (`tools/session_search_tool.py:629`); bot receipt reader lacks the writer lock (`tools/bot_live_delivery.py:459`); `cron.jobs.mark_job_run` lacks `execution_id` (`cron/jobs.py:2424`); duplicate session-key namespace inverse (`gateway/session.py:666` vs `qubit_state_keys.py:77`). Each has the upstream test named in `docs/STATUS.md`.
- 🔴 **Still open:** 167 dead symbols; 784 absent + 894 short-port test symbols; retirement.

---

## Wave 5 — 2026-10-20 (5 lanes merged; boot fix not live)

Full evidence is in `docs/STATUS.md` (top entry). Durable state:

- ✅ Merged on `main`: `49f749514` (scoped turn half + fanout overflow), `c7f5c7409` (3 security/correctness defects), `b0a3d8613` (titled session / welcome trial / owner prune), `bd4e2d913` (dead-symbol tranche 3 + 17), `f3a1e6898` (boot reservation + session authority; `worker.adopt` rung 4 on a scratch home).
- 🟡 **Not applied live:** the boot/worker changes are merged but the running `qubit-gateway.service` still executes the pre-merge code (no restart by design). A restart is needed to exercise it live; prove on a scratch home first if in doubt.
- 🔴 **Open residuals (pre-existing, named by the lanes, not fixed):**
  - ✅ Fixed on branch `fix/residual-gw-wave6` (`f33527445`): `same_create_retry` is defined in `qubit_state_local.py`, with both call sites' retry/conflict paths tested.
  - ✅ Already fixed in `main` by `6a8dbf5c1` (canonical contract module imported), pinned by `d4bd5e917`; the boot path now validates against `CanonicalSnapshot`, not `SessionCreateResult`.
  - `test_source_release_channels.py` / `test_source_release_probe.py` still have pre-existing failures unrelated to `_probe`/forward-only (later `_prepare_git_command` lambda signature, zip fallback, config-probe subprocess).
- 🔴 **Remaining milestone work:** 216 dead symbols (13 deferred architectural ports, evidence in `TRIAGE.md`); 1,691 test symbols / 804 files (non-blocking); retirement steps. Re-run `qubit_runtime/scripts/check_dead_symbols.py` and `scripts/audit_sync_completeness.py` rather than quoting these counts.

---

## 0. THE ONE THING THAT MATTERS MOST

**🔴 The entire session's work is UNCOMMITTED.**

- `git log -1` = `bd6ec9042 Add OmniRoute opt-in integration design spec`
  (2026-10-11 **08:08**). Nothing since — the session's last ~22 hours produced
  **zero commits**.
- `git status` shows **23 changed/untracked paths**, including every fix below:
  `voicebridge/qubit_compat.py`, `webui.py`, `agent.py`, `mcp_servers.py`,
  `voicebridge/kitchen.py` (new), `voicebridge/viking_studio.py` (new),
  `frontend/qubit-web/src/App.tsx`, `OpenVikingStudioPage.tsx` (new),
  `OmniRoutePage.tsx` (new).
- **Next action:** review the diff and **commit** (the owner's own
  `autobackup` branch / `auto-push repos` cron at 23:30 nightly only pushes
  what is committed). This is the highest-value action in the file — until it
  is committed, a crash loses everything and the next agent re-does it all.

---

## 1. Unfinished / at-risk items, prioritized

### P1 — Live verification gaps (things "done" in source but never seen working)

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 1.1 | **Sidebar grouping / collapsible groups not seen rendering** | Source has groups (`App.tsx:398` `"Voice & Speech"`, `:407` `"Tasks & Scheduling"`, children at `:401`/`:410`); bundle rebuilt (`qubit-dist/index.html` 2026-10-12 09:00). Owner said *"sidebar menu still the same wtf"* twice (archive_006:171, archive_007:1) | Recurring complaint across 3 archives; each time answer was "hard-reload, asset hash changed" | Have an agent **drive a real browser** (Playwright was unavailable all session — "socket-path error") to `http://127.0.0.1:8890/chat`, hard-reload, and screenshot the rendered sidebar. Confirm group parents collapse/expand. | 🟡 |
| 1.2 | **OpenViking studio iframe never seen rendering** | `OpenVikingStudioPage.tsx` wrote + typechecks (0 tsc errors, archive_002:56); routes `/api/qubit/viking/studio-health` + `/studio-wiring` now exist in `viking_studio.py` | `viking_studio.py:37-38…138`; route gate `webui.py:1771` | Load `/open-viking` in a browser and confirm `:1933/studio` actually appears in the iframe (CSP is server-side-proxied but the iframe itself was never eyeballed). | 🟡 |
| 1.3 | **Kitchen backend wired but untested end-to-end** | `voicebridge/kitchen.py` exists (built 08:30); route `webui.py:1784` `POST /api/qubit/kitchen/analyze`; uses REA's `_rea_analyze_js` | `kitchen.py:147`, `:192`; `KitchenPage.tsx` modified | Paste a real repo into `/kitchen` and confirm the decompose→capabilities→plan pipeline returns results (not the old "UI SHELL ONLY"). | 🟡 |

### P2 — OpenViking memory stack (largely working; two loose ends)

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 2.1 | **VLM switched to qubit-brain-vision** | `ov.conf` → `ollama/qubit-brain-vision:latest`, `think:false`, `num_ctx:32768`; config `vision.model: qubit-brain-vision` (line 243). Fixed the `empty_response` extraction failure | `~/.openviking/ov.conf`; `runtime/config.yaml:241-243` | None — working. Extraction proven end-to-end (real profile/preference/event files in store). | ✅ |
| 2.2 | **`memory.custom_templates_dir` guardrail** | Set in `ov.conf` → `/home/trick/.openviking/custom_templates`; drops the `avatar`/`introduction` fields that caused the model to **invent a fake avatar URL** | `ov.conf` `memory.custom_templates_dir`; archive_005:50 (proven across 4 clean extractions) | Verify the dir still holds the custom YAMLs and that the loader glob is **non-recursive** (a `memory/` subdir silently loads 0 schemas — pitfall hit in archive_005:50). | ✅ |
| 2.3 | **`每日节奏确认.md` invented Chinese event** | Was flagged as still in the store (archive_005:71,73) | `find` for it on disk = **absent** | None — gone. | ✅ |
| 2.4 | **Andrei Popescu contamination** | Test probe injected fake user persona → overwrote `identity.md`, created false `profile.md` + `preferences/andrei popescu/` (archive_004:108) | `find` = absent; store clean | None — cleaned. **Design risk remains:** OpenViking has no test/prod separation; any input becomes durable truth. Namespace writes before building Qubit Office (multi-bot). | ✅ (risk noted) |
| 2.5 | **Nested-GET 404 for Obsidian notes** | Root cause: `qubit_asgi.py:145` regex rendered `/notes/{name:path}` as `[^/]+`. Fix added: GET route `.+` + `h_plugin_obsidian_note_get` (`qubit_compat.py:9504`, `:16819`) | Route rows present in `qubit_compat.py` | Fix is in the tree but was awaiting a **restart** (archive_005:15). Service last started 08:48; confirm live: `curl /api/plugins/obsidian-memory/notes/<nested/path>`. | 🟡 |
| 2.6 | **`POST /refresh` bridge (Obsidian read staleness)** | Root cause: `_vault_caches` (line 139) populated once, cleared only by `invalidate_cache()`; `/refresh` was not bridged → read API served stale data | Bridge now present: `qubit_compat.py:9504` + `:16803` `h_plugin_obsidian_refresh`, proxy `:16754` | This was the **Qubit Office blocker** (write a note, other bots can't read it). Confirm live with a real write→refresh→read round-trip. | 🟡 |
| 2.7 | **QueueManager drain timeout** | `[QueueManager] Drain timeout for SessionC…` — commit queue not draining cleanly (archive_003:37) | Log line; not re-investigated | Low priority — extractions now land async (~1 min). Watch if extraction latency regresses. | 🟡 |

### P2 — Sidebar / IA (Decisions in force)

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 2.8 | **Do NOT delete the pages** | Verified NOT to be useless across 3 rounds. `/qubit-talk` = voice console; `/webchat` = target of `/webchat` slash command + 3 voice commands (`voice_commands.py:8780`); 5 OpenViking pages = only UI for the local context engine; `/computer` = 975-line impl, 4 endpoints all 200 | archive_002:54,60,69; archive_007:57 | **Leave them.** If revisiting, the live risk is the voice-command + chat-route-contract tests, not the pages. | ✅ (ruled) |
| 2.9 | **Grouping: nothing deleted, only renamed/grouped** | `Talk`→"Voice & Speech", `Qubit Memory Backends`→"Memory Providers", dropped "Qubit" from WebChat; Tasks 5-flat → 1 parent + 4 children | `App.tsx:398,407`; archive_002:101 | Verify in browser (see 1.1). | 🟡 |
| 2.10 | **`/qubit-chat` kept for voice** | Owner ruling: "leave /qubit-chat cos we need that for voice" (archive_006:146) | `App.tsx` | None. | ✅ (ruled) |

### P3 — Tooling & hygiene

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 3.1 | **graphify dropped** | Verdict: redundant with graft, needs an LLM, not in AGENTS.md, no MCP server, was eating ~1.65 GB | `graphify-out*` dirs **absent** from repo root; `.graphifyignore` untracked (new) | Confirm the CLI uninstall decision (`uv tool uninstall graphifyy`) if disk is tight. Don't rebuild. | ✅ (dirs gone) |
| 3.2 | **graft staleness** | `graft/INDEX.md` mtime 2026-10-12 08:10; `graft/` is 944 MB. Prior `graft build` was killed/restarted; `graft check` hung past 5 min twice | `graft/INDEX.md`; archive_006:99,118 | Run `graft build` to completion after the uncommitted changes land; investigate whether `graft` can **scope** to `voicebridge/` + `frontend/` (full scan of 28,904 files is why check/build hang). | 🟡 |
| 3.3 | **REA MCP registered** | `runtime/config.yaml:1067` `rea:` → `command: rea, args: [--mcp]`; 170 tools verified; parses JS/.NET without Ghidra | `runtime/config.yaml:1067-1070` | Registered by hand (sanctioned writer). Needs a **restart** to load into the running process (archive_006:206). | 🟡 |
| 3.4 | **69 "impeccable" skill files in .claude/.cursor/.gemini/.github/.kiro** | An `impeccable` bump 0.1.11→0.1.14 with a 20 MB binary appeared in the tree; flagged "don't let it ride on our commit" | archive_003:127 | Decide: separate commit or exclude. Check `git status` at commit time. | 🔴 |
| 3.5 | **`rea doctor` healthy:false** | Only the native-engine checks fail (hopper/ghidra/ida missing). `node` ✅ `host` ✅ | archive_006:181 | Install Ghidra only if native binary RE is needed; JS/Electron path already works. | ✅ (understood) |
| 3.6 | **Disk reclamation** | Freed ~6.6 GB (uv/npm/pip caches, qubit scratch, graphify bloat). Now `/` 118 G free, 72% used | `df -h` | **Still reclaimable, needs owner call:** ~3.9 GB of `qubit-aio-*`/fix branches (`restore/qubit-aio-full` has 1 uncommitted file); `.config/Code` 6.8 G; `.cache/qubit-pins` 3.6 G; `.wine-flstudio` 3.4 G (owner said delete; **still present**). Check each for unmerged work before deleting. | 🔴 |
| 3.7 | **AION2 = 82 GB of `.steam`** | Explained the "60 GB vanished" — the game install. Not a leak | archive_006:130 | None. | ✅ |
| 3.8 | **Rotate the sudo password** | Agent noted the owner's sudo password is in the session transcript (archive_003:127) | transcript | **Security follow-up — not yet confirmed done.** Rotate it. | 🔴 |

---

## 2. The "14 tests" — actual completion status

Owner sent the tests in three batches (archive_005:10 = 6 core + 3 enhancement;
archive_005:42 = 7 upgrade). The owner counted **"14 tests"** (archive_005:68).
Final tally as reported in **archive_005:71** and **:73**, cross-checked on disk:

**Batch A — 6 core stress tests**
| Test | Verdict | Evidence |
|------|---------|----------|
| 1 Recursive vault linking | PASS | `AI_Brain/Master_Brain_Map.md` (6,713 B) exists |
| 2 Context compression (<30-word L0) | PASS | 27 words, counted from disk |
| 3 Bi-directional schema sync | PASS (test) / **premise false** | inodes differ, no symlink — proven |
| 4 Deep hierarchy blind-query | PASS | `ALPHA-OMECRON-9` returned rank 1, score 0.720 — **but anchor deleted after** (not re-runnable) |
| 5 Ubuntu hardware ingest | PASS | `AI_Brain/Ubuntu_Hardware_Profile.md` (3,187 B) exists |
| 6 Memory sanity cleanup | PASS (0 deletions) | `AI_Brain/Memory_Sanity_Report.md` (2,787 B) exists |

**Batch B — 3 enhancements**
| Item | Verdict |
|------|---------|
| Enh 1 Autonomous watcher | PASS at the time — **but `~/vault_watcher.py` was deleted; not running now** |
| Enh 2 Dataview generator | PASS (block written) — **rendering unverified** (needs Obsidian Dataview plugin) |
| Enh 3 Self-evolution manifesto | PASS — `AI_Brain/Agent_Optimization_Manifesto.md` (5,837 B) exists |

**Batch C — 7 upgrades**
| Test | Verdict |
|------|---------|
| 1 Auto-pruning context optimizer | **NO-OP** — nothing was 72h stale (correct, but not a demonstration) |
| 2 Predictive task caching | PASS — `ov find` returned correct note (0.803); `Cache_*.md` files exist |
| 3 Local code sandbox | PASS — `AI_Brain/Draft_Script_Specs.md` + validated script |
| 4 Git-backed versioning | PASS — vault repo exists (`de700d2 agent(sync):…`), but only 1 file committed |
| 5 Chronos cron injection | PASS — `AI_Brain/System_Cron_Registry.md` (5,528 B) exists |
| 6 Cross-note conflict resolver | PASS — `AI_Brain/System_Conflict_Report.md` (8,786 B) exists |
| 7 Visual metric canvas | PASS — `AI_Brain/Live_System_Canvas.md` (4,946 B) exists |

**Truly abandoned / never ran (the honest gaps):**
- **Test 2 "Semantic Needle in a Haystack" (limbic)** — ❌ **NEVER RAN.** Required
  the `limbic` library, which is broken on this machine (964-byte tokenizer vs
  its 1024 floor). An agent substituted the plugin list endpoint — *not the test
  as written*. Owner accepted OpenViking covers the capability (archive_005:73).
- **Test 6 "Travel Planner"** — ❌ **NEVER RAN** in that batch (owner's message
  was truncated before the instructions). **Later DONE** in archive_006:12 —
  `AI_Brain/Travel/` has 6 files incl. `Rome_Trip_October_2026.md` (5,867 B).
  ⚠️ That run **edited `Point-A.md`** (md5 `710299f5…` → `308b5461…`); revert =
  delete lines 21–23 if unwanted.

**Sidebar / OpenViking items — completed vs abandoned:**
- **Completed:** OpenViking VLM fix, extraction pipeline, `ov` CLI CRUD, Obsidian
  **writes**, `custom_templates_dir` invention guardrail, studio backend routes,
  `/refresh` bridge (code), REA registration, graphify removal, save ~6.6 GB.
- **Abandoned / still open:** browser-render verification of the sidebar + studio
  page; the limbic Test 2; Obsidian read-staleness **live** confirmation; the
  3.9 GB branch cleanup decision; sudo-password rotation; and — above all — the
  **uncommitted working tree**.

---

## 3. Why this recurred, and the mechanism to stop it

**Root cause of the amnesia.** Each archive ends with a "Still open / needs you"
list (archive_002:161, 003:127, 004:108, 005:73, 006:206, 007:57) written into
the *chat transcript* — not into a file. The next agent, starting fresh, never
reads the prior transcript. So the same "needs a restart" / "still pending"
items re-appeared for days, and the owner saw "work stopped" as breakage.

**Smallest durable fix (this file).** `docs/OPEN_THREADS.md` is now the ledger.
The convention: **at the end of every turn that leaves anything incomplete,
append/update the relevant row here** — one line, with `where it got to`,
`evidence`, and `next action`. Cheap, greppable, version-controlled, and it
survives compaction because it is on disk.

**Agent rule (recommend adding to `AGENTS.md` — NOT yet applied; the file is
write-protected and the edit needs owner approval):**
> Before starting work, read `docs/OPEN_THREADS.md`. When you stop — for any
> reason: iteration cap, compaction, interruption — update it with what is still
> open and the exact next action. A turn that leaves work incomplete and does
> not update this file is an incomplete turn.

**Enforcement hook (cheap, optional):** the `auto-push repos` cron (23:30) could
append a diff-stat line + `git status --short` to a `docs/OPEN_THREADS.log` so
even an abrupt kill leaves a breadcrumb of the working tree's dirty state.
Not implemented here — recommend only if the agent rule alone proves
insufficient.

---

## 4. Office game frontend programme — session 2026-10-14

Working from `docs/game-office/frontend-game/README.md` (the audited baseline + rebuild plan).
Owner directive: *"apply and start"*, then *"verify and fix and continue"*.

| # | Item | Where it got to | Evidence | Next action | State |
|---|------|-----------------|----------|-------------|-------|
| 4.1 | **Frontend amendment applied** | Appended to the LIVING log `05-AMENDMENT-LOG.md` (§ Frontend amendment): C4 reaffirmed, C7 clarified, viewpoint-vs-relocation split, blanket source-string bans superseded, execution partition superseded, plus the T1–T6 test-change list. No C-rule or frozen contract edited in place | `docs/game-office/05-AMENDMENT-LOG.md` § Frontend amendment | None — done as the plan's F00 required | ✅ |
| 4.2 | **`docs/STATUS.md` does not exist** | Repo-context and several authority docs reference it; `test -e docs/STATUS.md` = **absent**, not merely unread. No substitute invented; resolving authorities named instead | `frontend-game/README.md` § Authority (updated 2026-10-14) | Owner: name the governing authority, or restore `docs/STATUS.md` | 🔴 |
| 4.3 | **F01 baseline + fixture done** | 9 office files archived with SHA-256 (`evidence/baseline-archive/`); `tower-recording.json` copied byte-identically (sha `fbb9a00c…`) with `PROVENANCE.md`; 606 sprite assets + the 7-module load order recorded | `evidence/F01-BASELINE-INVENTORY.md`, `fixtures/PROVENANCE.md` | None | ✅ |
| 4.4 | **F02 done and VERIFIED LIVE** | Three defects fixed in `qubit-office.js`: (1) listeners never bound — effect read `canvasRef.current` at mount while the loading branch renders no canvas; (2) Fit/Reset no-ops — `fitRef` overwritten by the live camera every redraw, and `fit`/`reset` set `_auto` while `redraw()` read `_autoZoom`; (3) inspector went stale — `setPick` stored the payload object | **Live browser probe after a bridge restart**: canvas listeners `[]` → 6; `wheel_changed_pixels` false → **true**; `drag_changed_pixels` false → **true**; panel age advanced `165547s` → `165548s` across a poll | None | ✅ |
| 4.5 | **F03 done** | `opts.state` threaded into `drawAgentSprite`, so the atlas resolves all twelve poses instead of falling back to walking/idle (`sprites.js:938-944` documents the seam) | `tests/test_office_interaction_lifecycle.py`; sprite + visual suites green | Atlas pose IDs not independently re-derived by the live probe — stated as the one F03 claim not pixel-proven | 🟡 |
| 4.6 | **F04 done — engine CHOSEN: Phaser 4.2.1** | Owner ruling *"best one with most features and newest and most capable to work with ai"* → `phaser@4.2.1` pinned exactly. MIT (Richard Davey / Phaser Studio), source unmodified, no Phaser code copied in. `PhaserSceneHost.ts` implements `SceneHost` beside the retained `CanvasSceneHost` fallback (one-argument swap). **Live browser proof**: Phaser 4.2.1 boots in Chrome with **zero errors**; `textures.createCanvas` / `getContext` / `refresh` all present and a written pixel reads back; canvas count 1 → 0 after `destroy(true)`. **Cost: 7.29 MB raw / 1.25 MB gzip** — ~500× the rest of the plugin; NOT promoted to the served entry, held by a test | `tests/test_office_game_packaging.py` (20 passed); `evidence/F04-ENGINE-DECISION.md`; decision recorded in `frontend-game/10-DECISIONS.md` | **Owner decision:** whether to promote `qubit-office.game.js` as the served entry given 1.25 MB gzip, or custom-build/lazy-load Phaser. Also: add Phaser to `THIRD_PARTY_NOTICES.md` before publication | ✅ (decision + host) / 🔴 (promotion) |
| 4.7 | **Tests added, full game suite green** | `test_office_interaction_lifecycle.py` (18), `test_office_game_packaging.py` (12), `test_office_game_adapter.py` (15), `test_office_game_navigation.py` (16); T2 C4-over-the-whole-plugin-dir in `test_office_tower_plugin.py`; `test_office_inspector.py` harness adapted (not weakened — both MUTATION tests still pass) | `61 passed` (the 4 new suites) and `273 passed` (12 office suites) | None | ✅ |
| 4.11 | **F05 done** | `game/types.ts` typed from the REAL recorded payload + `game/adapter.ts`: serialized abortable loads, revision fence, stale retention, visibility pause, provenance. Tests caught and I fixed 2 real defects (dispose left `loading` true; a failed read claimed a payload while reporting provenance `none`) | `tests/test_office_game_adapter.py`; fence falsifiable (disabling it fails the out-of-order test) | None | ✅ |
| 4.12 | **F06 done — BOTH halves** | `game/navigation.ts` pure reducer (5 place kinds, portal graph DERIVED from the real floorplan, deep-link encode/decode, capped Back, typed refusals) + `game/input.ts` input-context router (containment chain world ⊂ panel ⊂ modal ⊂ text ⊂ editor; innermost-first). Wired into the shell: `keydown` on the container, routed, applied via `travel`/`back`. **Two real bugs found by tests and fixed**: skipping the `lobby → floor 1` portal made the boardroom unreachable; the panel guard `!active.includes("world")` never fired because `world` is always the base context, so the world walked under an open panel | `tests/test_office_game_navigation.py` (16) + `tests/test_office_game_input_context.py` (16); 3 falsification runs (typing guard → 4 fail; portal skip → 1 fail; deleted binding → 1 fail) | None | ✅ |
| 4.13 | **Wiring test caught being a false positive** | `test_the_built_artifact_actually_wires_the_keyboard_and_navigation` originally asserted `"keydown" in src` — which passes even when only `removeEventListener` remains, i.e. on a completely dead keyboard. Strengthened to match `addEventListener("keydown",` by shape; re-falsified and confirmed it now fails on the dead-binding mutation | mutation: deleting the binding + re-running the strengthened assertion | None | ✅ |
| 4.8 | **Bridge restarted for verification** | `systemctl --user restart qubit-voicebridge.service`; MainPID 153843 → **281483**. Clean start, listening on 0.0.0.0:8890 | `journalctl --user -u qubit-voicebridge.service` | None — user's call was granted | ✅ |
| 4.9 | **The office API is `/agent/api/office/tower`, not `/api/office/tower`** | A bare `curl /api/office/tower` returns 404 and looks like a missing route; the SPA rewrites the `/agent` prefix (`webui.py:2484-2488`). With the session token from `window.__QUBIT_SESSION_TOKEN__`, `/agent/api/office/tower` returns **200** with real data | `webui.py:2484`; live 200 | Worth knowing before anyone debugs a "missing" route again | ✅ |
| 4.10 | **All work UNCOMMITTED** | This session: modified `05-AMENDMENT-LOG.md`, `frontend-game/README.md`, `60-IMPLEMENTATION-PLAN.md`, `OPEN_THREADS.md`, `qubit-office.js`, `qubit-office.bundle.js`, `tests/test_office_{inspector,tower_plugin}.py`; new `docs/game-office/frontend-game/` (+evidence, +fixture provenance), `plugins/qubit-office/game/` (7 files), `frontend/qubit-web/vite.office-game.config.ts`, `tests/test_office_game_{packaging,adapter,navigation}.py`, `tests/test_office_interaction_lifecycle.py` | `git status --short` | Owner: the pre-existing 43-file dirty tree plus these. Not committed — the owner's call (see § 0, still the top of this file) | 🔴 |

**Next in the plan:** **F08 (responsive shell, panels, settings) is PARTIAL** — see its STATUS block in
`60-IMPLEMENTATION-PLAN.md` for what landed and the four items still open (the world not growing to fill
the shell was the largest; it is now fixed and verified live). **F07 is done** (playable slice, 17 tests,
committed `186abf7d4`); **F09 (character roster, profile, dialogue shell)** and **F12 (operational
panels)** are DONE — see § 4.14 below.

### 4.14 — Current state of Qubit Office (re-verified 2026-10-14, evidence-backed)

VERIFIED by running, this session. "VERIFIED" below means the exact command was run and its output is
the evidence; "NOT VERIFIED" means it is claimed or plausible but was not exercised live.

| # | Claim | State | Exact evidence |
|---|-------|-------|----------------|
| 4.14a | **The REAL third-party office app is vendored** — `github.com/marmottajr/habblaud`, MIT, commit `4ccce2537472`, client build output only (server / Claude Code mod / Docker NOT vendored) | **VERIFIED** | `.venv/bin/python scripts/vendor_habblaud.py --check` → `ok: 77 files, **2147631** bytes, commit 4ccce2537472` (exit 0). `PROVENANCE.json` records source URL, commit, MIT licence and per-file sha256. Lives at `frontend/qubit-app/plugins/qubit-office/habblaud/`. **CORRECTED 2026-10-15:** this row previously read `--src /tmp/habblaud-full --check` and `2147125 bytes`; `--check` needs no `--src`, no checkout exists at that path, and the byte total had drifted (the tree is also now English-pinned, hence 2147631 not 2147637). |
| 4.14b | **The vendored app is served and renders** | **VERIFIED (served AND renders)** | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/` → **200**. The body is the real Habblaud `index.html` (pt-BR, `<canvas id="world">`, module `bundle/main-DND_b4_E.js`). Bundle + an asset also return 200. **Pixel-verified** by headless-chrome probe: default `/qubit-office` frame reports `Habblaud Connected 4 rooms 10 agents` with **0 page errors**; an earlier probe of the same page counted **22,776 non-background canvas pixels across 162 tones** (a black/empty canvas would be one tone). |
| 4.14c | **Which implementation is the default on `/qubit-office`** | **VERIFIED — the VENDORED APP** | `game/entry.ts` mounts the vendored app by default; the hand-built shell is opt-in via `?shell=1`. Live probe (headless chrome): plain `/qubit-office` → **1 iframe**, src `/plugin-apps/qubit-office/habblaud/`, frame text `Habblaud Connected 4 rooms 10 agents`; `?shell=1` → **0 iframes, 2 canvases** (the old shell). NOTE: `manifest.json` line 11 still declares `"entry": "qubit-office.game.js"` — that is the plugin entry, and the *entry itself* chooses the app. Both statements are true at different layers; do not read the manifest as "which renderer you get". An earlier revision of this row claimed the hand-built renderer was the default — **that was measured before the inversion landed and is corrected here.** |
| 4.14d | **The default/fallback switch** | **VERIFIED — `?shell=1` opts back into the hand-built shell; `?app=1` is a compat alias that also wins** | `game/entry.ts:81`: `const wantShell = params.has("shell") && !params.has("app")`. **CORRECTED 2026-10-15:** this row previously claimed "the earlier `?app=1` form is gone". It is NOT gone — it is a deliberate backward-compatibility alias for bookmarks from the first Habblaud preview, and it *overrides* `shell` (`?shell=1&app=1` mounts the app). The vendored app is still the unconditional default. `tests/test_office_game_shell.py::test_the_default_renderer_is_the_app_and_the_shell_is_opt_in` now evaluates the real expression under node for all four param cases. |
| 4.14e | **F09 (character roster, profile, dialogue shell) is DONE** | **VERIFIED (tests pass)** | `tests/test_office_game_character.py` — real count in § 4.14g (run together with F12). |
| 4.14f | **F12 (operational panels, boardroom) is DONE** | **VERIFIED (tests pass)** | `tests/test_office_game_operational.py` — real count in § 4.14g. |
| 4.14g | **The office game suite passes** | **VERIFIED 2026-10-15** | `.venv/bin/python -m pytest tests/test_office_game_*.py -q` → **`212 passed in 110.04s`**, exit 0, **0 skipped**. **CORRECTED 2026-10-15:** this row previously read `211 passed`, and at an earlier point `3 failed, 209 passed` — the three failures were stale tests pinning the pre-rewrite contract of `entry.ts`/`office-api.ts` and were reconciled in `d50a56945`. F09+F12 alone: `pytest tests/test_office_game_character.py tests/test_office_game_operational.py -q` → `66 passed`. |
| 4.14h | **The vendored app is the DEFAULT renderer of the Office tab; the F00–F17 packets describe the fallback** | **VERIFIED 2026-10-15** | **CORRECTED:** this row previously called the vendored route a "PARALLEL track" with "no reconciliation document yet". Both parts were stale. The vendored app is the default at `/qubit-office` (see § 4.14c) and the hand-built packet renderer is the opt-in fallback (`?shell=1`). Reconciliation now exists: `docs/OFFICE-DOC-RECONCILIATION.md`, plus `docs/OFFICE-LIVE-VERIFICATION.md`, `docs/BROWSER-PARITY-FINDINGS.md`, `docs/FIREFOX-PARITY-FINDINGS.md`, `docs/OFFICE-ACCEPTANCE-AND-RISK.md`, `docs/VENDOR-REPRODUCIBILITY.md`, `docs/VENDOR-ATOMIC-PUBLISH-FIX.md`. The packets remain the plan of record for the hand-built renderer they describe. |
| 4.14i | **`docs/STATUS.md` exists** | **VERIFIED present 2026-10-15** | `ls -la docs/STATUS.md` → `6172` bytes. **CORRECTED:** this row previously stated the file did not exist and recorded its absence as a labelled unknown. It exists now (as of 2026-10-15 17:18). |
| 4.14j | **Work is COMMITTED; the tree is clean** | **VERIFIED 2026-10-15** | `git status --short \| wc -l` → **0**. **CORRECTED:** this row previously read **49** dirty entries with HEAD at `3d7989e36`. HEAD is now `fba27ca06` (through `c3b005f18` checkpoint → `ead2053b5` clarify/vendor/office rescue → `80f232080` A8 token fix → `baf6093f5` clarify tests → `fba27ca06` vendor preload assertions). |

### 4.15 — Office test toolchain: the `NODE_ENV=production` trap (2026-10-14)

`pytest tests/test_office_game_*.py` errors when `NODE_ENV=production` is set in the shell: `npm ci` /
`npm install` then defaults to `--omit=dev`, installs no devDependencies, and
`frontend/qubit-web/node_modules` loses `typescript` and `vite`. `scripts/build_dashboard.sh` also
`rm -rf`s that dir unless `KEEP_NODE_MODULES=1`. Two symptoms: the office suites used to echo
`N skipped` as a **false green** (fixed — they now error, see below), and the packaging suite failed
with `Cannot find module .../typescript/bin/tsc`. Recovery — set `NODE_ENV=development` first, then:

```
npm install --include=dev --prefix frontend/qubit-web typescript vite   # restore the tree copy
KEEP_NODE_MODULES=1 scripts/build_dashboard.sh                          # rebuild keeping node_modules
export QUBIT_TSC=/path/to/tsc                                          # or point straight at one
```

`tests/_office_toolchain.py` self-heals: when no `tsc` resolves it installs `typescript@5.9.2` into
`~/.local/state/qubit-voicebridge/runtime/cache/scratch/ts-tool` (outside the tree, so a dashboard build
cannot delete it) and the suites **RUN**. With `QUBIT_TSC_SELF_HEAL=0` and no compiler, every office
suite **ERRORS at collection** naming the exact install command — never a skip. Falsified both ways
2026-10-14 (hide `tsc` + `SELF_HEAL=0` → collection error; hide `tsc` + self-heal → `18 passed`).

**Intermittent test failure — ROOT-CAUSED AND FIXED 2026-10-15.** Running
`pytest tests/test_office_vendor_habblaud.py tests/test_office_assets_vendored.py tests/test_office_plugin_app.py -q`
once produced **`5 failed, 81 passed`**; four re-runs produced `86 passed` every time; no root cause was found
at the time. The root cause is now identified and repaired:

`scripts/vendor_habblaud.py::vendor()` **deleted the destination tree and wrote `PROVENANCE.json`
mid-assembly**, so any concurrent reader — including these tests, which hash the tree against that
record — could observe a half-generated directory. **Measured on the old algorithm: 0 of 90,818
concurrent reads were consistent; 19 were inconsistent.** The intermittent first-run failure was a
**correct detection of an inconsistent tree**, not a broken test.

The fix (commit `ca03150e8`) makes publish order the consistency barrier: the record is unlinked
first, every file is written, and the record is written **last** via temp-file + `os.replace`; `LICENSE`
is copied the same way. Full write-up: `docs/VENDOR-ATOMIC-PUBLISH-FIX.md`, including one residual
hole that is **deliberately not closed** (~1 in 600k reads can still catch an in-place file overwrite,
because `copytree` writes non-atomically). Treat the vendored-app hash assertions as **reliable but
still fenced against concurrent re-vendors** — and assert **exit code + file count, never the byte
total**, which drifts legitimately across rebuilds.

---

*Generated 2026-10-12 by mining session `20261010_005652_080bf0` archives
001–007. Re-verify any 🟡/🔴 row against the worktree before acting — status
claims here are as-of this date and the tree is still changing.*

---

## SESSION 2026-10-16 — 5-lane catch-up + rebrand ruling

**State verified on disk 2026-10-16 ~08:40.** Branch `pre-sync-worktree-snapshot`,
HEAD `2808e2a58`, tree clean, **85 ahead / 0 behind `canonical/main`** (a clean superset, not
a divergence). Tagged `session/2026-10-16-catchup`.

### 🔴 THE LOSS RISK: nothing is pushed

`git branch -r --contains HEAD` is empty — every commit below is **local-only**. Remote is
`canonical` → `github.com/cgraph-dev/Qubit-AIO.git`. Push before doing anything else.

### ✅ Landed this session (12 commits)

| Commit | What |
|---|---|
| `080514a2d` | **SUPERVISED_CHILD dual-read**: writers emit `QUBIT_*`, 10 readers accept both. Fixes the `venv_sync.py:308` split-brain. 22 tests; 7 proven red without the change. |
| `3301809e6` | lane-6 report (`REPORT-supervised-marker.md`) |
| `70260712c` | **`docs/REBRAND-SWEEP-PLAN.md`** — the 630-symbol sweep design |
| `63b458a41` | `scripts/upstream_drift_report.py` + pin reconciliation (reads `FROZEN_REV` from one home, fails closed; **did not touch** the `69948c0057` anchor — still 16 occurrences) |
| `73a78da84` | drift report doc |
| `2808e2a58` | `docs/DRIFT-REPORT-2026-10-16.txt` — verified, deterministic |
| `6db59dfeb` | **HEAD defect fix**: branch rows lacked `message_identity()`; ported from `sync-w24` `8efc0dd62`. Verified independently. |
| `6ee2adf0c` | Office memory-receipt fix (concurrent session's work, frozen + attributed) |
| `516a5390a`…`62e7a4518` | `scripts/check_commit_hygiene.py` + 27 tests + report |
| `d2c467e9a`,`1b21b26a1`,`b8cd9c233` | `sync-w24` safe subset (2 of 86) + manifests |

### Verified findings worth not re-deriving

- **Upstream drift = 3,895 commits, but only 298 genuine production-file additions.** 1,114
  we already carry (upstream merely newer); 127 deleted. The commit count overstates work ~5×.
  Re-run: `scripts/upstream_drift_report.py` (~9 min, deterministic).
- **`sync-w24`'s 86 "TAKE" paths: 84 are unsafe.** Proven: all 84 delete 1–67 non-blank HEAD
  lines, and 43 carry Hermes-branded branch-only content. `deferred.json` has per-path detail.
- **`sync-w24` is not a stale dead branch** — it carried a real fix HEAD never took
  (`8efc0dd62`), now landed. Re-read it for content, not only for conflicts.
- **Agent lane `catchup-1` went the INVERSE direction** (~140 `QUBIT_*`→`HERMES_*` in tests,
  treating drift as spec). Parked at `parked/catchup-1-hermes-direction` with a git note.
  Not salvageable under the owner ruling except one `lambda force, **kw` signature fix
  (recorded in the sweep plan).
- **`hermes_cli` test lane hit its iteration cap** at 338 failing (from 535). Unfinished —
  needs re-dispatch. Its orphaned edits are at `/tmp/preserved-ag1-leftovers.patch`.

### 🟡 Open

- **Push the branch.** Highest priority; nothing is backed up.
- **`hermes_cli` tests**: 338 failing remain (`qubit_runtime/tests/hermes_cli/`). Re-dispatch.
- **Rebrand sweep**: Phase 0 (manifest tool) not started. See `docs/REBRAND-SWEEP-PLAN.md`.
- **`check_commit_hygiene.py` reports 13 live violations** — the peer worktree dirt is
  expected; the `stranded_branch_commits` one is the push issue above.
- **Stale stash recovery**: another session's 590-line office WIP was found in `stash@{0}` and
  preserved to `/tmp/preserved-stash0-office-590lines.patch` (sha256 `4a16d9bc…`). **The stash
  stack is shared across worktrees — never bare `git stash pop` in a worktree you did not
  create.** Its owning session should reclaim it.

---

## COVERAGE GAP found 2026-10-16 (during the 8-lane dispatch)

**🔴 Root `tests/` — 904 files — is not covered by any current lane** (all 8 lanes target
`qubit_runtime/tests/*`). It is a real separate tree, not a symlink to `qubit_runtime/tests/`.

It has live failures. Sampled at `9facd1ca9`:

- `tests/agent/test_auxiliary_client.py::TestGetTextAuxiliaryClient::test_returns_none_when_nothing_available`
  fails: the test stubs `_read_nous_auth`, `_resolve_codex_credential_and_base` and
  `_resolve_api_key_provider` all to `None`, yet `get_text_auxiliary_client()` still returns an
  `openai.OpenAI` instance — so a **fourth resolution path** exists that the test does not stub.
  Likely a stale test (a route landed upstream) rather than a code bug, but that must be proven,
  not assumed: check whether the new path is legitimate and the test is behind, or whether the
  path should have been gated on credentials.

**Action for the next session:** dispatch a lane for `tests/` (root) with the same protocol as
lanes 8–11 (measure baseline first, per-class verdicts, isolation check, PROGRESS.md trail).
Also still uncovered: `frontend/` (TS/Vitest) and the `ui-tui` vitest suite.

**Why it was not dispatched immediately:** 8 lanes were already running and loadavg was 19.06
with 22 GB free. Adding a 9th heavy pytest lane risks an OOM-kill that would corrupt a
running lane's run, which costs more than the delay.

---

## 2026-10-16 (later) — restart recovery, a caught regression, and stale lesson

### The regression that validated the sweep plan

A lane renamed one env var to its canonical `QUBIT_*` name and added a dual-read alias
(`anon_auth.GUEST_ONBOARDING_ENV`). It **looked** correct and followed the documented
single-write/dual-read pattern — and **broke 20 tests** (baseline 1 failed → 21 failed).

Cause: it migrated the *reader* while the **writers and the tests still set the old spelling**.
Dual-read is only half a migration. Reverted as `ad2e66acc`.

**Rule for any future env-var rename (all three together, never one):**
readers, every writer (setenv / `.env` line / launcher / systemd unit / launchd plist), AND the
tests. Then report a **before/after failure count** — the harness makes this one run, and it is
what would have caught this in seconds instead of by hand.

### 🔴 HARD RULE: never bare `git stash`/`pop` in this repo

The stash stack is **shared across worktrees** and holds **13 entries from prior sessions**
(`stash@{0}` is a 590-line office WIP, preserved to `/tmp/preserved-stash0-office-590lines.patch`,
sha256 `4a16d9bc…`). A bare `git stash pop` collided with it **twice in one session** and produced
4 merge conflicts in files that were never mine.

To get a baseline without touching the stash: `git checkout HEAD~1 -- <file>`, run, then
`git checkout HEAD -- <file>`. Never `git stash`.

### Verified Phase 0 tooling (branch `agent/catchup-12`)

`scripts/rebrand_sweep.py` — runs, gates CI, honours the pin deny-list. Measured at `ad2e66acc`:

```
symbols=926 (hermes=670 qubit=256)  dual_spelled=91 (undeclared=88)
boundary=432   deny_list=18 files
```

So the plan's original figures (630 / 231 / 152) have **drifted** — recompute, do not quote them.
`--check` exits 1, listing 88 symbols spelling both ways in code with no declared dual-read.
Safety verified: the frozen pin `69948c0057` is still 16 occurrences in `scripts/`, and the branch
modifies none of the 18 deny-listed files.

### Landed during recovery (all pushed to canonical/main)

| Commit | What |
|---|---|
| `2c9bf297f` | **Fourth branch-copy path** carries `message_identity()` — sibling to CLI/workdir/Desktop. Falsified 3-red-without / 3-green-with. |
| `ad2e66acc` | Revert of the `GUEST_ONBOARDING` regression |
| `0b21c3a5f` | Phase 0 sweep tooling (recovered) |
| `7f2a2098c` | sync-triage hunk matrix harness |

Rescued-but-unlanded: a `QUBIT_GUEST_ONBOARDING` change that needs writers+tests moved with it.
---

## 2026-10-17 — Desktop vitest: node-builtin tests are dead in the jsdom project

**🔴 OPEN (documented, not fixed).** `apps/desktop/src/**` tests that import a `node:`
builtin cannot run under the `ui` vitest project, which pins `environment: 'jsdom'`.
Vite externalizes `node:` for browser compatibility and jsdom has no loader for it, so the
suite fails at import with `Error: No such built-in module: node:` — reported as `(0 test)`,
i.e. the file never even collects.

**Scale (measured).** 12 of the 14 `src/**` tests importing a `node:` builtin have no
`@vitest-environment` directive and are in this state:

```
src/app/chat/right-rail/preview-act.test.ts
src/app/chat/right-rail/preview-file-save-scope.test.tsx
src/app/right-sidebar/files/ipc.test.ts
src/components/assistant-ui/thread/system-message.test.tsx
src/components/assistant-ui/tool/inline-edit-persistence.test.tsx
src/components/pane-shell/pane-visibility.test.ts
src/components/ui/__tests__/no-native-title.test.ts
src/lib/speech-text.identifier-corpus.test.ts
src/plugins/hermes-bots/cron-prompt.test.ts
src/plugins/hermes-bots/group-chat-view.inline-code.test.tsx
src/plugins/hermes-bots/group-chat-view.wrap.test.tsx
src/plugins/hermes-bots/relay-deliver-budget.test.ts
src/store/nanostores-batch-guard.test.ts
```

Only `src/hover-variant.test.ts` is fine, because it imports **no app code** — a pure node
test that carries `// @vitest-environment node` and so runs correctly.

**What does NOT work (probed, not assumed).** A minimal reproduction (one file using
`node:child_process` + jsdom) was used to test each candidate; all still failed with
`No such built-in module: node:`:

- `test.server.deps.inline: [/node:/]`  ·  `inline: true`
- `ssr.noExternal: true`  ·  `resolve.conditions: ['node']`
- `test.pool: 'forks'`
- `test.server.deps.external: [/node:/]`
- `resolve.alias` mapping each `node:<m>` to `<m>`
- static **and** dynamic `await import('node:child_process')` inside a test body

So it cannot be fixed by a config knob. The two requirements are mutually exclusive in one
file: a `node` environment gives real builtins but no `window`; `jsdom` gives `window` but no
builtins. `// @vitest-environment node` alone swaps one failure for the other
(`ReferenceError: window is not defined` from the app import graph).

**The real fix (not done).** `cron-prompt.test.ts` needs both: it shells out (`spawnSync`) and
imports `./cron`, whose module graph reaches `src/store/onboarding-gate.ts`, which calls
`loadGate()` at import time and dereferences `window.hermesDesktop`. Hand-stubbing `window` in
the test fails twice over — vitest hoists static imports above any statement in the file, and
the graph then wants `window.addEventListener` and more (it pulls `src/sdk/index.ts`). The
prescribed fix is the one the root AGENTS.md gives for logic trapped in a god-file:
**extract the pure functions** (`routinePrompt`, `normalizedProfileName`, `routineInputError`,
`isLegacyDelegatedRoutine`, plus `shellQuote` and the three literals they use) into a light
`cron-prompt.ts` sibling with no React/SDK import, and point the test at that. `botsText()`
(from `./i18n`, which imports `@qubit/plugin-sdk`) is the coupling to break or pass in.

**Interim state.** `cron.tsx`'s delegation wrapper was rebranded to `qubit -p … chat` and the
test's assertion moved with it (commit `3338de83a`) — the pairing is correct, but the test
cannot execute until the above is done, so it currently proves nothing.

