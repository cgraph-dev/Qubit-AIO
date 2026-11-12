# Qubit Office — complete plan and phases

**Status:** consolidated execution plan v8. Design/contracts only where marked; no implementation
claim and no `VERIFIED` verdict.
**Purpose:** one navigable plan that records every phase, workstream, decision, artifact and gate
so nothing depends on chat memory.
**Conflict rule:** detailed contracts win over this summary; this file is the single navigation and
sequencing authority. Superseded phase lists are historical only.

---

## 1. Current state (measured, not aspirational)

- Qubit Office UI, rebrand, loading screen/weather, HUD, Agents, War Room, Approvals and Etsy
  connection surfaces exist in the live build.
- Live office scene: **4 rooms, 10 agents, 3 departments**.
- 100-floor projection: **100 floors, 5 wings, 300 rooms, 3,075 desks**.
- **283 agent records** linked from office seats; **283 assigned desks**; **18 floors / 36 rooms
  staffed** in the projection.
- Business OS: **1 tenant, 1 business (`Personal`, draft), 1 War Room session
  (`Etsy digital shop pilot`)**, 0 tasks, 0 jobs, 1 pending decision, 0 ledger entries, 0 agent
  turns.
- **No** durable sessions/workspaces, owner/agent chat, agent/agent chat, capability gateway,
  memory adapters, real Etsy/social/CRM operations, Character Studio, outside world, or rendered
  100-floor 3D world.
- Assets: **1,523 files, 404 MB** under `/home/trick/Desktop/UI Office`.
- Plan set: master plan, department/interaction design, research gaps, idea ledger, asset ledger,
  repo ledger, domain contract, threat model, Phase 0 plan.
- No `main` merge; integration branch carries the implementation.
- No full matrix; `VERIFIED` is not claimed.

---

## 2. Scope and non-goals

### Scope — full build from the start (owner decision)

No reduced MVP. Build the full system:

- full campus/department/floor/room/desk data from the start
- all departments, rooms, facilities, shared floors and outside world
- all 283+ agent identities and their sessions/workspaces (active set still controls rendering)
- all businesses/departments, not only Etsy
- full War Room, Memory Room, approvals, ops, comms, panels and capability catalog
- Character Studio merging Habblaud + LimeZu generators
- all room styles, assets, FX, maps, animations
- social/CRM/support/finance/contracts/HR/procurement/analytics
- durable execution, identity, security, observability, backup/DR
- multi-entity-ready from the data model; personal entity first

### Non-goals

- No fake success, phantom agents, renderer-owned business truth, unbounded money/legal/irreversible
  action, unlicensed code/assets, or silent duplicate side effects.
- No feature is cut as “MVP”; phases are dependency/merge order only.
- Full scope does not mean every floor/agent animates at once. It means the data and features exist;
  active/dormant rendering still applies.

## 3. Authority and source-of-truth map

| Concern | Authority | Detail |
|---|---|---|
| Entities, fields, invariants | `docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md` | Business OS event log/SQLite/Postgres |
| Departments, sessions, memory, interactions | `docs/QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md` | design contract |
| Research gaps/forgotten requirements | `docs/QUBIT-OFFICE-RESEARCH-GAPS.md` | external review |
| Ideas and status | `docs/QUBIT-OFFICE-IDEA-LEDGER.md` | owner idea ledger |
| Assets/licences | `docs/QUBIT-OFFICE-ASSET-LEDGER.md` | local inventory |
| Repos/commits/licences | `docs/QUBIT-OFFICE-REPO-LEDGER.md` | dependency ledger |
| Threats/controls/tests | `docs/QUBIT-OFFICE-THREAT-MODEL.md` | security baseline |
| Phase 0 work/gates | `docs/QUBIT-OFFICE-PHASE-0.md` | execution checklist |
| History/status | `docs/STATUS.md` | evidence ledger, not authority for design |

Obsidian is a derived projection. OpenViking is behind one scoped adapter. The renderer, tower,
office UI and maps never own business truth.

---

## 4. Architecture summary

**Campus → Department Floor → Room → Cell (16×12) → Desk → Agent**

- One floor = one department. Floors merge into a campus.
- Shared floors/rooms: War Room, Memory Room, Ops/Approvals, Comms, Server/Infra, Studio,
  Training, Lobby, Cafeteria/Atrium, outside world.
- Every room has a capability manifest: workflows, roles, toolsets, artifacts, queues, KPIs,
  memory scope, privacy, activation.
