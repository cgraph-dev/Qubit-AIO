# Qubit Office — Master Plan

**Status:** planning baseline plus waves 1–5 and live seat/tower/approvals deployment on `:8890` from `integration/qoffice-wave1`. Not a release verdict, not `VERIFIED`, and not a claim that any phase is complete. `main` is still unmerged.
**Execution mode:** live deployment performed with the owner's explicit `do it` (2026-11-11); main merge and release gates remain separate.
**Scope:** Qubit Office (the campus/office product) + Qubit Business OS (the durable business-operations layer).
**Rule:** the world is complete from day one; only active businesses/rooms are simulated. Business truth lives in systems of record, never in the renderer.

---

## 1. Vision

Qubit Office is the operator's control room for a company of human-directed AI workers.

- Every business, wing, room, desk, agent and task has a real record.
- The office shows real state; it never invents work.
- Agents do throughput; humans own thresholds (money, law, contracts, irreversible actions).
- A business can be created from a playbook, then operated and improved 24/7.
- The world is fully built; only the active businesses animate.
- The product is **Qubit Office** to users. Habblaud-derived code is a vendored component, not a product brand.

---

## 2. Product principles

1. **Authoritative state vs presentation state.** Business records, balances, tickets and approvals are authoritative. Animation, walking, lights and effects are presentation.
2. **Full static world, sparse simulation.** All wings/rooms/suites/desks exist from the start. Unused rooms are furnished and dormant; only active businesses materialize agents.
3. **Agents propose; systems commit.** The tool gateway, policies, ledgers and systems of record decide what happened.
4. **Human approval by consequence.** Payments, contracts, legal, hiring, irreversible data changes, customer commitments and credential changes require approval.
5. **Durable by default.** Work survives browser close, process restart, deploys and delayed approvals.
6. **No fake success.** No agent "completion" without an artifact, row, transaction, message, ticket or provider receipt.
7. **Copy/adapt before inventing.** Reuse proven UI/office code and infrastructure; write glue, not a new engine.
8. **One source of identity.** Stable IDs everywhere; names are display attributes.
9. **Unique rooms, coherent style.** Rooms differ by archetype, palette, furniture family, seed, signage and lighting, constrained by a shared visual grammar.
10. **Personal first, but honest.** Private/local use may relax distribution gates; real businesses still require legal/accounting/security discipline.

---

## 3. Two products on one foundation

### Qubit Office (World/control room)
The campus, rooms, desks, agents, office UI, live status, approvals, terminal, deliveries, timelapse, character editor, room styles and World/city.

### Qubit Business OS
Durable workflows, business objects, systems-of-record adapters, approvals, budgets, audit, KPIs, playbooks, business creation and maintenance.

**Rule:** the office is an interface to the Business OS. It is not the database.

---

## 4. Naming and white-label policy

### User-visible product
- Name: **Qubit Office**.
- Remove every user-visible Habblaud name, logo, title, iframe label, PWA name, loading copy, error copy, About copy, docs and default configuration from product surfaces.
- Centralize brand strings and asset references; do not hard-code names.
- Rename the app surface, loading screen, title and accessible labels to Qubit Office.

### Legal/provenance (must stay)
- Keep the original MIT copyright and permission notice.
- Keep a `THIRD_PARTY_NOTICES` entry naming the vendored Habblaud component, upstream commit and local changes.
- Keep provenance/SBOM records. Do not replace a copyright holder with "Qubit Office".
- Attribution may live in About/legal/THIRD_PARTY_NOTICES rather than the main UI.

### Technical migration
- Phase 1: inventory every `habblaud` reference; classify UI, public API, private code, tests, assets, provenance.
- Phase 2: add canonical `/api/office/*` routes and keep `/api/office/habblaud/*` as compatibility aliases until all callers migrate.
- Phase 3: rename internal directories/packages only where safe; keep aliases documented.
- Do not do a blind global replacement. A grep that finds a name does not prove which side reads it.

---

## 5. World architecture

### Hierarchy
```
Campus
  Wing / Zone
    Logical Room
      Cell (16×12 Habblaud-compatible room primitive)
        Desk / Seat
          Agent presence
    Business / Department
```

- Existing 3 "floors" become **3 wings**; preserve `legacyFloorId` for compatibility.
- No new vertical floors. Expand horizontally.
- Rooms needing more than 6 desks use **cell clusters**: 2 cells = 12 desks, 3 = 18, 4 = 24+; never stretch a 16×12 cell.
- Each cell keeps original furniture/seat/path geometry; the logical room aggregates cells.
- Seat IDs are namespaced (`roomId:cellId:localSeatId`).
- `characterMode: single | multi`: single preserves classic project-room behavior; multi supports Qubit departments.

### Active/dormant model
Every room has separate layers:
1. Authoritative business record — always exists.
2. Static presentation — always exists (walls, furniture, signs, lights off/on state).
3. Simulation actors — only when active.
4. Presentation proxy — labels/lights/silhouettes when distant.

States:
```
AUTHORED → LOADED_STATIC → OBSERVED → PRESENTATION_ACTIVE
        → SIMULATION_ACTIVE → DORMANT → REHYDRATING
```

Tiers:
- Tier 0 dormant: static room, business advances via events/timers.
- Tier 1 aggregate: headcount/queue/KPIs, no agents.
- Tier 2 low-rate: important agents update slowly.
- Tier 3 proxy: lights/labels/silhouettes.
- Tier 4 full: visible/nearby agents with full animation/pathfinding.

### Non-negotiable rules
- Business outcomes never depend on whether a room is visible.
- Dormant rooms retain state, schedules, approval queues and pending events.
- Re-entering a room reconstructs visuals from authoritative state.
- Active set uses camera + interaction + urgent events + deadlines, not camera alone.
- Static geometry cached per room/wing; active rooms full; neighbors simplified; occluded rooms not drawn; minimap from data.

---

## 6. Room style system (LimeZu)

### Source
Modern Interiors is a modular library, not one fixed tileset: thousands of furniture variants, theme sorter folders, modular floors/walls/rugs/tables, 16/32/48 sizes, shadowless/black-shadow variants, 100+ animated objects, character/portrait parts.

### Rules
- Use **16×16 as the world baseline**; do not mix 16/32/48 inside one room. Use 32/48 for portraits/close-ups/UI.
- Pick one shadow policy per scene (shadowless or black shadow), not mixed.
- Respect single-direction furniture: place against walls/sockets; do not rotate arbitrarily.
- Verify asset completeness per size; fall back to 16×16.
- No paid art committed; local builder only; credit LimeZu.

### Room style recipe
```json
{
  "archetype": "support_hub",
  "palette": "cool_teal_neutral",
  "floor": "carpet_dark",
  "wall": "panel_light",
  "rug": "teal_accent",
  "furniture_set": "support_desks_modern",
  "lighting": "cool_overhead",
  "shadow": "shadowless",
  "decor": ["headsets", "queue_board", "plants"],
  "seed": 184729,
  "style_version": 1
}
```

### Archetypes
Executive boardroom, open office, reception, support hub, sales floor, finance/accounting, marketing/content studio, engineering/control room, research/library, meeting room, kitchen/break room, wellness/quiet room, showcase/museum, server/ops, legal/compliance, training/classroom, fulfillment, media/TV studio.

### Composition
1. Functional shell (door, windows, service wall).
2. Required anchors (desks, reception, table, storage).
3. Secondary furniture.
4. Decor, signage, decals.
5. Lighting.
6. Validate walkability/capacity/readability.

### Variation
- Deterministic `room_seed = hash(business, wing, room, style_version)`.
- Independent streams: shell, furniture, palette, clutter, decals, lighting.
- Vary secondary props, rug, plants, posters, accent, desk template.
- Never vary doors, required anchors, main circulation, capacity, collision, only light.
- Curated palettes (60/30/10); no random sprite soup.
- Visual goldens per archetype/palette; reject wrong-theme props and clutter.

### Asset catalog
Every asset declares: theme, category, size, footprint, z-order, wall/floor, orientations, animation, shadow variant, tags, compatibility. Build the catalog from the purchased pack; never commit the art.

---

## 7. Loading screen + weather

### UX
- Qubit Office branded loading screen.
- User-supplied background image (placeholder now), with readable overlay.
- Progress/status, retry/error, tips, accessibility announcements.
- Smooth cross-fade to the office when the first real state is ready.

### Weather
- Source: Open-Meteo or configured weather; server/backend normalized snapshot; cache 10–30 min; fallback if unavailable.
- Never block loading on weather.
- Normalized states: clear-day, clear-night, partly-cloudy, cloudy, rain, storm, snow, fog, fallback.
- Map one effect at a time:
  - clear-day → `weather/god-rays`
  - partly-cloudy/cloudy → `weather/clouds`
  - rain → `weather/rain`
  - storm → `weather/storm`
  - snow → `weather/snow`
  - fog → `weather/fog`
- Do not combine rain+storm; storm already contains rain/lightning.
- Hysteresis/thresholds to prevent flicker.

### Pixelfx
- Vendor only the needed weather effects from `patch-gg/pixelfx` (MIT), with commit/provenance.
- Fixed 320×180 frame, scaled up with pixelated rendering.
- Layers: background image → tint/vignette → back effects → UI/progress → front effects.
- `createFrame`, `createFx`, `update`, `draw('back'/'front')`, `composite`, `putImageData`.
- Performance: VFX compositor ≤4 ms/frame target; loading total ≤8 ms mid-range; no blocking >50 ms startup; weather request timeout 1–2 s.
- Reduced motion: disable/soften; low-power/mobile fallback.
- Cleanup on unmount; no effect leaks; no network on every frame.

### Background
- Placeholder CSS/SVG now; configurable `office.loading.background`; user replaces with final art.
- Keep text contrast and safe areas.

---

## 8. Business OS

### Core rule
Agents may reason; systems of record decide, commit and prove what happened.

### Layers
1. Control plane: businesses, legal entities, playbooks, roles, tools, policies, budgets, approvals, feature flags.
2. Runtime plane: durable workflows, event bus, timers, queues, leases, retries, dead-letter, human tasks, tool gateway.
3. Systems of record: CRM, orders, payments, invoices/accounting, support, documents, email, website, analytics.
4. Agent layer: typed roles with objective, inputs, tools, scopes, spend/customer limits, approvals, escalation, timeout, model fallback, evals, owner.
5. Human operations: approval cards with evidence, cost, consequences, reversal, expiry; approve/reject/edit/delegate/escalate/stop.
6. Observability/evals: workflow/run/agent/model/prompt/tool/policy/approval/provider IDs, costs, latency, outcome, human intervention, KPIs.

### Business objects
Business, legal entity, brand, offer, product, lead, customer, opportunity, order, subscription, invoice, payment, refund, contract, campaign, ticket, task, project, run, artifact, approval, ledger entry, KPI, incident, SOP/playbook, agent, role, shift, review.

### Autonomy
- L0 read/summarize.
- L1 draft.
- L2 recommend.
- L3 reversible action with caps.
- L4 money/customer commitment with approval.
- L5 legal/contracts/hiring/irreversible — human/dual control.

### 24/7 requirements
- Durable execution (Temporal or equivalent), deterministic orchestration.
- Idempotency keys, retries, timeouts, dead-letter, reconciliation.
- Unknown outcomes are a distinct state; never blindly retry money/email/CRM writes.
- Timers/schedules/heartbeats/dead-man's switch.
- Provider/model fallback, rate limits, cost caps, budget ledger.
- Backups, restore drills, migrations, canary/blue-green.
- Human escalation/on-call and incident runbooks.
- Audit/reason-of-record.

