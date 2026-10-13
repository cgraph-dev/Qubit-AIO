# 70 — Visuals and Assets

**Revised by D11.** The tower is **2D isometric Phaser**. Agents are **procedurally drawn** today.
Tripo GLB characters are a **phase-2 upgrade** — so the first half of this file is an *upgrade
spec*, not a phase-1 dependency.

**Nothing in this file blocks M4.** Phase-1 agents are drawn in code; the Tripo assets are an
upgrade that lands whenever the owner gets around to exporting them (M6).

**Owner: the exact download settings are in § *Exact Tripo settings* below.** Read that section
before generating anything — one wrong choice cannot be fixed by re-exporting.

---

## Rendering: the Phaser iso model (phase 1, current)

Reusable under MIT from `BitmapAsset/pepeclaw` (`src/phaser/`). Constants and structure worth
keeping as-is unless we have a reason:

| Constant | Value | Note |
|---|---|---|
| `TILE_W` × `TILE_H` | 300 × 150 | 2:1 isometric ratio |
| `WALL_H` | 84 | room wall extrusion |
| Room spacing | 172 × 98 | grid step between room centers |
| Scene | 1500 × 900 | camera zoom/pan sits on top of this |

Rooms are isometric **diamond polygons** with extruded walls; walkways are L-shaped glowing paths
between adjacent room centers; agents walk to a per-room **agent slot offset**
(`agentSlots[roomId][i]`) so a room's occupants sit in a stable arrangement.

Camera: drag to pan, wheel to zoom (clamp ~0.5–2.5), click to select, double-click to follow,
smooth-eased toward the target rather than snapped. Minimap for office-wide navigation.

**What this buys us:** the entire navigation problem is already solved, in code we can reuse, at
zero art cost. That is the whole argument for D11.

### Floor stack — what changes from pepeclaw

pepeclaw is **one flat office of 8 rooms in a fixed 3×3 grid**. We have **stacked floors, one per
department** (D13). So we keep their room/camera/walkway model and add:

| Change | Spec |
|---|---|
| Room grid | per-floor, up to 12 rooms, defined by floorplan JSON (pepeclaw hardcodes 8) |
| Floor stack | current floor at full opacity; floor below at 35% and offset down; floors above as a left-rail stripe |
| Lift shaft | the persistent inter-floor navigation — replaces pepeclaw's "overview mode" as the primary way to move |
| Floor directory | left rail: floor number, department name, headcount, P&L state (green/amber/red) |
| Floor transition | slab animates out/in on switch |

It should read like an office building seen from the side, not a level select.

## Agent rendering

### Phase 1 — procedural (M4)

A Phaser Container built from Graphics primitives: shadow, glow, two legs, torso, two arms, head,
status dot, name label. Animated by transform only — leg swing, arm swing, vertical bob — plus
overlays:

| Overlay | Shown when |
|---|---|
| Desk + monitor | state is `working` |
| Typing dots | a turn is in flight |
| Speech bubble | awaiting input / an escalation is open |
| Selection ring | selected |
| Follow ring | camera is following this agent |

This is what pepeclaw already does and it is enough to ship a legible tower.

### Phase 2 — Tripo GLB characters (upgrade)

Swapped in at the **same tile coordinates**. An agent is a position plus a state; how it is
painted is a renderer detail, so this is additive and can land after the tower is already useful.

```
concept (image or text)
  └─► Tripo generate (image→3D, standard texture)     ~30 credits
      └─► HD texture                                   +18 credits
          └─► Auto Rig                                  25 credits
              └─► retarget idle / walk / work / talk    10 credits each
                  └─► export GLB (basic convert)         5 credits
                      └─► assets/characters/<slug>.glb
```

**~78–118 credits ≈ $0.78–$1.18 per animated character** (Tripo API: 100 credits = $1.00).

Tripo's **Studio subscription and its developer API are separately billed product lines** —
Studio credits ≠ API credits. Confirm which one before budgeting (owner question, `80-RISKS.md`).

### Owner-supplied asset intake — what to ask for

