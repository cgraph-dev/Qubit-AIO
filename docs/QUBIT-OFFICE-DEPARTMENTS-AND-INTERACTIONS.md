# Qubit Office — departments, sessions, memory and interactions

**Status:** design baseline. Not a release verdict, not `VERIFIED`, and not a claim that any
part is implemented. This file persists the owner's department/office/interaction model so it
cannot be lost in chat history.

Related authority: `docs/QUBIT-OFFICE-MASTER-PLAN.md`, `docs/IDENTITY.md`,
`docs/DECISIONS.md`, `docs/BRAND.md`.

---

## 1. Core model

**Campus → Department Office → Room → Cell → Desk → Agent**

- The current Qubit Office image (reception, kitchen, boardroom, desks, restrooms, lounge,
  review) is **one department floor**.
- Each additional department gets **its own floor** built from the same functional grammar,
  not a copy-paste of the exact same art and layout.
- Departments merge into one campus: floors/wings, elevator/stairs, atrium, shared corridors.
- A **department is an organisational unit**, not a business. A business may span departments
  or floors; one department may serve several businesses.
- **Special rooms/floors are shared services**, not departments: War Room, Memory Room,
  Ops/Approvals, Comms Hub, Server/Infra, Studio, Training/Auditorium, Lobby/Security,
  Cafeteria/Atrium/Garden/Outside.

## 2. Department floor blueprint

Every department floor is designed to be autonomous but coherent:

- Reception / entry
- Kitchen/kitchenette + break area
- Restrooms/bathrooms (and optional showers/changing)
- Lounge / quiet area
- Boardroom + meeting rooms
- Focus rooms
- Open desk area
- Department head office
- Storage/archive
- Comms/ops corner
- Optional lab/studio/library depending on department

Shared campus amenities may include a large cafeteria, auditorium, gym, medical room, garden
and outside world. Per-floor facilities remain so a department can function alone.

### Not identical copies

Each department floor uses a **floor template + variant**:

- Required zones and minimum capacities
- Layout variants by team size
- Palettes, furniture families, lighting, signage, clutter
- Deterministic floor seed
- Asset overrides
- Capacity/pathfinding/egress validation
- Unique room/desk/seat IDs and memory scopes

Floor 1 can be the Executive/Operations department shown in the reference image. Floors 2–N are
other departments. Special floors sit between/above them.

## 3. Floor/department builder

Owners need a real **Department/Floor Builder**:

- Create a floor from a template
- Add/remove rooms and desks
- Add cells within the fixed 16×12 cell contract (never stretch a cell)
- Add bathrooms, kitchen, lounge, reception, meeting/focus rooms
- Clone a floor or department
- Resize, merge or split departments
- Assign business, roles, agents, capabilities, memory scope
- Validate required rooms, doors, paths and capacity
- Preview, version, undo and approve
- Agents may propose changes; the owner/builder applies them

Agents must not freely rewrite live walls. A scoped build mode and approval path apply.

## 4. Agent sessions, workspaces and chat

Each agent needs a durable lane, not one global chat:

- `agent_id`, `department_id`, `room_id`, `desk_id`, role, capabilities
- Own session/thread in the canonical session store
- Own workspace: files, scratch, artifacts, skills, checkpoints, task queue
- Own worktree/sandbox where code work needs isolation
- Task/run checkpoints and append-only events; stop/resume; restart recovery
- Owner↔agent chat from the office
- Agent↔agent chat, room channels, mentions, handoffs through a department/office message bus
- Cross-floor/department messaging with permissions
- No silent completion: work survives restart and handoffs carry artifacts

### Work-loss rules

- Leaving for coffee/break queues the task.
- A fight/medical/HR event pauses the turn with a checkpoint.
- Quitting archives the session/workspace and creates a handoff.
- Every interaction writes an event/artifact/memory note.
- Restart resumes from the last checkpoint.

## 5. Memory: Obsidian + OpenViking (no Brain model integration)

Memory is the requirement; a model "Brain" is not being integrated.

- **Department Obsidian graph** — own vault folder/graph view: department MOC, rooms, agents,
  tasks, projects, deliverables, decisions.
