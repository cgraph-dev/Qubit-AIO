# Qubit Office — mandatory UI implementation contract for weak agents

**Status:** canonical UI design/implementation contract. Design only; not `VERIFIED`.
**Use:** every frontend phase must implement this contract exactly before changing behavior.
**Rule:** the office/room/map is a projection of real business state. A visually working desk, room
or animated agent is not evidence of running work.

**Source of UI truth:** `frontend/qubit-app/plugins/qubit-office/game/app/`,
`voicebridge/office/world/` and `voicebridge/business/`. UI must not be a second database or
session authority.

---

## 1. Exact office shell

### Required layout

```text
Dashboard top: Qubit Office · Connexion status · Rooms · Agents · Working · Needs you
Dashboard toolbar: Search · Agents · HUD · Approvals · Session · map settings
Office host canvas (Habblaud-derived scene)
  Agents dock (left)      |       HUD dock (right)
  @office-dock-top=toolbar | other panel
```

**Preserve:**
- The office iframe occupies the host content column at full available height/width.
- Desktop agents left, HUD right. Keep a visible 24 px gap when both docks are open.
- Dock top uses the existing toolbar offset (`--office-dock-top:128px` and
  `data-office-dock-top="toolbar"`). Never cover Panels, Agents, HUD, Settings or Help icons.
- One dock is `min(44rem,92%)`; both docks split available width `calc(50% - var(--office-s3))`.
- Close buttons stay clickable. Agents close remains accessible while HUD is open.
- Phone: docks stack, heads wrap, no horizontal overflow.
- Only the Qubit Office loader appears. Habblaud `.ui-splash` is hidden/removed, permanently.
- Header/brand is **Qubit Office**, never Habblaud. Legal credit lives in About/licence notices.

**Core visual style:**

- Pixel-art warm interior: dark warm shell, warm floor panels, readability-first contrast.
- Stable pixel grid, no antialiased sprites, no arbitrary CSS scaling, no stretched cells.
- A department is not a static image: it has real room/cell/desk identifiers and capabilities.
- Habblaud supplies visual state/layout conventions, not domain authority.

### Required component locations

- `frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.tsx` — shell, dock/layout,
  frame loader, menu injection, keyboard/focus.
- `.../office-app.ts` — frame path/source.
- `.../api-bridge.ts` — only whitelisted GET/read mappings and typed refusals; never expose writes
  through the iframe.
- `.../agents-panel.ts`, `AgentsPanel.tsx` — real roster, grouping, seats, portraits/fallbacks.
- `.../AgentControlDrawer.tsx` — selector/controls/permissions/runtime editor.
- `.../TowerNavigator.tsx` — floor/room navigation.
- `.../CharacterStudio.tsx`, `.../RoomBuilder.tsx`, `.../FloorNavigator.tsx` — required future
  modules; do not start a second editor/store with different names.

The Habblaud file tree is vendored at
`frontend/qubit-app/plugins/qubit-office/habblaud/` and may be changed only with additive,
compatible changes; never blind-find/replace whole modules or delete required menu logic.

---

## 2. Canonical map, floor and room model

The schema uses:

```text
Campus → Wing → DepartmentFloor → Room → Cell(16×12) → Desk/Seat → Agent
```

Each floor is a **complete department**, not a skin. It has:

- Entrance/reception and lobby/elevator/stairs link
- Open desk/work area with headcount and role-specific desks
- Boardroom/meeting room
- Focus room(s)
- Department head office
- Kitchen / break / kitchenette
- Restrooms / bathrooms
- Lounge and quiet/rest area
- Review/QC room
- Storage/archive
- Comms/ops corner
- Optional department-specific lab (trading, legal, media, science, support, code, etc.)

Shared floors/rooms: War Room, Memory Room, Approvals/Ops, Comms, Server/Infra, Studio, Training,
Lobby/Security/Mail, cafeteria/atrium, garden/outside. A business can span floors; a floor is an
organisational department, not automatically a legal entity.

### Map schema required fields

