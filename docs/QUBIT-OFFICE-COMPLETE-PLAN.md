# Qubit Office — complete execution plan

**Version:** v10 (canonical). **Status:** design/contract plan; implementation evidence is tracked
separately. Not `VERIFIED`.
**Owner decisions:** full scope from the start; no reduced MVP; no overengineering; reuse the
repos/assets already supplied; no renderer-owned business truth.
**Conflict rule:** detailed contracts/ledgers win for their area. This file wins for phase order.
All earlier phase lists are historical.

---

## 1. Executive summary

Qubit Office is the control room over a durable Business OS:
- full campus/department/room/desk data
- all agents with durable sessions, memory, capabilities and chat
- all businesses/departments with real systems of record
- Character Studio, room styles, outside world, maps, FX and interactions
- local-first, later invite-only World and entities

The build is **full scope**, but dependencies still determine merge/activation order. A phase is
not a feature cut; it is the order in which work can be safely integrated and proven.

**Reuse-first rule:** if a supplied repo or existing Qubit component already solves a problem, use
it or adapt it under its license. Do not rebuild an engine, editor, renderer, memory system, office,
router, terminal, queue or panel system from scratch.

---

## 2. Current measured state

- Live: Qubit Office shell, 4 rooms / 10 agents / 3 departments, loading/weather, HUD, Agents,
  War Room, Approvals, Etsy connection, rebrand.
- Business OS: 1 tenant, 1 draft business, 283 agent records, 1 War Room session (`Etsy digital
  shop pilot`), 0 tasks/jobs/ledger/turns.
- 100-floor projection: 100 floors, 5 wings, 300 rooms, 3,075 desks; 283 assigned; 18 floors /
  36 rooms staffed in the projection.
- Assets: 1,523 files / 404 MB under `/home/trick/Desktop/UI Office`.
- Plan docs: master, this plan, department/interaction design, research gaps, browser review,
  improvements, idea/asset/repo ledgers, domain contract, threat model, mandatory checklist,
  Phase 0.
- Missing: real sessions/workspaces, chat, capability gateway, memory adapters, actual turns,
  real business operations, Character Studio, outside world, rendered 3D world, full release
  evidence.
- No `main` merge; no full matrix; no `VERIFIED`.

---

## 3. Build principles

1. **Full data, controlled activation.** All departments/rooms/agents/businesses exist in data;
   only active areas render/simulate. Full scope does not mean all 283 agents animate at once.
2. **Business OS is truth.** Office/tower/maps/Obsidian are projections.
3. **One execution boundary.** Every model/tool/MCP/terminal/browser/file/message/memory/external
   action passes through ToolGateway.
4. **Reuse before build.** Habblaud/LimeZu/pixelfx/SkyOffice/existing Qubit components first.
5. **No fake success.** Artifact, receipt, row, message ID or ticket required.
6. **Deny by default.** No wildcard permissions; no secrets in prompts/workspaces.
7. **One writer per mutable resource/file.** Others submit patches/fragments.
8. **Bounded autonomy.** Budgets, retries, handoffs, depth, concurrency, approvals, kill switch.
9. **Mandatory gates are release blockers**, not suggestions.
10. **No new plan order.** New ideas go to the idea ledger; phases do not reorder without owner
    sign-off.

---

## 4. Authority map

| Data | Authority | Projection |
|---|---|---|
| Owner/tenant/entity/business/department/room/agent | Business OS | Office UI, maps, tower |
| Session/run/task/workspace/checkpoint/handoff | Business OS/event log | Panels, timeline |
| Message/approval/ledger/receipt | Business OS event log | Inbox, audit |
| Capability/grant/call | Policy + ToolGateway | Agent panel |
| CharacterProfile | Business OS | Habblaud character, LimeZu portrait |
| Map/world | versioned map schema | Web renderer, optional Godot |
| Obsidian | derived projection | Human graph/search |
| OpenViking | adapter into memory API | Semantic retrieval |
| FX | event consumer | Animations |