- Every agent is a security principal with a durable session, workspace, task queue, checkpoint
  and explicit grants.
- Every consequential action goes through ToolGateway.
- Every external outcome has a receipt, unknown-state handling and reconciliation.
- Every business outcome has a system-of-record record, not just a chat reply.

---

## 5. Cross-cutting contracts

### 5.1 Agent configuration

Selectors: identity/owner, permissions (RBAC+ABAC), skills, MCP servers/tools, toolsets, model/
provider/reasoning/fallback, budgets/autonomy, memory scopes, secrets, schedule, appearance,
session policy.

Rules:
- deny-by-default; no wildcard
- skills require capabilities but never grant them
- MCP allowlist at server and tool level
- model route/fallback pinned per run
- capability snapshot frozen per run; revocation interrupts
- secrets only via vault/gateway
- UI shows effective grants/inherited vs explicit/leases/budgets/audit

### 5.2 Session/runtime model

Session (durable owner↔agent) → Run (bounded attempt) → Task (durable work) → Workspace
(task-pinned) → Checkpoint (recovery) → Handoff (explicit transfer). Leases/fencing protect
concurrency. Unknown outcomes reconcile. Stop revokes leases/tokens and preserves a checkpoint.

### 5.3 Memory

- Event log authoritative.
- Obsidian: department graph, office-wide graph, agent notes, decisions/artifacts; derived only.
- OpenViking: adapter over authenticated namespaces; scoped collections; provenance/retention/
  deletion/conflict rules.
- No Brain model integration; memory is the requirement.

### 5.4 Multi-agent

Supervisor + bounded delegation; work-queue leases; artifact collaboration; reviewer/council;
single-writer pairing; department channels; commit-authority conflict resolution.

### 5.5 Security

Identity/SSO/MFA/workload identity, tenant isolation, sandbox, deny egress, secrets manager,
prompt-injection defenses, MCP/tool allowlists, tamper-evident audit, approvals bound to payload
hash, kill switch, backups/DR, supply-chain controls.

### 5.6 Business systems

Own the control plane and orchestration; integrate systems of record: CRM, support, marketing/
social, finance/accounting/payments, contracts/legal, HR, procurement, analytics, multi-entity
books. Etsy is the first vertical, not the product.

---

## 6. Phases

### Phase 0 — Freeze, contracts and ledgers

**Goal:** stop drift; make the system buildable.

Tasks:
- P0-01 freeze v8 and authority map
- P0-02 domain/projection contract
- P0-03 asset ledger (1,523 files, licences, intended use)
- P0-04 idea and repo ledgers
- P0-05 threat model
- P0-06 local durable-substrate decision + prototype (Temporal deferred)
- P0-07 staging/replay/fault-injection harness
- P0-08 Phase 1 tickets with acceptance tests

Exit gates:
- All Phase 0 artifacts committed.
- Local run/task/checkpoint/lease prototype resumes after restart; duplicate effect prevented/reconciled; approval survives.
- Staging/replay reproducible.
- No real credentials/operator data.
- Phase 1 tickets bounded and testable.

Not yet: tower, character studio, outside world, mature interactions, real social/Etsy.

### Phase 1 — Foundation: identity, durable state, security, observability

Tasks:
- P1-01 owner/tenant/business/legal-entity records; local owner auth, future OIDC/SSO.
- P1-02 RBAC+ABAC policy engine; deny-by-default; expiry/revocation.
- P1-03 local durable workflows: states, timers/waits, retries, outbox/inbox, event history; Temporal adapter deferred.
- P1-04 run state machine incl. unknown; reconciliation/dead-letter ownership.
- P1-05 append-only event log, audit chain, correlation IDs, traces/metrics/logs.
- P1-06 secrets manager/vault, sandbox, filesystem jail, egress policy.
- P1-07 backups/restore, RPO/RTO, tenant restore, restore drills.
- P1-08 schema/policy/prompt migration framework.
- P1-09 test/staging/replay and fault injection.

Exit gates:
- One durable workflow survives restart.
- Unauthorized call refused.
- Backup restored/verified.
- Trace explains a complete run.
- Secret canary absent from prompt/log/memory/workspace.

### Phase 2 — Agent runtime and capability gateway

Tasks:
- P2-01 AgentProfile/selectors; effective-policy compiler.
- P2-02 capability catalog (health/version/risk/scopes).
- P2-03 ToolGateway mandatory for model/tools/MCP/terminal/browser/files/messages/memory/
  connectors/payments/Etsy.
