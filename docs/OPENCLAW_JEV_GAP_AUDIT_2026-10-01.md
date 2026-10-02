# OpenClaw + Jev Gap Audit — 2026-10-01

**Method:** 5 parallel read-only audit lanes (no code changed, no live mutations).
No `M1`/`M2`/`VERIFIED` claimed. STATUS §§1–65 and `docs/DECISIONS.md` still govern.

## 1. The corrected count (replaces "434/434 disposed")

Source: `docs/generated/QUBIT_OPENCLAW_METHOD_INVENTORY.json` (434 rows),
independently recomputed from `methods[]` — identical to the header.

| Class | Count | Meaning |
|---|---|---|
| REAL (`IMPLEMENTED`) | **252** | handler executes the capability via a canonical backend |
| REFUSAL (`SAFE_EQUIVALENT`) | **87** | typed code; upstream action NOT performed (local counterpart at most) |
| OPERATOR_ONLY_EXTERNAL | **75** | terminal once the named prerequisite exists |
| OPERATOR_ONLY_ARCHITECTURE | **20** | **OPEN — not terminal; each needs individual owner approval** |
| OPEN (un-dispositioned) | 0 | — |

> Only **252/434 execute**. "Disposed" applies to 414 at most, never to the 20
> architecture rows. Sibling view: capability ledger
> `docs/generated/QUBIT_GAP_LEDGER.openclaw.json` = 104 rows
> (94 IMPLEMENTED / 2 SAFE_EQUIVALENT / 7 OPERATOR_ONLY / 1 arch-OPEN OC-48).

**Stale-evidence note (not a behavior gap):** every inventory row cites
`voicebridge/gateway/openclaw_handlers/*.py`, which no longer exists — the live
tree is `voicebridge/gateway/qubit_handlers/` (STATUS §36 rename). Handlers
verified present under the new name; evidence strings need a regen, not a fix.

## 2. What OpenClaw actually added (family spot-checks, hermetic proofs)

| Family | Verdict | Evidence |
|---|---|---|
| Session reset/rewind/cleanup | REAL | `sessions_advanced.py:236`, delegates with confirm |
| Agent workspace list | REAL | `agents.py:81`, via `files.inspect` |
| Agent create/update/delete | REFUSAL (by design) | `agents.py:159`, single-agent product decision |
| Worktree list/create | REAL reads; mutating create confirm-gated | `worktrees.py:129,171` |
| Kanban tab ops | REAL | `board.py:59` via `kanban.tab_create` |
| MCP app call | REAL | `mcp_app.py:82` via `plugin_runtime.invoke` |
| Talk realtime / voicewake writes | REFUSAL | `talk_node.py:342`, `misc_surface.py:157` |
| Cron create | REAL | `content.py:70` via `schedules.create_job` |
| Node list / device pairings | REAL reads | `system.py:576`, `:865` |
| Confirm / audit / e-stop | REAL gates | no-confirm → `CONFIRMATION_REQUIRED`; latched e-stop → `EMERGENCY_STOP` |

## 3. Dashboard pages: NOTHING to create

All 19 reference pages exist in Qubit; both plugin tabs (`/kanban`,
`/achievements`→`qubit-achievements`) resolve. Qubit-only pages (4): `/agents`
(read-only roster), `/voice-edit`, `/chat` split (`TerminalChatPage` +
`/qubit-chat`), `/qubit-boards` overlay. 5/5 fantasy-UI spot checks pass
(Agents, Channels incl. WA/TG onboarding, Plugins incl. pinned-index install,
MCP incl. resources/prompts/templates, VoiceEdit — all PORTED backends).
Router-only surfaces (git status, local-models, display WS, chat workspaces)
are correctly embedded, not pages — do not invent pages for them.
**Remaining dashboard work is behavior-inside-pages, not new pages.**

## 4. Qubit terminal (`qubit agent`): NOTHING to port at top level

