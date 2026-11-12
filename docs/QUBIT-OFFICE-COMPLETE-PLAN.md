# Qubit Office — complete execution plan v11

**Status:** canonical full-scope execution contract. Design only; no phase is `VERIFIED`.
**Owner decisions:** full scope from start; no reduced MVP; reuse the supplied repos/assets; no
renderer-owned business truth; no homegrown reimplementation of an existing engine.
**Rule:** phases are dependency/merge order, not scope cuts. The reference lane is architecture
validation, not a reduced product.

Detailed corrections: `docs/QUBIT-OFFICE-PLAN-AUDIT-V10.md`.
Mandatory gate: `docs/QUBIT-OFFICE-MANDATORY-CHECKLIST.md`.

---

## 1. Execution contract for every task

Every task has:

- `id`, owner, reviewer, predecessor IDs and merge gate.
- Exact input paths and output paths.
- Schema/API/CLI contract and version.
- Acceptance command, expected output, timeout and evidence manifest.
- Negative/failure tests and runbook.
- Required owner approval where applicable.

**Merge rule:** no phase merges to integration or live until its gate command returns exit 0 and its
`manifest.json` exists with `status: "PASS"`. Absence, staleness, waiver without owner sign-off or
false status is `FAIL`.

**Parallel writers:** never write the same file concurrently. One owner per mutable file/resource;
other agents submit patch fragments for independent review.

**Command contract:** commands below are required interfaces to create, not claims that the command
exists yet. Each phase creates its acceptance script at `scripts/acceptance/phase-N.sh` and writes
`evidence/phase-N/manifest.json` with hashes, versions, inputs, outputs, timestamps and exit code.

---

## 2. Current measured state

- Live Qubit shell: 4 rooms / 10 agents / 3 departments.
- 283 agent/seat records linked into Business OS; these are **seed/projection records**, not proof of
  an operational workforce.
- 100-floor template projection: 100 floors, 300 rooms, 3,075 desks; 18 floors / 36 rooms staffed in
  the projection.
- 1 tenant, 1 draft business, 1 War Room session, 0 tasks, 0 jobs, 0 ledger entries, 0 turns.
- Assets: 1,523 files / 404 MB.
- No real agent sessions/workspace/turn execution, real business operations, Character Studio,
  outside world, capability gateway or full release evidence.
- No `main` merge and no `VERIFIED`.

---

## 3. Dependency DAG

```text
P0 contracts -> P1 minimal execution boundary -> P2 reference agent lane
P1 operations -> P2 reference agent lane
P2 reference agent lane -> P3 campus projection -> P4 workforce
P2 reference agent lane -> P5 business workflows
P4 workforce + P5 workflows -> P6 Character Studio/UI/world
P6 -> P7 interactions/FX -> P8 scale/release
```

- Campus templates, assets, business providers, FX and research can be worked on in parallel, but no
  phase is accepted before predecessors.
- Trading is separate readiness: `research -> backtest -> paper/testnet -> owner-readiness gate`.
- Phase 1 proves a vertical reference lane before adding broader adapters.

---

## 4. Phase 0 — contracts, audit, fixtures and execution substrate

**Objective:** make implementation deterministic, reusable and testable.

