# Qubit Office — 2026 improvement review

**Status:** external research synthesis. Release claims are reported/uncertain until verified
against official notes or package metadata. Not implementation evidence, not `VERIFIED`.

**Research inputs:** Perplexity reviews of agent-platform releases, UI/map/room design and
100-floor/283-agent scaling; local plan/repo review.

---

## 1. New releases and ecosystem changes

**Rule:** adopt patterns, not versions, until the release is verified.

| Project | Reported change | What to adopt | Caution |
|---|---|---|---|
| LangGraph / LangSmith | reported 1.2 durable crash recovery/interrupt schemas | explicit state machine, interrupts, replay patterns | do not couple domain model to framework internals; exact release unconfirmed |
| Microsoft Agent Framework / AutoGen | reported 1.0 GA Python/.NET with MCP/multi-provider | optional execution adapter; MCP/tool abstraction | rapid API change and Microsoft coupling |
| Temporal | reported OpenAI Agents SDK integration | durable activities, timers, signals, approvals | operational weight; verify exact integration |
| MCP specification | reported 2026 trace-context propagation | MCP at ToolGateway with allowlists/schema/provenance | MCP is not authorization; prompt-injection and supply-chain risk |
| Google ADK | reported graph/multi-agent changes | graph/delegation patterns | provider gravity; verify self-hosted maturity |
| OpenAI Agents SDK | active 2026 releases, MCP tool metadata | optional agent-runtime adapter | pre-1.0 churn; no built-in business durability |
| Anthropic/Claude Agent SDK | no confidently verified durable-runtime release | model/coding adapter | SDK/licensing/API behavior may change |
| E2B/Daytona | no confidently verified 2026 change | sandbox profile/jail/egress/secret/expiry abstraction | sandbox ≠ safe; escape/exfiltration remains |
| OpenTelemetry GenAI | semantic conventions evolving | one trace across request→run→model→tool→approval→receipt | avoid leaking prompts/secrets as attributes |
| Agent identity/auth | 2026 platform identity/gateway concepts reported | Qubit-owned principals, delegated authority, expiry, revocation | agent name/session/token is not identity |
| Letta/Mem0/OpenViking | no confidently verified release | scoped memory adapter | memory can become un-audited second truth |

**Decision:** keep Qubit's event log, workflow state, capability gateway, leases, receipts and
reconciliation authoritative. Frameworks are adapters.

---

## 2. Platform improvements to add

1. **Capability Gateway as stable ABI** — one internal interface for model/tool/MCP/terminal/
   browser/files/memory/messaging/connectors; record policy decision, snapshot, lease, evidence.
2. **Durability conformance suite** — every adapter tested with restart, crash-after-effect,
   duplicate delivery, revoked lease, approval expiry, provider 5xx and unknown outcome.
3. **Trace-to-receipt causal graph** — link request, run, task, model, MCP, approval, sandbox
   artifact, provider request ID, receipt, ledger row and reconciliation.
4. **Agent identity + short-lived capability leases** — run/capability scoped, expiry, fencing,
   audience, payload hash, immediate revocation.
5. **Framework-neutral durable adapter** — normalize events into Run/Task/Checkpoint/Handoff;
   framework state is execution detail.
6. **Replayable operator UI** — inspect event history, branch simulation, compare policy/model
   versions, resume only from checkpoint; effects stubbed by default.
7. **Model/vendor risk registry** — version, license, residency, pricing, data handling, fallback,
   outage/exit; pin per run.
8. **UI observability** — run timeline, current state, waiting reason, effective grants, cost,
   evidence, checkpoint, approval, receipt, unknown/replay view. No decorative activity as truth.

---

## 3. UI improvements

### Command-center shell

- Persistent roster, global search, floor/department switcher, notification inbox, task filters,
  breadcrumbs, keyboard shortcuts.
- Agent status = explicit task/state/attention/model/tool/checkpoint, not animation alone.
- Attention center: blocked, approval needed, failed, completed, handoff, policy event; snooze,
  owner, dedupe, severity, deep links.
- 100-floor navigator: typeahead, filters, favorites, recents, elevator jump, occupancy heatmap,
  direct agent/room results.
- Progressive disclosure: map canvas simple; agent/room/task/memory/audit in stable-URL drawers.
- Accessible dual interface: every canvas entity mirrored in semantic DOM; keyboard/focus,
  text-only floors, reduced motion, high contrast, non-color status.
- Mobile triage mode: alerts, agents, chat, approvals, search, floor jump; builder on desktop.
- Presence privacy: public/department/private/redacted; coarse status when restricted; audit.
- Performance dashboard: FPS, entity count, atlas/chunk residency, network/event rate.
- 2D-first renderer contract: 2.5D/3D only after usability/performance pass.