71 commands on both sides; the 5 reference-only names are aliases
(`gui→desktop`, `learning/memory-graph→journey`), a deprecated hidden `login`,
and the `help` meta-command. Subcommand depth verified (sessions 20, mcp 13,
skills 20, cron 13, gateway 12, portal 5, worktree 4). Refusals live ONLY at
the bare-`qubit` wrapper (`cli_qubit_forward.py::NATIVE_REFUSALS`: serve,
resume, dashboard, uninstall, login, …), each naming a working counterpart —
no `qubit agent` handler is a bare refusal.

## 5. Jev boundary: ABSENT, gate holds

`voicebridge/jev/` does not exist; exactly one `jev` hit in scope
(`voice_edit/brain.py:14`, docstring only); zero in `frontend/`; no config
keys, no TODOs, no imports. Activation stays **BLOCKED on M4** per
`docs/QUBIT_JEV_BRAIN4_ROUTING_PLAN_2026-09-27.md`. Jev-gated when live:
advisory routing/toolset/skill/plugin/mcp selection, caution/clarification
requests, `qubit jev *` CLI, SystemPage routing group, receipt block —
never `/chat` PTY, weights, execution, or e-stop bypass.

## 6. Still-open work (owner-ordered)

**Needs owner approval first (the 20 architecture rows):**
agents.{create,update,delete}, cron.scratch.{get,set}, mentions.{dismiss,list},
skills.proposals.{evaluate,historyScan}, skills.upload.{begin,chunk,commit},
taskSuggestions.{accept,create,dismiss}, tasks.dismiss,
users.{setDisplayName,setRole}, voicewake.{routing.get,set}.

**Genuine gaps (port/verify, no approval needed):** remote nodes + device
runtime beyond status reads; multi-user/account/role behavior; hosted portal +
OAuth/GitHub flows; remote agents / agent-to-agent; realtime talk transport;
server-push/subscriptions; advanced worktree ops; skill upload/proposal/
security workflows; channel config bridge (`msg-platforms.json` → transport
account store); session message-content search; checkpoint pruning; plugin
visibility/cache invalidation; binary download/streaming; revision sync +
clean-install verification; full voice-path E2E (inventory counts resolution,
not execution).

**No action:** new dashboard pages (none missing), top-level CLI ports (none
missing), Jev (gated), legal attribution (must stay).

## 7. Separate-surface build (2026-10-01, owner-directed)

Owner rule implemented: every OpenClaw-integrated feature lives on its OWN
Qubit-branded page, apart from the working Hermes-rebranded surfaces.
Ported from source, additive-only, verified no-regression.

- Builders (disjoint, new files only): `6b483910` (agent-workspace, nodes,
  devices), `e484578b` (session-ops, worktrees, checkpoints), `7ccf205c`
  (board-admin, task-inbox), `ec86578e` (talk, scheduler-media), `489ee293`
  (admin skills-review, mcp-calls, users). 13 pages + 5 api modules + tests.
- Integrator `12eb1c32`: 25 `_r` rows → 1 generic bridge handler
  (`_QUBIT_FAMILY_BRIDGE`, 105 allowlisted paths, unknown → typed 404;
  mutations e-stop→409→execute) + 13 App routes/nav + rebuilt bundle.
- Proof: 589 frontend vitest + 95-file backend sweep green, brand scan 0
  leaks, live reads 200 per family, net-zero 409 on mutations.
- Honest gates kept (403 + counterpart, never fake buttons): device identity,
  journal-checkpoint create/verify, relay/event/prompt/action rows, upload/
  evaluate flows. Two follow-ups recorded: `ROUTE_TITLES` page-header names,
  generated route-ownership artifacts regen at freeze.

## 8. Second-wave family build (2026-10-02, integrator lane 2)

Same owner rule as §7: five more OpenClaw-integrated features live on their
OWN Qubit-branded pages, apart from the working Hermes-rebranded surfaces.
No `M1`/`M2`/`VERIFIED` claimed. No `~/.hermes` touch, `:9119` untouched,
no browser tabs driven, no model slots moved.

