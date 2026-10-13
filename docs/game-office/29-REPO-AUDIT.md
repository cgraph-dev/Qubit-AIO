# 29 — Reference repository audit

Six reference repositories are cloned under `/tmp/repo-ports/`. This document is
the per-repo answer to one question: **what in this repo is worth taking into
the Qubit Office, and what is not?**

It exists because the Office tower looked flat, the canvas was full of dead
space, and characters overlapped each other. Every verdict below is against our
**current** renderer at
`frontend/qubit-app/plugins/qubit-office/qubit-office.js` (956 lines at the time
of this audit), not against an imagined starting point. Where a technique is
already in our file, the audit says so and names the function that already
carries it — otherwise "port X" would be inventing work we have already done.

Method: licences were read from the files themselves, not from a summary; every
source file cited was opened and counted with `wc -l`; every construct quoted
carries the line number it actually sits on.

---

## 1. Licence verification (read from the files, not a table)

| repo | path checked | first line of file | SPDX-style verdict |
|---|---|---|---|
| `ai-town` | `/tmp/repo-ports/ai-town/LICENSE` (1067 B) | `MIT License` / `Copyright (c) 2023 a16z-infra` | MIT — portable |
| `starnet` | `/tmp/repo-ports/starnet/LICENSE` (1068 B) | `MIT License` / `Copyright (c) 2026 Andrew Sims` | MIT — portable |
| `pixel-agents` | `/tmp/repo-ports/pixel-agents/LICENSE` (1071 B) | `MIT License` / `Copyright (c) 2026 Pablo De Lucca` | MIT — portable |
| `agent-quest` | `/tmp/repo-ports/agent-quest/LICENSE` (1074 B) | `MIT License` / `Copyright (c) 2026 Fulvio Scichilone` | MIT — portable |
| `agent-monitor` | `/tmp/repo-ports/agent-monitor/LICENSE` (1065 B) | `MIT License` / `Copyright (c) 2026 Ruiqi Li` | MIT — portable (`package.json` also declares `"license": "MIT"`) |
| `aphae` | `/tmp/repo-ports/aphae/LICENSE*` | **`No such file or directory`** | **No licence → all rights reserved → READ-ONLY, no code use** |

Five are MIT and portable. `aphae` ships no licence file of any kind, so its
code may not be copied, adapted, transliterated or pasted — see §6.

---

## 2. What we ALREADY have (so this audit can name what is genuinely NEW)

Read from our own file before judging the references. These are done; a port
that reproduces one of them is not a port, it is a rewrite.

- **2:1 isometric projection** — `qubit-office.js:254` `iso(x, y, z, cam)`:
  `sx = (x - y) * TILE_W/2`, `sy = (x + y) * TILE_H/2 - z * DECK_Z`, with
  `TILE_W=34, TILE_H=17` (`:90`). The `z` term stacks floors.
- **Camera with zoom + clamped pan** — `iso()` applies `cam.cx/cy/zoom`; the
  clamp is `clampPan(cam, cols, rows)` (`:264`), margin `PAN_MARGIN=0.30`
  (`:98`); wheel zoom and pointer pan live in the `Office` component.
- **Auto-fit framing** — `extent(floors)` (`:276`) computes the built
  bounding box in iso space and `fitToBuilding(cam, floors)` (`:298`) fits it,
  with a legibility zoom floor `CHAR_FLOOR_ZOOM=0.85` (`:97`) so characters
  never shrink to specks.
- **Sprites with feet anchoring, ground shadow, walk bob** —
  `drawShadow()` (`:324`, squashed ellipse), `drawAgent()` (`:336`, body above
  the feet point per `FEET_ANCHOR`, facing offset baked into the head), and the
  `progress`-driven bob (`:496-499`).
- **A minimap** — `drawMinimap(cv, v, floor)` (`:519`): world extent scaled
  into a small canvas, rooms as rects, agents as markers.
- **A P&L chart** — `drawPnl(cv, series)` (`:560`), bars + net line from
  `v.history`. No reference repo has one; this is ours.
- **Marker over the head** — `drawMarker()` (`:370`) for stalled / walking.

**CORRECTED — this paragraph predates the integration and was wrong when
read later.** It said there was no sort anywhere in `qubit-office.js`. There
now is: `paintOrder()` in `qubit-office.js` and `sortByDepth()` in
`iso-engine.js` impose a total order on screen-y (with `id` as the same-depth
tiebreak), replacing the fixed deck-then-characters pass described below. The
paragraph is kept verbatim behind this note because the *audit finding* it
records — that the reference repos solve this and we did not yet — is what
drove the change; only the "we do not have it" clause is stale.