### Business creation pipeline
1. Research/validate opportunity.
2. Business model/unit economics.
3. Legal entity/tax/bank/payments (human).
4. Brand/domain/website.
5. Offer/product.
6. Marketing/sales.
7. Onboarding/delivery.
8. Support.
9. Bookkeeping.
10. KPI review/improve.
11. Clone/scale playbook.

### Business maintenance/improvement
Weekly business review, anomaly detection, cost/API/schema drift monitors, experiments, pricing/content refresh, support quality, postmortems, SOP updates, maintenance agents, canary changes.

---

## 9. Workstreams

### A. Brand and experience
Rebrand to Qubit Office; loading screen + weather; design system; accessible states; docs/metadata; legal notices.

### B. World and rendering
Full static campus; wings/rooms/cells/clusters; active/dormant state; culling/LOD; minimap/camera; wall/door/furniture rendering; room style integration.

### C. Room styles and assets
LimeZu asset catalog; archetypes; palettes; furniture sets; seeds; validation; goldens; local pack builder; credit/provenance.

### D. Business OS
Durable workflows; systems of record; CRM/payments/support/accounting/email; approvals; budgets; audit; KPIs; pilot business.

### E. Agent operations and panels
Roles; task board; context/skills; meetings; analytics; approvals; deliveries; terminal; command palette; export; notifications.

### F. World / self-host
Self-hosted World: single-user → invite-only → social; city/chunks/visits; auth/privacy/validation; later.

### G. Quality, security, release
Threat model; sandbox; tenant isolation; idempotency/reconciliation; performance; backups/DR; tests; full matrix once.

---

## 10. Phases and gates

### Phase 0 — Decisions and inventory
- Pick pilot business, legal owner/entity, jurisdictions.
- Define systems of record, approval thresholds, budgets, hosting, privacy.
- Inventory all Habblaud references; license/SBOM; baseline screenshots/tests.
- Freeze brand policy and legal notice placement.
**Gate:** signed decisions + inventory + baseline.

### Phase 1 — Brand, loading, foundation
- Visible Qubit Office rebrand; loading screen with placeholder background.
- Weather provider + pixelfx weather effects; reduced-motion/fallback.
- Central brand config; PWA/title/iframe/About/docs.
- Canonical API route plan + compatibility aliases (no breaking rename yet).
**Gate:** no visible Habblaud brand; loading works offline/online/reduced-motion; tests.

### Phase 2 — Full static world
- Full campus: all wings, rooms, suites, desks, cells/clusters.
- Room style system + catalog + archetypes + goldens.
- Active/dormant state machine; static caches; culling/LOD; minimap/camera.
- Multi-agent room mode; no vertical floors.
**Gate:** full world loads; only active rooms simulate; style QA; performance budgets.

### Phase 3 — Business OS foundation + pilot
- Postgres + durable workflow engine + queues + secrets + object storage.
- CRM/payments/support/accounting/email/website adapters.
- Approvals, budgets, audit, tool gateway, reconciliation.
- One pilot business with one real workflow end-to-end.
**Gate:** pilot runs 24/7, survives restart, real artifact/payment/receipt, human approval.

### Phase 4 — Agent workforce and panels
- 3–5 roles: Sales, Support, Delivery, Finance, Chief of Staff.
- Task board, meetings, skills, context, analytics, deliveries, terminal.
- Active/dormant visuals; no fake activity.
- Maintenance agents, cost/schema monitors.
**Gate:** real business work visible and actionable in the office.

### Phase 5 — Business Factory
- Playbooks/templates; create business → suite activates; isolated data/budgets/books.
- Business lifecycle: launch, operate, maintain, improve, pause, close.
- Clone/compare; shared services.
**Gate:** second business created from playbook and operated independently.

### Phase 6 — World, character, FX
- Self-hosted World: single-user → invite-only → social; city/chunks/visits.
- Character editor/portraits; LimeZu persona profile; pixelfx VFX; seasonal/day-night.
**Gate:** optional World works locally; office remains primary; no privacy leaks.

### Phase 7 — Hardening and release
- Threat model, sandbox, tenant isolation, DR, load/failure tests, evals.
- One full matrix at the end; live deploy + rollback.
**Gate:** release evidence; `VERIFIED` only then.

---

## 11. Acceptance criteria

- **Brand:** no user-visible Habblaud; MIT notice/provenance retained.
- **Loading:** weather-driven pixelfx effects; placeholder background; works offline/cached/reduced motion; no blocking.
- **World:** all rooms exist from start; only active businesses simulate; dormant state persists; no business outcome depends on visibility.
- **Rooms:** every room has a style recipe; unique but coherent; 16×16 baseline; catalog/validation/goldens; no paid art committed.
- **Multi-agent:** room clusters 9/12/18/24+; seat IDs; capacity enforced; pathfinding hierarchical.
- **Business OS:** durable workflow, idempotency, unknown-outcome handling, approvals, audit, budgets, systems of record.
- **24/7:** restart/reconnect/failover/backups/on-call; no lost committed work.
- **Office UI:** real state only; no phantom agents; approvals/messages/terminal/deliveries functional or explicitly gated.
- **Security:** tool gateway, least privilege, sandboxed terminal if exposed, tenant isolation.
- **Release:** focused tests per phase; one full matrix at the end; `VERIFIED` only with evidence.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Full world feels empty | Furnished dormant rooms, lights/status, business suites, “available” signage; do not fake agents |
| Rebrand breaks APIs/tests | Inventory; canonical routes + aliases; do rename after parity |
| MIT notice removal | Keep copyright/license/provenance; hide brand only |
| Room style becomes random soup | Archetypes, palettes, furniture sets, sockets, validation, goldens |
| 16/32/48 mixing | 16 for world; 32/48 for portraits/close-ups; one shadow policy |
| Giant world performance | Static caches, chunks, culling, LOD, active set, low-rate/dormant tiers |
| Active set pauses business | Durable workflows independent of view; events continue off-screen |
| Terminal escape | Local-only/sandbox, no secrets, quotas, audit; human approval |
| Self-hosted World scope | Single-user → invite-only → social; separate phase |
| Agent errors/hallucination | Artifact grounding, evals, approvals, human review |
| Money duplication | Idempotency, provider IDs, reconciliation, unknown state |
| Legal/contracts | Human/dual approval; owner/legal entity; insurance |
| Cost drift | Budgets, cost auditing, cheaper models, caps |
| Unlicensed assets | Local-only paid art; notices; no redistribution |
| “Full from start” scope creep | Full static world/data, one live pilot; don't fake all businesses |

---

## 13. Decisions required

1. Pilot business type and offer.
2. Legal owner/entity/jurisdiction/tax.
3. Systems of record (CRM, accounting, payments, support, email, website).
4. Approval thresholds (money, contracts, refunds, customer commitments, hiring).
5. Autonomy levels per role at launch.
6. Budgets (models, APIs, infra) and cost caps.
7. Hosting model (local/VPS/cloud), backups/DR, on-call.
8. World: local-only, invite-only, or public self-hosted; official remote?
9. Room style baseline (16×16) and initial archetype set.
10. Active-set policy (camera, business events, deadlines, notifications).
11. Licensing posture (personal/private vs future distribution).

---

## 14. Reporting and evidence

- Each workstream commits in its own worktree; no live deploy without approval.
- Focused tests per change; no full matrix before implementation freeze.
- Record evidence in `docs/STATUS.md`; update `docs/OPEN_THREADS.md` for in-flight work.
- Never claim `VERIFIED`, `M1`, `M2`, `COMPLETE`, or 100% without the final gate.

---

## 15. Current state (reference)

- Existing Qubit Office has a vendored Habblaud 0.11.0 client, Qubit shell fallback, office theme/floor surfaces, agents panel, seat model, HUD, tower/store, and a large test suite.
- Upstream Habblaud is 1.0.3; re-vendor is a prerequisite for local 1:1.
- Current Qubit compat has terminal disabled, character save refused, deliveries empty, World absent.
- LimeZu Modern Interiors full pack is available locally; only recipe/builder may be committed.
- No implementation of this master plan has started.

---

## 16. Plan review v2 — corrections that must be built into the plan

| # | Risk / correction | Required plan change |
|---|---|---|
| 1 | "Full world" confused with "full simulation" | Full static world from day one; only active businesses/rooms get agents. Dormant rooms keep records, schedules, queues and approvals. |
| 2 | Rebrand treated as search/replace | Inventory 1,000+ references; central brand config; visible surfaces first; canonical API routes + legacy aliases; keep MIT notices. |
| 3 | Rebrand may break APIs/tests | Add `/api/office/*` canonical routes, keep `/api/office/habblaud/*` aliases; one implementation, no duplicated handlers; migrate tests deliberately. |
| 4 | Loading screen can block the app | Weather is an enhancement, never a dependency. Render background immediately; fetch weather with timeout; fallback to neutral; retry visible. |
| 5 | Weather effect flicker | Normalize to a small state set; use hysteresis; one effect at a time; do not combine rain+storm. |
| 6 | Room styles become random soup | Archetype + palette + furniture set + composition rules + deterministic seed + validation; never random global furniture. |
| 7 | Mixing asset sizes/shadows | 16×16 world baseline; 32/48 for portraits/close-ups; one shadow policy; single-direction furniture placed by sockets. |
| 8 | Active set pauses business | Business workflows are durable and event-driven; view state never decides whether work happens. |
| 9 | Giant world performance | Static caches, wing/room chunking, culling, LOD, active tiers, low-rate/dormant simulation. |
| 10 | Room clusters change identity | Logical room above 16×12 cells; stable seat IDs; `legacyFloorId`; `single/multi` character mode. |
| 11 | Business OS becomes a demo | Systems of record, idempotency, unknown outcomes, approvals, budgets, audit, reconciliation. |
| 12 | "All businesses live" at once | Full world and all suites exist; activate one pilot business first; clone playbooks later. |
| 13 | Self-hosted World swallows the project | World is a separate phase: single-user → invite-only → social; office/Business OS do not depend on it. |
| 14 | Terminal is treated as UI | Treat as code execution: local/sandbox, quotas, least privilege, audit, approval. |
| 15 | Scope creep / overengineering | Reuse offices, rooms, editor, panels and proven infrastructure; write glue, not new engines. |
| 16 | Empty world feels unfinished | Furnished dormant rooms, lights/status, business suites, signage; never fake agents. |
| 17 | Content QA explosion | Style catalog + archetype templates + visual goldens + validation; not hand-placed unique rooms. |
| 18 | Personal use mistaken for no rules | Personal/local relaxes distribution, not legal responsibility, security or paid-art redistribution. |

---

## 17. Work breakdown structure