Detail: `docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md`.

---

## 5. Reuse map (do not rebuild)

| Need | Reuse | Notes |
|---|---|---|
| Office/room rendering, seats, character state | Habblaud | MIT; don't break current behavior |
| Room floors/furniture/portraits/UI | LimeZu packs | paid/local-only; licence ledger |
| Loading/weather/FX | pixelfx | MIT; event-driven |
| Room/chat/map commands | SkyOffice | MIT; verify commit |
| Worktree isolation, departments, token handling | claw-empire | Apache-2.0; verify notices |
| Session/meeting UI patterns | agent-monitor | no LICENSE file → concepts only |
| Roster/skills/MCP selector concepts | agents-office | PolyForm; concepts only, no copy/bundle |
| Per-agent sandbox/worktree concepts | Agent Office / JSM HQ | MIT; verify commits |
| Business/CRM/ERP patterns | Veska/Oryh/AgenticCRM/Autonomous Business OS/FusionClaw/OOSDK | later references; verify licences |
| Existing Qubit state/session/gateway | Qubit runtime | extend, don't replace |
| Map pipeline | Tiled | import/export candidate; validate schema |
| Web renderer | Phaser or Pixi | benchmark before choosing |

Full mapping: `docs/QUBIT-OFFICE-REPO-LEDGER.md`.

---

## 6. Phases

### Phase 0 — Program setup and contracts

**Goal:** make the build unambiguous and reusable.

Tasks:
- P0-01 freeze v10, owner decisions, no new phase reordering.
- P0-02 finalize domain/projection contract (`DOMAIN-CONTRACT`).
- P0-03 asset ledger: per-file sha256/licence/use; paid/local-only rules.
- P0-04 idea/repo ledgers; exact commits/licences; concept-vs-code status.
- P0-05 threat model + mandatory checklist accepted.
- P0-06 reuse audit: map every existing repo/component to the phase where it is used.
- P0-07 staging/replay environment: throwaway home, synthetic tenant, fake providers/tools,
  deterministic seeds, fault injection.
- P0-08 scale fixture: 100 floors, 3,075 desks, 1,000 agents (data, not rendered).
- P0-09 renderer benchmark: Phaser 4 GPU layers vs Pixi chunking with identical maps/sprites.
- P0-10 durable execution decision: existing SQLite run/task/checkpoint/lease/outbox now; Temporal
  adapter when multi-worker/HA is measured. Do not build a homegrown full workflow engine.

Exit gate:
- All contracts/ledgers/threat/checklist committed.
- Reuse map and licence status for every repo.
- Staging/replay and scale fixture runnable.
- Renderer benchmark recorded.
- Phase 1 tickets have acceptance/failure tests.

### Phase 1 — Core platform and security

**Goal:** make sessions, actions and recovery safe before real work.

Tasks:
- P1-01 owner/tenant/business/entity records; local owner auth, OIDC-ready.
- P1-02 RBAC+ABAC policy engine; deny-by-default; expiry/revocation.
- P1-03 event log/audit chain; correlation IDs; append-only messages.
- P1-04 durable run/task/workspace/checkpoint/lease/outbox; unknown state + reconciliation.
- P1-05 ToolGateway skeleton; capability catalog; policy compiler; capability snapshots.
- P1-06 message fabric: owner↔agent, agent↔agent, rooms, threads, delivery/replay.
- P1-07 approvals: payload hash, evidence, risk, cost, expiry, delegate/escalate.
- P1-08 secrets manager, sandbox, filesystem jail, deny egress, outbound allowlists.
- P1-09 observability: OpenTelemetry GenAI spans/metrics, content capture off, eval events.
- P1-10 backups/restore, RPO/RTO, tenant restore, restore drills.
- P1-11 migration/versioning for schemas/policies/prompts/checkpoints.
- P1-12 test/staging/replay/fault injection; per-tool eval/schema canary.

