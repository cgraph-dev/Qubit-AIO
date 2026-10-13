# 50 — Architecture — where each borrowed system lives

The merge touches four layers. Each borrowed system lands in exactly one, at a named seam. No
system is allowed to become "everywhere", because the repo's recurring bug class is one
concept with two authorities.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  L4  BROWSER — frontend/qubit-app/plugins/qubit-office/                  │
│                                                                          │
│   qubit-office.js   the renderer + React shell (reads the tower payload) │
│   iso-engine.js     projection, inverse, depth key, sortByDepth  [kept]  │
│   sprites.js        baked feet-anchored figures        [M6 -> real PNGs] │
│   camera.js         fit / clamp / minimap transform              [kept]  │
│   control-bar.js    the read-only buttons + named refusals       [kept]  │
│   motion.js         paths, facing, stations                      [kept]  │
│   assets/           vendored art: sprites, furniture, tiles, font, sfx   │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  polls GET /agent/api/office/tower
┌───────────────────────────────▼──────────────────────────────────────────┐
│  L3  READ ROUTE — voicebridge/qubit_compat.py::h_office_tower            │
│   read-only (C4) · 503 on failure, never 500 · ?motion / ?now / ?preview │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  calls
┌───────────────────────────────▼──────────────────────────────────────────┐
│  L2  THE FOLD — voicebridge/office/tower.py                              │
│   state(cfg, now, preview) — pure function of (office_event rows, clock) │
│   animation state, walk paths, stations, floorplan, HUD, activity        │
│   NO timers, NO background loop  (M14 = C7)                              │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  reads/writes
┌───────────────────────────────▼──────────────────────────────────────────┐
│  L1  STORE — voicebridge/office/*.py  +  office.db (append-only)         │
│   ledger, org, envelopes, reviews, audit, business, work, seating_plan   │
│   room_layout [M11 -> becomes a live grant source]                       │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 5.1 Where each source lands

| source | landing seam | layer | ruling |
|---|---|---|---|
| starnet sprites | `sprites.js` — replace the procedural bake with PNG atlases; keep the feet-anchor + shadow contract | L4 | M6 |
| starnet furniture | `drawTower()` furniture pass; new items in the `paintOrder()` array | L4 | M8 |
| starnet font | canvas `fillText` for room/seat labels | L4 | M10 |
| starnet law ("layout IS state") | `room_layout.py` — programme becomes the grant source; consumed by `tower.floorplan` | L1/L2 | M11 |
| agent-quest tiles | `drawTower()` deck/floor pass + room exteriors | L4 | M7 |
| agent-quest "building = the tool" | room `kind` derivation in `tower.py` | L2 | M7 |
| pixel-agents occlusion law | `paintOrder()` / `sortByDepth()` — **already there, keep it** | L4 | M12 |
| pixel-agents areas | room↔team binding (`room_layout` + `seating_plan`) | L1 | M11 |
| ai-town three-layer split | validation of the existing L1/L2/L3 split — no code change | — | M14 |
| ai-town "world pauses" | C7, already enforced in `tower.py` | L2 | M14 |
| agent-monitor boss chat | `escalation.py` gains an owner→agent direction on the same journal | L1 | M13 |
| agent-monitor autowork | existing `office_event` stream + `ACTIVE_WINDOW_S` | L2 | M13 |

---

## 5.2 The seams (what stays stable while the other side changes)

The merge's licence to change the renderer without touching the backend is these four seams.
Each is a contract that must not move:

1. **The tower payload.** `SCHEMA = "qubit-office-tower/1"`. The renderer reads
   `hud / floorplan / characters / activity / history`. **The merge adds no field the backend
   cannot produce** — art changes how a character is *drawn*, never what it *is*. New per-seat
   art fields (e.g. a `sprite_set`) are additive and default-safe.

2. **The depth key.** `depthKey(x, y, z)` → `sortByDepth()` → `paintOrder()`. A sprite sorts on
   the **feet point's screen-y**, exactly like the vector figure did (M12). Swapping the
   figure's pixels must not change its sort key, or occlusion regresses.

3. **The animation state.** `_ANIMATION` in `tower.py` maps event kind → `working / walking /
   carrying / blocked / idle / …`. The sprite layer must render **only** these states. A new
   pose may be added; the state machine may not.

4. **The event stream.** `office_event` rows. No visual may be driven by anything else (C7).

---

## 5.3 The weathering rule (why the merge cannot drift into decoration)

**No event, no sprite.** A figure is drawn in a pose only because `tower.py` said so from an
event row. If a sprite atlas has 15 poses and only 6 are reachable from `_ANIMATION`, the
other 9 are **not drawn** — they wait for a state that emits them. This is the same rule C7
enforces for motion, applied to art: art is a rendering of state, never a garnish.

Consequence for the vendoring script: it may vendor all 50 sets, but the renderer wires the
poses `_ANIMATION` can actually reach, and the test asserts no pose is drawn without a
matching state.

---

## 5.4 Module wiring (how a new module joins the bundle)

The plugin loader injects exactly **one** `<script>` per manifest entry. Multi-file support
does not exist, so `scripts/build_office_bundle.py` concatenates modules in dependency order
(`ORDER` list). A new module that exports a global:

1. add its file to `ORDER` with the global it must define (the script asserts the global
   appears, so a module that fails to export fails the build);
2. the renderer reads that global with a null fallback and **names the missing module in the
   page** rather than silently degrading.

Assets (images/fonts) are **not** concatenated — they are fetched at runtime from
`/dashboard-plugins/qubit-office/assets/…` via `<img>`/`Image()`/`new FontFace`.

---

## 5.5 Why this is not a second app (M1 restated)

Every borrowed system sits behind one of the four existing seams. Nothing adds a server, a
store, an event stream, or a confirmation path. The merge is additive at the edges of surfaces
that already exist — which is the whole reason it is a tab and not an app.