- P2-04 sessions/runs/tasks/workspaces/leases/checkpoints/handoffs.
- P2-05 capability snapshots; revocation interrupts.
- P2-06 message fabric: owner↔agent, agent↔agent, rooms, threads, delivery/replay.
- P2-07 durable approvals: payload hash, evidence, risk, cost, expiry, delegate/escalate.
- P2-08 memory adapter: event log authority; Obsidian projection; OpenViking scope.
- P2-09 effective-state UI.
- P2-10 one real agent lane: task → capability → approval → artifact → checkpoint → restart.

Exit gates:
- One agent lane works end-to-end.
- Permissions/skills/MCP selectors are enforced, not decorative.
- Messages replay; approvals survive restart; revocation stops execution.
- No duplicate side effect after restart.

### Phase 3 — One department floor vertical slice

Tasks:
- P3-01 department template from current office image
- P3-02 reception, work area, boardroom, focus, kitchen/break, restrooms, lounge, head office,
  storage
- P3-03 real room/seat/capability manifests; 3–5 real agents
- P3-04 department Obsidian graph + OpenViking scope
- P3-05 room map, character/portrait basics, status/activity feed
- P3-06 owner chat, agent-to-agent chat, handoff
- P3-07 business record, tasks, artifacts, metrics, audit
- P3-08 restart/recovery tests

Exit gates:
- User can watch real work, talk to agents, see artifacts, recover after restart.
- Office is a projection; business truth survives with the browser closed.
- No more than one department; no 100-floor expansion yet.

### Phase 4 — First business workflow (Etsy)

Tasks:
- P4-01 niche → licensed digital asset
- P4-02 draft listing (title/description/tags/price/files)
- P4-03 IP/licence evidence + owner approval
- P4-04 official OAuth publish + provider receipt
- P4-05 order/download/support/refund flows
- P4-06 payout/reconciliation/ledger
- P4-07 margin report
- P4-08 unknown-outcome/retry/rollback

Exit gates:
- One complete Etsy flow with receipts and reconciliation.
- Digital asset versioning/malware scan/entitlement.
- No fake success.
- Spending/publishing/refunds gated.

### Phase 5 — Business systems of record

Tasks:
- P5-01 CRM/contacts/leads/consent/pipeline
- P5-02 support/tickets/SLA/handoff
- P5-03 marketing/social campaigns/drafts/approvals/publication receipts
- P5-04 finance/accounting/payments/ledger/reconciliation
- P5-05 contracts/obligations/renewals
- P5-06 HR/people/agent lifecycle
- P5-07 procurement/vendors
- P5-08 analytics/KPI contracts/freshness
- P5-09 multi-entity separation

Exit gates:
- Each business action has a system-of-record row and provider receipt.
- No financial truth from narrative/dashboard totals.
- Human approvals for money/legal/publish/contact/irreversible.

### Phase 6 — Multiply departments and campus

Tasks:
- P6-01 clone working department template
- P6-02 department/floor/room builder with validation/versioning/undo
- P6-03 department-specific capabilities/memory/roles/business/assets
- P6-04 campus map/elevators/stairs/shared floors
- P6-05 active/dormant scheduler with fairness/leases/business continuity
- P6-06 multi-department permissions/budgets/reporting

Exit gates:
- New department can be created, staffed, operated, recovered.
- Dormant department continues business execution.
- No business depends on camera visibility.

### Phase 7 — Character Studio, UI and world

Tasks:
- P7-01 CharacterProfile merge (Habblaud + LimeZu Character 2.0 + Portrait 1.5)
- P7-02 one save authority; transactional projections; rollback
- P7-03 full panel inventory/effective-state controls
- P7-04 distinct room styles/furniture/lighting/assets
- P7-05 outside world/campus exterior
- P7-06 renderer-neutral map/chunk/layer/door/portal schema
- P7-07 web renderer first; Godot after parity
- P7-08 asset/licence enforcement across bundles/caches/screenshots/backups/exports

Exit gates:
- Visual office reflects real state.
- Character edits round-trip between profile and renderer.
- No paid art in public tree/export.
- No renderer-invented business truth.

### Phase 8 — Interactions, FX, mature simulation and scale