| WBS | Workstream | Owner | Main outputs | Depends on | Gate |
|---|---|---|---|---|---|
| 0 | Product/decisions | Owner + tech lead | pilot business, legal entity, systems of record, approval thresholds, budgets, hosting, World scope, style baseline | — | Signed decision log |
| 1 | Brand/UI | Frontend + brand | Qubit Office name/logo/title/loading/PWA/About/docs; central brand config; visible-brand guard | 0 | No visible Habblaud; tests |
| 2 | Loading/weather | Frontend + backend | loading component, placeholder background, weather provider, pixelfx effects, reduced motion, retry/offline | 1 | Weather matrix + perf tests |
| 3 | Rebrand technical | Full-stack | inventory/classification; canonical API routes + aliases; bundle/manifest rename; provenance | 1 | Alias tests + legal notice |
| 4 | World/static | Game/World | full campus manifest; wings/rooms/cells/clusters; static caches; culling/LOD; minimap/camera | 0,3 | Full world load + budgets |
| 5 | Room styles/assets | Art + tools | LimeZu catalog; archetypes; palettes; furniture sets; recipe schema; generator; validation; goldens | 4 | Every archetype styled/validated |
| 6 | Active/dormant | World + backend | state machine; active-set scheduler; dormant persistence; event-driven business; rehydration | 4,5 | Active vs dormant tests; no view dependency |
| 7 | Business OS core | Backend + platform | durable workflows, queues, timers, idempotency, reconciliation, approvals, audit, budget ledger | 0 | Durable restart/approval tests |
| 8 | Systems of record | Integrations | CRM, payments, support, accounting/email/website adapters; tool gateway; credentials | 7 | One real pilot workflow |
| 9 | Agent workforce | Agent/platform | 3–5 roles; task board; meetings; skills; context; analytics; approvals; deliveries; terminal | 7,8 | Real work visible/actionable |
| 10 | Business Factory | Product + backend | playbooks/templates; create business; activate suite; isolation; lifecycle; clone | 8,9 | Second business from playbook |
| 11 | World/self-host | World/backend | single-user city; invite-only; visits/social; auth/privacy/validation | 4,10 | Optional World local; no office dependency |
| 12 | Character/FX/polish | Frontend/art | character editor, persona profile, pixelfx VFX, seasonal/day-night, mobile | 5,6 | Visual goldens + reduced motion |
| 13 | QA/security/release | QA/security | threat model, sandbox, tenant isolation, load/failure/DR tests, full matrix, rollback | all | `VERIFIED` evidence |

---

## 18. Detailed specifications

### 18.1 Rebrand migration

1. Export all `habblaud` references; classify: user-visible, public API, private code, tests, assets, provenance, generated.
2. Baseline: API snapshots, screenshots, PWA install, bundle contents, license scan, e2e.
3. Central brand module: `name`, `shortName`, `logo`, `support`, `title`, `loading`, `About`.
4. Visible surfaces: document title, iframe title, loading/error/offline, PWA manifest/icons, OG/Twitter, accessibility labels, empty states, docs, default config.
5. Canonical routes: `/api/office/*`; legacy `/api/office/habblaud/*` aliases; one implementation; deprecation headers; tests.
6. Bundle/assets: public chunk names, service worker, manifest, generated paths; rename only after aliasing.
7. Legal/provenance: keep MIT copyright/licence, `THIRD_PARTY_NOTICES`, upstream commit, local changes, SBOM.
8. Remove legacy aliases only after all callers/tests migrate and compatibility window closes.

Acceptance:
- No user-visible Habblaud in the Office UI/bundle/manifest/default config.
- MIT notice and provenance still present.
- Old routes still work or are explicitly versioned.
- No blind global replace; no broken writer/reader pair.

### 18.2 Loading screen / weather

States:
- boot (background + neutral layer immediately)
- weather-loading (non-blocking)
- ready (cross-fade to office)
- degraded/offline (neutral effect, retry)
- error/retry.

Visual mapping:
- clear-day → god rays
- partly-cloudy/cloudy → clouds
- rain → rain
- storm → storm (no rain layered)
- snow → snow
- fog → fog
- unknown → neutral/no effect.

Technical:
- Background configurable (`office.loading.background`); placeholder CSS/SVG now.
- Weather source server-side (Open-Meteo/config); normalized; TTL 10–30 min; timeout 1–2 s; fallback.
- Pixelfx 320×180; back/front passes; `composite`; pixelated upscale; cleanup.
- Performance: VFX ≤4 ms/frame; loading total ≤8 ms mid-range; no startup block >50 ms.
- Reduced motion disables/softens; mobile/low-power fallback.
- Weather never blocks the app.

Tests:
- mapping/hysteresis/fallback/offline/timeout; reduced motion; no effect leak; background replacement; progress/error/retry; accessibility; performance budget.

### 18.3 Room styles

Catalog fields:
`id, file, theme, category, size, footprint, z, layer, wall/floor, orientations, animation, shadow_variant, tags, compatibility`.

Recipe:
`archetype, palette, floor, wall, rug, furniture_set, lighting, shadow, decor, seed, style_version`.

Archetypes: executive, open office, reception, support, sales, finance, creative, control room, research, meeting, kitchen, wellness, showcase, server/ops, legal, training, fulfillment, media.

Rules:
- 16×16 world; 32/48 for portraits/close-ups.
- one shadow policy; single-direction furniture by socket.
- required anchors first; secondary props; decals/lighting last.
- 60/30/10 palette; one dominant material; one accent.
- independent seeded streams; vary secondary properties only.
- validate walkability, capacity, collision, interaction, readability, wrong-theme props.
- visual goldens per archetype/palette; no paid art committed.

### 18.4 Active/dormant world

Data:
`Business`, `Wing`, `LogicalRoom`, `Cell`, `Desk`, `AgentPresence`, `RoomState`, `Scheduler`.

Room state:
`AUTHORED → LOADED_STATIC → OBSERVED → PRESENTATION_ACTIVE → SIMULATION_ACTIVE → DORMANT → REHYDRATING`.

Tiers:
0 dormant static + event-driven business; 1 aggregate; 2 low-rate agents; 3 proxy; 4 full.

Active-set inputs:
camera, interaction, urgent event/deadline, notification/approval, business priority, fairness/aging.

Rules:
- static geometry cache per room/wing; active full; neighbors simplified; occluded no draw.
- business workflows never depend on view.
- dormant room retains state/schedules/approvals/pending events.
- rehydrate visuals from authoritative state.
- unit tests for activation policies; load tests at 24/64/128/500 agents; no fake activity.

### 18.5 Business OS

Core:
- Durable workflow engine (Temporal or equivalent), event log, queues, timers, leases, retries, dead-letter.
- Idempotency keys, provider request IDs, unknown-outcome reconciliation.
- Systems of record: CRM, orders, payments, accounting, support, documents, email, website, analytics.
- Tool gateway: auth, tenant checks, schema validation, rate/budget, DLP, approval, idempotency, dry-run, audit, egress.
- Human approval cards with evidence, payload, cost, side effects, policy, reversal, expiry.
- Tenant isolation: `tenant_id`, `business_id`, `department_id`, `actor_id`, `purpose`, `classification`, `policy_version` on every record/event/tool call.
- 24/7: durable restarts, provider fallback, backups/restore, on-call escalation, incident runbooks.
- Autonomy L0–L5; money/legal/hiring/irreversible human-approved.

Acceptance:
- One pilot business runs a real workflow end-to-end with artifact/receipt.
- Restart mid-workflow resumes; approval pauses/resumes; unknown outcome reconciled; duplicate prevented.
- No direct agent writes to systems of record outside tool gateway.

---

## 19. Test strategy

| Layer | Tests |
|---|---|
| Unit | models, state machines, policy, idempotency, weather mapping, style recipes, capacity/validation |
| Contract | API routes/aliases, event schemas, tool gateway, system-of-record adapters |
| Visual | room goldens per archetype/palette; loading weather states; reduced motion; 1440/390 |
| E2E | full world load, active/dormant switch, create business, pilot workflow, approval, restart |
| Failure | provider outage, timeout, unknown outcome, duplicate delivery, API schema change, dead-letter |
| Security | tool permissions, tenant isolation, secret handling, prompt injection, sandbox escape, CSRF/auth |
| Performance | 24/64/128/500 agents; active-set budgets; static cache; loading compositor; memory |
| DR | backup/restore, restart, database migration, rollback, version skew |
| Final | one full matrix on a frozen SHA; live deploy/rollback; `VERIFIED` only then |

No full matrix before implementation freeze. Focused checks per change.

---

## 20. Phase gate checklist

- Phase 0: decisions signed; inventory/SBOM; baseline; legal notice placement.
- Phase 1: no visible Habblaud; loading weather works; offline/reduced-motion; tests.
- Phase 2: full world load; all rooms styled/validated; active/dormant; budgets.
- Phase 3: pilot business durable workflow; real artifact/payment/receipt; restart/approval.
- Phase 4: 3–5 roles; real work actionable; panels honest.
- Phase 5: second business from playbook; isolation.
- Phase 6: optional local World; office unaffected; character/FX goldens.
- Phase 7: security/DR/load/failure; full matrix; live rollback; `VERIFIED`.

---

## 21. What this plan deliberately does not do

- It does not authorize implementation before sign-off.
- It does not promise 24/7 without a human on-call and legal owner.
- It does not remove licence/attribution obligations.
- It does not claim all 283+ agents animate at once.
- It does not make the office the source of business truth.
- It does not invent dates, percentages, or completion claims.

---

## 22. New external projects reviewed (2026)

| Project | Licence / status | What to take |
|---|---|---|
| **DAACS OS** (`omnify2026/DAACS`) | Licence not finalized — **concepts only** | Goal → PM plan → execute → review → verify → approve; local desktop/web; approval queue; custom agents; office view; dynamic sequencer |
| **Canopy** (`kinnerkarmanish/mak`) | MIT | Workspace protocol (markdown), 5-layer org, workflow DAG, cron/heartbeats, 330-agent roster, budgets/governance, cost console, virtual office, marketplace |
| **OctoOffice** (`Chepko932/OctoOffice`) | Apache 2.0 | 7 departments, mission control, YAML workflows, Tiled/Pixi office, SQLite, live terminal, meetings |
| **Blank Collar** (`The-Blank-Collar/blankcollar-agentic-os`) | MIT | Goal-first orchestration, department abstraction, policy engine (`allow/approve/deny`), memory, simulation, Stripe, channels, observability, feedback |
| **Agent Office** (`AgentSystemLabs/agent-office`) | MIT | Floor per project, workers at desks, live terminal, voice, GitHub issues/PRs, git worktrees, agents managing agents, `/lite` 2D mode |
| **Outworked** | Public; licence verify | Desktop office, asset packs, scheduled tasks, Slack/iMessage, browser, MCP, approvals, cost dashboard |
| **BagIdea Office** | Public; licence verify | Wallpaper office, social simulation, self-extending plugins/proposals, memory/skills, approval security center |
| **PixelDesk** | Public/open-core; verify | Humans + agents as colleagues, shared brain, cost transparency, PWA, proximity |
| **Veska / AI Workforce** | Apache 2.0 | AI-native ERP/CRM: 52 tools, 12 departments, CRM/support/finance/HR/ops |
| **Oryh** | Apache 2.0 | Agent-first ERP/CRM, skills-not-SDK, audit, durable business records |
| **AgenticCRM** | MIT | WhatsApp CRM, tickets/SLA, invoices/payments, 24/7 keepalive, multi-agent workflows |
| **Autonomous Business OS** | MIT | Lead → qualify → onboard → deliver → finance → knowledge; HITL; integrations |
| **OOSDK** | MIT | Ontology/policy-as-code, deterministic decisions, caged LLM advisors, order-to-cash |
| **FusionClaw** | MIT | Business OS over MCP, CRM/ops/finance, scoped keys, confirmation gates, audit |
| **RevealUI** | MIT | Auth/content/offers/payments/agents runtime, one permission model |
| **Agent Company AI** | MIT | 9 roles, Stripe/Gumroad, invoices, spend caps, autonomous CEO loop |
| **JSM HQ** | MIT | Lead/worker office, per-agent repo clone + Docker sandbox, MCP coordination, PR completion |
| **HermitShell** | MIT | Per-agent Docker isolation, Telegram, scheduler, dashboard |