- `campus_id`, `wing_id`, `floor_id`, `room_id`, `cell_id`, `desk_id`, `agent_id`
- room kind/archetype, bounds, doors, required rooms, access policy
- walkable cells, collision, capacity, egress, portal links
- tile coordinates and `z_index`
- asset/style references and hashes, no paid asset redistribution
- `activation_state`, `memory_scope`, `capability_manifest_id`, `owner_id`, `status`
- schema version and compatibility/migration metadata

**Hard constraints:**
- 16×12 cells; never stretch a cell. Larger rooms use 2/3/4 cell clusters.
- Desks are `2×1` tiles unless a specific object uses another footprint.
- Stable ID format `roomId:cellId:localSeatId`.
- Doors/portals must exist and connect valid rooms. No unreachable desks.

---

## 3. Renderer rules (Pixel art)

- Tiles default to **16×16**; 32/48 only for portraits, close-ups and UI.
- **Nearest-neighbour** sampling; no bilinear smoothing.
- Zoom only in integer multiples or a documented pixel-perfect ratio.
- Sprite ID, tile origin, atlas page and UV rectangle are stable and hash-versioned.
- Atlas pages by visual style and resolution; controlled padding, no leaking paid art.
- Z-order: terrain/floor → walls/props → furniture → characters → overlays/effects → UI.
- Character animation: named sets (idle/walk/type/talk/think/break/celebrate), deterministic timing.
- Static rooms bake into a render texture once where supported; do not redraw static detail every
  frame.
- Pools for sprites/tiles/effects; do not allocate new Texture per frame.
- Camera bounds: clamp to current floor bounds; no free-camera 3D in the first implementation.
- Existing Habblaud conventions drive sprite/state layout. Do not redesign the art engine.

### Performance budget

- P95 frame ≤16.7 ms desktop, ≤33.3 ms low tier.
- Active fully simulated agents ≤40 by default; visible avatars ≤80; throttle distant avatars.
- One active floor, optional bounded adjacent-floor prefetch; cancellation on floor change.
- Pathfinding ≤2 ms average and ≤8 ms P95; use hierarchical paths/portal reservations.
- Dirty/static render whenever possible; bounded LRU atlases/chunks; no unbounded variant cache.
- Browser heap ≤256 MB low tier / ≤512 MB desktop after 10 min floor navigation.
- Client events ≤20/s normal, ≤60/s burst with coalescing.
- Reduced motion still preserves essential status cues.

**Benchmark required:** `python scripts/office_benchmark_renderer.py` on the same map before
choosing Phaser/Pixi. Selection changes the renderer, never the visual semantics.

---

## 4. Floor and room navigation

Required behavior:

1. Left: wing/floor/department navigator with search/filter/favorites/recents.
2. Center: active floor. Right: selected room/agent/task detail drawer.
3. Minimap: rooms, doors, portal, active agent, target, blocked paths.
4. Elevator/stairs: jump instantly; optional short transition; never forced traversal.
5. Back/forward history and direct URL: `/qubit-office/floor/<floorId>?room=<roomId>&agent=<id>`.
6. Floor switch preserves selection when possible and aborts old request/socket subscriptions.
7. Active + bounded adjacent-floor loading with placeholder/error/cancel states.
8. Search by agent, role, room, desk, task, artifact, memory, status; "Jump Here" always available.

Interaction:
- Click room → room card with capability manifest, occupants, artifact/workflow hooks.
- Click desk/agent → agent drawer. Clicking a seat never invents a running agent.
- Builder edits use draft/version/preview/undo/approve; no free-form live wall rewrites.
- No all-100-floor full-fidelity canvas. A campus/minimap is semantic data plus lightweight art.

---

## 5. Character Studio — one canonical profile, three adapters

**Never maintain three editors.** One studio edits `CharacterProfile`; all outputs are disposable
projections from that profile.

```
CharacterProfile
  profile_id, agent_id
  body_id, skin_id, eyes_id, hair_id, outfit_id, accessory_ids
  palette/colors, layer_order, shadow_policy
  portrait_cell, portrait_size (32/48), animation_set
  style_version, seed, revision
  source_asset_refs (local paths/hashes only)
```