- Builders (disjoint, new files only): `CanvasPage` + `ocFamilyCanvasApi`
  (9-row ROUTES manifest), `TasksPage` + `ocFamilyTasksApi` (list/get/cancel/
  retry + `dismiss` REFUSED-NEVER-WIRED), `TaskflowsPage` + `ocFamilyFlowsApi`
  (11 cron-reuse rows + 5 needs-bridge rows), `MemoryBackendsPage` +
  `ocFamilyMemoryApi` (5 rows, all NEEDS-BRIDGE, canonical `via` paths),
  `WebChatPage` + `ocFamilyWebchatApi` (reuse-only, NEEDS_BRIDGE empty).
- Integrator (this lane, single wiring owner): extended `_QUBIT_FAMILY_BRIDGE`
  **+23 rows (108 → 131, 13 → 17 families)** verbatim from the manifests;
  unknown family/action stays a typed 404; mutations e-stop → 409 → execute.
  Webchat adds **0 rows by design** (every capability reuses an existing chat
  endpoint). 5 App routes/nav (`/canvas`, `/qubit-taskflows`, `/qubit-tasks`,
  `/qubit-memory`, `/webchat`, lazy) + 5 `ROUTE_TITLES`
  (Qubit Canvas, Qubit Taskflows, Qubit Tasks, Qubit Memory Backends,
  Qubit WebChat). Bundle rebuilt (`scripts/build_dashboard.sh`, tsc clean).
- Row truth: **canvas 9** — no file host or node-presentation backend is
  registered, so every row answers an honest typed gate (`CANVAS_HOST_UNWIRED`
  reads/writes, `CANVAS_PRESENT_UNWIRED` present/hide/navigate/snapshot;
  mutations gate 403 before any confirm is staged). **tasks 4** — `tasks.list`/
  `tasks.get` onto the shared ledger (`task_ledger.list_tasks`/`show`),
  `tasks.cancel`/`tasks.retry` onto the live run primitives
  (`ActionStore.cancel`/`retry`, chat_transport by session); `dismiss` has no
  row (page renders `TASKS_DISMISS_REFUSAL` verbatim). **flows 5** — `hooks`
  reads the real hook catalog (`hooks.status`); `hook_enable`/`hook_disable`/
  `cancel`/`programs` answer honest gates naming the real surface
  (`/api/cron/jobs`, confirm-gated pause/delete); the 11 cron rows reuse the
  existing `/api/cron/*` routes (no bridge rows). **memory 5** — status-only
  gates (`MEMORY_BRIDGE_UNWIRED`; `provider-set` mirrors the verbatim
  `MEMORY_LOCAL_ONLY` refusal); the live status is served today at the
  canonical `GET /api/memory` (qubit-local, the only provider) — no writer
  invented. **webchat 0** — sessions list/create/history + turn send/stream
  all reuse existing endpoints (verified served, see proof).
- Proof: `tests/test_qubit_family_bridge9u.py` **16 passed** (allowlist +
  staged-then-execute per new family); 5 new page suites + title test **34
  vitest passed**; qubitchat/confirm/key/planner sweep **108 passed, 1
  pre-existing HEAD failure** (`test_web_branch_failure_is_typed` — fails
  identically with all uncommitted dirt stashed; the live Perplexity key
  answers where the test's mocked failure expects a typed refusal —
  environment, not this lane); canonical public-surface scan **0 leaks**;
  added bridge rows carry 0 legacy tokens.
- Live (restarted ONLY `qubit-voicebridge`, pid 402789 → 421844):
  `GET /agent/api/qubit/tasks/list` → real ledger rows (kanban tasks);
  `GET /agent/api/qubit/flows/hooks` → real catalog (3 bundled hooks);
  `GET /agent/api/qubit/canvas/list` → honest `403 CANVAS_HOST_UNWIRED`;
  `GET /agent/api/qubit/memory/status` → honest `403 MEMORY_BRIDGE_UNWIRED`
  (canonical `GET /agent/api/memory` serves the real provider list);
  `GET /agent/api/qubit/chat/sessions` → real sessions (total 120);
  `GET /webchat` shell → 200; `POST .../tasks/cancel` (no confirm) →
  net-zero 409 + `confirmation_id` (nothing executed).