**Rules:** verify the exact repository licence/commit before copying. No licence = no code/assets. Concepts are safe. Personal/local use still does not erase attribution or security obligations.

---

## 23. Plan review v3 — critical corrections

1. **The world is a projection, not the product.** Every room must map to a real capability/workflow. Decorative rooms are allowed only as furnished dormant shells.
2. **Full world does not mean full live businesses.** All suites exist; one pilot business is activated first; the rest are dormant but persistent.
3. **The map must not define the domain.** Business objects, permissions and workflows are independent of rooms, desks, animations and pixels.
4. **Business OS is a separate product.** Do not rebuild ERP/CRM/accounting from scratch in the first release. Own the control plane; integrate systems of record.
5. **Full campus has content debt.** Use archetype/style generators, goldens and dormant furnished shells; do not hand-build every room.
6. **Room style must be constrained.** 16×16 baseline; one shadow policy; single-direction furniture by socket; validation and visual QA.
7. **Active set must be capability-aware.** Camera is one input. Deadlines, approvals, incidents, business priority and interaction also activate.
8. **Business work is independent of view.** Dormant rooms keep schedules, approvals, payments, tickets and queues via durable workflows.
9. **Loading weather is an enhancement, not a dependency.** Background and progress render immediately; weather timeout/fallback; reduced motion; no location leak by default.
10. **Rebrand is a migration, not a search/replace.** Inventory, brand layer, visible surfaces, canonical routes + aliases, bundle/manifest, notices, then remove old names.
11. **MIT notice must stay.** Hide Habblaud from users; keep copyright/licence/provenance.
12. **Self-host contract must be designed now.** Portability, migrations, storage/queue abstraction, config schema, backups/restore, telemetry; even if World ships later.
13. **One pilot business before factory.** Prove one real workflow with money/customer/artifact/receipt; then clone.
14. **No agent is a legal person.** Human owner/entity, approvals, insurance, contracts, tax, on-call.
15. **No fake success.** No “done” without artifact/receipt; no phantom agents; no invented floors/businesses.

---

## 24. Phase order v3 (supersedes the earlier list)

### Phase 0 — Decisions, inventory, contracts
Pilot business, legal entity, systems of record, approval thresholds, budgets, hosting, World scope, style baseline. Inventory all Habblaud references; licence/SBOM; baseline screenshots/tests; define API aliases and deployment contract.
**Gate:** decisions signed; inventory/SBOM; legal placement; baseline.

### Phase 1 — Qubit Office shell + full static world
Rebrand visible surfaces; loading screen + weather pixelfx; full campus manifest; wings/rooms/cells/clusters; room-style catalog + archetypes; active/dormant state machine; static caches/culling/minimap.
**Gate:** no visible Habblaud; loading works offline/reduced-motion; full world loads; every room styled/validated; active/dormant tests; performance budgets.

### Phase 2 — Business OS foundation + pilot
Postgres + durable workflows + queues/timers/leases/retries/dead-letter; systems-of-record adapters; tool gateway; approvals; budgets; audit; one pilot business end-to-end.
**Gate:** real artifact/payment/receipt; restart/approval/unknown outcome; no duplicate; 24/7 with on-call.

### Phase 3 — Agent workforce + panels
3–5 roles; task board; meetings; skills; context; analytics; deliveries; terminal; approvals; active/dormant visuals; maintenance agents.
**Gate:** real work visible/actionable; panels honest.

### Phase 4 — Business Factory
Playbooks/templates; create business → suite activates; isolation; budgets/books; lifecycle; clone/compare.
**Gate:** second business from playbook; independent operation.

### Phase 5 — World/self-host/social
Single-user local World → invite-only → social; city/chunks/visits; auth/privacy/validation; character/FX.
**Gate:** optional World local; office unaffected; no privacy leaks.

### Phase 6 — Hardening/release
Threat model; sandbox; tenant isolation; DR; load/failure; evals; one full matrix; live deploy/rollback.
**Gate:** evidence; `VERIFIED` only then.

**Why this order:** the world and brand are visible early; the business control plane is proven before scaling; the factory comes after one real business; World/social never blocks the office.

---

## 25. First-wave execution contract (4 agents)

Owner has authorized a **bounded technical first wave**. No live deploy. Each agent gets its own worktree from `plan/qoffice-master`; no edits to `main`; no live service/config; no paid art committed; no unlicensed code; keep MIT notices; focused tests; commit small units.

| Lane | Owns | Must not touch | Deliverable |
|---|---|---|---|
| **A. Rebrand** | brand constants; visible shell/metadata/About/PWA/docs; inventory map; legal notice entry | loading screen; API route renames; vendored asset bundle renames | Qubit Office brand layer + inventory/aliases plan + tests |
| **B. Loading/weather** | new loading component; placeholder background; weather provider/normalizer; pixelfx weather subset; shell integration for loading | API/business modules; room styles | loading screen with rain/storm/snow/god-rays/clouds/fog + tests |
| **C. World/room styles** | campus manifest; wings/rooms/cells/clusters; active/dormant state; LimeZu asset catalog/style recipes/archetypes; validation/goldens harness | UI rebrand/loading; business OS | world/room-style foundation + tests |
| **D. Business OS foundation** | business objects; approval/idempotency/ledger/job schema; tool gateway contract; tests | UI rebrand/loading; world rendering | durable control-plane skeleton + tests |

**Global agent rules:** no `main`/live; no full matrix; no fake TODO completion; no `VERIFIED`; report exact commands and blockers; if a decision is missing, use the documented default and list it.

---

## 26. Decisions needed from the owner (plain language)

1. **What is the first business?** Pick one simple real business to prove, e.g. lead follow-up service, content/SEO service, or e-commerce ops.
2. **Who owns it legally?** You or a company; country; tax; who signs contracts.
3. **Which tools does the business already use?** CRM, payments, email, support, accounting, website.
4. **What can agents spend/do without asking?** Daily/monthly budget; refunds; discounts; emails; ads; contracts.
5. **Where will it run?** Your PC, a VPS, or cloud; who is on-call if it breaks.
6. **Do you want the online World?** Local only, invite-only, or public self-hosted.
7. **Which room style baseline?** Recommended 16×16 LimeZu world, 32/48 only for portraits.
8. **Personal only or eventual product?** Personal/private is simpler; selling later reopens licensing/distribution.


---

## 27. Decision log — owner answers (2026-11-11)

| # | Decision | Locked choice |
|---|---|---|
| D1 | First business | **Etsy digital shop** as the pilot, with niche selected through the War Room. Digital downloads first (no inventory/shipping). |
| D2 | Legal owner | **Personal** now; later Estonia / Hong Kong / US entities. Build tenancy/entity-ready now, do not mix personal and company books. |
| D3 | Tool access | Agents may use **all tools/capabilities (A–Z)**, but each agent has a scoped permission profile, budget and approval gates. No unbounded money/legal/irreversible action. |
| D4 | Agent controls | Clicking an agent opens a real control panel: model/provider, reasoning effort, permissions, tools, budget, autonomy level, schedule, skills, memory, terminal/browser access, stop/pause/takeover, audit. |
| D5 | Hosting | **Local PC first**; rent a VPS/GPU when 24/7 or load requires it. Local-first, portable, backups, migration path. |
| D6 | World | **Local/off now**; invite-only later if useful; public World much later. Office/Business OS never depend on World. |
| D7 | Style | **16×16 LimeZu baseline** for the world; 32/48 only for portraits/close-ups/UI. User supplies loading background; weather effects are optional. |
| D8 | Distribution | **Personal/private** build. Keep MIT notices and paid-art local-only rules; selling/distributing later reopens licensing review. |

### D6 explanation (what “World” means)

- **Local only:** the office/city runs only on your PC. Nobody else can visit. Simplest and private.
- **Invite-only:** you host it (PC/VPS); friends/team get accounts/invites. Visits and social possible; you manage access.
- **Public:** anyone can join/visit; needs accounts, moderation, abuse handling, privacy, scale. Much larger project.

**Recommended:** local/off now, invite-only later if needed, public not now.

### D3 explanation (all tools, but safe)

“All tools A–Z” means agents can be given browser, shell, files, email, Etsy, APIs, CRM, payments, analytics, docs, images, etc. It does **not** mean every agent can spend money or sign/commit without checks. Each agent gets a permission profile:

- read / draft / act / approve
- tool allowlist
- file/network/browser/terminal scopes
- spend and rate limits
- required human approvals
- audit and revoke

This gives full capability without letting one mistake drain an account or contact a customer unchecked.

---

## 28. War Room / Idea Lab

### Purpose
A special room where agents turn raw inputs into tested business ideas and plans.

### Inputs
YouTube links/transcripts, web links, posts, screenshots, documents, notes, voice notes, existing products, competitor links, customer comments.

### Workflow
1. **Intake** — normalize links/media into source artifacts (transcript, page text, images, metadata).
2. **Research** — market, competitors, demand, pricing, keywords, risks.
3. **Debate** — roles: Moderator, Researcher, Skeptic, Marketer, Operator, Finance, Customer, Legal/Risk, Builder.
4. **Evidence** — every claim tagged `FACT`, `INFERENCE`, `ASSUMPTION`, `UNKNOWN`; cite source artifact.
5. **Synthesis** — idea brief, score (demand/effort/cost/risk/fit), experiments, next tasks.
6. **Decision** — owner approves/edits/rejects; decision log saved.
7. **Launch** — approved idea becomes a business/project/playbook with budgets and approvals.

### Room design
War room / creative studio: large table, wall screen, whiteboard, sticky notes, projectors, plants; warm/creative palette; 16×16 LimeZu style; every source/idea shown as an artifact, not fabricated conversation.

### Rules
- No auto-publish, no auto-spend, no fake customer research.
- Sources and assumptions visible.
- Dissent is recorded, not averaged away.
- The output is a plan/artifact, not a chat transcript.
- Council-of-high-intelligence lenses can be used for high-stakes decisions.

---

## 29. Etsy pilot playbook

**Pilot:** one personal Etsy shop selling digital downloads (templates/printables/planners). No inventory or shipping.

### War Room decides niche
Use the War Room to compare niches on demand, competition, price, effort, IP risk and speed to first sale.

### Pipeline
1. Niche and product brief.
2. IP/licence check (no copied art/fonts; commercial-use assets only).
3. Product design/proof (Canva/design tool; human review).
4. Listing copy, tags, title, price, mockups.
5. Shop setup/policies (Etsy seller policy; taxes; personal seller).
6. Publish listings — **human approval initially**.
7. Customer messages/orders — draft by agents; human approval initially for refunds/commitments.
8. Etsy Ads/analytics — budget caps; human approval.
9. Bookkeeping — export/summary; personal books now, entity-ready later.
10. Review: sales, conversion, messages, refunds; improve listings.

### Integrations
Official Etsy API/export where allowed; no scraping/ToS violations. Start with **dry-run** adapters and manual approval. Real credentials only after the owner sets them.

