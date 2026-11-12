# Qubit Office — browser review against other builders

**Status:** live web research synthesis (2026-11-12). Not implementation evidence, not `VERIFIED`.
External product claims are as published; verify before copying code.

## 1. What other builders do

### Enterprise agent platforms

- **AWS seven-layer agentic reference architecture:** orchestration, security/guardrails, identity,
  observability, data/knowledge, registry/policy, experience. Use a centralized **agent registry**
  (tools, skills, owner), specialist agents with bounded tools, MCP/A2A, short-lived credentials,
  human approval and tamper-evident audit.
- **Bain three-layer platform:** orchestration, observability, governed data; identity/policy for
  non-human principals; one trace across agents/workflows/tools; memory as first-class
  infrastructure; canary/rollback/evals.
- **Temporal durable agents:** activities for LLM/tool calls, at-least-once semantics, idempotency
  keys, granular activities, explicit timeouts/retries, human approval as durable wait.
- **Multi-agent failure studies:** shared writable state, unowned resources, green checks that
  prove nothing, agents optimizing the gate, unbounded tool chains, retry storms, handoff loops,
  role overlap and schema drift are the recurring failures.

### Office/world builders

- **The-Office:** 16 Claude agents in an isometric PixiJS office, hand-rolled A*, 60fps.
- **pixel-agents fork:** authoritative Colyseus server + Phaser renderer, Tiled map pipeline, zones,
  portals, meetings, human players.
- **Office Claws:** Pixi 8, layered character sprites, state machine, frame caching, fraction-based
  layout, nearest-neighbor pixel scaling.
- **Phaser 4:** `SpriteGPULayer` and `TilemapGPULayer` render huge sprite/tile counts in few draw
  calls; trade-off is reduced runtime flexibility and single texture per layer.
- **Pixi chunking:** visible chunk culling, object pooling, render-texture baking, compact tile
  arrays, atlas batching.
- **Teamflow:** huge tilemaps via WebGL meshes, tile/atlas blocks, IndexedDB hashing, GPU-resident
  geometry, culling.

### Key sources

- https://aws.amazon.com/blogs/machine-learning/pluggable-agentic-ai-framework-a-seven-layer-composable-enterprise-agentic-ai-reference-architecture-on-amazon-bedrock-agentcore/
- https://www.bain.com/insights/the-three-layers-of-an-agentic-ai-platform/
- https://temporal.io/blog/idempotency-and-durable-execution
- https://docs.temporal.io/activity-definition
- https://phaser.io/news/2026/05/phaser4-spritegpulayer-performance
- https://salivity.github.io/pixi.js/article/chunking-large-tilemaps-in-pixi-js-for-performance
- https://www.teamflowhq.com/dev/pushing-the-limit-with-tilemap-rendering
- https://www.avilevi.co.il/en/projects/the-office/
- https://officeclaws.com/en/blog/building-pixel-art-office
- https://madsalt.tech/blog/multi-agent-orchestration-what-broke/
- https://turion.ai/blog/multi-agent-orchestration-infrastructure-production/
- https://adamarant.com/en/blog/12-mistakes-we-see-teams-make-building-their-first-multi-agent-ops-system
- https://aaif.io/blog/when-good-agents-make-bad-systems
- https://tianpan.co/blog/2026/01/28/dont-build-multi-agents
- https://www.splunk.com/en_us/blog/artificial-intelligence/multi-agent-system-failures.html
- https://failuremodeweekly.com/posts/handoff-failures-in-multi-agent-orchestration-pipelines

## 2. Bad practices / errors in the current plan

1. **Full scope can become 283 continuously simulated agents.** Fix: full data/records, active-set
   ceiling, dormant execution, budgets. Never render/simulate all by default.
2. **No explicit agent registry.** Roles/floors are not enough. Add a registry of agent ID, owner,
   tools, skills, model policy, capabilities, version, status, health.
3. **No A2A protocol.** MCP covers tools; agent-to-agent handoffs need typed A2A messages/contracts.
4. **No one-writer-per-file/resource rule.** Leases alone are not enough. Every mutable file,
   branch, shared record and append-heavy file (lockfiles, translations) gets one owner; others
   submit patches/fragments.