- Left unmapped + why: canvas file host + node presentation (no backend
  exists — follow-up is a confined static host `GET /__qubit__/canvas/*`
  reusing the plugin-asset precedent, then brokered path-confined writes;
  until then the preview frame stays a pure URL-string builder); flows hook
  enable/disable + programs registry (no hooks backend; cron covers real
  scheduling); memory writers beyond qubit-local (local-only policy — the
  gate IS the product); webchat needs nothing (reuse verified live).
   Concurrent lanes' uncommitted dirt (MCP, skills, SessionOps/Profiles/
   Checkpoints/Worktrees rewrites) was read, not touched, and stays
   uncommitted for those lanes.

## 9. Final status — family build + readiness (2026-10-02, docs lane I5)

Docs-only lane: no code changed, no other docs touched, no generated
artifacts touched. Every number below was re-verified by running the count
commands in this session, never copied. No `M1`/`M2`/`VERIFIED` claimed.

- **Family build committed.** Second-wave integrator `8afed7c2` (bridge
  131 rows / 17 families, 5 App routes/nav, rebuilt bundle) and builders
  `3bf2c6d8` (canvas), `54adf37b` (tasks), `f676f0ee` (taskflows),
  `81507d20` (memory-backends), `cd645a7c` (webchat) are all in git log.
  Live totals re-verified: 40 non-test page modules, 39 route-table lines
  in `App.tsx`, 304 `<Button` controls, 150 `api.*` call sites, 131 bridge
  rows over 17 families, 387 `_r(` registrations.
- **Canvas/hooks backends: pages committed, backends in-flight.** The I1
  canvas page commit (`3bf2c6d8`) exists in git log. Backend truth is
  unchanged from §8: no canvas file host or node-presentation backend is
  registered (every canvas row answers an honest typed gate); flows
  `hooks` reads the real hook catalog while hook enable/disable stays an
  honest gate. The confined static-host and hooks-backend follow-ups are
  unbuilt.
- **Turn-time detection: in-flight.** No I2 commit is present in git log.
- **Brand sweep: pending.** No I3 sweep commit is present in git log.
- **20 architecture rows still OPEN pending owner** (listed in §6 —
  `agents.*`, `cron.scratch.*`, `mentions.*`, `skills.proposals.*`,
  `skills.upload.*`, `taskSuggestions.*`, `tasks.dismiss`,
  `users.{setDisplayName,setRole}`, `voicewake.*`). A typed refusal is
  never a working capability; each needs individual owner approval.
- **Jev still gated.** Activation stays BLOCKED on M4 per the Jev routing
  plan; no Jev surface was built or wired by any family lane.
- **Upstream model outages are external, not Qubit defects.** Upstream
  volatility already forced the guard in `8f1c4f74` (model select refuses
  dead upstream models, slot unchanged); slots stay pinned to working
  tiers until the owner makes the pin call below.

**Honest readiness — proven working vs needs the owner.** Proven: 13
first-wave + 5 second-wave family pages on their own Qubit-branded
routes, served verbatim through the 131-row allowlist bridge (live reads
200 per family, mutations net-zero 409 with staged-confirm settle,
unknown family/action typed 404); nothing to create on pages or top-level
CLI; Jev boundary holds. Needs the owner: (1) the `packet7d` ruling —
  its branding case fails identically on pristine HEAD, so it is a
  scope/policy call, not a lane fix; (2) the upstream pin call — which
  model to pin after the muse retirement (deepseek vs local tier); (3)
  the MCP/skills lanes' uncommitted files (visible in `git status`,
  owned by their concurrent lanes — not touched here); (4) Chromium flag
  persistence — CDP needs `--remote-debugging-port=9222` on every browser
  launch (or an explicit `cdp_launch` opt-in); (5) the sudo-gated
  disk/snap items, which no unprivileged lane can close.