| ID | Inputs → outputs | Owner | Acceptance commands and evidence |
|---|---|---|---|
| P0-01 | owner decisions -> frozen v11 + status | Integration lead | `python scripts/verify_doc_citations.py` → `evidence/phase-0/contract.json` with v11/hash |
| P0-02 | domain authority map -> `voicebridge/business/contracts/{domain,event,agent,session,run}.schema.json` | Platform + QA | `python scripts/validate_domain_contract.py` → schema-valid + invariant negative cases |
| P0-03 | local packs -> per-file asset summary + manifest | Licensing | `python scripts/office_asset_ledger.py --source "/home/trick/Desktop/UI Office" --out /tmp/qoffice-assets/manifest.json --check` → 1,523 rows, SHA/dimensions/licence/use present |
| P0-04 | repo list -> approved dependency lockfile + reuse record | Licensing + security | Every dependency has URL, SHA, SPDX, notices, permitted use, reviewer/date. CI refuses unapproved deps. Concept-only repos cannot contribute code. |
| P0-05 | threat model + RACI -> threat-test catalog + owner matrix | Security + owner | Tests exist for every OWASP ASI / gate: fixture, command, expected denial, runbook, owner, expiry |
| P0-06 | throwaway home -> synthetic tenant + fake provider/tool transports | QA | `python scripts/stage_replay.py --fixture synthetic-department --no-network` → clean restart, deterministic replay |
| P0-07 | template fixtures -> `evidence/phase-0/campus.json` | QA + integration | `python scripts/generate_campus_fixture.py --check` = floors 100, wings 5, rooms 300, desks 3075, staffed agents 0; template != production |
| P0-08 | identical render fixture -> Phaser vs Pixi benchmark | Frontend | `python scripts/office_benchmark_renderer.py` outputs frame/heap/atlas/path metrics; selected renderer is behind projection interface |
| P0-09 | existing SQLite control plane -> run/task/lease/checkpoint/outbox prototype | Platform | `python -m pytest -q tests/durable_substrate` → crash/retry/unknown/revoke/restore proof; Temporal explicitly deferred until measured HA need |

**Phase gate:** all outputs exist and pass their commands. No Phase 1 external work without P0-02,
P0-05 and P0-06 accepted.

---

## 5. Phase 1 — minimal trustworthy execution boundary

**Objective:** one invocation contract that a lane can use without duplicating an execution engine.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P1-01 | owner/tenant/business/entity records + local owner auth | Platform + security | identity, RBAC+ABAC, deny-by-default, expiry, step-up, revocation tests; cross-tenant denied |
| P1-02 | `EventEnvelope` contract -> append-only event log + outbox + projection checkpoints | Platform + QA | aggregate order, correlation/causation, tenant keys, payload hashes, retention/tombstone, rebuild tests |
| P1-03 | policy compiler + capability catalog -> ToolGateway invocation contract | Platform + security | per-run capability snapshot; deny-by-default; allowed read capability executes; unknown tool rejected |
| P1-04 | run/task/workspace/checkpoint/lease/outbox records | Platform + QA | crash after effect, no duplicate; unknown reconciles; revoke interrupts; single writer enforced |
| P1-05 | A2A/message schema -> message fabric | Platform + QA | owner↔agent + agent↔agent, order/replay, spoof/duplicate/delivery/ack/retention tests |
| P1-06 | approval workflow with exact normalized intent -> approval rows | Security + owner | exact payload/credential/params/expiry/policy bound; A-approved/B-execution denied; hidden subaction denied; postcondition validated |
| P1-07 | secrets manager + sandbox profile + filesystem jail + deny egress | Security | no credentials in prompt/log/memory/workspace; host-socket/metadata/escape/SSRF tests fail closed |
| P1-08 | OpenTelemetry GenAI instrumentation -> run spans + metrics + eval events + sampling | Operations | one trace covers request/run/model/tool/approval/receipt/reconciliation; content capture OFF; no PII |
| P1-09 | migration framework + backup/restore + RPO/RTO | Operations + QA | compatibility adapters; encrypted restore, tenant-restore and replay verification pass |
| P1-10 | **Reference lane**: owner → session → scoped context → read tool → approval-gated external write → artifact/receipt → checkpoint → restart | Integration + QA | `scripts/acceptance/phase-1.sh exit 0`; fake read, approval-gated fake provider write, trace/evidence all present; no live external side effect. If this fails, no other phase merges. |

**Phase gate:** P1-10 PASS proves gateway/run/approval/message/memory/receipt/rebuild/restart.
P2 adds adapters behind the same invocation contract; no second gateway/run engine.

---

## 6. Phase 2 — agent runtime, sessions, memory and handoffs