- **Office-wide Obsidian graph** — links departments and special rooms into one graph; each
  department view remains filterable.
- **Agent notes** — each agent has a note with session summaries, decisions, artifacts and
  backlinks.
- **War Room / Memory Room** — cross-department notes, evidence, decisions and dissent.
- **OpenViking special folder** — scoped collections:
  `office/global`, `office/<department>`, `office/<department>/<agent>`.
- Writes are scoped, provenance-tagged and permission-checked; an agent cannot silently
  overwrite another department's memory.
- Obsidian and OpenViking connect through stable note IDs, backlinks and artifact references,
  not duplicated prose.

## 6. Interaction engine

Every action is data, not hardcoded:

```
ActionDefinition
  id, label, category
  room_types, props, required_agents
  preconditions (needs, mood, relationship, role, time, privacy)
  duration, cooldown
  animation, emote, particles, sound
  effects (energy, stress, morale, health, focus, reputation)
  relationship effects
  memory/note event
  risks/consequences
  approval/consent requirements
  mature_content flag
```

Agents choose actions through **needs + traits + relationships + context**:

- Needs: energy, hunger, thirst, hygiene, social, stress, focus, morale, health.
- Traits: introvert, ambitious, risk-taker, calm, aggressive, romantic, loner, joker,
  workaholic.
- Relationships: trust, respect, attraction, resentment, friendship, rivalry.
- Context: deadline, manager, room privacy, shift, recent events.
- A utility score selects the next action; cooldowns prevent loops.

## 7. Action catalog

**Work:** pair programming, code review, whiteboard, document review, deadline sprint,
overtime, take a call, mentor, onboard, interview, performance review, ask for raise, resign,
hand off work, archive workspace, train replacement.

**Social:** coffee, lunch, water-cooler chat, gossip, joke, compliment, argue, apologize,
reconcile, prank, toast, birthday, play RPS, board game, karaoke, watch TV, exercise together.

**Needs:** bathroom, wash hands, shower, snack, water, nap, smoke/vape, medication, sick leave,
burnout break.

**Conflict:** disagreement, argument, insult, shove, fight, report to HR, file complaint,
mediate, suspend, quit, get fired, transfer, retire.

**Romance / mature (opt-in):** flirt, ask out, date, hold hands, kiss, private encounter
(fade-to-black), breakup, jealousy, workplace romance policy.

**Risky / mature (opt-in):** alcohol, substance use, gambling, with health/legal/HR
consequences and support/recovery paths. No real-world instructions.

**Career/life:** promotion, demotion, transfer, resign, rehire, sick day, vacation, parental
leave, retirement, move desk, change department, start a side project.

## 8. Mature-content policy

- Mature content is **off by default** and owner-enabled per department.
- Sexual interactions are **non-graphic**: fade-to-black, consent/relationship state, no
  explicit depiction.
- Substance use is simulated with consequences; no dosing, sourcing or how-to.
- Fighting is non-graphic; no gore.
- Age-appropriate fictional agents only; content labels and moderation apply.
- Owner controls what a department/agent may do; audit records every mature event.

## 9. Consequences

Actions must change state:

- Stress, health, morale, focus, reputation.
- Relationships and department climate.
- HR cases, warnings, suspensions, terminations.
- Work delays, missed deadlines, handoffs.
- Memory notes and Obsidian links.
- Approval/consent requirements.
- Recovery/support paths, not only punishment.

## 10. Customization

- Action catalog editable per department/room/agent.
- Per-agent traits, needs, relationship preferences, animation set, voice, schedule.
- Per-room props and allowed actions.
- Per-department policy: romance, substances, fighting, privacy, HR consequences.
- Templates and overrides versioned with preview/rollback.

## 11. FX, maps and Godot

Use the FX repo event-driven, not random decoration:

- walking to room/prop, sitting, typing, thinking, talking
- coffee/smoke/vape particles
- argument/fight emotes, no gore
- private encounter: door close, light dim, fade-to-black
- medical/HR/security events
- elevator/floor transitions, weather/outside

Maps:

- Campus map
- Department floor map
- Room map
- Memory graph map

