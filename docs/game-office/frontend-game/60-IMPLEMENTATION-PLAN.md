# Sequential implementation plan

**Goal:** a working Japanese-anime office game frontend at the existing route, with building entry/exit, floor/room travel, character interaction, settings and complete operational panels.
**Architecture:** host React + one Phaser world, typed backend adapter, separate view/presentation/domain state.
**Execution:** one Sol 6.1 worker; no delegation, auto-commit/push, backend rewrites, service toggles or paid art generation. Planning permission is not implementation permission.

## How to use these packets

Execute in order. Each packet is split into observable small steps and declares exact proposed paths. New paths/test names below do **not** exist yet unless explicitly called existing. Commands for future files are planned gates, not already-passing proof.

Respect implementation-first verification: write behavioral tests/fixtures alongside each packet, run only minimal focused checks needed to prevent damage, and record deferred checks. Full final matrix runs after implementation is frozen and the integration lead permits it. No repeated broad suite loop.

## F00 — Approve scope/amendment and resolve authority

**Docs:** existing `docs/game-office/10-DECISIONS.md`, `05-AMENDMENT-LOG.md`, `28-HUD-PANELS.md`, `95-CONTRACT.md`, `99-HANDOFF.md`; this spec set.
1. Record approved engine/presentation/interaction decisions without rewriting historical rulings.
2. Resolve C4 staged ops and C7 truth-vs-animation, and the scoped single-worker execution rule.
3. Correct only confirmed stale coverage/range statements; keep unknowns labelled. Do not edit frozen release contracts.
**Proof:** owner approval reference + a contract/test-change list. **Stop:** no implied authority to delete restrictive tests.

## F01 — Rollback baseline and fixture contract

**Existing:** old office source, manifest/fleet copies, bundle builder; **create:** `frontend/qubit-web/src/lib/office-game/fixtures/tower-recording.json`, provenance note.
1. Capture/archive only office-owned baseline files without committing unrelated work.
2. Freeze real payload shape from evidence; mark recording timestamp/source and preview status.
3. Inventory asset paths, module globals and currently broken journeys.
**Proof:** archive manifest and restore rehearsal in scratch; recorded fixture is never labelled live.

## F02 — Repair interaction lifecycle and camera state

These narrowly scoped repairs make the retained baseline a usable rollback/reference
and characterize the bug class. They are **not** the redesign. If the old entry is
retired immediately, carry their behavioral checks into the new implementation rather
than spending a separate release cycle polishing the rejected page.

**Modify:** `frontend/qubit-app/plugins/qubit-office/qubit-office.js` input/lifecycle/camera sections; **create tests:** `frontend/qubit-web/src/lib/office-game/input.test.ts`, `camera.test.ts`, `selection.test.ts`.
1. Bind listeners after the canvas exists; dispose/rebind across loading/error/empty/remount.
2. Separate canonical fitted camera from user camera; implement refit/reset explicitly.
3. Store selected agent ID; derive inspector from current snapshot.
4. Add drag threshold, pointer capture, buttons filtering and cursor-anchored zoom.
**Proof:** initial-load wheel/drag/click work; Zoom→Fit→Reset returns canonical framing; selected state updates on new revision. No extra snapshot/agent run from camera gestures.

## F03 — Fix semantic presentation seams

**Modify:** `qubit-office.js`, `sprites.js`, `atlas.js`; **tests:** `sprite-state.test.ts`.
1. Thread real semantic state and stable appearance ID to atlas selection.
2. Keep feet anchoring/tint/depth; add named asset failure/retry diagnostics.
3. Verify working/meeting/blocked/unknown/idle clip selection without manufacturing backend events.
**Proof:** compare expected sprite pose IDs and rendered fixture regions; retained no-phantom-work rule.

## F04 — Phaser/host-React packaging spike

**Create:** `frontend/qubit-app/plugins/qubit-office/game/{entry.ts,GameHost.ts,OfficeGameShell.ts}`, `frontend/qubit-web/vite.office-game.config.ts`; **modify:** owning frontend manifest/lockfile and office build pipeline only after dependency approval.
1. Verify/pin approved Phaser 3 version and APIs; do not copy Phaser 4 assumptions.
2. Compile one entry registering existing `qubit-office`; host supplies React/hooks.
3. Mount/destroy one engine in StrictMode; test plugin host and actual asset paths.
4. Record bundle size, load cost and licensing/provenance. Compare Canvas/Pixi fallback only if this spike fails.
**Proof:** one functioning scene/input surface, one React runtime, no duplicate registration; cold reload/unmount clean. This packet does not produce the final game.

