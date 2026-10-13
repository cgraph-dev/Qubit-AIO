# 30 — Source inventory

Per repo: exact paths, real file counts, what is taken, what is rejected. Every count was
produced by a command in this session; none is estimated.

Rule (skill): **cite `repo@ref:path` for every claim, and record what is deliberately *not*
taken alongside what is.**

---

## 3.1 `androoAGI/starnet` @ `54899e5ff3f2` — the primary source

**Size on disk:** 3.5 GB / 47,183 files (mostly build
output — see "rejected").

### Assets taken

| asset | path in starnet | count | used for |
|---|---|---|---|
| crew sprite sets | `frontend/assets/sprites/approved_*` | **50 folders** | the agent figures (M6) |
| crew poses | inside each: `rot_{8 dirs}.png`, `walk_{dir}_{1-4}.png`, `sit_{dir}_4.png`, `type_{dir}_{1-4}.png` | 8 rot + 4 walk + 1 sit + 4 type | idle facing, walking, seated, typing |
| furniture | `frontend/assets/furniture/*.png` | **48** | desks/chairs/plants/props (M8) |
| UI sounds | `frontend/assets/sfx/*.wav` | **12** | optional; gated by M9 |
| pixel font | `frontend/assets/fonts/vt323.woff2` | 1 | in-canvas labels (M10) |

Example pose list (`sprites/approved_android/`): `rot_east.png`, `rot_north.png`,
`rot_north-east.png`, … `walk_east_1..4.png`, `sit_north_4.png`, `type_north_1..4.png`. The
8-directional rotation set is what makes a figure face its travel direction without the
renderer faking it.

### Techniques taken (re-expressed, no files copied)

| starnet source | technique | already in / becomes |
|---|---|---|
| `frontend/agent-demo/proportion-review.js` | props + bodies in ONE list sorted by screen-y of the standing point | `paintOrder()` (already) |
| `frontend/agent-demo/sprites.js` | feet-anchored sprite, ground shadow, frame-pad caches | `sprites.js` bake (already) |
| `frontend/agent-demo/stationbake.js` | hue-preserving `shade()`, iso wall-face proportion | `shade()` (already) |
| *design law* (README, product contract) | "a room is a capability-scoped team; a hallway is a handoff lane; a placed object is a real capability grant" | **M11** — adopted as the tower's law |

### Rejected from starnet

| rejected | why |
|---|---|
| its Tauri desktop harness (`src-tauri/`, 6.2 MB) | the Office is a dashboard plugin, not a desktop app |
| `website/` (1.4 GB) and `frontend/` build output (1.4 GB) | build artifacts, not source — this is the bulk of the 3.5 GB |
| its station art style | the Office has its own 2:1 iso camera and dimensions |
| its 39,869 PNGs wholesale | only `sprites/approved_*` + `furniture/` are used (98 files) |

---

## 3.2 `FulAppiOS/Agent-Quest` @ `6c32b2986096` — the tiles

Art under CC0 (below).

| asset | path | count |
|---|---|---|
| CC0 theme, total | `client/public/assets/themes/tiny-swords-cc0/` | **207 files** |

Breakdown by top-level folder:

| folder | files | used for |
|---|---|---|
| `UI/` (Banners, Buttons, Icons, Pointers, Ribbons) | 90 | HUD chrome, seat badges |
| `Factions/` (Goblins, Knights) | 61 | (rejected — fantasy characters fight the agent theme) |
| `Deco/` | 18 | props |
| `Resources/` | 16 | resource pips (maps to ledger cost/revenue) |
| `Terrain/` (Ground, Water, Bridge) | 10 | **floor surface tiles (M7)** |
| `BuildingsCustom/` (Alchemist, Arena, Castle, Chapel, Forge, Library, Tavern, Tower) | 8 | **room exteriors (M7)** |
| `Effects/` | 2 | optional |

### Technique taken

**The single best idea in any source** (`65-REFERENCES.md`): *the building an agent walks to
**is** what it is doing* — Read→Library, Edit→Forge, Bash→Arena. The Office's version: the
room's `kind` comes from the tool being used. Given `BuildingsCustom` names 8 archetypes, this
maps naturally onto room kinds.