Exit gate:
- Restart resumes a run; duplicate effect prevented/reconciled.
- Unauthorized capability call denied.
- Approval survives restart and is payload-bound.
- Secret canary absent from prompts/logs/memory/workspaces.
- Restore verified; trace explains a run end-to-end.

### Phase 2 — Agent runtime and capability integration

**Goal:** every agent has a real, recoverable lane using Qubit’s actual capabilities.

Tasks:
- P2-01 Agent Registry: identity, owner, department, tools, skills, MCP, model policy, grants,
  health, version.
- P2-02 Agent configuration selectors: permissions, skills, MCP/tool allowlists, model/provider/
  reasoning/fallback, budgets, autonomy, memory scopes, secrets, schedule, appearance.
- P2-03 Framework-neutral agent adapter; model/provider routing pinned per run.
- P2-04 ToolGateway execution for model/tools/MCP/browser/terminal/files/messages/memory/connectors.
- P2-05 sessions/workspaces/leases/checkpoints/handoffs; task-pinned workspace; one writer.
- P2-06 A2A typed/authenticated messages; no privilege inheritance; replay protection.
- P2-07 memory adapter: event log authoritative; Obsidian projection; OpenViking scoped adapter;
  provenance/retention/deletion.
- P2-08 agent chat, owner chat, room channels, handoff.
- P2-09 actual turn execution: prompt → model → tool → approval → artifact → receipt → checkpoint.
- P2-10 effective-state UI: grants, model, leases, budgets, approvals, audit.
- P2-11 independent verifier; agents cannot edit checks/evals/policy; retry/handoff/depth caps.

Exit gate:
- One agent completes real recoverable work with one capability and one approval.
- A second agent can receive a typed handoff with artifacts.
- Revocation interrupts a live call; restart does not duplicate effects.
- Memory scope/ACL and A2A spoof tests pass.

### Phase 3 — Full campus and office data

**Goal:** full structure, not a reduced department.

Tasks:
- P3-01 all department/floor/room/cell/desk records from templates; full campus data.
- P3-02 per-floor facilities: reception, kitchen, restrooms, lounge, boardroom, focus, desks, head
  office, storage, comms.
- P3-03 room capability manifests: workflows, tools, roles, artifacts, queues, KPIs, privacy,
  memory, approvals, activation.
- P3-04 all 283+ agent placements with active/dormant policy; no fake occupancy.
- P3-05 map schema: campus/department/room/cell; Tiled import/export candidate; validation,
  portals, connectivity, egress, capacity.
- P3-06 renderer: benchmark-selected web 2D; one active floor, chunked tilemaps/atlases, LRU,
  context-loss recovery, dirty rendering.
- P3-07 full panels shell: command center, search, floor navigator, agent/room/task drawers,
  notifications, accessibility DOM mirror.
- P3-08 Character Studio shell: Habblaud state + LimeZu character/portrait adapters.
- P3-09 outside-world data shell and lobby/elevator connection.

Exit gate:
- Every department/room/desk exists in data; selected floor renders correctly.
- Active/dormant rendering does not affect business execution.
- Map validation/accessibility/device budgets pass.
- Room capabilities resolve to real workflows/tools/memory.

### Phase 4 — Full agent workforce and collaboration

**Goal:** all agents work, talk, remember and hand off without losing work.

Tasks:
- P4-01 all agent sessions/workspaces provisioned; per-agent memory scopes.
- P4-02 owner↔agent chat; agent↔agent threads; room channels; mentions; handoff acceptance.
- P4-03 supervisor/delegation patterns with caps, leases and independent verification.
- P4-04 task queue, worktrees/sandboxes, artifacts, checkpoints, recovery.
- P4-05 full capability access through ToolGateway; no hardcoded provider.
- P4-06 department Obsidian graphs + office graph; OpenViking scopes.
- P4-07 agent-to-agent conflict resolution; one writer per resource; append-only fragments.
- P4-08 pause/stop/revoke/quarantine/kill switch; behavior baseline/anomaly detection.
- P4-09 replayable operator UI; run timeline; cost/evidence/unknown states.