Tasks:
- P8-01 needs/traits/relationships/action catalog
- P8-02 consequences: stress/health/morale/HR/legal/career
- P8-03 mature-content policy: off by default, non-graphic, consent/HR/legal guards
- P8-04 event-to-FX schema/replay/cancel/fallback/reduced motion
- P8-05 outside life/day-night/weather/seasons
- P8-06 100/500/1,000-agent load tests; frame/memory/event/persistence budgets
- P8-07 capacity/backpressure/disaster game days/release rings/evals
- P8-08 optional Godot/2.5D/3D after web parity

Exit gates:
- Interactions do not weaken durability/security/business truth.
- Scale meets budgets.
- Mature content is opt-in and bounded.
- FX are event-driven, not random business state.

### Phase 9 — Release and operations

Tasks:
- P9-01 security/privacy review and negative tests
- P9-02 backup/restore/incident/on-call/kill switch
- P9-03 feature flags/deployment rings/canary/rollback
- P9-04 observability/evals/scorecards/synthetic monitoring
- P9-05 self-hosting contract/setup/migrations/import/export/telemetry
- P9-06 manual runbooks/exit/shutdown/close
- P9-07 one final matrix on frozen SHA

Exit gates:
- All P0/P1/P2 gates have evidence.
- Restore drill passes.
- Unknown-outcome/reconciliation drills pass.
- Release can be rolled back.
- `VERIFIED` only if the owner release gate is satisfied.

---

## 7. Phase 0 artifact status

| Artifact | Status |
|---|---|
| `QUBIT-OFFICE-MASTER-PLAN.md` | present |
| `QUBIT-OFFICE-COMPLETE-PLAN.md` | present (this file) |
| `QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md` | present |
| `QUBIT-OFFICE-RESEARCH-GAPS.md` | present |
| `QUBIT-OFFICE-IDEA-LEDGER.md` | present (70 rows) |
| `QUBIT-OFFICE-ASSET-LEDGER.md` | present (summary; per-file generator pending) |
| `QUBIT-OFFICE-REPO-LEDGER.md` | present |
| `QUBIT-OFFICE-DOMAIN-CONTRACT.md` | present |
| `QUBIT-OFFICE-THREAT-MODEL.md` | present |
| `QUBIT-OFFICE-PHASE-0.md` | present |

Phase 0 **documentation** is present. Phase 0 **execution** is not:
- Local durable-substrate prototype: pending
- staging/replay harness: pending
- per-file asset ledger generator: pending
- Phase 1 tickets with tests: pending
- exit-gate evidence: pending

---

## 8. Research gaps that must not be lost

Must-haves: durable execution/reconciliation; identity/tenancy; capability gateway; sessions/
tasks/workspaces/leases/checkpoints; message fabric; durable approvals; sandbox/secrets/egress;
tamper-evident audit; observability/evals; backups/DR; cost/backpressure; business systems of
record; Etsy/digital operations; multi-entity; self-hosting/exit.

Additional must-haves: isolated test/staging/replay; data lineage; schema/policy migrations;
access recertification; notification/on-call; jurisdiction rules; content moderation/asset use;
verifiable receipts; feature flags; backup-restore verification.

Nice-to-haves: agent scorecards; synthetic monitoring; trace review; knowledge lifecycle;
semantic search/dedup; trust center; vendor/model risk registry; deployment rings; disaster game
days; local-first sync.

Full detail: `docs/QUBIT-OFFICE-RESEARCH-GAPS.md`.

---

## 9. What is explicitly deferred

- 100-floor rendered world
- outside world/terrain
- Character Studio implementation
- mature interactions and social simulation
- real social/CRM/finance integrations
- Godot
- 283 active agents
- broad autonomy
- final matrix

These remain in the plan but cannot start before Phase 3/4 prove the first department and business
workflow.

---

## 10. Immediate next actions

1. Freeze this v8 plan; no more reordering.
2. Accept/owner-review the domain contract and threat model.
3. Build the per-file asset ledger generator.
4. Verify licences/commits for claw-empire, SkyOffice and no-license repos.
5. Stand up the local durable-substrate prototype + staging/replay harness.
6. Write Phase 1 tickets with acceptance/failure tests.
7. Implement identity/tenant/event log.
8. Implement ToolGateway deny-by-default skeleton.
9. Build one durable agent lane.
10. Start the first department floor only after Phase 1/2 gates pass.

**Do not build the tower, character editor, outside world or mature interactions first.**


---

## 11. Full-scope build v9 (owner decision)

