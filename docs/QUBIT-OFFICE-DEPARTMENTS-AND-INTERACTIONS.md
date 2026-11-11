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