Render one floor at a time; preload adjacent floors; active departments animate, dormant floors
stay cached/static.

Godot is optional. It may render the campus/world if 2.5D/3D performance requires it, but it
must read the same API/state and never own business truth. Do not switch before the map schema
and one department work in the web renderer.

## 12. Repo patterns to reuse

- **Habblaud:** current room rendering, seats, room zones, agent presence.
- **SkyOffice:** multi-floor/map/character movement patterns.
- **pixel-agents:** pathfinding and agent state.
- **agent-monitor:** agent/session/status panels.
- **agents-office / claw-empire:** office/business simulation structures.
- **StarNet / Agent Quest:** department/business UI patterns.
- **FX repo/pixelfx:** ambient/working/thinking/speaking/approval/transition effects.
- **Godot:** optional renderer for a larger campus.

Licences and attribution apply; do not copy paid art or non-redistributable assets.

## 13. What broke the earlier plan

1. Treating the written plan as progress.
2. Building narrow testable slices (Etsy, War Room backend, Perplexity) instead of one complete
   department.
3. No asset/idea ledger, so assets and repo reviews had no path into the build.
4. Misunderstanding Perplexity as a product feature; it belonged to planning.
5. Building agent records before agent sessions/workspaces/memory.
6. Building rooms as directory data, not capability-bearing spaces.
7. No conversation fabric for owner↔agent or agent↔agent.
8. Not mapping repos to concrete behaviours.
9. Not using assets/FX for rooms, maps or outside world.
10. Calling backend existence "done".

## 14. Implementation order

1. Persist this model and the asset/idea ledger.
2. Build **one complete department floor** from the current office image:
   business record, rooms/assets, 10 agents, individual sessions/workspaces, owner chat,
   agent-to-agent chat, War Room and Memory Room links, department Obsidian graph, OpenViking
   scope, capabilities/permissions, event-driven FX, map, tasks/artifacts/checkpoints.
3. Prove it end-to-end with restart/recovery and no lost work.
4. Clone the template into the other departments/floors.
5. Merge the campus: shared floors, elevators, maps, outside world.
6. Add the builder panel, customization, mature actions and larger FX set.
7. Expand to the Business Factory and full-service corporation.

**First measurable milestone:** one department floor where agents work, chat, remember, use
tools, take breaks/interact, recover from restart, and leave artifacts — then clone it.

---

## 15. Character Studio — merge Habblaud editor + LimeZu generators

There must be **one** Character Studio, not three disconnected editors.

### Sources

- **Habblaud character editor/state:** existing character layers, animation states and preview
  surface in the vendored client. Character save is currently refused by the Qubit boundary;
  the canonical profile replaces that gate.
- **LimeZu Character Generator 2.0:** body, eyes, hair, outfit and skin parts; the measured
  catalog is body 9 / eyes 7 / hair 29 / outfit 33 / skin 9. Local-only paid art.
- **LimeZu Portrait Generator 1.5:** portrait parts/atlas for agent cards, controls and
  close-ups.

### Canonical profile

```
CharacterProfile
  profile_id, agent_id
  body_id, skin_id, eyes_id, hair_id, outfit_id, accessory_ids
  colors, layers, palette
  portrait_cell, portrait_size (32/48)
  animation_set, idle/working/talking presets
  seed, style_version
  source_assets (local paths/hashes, never shipped)
```

### Studio surfaces

- Live preview at world scale and portrait scale
- Part pickers with category counts and search
- Randomize / lock parts / undo / presets
- Layer ordering, colors, shadow policy
- Animation preview: idle, walk, type, talk, think, break, celebrate
- Save to the Business OS agent record; sync Habblaud character state and LimeZu
  portrait/atlas references
- Per-agent override, department defaults, role defaults
- Import/export JSON; versioned revisions
- Paid-art guard: no generated/paid asset is committed to the public tree; build locally

### Merge rule

Habblaud supplies the character **state/editor/animation contract**. LimeZu supplies the
**parts and portrait atlas**. The Qubit Character Studio is the only save authority. Do not
keep a second editor with a second save path.

## 16. Outside world / campus exterior

The outside world is a **data-driven campus map**, not a decorative backdrop.