> **F04 STATUS — done 2026-10-14, with one decision left open.** The packaging path is proven:
> TypeScript → one IIFE, React external, clean mount/destroy/remount, deterministic build, 12 tests
> (`tests/test_office_game_packaging.py`). The artifact is `qubit-office.game.js`, ADDITIVE — the
> manifest still names the existing bundle and a test holds that.
> **Phaser is NOT installed and NOT chosen.** Step 1 ("verify/pin approved Phaser 3") is blocked on
> the owner: Phaser 3's latest is `3.90.0` but **Phaser 4 is now GA at `4.2.1`**, which it was not
> when P02 was written. Measurements, the reversibility argument and the open decision are in
> `evidence/F04-PACKAGING.md`. The `SceneHost` seam makes any of Phaser 3 / Phaser 4 / PixiJS a
> one-factory swap.

## F05 — Data adapter and truthful state lifecycle

**Create:** `game/{types.ts,adapter.ts}`, tests `adapter.test.ts`, `snapshot-recovery.test.ts`.
1. Normalize real tower into frontend model; keep IDs/nulls/evidence and capability availability.
2. Implement serialized abortable snapshot loads, stale retention and revision/generation fence.
3. Bind existing event transport only after proving actual office payload compatibility; otherwise explicit snapshot fallback.
4. Add visibility pause/resume for presentation/network only.
**Proof:** slow/out-of-order/error/unmount responses cannot regress revision or blank last-known scene; global org never paused by hidden tab.

> **F05 STATUS — done 2026-10-14.** `game/types.ts` (typed from the REAL recorded payload, no invented
> fields) + `game/adapter.ts`: serialized abortable loads, revision fence, stale retention, visibility
> pause, provenance that never calls a recording live. 15 tests
> (`tests/test_office_game_adapter.py`), all passing; the fence is falsifiable (disabling it fails
> `test_a_late_response_cannot_overwrite_a_newer_one`). Two real defects were caught by the tests and
> fixed: dispose() left `loading` stuck true, and a failed read reported provenance `none` while
> claiming a payload.

## F06 — Navigation model and portal graph

**Create:** `game/navigation.ts`, `input.ts`; tests `navigation.test.ts`, `input-context.test.ts`.
1. Define exterior/building/lobby/floor/room/portal identities and pure transition reducer.
2. Separate visitor viewpoint from worker location; derive single headquarters wrapper for legacy preview only.
3. Encode location deep links, Back, restore and invalid-target fallback.
4. Route keyboard/touch/UI inputs through explicit world/panel/text/modal/editor contexts.
**Proof:** navigation reducer round-trips every enter/exit/floor/room path; editing text never moves viewpoint or closes the app.

> **F06 STATUS — done 2026-10-14 (BOTH halves).** 
>
> **Half 1 — the reducer** (`game/navigation.ts`): pure, over explicit place identities
> (exterior/building/lobby/floor/room), portal graph DERIVED from the real floorplan, deep-link
> encode/decode validated against real ids, capped Back, typed refusals for portal-less hops.
> 16 tests (`tests/test_office_game_navigation.py`).
> **One real bug found and fixed by these tests:** the first cut skipped the `lobby → floor 1` portal
> because the lobby *is* floor 1 — which made floor 1's rooms (the boardroom) unreachable.
>
> **Half 2 — the input-context layer** (`game/input.ts`), which the plan's step 4 requires and which
> carries the proof's second clause. Input is routed by the focused CONTEXT, not by the key: the
> contexts form a strict containment chain (`world` ⊂ `panel` ⊂ `modal` ⊂ `text` ⊂ `editor`) and a
> key is offered innermost-first, so **if any text-bearing context is active the world cannot
> interpret the key at all**. That makes "typing never navigates" structural rather than a list of
> exceptions. 16 tests (`tests/test_office_game_input_context.py`).
> **A real routing bug found by those tests:** the panel guard was `!active.includes("world")`, but
> `world` is always active as the base context — so a panel never swallowed movement and the world
> walked underneath an open panel.
>
> **Wired, not just written:** the shell binds `keydown` to the container (never the document), routes
> it, and applies travel/back through the reducer. A test asserts the BUILT artifact actually adds AND
> removes the listener — an imported-but-unbound module is the F02 bug in its general form. That test
> was caught being a false positive (it passed on a dead keyboard) and strengthened to match the
> binding's shape.
>
> Both halves falsifiable: removing the typing guard fails 4 tests; reintroducing the missing
> `lobby → floor 1` portal fails the reachability test; deleting the binding fails the wiring test.

## F07 — First playable vertical slice