Adapters:
- **Habblaud:** character state/editor layers, sprite/animation conventions.
- **LimeZu Character Generator 2.0:** body 9 / eyes 7 / hair 29 / outfit 33 / skin 9 parts.
- **LimeZu Portrait Generator 1.5:** portrait cells/atlases for agent cards/close-ups.

Required studio features:
- Category pickers/search/counts, randomize, part locks, undo/redo/presets.
- Layer order, palette, shadow policy, missing-part fallback and migration.
- World-scale and portrait preview; animation preview idle/walk/type/talk/think/break.
- One authoritative save in Business OS; Habblaud/LimeZu are projections only.
- Versioned revisions, import/export JSON, paid-art local-only check.
- Accessible keyboard operation, live text names for parts and visible errors.

Target: `frontend/qubit-app/plugins/qubit-office/game/app/CharacterStudio.tsx` plus backend
profile adapters, never a separate profile database.

---

## 6. Required panels and what to reuse

| Panel | Authority / use | Reuse, don’t rebuild |
|---|---|---|
| Agents & roster | Business OS roster, seat→agent links | Habblaud roster visuals; agent-monitor session cards |
| Agent controls | Agent grants/model/policy/budgets | agents-office selector **concept only** |
| Agent chat/session | Durable session/messages | agent-monitor meeting UI pattern, not its state |
| Deliveries/timelapse | artifacts/receipts/event log | Habblaud deliveries/timeline |
| War Room | sessions/evidence/debate/decision | council research, no Perplexity hardcode |
| Approvals/Ops | approval journal, jobs, risks, reconciliations | Qubit authority; exact payload preview |
| Memory Room | Obsidian projection + OpenViking adapter | scoped memory/provenance, no Brain model |
| Character Studio | canonical CharacterProfile | Habblaud + LimeZu adapters |
| Room/floor builder | draft map schema, validation | Tiled-compatible map pipeline candidate |
| Campus map / floors | map schema, room/seat/capabilities | SkyOffice command/portal/map patterns |
| Outside world | exterior/lobby/weather/chunks | LimeZu licensed local assets, pixelfx |
| Trading | market data, strategy/risk/ledger | Trading Department design; no live by default |
| CRM / support / marketing / finance / contracts / HR / analytics | systems of record | ERP/CRM repo concepts later; no data fabrication |
| Audit/settings | policy/revocation/grants/tool versions | Qubit policy compiler, gateway |

**Required shell behavior:** each panel has authority, access checks, loading/empty/error/unknown
states, deep-link route, split/stack rules, focus restoration and cancel of stale work. Panels are
not a new state store.

---

## 7. Agent status and action semantics

Show every state explicitly: `seed/projection`, `live`, `idle`, `paused`, `stopped`, `revoked`,
`archived`, `disconnected`, `unknown`, `replay/simulated`.

- A seat/avatar presence never implies working.
- `Working` only while a durable run is executing.
- `Completed` only from artifact/receipt/provider confirmation.
- `Unknown` stays unknown until reconciliation; never label it completed.
- Replay/seed/simulated markers must be visually distinct.
- Task/action controls check capability, actor, scope, expiry and approval.
- Destructive/stop/revoke actions require confirmation, pending indication, operator attribution,
  cancellation timeout and receipts.
- No fake “running” or “done” from a seat sprite.

Use agent-quest state/liveness semantics and pixel-agents team grouping only; never use pixels as
truth.

---

## 8. UI access, permissions and privacy

- All map/panel action paths: **UI intent → ToolGateway → policy check → action → event/projection**.
- No iframe mutation shortcut, no direct shell/file/network mutation.
- Presence, room names, agent tasks and memory are redacted by policy: public / department / private
  / restricted, with coarse status fallback.
- Full args/price/risk/target shown before approving high-impact actions; approval expires on change.
- Agents cannot edit tests, evaluation, policy, gates, manifest or approval code.
- Agents can never remove safety approvals from controls.

---

## 9. Accessibility required per component

Each new panel ships all of these in its first version:

- semantic DOM equivalent for all canvas entities (floor/room/desk/agent/task)
- keyboard-operable activation and focus-visible state
- visible focus restore after floor/tab/dialog change; no canvas keyboard trap
- loading, empty, error and status announcements with meaningful text
- non-color indicators for working/approval/error/unknown
- visible labels and accessible names; no raw icons without text
- reduced motion setting preserving status and navigation
- zoom 200% without horizontal scroll, minimum target size, reflow
- screen-reader-reviewed loading/error states; no silent disabled core workflow

Mandatory tests: keyboard-only, NVDA/VoiceOver/TalkBack, high-contrast mode, 200% zoom, touch,
muted audio and reduced-motion.

---

## 10. Source-of-reuse map and explicit avoidances

- **Habblaud bundle/iframe:** use its rooms/seats/furniture/menu/character conventions; avoid its
  runtime as authority, its splash, or opaque engine assumptions.
- **SkyOffice server/rooms/commands:** use room/player/chat command boundaries and room/map patterns;
  do not use its player state as Qubit session truth.
- **agents-office config/roster/skills:** use per-agent selector ideas only. PolyForm/no bundle;
  skills cannot grant capabilities.
- **agent-monitor AgentMeeting/useAgentChat:** use meeting/chat UI patterns; map messages to durable
  authorized event log, never its live-state semantics.
- **agent-quest state-manager:** use session/liveness vocabulary, idle/busy/disconnected transitions;
  do not infer activity from seat location/avatar.
- **claw-empire README:** worktree/dept/skill ownership ideas; not proof of workers or completion.
- **pixel-agents teamUtils:** lead/teammates presentation; membership is not permission/status.
- **LimeZu:** art, palette/furniture/body/portrait parts; local-only, licence ledger required.
- **pixelfx:** event-driven loading/status/weather FX; no fake ambient work.
- **Tiled:** candidate import/export/portal pipeline; map objects contain no business facts.

See `docs/QUBIT-OFFICE-REPO-LEDGER.md` before copying code. No LICENSE file = concepts only.

---

## 11. Acceptance contract

These are required future interfaces, not passing claims yet:

- `python scripts/validate_campus_schema.py` — all IDs, bounds, doors, portals, collision, capacity,
  egress and ownership validate.
- `python scripts/office_benchmark_renderer.py` — budget report for the selected renderer.
- `python scripts/verify_character_profile.py` — profile/projection roundtrip, missing part fallback,
  revision migration, paid-art guard.
- `npx vitest run --config vitest.office-app.config.ts` — all office panels pass.
- `scripts/acceptance/phase-3.sh` → `evidence/phase-3/manifest.json`
- `scripts/acceptance/phase-6.sh` → `evidence/phase-6/manifest.json`
- `python scripts/verify_evidence.py evidence/phase-*/manifest.json` — freshness and hash verification.

Negative acceptance tests must include:
- Habblaud splash re-appears → removed by host patch.
- Dock covers top menu → fails until `data-office-dock-top=toolbar` is correct.
- Both docks open → 24 px gap remains, no overlap/overflow.
- Static room stretched >16×12 → rejected.
- Agent disappears offscreen but keeps working → no animation required and business continues.
- 100 floors navigated quickly → cancellation, LRU and no unbounded memory.
- Paid asset in public bundle/export → build fails.
- Seed avatar with no run → never rendered working/completed.
- Screen reader keyboard-only traversal finds the same agent/room/task.
- World closes while workflow runs → no task is lost.

## 12. What a weak agent must not do

- Do not invent UI art, state, rooms, floors, avatars, panels, credentials or provider results.
- Do not replace the office renderer before the existing Qubit shell has a projection interface.
- Do not create three character editors or duplicate databases.
- Do not put Habblaud branding or splash back in the product.
- Do not hardcode Perplexity, Etsy, or a single model provider into agent behavior.
- Do not write a new map engine, state engine or second ToolGateway.
- Do not render all 100 floors/all agents continuously.
- Do not call a directory/template a live business or agent.
- Do not hide unknown/pending states behind a “done” badge.