- Ground, roads, sidewalks, parking, paths, trees, gardens, fences, props
- Building exterior/facade, roof, entrance, windows, signage
- Lobby/security entrance connecting exterior → department floors
- Weather, day/night, seasonal variants
- Visitors/vehicles/ambient life as presentation only
- Campus map, department entrances, elevator/stairs
- Chunks/tiles so the map can grow without loading everything
- Active/dormant exterior zones; only active areas animate
- Asset licensing recorded per pack; paid LimeZu/other art stays local

### Renderer

Web/Phaser can render the first exterior map. Godot is optional for a larger 2.5D/3D campus;
it reads the same map/state API and owns no business truth. Do not switch before the map schema
and one department floor work.

## 17. Full panel inventory

Panels must be reconciled against Habblaud, Agent Quest, StarNet, agent-monitor and the office
shell. At minimum:

**Agents/work**
- Agents roster, Agent Controls, Agent Chat, Agent Session, Agent Terminal
- Deliveries, Timeline/Timelapse, Tasks, Jobs, Artifacts

**World**
- Campus map, Department map, Room map, Floor directory
- Room Builder, Department Builder, Character Studio, Wardrobe
- Weather/time, Visitors, Exterior

**Business**
- War Room/Idea Lab, Memory Room, Approvals, Ops cockpit
- Ledger, CRM/leads, customers, marketing/social, Etsy, reports, KPIs

**System**
- Models/providers, tools/toolsets, MCP, plugins, skills, budgets, audit, security
- Notifications/inbox, search, settings, activity

Every panel needs: data authority, permissions, empty/error states, real actions, and a
capability mapping. No panel may invent state.

## 18. Capability catalog

Agents get the **full Qubit capability catalog**, not a hardcoded research provider.

- Qubit tools and toolsets
- MCP servers and tools
- Plugins and skills
- Terminal/command execution
- Browser/CDP and web search
- Files/read/write/search
- Messaging/channels/email
- Connectors and platform integrations (Etsy/social/CRM/payments)
- Memory: Obsidian and OpenViking
- Agent-to-agent/owner messaging
- Model/provider selection with fallback

Each capability has: id, category, risk, required scopes, approval policy, budget/rate limits,
provider/availability, audit behaviour. Per-agent grants are explicit; no wildcard defaults.
War Room, research, council and business workflows call this catalog; they do not call a
specific provider.

## 19. Asset ledger

Every asset gets a record before use:

- pack/file, sha256, dimensions, license, local-only/paid flag
- intended department/room/style/prop/character slot
- extracted/sliced status, atlas/frame coordinates
- used-by rooms/agents/panels
- provenance/attribution

Assets remain outside the public tree when the license forbids redistribution.

## 20. Coverage status

| Area | Design coverage | Implementation |
|---|---|---|
| Department floors, rooms, facilities | Covered | Partial: 4 live rooms, 100-floor room directory |
| Department/floor builder | Covered | Not built |
| Room styles/LimeZu variation | Covered | Partial: one theme, six floor surfaces |
| Agent sessions/workspaces | Covered | Not built |
| Owner↔agent / agent↔agent chat | Covered | Not built |
| Obsidian department + office graph | Covered | Not built |
| OpenViking scopes | Covered | Not built |
| Interaction/action engine | Covered | Not built beyond coffee/RPS |
| Mature-content policy | Covered | Not built |
| FX/animation mapping | Covered | Loading weather only |
| Maps/campus/exterior | Covered | Not built |
| Godot option | Covered | Not built |
| Character Studio merge | Covered here | Not built |
| Full panel inventory | Covered here | Partial: Agents/War Room/Approvals/Etsy |
| Capability catalog | Covered here | Partial: permission data, no gateway |
| Asset ledger | Covered here | Not built |
| Business/room/agent implementation | Covered | 1 business, 283 records, no operations |

**Nothing above is `VERIFIED`; the table is a scope map, not a completion claim.**

---

## 21. Agent configuration — what an agent can select and use

**Decision:** the agent is the security principal. Desk, room and department are presentation and
organisation metadata; they never grant access.

### AgentProfile