**Objective:** every agent has a real recoverable lane using the same invocation contract.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P2-01 | Agent Registry + AgentProfile selectors: permissions/skills/MCP/server/tool/model/budget/memory/secrets/schedule/appearance | Platform + security | effective grants/diffs; unknown/denied scope refused; skills cannot grant capabilities; MCP server/tool allowlist+schema hash; pinned model route/fallback |
| P2-02 | sessions/workspaces/leases/checkpoints/handoffs: durable session, bounded run, durable task, task-pinned workspace | Platform + QA | stop/resume/archive/recover; lease loss stops execution; workspace one-writer; checkpoint includes receipts/workspace/policy |
| P2-03 | framework-neutral agent adapter + dynamic capability catalog | Platform + security | model/provider/tool/skills/connector versions/health/version pinned; no hardcoded research provider; availability/unsupported states typed |
| P2-04 | memory adapter: event log authoritative; Obsidian projection; OpenViking scoped adapter | Memory + privacy + QA | scope ACL, provenance/confidence/expiry, quarantine/poisoning, cross-scope denied, deletion/export/rebuild tests |
| P2-05 | A2A message/handoff contract: actor/principal/beneficiary/delegator/capability subset/expiry | Platform + security | spoof/replay/over-delegation denied; recipient accepts explicit handoff; source read-only; no privilege inheritance |
| P2-06 | actual agent turn: prompt → model → tool → approval → artifact → provider receipt → checkpoint | Agent runtime + QA | one low-risk real turn with fake provider records success; denial leaves no turn/usage/ledger; unknown does not imply completed |
| P2-07 | effective-state UI: grants/model/memory/leases/budget/approvals/audit + independent verifier | Frontend + QA | inherited-vs-explicit grants, pending leases, revoked capabilities, retry/handoff caps, no self-verification; agents cannot edit checks/policy |

**Phase gate:** one real recoverable agent turn + typed handoff with artifacts + ACL/Poisoning/revocation
proof. This is not an MVP cut; it validates the runtime before scaling.

---

## 7. Phase 3 — full campus data, maps, renderer and shell

**Objective:** all departments/floors/rooms/desks/business areas exist in validated data and can be
rendered without inventing operational agents.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P3-01 | all department templates + Trading Department -> all floor/room/cell/desk records | World + integration | generate full campus data; IDs unique; 100 floors, 5 wings, 300 rooms, 3,075 desks; Trading room/manifest present; seed ≠ production |
| P3-02 | archetypes/capability manifests -> room workflow/role/tool/artifact/queue/KPI/memory/privacy/approval | World + business + QA | every room maps to at least one real workflow; absent capability manifest is a typed error; no decorative room pretending work |
| P3-03 | map schema + Tiled candidate import/export -> map versions, portals, connectivity | World + QA | IDs, coordinates, doors, paths, egress, capacity and memory-scope checks; invalid map rejected; transactional publish/undo |
| P3-04 | benchmark-selected 2D renderer: camera floor, chunks, atlas, LRU, dirty rendering, context-loss fallback | Frontend + QA | one active floor renders; no all-floor draw; context-loss recovery; dirty-render; FPS/heap/device budgets pass |
| P3-05 | command-center shell: global search, roster, floor/agent/task filters, notification inbox, drawers, semantic DOM | Frontend + accessibility | keyboard-only floor/agent/task/artifact navigation; focus visibility; reduced motion; no color-only state; mirror semantics |
| P3-06 | outside-world data: lobby/elevators/campus exterior/chunks | World + QA | exterior data is chunk-validated; only active zones animate; lobby connects to elevators; no outside resource unbounded loading |
| P3-07 | Character Studio shell: canonical CharacterProfile with Habblaud state + LimeZu character/portrait projections | Frontend + art + QA | one save authority; versioned revisions and rollback; paid-art guard; no second editor or raw paid asset in public bundle |

**Phase gate:** full valid data, selected-floor renderer, accessible search/map shell and character
profile shell. Do not create animated/working departments until a real agent lane exists.

---

## 8. Phase 4 — full agent workforce and collaboration