| Requirement | Value | Why |
|---|---|---|
| Format | **GLB** (binary glTF) | Only format carrying mesh + PBR texture + skeleton + clips in one file; loads via GLTFLoader with no conversion |
| Texture resolution | **1K** | Characters render ~64–96px tall; 4K is 16× the bytes for no visible gain |
| Rigged | **Yes** (Auto Rig before export) | A static mesh has no walk cycle |
| Clips | `idle`, `walk`, `work`, `talk` — named exactly | The renderer matches on clip name |
| Scale | **consistent across the whole cast** | A 4 m character next to a chibi breaks the iso floor |
| Silhouette | humanoid-ish | Same reason |

Not needed: USD, OBJ, STL, 3MF. (OBJ carries no skeleton; STL/3MF are 3D printing formats.)
FBX is a **backup only** — better rig fidelity if we ever need to re-export or re-rig in Blender;
never used at runtime.

Drop path: `frontend/qubit-app/plugins/qubit-office/assets/characters/<slug>.glb`

### Exact Tripo settings to use when generating the cast

Verified against Tripo's own docs and pricing material (2026-10-12). **These are the settings; do not
improvise, because a wrong one costs credits and cannot be fixed by re-export.**

| Setting | Choose | Why |
|---|---|---|
| **Export format** | **GLB** | The only single file carrying mesh + PBR texture + skeleton + clips, and the only one that loads directly in the browser (three.js `GLTFLoader`). FBX is a *backup* — better rig fidelity, but needs a Blender conversion step before it can render |
| **Texture** | **1K** | Agents render 64–96 px tall. 4K is 16× the bytes for zero visible gain |
| **Rig type** | **biped** (humanoid) | Tripo's other rig types are quadruped/hexapod/octopod/avian/serpentine/aquatic. The cast is humanoid; a quadruped rig cannot be retargeted to a walk cycle for a person |
| **Bone naming spec** | **Mixamo**, not Tripo's own | Tripo Studio offers `spec: tripo (default)` or `mixamo`. Mixamo names are the *lingua franca* of humanoid animation — choosing them now means the whole cast can be re-rigged, retargeted, or extended later (Mixamo/Blender/Unity/Unreal) without renaming bones. This is a one-click decision that is expensive to reverse |
| **Auto Rig** | **yes**, before export | A static mesh has no walk cycle, and rigging after export costs more |
| **Pose** | **T-pose** | Tripo exports rigged characters in T-pose; retargeting needs a neutral reference pose |
| **Silhouette** | humanoid, consistent proportions | A 4 m character next to a chibi breaks the isometric floor |
| **Rig Check first** | **yes** — Tripo's `rig-check` is free | It verifies the mesh is riggable and recommends the rig type *before* you spend the 25 auto-rig credits. Run it on every character |

**Not needed, do not download:** USD, USDZ, OBJ (no skeleton), STL, 3MF (3D-printing formats).
USDZ is Apple AR Quick Look — irrelevant to a browser iso renderer.

**Clip names.** Tripo's preset animations cover `idle` / `walk` / `run` and others. We match on clip
*name* at runtime, so after download the four clips must be named exactly:

```
idle    walk    work    talk
```

Where Tripo's preset names differ (`run`, `Walking`, `Idle_A`), rename them in the manifest step —
the manifest's `clips` array is what the renderer reads, so the rename is recorded, not hidden. If a
`work` clip does not exist as a preset, use a second `idle` variant and say so in the manifest; a
missing clip must never be silently substituted.

**Per character, the credit path** (Tripo API pricing: 100 credits = $1.00):

```
generate (image→3D, standard texture)   ~30
  HD texture                            +18
  Auto Rig                              +25
  retarget idle / walk / work / talk    10 each
  export GLB (basic convert)             +5
                                       ─────
                                  ~78–118  ≈ $0.78–$1.18 per animated character
```

A cast of 12 is therefore **~$9.36–$14.16**, matching the "one-time art capex ~$15" line in
`README.md`.

## Asset manifest (build input, not runtime state)