```
AgentProfile
  agent_id, owner_id, display_name
  department_id, room_id, desk_id, role_id
  security_principal_id
  model_policy { primary provider/model, reasoning, fallback chain, context/latency/data rules }
  capability_grants[] { capability, resource, action, data_class, environment, expires_at, approval }
  skills[] { skill_id, version, binding source }
  mcp_servers[] { server_id, version, tool allow/deny, health }
  memory_scopes[] { scope, read/write/append, retention }
  secret_refs[] { secret_id, allowed tools, expiry }
  autonomy_ceiling, budgets, rate limits, concurrency
  session_policy { owner, visibility, retention, default workspace }
  appearance { CharacterProfile id, portrait, voice, animation preset }
  policy_revision, status, created_at, retired_at
```

### Selector groups in the UI

1. **Identity/ownership** — immutable owner, department assignment history, security principal,
   session owner, cross-department grants with expiry.
2. **Permissions** — deny-by-default; selectors for subject, capability, resource, action,
   environment and data classification. Separate read / draft / execute / approve / administer /
   delegate. Deny wins. Time/task/approval-bound grants.
3. **Skills** — versioned, schema-bound, selectable by agent/role/department/task/risk. A skill
   can **require** capabilities but cannot grant them. Pin the skill revision per run.
4. **MCP and tools** — server allowlist per agent/session, then tool-level allow/deny within each
   server. Catalog shows health, version, risk, scopes, approvals and budgets. One gateway.
5. **Models/providers** — per-agent primary model/provider, per-task policy (reasoning, context,
   latency, data residency), fallback chain pinned at run start; model permissions separate from
   tool permissions; budget-aware downgrade and human escalation.
6. **Autonomy and budgets** — per-agent ceiling; per-run tokens/wall-clock/tool calls/concurrency/
   money; department and office aggregate budgets; provider/tool/agent rate limits; dead-man
   switch and automatic pause on anomalous spend.
7. **Memory scopes** — agent-private, session/run, department, office, task-scoped shared context;
   explicit read/write/append per scope; provenance, visibility, retention, conflict metadata.
8. **Secrets** — vault only; gateway injects the one credential a call needs; no secrets in
   prompts, workspaces, memory, logs, checkpoints or artifacts; immediate rotation/revocation.
9. **UI controls** — effective capabilities, inherited vs explicit grants, model/provider, memory
   boundaries, active sessions/leases, budget reservations, approvals and recent policy decisions;
   diff, simulate, start/pause/stop/revoke/archive/delegate.

## 22. Session, run, task, workspace and checkpoint model

**Decision:** durable session, bounded run, durable task, pinned workspace, checkpointed effects.

| Component | Purpose | Key fields | Lifecycle |
|---|---|---|---|
| Agent principal | Stable identity/accountability | agent_id, owner, department, role, autonomy ceiling, policy revision, model policy | provision → approve → active → suspended/revoked → archived |
| Durable session | Long-lived owner↔agent lane | session_id, agent_id, owner, participants, visibility, memory scopes, workspace, status, last checkpoint | create → active → paused → resumed → archived |
| Run/turn | One bounded execution attempt | run_id, session_id, task_id, parent_run, model/provider, prompt revision, capability snapshot, budget snapshot, lease, status | queued → leased → executing → waiting/approval → checkpointed → completed/failed/cancelled/unknown |
| Task | Durable work that can move between agents | task_id, objective, acceptance criteria, priority, owner, assignee, dependencies, required capabilities, data class, budget, deadline, artifacts | draft → assigned → claimed → blocked → handed off → completed/rejected/cancelled |
| Checkpoint | Restart/handoff recovery | checkpoint_id, run_id, sequence, state summary, pending actions, tool receipts, artifacts, memory writes, approvals, workspace revision, safe resume point | write before/after side effects; validate policy before resume; old checkpoints readable |
| Workspace | Durable files/artifacts | workspace_id, owner scope, task_id, session_id, repository/worktree ref, storage class, access policy, revision, manifest, retention | provision → active → checkpointed → frozen/handed off → archived/deleted |
| Sandbox/worktree | Execution isolation | sandbox_id, workspace_id, base revision, branch, filesystem/network policy, resources, secret bindings, execution log | create pinned → execute under lease → commit artifact → destroy/read-only |
| Capability snapshot | Freeze effective policy | snapshot_id, agent, policy revision, grants/denials, tool/server versions, model constraints, expiry | materialize at run start; every call checks it; revocation invalidates immediately |
| Message/event log | Canonical interaction record | event_id, conversation, sender, recipients, correlation, causation, type, payload ref, visibility, idempotency, timestamp | append → deliver → ack/dead-letter; replayable |
| Handoff | Transfer without losing context | handoff_id, task, from, to, reason, checkpoint, artifacts, unresolved questions, required capabilities, deadline | proposed → accepted/rejected → ownership transfer; source read-only |
| Policy/budget ledger | Explain and enforce limits | policy revision, grant, budget, subject, scope, counters, reservations, approver, expiry | draft → approved → active → amended/revoked → retained |