5. **No retry/loop/handoff ceiling.** Add max attempts, max handoffs, max delegation depth, no
   progress detector, circuit breaker and escalation.
6. **No per-tool eval/schema-drift canary.** Tool descriptions/schemas change silently; add pinned
   schema snapshots and at least one eval prompt per tool in CI.
7. **No separate verifier.** The orchestrator must not grade its own work; use an independent
   verifier with different prompt/model where practical.
8. **No “agents cannot edit the checks” rule.** Hooks/evals/approval policy must be outside agent
   write scope.
9. **No cost/wave ceiling.** Cap parallel agents per supervisor (e.g. 5) and global concurrency;
   token/retry budgets.
10. **No Tiled/map-pipeline decision.** The builder needs an import/export pipeline and validation,
    not just a runtime map schema. Tiled is a strong candidate.
11. **No renderer benchmark decision.** Phaser 4 GPU layers vs Pixi chunking must be tested with
    identical maps/sprites; don’t guess.
12. **No WebGL context-loss fallback.** Add recovery/static fallback; browser memory pressure is real.
13. **No dirty-render strategy.** Redraw only when state changes; reduce GPU/power for idle floors.
14. **No active-floor/portal/pathfinding budget.** Hierarchical A*, portal reservations, bounded
    replans, path cache.
15. **No command-center/search-first UI contract.** Browsing 100 floors cannot be the primary
    navigation.
16. **No separate presentation/authoritative event channels.** Movement/FX must not flood business
    event streams.
17. **No scale fixture before tower.** Build 100-floor/3,075-desk/1,000-agent synthetic fixture and
    benchmark before art.
18. **No device-tier release gate.** Desktop/low-end/mobile/context-loss tests required.

## 3. Required plan changes

### Platform

- Add **Agent Registry** to domain: agent ID, owner, department, tools, skills, MCP servers, model
  policy, capability grants, version, status, health.
- Add **A2A handoff contract** alongside MCP.
- Add **one-writer-per-resource** and append-only fragment protocol.
- Add **max attempts / max handoffs / max delegation depth / no-progress detector**.
- Add **per-tool eval + schema snapshot** in CI.
- Add **independent verifier** for high-impact work.
- Add **gate/check write protection** (agents cannot edit tests/eval/policy).
- Add **parallel wave cap** (start 5; configurable) and aggregate budgets.
- Add **activity granularity**: one external effect per activity; idempotency key; timeout; retry
  policy; no long multi-step activity.

### UI

- Command-center shell; global search; floor/agent/task/artifact navigation.
- Agent cards show task/status/attention/checkpoint/model/tool/cost; animation secondary.
- Notification/attention center; separate operational vs social/ambient events.
- Accessible semantic DOM mirror; keyboard; text-only floor; reduced motion.
- Mobile triage-first; builder desktop.
- Presence privacy and redaction.

### Maps/rooms

- Tiled import/export + versioned map schema, portal/connectivity validation.
- Chunked tilemaps, atlas manifests, object pooling, render-texture baking for static rooms.
- One active floor; visible-room prefetch; LRU atlas/chunk cache.
- Minimap/floor directory/elevator jump/search; no forced walking.
- Capability-bearing room cards; room modes.
- Dirty rendering; context-loss recovery; device tiers.

### World/scale

- Build scale fixture before tower art.
- Benchmark Phaser 4 GPU layers vs Pixi chunking with identical assets.
- Full data, active-set rendering; 283 agents do not all simulate continuously.
- Separate authoritative business events from ephemeral presence/FX.
- Budgets/gates from `QUBIT-OFFICE-IMPROVEMENTS-2026.md`.

## 4. Corrections to existing plan wording

- “Full from start” means **all data/features exist**, not all 283 agents render/reason at once.
- “All departments” means all records/templates exist; active-set policy still applies.
- “Temporal” means activities are idempotent and granular; unlimited retries are not acceptable.
- “Obsidian/OpenViking memory” means projections/adapters; event log authoritative.
- “Agent chat” means durable typed messages/A2A, not a global chat room.
- “Map builder” means Tiled-compatible import/export and validated publish, not free-form live edits.
- “Renderer” means benchmarked web 2D first; Godot after parity.