**Owner decision:** no MVP, no reduced first slice. The full product scope is committed from the
start. The plan review remains a bug/risk list, not a scope reduction.

All prior “MVP”, “one department”, “3–5 agents”, “defer Temporal/SSO/multi-entity/social”, and
“defer Character Studio/outside world/interactions” statements in this document are superseded.

### Full-scope workstreams

1. **Platform foundation:** identity/tenancy, durable execution, event log, policy/capability
   gateway, sessions/runs/tasks/workspaces/leases/checkpoints, message fabric, approvals,
   reconciliation, secrets/sandbox/egress, observability/evals, backups/DR.
2. **Office/campus:** all departments, floors, rooms, facilities, shared floors, maps, outside world.
3. **Agent workforce:** all 283+ identities, per-agent sessions/workspaces, chat, handoffs,
   delegation, full Qubit capability selectors (tools/skills/MCP/models/permissions/memory).
4. **Memory:** department + office Obsidian graphs, OpenViking scopes, provenance/retention/
   deletion.
5. **Businesses:** all departments and business workflows, Etsy first live, then social/CRM/support/
   finance/contracts/HR/procurement/analytics, multi-entity-ready.
6. **Visuals:** Character Studio merge, LimeZu room styles, full panels, FX, animations, weather,
   outside world.
7. **Interactions:** social/needs/relationships/conflict/romance/career/mature policy and
   consequences.
8. **Scale/security/release:** 100/500/1,000-agent load, backup/restore, incident/kill switch,
   deployment rings, evals, release gates.

### Dependency order (not scope cuts)

- Contracts/ledgers/threat model first.
- Identity/durable execution/capability gateway before real external actions.
- Session/chat/memory before agent collaboration.
- One business workflow before all business systems; then activate the rest.
- Character/outside world can be built in parallel with backend, but cannot own business truth.
- Interactions/FX can be built in parallel, but cannot start before action/event schema.
- Security/recovery gates apply to every workstream.

### Full-scope acceptance

Every department/room/business/agent exists in data; every external action has approval/receipt/
reconciliation; every page reflects real state; every paid asset/licence is recorded; every
failure is typed; restart/restore has evidence. `VERIFIED` still requires the owner release gate.



---

## 12. 2026 improvement addenda

The external review in `docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md` adds mandatory plan changes:

- Build a 100-floor/3,075-desk/1,000-agent synthetic scale fixture before the tower.
- Define the projection contract and renderer-neutral map schema before chat/animation.
- Benchmark Phaser vs Pixi; web renderer first; Godot only after parity.
- Render one active floor, stream chunks/atlases, use active/dormant tiers.
- Add command-center shell, accessible dual UI, notification center, search-driven routing,
  minimap/floor directory and capability-bearing room cards.
- Add performance budgets and CI/staging gates.
- Separate authoritative business events from ephemeral presentation events.
- Add context-loss/reconnect/restore/fault-injection tests.
- Keep full scope; use dependency gates, not scope cuts.


---

## 13. Browser comparison corrections

The browser review in `docs/QUBIT-OFFICE-BROWSER-REVIEW.md` adds mandatory corrections:

- Add an **agent registry** and **A2A handoff contract**, not only roles/MCP.
- Enforce **one writer per mutable resource/file**; append-only fragments for shared files.
- Add **retry/handoff/delegation ceilings**, no-progress detection and circuit breakers.
- Add **per-tool eval/schema-drift canaries**, an independent verifier and protected checks.
- Cap parallel agent waves and aggregate budgets.
- Add Tiled-compatible map import/export, validation and chunked tilemaps.
- Benchmark **Phaser 4 GPU layers vs Pixi chunking**; web 2D first, Godot later.
- Add context-loss recovery, dirty rendering, device tiers and scale fixture.
- Full data/features from start, but **not all 283 agents continuously simulated**; active-set
  rendering and dormant execution remain.


---

## 14. Mandatory 100% checklist

The release/implementation gate is `docs/QUBIT-OFFICE-MANDATORY-CHECKLIST.md`. It maps OWASP
Agentic Top 10 2026, MCP OAuth/tool-schema/output rules, sandbox/RCE, approvals, memory, A2A,
WCAG 2.2 AA, OpenTelemetry GenAI, Temporal durability, privacy/licensing and performance/device
budgets to PASS/FAIL evidence. No release or `VERIFIED` claim while an applicable row is FAIL,
UNTESTED or waived without owner sign-off.