### Work-loss and recovery rules

- Checkpoint before and after side effects; record tool receipts and workspace revision.
- Resume only from a validated checkpoint; revalidate policy and capability snapshot.
- Lease every run/task/workspace; heartbeat and fencing token; expiry stops execution.
- Unknown provider outcomes are reconciled, never marked complete.
- Stop revokes leases/tokens, interrupts gateway calls, writes a checkpoint and surfaces unknowns.
- Handoff carries artifacts, unresolved questions, acceptance criteria and read-only source state.

## 23. Multi-agent collaboration patterns

| Pattern | Use | Avoid | Safeguards |
|---|---|---|---|
| Supervisor + bounded delegation | Lead decomposes work | Inheriting all child authority or self-approval | typed subtasks, budgets, data class, deadline, acceptance, parent ceiling, audited/revocable |
| Work queue + leases | Independent tasks | Shared mutable task state / first responder wins | atomic claim, expiry, heartbeat, fencing, retry, dead letter |
| Artifact collaboration | Research, coding, review, docs | Passing raw long chat history | immutable artifacts, typed summaries, citations, read scopes, validation |
| Reviewer/council | High-impact decisions | Many agents writing the system of record / voting on facts | independent contexts/budgets, dissent, one commit authority, human approval |
| Pairing | Coding/docs | Concurrent writes to one branch/filesystem | single-writer lease; others propose patches; conflict tests |
| Shared department context | SOPs, decisions, status | Unrestricted scratchpad | typed namespaces, append-only decisions, provenance, retention, ACLs |
| Office broadcast/channels | Announcements, incidents, cross-floor requests | One global chat with every secret | topic ACLs, recipient filtering, rate limits, threading, receipts, size limits |
| Conflict resolution | Competing edits/recommendations | Last-write-wins | commit authority, version checks, deterministic priority, owner escalation, evidence, merge/reject events |

**Anti-patterns:** desk/department as permission boundary; one global chat; one mutable workspace
shared across tasks; wildcard MCP access; skills that grant capabilities; fallback without pinned
policy; checkpoint as chat summary only; global memory retrieval; concurrent worktree writers;
delegation by natural-language request alone; last-write-wins; stop that only hides the agent;
secrets in prompts/workspaces/logs; office UI as source of truth; unbounded fan-out;
resume-last-message instead of validated checkpoint.

## 24. Repo patterns — verified evidence

- **agents-office:** per-agent roster fields (name, role, does, tools, brief, model, effort),
  MCP allow/deny/departments, team limits, skills bound to agents/departments, fixed seats.
  Take: selector model and skill binding. Avoid: local JSON as authority, fixed 35 seats, full MCP
  list by default, a Brain folder as security boundary.
- **agent-monitor:** multi-agent meeting UI, WebSocket chat, session/sub-agent grouping, agent
  cards. Take: session inspection/grouping and meeting UI. Avoid: simulated chat as durable task
  state, authorization or execution control.
- **agent-quest:** per-session agent state manager, subagent detection, idle/busy/completed
  lifecycle, live-session oracle. Take: session identity and subagent lifecycle. Avoid: inferring
  provider architecture beyond the observed state manager.