**What we did NOT have at audit time — confirmed by `grep` over our file then:**
there was no ordering key in `qubit-office.js`.
Every character was drawn in a single loop (`:480-509`) *after* every deck
(`:405-477`), in payload order. That is the mechanical cause of the overlap the
owner called out: two characters on the same floor paint in whatever order the
API listed them, and a near figure never paints over a far one.

---

## 3. Per-repo findings

### 3.1 `pixel-agents` — the z-sort source. **ADAPT (highest value).**

Renderer/engine surface (`wc -l`): `webview-ui/src/office/engine/renderer.ts`
1048, `engine/officeState.ts` 1193, `components/OfficeCanvas.tsx` 890,
`office/projection.ts` 77, `office/colorize.ts` 253, `engine/characters.ts` 342,
`engine/seatPlacement.ts` 56, `office/types.ts` 305.

**The single most valuable file: `renderer.ts`.** It is the only reference that
solves *occlusion* by an explicit sort, and our renderer has HAS a sort (corrected: this line predates integration -- `qubit-office.js` now defines `paintOrder()` and `iso-engine.js` defines `sortByDepth()`, a total order on screen-y with `id` as the same-depth tiebreak).

- `webview-ui/src/office/engine/renderer.ts:487` —
  `drawables.sort((a, b) => a.zY - b.zY);` — **one pass, every drawable**
  (walls, furniture, characters, pets) goes into an array, gets a `zY`, then
  paints back-to-front. Lines `:945-947` fold walls into the same array
  (comment `:945`, then `allFurniture = [...wallInstances, ...furniture]` at
  `:947`) so a character can sort *behind* a wall.
- The sort key is deliberately the **bottom** of the tile, not the center:
  `renderer.ts:404` `const charZY = ch.y + TILE_SIZE / 2 + CHARACTER_Z_SORT_OFFSET;`
  with `CHARACTER_Z_SORT_OFFSET = 0.5` (`webview-ui/src/constants.ts:40`) and
  `TILE_SIZE = 16` (`:4`). The comment at `:401-403` states the reason: sorting
  by the tile bottom puts a figure *in front of* same-row furniture but
  *behind* furniture one row down.
- Two tie-breakers are encoded as sub-pixel offsets rather than branches: the
  selection outline sorts **before** its character with
  `OUTLINE_Z_SORT_OFFSET = 0.001` (`constants.ts:41`, used `renderer.ts:438`),
  and pets use the *same* boundary formula as characters (`renderer.ts:475-476`,
  `petZY = pet.y + TILE_SIZE / 2`) so a pet and its owner never flicker.

**Already ours:** `colorize.ts` (HSL recolour preserving per-pixel alpha) is
already ported to `shade()`/`tint()`; `OfficeCanvas.tsx` camera is already
`iso()`/`clampPan()`. `projection.ts` is a clean single-source tile→screen with
DPR handling — useful only if we ever add DOM overlays over the canvas; today we
draw everything in-canvas, so it is a **no-op** for us.