### Guardrails
- No copyright/IP infringement.
- No fake reviews/claims.
- No auto-refunds above threshold.
- No auto-messaging that commits to custom work/price.
- Personal seller account now; separate entity later.

---

## 30. Agent control panel

When clicking an agent:

- **Identity:** name, role, avatar, business, room, seat, manager.
- **Brain:** provider, model, reasoning effort, fallback model, context window, temperature/tool settings.
- **Permissions:** read/draft/act/approve; tools; file/browser/network/terminal/email/Etsy/payments scopes; per-tool allow/deny.
- **Limits:** daily/monthly spend, per-action cap, rate limits, refund/discount caps.
- **Autonomy:** L0–L5, required approvals, escalation.
- **Work:** current task, queue, schedule, subagents, handoffs.
- **Knowledge:** skills, memory, SOPs, references.
- **Controls:** pause, stop, takeover, reassign, revoke, rotate credentials.
- **Audit:** every action, cost, model, tool, approval, artifact.

The panel must write through the Business OS/agent authority, not just localStorage.

---

## 31. Personal-now, entity-later model

- Tenancy supports `personal`, `company`, and multiple legal entities from day one.
- Every record carries `tenant_id`, `business_id`, `legal_entity_id`, `currency`, `tax_region`.
- Personal Etsy shop uses the personal tenant.
- Later Estonia/HK/US entities get separate books, accounts, approvals and credentials.
- Never mix personal and company money/data; migration/clone path must be explicit.
- Local PC now; export/backup makes moving to a VPS/entity later possible.


---

## 32. Full-service corporation capability map

**Rule:** no artificial cap on rooms, floors/wings, departments, roles or agents. Capacity is bounded only by hardware, platform terms, budgets, permissions and human approval.

### Departments / divisions

- **Executive:** CEO (human), Chief of Staff, Strategy, Operations, Board/Council.
- **Revenue:** Sales/SDR, Lead Generation, CRM, Partnerships, Account Management, Customer Success, Pricing.
- **Marketing:** Brand, Content, Social Media, Community, SEO/GEO, Paid Ads, Email/Lifecycle, Influencer, Creative/Video, Analytics.
- **Product & Engineering:** Product, UX/UI, Web, Apps, E-commerce, QA, DevOps, Data, Security.
- **Delivery:** Project/Program, Onboarding, Services, Fulfillment, Training, Support.
- **Finance:** Bookkeeping, Invoicing, Payments, Reconciliation, Tax, Procurement, Reporting.
- **Legal & Compliance:** Contracts, Privacy, IP, Vendor, Risk, Platform Policy.
- **People:** Recruiting, Onboarding, Performance, Training (agents/humans).
- **Research / War Room:** Market, Competitor, Customer, Trend, Innovation, Validation.
- **Maintenance:** Cost, API/schema drift, security, backups, incidents, QA, monitors.

### Service lines (A–Z)

Websites/landing pages, web apps, e-commerce, SEO/GEO, content, social media, community, email, ads, CRM, support, automation, branding/design, video/creative, data/analytics, onboarding, retainers, training, AI transformation, maintenance.

### Role template fields

Every role has: department, mission, responsibilities, skills, tools, data scopes, permissions, budgets/limits, autonomy level, approvals, schedules, KPIs, escalation, model/provider defaults, memory/SOPs, human owner.

### 100-storey / 1000-agent scale

- Floors/wings = organizational groups, not business truth.
- No hard cap on floors/rooms/desks/agents; the static world can be large.
- Only active businesses/rooms materialize agents; dormant rooms stay furnished/persistent.
- Active set: camera, interaction, deadlines, approvals, incidents, priority, fairness.
- Directory/elevator/minimap/floor stack for navigation; no fake floors or agents.
- Static caches, chunking, LOD, hierarchical pathfinding, simulation tiers; load-test at 100/500/1,000 agents.
- Room clusters: 16×12 cells; 9/12/18/24+ desks; never stretch a cell.

> Note: this supersedes the earlier “no more floors” preference where the owner now wants a 100-storey corporation. Floors are allowed; they remain presentation/grouping, and only active areas simulate.

---

## 33. Platform-safe social, leads, clients and delivery

### Social media — official APIs, human approval, no fake engagement

- Use **official APIs/OAuth** only. Never browser-automate or scrape logged-in platforms.
- X: automated posts allowed via API; **no automated likes/follows/bulk engagement**; label automated accounts; replies/DMs only with opt-in and per-interaction limits; pay-per-use caps.
- Meta (Facebook/Instagram/Threads): app review/business verification; frequency/spam rules; Page/professional accounts; official publish APIs; respect per-24h caps.
- LinkedIn: only documented OAuth/Posts API routes; **no crawlers/bots/browser automation**, no bulk messaging/inauthentic engagement; many read/message features are partner-only.
- TikTok: Content Posting API; audit required or posts are private; low per-token limits; no fake likes/followers.
- YouTube: Data API quota; uploads via official API with privacy/publish-at controls; 100 uploads/day bucket.
- Pinterest/Reddit/others: official API/approval only; no scraping/automation where prohibited.
- **Human approval** for first publish, claims/pricing/guarantees, sensitive replies, crisis/complaints, DMs to high-intent buyers, and anything irreversible.
- **No fake reviews, testimonials, engagement, followers, or unoriginal content.**

### Lead generation and clients

- ICP, consent, suppression lists, opt-outs, data minimisation, GDPR/CCPA/CAN-SPAM.
- CRM is system of record; avoid duplicates; source/attribution/consent stored.
- AI may research/score/draft; human approves targeting changes, campaigns, high-value replies, pricing/claims/commitments.
- Multi-channel coordination prevents duplicate/contradictory messages.
- Daily/weekly review; deliverability, reply quality, meetings, revenue—not just sends.

### Websites, apps, services

- GitHub/Git worktrees per project; isolated sandbox for code; CI/tests; human code review before deploy.
- Client workspaces isolate data, credentials, assets, brand.
- Staging → approval → production; rollback and backups.
- SEO/GEO/content/ads/reporting follow the same approval and evidence rules.

### Etsy pilot

Digital downloads first; shop setup/listing/pricing/publish/refunds/messages/ads require human approval initially; official Etsy routes only; IP/licence check; no fake reviews/claims; personal seller now, entity later.

---

## 34. War Room for full-service ideas

Inputs: YouTube links/transcripts, web pages, posts, comments, competitor shops/sites, images, notes, customer conversations.

Debate roles expand to: Moderator, Researcher, Skeptic, Marketer, Social/Community, Sales/Lead Gen, Product/Engineering, Web/App, Delivery, Finance, Legal/Platform Policy, Customer, Builder.

Outputs: idea brief, niche/service choice, customer/ICP, offer, channels, content/social plan, lead-gen plan, website/app scope, pricing, budget, risks, experiments, 30/60/90-day plan, tasks.

Evidence labels: FACT / INFERENCE / ASSUMPTION / UNKNOWN; source artifacts; dissent preserved; no fabricated research; owner approves.

---

## 35. Phase order v4 (full-service)

0. **Decisions/platform/legal:** pilot (Etsy digital), personal seller, tools, approval limits, budgets, local hosting, platform accounts/app reviews, entity-later plan.
1. **Office shell + full static world:** rebrand, loading/weather, 100-floor/wing-capable campus, rooms/styles, active/dormant, controls. *(Wave 1 done as foundation.)*
2. **War Room + role registry + agent controls:** 100+ role templates, idea lab, per-agent model/permissions/budget/autonomy.
3. **Business OS + Etsy pilot:** durable workflows, CRM/payments/support/accounting adapters, Etsy dry-run, approvals, ledger.
4. **Full-service agency:** social/lead-gen/content/ads/SEO/web/app/service playbooks; official APIs; human approvals; client workspaces.
5. **100-storey scale + Business Factory:** many floors/rooms/agents active-only; create/clone businesses; multi-entity later.
6. **World/self-host + hardening:** local/invite-only World; security, DR, load; full matrix once.

---

## 36. Wave-2 agent contract (4 lanes)

- **A. War Room/full-service idea lab** — finish partial work; intake/debate/evidence/score/decision/tasks; no auto-publish/spend; tests.
- **B. Role/capability registry** — 100+ role templates across departments, tools/permissions, org/floor templates, scaling; tests.
- **C. Agent control panel** — model/provider, permissions, budgets, autonomy, schedule, stop/revoke; backend + UI; tests.
- **D. Platform integration + Etsy pilot** — official API/dry-run adapters for Etsy/social/CRM/email, platform-policy gates, approval/rate-limit handling, Etsy playbook; no credentials/network/live actions.

Each lane owns its worktree; no main/live/paid art/fake success; focused tests; commit; report blockers.


---

## 37. Wave-2 implementation results

Landed on `integration/qoffice-wave1` from `61cc97bff`; not merged to `main`, not deployed, not live-verified.

- **War Room / Idea Lab:** `c8ed98cb9`, tests `705f0ec8b`; schema v2, six source kinds, evidence labels, 13 debate roles, dissent, owner-only decisions, approval-gated task creation, offline adapters. 18 tests.
- **Role/capability registry:** `c57612c14`; 22 departments, **132 roles**, 100 floor/wing templates (22 active, 78 dormant), 5 wings, validation for permissions/approvals/autonomy. 27 tests.
- **Agent controls:** `194fdbe57`, tests `5d73e5f59`; durable `bos_agent`/turn/usage/operation tables, model/provider/reasoning/fallback, explicit permissions, budgets/rate limits, autonomy L0–L5, approvals, pause/resume/stop/takeover/revoke/archive, append-only audit. 34 tests.
- **Etsy + platform policy:** `fa9272426`, `d9af53f29`, `0fa4d5113`; policy registry for Etsy/X/Meta/LinkedIn/TikTok/YouTube/Pinterest/Reddit/email/web/CRM, dry-run Etsy/social/CRM/email adapters, approval/rate/idempotency gates, Etsy pilot playbook, docs. 81 tests.

**Combined verification:** 274 Python tests passed across BOS, War Room, roles, agent controls, platform policy, Etsy/social/CRM/email, world/room-style, asset catalog, office business and Qubit Office brand. Ruff/compile/diff checks passed. No real credentials, network calls, paid art, live service restart, or main merge.

**Still not done:** UI wiring for War Room/roles/agent controls, ToolGateway executors for adapters, real platform apps/OAuth (Etsy/X/Meta/LinkedIn/TikTok/YouTube), pilot niche/product selection, office floor/room instantiation beyond templates, 100/500/1,000-agent load tests, main merge, deployment, and the final full matrix.


---

## 38. Wave-3 implementation results (UI + tool gateway)

Landed on `integration/qoffice-wave1`; not merged to `main`, not deployed, not live-verified.

- **Business UI/API bridge:** `4a365a1dd`, `c12f0d424`, `6d792adbc`; owner-scoped `/api/office/business/*` and `/agent/api/office/business/*`; War Room dock, agent-control drawer opened from the Agents panel, 132-role/100-floor directory. 92 focused Python tests; 21 new Vitest tests; full office app **17 files / 181 tests passed**.
- **ToolGateway integration:** `6196d4b4a`, `8ace1bd21`, `47337cc71`; dry-run Etsy/social/CRM/email executors, platform policy enforcement, approval/expiry, rate/idempotency, single audit/ledger record, Etsy draft→approval→dry-run publish workflow. 218 focused tests.
- **Combined verification on integration:** **320 Python tests passed**; **181 office Vitest tests passed**; ruff clean; `git diff --check` clean.