**Objective:** all agents can work, chat, remember, handoff and stop without losing work.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P4-01 | all agent sessions/workspaces and role/access policies | Runtime + QA | each agent has session/workspace/lease/memory scope; paused/revoked/archived never occupies a working desk |
| P4-02 | owner↔agent + room/agent-to-agent threads | Runtime + QA | persistent delivery order, threading, duplicate detection, offline replay, ACL/recipient filtering and mention authorization |
| P4-03 | bounded delegation/work-queue leases/worktrees/artifacts | Runtime + QA | max depth/handoffs/retries/waves; no-progress detection; lease loss stops calls; only one mutable owner |
| P4-04 | full capability usage via gateway; model/tool/MCP/terminal/browser/files/messages/memory/connectors | Runtime + security | per-call authorization/egress/budget/approval checks; denied tool cannot be reached through a model or prompt |
| P4-05 | department + office Obsidian projections + OpenViking scopes | Memory + privacy + QA | department MOCs, stable artifact/backlinks, shared MOC crosslinks, ACL-preserving semantic retrieval, rebuild delete/export works |
| P4-06 | behavior baseline, anomaly/quarantine, pause/stop/revoke/archive, out-of-band kill switch | Security + operations | kill switch stops scheduler/gateway/leases/egress; provider cancellation or credential revocation tested; operator path is independent |
| P4-07 | replayable operator UI + causal timeline + cost/evidence/unknown | Frontend + operations | every run status derives from durable events; simulated replay defaults to stub effects, no real calls; pending/unknown never rendered completed |

**Phase gate:** multi-agent collaboration with no shared-state corruption, no lost restart work,
capped handoff/retry, real kill switch and no fake progress.

---

## 9. Phase 5 — business operations and systems of record

**Objective:** real businesses with approvals, receipts, reconciliation and no fabricated truth.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P5-01 | Etsy workflow: niche → licensed digital asset → draft → approval → official OAuth sandbox publish → receipt → order/download/support/refund → payout → ledger/reconciliation | Business + security + QA | exact approval payload; sandbox receipt; unknown-state reconciliation; digital version/entitlement; no real publish during acceptance |
| P5-02 | Trading readiness: market data + strategy registry + deterministic backtest + paper broker + portfolio ledger + risk/kill-switch | Trading + compliance + QA | only paper/testnet initially; fees/slippage/out-of-sample; venue health, max notional/leverage/concentration, drawdown, risk-veto, kill-switch; no withdrawal rights |
| P5-03 | CRM/contacts/leads/consent/pipeline + support/tickets/SLA | Business + privacy + QA | dedupe/identity, consent/suppression, pipeline aging, assignment leases, SLA/handoff, internal-note vs customer message separation |
| P5-04 | marketing/social campaigns/drafts/approvals/publication receipts | Business + QA | draft-vs-published, brand/legal checks, platform policy/rate limits, exactly published payload/venue/time, no fake engagement/spam |
| P5-05 | finance/accounting/payments/ledger/reconciliation | Finance + QA | double-entry, provider idempotency, unknown-outcome resolution, refund/chargeback support; narrative never equals financial truth |
| P5-06 | contracts/obligations/renewals + jurisdiction/tax rules | Legal + QA | versions/clauses/dates/acceptance, approval-for-signing, obligation/renewal workflow, effective-date/personal-vs-entity books |
| P5-07 | HR/procurement/analytics + agent lifecycle | Business + QA | join/move/leave/revoke, role/skill/access/training, vendor approval/spend checks, KPI formula/source/freshness/target |
| P5-08 | multi-entity-ready books + business factory | Business + security + QA | owner/tenant/entity/brand separation; personal first; cloning cannot inherit authority, budget, bank or credentials implicitly |

**Phase gate:** every committed business action returns a system-of-record row or provider receipt.
All external actions are typed, approval-gated and reconciled. Money/legal/publish/contact/
irreversible require human authorization. Trading readiness is still not live capital.

---

## 10. Phase 6 — Character Studio, styles, outside world and panels