`assets/manifest.json`, one entry per asset:

```json
{
  "slug": "scout-01",
  "kind": "character",
  "renderer": "glb",
  "file": "characters/scout-01.glb",
  "source_plate": "concepts/scout-01.png",
  "generator": {
    "vendor": "tripo",
    "product_line": "studio",
    "account_id": "<recorded>",
    "plan_tier": "pro",
    "commercial_rights": true,
    "generated_at": "2026-10-12T09:14:00Z",
    "credits_spent": 78
  },
  "rig": "humanoid-v1",
  "clips": ["idle", "walk", "work", "talk"],
  "scale": 1.0
}
```

Phase-1 procedural characters need no manifest entry — they are code, not assets.

## The license gate (D10 / C9)

Tripo's own published terms:

- **Free-tier output is non-commercial.** Commercial rights are granted on paid tiers
  (Pro/Enterprise) — stated on Tripo's pricing page and repeated in its help centre.
- The generated **model** belongs to the paying user, but Tripo **explicitly disclaims
  responsibility for the copyright status of the input** — so the source image must be one the
  owner holds.
- Output may not be used to build something that directly competes with Tripo.

So the build script:

1. **Refuses to build** a revenue-bearing surface if any used asset has
   `commercial_rights != true` or a non-commercial `plan_tier`.
2. **Refuses to build** an asset whose `source_plate` has no recorded provenance.
3. **Records account, tier, and date**, so if the terms change the owner can prove which terms
   applied when the asset was made.

This gate runs at **asset build**, never at tower build — so it cannot block M4.

## State → visual map

Driven **only** by `office_event` (D14). No event, no motion.

| Agent state | Phase 1 (procedural) | Phase 2 (GLB) |
|---|---|---|
| `idle` | idle bob | `idle` clip |
| `working`, turn in flight | typing dots + desk glow | `work` clip |
| `working`, model thinking | slow pulse above head | `idle` clip + pulse |
| `blocked` | amber clock icon, bobbing | `idle` + clock icon |
| escalation pending | bubble + walk to manager room | `walk` → `talk` |
| spend event | coin/energy drain toward the vault | same, over `work` |
| revenue event | deposit at vault + floor plate flash | same, over `idle` |
| `suspended` | desaturated, static | static, no clip |
| `terminated` | seat clears in one anim cycle | same |

**Deliberate omission:** there is no "happy" or "stressed" state. Mood is not data; inventing it
would violate D14 and would make the tower prettier and less true.

## Asset sources, in order of preference

1. **Procedural** (phase 1) — drawn by code, zero cost, zero license risk.
2. **Tripo** (phase 2) — agents and hero props where a real character look matters.
3. **CC0 tileset packs** (the Tiny Swords class of pack, as Agent-Quest bundles CC0 by Pixel Frog)
   — floors, walls, furniture. Free, commercial-safe, hand-authorable.
4. **Deterministic placeholder tiles** — so the build never blocks on art.

Rule: no asset enters without a `manifest.json` entry naming its license and source. An unlisted
file on disk is treated as missing.

## Not carried over from pepeclaw

| Their feature | Decision |
|---|---|
| Any layout/placement editor | **Adopted** — D12 was flipped: the editor is in scope as **M7** (`75-EDITOR.md`). *This row said "excluded" until 2026-10-12; it was left behind when D12 changed.* |
| Built-in demo data fallback | **Partly adopted.** We do not render invented office data, but an empty tower renders an **honest empty state** ("nothing built yet", "no material dropped") — the same instinct as their labelled demo mode, without the lie |
| OpenClaw gateway bridge | **Excluded** — we read our own store, not a foreign gateway |
| Their 8 themed rooms (Genome Lab, Breeding Arena, …) | **Not reused as-is** — our rooms come from the floorplan and the department's function. The *idea* of a task room is adopted on the R&D floor (`14-RND-FLOOR.md`) |
| Phaser scene constants (`TILE_W/H`, room diamond geometry, agent slots, camera clamp) | **Adopted as-is** — MIT code, see the top of this file |