**Still not done:** main merge/deploy, real OAuth/platform accounts, pilot niche/product, 100-floor renderer/load test, live browser verification, final full matrix.

**Immediate next:** main is still dirty from another session (generated TUI build file, untracked pixelfx tools, package-lock). Do not merge over it. Once clean: merge `integration/qoffice-wave1`, rebuild the served bundle, deploy, then run the live Etsy/War Room/agent-control journey. Owner decisions needed for the Etsy niche and real platform accounts.


---

## 39. Live deployment (2026-11-11)

Deployed from `integration/qoffice-wave1` to the installed live package. This is a **live deploy, not a release**; `main` remains unmerged and `VERIFIED` is not claimed.

- Backup: `/home/trick/.cache/qubit-deploy-backup-qoffice-wave3-20261111-075442`
- Live bridge: `qubit-voicebridge.service`, active, pid `3892438`
- Served bundle: `qubit-office.game.js` sha256 `9ab0b9b46a62b57194142bf975170a3abd5ee878551dd1612ba3391d5d9794cb` (7,610,301 bytes), identical to the integration build
- `/qubit-office` → **200**; `/agent/api/office/business/overview` → **200** with the session token
- Browser-verified (CDP/Chrome): Agents dock, War Room/Idea Lab, Roles & floors (100 floors), Agent Controls drawer, and the vendored topbar now reads **Qubit Office** instead of Habblaud.

**Honest limits visible in the live UI:**
- The Agent Controls drawer opens for an office seat but says no Business OS tenant/agent is linked yet; the first War Room session creates the local Personal tenant, and seats must still be linked to managed BOS agents.
- The 100-floor directory is a list/template view; the rendered office still shows the existing 4 rooms / 10 agents.
- Etsy/social/CRM remain dry-run; no real shop, OAuth account, or external action was executed.
- The iframe topbar identity is patched by the Qubit shell on load; the legal MIT attribution remains visible.


---

## 40. Live seat linking and 100-floor tower (2026-11-11)

- **Seat → BOS bootstrap:** `POST /api/office/business/bootstrap` creates the Personal tenant/business and maps all **283 office seats** to BOS agent records with `metadata.office_seat_slug`. First live run: **283 created in ~2.9 s**; repeat is idempotent (~0.05 s, 0 created). Clicking an office agent now opens real identity, brain, permissions, budgets, autonomy, schedule and audit controls.
- **Safe defaults:** provider `qubit`, model `active`, reasoning `medium`, deny-all permissions, zero spend, autonomy `L0`, approvals for spend/legal/publish/contact/irreversible. No model turn, external call or spend is performed by bootstrap.
- **100-floor tower:** War Room → Roles & floors now renders a **Tower directory**: 100 floors, 5 wings, 22 active / 78 dormant, room/desk counts, floor selection and detail. It is a navigation layer over real templates, not a rendered 3D scene.
- **Redaction fix:** credential redaction no longer hides non-secret token limits (`max_tokens`, `prompt_tokens`, etc.); real tokens/keys remain redacted.
- **Live:** bridge pid `4078662`; `/qubit-office` 200; bundle `16e16fe3…` (7,619,244 bytes); backup `/home/trick/.cache/qubit-deploy-backup-qoffice-seats-20261111-085003`; verified with 319 Python tests, 198 office Vitest tests, and live browser CDP.
- **Still not done:** the 100-floor view is not the rendered world; the live scene remains the existing 4 rooms / 10 agents. No real Etsy/social accounts, no model turns from the controls yet, no `main` merge, no full matrix/`VERIFIED`.


---

## 41. Wave-5: world rooms, editable controls, research, approvals (2026-11-11)

**Landed and deployed**
- **World projection:** deterministic 100-floor/5-wing plan with 300 rooms, 575 fixed 16×12 cells, 3,075 desks, zero staffed agents; `/api/office/world/tower` full room-scene read and `/api/office/world/tower-plan` bounded summary.
- **Tower room scene:** War Room → Roles & floors renders room cells and empty desk slots for the selected floor; the other 99 floors stay a directory. No fake agents.
- **Editable agent controls:** model/provider/reasoning/fallback, tokens, budgets, rate, autonomy, schedule/timezone, explicit permission scopes; real update API, dirty/cancel/reset, validation, audit refresh. Approvals stay enforced.
- **War Room research + Etsy pilot:** deterministic local research provider with labelled evidence, disabled-by-default remote seam, and an idempotent owner-pending `Etsy digital shop pilot` session. Live pilot session created: `wrs_01M75C2PFR1FQ0GWTZVQ04M8DR` (state `owner_decision`, no decision/tasks/spend).
- **Approvals panel:** office dock + menu icon + pending badge, list/approve/reject through the shared journal, typed errors, named empty state. Live: 0 pending.

**Verification / live**
- 356 Python tests and 234 office Vitest tests passed before deploy; ruff/diff clean.
- Live bridge pid `637319`; `/qubit-office` 200; served bundle `02b2d34c…` (7,680,582 bytes), identical to integration.
- Backup: `/home/trick/.cache/qubit-deploy-backup-qoffice-wave4-20261111-151455`.
- Browser-verified: 100-floor tower with room cells/desks, agent edit form, approvals dock (0 pending), Qubit Office brand.

**Still not done**
- The rendered office scene remains the existing 4 rooms / 10 agents; the 100-floor room scene is a selected-floor directory view, not a 3D world.
- No real Etsy OAuth/shop/listing, no social/CRM accounts, no model turns from the control panel.
- `main` is unmerged; no full matrix / `VERIFIED`.


---

## 42. Wave-6: research, Etsy connection, turn preview, world staffing (2026-11-11)

**Landed**
- **Perplexity research:** optional `PerplexityResearchProvider` (`sonar-pro`, configured URL/key), citation-backed FACTs only, local provider default, idempotent session research, no decision/tasks/spend. UI “Research with Perplexity”.
- **Etsy OAuth setup:** PKCE/state authorization, token exchange/refresh, typed `not_configured`/`authorization_required`/`connected`, secure token store, connection dock. Live status is `not_configured` (no app credentials yet); no listing/publish/spend.
- **Agent turn preview:** owner-gated read-only plan for prompt/tools/spend: validates active state, permissions, autonomy, budgets/rate/turns/tokens, approvals and model readiness. No turn, usage, spend, model or network side effect. UI in Agent Controls.
- **World occupancy + assignment:** explicit `?staffed=1` projection and owner-gated `POST /agents/world-assign`. Assigned **283/283** agents in ~1.9s to real tower desks; live view is **18 floors / 36 rooms staffed, 283 occupied desks, 0 unplaced**. Explicit links are preserved; no desk is reused.

**Live verification**
- Bridge pid `1004761`; `/qubit-office` 200.
- Served bundle `f88db466…` (7,714,631 bytes); backup `/home/trick/.cache/qubit-deploy-backup-qoffice-wave5-20261111-173900`, assignment backup `/home/trick/.cache/qubit-deploy-backup-qoffice-worldassign-20261111-174229`.
- 267 Python + 269 office Vitest tests passed before deploy; 6 focused assignment tests; ruff/diff clean.
- Browser-verified: tower room scenes now show **named agents on desk cells**, 283 staffed / 0 unplaced; agent preview returns an allowed plan; approvals empty; Etsy says not configured.

**Still not done**
- The world is a selected-floor room scene, not a rendered 3D city; only 18 of 100 floors are staffed.
- Real Etsy OAuth/listing requires operator app credentials; Perplexity research was not run live (no spend).
- No real agent turn execution yet (preview only); no social/CRM accounts; `main` unmerged; no full matrix/`VERIFIED`.


---

## 43. Department/office/interaction design persisted

The owner's department-per-floor model, per-agent sessions/workspaces, department Obsidian
graphs, OpenViking scopes, interaction/action engine, mature-content policy, builder panel,
FX/map/Godot guidance and the corrected implementation order are persisted in
`docs/QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md`. That file is the reference for this
work; this section only points at it.


---

## 44. Coverage additions

The Character Studio merge (Habblaud + LimeZu Character/Portrait Generators), outside-world
campus map, full panel inventory, capability catalog, asset ledger and the coverage-status
table are persisted in `docs/QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md` §15–§20. That file
is the reference; this is a pointer, not a second authority.

---

## 45. Organized execution plan v6 (canonical)

**Status:** this section is the canonical execution order. Where earlier phase lists, wave reports or
owner-decision notes conflict, this section wins for *what to build next*. It is still design,
not evidence of implementation.

### Plan freeze

No more plan rewrites until one department vertical slice works. New ideas go to the idea ledger;
they do not reorder the build.

### Authority model

| Data | Authority | Projections |
|---|---|---|
| Business, department, room, agent, session, task, artifact, message, approval, ledger, capability call, FX event | Business OS event log/SQLite (later Postgres) | Office, tower, maps, panels |
| Obsidian notes/graph | **Derived projection** | Human graph/search |
| OpenViking memory | Adapter into Qubit memory API, scoped by tenant/agent | Semantic retrieval |
| CharacterProfile | Business OS, versioned | Habblaud character state, LimeZu portrait atlas |
| World/map layout | Versioned map schema | Web renderer, optional Godot |
| FX | Event consumer over authoritative events | Animations/particles |

Obsidian is **not** a runtime database. OpenViking is **not** written through invented filesystem
paths; it goes through one adapter with authenticated namespaces, commit boundaries, provenance,
retention and deletion. Business state must remain rebuildable without the graph.

### Floor decision (resolves the contradiction)

- Qubit Office may have **100 floors**.
- **Each floor is a department office.**
- Floors are visual/organisational groupings; business truth stays in the Business OS.
- Only active businesses/workflows materialise working agents. The current 283 assigned seats are
  a **seed/demo projection**, not evidence that 283 agents are operational.
- The first real build is **one department floor**, not the whole tower.

### Execution order

1. **Freeze and inventory**
   - Asset ledger, idea ledger, repo-dependency ledger, license/provenance.
   - One authoritative schema document for Business/Department/Room/Agent/Session/Task/
     Artifact/Message/Memory/Approval/CapabilityCall/FXEvent.

2. **One department floor**
   - Use the current office image as the first department template.
   - Reception, work area, meeting/boardroom, focus room, break/kitchen, restrooms, lounge,
     head office, storage.
   - Real room IDs, seats, assets, capability manifest, map, memory scope.
   - 3–5 real agents, not 283 synthetic occupants.

3. **Durable agent sessions and workspaces**
   - Session/thread, run, checkpoint, workspace, sandbox/worktree, artifact, lease, handoff.
   - Stop/resume/restart tests; no lost work.
   - Agent panel showing the real session.

4. **Conversation fabric**
   - Append-only messages: sender, audience, thread, reply, artifact refs, visibility,
     classification, delivery state, idempotency, authorization.
   - Owner↔agent and agent↔agent chat before broad panels.

5. **Capability gateway**
   - One execution boundary for model turns, tools, terminal, browser, files, messaging,
     Etsy, memory writes and MCP.
   - Tenant/agent scopes, approvals, budgets, rate limits, DLP, egress, idempotency,
     dry-run, audit, kill switch.