**Objective:** full visual customization over real working systems.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P6-01 | Character Studio merge: Habblaud state/editor + LimeZu Character Generator 2.0 + Portrait 1.5 → one CharacterProfile | Frontend + art + QA | body/eyes/hair/outfit/skin counts valid; preview world+portrait; randomize/lock/undo/presets; version/rollback; paid-art guard |
| P6-02 | room styles/archetypes/furniture/palettes/light/signage/seeds + department/room builder | World + art + QA | draft/version/preview/undo/approve/rollback; path/capacity/egress/collision/assets checks; unique style per department |
| P6-03 | outside world/campus exterior/lobby/elevators/weather/day-night | Frontend + QA | exterior chunks active-zone only; no unbounded animation; environment event mapping passes |
| P6-04 | full panel inventory: agents, sessions, chat, terminal, deliveries, timeline, War Room, Memory, Approvals, Ops, CRM, marketing, Etsy, Trading, models/MCP/plugins/skills, budgets, audit, settings | Frontend + QA | every panel data authority/permissions/empty/error/retry/real action or typed refuse; no fabricated state |
| P6-05 | WCAG 2.2 AA + device variants: keyboard/focus/contrast/target/zoom/reduced motion/screen reader/touch/text mirror | Accessibility + QA | automated axe + manual keyboard/screen-reader/high-zoom/muted/reduced-motion/touch review; canvas no trap |
| P6-06 | licence-aware asset build + local-first sync/conflict/export/exit | Build + licensing + QA | asset provenance required; paid-art leak scan bundle/cache/screenshots/backups/exports; conflicts reviewed before merge; exit/export works |

**Phase gate:** character roundtrip, room-style builder, outside-world projection, complete panel
authority and accessibility/license compliance. No renderer invents business truth.

---

## 11. Phase 7 — Interactions, FX and social simulation

**Objective:** a lively world with bounded consequences and no phantom outcomes.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P7-01 | needs/traits/relationships/action catalog | Simulation + QA | action preconditions/cooldowns/room props/relationship/effects; no random business fact/claim |
| P7-02 | consequence engine: stress/health/morale/reputation/relationships/HR/legal/career | Simulation + compliance + QA | authoritative HR/legal/business events are separated from simulation-only effects; approvals for sensitive consequences |
| P7-03 | mature policy: romance/substances/fighting/conflict | Simulation + compliance + QA | mature content off by default; consent/age/privacy/HR checks; non-graphic private encounter only; no real-world drug how-to |
| P7-04 | event-to-FX schema with replay/cancel/fallback/reduced motion | Frontend + QA | event id/animation/seed/priority/cancel/replay tests; all nonessential effects disableable; reduced-motion preserves critical cues |
| P7-05 | outside life, weather, seasons and social graph projection | Frontend + QA | source events drive effects; no fake activity/no activity from empty events; social memory records scoped ACL |
| P7-06 | simulation audit and replay | QA + security | deterministic replay of interactions, no fabricated agent work or external side effects, incident record retained |

**Phase gate:** simulation changes declared state only; no business truth is invented by animation or
mature actions.

---

## 12. Phase 8 — Scale, security, recovery and release

**Objective:** independent verification under scale and failure.