**Create:** `game/scenes/{BootScene,DistrictScene,LobbyScene,FloorScene}.ts`, `systems/{WorldAssembler,CameraController,InteractionController}.ts`, essential HUD/map components.
1. Build exterior entrance + reception/lobby + elevator/stairs + floor landing + two rooms.
2. Implement approach/click/E/keyboard/touch interactions and symmetric exits.
3. Preload destination assets and keep previous scene on failure; add skippable/reduced-motion travel.
4. Show one original/scaffold character tied to a real/recorded identity, not spawned staff.
**Proof:** outside→enter→lobby→elevator→floor→room→back→exit succeeds by mouse and keyboard; location survives reload.

**Visual gate:** the owner accepts the facade/lobby/interior/cast/HUD direction as
the requested game, not just an engineering prototype. Until then the slice stays a
prototype; do not produce the full asset batch or claim the visual problem solved.

## F08 — Responsive game shell, panels and settings

**Create:** `game/ui/{Hud,PanelHost,SettingsPanel}.ts`, `styles/{tokens.css,game.css}`; **modify:** scoped host layout in `App.tsx` and appropriate full-height route predicate, not CHAT routes.
1. Allocate actual full-height world area; retain Exit to dashboard/emergency control.
2. Build one primary panel/drawer/exclusive-modal policy with focus restoration.
3. Implement Display/Audio/Controls/Interface/Notifications/Accessibility/Connection/Privacy settings with dirty state, preview, reset/cancel/apply and explicit scope.
4. Add container/DPR observer, 390px layout, text zoom and reduced motion.
**Proof:** settings change only local view controls unless a named scoped backend action is confirmed; modal/input contexts work at desktop/mobile sizes.

**STATUS — partial (2026-10-14).** Landed, with `tests/test_office_game_shell.py` (22 tests, falsified 2 ways):

| Built | Note |
|---|---|
| `game/styles/tokens.css` | Every shell value is a token. Text zoom turns ONE knob (`--office-scale`); reduced motion and contrast are token swaps, not filters — a filter over the scene would wash out the state animation colours, so a `blocked` agent could read as `idle`. |
| `game/styles/game.css` | Layout, HUD, panel/modal/scrim, the 390px breakpoint. Every rule is scoped to `.office-game` (asserted, so the office cannot restyle the dashboard). |
| `game/ui/settings.ts` | The setting specs, the scope rule, dirty state, safe persistence, shell attributes. |
| `game/ui/PanelHost.ts` | One panel, one drawer, exclusive modal; real focus trap with wrap and restore; the F06 input contexts derived from panel state. |
| `App.tsx` route | `/qubit-office` added to `FULL_HEIGHT_ROUTE_PATHS` — **not** `CHAT_ROUTE_PATHS`; the two predicates answer different questions and the test asserts they stay separate. |

**The scope rule is real, not decorative.** `floorLayout` is declared `backend` scope with action `office.floor.update`, because a room's objects grant its occupants' tools (C12) — so rearranging a floor changes capabilities. Applying it through the local store is REFUSED at runtime and asserted by a test, it is never persisted or restored from storage, and the control renders DISABLED with its explanation rather than hidden (hiding it would make the rule invisible).

**Focus is a behaviour here, not an attribute.** Tab wraps at both ends, Shift+Tab wraps backwards, a disabled control is skipped, an empty trap returns null instead of throwing, and closing a modal restores focus to the control that opened it. All five are tested; two were falsified by mutation (removing the refusal, and replacing the wrap with a clamp).