### Rejected

| rejected | why |
|---|---|
| `Factions/` sprites | fantasy theme; the Office renders an agent *corporation*, not a village |
| its session-log scraping data source | the Office reads `office_event`, not editor logs |
| its building-level minimap component | the Office has `drawMinimap()` already |

---

## 3.3 `pixel-agents-hq/pixel-agents` @ `d1e007a9fdf3`

**Size:** 5.4 MB / 380 files. 63 PNGs.

| taken | where | becomes |
|---|---|---|
| **the occlusion law** — one array, sort on tile **bottom** (`renderer.ts:487`, `:404` `charZY = y + TILE/2 + 0.5`), walls folded into the same array | design doc `26-OCCLUSION-AND-DEPTH.md` | **M12** — already implemented as `paintOrder()`/`sortByDepth()`; the merge keeps it |
| **areas** — paint a named region, bind folders/teams to it | design | room↔team binding (`room_layout.py`, M11) |
| speech bubble = waiting-for-input | design | already the `blocked` animation state |
| `webview-ui/public/assets/characters/char_*.png` (63) | assets | **rejected** — starnet's 50 sets are higher quality and already 8-directional; a second sprite style would split the look |

### Rejected
Its VS Code extension form; its Claude-Code hook provider (our runtime is not Claude Code);
its top-down grid (the formula is `worldY + TILE/2`, ours is `(x+y)` — the *technique* transfers, the *formula* does not, which `26-OCCLUSION-AND-DEPTH.md` already records).

---

## 3.4 `a16z-infra/ai-town` @ `8e05997f2409`

**Size:** 16 MB / 208 files.

| taken | becomes |
|---|---|
| **the three-layer split** — simulation engine + shared global state + vector search as clean layers | validates the Office's own split (`tower.py` fold + store + read route) |
| **"the world pauses when nothing is watching"** | **M14** — this *is* C7; cite it, do not reimplement |
| tile grid with explicit tile dimension (`worldMap.ts`) | already `TILE_W`/`TILE_H` |
| facing from travel direction + status marker (`Character.tsx`) | already `drawAgent()`/`drawMarker()` |

### Rejected
Convex (a cloud dependency; the Office is local-first); its PixiJS town art
(`public/assets/rpg-tileset.png`, `magecity.png`) — CC-unknown, superseded by agent-quest's
CC0 tiles; its chat/socialize focus over work.

---

## 3.5 `ruiqili2/agent-monitor` @ `bec78ad8cb64`

**Size:** 1.7 MB / 132 files. **No assets** (0 media files).

| taken | becomes |
|---|---|
| **boss chat** — broadcast + per-agent direct | **M13** — maps onto the existing escalation/journal surface, not a new channel |
| **autowork** on a ticker with per-session policy | the turn cadence; already the `office_event` stream + `ACTIVE_WINDOW_S` |
| painter's-algorithm depth sort on `col + row` (`src/engine/canvas.ts`) | already recorded in `25-PORTED-CODE.md`; the same law as pixel-agents |

### Rejected
Its OpenClaw gateway coupling (the Office has its own ops broker); its Next.js card dashboard
form (the Office is a canvas, not a card list).

---

## 3.6 `rsanandres/aphae` — dropped

No license at any upstream path. Deleted from `/refs`. Nothing
taken. Its *director pattern* idea remains available and is already applied to the CEO weekly
review per `65-REFERENCES.md`, with no code copied — that status is unchanged and correct.

---

## Summary — the byte budget

| source | files taken | assets taken |
|---|---|---|
| starnet | ~98 asset files + 0 code files | 50 sprite sets, 48 furniture, 12 sfx, 1 font |
| agent-quest | ~120 asset files (Terrain + BuildingsCustom + UI subset) | CC0 tiles/buildings/UI |
| pixel-agents | 0 files (law only) | — |
| ai-town | 0 files (law only) | — |
| agent-monitor | 0 files (law only) | — |
| **total** | **~218 asset files**, 0 verbatim source files | |

**Zero source files are copied verbatim from any repo.** The merge takes art files and
design laws; every line of Office logic remains the repo's own.