Exit gate:
- Multiple agents collaborate on a task without shared-state corruption.
- Handoff loop/retry/delegation caps work.
- Chat/memory survive restart; revocation stops active calls.
- No work lost on crash.

### Phase 5 — Business operations and systems of record

**Goal:** all businesses/departments operate on real records.

Tasks:
- P5-01 Etsy end-to-end: niche → asset → draft → approval → OAuth publish → receipt → order/
  download/support/refund → payout → ledger → reconciliation → margin.
- P5-02 CRM/contacts/leads/consent/pipeline.
- P5-03 support/tickets/SLA/handoff.
- P5-04 marketing/social campaigns/drafts/approvals/publication receipts.
- P5-05 finance/accounting/payments/ledger/reconciliation.
- P5-06 contracts/obligations/renewals.
- P5-07 HR/people/agent lifecycle.
- P5-08 procurement/vendors.
- P5-09 analytics/KPI contracts/freshness.
- P5-10 multi-entity-ready books/entities; personal first.
- P5-11 business factory: create/clone/operate departments and businesses.

Exit gate:
- Every committed outcome has a system-of-record row or provider receipt.
- Unknown outcomes reconcile; money/legal/publish/contact require approvals.
- No business depends on the office renderer.
- One pilot business fully operational; additional businesses can be cloned.

### Phase 6 — Character Studio, room styles, outside world, full UI

**Goal:** full visual identity and customization over working operations.

Tasks:
- P6-01 Character Studio: merge Habblaud state/editor with LimeZu Character/Portrait generators.
- P6-02 one CharacterProfile save authority; transactional projections; versioning/rollback.
- P6-03 per-agent appearance, portraits, voices, animation sets, role presets.
- P6-04 room styles: archetypes, furniture sets, palettes, lighting, signage, seeds/variants.
- P6-05 department/floor/room builder: draft/version/preview/undo/approve/rollback.
- P6-06 outside world/campus exterior, lobby/elevators, weather/day-night, active zones.
- P6-07 full panel inventory: agents, sessions, chat, terminal, deliveries, timeline, War Room,
  Memory, Approvals, Ops, ledger, CRM, marketing, Etsy, models/MCP/plugins/skills, budgets, audit.
- P6-08 accessibility/WCAG 2.2 AA across canvas and DOM.
- P6-09 licence-aware asset build; no paid art leakage.

Exit gate:
- Character edits round-trip; room styles validate; map/world reflects real state.
- Every panel has data authority, permissions, empty/error states and real actions.
- Keyboard/screen-reader/reduced-motion review passes.
- Paid/licensed art boundaries pass.

### Phase 7 — Interactions, FX and social simulation

**Goal:** make the world alive without creating fake business truth.

Tasks:
- P7-01 needs/traits/relationships/action catalog.
- P7-02 consequences: stress/health/morale/HR/legal/career.
- P7-03 mature-content policy off by default; non-graphic, consent/HR guards.
- P7-04 event-to-FX schema; replay/cancel/fallback; reduced motion.
- P7-05 motion, weather, outside life, day/night/seasons.
- P7-06 action authority: simulation-only vs authoritative HR/legal/business events.
- P7-07 social graph visualization and room activity.
- P7-08 no random business claims or fake completion from simulation.

Exit gate:
- Interactions alter only declared state; sensitive events require policy.
- FX are event-driven, reversible/disableable and not business truth.
- Mature content is opt-in and bounded.

### Phase 8 — Scale, performance, security and release

**Goal:** prove the full system under load and failure.