**Still open in F08:**
0. **3 pre-existing failures, NOT from this work — RESOLVED.** `tests/test_office_hud_panels.py` (P4, P5) and `tests/test_office_plugin_wiring.py::test_the_declared_entry_loads_and_defines_its_modules`. Verified pre-existing by stashing my edits and re-running: they fail identically on `186abf7d4`. **They had TWO distinct causes, and the entry promotion was only one of them.**

   - **The HUD-panel failures (P4, P5) were NOT the globals at all — they were a stale harness against a changed renderer contract.** `tests/test_office_hud_panels.py` reads the renderer SOURCE (`qubit-office.js`), never the bundle, and its fake `window` never supplies the globals — so it has always rendered the "Office modules not loaded" fallback path, before and after the promotion. What actually changed is the *selection contract*: commit `0c26cb2c6` moved the inspector to select by **`agent_id`** (`pick` holds an id, resolved against the CURRENT `v.characters`) so the panel cannot pin to a click-time revision. The harness still passed the character **object** as `pick`, which no longer matches, so the inspector rendered "That agent is no longer in the tower." Verified directly: running the current test harness against the *pre-game* renderer (`276aa3e14`, where `pick` WAS the object) passes, and against the current renderer fails — with the globals null in both. The fix is the harness, not the renderer: it now does what a real click does (character in `characters[]`, `agent_id` as `pick`); every assertion is unchanged.
   - **The wiring failure WAS the globals, and the resolution is option (c): they are obsolete on the SERVED surface.** They are the exports of the hand-stitched `qubit-office.bundle.js`, and they exist only to feed the classic canvas `qubit-office.js`. The manifest now promotes the Vite game (`qubit-office.game.js`), which registers its own component and needs none of them; the classic bundle is kept ONLY as the rollback artifact (`tests/test_office_game_packaging.py` asserts it stays on disk). **(a) bundling the classic exports into the game would ship a second, unused renderer; (b) loading both is impossible AND forbidden** — `usePlugins.ts` injects exactly ONE `<script>` per manifest `entry` (the manifest has a single `entry` string), and `docs/game-office/frontend-game/30-ARCHITECTURE.md` states the manifest "selects exactly ONE entry. Never leave duplicate native and plugin route owners active." The old surface's real guarantee — *the file the browser loads is not inert: it runs and registers a live React component for the tab* — now lives in the game entry's SDK registration, and the test asserts it there (name + component TYPE). The classic-globals guarantee is retained as a second test over the rollback bundle, so a rollback is still a working renderer.
   - Two harness fixes from the promotion are kept and worth keeping:
     - `test_office_plugin_wiring.py` could not evaluate the game bundle at all: Phaser 4 reads `navigator.userAgent` and probes a 2D canvas **at module init**, so in a DOM-less context the bundle threw before any plugin code ran. The probe supplies a minimal browser shim, and the entry path is passed via the environment (not argv). **This is a real property of the artifact, not a test quirk: the bundle cannot be evaluated without a DOM (SSR, prerender, plain `import`) and the failure presents as "the bundle is broken" rather than "there is no DOM".**
     - Twenty-one suites drove `node -e <program>`, which embeds the program in argv. With the 7 MB bundle inlined that exceeds `ARG_MAX` (`OSError: [Errno 7] Argument list too long`) and reads as a broken renderer. All now feed the program through **stdin** (`tests/_office_node.py`), which has no such limit.

1. **The world did not GROW to fill the shell** — FIXED. The route wrapper chain contains a `display: contents` div, so `height: 100%` resolved against an auto-height ancestor. `flex: 1 1 auto` + `min-height: 0` sizes it instead. Verified live: world 500→**879** desktop, 500→**668** mobile, body overflow 0, one canvas, no errors. The SPA is a BUILT artifact (`frontend/qubit-dist/`) — the route flag was in source but not in what was served until `scripts/build_dashboard.sh` ran.
2. **The eight settings groups are not all present.** The mechanism (spec-driven specs, scope, dirty state, reset/apply) is built and tested; the rest are data, and any `backend`-scoped ones need their real action names from the backend before they are declared.
3. **Dirty state is computed but not surfaced** — no preview/reset/cancel/apply controls yet.
4. **DPR is not separately observed** (container size is, via `ResizeObserver`).

**Environment trap hit twice, worth a note:** `NODE_ENV=production` in the shell makes `npm ci`/`npm install` omit every devDependency, so `vite`, `typescript` and `@vitejs/plugin-react` all silently vanish and 300+ packages go missing. Install with `NODE_ENV=development npm ci --include=dev`.

## F09 — Character roster, profile and dialogue shell

**Create:** `game/ui/{CharacterPanel,ConversationPanel}.ts`, `systems/CharacterPresenter.ts`; tests `character.test.ts`, `conversation.test.ts`.
1. Build matching portrait/world identity; roster filters and Go to target.
2. Render existing role/supervisor/memory/dossier/evidence with known/unknown states.
3. Bind canonical recipient/session stream; distinct pending/streaming/tool/owner-wait/error/stale states.
4. Finalize one message ID once; support optional typewriter/instant text, follow-up, artifacts and explicit interruption.
**Proof:** real target receives a message, reload recovers it, stream doesn't duplicate, close doesn't cancel. If binding absent, preview tests pass but LIVE integration remains blocked.

## F10 — Cohesive art kit and animation

**Create:** `game/assets/catalog.json` and approved source/export assets under `frontend/qubit-web/public/plugins/qubit-office/assets/game/`; implement referenced appearance adapter.
1. Approve one facade/lobby/floor/room/character portrait+sprite design board before scaling.
2. Build consistent tile/prop/signage/door/elevator/UI frame vocabulary.
3. Add semantic work states and separate cosmetic idle/visitor motion/ambience.
4. Add optional cleared/original SFX/music with user-gesture unlock and volume settings.
**Proof:** cast identity/scale/feet/occlusion agree, no franchise collage; work indicators disappear with absent/stale evidence. Paid generation needs separate approval.