6. **Memory**
   - Event log authoritative.
   - Department/agent Obsidian projections; OpenViking adapter with authenticated namespaces.
   - Conflict, retention, deletion, export, rebuild and provenance rules.

7. **First real turn + first business workflow**
   - One low-risk read/draft capability through the gateway.
   - Trace prompt, context, tools, approvals, output, artifact, cost, checkpoint, audit.
   - Then Etsy: draft listing → IP/license evidence → owner approval → official OAuth call →
     provider receipt → ledger/reconciliation → unknown/rollback state.

8. **Character Studio and room builder**
   - One canonical CharacterProfile; Habblaud/LimeZu adapters; transactional projections.
   - Department/floor/room builder with validation, versioning, undo and approval.

9. **World**
   - Renderer-neutral map/chunk/layer/door/portal schema.
   - Campus exterior + one department floor in web renderer.
   - Godot only after parity tests; it is a renderer, not a second backend.

10. **Interactions, FX and scale**
    - Interaction engine + mature policy + consequences.
    - Event-to-animation schema; FX replay/cancellation/fallback.
    - 100-floor budgets, active/dormant scheduler, 100/500/1,000-agent load tests.

11. **Security and release**
    - Threat model, secret broker, sandbox, egress, filesystem jail, prompt-injection defense,
      approval anti-confusion, encrypted backups, deletion/export, incident kill switch.
    - Scenario evidence and negative-security evidence, not only unit counts.

### Hard acceptance for the first slice

A department is not “done” until:
- 3–5 agents have durable sessions and recover after restart.
- Owner can chat with an agent; two agents can chat/hand off with artifacts.
- One agent uses a scoped capability through the gateway.
- One task produces a real artifact and audit/ledger record.
- Department memory is visible in Obsidian and queryable in OpenViking, with provenance.
- One approval gates a real external draft.
- The floor map, room styles, characters and FX render honestly.
- No work is lost on restart; failures are typed, not silent.

## 46. Perplexity review — misconfigurations and 15 suggestions

Perplexity reviewed the plan and design doc. Its verdict: strong control-plane foundation and
honest gap reporting, but internally inconsistent, too broad, and sequenced around records,
templates and projections rather than one usable department.

### Misconfigurations to fix

1. Floor contradiction: “no more floors” vs 100 floors. **Resolved:** 100 floors allowed, each
   floor is a department, active-only; first slice is one floor.
2. 283 assigned seats presented as occupancy while no active business/workflow exists. **Fix:**
   treat as seed/demo; staff only the active department.
3. Directory/templates/wave reports treated as world progress. **Fix:** acceptance is a rendered,
   working department, not a directory.
4. Agent records precede sessions/workspaces/messages. **Fix:** sessions and conversation fabric
   before more controls.
5. Permission data/dry-run adapters are not a capability gateway. **Fix:** one executable gateway.
6. Etsy integration before the usable agent loop. **Fix:** gateway + one turn + one workflow first.
7. No authoritative memory write protocol. **Fix:** event log owns truth; Obsidian projection;
   OpenViking adapter with commit/provenance/retention.
8. Unsafe OpenViking namespace assumptions. **Fix:** Qubit adapter, authenticated namespaces,
   no invented direct paths.
9. Obsidian treated as a runtime database. **Fix:** derived projection only.
10. No canonical conversation fabric. **Fix:** one message schema, ordering, delivery, auth,
    retention, artifacts.
11. Rooms lack capability manifests. **Fix:** room → roles/toolsets/workflows/artifacts/KPIs/
    memory/privacy/activation.
12. Interactions have no bounded authority model. **Fix:** separate authoritative HR/legal/
    business events from simulation-only effects; approvals where real.
13. Security/privacy controls incomplete for terminal/browser/files/memory/payments/social.
    **Fix:** threat model, secret broker, sandbox, egress, jail, prompt-injection defenses,
    kill switch, anti-confusion approvals.
14. Personal hosting does not remove tenancy/privacy risk. **Fix:** encryption, access control,
    backup exclusions, deletion/export, personal/business separation, future entity migration.
15. Character Studio has incompatible save authorities. **Fix:** Business OS CharacterProfile is
    authoritative; Habblaud/LimeZu are projections; transactional revisions/rollback.
16. Paid-art safeguards miss runtime/build leakage. **Fix:** ledger, fail-closed builders, scans of
    bundles/caches/screenshots/backups/exports, local-only installation.
17. 100-floor scale lacks budgets. **Fix:** frame/memory/event/persistence/pathfinding/cold-start
    targets and load tests.
18. Active/dormant can starve operational work. **Fix:** scheduler contract, starvation bound,
    leases, queue partition, tests.
19. FX contracts underspecified. **Fix:** event-to-animation schema, cancellation/replay,
    deterministic seeds, authoritative-vs-visual separation.
20. Godot has no portability contract. **Fix:** renderer-neutral map/coordinate/animation/input
    schema and parity tests before any Godot work.
21. Repo reviews are not an implementation dependency ledger. **Fix:** exact commits, behavior
    mapping, rejected code, license/security review.
22. Evidence is too test-centric. **Fix:** scenario, recovery, gateway-enforcement, external
    reconciliation and negative-security evidence.

### 15 suggestions to do

| # | Suggestion | Why | Do | Phase | Effort | Risk |
|---|---|---|---|---|---|---|
| 1 | Ship one capability-bearing department slice | Current work optimizes records/directories, not operations | One floor, 3–5 agents, 4–6 rooms, real tasks/artifacts/workflow | Next | L | Medium |
| 2 | Define one domain/projection contract | Office/tower/Obsidian/OpenViking/renderer imply overlapping authority | Publish schemas and field ownership for all core records | Next | M | Low |
| 3 | Build durable per-agent sessions/workspaces | Controls edit entities that cannot do recoverable work | Session, run, checkpoint, sandbox, artifact, lease, handoff; restart tests | Next | L | High |
| 4 | Build the conversation fabric | Chat/handoff/approval/memory all need one message substrate | Append-only message schema with audience/auth/delivery/artifacts | Next | L | High |
| 5 | Make ToolGateway the only execution boundary | Capability catalog/dry-run is not a security boundary | Route turns/tools/terminal/browser/files/messaging/Etsy/memory/MCP through it | Next | XL | Critical |
| 6 | Build a safe memory adapter | Obsidian/OpenViking direct writes risk divergence/namespace failure | Event log authoritative; Obsidian projection; OpenViking adapter with provenance/retention | Next | L | High |
| 7 | Prove one real agent turn end to end | Preview has no model/network effect; core loop unproven | One low-risk capability, full trace, artifact, cost, approval, checkpoint | Next | L | Critical |
| 8 | Complete Etsy as a human-gated workflow | OAuth/dry-run stops before business outcome | Draft → IP evidence → approval → official call → receipt → ledger/rollback | After 5–7 | L | High |
| 9 | Separate active staffing from seat seed | 283 synthetic occupants imply operations that do not exist | Seed records but staff only the active department; dormant rooms empty/template | Next | M | Medium |
| 10 | Add room capability manifests | Rooms are currently cells/desks only | Roles, toolsets, workflows, artifacts, KPIs, memory, privacy, activation | Next | M | Medium |
| 11 | Build the first real department floor | Tower scene is a projection, not a working floor | Instantiate one floor from the image with real rooms/assets/paths/seats | After 1–10 | L | Medium |
| 12 | Deliver one renderer-neutral map schema | Outside world/Godot are premature without a map contract | Map/chunk/layer/door/portal/coordinate/occupancy schema + web exterior | After first slice | L | Medium |
| 13 | Implement Character Studio transactionally | Habblaud/LimeZu save authorities conflict | Versioned CharacterProfile; validate assets/licenses; publish projections; rollback | After first slice | M | Medium |
| 14 | Add asset/license ledger before more assets | Paid-art constraints lack build/runtime enforcement | Ledger required; fail-closed builders; scan bundles/caches/screenshots/backups | Next | M | High |
| 15 | Add security/privacy/recovery gates | Promised surface includes terminal, credentials, payments, personal data | Threat model, secret broker, sandbox, egress, jail, injection defense, kill switch | Next | XL | Critical |


---

## 47. Agent configuration, sessions and multi-agent runtime

The agent selector model, session/run/task/workspace/checkpoint contract, multi-agent patterns,
verified repo evidence and the 15 Perplexity suggestions are persisted in
`docs/QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md` §21–§25. That file is the reference; this is
a pointer, not a second authority.


---

## 48. External research: gaps and forgotten requirements

A three-part Perplexity review (agent-platform patterns, security/privacy/ops, business
operations) plus reference-repo inspection found foundation gaps that the earlier plan did not
enforce: durable execution/reconciliation, real identity/tenancy, sandbox/secrets/egress, leases
and message fabric, durable approvals, observability/evals, backups/DR, and actual business
systems of record.

The full synthesis, company patterns, must-haves, forgotten requirements and revised execution
order are in `docs/QUBIT-OFFICE-RESEARCH-GAPS.md`. That file is the reference; this section is a
pointer, not a second authority.


---

## 49. Additional research findings

Ten more must-haves (test/staging/replay, data lineage, schema/policy migration, access
recertification, notification/on-call, jurisdiction rules, moderation/asset-use, verifiable
receipts, feature flags, backup-restore verification) and ten nice-to-haves (agent scorecards,
synthetic monitoring, trace review, knowledge lifecycle, search/dedup/entity resolution, trust
center, vendor/model risk registry, deployment rings, disaster game days, local-first sync) are
recorded in `docs/QUBIT-OFFICE-RESEARCH-GAPS.md` §8.

---

## 50. Canonical phased execution order v7

**This section supersedes §45 and any earlier phase list where they conflict.**
No more plan rewrites. New ideas go to the idea ledger; they do not reorder this build.

### Phase 0 — Freeze, contracts and ledgers

**Goal:** stop scope drift and make the system buildable.

Deliverables:
- Frozen execution order and decision log.
- Canonical domain/projection contract: Owner, Tenant, Business, LegalEntity, Department, Room,
  Cell, Desk, Agent, Session, Run, Task, Workspace, Checkpoint, Artifact, Message, Approval,
  LedgerEntry, CapabilityCall, FXEvent.
- Authority table: Business OS event log is truth; office/tower/Obsidian/maps are projections.
- Asset ledger: every file, pack, license, paid/local-only flag, intended room/character/UI use.
- Idea ledger: every owner idea with status (idea/planned/coded/live/missing).
- Repo dependency ledger: repo, commit, license, pattern taken, code rejected, security notes.
- Threat model and data-classification baseline.
- Test/staging/replay environment decision.
- Durable workflow engine decision (Temporal/equivalent or explicit local alternative).

Exit gate: every proposed feature maps to a phase, owner, data authority and evidence requirement.

**Do not start:** 100 floors, character studio, outside world, mature interactions, social accounts.

### Phase 1 — Foundation: identity, durable state, security, observability

**Goal:** make it safe and recoverable before agents do real work.

Deliverables:
- Human/agent/service/workflow identities; local owner auth now, OIDC/SSO-ready.
- RBAC + ABAC policy engine; deny-by-default; tenant/business/department/resource/action/data/
  expiry; revocation.
- Durable execution: runs, tasks, timers, signals, retries, waits, event history, replay.
- Run state machine including `unknown`, reconciliation and dead-letter ownership.
- Append-only event log, audit chain, correlation IDs, metrics/traces/logs.
- Secrets manager/vault, sandbox profiles, filesystem jail, network egress policy.
- Backup/restore, RPO/RTO, tenant-level restore, restore drills.
- Test/staging/replay environments with synthetic externals and fault injection.
- Schema/policy/prompt migration framework.