Tasks:
- P8-01 100/500/1,000-agent load; 100-floor scale fixture.
- P8-02 device tiers: desktop/low-end/mobile; frame/memory/bundle/event budgets.
- P8-03 WebGL context-loss, reconnect, restore/fault injection.
- P8-04 OWASP Agentic Top 10 tests; MCP OAuth/schema/audience; prompt injection; sandbox escape;
  memory poisoning; A2A spoof; rogue-agent/kill switch.
- P8-05 WCAG 2.2 AA manual and automated review.
- P8-06 OpenTelemetry GenAI traces/evals; sampling; no content/PII leakage.
- P8-07 backups/DR/restore drills; incident response; deployment rings/rollback.
- P8-08 final matrix once on frozen SHA; release gate.

Exit gate:
- Mandatory checklist has PASS evidence for every applicable row.
- Scale/device/accessibility/security/recovery gates pass.
- No `VERIFIED` without owner release evidence.

---

## 7. Parallel tracks

These can run in parallel with the phase owner, but cannot merge ahead of their dependencies:

- **Office/assets/UI:** room styles, panels, Character Studio shell, maps, outside world.
- **Business systems:** CRM/finance/contracts/HR/analytics adapters.
- **Security/ops:** threat tests, observability, backups, deployment.
- **FX/interactions:** event schema, animations, social simulation.
- **Scale:** synthetic fixtures, renderer benchmark, load/profile.

Parallel work does not reorder truth: ToolGateway, event log, sessions and business records remain
authoritative.

---

## 8. Mandatory gates by phase

| Phase | Mandatory gate |
|---|---|
| 0 | Contracts, ledgers, reuse/licence map, threat model, staging/replay, scale fixture, renderer benchmark |
| 1 | Identity/policy, durable run, gateway, secrets/sandbox, approval/reconciliation, backup restore, OTel |
| 2 | Agent registry, sessions/workspaces, MCP/A2A, memory ACL, real turn, revocation |
| 3 | Full campus data, room capabilities, map validation, active-set renderer, WCAG shell |
| 4 | Multi-agent collaboration, one-writer, caps, handoff, kill switch, no lost work |
| 5 | Business receipts/reconciliation, approvals, systems of record, Etsy end-to-end |
| 6 | Character save authority, room builder, full panels, outside world, accessibility/licence |
| 7 | Interaction authority, mature policy, event FX, no fake business truth |
| 8 | OWASP/MCP/WCAG/OTel/scale/recovery/release evidence |

Full checklist: `docs/QUBIT-OFFICE-MANDATORY-CHECKLIST.md`.

---

## 9. Bugs/misconfigurations corrected

- Duplicate phase orders removed; this v10 is canonical.
- Full-scope vs MVP/deferral contradictions removed.
- Full data vs active rendering clarified.
- 283 agents ≠ 283 continuously simulated.
- Temporal not forced before measured need; existing SQLite control plane first, adapter later.
- Agent registry/A2A/one-writer/retry caps/verifier/protected checks added.
- OWASP/MCP/WCAG/OTel mandatory gates added.
- Renderer/map/scale budgets and Tiled/Phaser/Pixi decisions added.
- Reuse-first mapping added; no rebuilding existing repos/engines.
- Asset/repo licence gaps remain explicit, not assumed.

---

## 10. Immediate next actions

1. Freeze this v10 and stop phase reordering.
2. Complete per-file asset ledger and exact repo commit/licence mapping.
3. Approve domain contract/threat model/mandatory checklist.
4. Build staging/replay + 100-floor/1,000-agent fixture.
5. Benchmark Phaser 4 GPU layers vs Pixi chunking.
6. Implement Phase 1 identity/event/gateway/run/approval/recovery.
7. Build Phase 2 agent registry/one real lane.
8. Populate full campus data and room capability manifests.
9. Use Habblaud/LimeZu/pixelfx/SkyOffice first; add other repos only where mapped.
10. No release/`VERIFIED` until mandatory checklist has evidence.

**Do not build another narrow MVP or another isolated backend slice.**