## F11 — Multiple buildings, floors and room expansion

**Extend:** `navigation.ts`, world assembler/scenes/assets; tests `buildings.test.ts`, `floor-layout.test.ts`, `agent-route.test.ts`.
1. Consume owner-supplied building/portal graph; render two buildings with their distinct interiors.
2. Load arbitrary floor geometry without modulo room overlap; filter/cull by scene context.
3. Render authoritative multi-floor worker route segments; local visitor remains separate.
4. Expose directory/minimap/fast travel through same reducer.
**Proof:** enter A→floor 2→exit→enter B→floor 1; reload/Back/Go to character land correctly; unavailable/deleted portal handled. Synthetic performance fixture is explicitly labelled, never live integration proof.

## F12 — Operational panels and boardroom

**Create:** `game/ui/{BoardroomPanel,WorkPanel,DepartmentPanel,KnowledgePanel,ActivityPanel}.ts`; tests `requests.test.ts`, `finance.test.ts`.
1. Tasks/proposals/evidence, money folds and activity grouping use existing sources.
2. Present real CEO agenda/request with options, recommendation, artifact and affected work.
3. Bind canonical stage/settle with revision/idempotency/e-stop; read back exact target.
4. De-noise routine notifications; preserve blocking requests and access needs.
**Proof:** answer one real test/paper request through the existing authority; duplicate click does not duplicate settlement; stale money is marked, null is not zero.

## F13 — Editor and capability-aware furniture

**Create:** `game/ui/EditorPanel.ts`, `game/editor/{draft.ts,commands.ts}`, tests `editor.test.ts`.
1. Select/move/rotate/place/delete, ghost validation and local draft undo/redo.
2. Separate cosmetic and capability-bearing catalog entries; show before/after reach impact.
3. Stage/settle committed edits via owner-bound validators, handle revision conflict and readback.
4. Protect unsaved drafts; don't allow client grants, hiring or persistent layout writes.
**Proof:** invalid/blocked/no-confirmation edit leaves actual state unchanged; accepted edit arrives on authoritative revision; undo of committed edit is another audited action.

## F14 — Profile, accessibility and failure hardening

**Tests:** `accessibility.test.tsx`, `profile-scope.test.ts`, `reconnect.test.ts`, targeted browser scenarios.
1. Keyboard/reader roster and semantic icons; modal focus traps; remapping collision detection.
2. No profile/session/model preference leakage; no raw credentials in UI/artifact previews.
3. Network drop, asset decode fail, unknown state, absent recipient, changed floor, stale revision.
4. Repeated mount/unmount has no engine/listener/request/texture growth.
**Proof:** every failure preserves an honest usable shell and names its blocked capability.

## F15 — Profile performance before optimizing

**Proposed browser harness:** `scripts/office_game_journey.py` with labelled fixtures; summary evidence schema in `70-ACCEPTANCE-AND-HANDOFF.md`.
1. Measure frame pacing, memory/texture usage, bytes, requests/sec, cold load and transition times.
2. Characterize recorded/synthetic 10/50/100-character scenarios without spawning real agents.
3. Optimize measured hotspots: culling, atlases, labels, shared textures, lazy details and quality tiers.
**Proof:** declared target hardware/browser/scenario and raw measurements; no “lighter/faster” without baseline.

## F16 — Integration freeze and final matrix

1. Freeze implementation; reconcile docs/fleet/manifests/assets and named amendments.
2. With integration-lead approval, run final focused unit/type/build/browser matrix once.
3. Repair affected failures with narrow checks; repeat final matrix only after repairs freeze.
4. Run live backend-bound journeys separately from recorded preview/performance fixtures.
**Proof:** acceptance manifest reports each journey as passed/failed/blocked with evidence; no generic COMPLETE from a subset. Do not commit/push without explicit request and required independent review.

## Critical path and backend stops

F00→F01→F02/F03 sequentially→F04→F05→F06→F07→F08→F09→F10→F11→F12→F13→F14→F15→F16. No agent fan-out. F09 live conversation, F11 multi-building/worker travel, F12 request settlement and F13 committed editor integration are blocked only on their named owner contracts—not on cosmetic UI work.

Local fixtures/unit/browser checks need no AI-provider invocation. Developer model costs, art generation cost and implementation duration are not estimated here; adding plausible numbers would be fiction.