**Genuinely NEW:** the **single-array z-sort with a tile-bottom key and a
characters-on-top-of-walls fold**. This is the direct fix for the overlap
defect. Note the caveat: pixel-agents is **top-down**, not isometric
(`zY = worldY + TILE/2`). The *technique* transfers; the *formula* must be
re-expressed as `(x + y)` for our 2:1 grid, which is exactly what
`agent-monitor/isometric.ts` already does for the depth key — so the two sources
combine cleanly (§4, port #1).

*Verdict:* **TARE** — take the sort structure and the offsets; leave the DOM
projection and the top-down geometry behind.

### 3.2 `agent-monitor` — iso-native depth key + A*. **TARE.**

Renderer/engine surface: `src/engine/isometric.ts` 163, `src/engine/canvas.ts`
495, `src/engine/pathfinding.ts` 113, `src/engine/animation.ts` 221,
`src/sprites/characters.ts` 468, `src/sprites/effects.ts` 271,
`src/office/layout.ts` 207.

This is the **only** reference that is natively isometric *and* small enough to
read end to end.

**Single most valuable file: `src/engine/isometric.ts`.** It is the canonical
statement of the same 2:1 projection we already use, plus the two derived
functions we don't have:

- `isometric.ts:28` inside `gridToScreen()`:
  `x: MAP_OFFSET_X + (pos.col - pos.row) * (TILE_W / 2)` with `TILE_W = 48,
  TILE_H = 24` (`:7-8`) — the exact 2:1 ratio our `TILE_W=34/TILE_H=17` obeys.
- `isometric.ts:39` `screenToGrid()` — the **inverse** projection
  (`col = ((sx/(W/2) + sy/(H/2))/2)`). We have no inverse; it is what
  hit-testing a click back to a room needs.
- `isometric.ts:105` `drawIsometricBlock()` — a true **3-face cuboid** (top +
  left + right), lines `:117-144`, giving a wall/desk real volume. Ours draws
  only 2 cutaway faces (`qubit-office.js:426-440`).

**The depth key** lives in `canvas.ts`:
- `canvas.ts:214` `depth: runtime.pos.row + runtime.pos.col` — the isometric
  painter key. Furniture uses the identical key at `:183`
  (`sort((a,b) => (a.row + a.col) - (b.row + b.col))`), and the merged array is
  sorted at `:254` and again in the worker path at `:481`.
- `canvas.ts:227` `drawNameTag(ctx, sp.x, sp.y + 2, ...)` — the label is pinned
  to the **feet**, so a dense floor stays readable. Our `drawMarker()` floats at
  `y - 20 * zoom` (`qubit-office.js:373`) and would collide in a crowd.

**A* pathfinding** (`pathfinding.ts:33` `findPath`, manhattan heuristic `:18`,
4-neighbour `DIRECTIONS` `:26`) exists because their agents move on the grid.
**We do not need it:** our characters' positions are computed *server-side* in
Python (`voicebridge/office/tower.py`) and arrive as `position.x/y` + `progress`
— the renderer resolves no path by design (C7). Porting A* would be porting work
the backend already does.

**Already ours:** the 2:1 projection and the wall-face proportion. **Genuinely
NEW:** the `row+col` depth key for a per-floor sort, `screenToGrid()` inverse
hit-test, and the 3-face cuboid in `drawIsometricBlock`.

*Verdict:* **TARE** — the projection file and the depth key are the highest
signal-per-line in the whole set.

### 3.3 `agent-quest` — Y-sort tie-breakers + camera fit. **ADAPT.**

Renderer surface: `client/src/game/scenes/VillageScene.ts` 744,
`client/src/game/entities/HeroSprite.ts` 667,
`client/src/game/terrain/TerrainRenderer.ts` 827,
`client/src/components/Minimap.tsx` 76.

**Single most valuable file: `HeroSprite.ts:389` `updateDepth()`.** It is the
clearest written argument for *why* you sort by the feet, and it encodes the
label depth ladder:

- `HeroSprite.ts:394` `const footY = this._y + this.sprite.displayHeight * 0.5;`
  then `:395` `this.sprite.setDepth(footY + 0.5);` and the label chain at
  `:396-403` (`nameText` at `footY + 0.6`, halo at `footY + 0.4`). The comment
  `:390-393` gives the reason in one sentence: a hero whose feet align with a
  building's foot "would render behind it because his center-y is well above
  the building's foot-y". **This is precisely our defect** — our characters have
  no depth at all, so payload order decides who wins.

**Camera framing:** `VillageScene.ts:479` `fitCamera()` —
`zoom = clamp(min(w/villageW, h/villageH) * 0.85, minZoom, maxZoom)`, where
`minZoom()` (`:500`) is `max(cam.width/WORLD_WIDTH, cam.height/WORLD_HEIGHT)` so
"the world always fully covers the viewport" (comment `:496-498`). Our
`fitToBuilding()` already does the same job with a legibility floor; the *new*
part is the **0.85 inset factor** and the explicit min-zoom that forbids showing
canvas beyond the world edge — a direct answer to our dead space.

**Already ours:** `Minimap.tsx` is already ported to `drawMinimap()` (rooms as
rects, agents as markers). **Genuinely NEW:** the **sub-pixel label depth
ladder** (`sprite` and its text objects at `footY + 0.4/0.5/0.6`) so text never
sinks behind the body it names, and the **procedural tile generator** in
`TerrainRenderer.ts:146` `mkTile()` — `createImageData` filled by a seeded RNG
over a weighted palette (`:154-158`) — which could give our deck plates surface
texture with zero assets. The procedural tile is a nice-to-have; the label
ladder is the correctness fix.

*Verdict:* **ADAPT** — the depth ladder is worth more than the rest of the repo
combined; the procedural tile is optional polish.

### 3.4 `ai-town` — server-side path interpolation. **ADAPT (narrow).**

Renderer surface: `convex/util/geometry.ts` 132, `src/components/Character.tsx`
120, `convex/aiTown/movement.ts` 189, `convex/aiTown/worldMap.ts` 74. Note the
biggest files in the repo (`src/editor/le.js` 1371, `se.js` 1216) are the
third-party **LevelEditor** asset tool, not the game renderer — they are not
relevant.

**Single most valuable file: `convex/util/geometry.ts`, function
`pathPosition()` at `:26`.** It answers "where is this character *now*" from a
timed path:

- `geometry.ts:45` `const interp = (time - segmentStart.t) / (segmentEnd.t - segmentStart.t);`
  then `:47-50` linearly interpolates `position` between the two segment ends
  and carries the segment's `facing` (`:51`) and `velocity` (`:52-53`).

This is the *exact shape* of our payload: our backend already computes the same
point in Python and ships `position.x/y` + `progress`. So porting `pathPosition`
into the renderer would be **re-deriving server-side truth on the client**,
which our C7 rule forbids. The **transferable idea** is narrower: our
`facing` is already a payload field, but our bob uses
`Math.abs(Math.sin(pr * Math.PI * 4))` (`qubit-office.js:499`) — a fixed 4-cycle
regardless of distance, so a short step and a long walk bob identically.
ai-town's `velocity` term (`:52`) is the honest fix: **derive the bob phase from
the segment's actual distance**, not a constant.

Also useful, small: `Character.tsx:56-57`
`const roundedOrientation = Math.floor(orientation / 90); const direction = ['right','down','left','up'][roundedOrientation];`
— quantization of a continuous angle to 4 sprite directions. We already carry a
discrete `facing`, so this is **already covered** conceptually.

*Verdict:* **ADAPT** — one idea only (distance-phased bob), not the file. The
rest is server-side duplication we must refuse.

### 3.5 `starnet` — the one we already mined; mostly **IGNORE**.

Surface is enormous: `frontend/app/world.js` **11416** lines,
`frontend/app/stationui.js` 11261, `frontend/app/propsprites.js` **11993**,
`frontend/agent-demo/sprites.js` 749, plus `frontend/agent-demo/stationbake.js`
5367 and `frontend/app/worldsurface.js` 738. This is a shipped, side-view
*station* simulation (belts, stations, a full HUD framework, voice, workflow
panels) — **it is not isometric and it is not a small renderer.**

**Single most valuable file: `frontend/agent-demo/sprites.js`, function
`groundShadow()` at `:298`** (already partly ours).

- `sprites.js:293` `const SHADOW_RINGS = Array.from({ length: 12 }, ...)` — a
  **12-ring stack** (`:308-317`) that converges on the feet and darkens toward
  the center rather than drawing concentric bullseye bands, plus a separate
  tight contact ellipse at `:320-322`. The comment `:285-292` explains the
  design: a broad penumbra under a north-west key light, and a compact contact
  core that gives the body weight. Our `drawShadow()` is a *single* flat ellipse
  (`qubit-office.js:324-334`) — a faithful but crude version of this.
- `sprites.js:66-72` — the derived **foot-pad** measurement: because PixelLab
  masters carry transparent padding below the feet, they *measure* it and
  anchor the feet to the floor so the padding never floats the figure. We sidestep
  this by drawing vectors, not sprites, so it does not apply.
- `sprites.js:8-13` in `frontend/app/world.js` and the depth-slot note at
  `world.js:6453-6457` ("The response stays in this item's existing depth slot")
  confirm starnet orders by an explicit painter list — the *same idea* as
  pixel-agents, at 10× the code. Nothing here is worth extracting over
  agent-monitor's 163-line `isometric.ts`.

**Already ours:** `sprites.js` (feet anchor, ground shadow, walk bob) →
`drawAgent()`/`drawShadow()`; `stationbake.js` `shade()` and wall-face
proportion → `shade()`/`drawTower()`. Both are named in our provenance header.

**Genuinely NEW:** essentially nothing we should take *whole*. The only
candidate is refining `drawShadow()` from one ellipse to a 2-3 ring penumbra +
contact core — a real visual upgrade for ~15 lines.

*Verdict:* **IGNORE** (with one optional micro-port: the ringed shadow). The
repo is a good neighbour for *ideas about shipped quality bar* and nothing else;
its 30k+ renderer lines are a different genre.

### 3.6 `aphae` — admired design, **NO CODE MAY BE USED.**

Godot 4 / GDScript, 25054 lines of `.gd`. Largest: `scenes/agents/agent.gd` 974,
`scenes/conversations/conversation_instance.gd` 817, `scenes/ui/hud.gd` 742,
`scripts/utils/sprite_factory.gd` 657.

**Licence: NO FILE. `ls /tmp/repo-ports/aphae/LICENSE*` returns "No such file or
directory".** Absent a licence, default copyright applies: **all rights
reserved**. Not one line of its GDScript may be copied, adapted, transliterated
into JavaScript, or pasted — in any language, under any circumstance. This audit
therefore records what it *does well as a design*, read only, and does not
describe how it does it at code level.

Admired, at the design level only:

- **A social/verbal layer on top of the sim** — speech bubbles
  (`scenes/ui/speech_bubble_layer.gd`), a narrative log, a "confessional", a
  whodunit director. Our Office has an event feed and markers; aphae's
  *product* idea — that agent chatter is first-class and visible on the floor —
  is the ambition worth holding, and holding is all it is.
- **A camera with explicit fit + follow + bounds** (`scenes/ui/game_camera.gd`).
  We independently built the same primitives (`fitToBuilding`, pan clamp, the
  `Office` pointer handlers); **our implementation predates and does not derive
  from theirs**.
- **Headless simulation** (`scenes/main/headless_sim.gd`) — running the world
  without a window. Our backend already computes truth headlessly in Python, so
  the idea is already our architecture.

Note also that aphae's world is **top-down, not isometric**: it enables Godot's
built-in `y_sort_enabled = true` (see `scenes/world/office.tscn:27,34,85`) and
otherwise has no projection code of its own. So even setting the licence aside,
it offers no isometric technique to take.

**Confirmation for the record:** no `aphae` code was or will be copied, adapted
or transliterated; the renderer's provenance header
(`qubit-office.js:37-41`) and `docs/game-office/25-PORTED-CODE.md` state the
same.

*Verdict:* **IGNORE for code — and the "cannot use" section below is binding.**

---

## 4. Verdict table

| repo | licence | verdict | one-line reason |
|---|---|---|---|
| `pixel-agents` | MIT | **TARE** | The only reference with a real single-array z-sort (`renderer.ts:487`) and sub-pixel tie-breakers — the direct fix for our overlap defect. |
| `agent-monitor` | MIT | **TARE** | 163-line iso file that natively states our projection plus the `row+col` depth key (`canvas.ts:214`) and an inverse projection we lack. |
| `agent-quest` | MIT | **ADAPT** | `HeroSprite.ts:389` is the clearest feet-based depth argument and the label depth ladder; the rest overlaps work we have. |
| `ai-town` | MIT | **ADAPT** | One idea worth having — distance-phased bob from `pathPosition` (`geometry.ts:45`) — not the file; the rest is server-side duplication we must refuse. |
| `starnet` | MIT | **IGNORE** | A 30k-line side-view station sim; its two useful techniques (feet anchor, shade) are already ours, leaving only an optional ringed shadow. |
| `aphae` | **none** | **IGNORE (blocked)** | No licence → all rights reserved → no code may be used; also top-down, so it offers no iso technique even ignoring the licence. |

---

## 5. The top 5 ports still to do, ranked by (visual impact ÷ effort)

Ranked against *our* current file, after subtracting what §2 shows we already
have. Each names the source line and the function in `qubit-office.js` it would
land in.

1. **Per-floor back-to-front character sort by an iso depth key.**
   Combine `agent-monitor/src/engine/canvas.ts:214` (`depth = row + col`) with
   `pixel-agents/.../renderer.ts:487` (single-array sort, one pass). Rewrite the
   character loop at `qubit-office.js:480-509` to push every character *and
   every desk* into one `drawables` array and sort by `x + y` before painting.
   **Impact: fixes the exact defect the owner named — characters stop painting in
   payload order and a near figure finally occludes a far one. Effort: ~30 lines.**
   Highest ratio in the set.

2. **Adopt pixel-agents' tile-bottom key with the outline/name tie-breaker
   offsets.** `renderer.ts:404` + `constants.ts:40-41`. In our loop the key is
   `(x + y) + 0.5` for the body and `+ 0.6` for the label, so a name never sinks
   behind the body it names (same ladder as `agent-quest/HeroSprite.ts:396-403`).
   **Impact: a crowded floor stays legible. Effort: ~10 lines.** Pairs with #1
   and should ship with it.

3. **Inverse projection for hit-testing.** Port `agent-monitor/isometric.ts:39`
   `screenToGrid()` so a pointer click maps back to a room. **Impact: unlocks
   click-to-focus without a DOM overlay; also the honest basis for a
   "pan to this agent" affordance. Effort: ~12 lines**, but gated on the click
   handler actually wanting it — do not port it speculatively.

4. **Distance-phased walk bob.** Replace the fixed
   `Math.abs(Math.sin(pr * Math.PI * 4))` at `qubit-office.js:499` with a phase
   proportional to the travelled distance, the idea from `ai-town/geometry.ts:52`
   (`velocity = distance / duration`). **Impact: a two-tile step stops bobbing
   like a ten-tile march — motion reads as honest. Effort: ~5 lines**, needs one
   extra payload-independent derivation (the payload has no distance field; the
   phase can key off `progress` slope across polls, or stay as-is if that proves
   noisy — flag, don't fake).

5. **Real volume on desks and walls: the 3-face cuboid.** Port the top/left/right
   block from `agent-monitor/isometric.ts:105-145` (`drawIsometricBlock`),
   replacing the 2 cutaway faces at `qubit-office.js:426-440`. **Impact: desks
   and partitions read as solid objects rather than coloured quads — the
   "flat plates" complaint. Effort: ~25 lines and a palette decision for the
   three faces** (reuse `shade(base, ±)`); ranked below #1-2 because the
   building reads acceptably once depth order is correct.

**Optional micro-port (below the top 5):** upgrade `drawShadow()`
(`qubit-office.js:324`) from one ellipse to a 2-3 ring penumbra plus a contact
core, per `starnet/agent-demo/sprites.js:293-326`. Small visual win, ~15 lines.

---

## 6. Cannot use — `/tmp/repo-ports/aphae`

- **Licence check:** `/tmp/repo-ports/aphae/LICENSE*` → *No such file or
  directory*. No `LICENSE`, no `LICENSE.md`, no SPDX header in `project.godot`.
  Absent a licence, copyright is reserved by default.
- **What we admire (design, not code):** the social layer — speech bubbles
  (`scenes/ui/speech_bubble_layer.gd`), the confessional/narrator framing, the
  narrative log — treats agent conversation as a visible, first-class part of
  the floor rather than a side panel. That product idea is the one we hold.
- **What it does *not* offer:** it is top-down, using Godot's built-in
  `y_sort_enabled` flag (`scenes/world/office.tscn:27,34,85`), so it carries no
  isometric technique to take even if the licence were permissive.
- **Binding statement:** no `aphae` code has been copied, adapted,
  transliterated into JavaScript, or pasted into this repository, and none may
  be. Its only influence is at the level of panel-layout ideas, which are not
  code. This is consistent with `qubit-office.js:37-41` and
  `docs/game-office/25-PORTED-CODE.md`.

---

## 7. Repos worth far less than expected

- **`starnet`** is the surprise of the audit. Fetched as the apparent crown
  jewel (`world.js` alone is 11416 lines), it turned out to be a **side-view
  station simulation**, not an isometric office, and the two techniques we could
  use from it were **already ported** (`shade()`, the feet-anchored sprite and
  ground shadow). Beyond those it offers no portable renderer technique — only
  a very high shipping-quality bar and one optional shadow refinement. Budget
  zero further reading time on it unless we want that bar as a reference.
- **`ai-town`** is mostly a data/agent-conversation backend. The two largest
  source files (`src/editor/le.js` 1371, `se.js` 1216) are a vendored
  **LevelEditor asset tool**, not the renderer; the genuinely relevant renderer
  code is ~250 lines (`geometry.ts`, `Character.tsx`, `movement.ts`) and only
  one idea transfers.
- **`aphae`** is 25k lines with a rich feature surface, but a **licence wall
  plus a top-down world** means its *net* port value is zero code — know it, do
  not mine it.