Exit gate: one durable workflow survives restart; one unauthorized call is refused; one backup is
restored and verified; one trace explains a complete run.

### Phase 2 — Agent runtime and capability gateway

**Goal:** one real agent lane, not 283 records.

Deliverables:
- AgentProfile and selectors: permissions, skills, MCP/tools, model/provider, budgets, autonomy,
  memory scopes, secrets, schedule, appearance.
- Capability catalog with health/version/risk/scopes.
- ToolGateway as the only execution boundary: model, tools, MCP, terminal, browser, files,
  messages, memory, connectors, payments, Etsy.
- Sessions/runs/tasks/workspaces/leases/checkpoints/handoffs.
- Capability snapshots frozen per run; revocation interrupts active calls.
- Message fabric: owner↔agent, agent↔agent, room channels, threads, delivery, replay.
- Durable approvals bound to exact payload/evidence/risk/cost/expiry.
- Memory adapter: event log authoritative; Obsidian projection; OpenViking scoped adapter;
  provenance/retention/deletion.
- Effective-state UI: grants, inherited vs explicit, model, memory, leases, budgets, approvals.

Exit gate: one agent receives a task, uses one capability, gets an approval, produces an artifact,
checkpoints, restarts and resumes without duplication.

### Phase 3 — One department floor vertical slice

**Goal:** the first real Qubit Office.

Deliverables:
- One department template from the current office image.
- Reception, work area, boardroom, focus room, kitchen/break, restrooms, lounge, head office,
  storage.
- Real room/seat/capability manifests; 3–5 real agents, not synthetic occupants.
- Department Obsidian graph + OpenViking scope.
- Room map, character/portrait basics, status/activity feed.
- Owner chat with each agent; agent-to-agent chat; handoff.
- One department business record, tasks, artifacts, metrics and audit.
- Restart/recovery tests.

Exit gate: a user can watch real work, talk to agents, see artifacts and recover after restart.

### Phase 4 — First business workflow (Etsy)

**Goal:** one real business loop, human-gated.

Deliverables:
- Etsy pilot: niche → licensed digital asset → draft listing → IP/license evidence → owner approval
  → official OAuth call → provider receipt → order/download/support/refund → payout → ledger →
  reconciliation → margin report.
- Digital asset versioning, malware scan, entitlements and replacement policy.
- Unknown outcome handling and reconciliation.
- Approval inbox, payload hash, rollback/retry.

Exit gate: one complete Etsy flow with evidence and reconciliation; no fake success.

### Phase 5 — Business systems of record

**Goal:** make the company operational beyond one shop.

Deliverables:
- CRM/leads/contacts/consent/pipeline.
- Support tickets/SLA/handoff.
- Marketing/social campaigns/drafts/approvals/publication receipts.
- Finance/accounting/payments/ledger/reconciliation.
- Contracts/obligations/renewals.
- HR/people/agent lifecycle.
- Procurement/vendors.
- Analytics/KPI contracts and freshness.
- Multi-entity separation (personal now; future entities).

Exit gate: each business action has a system of record and provider receipt, not just a chat reply.

### Phase 6 — Multiply departments and campus

**Goal:** many businesses/departments without breaking the first one.

Deliverables:
- Clone the working department template.
- Department/floor builder: rooms, desks, cells, capacities, paths, validation, versioning, undo.
- Department-specific capabilities, memory, roles, business and assets.
- Campus map, elevators/stairs, shared War Room/Memory/Ops/Comms/Server floors.
- Active/dormant scheduler with fairness, leases and business-continuity guarantees.
- Multi-department permissions, budgets and reporting.

Exit gate: a new department can be created from template, staffed, operated and recovered.

### Phase 7 — Character Studio, UI and world

**Goal:** the visual/product layer over a working company.

Deliverables:
- Character Studio merging Habblaud character state/editor/animation with LimeZu Character and
  Portrait Generators; one canonical CharacterProfile, transactional projections, rollback.
- Full panel inventory and effective-state controls.
- Distinct room styles, furniture sets, lighting, assets and outside world/campus exterior.
- Renderer-neutral map/chunk/layer/door/portal schema.
- Web renderer first; Godot only after parity tests.
- Asset/license enforcement across bundles, caches, screenshots, backups and exports.

Exit gate: visual office reflects real state; no renderer invents business truth.

### Phase 8 — Interactions, FX, mature simulation and scale

**Goal:** make the world alive without compromising operations.

Deliverables:
- Interaction engine: needs, traits, relationships, action catalog, consequences.
- Mature-content policy off by default; non-graphic, consent/HR/legal guards.
- Event-to-FX schema, replay/cancellation/fallback, reduced motion.
- Outside-world life, day/night/weather/seasons.
- 100/500/1,000-agent load tests, frame/memory/event/persistence budgets.
- Capacity, backpressure, disaster game days, release rings, evaluations.
- Optional Godot/2.5D/3D campus after web parity.

Exit gate: interactions and scale do not weaken durability, security or business truth.

### Phase 9 — Release and operations

**Goal:** run it continuously.

Deliverables:
- Security/privacy review, threat-model tests, negative tests.
- Backup/restore drills, incident response, on-call, kill switch.
- Feature flags, deployment rings, canary/rollback.
- Observability dashboards, evals, scorecards, synthetic monitoring.
- Self-hosting contract: setup, migrations, health, import/export, telemetry policy.
- Exit/manual runbooks and shutdown/close procedures.
- Final matrix once, on a frozen SHA.

**Start point:** Phase 0. The first commits are contracts, ledgers, threat model, durable-workflow
decision and test/staging environment — not the tower, character editor, outside world or mature
interactions.


---

## 51. Phase 0 artifacts

The missing Phase 0 artifacts are now written:

- `docs/QUBIT-OFFICE-IDEA-LEDGER.md`
- `docs/QUBIT-OFFICE-ASSET-LEDGER.md`
- `docs/QUBIT-OFFICE-REPO-LEDGER.md`
- `docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md`
- `docs/QUBIT-OFFICE-THREAT-MODEL.md`
- `docs/QUBIT-OFFICE-PHASE-0.md`

They are design/inventory records, not implementation evidence. Phase 0 is complete only when its
exit gates in `QUBIT-OFFICE-PHASE-0.md` have evidence.


---

## 52. Consolidated complete plan

`docs/QUBIT-OFFICE-COMPLETE-PLAN.md` is the single navigable complete plan: current state, scope,
authority map, architecture, cross-cutting contracts, Phase 0–9 tasks, exit gates, artifact status,
research gaps, deferred work and immediate next actions. The detailed contracts and ledgers remain
authoritative for their areas; this is the navigation and sequencing entry point.


---

## 53. Plan review and MVP simplification

The plan set was audited for contradictions, duplicate phase orders and overengineering. The
review and canonical MVP scope are in `docs/QUBIT-OFFICE-PLAN-REVIEW.md`; `QUBIT-OFFICE-COMPLETE-PLAN.md`
§11 records the simplified first slice. Until that MVP passes, no tower, character studio, outside
world, mature interactions, Temporal, SSO, multi-tenant or multi-entity work starts.


---

## 54. Full repo mapping

All external reference repos recorded in §22 are mapped with take/avoid/licence/MVP relevance in
`docs/QUBIT-OFFICE-REPO-LEDGER.md` §2. The MVP shortlist is Habblaud, SkyOffice, pixelfx,
claw-empire, agent-monitor/agents-office concepts and Agent Office/JSM HQ sandbox concepts. The
ERP/CRM/business repos are later references, not MVP code.


---

## 55. Full-scope decision v9 (owner)

The owner rejected the MVP simplification. Full product scope is committed from the start: all
departments, floors, rooms, facilities, outside world, 283+ agents, businesses, panels, Character
Studio, FX, interactions, business systems, durable execution, identity/security, observability and
backup/DR.

`docs/QUBIT-OFFICE-COMPLETE-PLAN.md` §11 is the full-scope execution plan. `docs/QUBIT-OFFICE-PLAN-REVIEW.md`
remains a bug/risk review; its MVP recommendation is rejected. `docs/QUBIT-OFFICE-PHASE-0.md`
retains Temporal as the full-scope durable engine. Phases are dependency/merge order, not feature
cuts.


---

## 56. 2026 improvement review

`docs/QUBIT-OFFICE-IMPROVEMENTS-2026.md` records the 2026 release/UI/map/scale review:
new-release claims (reported/uncertain), platform improvements, UI/map/room ideas, scale
problems/mitigations, performance budgets and technical choices. Full scope remains; the review
adds dependency gates and measurable budgets, not scope cuts.


---

## 57. Browser comparison review

`docs/QUBIT-OFFICE-BROWSER-REVIEW.md` compares the plan with current agent-platform, multi-agent
and office-game builders. It adds an agent registry, A2A, one-writer-per-resource, retry/handoff/
delegation ceilings, per-tool evals/schema canaries, protected checks, Tiled map pipeline, renderer
benchmark, context-loss/dirty-render/device gates and active-set rules. Full scope remains; this
review adds production guardrails and measurable corrections.


---

## 58. Mandatory 100% gate

`docs/QUBIT-OFFICE-MANDATORY-CHECKLIST.md` is the obligatory gate: OWASP Agentic Top 10 2026,
MCP OAuth 2.1/PKCE/resource/audience/tool-schema rules, sandbox/RCE, approvals, memory, A2A,
WCAG 2.2 AA, OpenTelemetry GenAI, Temporal durability, privacy/licensing and performance/device
budgets. No release or `VERIFIED` claim while an applicable row is FAIL/UNTESTED/waived without
owner sign-off.


---

## 59. Professional execution plan v10

`docs/QUBIT-OFFICE-COMPLETE-PLAN.md` was rewritten as the single professional full-scope plan:
reuse-first, no MVP, Phase 0–8 with tasks/exit gates, parallel tracks, mandatory gates, corrected
phase order and bug/misconfiguration fixes. Durable execution uses the existing SQLite control
plane first; Temporal is a later adapter when measured need justifies it.


---

## 60. Trading Department

`docs/QUBIT-OFFICE-TRADING-DEPARTMENT.md` adds a full Trading Department for crypto spot, meme
coins and forex, plus a research lane for YouTube strategies. It defines roles, floor/rooms, market
data, strategy/backtest/paper trading, OMS/EMS, risk engine, double-entry ledger, exchange/broker
adapters, approvals, kill switch, legal/tax obligations and mandatory live gates. The department
exists from the start; live capital requires owner risk-policy sign-off and all safety tests.


---

## 60. Executable execution plan v11

`docs/QUBIT-OFFICE-COMPLETE-PLAN.md` is now the executable phase contract: input/output artifacts,
predecessor DAG, owner/reviewer, exact acceptance commands, failure tests and merge gates. The
reference vertical lane is mandatory architecture validation, not an MVP. Phase order is now
explicit: contracts → minimal execution boundary → agent runtime → campus projection → workforce /
business operations → UI/world → simulation → scale/release. Durable SQLite first; Temporal is a
later measured-HA adapter. Trading has its own research→backtest→paper→owner-readiness track.

The architecture and low-tier-agent audit is in `docs/QUBIT-OFFICE-PLAN-AUDIT-V10.md`.