### Character Studio

- One canonical profile; body/outfit/portrait/palette/animation/role presets.
- Live world + portrait preview; randomize/lock; versioning; accessibility preview.
- Character state, portraits and Business OS profile round-trip.
- No second editor or save authority.

---

## 4. Map and room ideas

- **Campus → Department → Room → Cell → Desk → Agent** as navigable scopes with breadcrumbs,
  occupancy, permissions, memory scope and direct links. Never require walking to reach a record.
- **Department template + deterministic variants**: required reception/kitchen/restroom/lounge/
  boardroom/focus/desks/head office/storage/comms; vary palette, props, lighting, topology by seed.
- **Capability-bearing rooms**: workflows, allowed actions, privacy, capacity, required agents,
  cooldowns, effects, memory events, approvals; expose in room cards.
- **Floor overview + minimap**: rooms, exits, elevators, active agents, target, blocked paths;
  zoom/pan/recenter; accessible list equivalent.
- **Search-driven routing**: agent/room/desk/task/artifact/memory/event results with coordinates
  and Jump Here; permission-aware snippets.
- **Builder validation**: draft/version/preview/undo/approve/rollback; paths, doors, egress,
  capacity, room requirements, unique IDs, permission/memory scope; no live free-form agent edits.
- **Elevator/stairs/floor directory**: instant jump plus optional transition; favorites,
  department grouping, status/occupancy.
- **Room modes**: normal/meeting/incident/maintenance/private/presentation; state-driven props,
  permissions, overlays.
- **Outside world as chunked context**: lobby→elevators; active-zone animation; visitors/weather/
  vehicles presentation only.
- **Memory graph as separate map**: office/department/agent/room/decision/task/artifact graph with
  provenance, permissions, backlinks, filters.
- **Hybrid 2D/2.5D**: fixed-grid orthographic rooms; optional depth for exterior/atrium; no
  free-camera 3D until interaction tests justify it.

### Avoid

- Walking through 100 floors to reach work.
- Animation as the only status signal.
- One giant full-fidelity campus map.
- Live free-form wall editing by agents.
- Decorative rooms without actions/data authority.
- Separate character editors/save authorities.
- 3D-first implementation.
- Unread/ambient event spam.

---

## 5. Scale problems and mitigations

| Problem | Mitigation | Priority |
|---|---|---|
| Rendering all 100 floors | Render camera floor + nearby rooms; chunk streaming; batching; LOD; context-loss recovery | P0 |
| Browser memory exhaustion | Device tiers; decoded texture/chunk caps; LRU; no duplicate atlases; heap/GPU proxies; low-memory tests | P0 |
| Streaming stalls/popping | Immutable manifest bundles; placeholders; adjacent prefetch; worker decompression; cancellation; offline current floor | P0 |
| 283+ continuous simulation | Active/dormant tiers; event-driven summaries; business execution independent of visibility | P0 |
| Cross-floor pathfinding | Hierarchical room/floor/campus graphs; portal reservations; batched replans; path cache | P1 |
| Presence/event storms | Separate authoritative and ephemeral events; quantize movement; interest-scoped snapshots; coalesce/backpressure | P0 |
| Conflicting authority | Event log/domain services authoritative; renderer read-only; versioned projections | P0 |
| Duplicate/lost effects | Idempotency, outbox/inbox, leases/fencing, receipts, unknown, reconciliation, dead-letter owner | P0 |
| Atlas fragmentation | Licence/style atlas families; co-visibility packing; stable IDs; controlled padding | P1 |
| Map editor corruption | Versioned schema, validation, UUIDs, portal/connectivity tests, undo, migrations, transactional publish | P0 |
| Save/restore inconsistency | Persist events/checkpoints; snapshot projections with versions/asset hashes; restore to staging, replay/verify/promote | P0 |
| Chat/memory overload | Partition, retention, pagination/summaries, provenance/ACL before retrieval, rebuildable projections | P1 |
| Visitor/multiplayer abuse | Authoritative server; intents not truth; tenant identities; rate limits/moderation; read-only spectator first | P0 |
| Godot web mismatch | Web renderer first; renderer-neutral schema; Godot only after parity/profiling/export-size tests | P1 |
| Combinatorial testing | Deterministic simulation, replay, property tests, fault injection, device matrix, visual regression, 100/500/1,000 agents | P0 |
| Performance budget drift | CI/staging gates on frame/heap/bundle/event/load/reconnect budgets | P0 |
| Main-thread stalls | Workers for parsing/decompression/pathfinding; compact deltas; virtualized lists; idle scheduling | P1 |
| Paid-art/cache leakage | Per-file licence metadata, build allowlists, bundle/backup/export scans, private/public packages | P1 |