- **pixel-agents:** lead + teammate relationships, inline vs separate tmux sessions, promoted
  background agents. Take: lead/teammate identity and routing attention. Avoid: assuming runtime
  persistence it does not prove.
- **claw-empire:** isolated git worktrees, departments, meetings, deliverables, skills library,
  agent management, encrypted provider/messenger tokens, active-agent kill. Take: worktree
  isolation, department UI, token encryption and kill control. Avoid: copying its database/model
  assumptions without checking.
- **SkyOffice:** bounded room chat (max 100 messages), room/player commands. Take: room chat
  envelope. Avoid: chat room state as the canonical conversation store.

## 25. Perplexity review — 15 agent/session suggestions

| # | Suggestion | Why | Do | Phase | Effort | Risk |
|---|---|---|---|---|---|---|
| 1 | Define canonical agent runtime contract | Records exist without enforceable lifecycle/ownership | Agent, Session, Run, Task, Workspace, Checkpoint, Handoff, CapabilitySnapshot, Message, Lease schemas + transitions/idempotency | 0 | M | M |
| 2 | Build one durable agent lane before more floors | Must prove runtime durability, not UI presence | One agent: owner chat, task queue, gateway calls, checkpoint/restart, artifact, scoped memory | 1 | L | L |
| 3 | Make ToolGateway mandatory | Catalog/permissions are not an execution boundary | Every tool/MCP/terminal/connector/memory/model/delegation call passes through it | 1 | L | L |
| 4 | Replace static capability lists with effective-policy evaluation | Selectors/inheritance without wildcard privilege | Grants on subject/capability/resource/action/data/environment/expiry; explainable allow/deny | 1 | L | M |
| 5 | Add dynamic capability catalog with health/versioning | Provider calls must not be hardcoded | Catalog servers/tools/skills/models/providers/connectors/memory adapters with schema/risk/scopes/health/version | 1 | M | M |
| 6 | Implement leases and fencing before concurrency | Retries/multiple agents create duplicate writes | Task/workspace/run leases, heartbeats, fencing tokens, expiry, single writer | 1 | M | L |
| 7 | Separate durable sessions from bounded runs | One global chat rejected; durable lane needs bounded runs | Session context durable; run gets frozen policy/model/budget and checkpoint chain | 1 | M | L |
| 8 | Pin workspaces/worktrees to tasks | Agent-owned workspace alone contaminates tasks | Agent base + task overlay; pinned revision; filesystem/network isolation; immutable artifacts; single writer | 1 | L | M |
| 9 | Implement explicit handoff protocol | Handoffs undefined | Checkpoint, artifact manifest, unresolved questions, capability requirements, recipient acceptance, source read-only | 1 | M | L |
| 10 | Unify Obsidian/OpenViking authorization | Two memory systems can diverge in ACL/retention/provenance | One memory policy service; typed scopes; note/artifact IDs; visibility/retention/conflict | 1 | L | M |
| 11 | Add real message fabric | Chat proposed, no substrate | Append-only events with topics/ACLs/threading/delivery/ack/correlation/idempotency/retries/dead letters/replay | 1 | L | M |
| 12 | Make revocation interrupt execution | Future-run-only revocation is insufficient | Short-lived capability tokens; gateway checks per call; kill leases and secret bindings on stop/policy change | 1 | M | L |
| 13 | Effective-state and audit UI | Configuration without operational control | Show effective permissions/inherited sources/model/memory/workspace/leases/reservations/approvals/decisions; diff/stop/revoke/archive | 2 | M | L |
| 14 | Prompt-injection/untrusted-content boundaries | Agents read files/web/MCP/messages/memory | Label provenance/trust; isolate retrieved content from control instructions; confirm instruction-derived capability changes; redact; tools cannot change policy | 1 | M | M |
| 15 | Test failure/collaboration semantics | Restart/handoff/concurrency/revocation/unknown outcomes are core | Crash after side effect, lease expiry, duplicate delivery, mid-run revocation, stale checkpoint, conflicting patch, poisoned memory, fallback, budget exhaustion, child failure | 1 | L | M |

**Decision:** build the first agent lane using this contract before expanding the office, character
studio, world or department count. Agent configuration is useful only when the gateway enforces it.