| ID | Inputs → outputs | Owner | Acceptance / negative tests |
|---|---|---|---|
| P8-01 | scale fixture: 100 floors/3,075 desks/1,000 agents -> load/device/frame/heap/event/pathfinding reports | QA + frontend + operations | all load/device tests and device tiers pass budgets: P95 frames 16.7/33.3 ms, active ≤40, avatars ≤80, heap caps, event caps |
| P8-02 | OWASP Agentic Top 10 + MCP OAuth/schema/audience/Poisoning/Sandbox/Rogue/Trust tests | Security + QA | all required attack cases blocked; token audience/PKCE/schema/multi-server/replay/egress/tool-chain tests pass |
| P8-03 | backups/restore/DR/incident/kill-switch/drills + deployment rings | Operations + QA | encrypted immutable backup restored, index/workflow/audit replay; RPO/RTO evidence; kill switch terminates and revokes; provider cancel works |
| P8-04 | OpenTelemetry GenAI traces/evals + accessibility + performance/device context-loss/recovery matrices | QA + security + accessibility | no prompt/PII leakage, eval regression blocked, keyboard/screen-reader/reduced-motion/high-zoom/muted checks pass, context-loss recovery works |
| P8-05 | final frozen commit, dependency lockfile, all phase manifests and owner release roster -> release evidence manifest + decision/waiver set | Integration + owner + QA + security | one final matrix passes on frozen SHA; all applicable rows PASS, no critical/high finding, restore/reconciliation passes, release decision signed; waivers require owner-signed risk/expiry |

**Phase gate:** one final frozen-SHA matrix with PASS evidence. No `VERIFIED` claim without this and
owner release sign-off.

---

## 13. Parallel-safe implementation lanes

Work can proceed in parallel **only after their predecessor contracts are accepted**:

| Lane | Start dependency | Cannot merge before |
|---|---|---|
| Contracts/domain/templates | now | P0-02 acceptance |
| Assets/licences/repo audit | now | P0-03/P0-04 acceptance |
| Staging/replay/scale fixture/renderer benchmark | after P0-02 | P0-06–P0-08 acceptance |
| Identity/event/policy/gateway | after P0-02 | P1-01–P1-04 acceptance |
| Message/approval/secret/sandbox/observability/backups | after P1-01 | P1-05–P1-09 acceptance |
| Agent runtime/registry/sessions/memory/A2A | after P1-04 and P1-10 | P2-01–P2-07 acceptance |
| Campus/map/renderer/panels/Character shell | after P1-10 | P3-01–P3-07 acceptance |
| Workforce/delegation/chat | after P2-07 | P4-01–P4-07 acceptance |
| Etsy/CRM/Finance/Marketing/HR/Legal/Analytics/Trading-readiness | after P2-07 | P5-01–P5-08 acceptance |
| Character Studio/outside world/builders/styles/panels | after P4/P5 acceptance | P6-01–P6-06 acceptance |
| Interactions/FX/mature actions | after P4/P6 | P7-01–P7-06 acceptance |
| Scale/release | after all earlier gates | P8-01–P8-05 acceptance |

No agent may edit its own tests, gates, licences, policy or evidence manifests.

---

## 14. RACI and approvals

| Owner | Responsible |
|---|---|
| Owner (human) | Approves money, legal, irreversible actions, trading readiness, release, scope |
| Integration lead | Owns merges, dependency DAG and live deployment |
| Platform owner | Owns identity, runs, gateway, event log, contracts and storage |
| Security reviewer | OWASP, sandbox, secrets, MCP, identity, approvals, kill switch |
| Licensing reviewer | Asset/repo commits, licences, notices, permitted use, paid art |
| Accessibility reviewer | WCAG 2.2 AA, DOM semantics, keyboard/screen-reader/device checks |
| Business owner | Etsy, CRM, support, finance, contracts, HR, KPI, business records |
| Trading/compliance owner | Paper readiness, jurisdiction, leverage/limits, live readiness and tax |
| Operations/QA | Staging, replay, backups, load, recovery, evals, evidence manifests |
| Release owner | Final frozen SHA, mandatory gate decision, waivers, deployment/rollback |

The owner always signs the live-trading risk policy and release verdict; no agent grants those
permissions to itself.

---

## 15. Mandatory final release gate

A release is blocked until:

1. Every applicable checklist row has PASS evidence.
2. Reference lane and one external provider-sandbox workflow have receipts/reconciliation.
3. All security, accessibility and performance tests are green.
4. Restore and unknown-outcome recovery pass.
5. One final matrix runs once on a frozen commit.
6. Owner signs the release decision and live-trading readiness separately.

Design and partial code already exist. No phase is declared complete until its acceptance evidence
exists, is fresh and is independently reviewed.