---

## 6. Performance budgets

- Interactive frame: P95 ≤ 16.7 ms desktop; ≤ 33.3 ms low-end/mobile.
- Long main-thread task: < 50 ms; no recurring > 100 ms.
- Initial JS/WASM shell: ≤ 2 MB compressed before floor/asset bundles.
- Visible-floor bundle: ≤ 8 MB compressed; adjacent prefetch ≤ 12 MB.
- Decoded texture memory: ≤ 128 MB low tier / ≤ 256 MB desktop; LRU before cap.
- Map/chunk data: ≤ 32 MB low / ≤ 128 MB desktop.
- Active fully simulated agents: ≤ 40 default, configurable ceiling with graceful degradation.
- Visible animated avatars: ≤ 80; distant avatars throttled/impostors.
- Pathfinding: ≤ 2 ms average, ≤ 8 ms P95/frame; bounded request queue.
- Client events: ≤ 20/s normal, ≤ 60/s burst with coalescing.
- Server movement tick: 10–20 Hz; business events durable/asynchronous.
- Reconnect recovery: ≤ 5 s normal broadband or explicit error.
- Persistence: no acknowledged command lost; every external effect idempotency key + receipt.
- Restore: 1,000-agent synthetic state restore + replay verification ≤ 60 s staging.
- Browser heap: ≤ 256 MB low / ≤ 512 MB desktop after 10 min navigation.
- Asset cache: bounded LRU per device; no unbounded variants.
- Accessibility: all nonessential FX disableable; usable at 30 FPS.
- Scale: pass 100/500/1,000-agent scenarios before full 283-agent presentation.
- Observability: trace UI intent → event → projection → receipt → recovery.

---

## 7. Technical choices

- **Web renderer first:** chunked 2D tilemaps, sprite batching/atlases, camera streaming; keep map
  schema renderer-neutral.
- **Phaser vs Pixi:** Phaser for integrated game loop/tilemaps/cameras/scenes; Pixi for custom
  ECS/render scheduling. Benchmark both with identical maps/agents/atlases/device profiles.
- **Godot:** not initial browser runtime; optional native/later client after web parity.
- **Authority:** server/domain event log + durable workflow; clients/renderer are projections.
- **Multiplayer:** authoritative server, client intents, interest management, sequence numbers,
  snapshot/delta reconciliation; visitor/spectator read-only first.
- **Simulation:** ECS or data-oriented presentation runtime; durable business entities outside the
  render loop.
- **Pathfinding:** hierarchical A*/nav graph per room/floor, portal reservations, bounded replans.
- **Assets:** licence-aware manifests, deterministic atlases, stable IDs, content hashes,
  co-visibility packing.
- **Persistence:** append-only events + transactional snapshots/checkpoints + outbox/inbox +
  idempotency.
- **Resilience:** WebGL context-loss recovery, device tiers, static/2D fallback.

---

## 8. What to add to the plan

### Phase 0 — before the tower

- Build a synthetic campus fixture: 100 floors, 3,075 desks, 1,000 agents.
- Define projection contract: commands, authoritative events, ephemeral events, snapshots,
  versions, sequence numbers, permissions, fallback states.
- Benchmark Phaser vs Pixi with identical scenes and device tiers.
- Define performance budgets and CI/staging gates.
- Define map/room schema, IDs, connectivity, portals, atlas manifest.

### Phase 1–2 — runtime/world contracts

- Active/dormant state machines with fairness, leases, catch-up summaries and business-continuity
  tests.
- Deterministic replay/fault injection.
- Memory projection/rebuild rules.
- Command-center shell/accessibility/notification center.
- Trace-to-receipt causal graph.

### Phase 3–5 — campus/business

- Search-driven navigation, floor directory, minimap, capability-bearing room cards.
- Builder validation, preview/approve/rollback.
- Room modes, department variants, outside-world chunks.
- Read-only visitor mode; writable multiplayer only after moderation/reconciliation.

### Phase 6–9 — scale/release

- 100/500/1,000-agent load; device tiers; context-loss/reconnect tests.
- Renderer-neutral map schema; Godot after parity.
- Release gates around budgets and restore.

**First failure risk:** uncontrolled scope coupling. Keep full-scope schemas/records, but activate
world/agents/visuals behind dependency gates; never render or animate every declared entity by
default.
