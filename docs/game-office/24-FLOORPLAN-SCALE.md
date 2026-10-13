# 24 — Floor-plan scale: the arithmetic behind "the map is too small for the org"

**Status: analysis only. No code was changed to produce this document.**

The diagnosis on the table is *"the map is too small for the org"*. That is half right, and
the wrong half matters: **the map is not too small — the desks are too close together.** A
6×4 room holds 6 desks 2 tiles apart; at a 2-tile pitch two things are simultaneously true and
neither of them is a size problem. Read the numbers, then the recommendation.

Every number below is computed, not asserted. The script that produced them is reproduced in
§10; the constants it uses are read from these two files, and each is cited by line:

| constant | value | source |
|---|---|---|
| `ROOM_COLS, ROOM_ROWS` | `3, 2` → **6 rooms/floor** | `voicebridge/office/tower.py:98` |
| `ROOM_W, ROOM_H` | `12, 7` | `voicebridge/office/tower.py:99` |
| `SEAT_SLOTS_PER_ROOM` | `6` | `voicebridge/office/tower.py:100` |
| `FLOOR_HEIGHT_UNITS` | `3` (deck `z` = floor number × 3) | `voicebridge/office/tower.py:95,161` |
| `room_slot_x` | `col*(ROOM_W+1) + 1 + (n % 3) * 2` | `voicebridge/office/tower.py:178` |
| `room_slot_y` | `row*(ROOM_H+1) + 1 + (n // 3) * 2` | `voicebridge/office/tower.py:189` |
| `TILE_W, TILE_H` | `34, 17` (2:1 iso) | `qubit-office.js:90` |
| `DECK_Z` | `26` px per z unit | `qubit-office.js:91` |
| `FACE_H` | `36` deck-face units | `qubit-office.js:137` |
| `CHAR_FLOOR_ZOOM`, `MIN_ZOOM`, `MAX_ZOOM` | `0.85`, `0.45`, `2.4` | `qubit-office.js:94,97` |
| figure height / width | `15` / `9` units, pre-zoom | `qubit-office.js:340` (`drawAgent`) |
| marker stack | `20` above feet, box `12` tall | `qubit-office.js:373-374` |
| fit padding | `padX = w*0.94`, `padY = h*1.5` | `qubit-office.js:308` |
| canvas | default `880×500`, clamped to viewport, max `1200` wide | `qubit-office.js:686,856` |

---

## 1. What a 6×4 room actually holds — confirmed, and the collision it implies

`room_slot_x` is `1 + (n % 3) * 2` → wx ∈ {1, 3, 5}. `room_slot_y` is `1 + (n // 3) * 2` →
wy ∈ {1, 3, 5, 7, …}. A room is 6 wide and 4 deep, so:

| slot `n` | x | y | inside 6×4? |
|---|---|---|---|
| 0 | 1 | 1 | yes |
| 1 | 3 | 1 | yes |
| 2 | 5 | 1 | yes |
| 3 | 1 | 3 | yes |
| 4 | 3 | 3 | yes |
| 5 | 5 | 3 | yes |
| 6 | 1 | **5** | **drops** (y > ROOM_H = 4) |
| 7 | 3 | **5** | **drops** |

**The brief's reading is correct: 3 columns × 2 rows = 6 desks per room**, and `SEAT_SLOTS_PER_ROOM`
agrees at 6. Row 2 would sit at y=5, which is outside a 4-deep room, so it drops silently.
6 rooms/floor × 6 desks = **36 seats/floor**.

Usable capacity as a function of pitch (first desk at 1, step = pitch, must stay ≤ the room extent):

| pitch | usable cols in 6 | usable rows in 4 | desks in 6×4 |
|---|---|---|---|
| 2 (current) | 3 | 2 | **6** |
| 3 | 2 | 2 | 4 |
| 4 | 2 | 1 | 2 |
| 5 | 2 | 1 | 2 |

This is the whole problem in one table: **a 6×4 room is exactly the wrong shape for a legible
desk pitch.** Widening the pitch without growing the room does not make the desks easier to read
— it makes the room hold *fewer* of them. (That is the trap in the brief's own illustration: at
pitch 4 a 9×6 room holds 6 desks, the same as today's 6×4 — this is section 5's whole point.)

---

## 2. At what zoom does a desk cell become smaller than the figure?

From `iso()` (`qubit-office.js:254`) the iso basis is
`dx = (TILE_W/2, TILE_H/2) = (17, 8.5)` and `dy = (-17, 8.5)` per tile, all scaled by `zoom`.
A character is drawn `h = 15*zoom`, `w = 9*zoom`.

| zoom | desk cell (px) | figure (px) | body width ÷ cell width |
|---|---|---|---|
| 0.45 (`MIN_ZOOM`) | 15.3 × 7.7 | 4.0 × 6.8 | 0.265 |
| 0.70 | 23.8 × 11.9 | 6.3 × 10.5 | 0.265 |
| **0.85 (`CHAR_FLOOR_ZOOM`)** | 28.9 × 14.4 | **7.6 × 12.8** | 0.265 |
| 1.00 | 34.0 × 17.0 | 9.0 × 15.0 | 0.265 |
| 1.40 | 47.6 × 23.8 | 12.6 × 21.0 | 0.265 |
| 2.40 (`MAX_ZOOM`) | 81.6 × 40.8 | 21.6 × 36.0 | 0.265 |

**The answer is: never.** The ratio is zoom-independent, because both the tile and the figure
scale linearly with zoom. A figure is 9 units wide against a 34-unit tile — always 3.8× narrower
than its cell. The renderer's own guard is therefore not about the desk at all; `CHAR_FLOOR_ZOOM
= 0.85` (comment at `qubit-office.js:95`) is the point where a 15-unit figure is ~13 px tall,
i.e. barely a fingerprint. That is a *spot-a-person-across-the-room* floor, not a legibility
floor: 12.8 px tall × 7.6 px wide is one glance, not a readable state.

Consequence for the real problem: **zoom cannot fix occlusion either.** The next section is why.

---

## 3. At what zoom do two desks 2 tiles apart overlap in SCREEN space?

Two different "overlap"s, and conflating them is what produced the current defect.

**(a) Same row, 2 tiles apart (current `room_slot_x`).** Screen delta = `2·dx = (34z, 17z)`.
Bodies are `9z` wide, so separation is 34z ≥ 9z at *every* zoom — a 25-unit gap in unscaled
units. **A 2-tile column pitch never overlaps the body.** Columns are not the problem.

**(b) Row over row, 2 tiles apart (current `room_slot_y`).** This is where it breaks, and it
breaks *independently of zoom*. Row `y+1` sits `(TILE_H/2)·1 = 8.5` units **lower** on screen
than row `y`, and the figure at `y+1` extends `15` units **upward** from its feet. So:

> covered ⟺ `15·z > 8.5·z` ⟺ **`15 > 8.5`** — the `z` cancels.

| zoom | head reaches (px) | back desk is offset (px) | overhang |
|---|---|---|---|
| 0.85 | 12.8 | 7.2 | **5.5 px** |
| 1.00 | 15.0 | 8.5 | **6.5 px** |
| 1.40 | 21.0 | 11.9 | **9.1 px** |

**This is the known defect, and it is a constant, not a function of zoom.** No zoom choice can
fix it; zooming in makes the absolute overhang *worse*. The row-to-row overlap is a geometry bug,
full stop.

---

## 4. When does the back row get FULLY occluded, and what pitch fixes it?

A figure at row `y` fully covers a figure at row `y-1` when its head reaches past the *whole*
back figure, not just its top pixel. The figure is 15 units tall and the row offset is 8.5 units
per tile, so the coverage fraction is `min(1, 8.5·p / 15)` for a row pitch of `p` tiles:

| row pitch `p` | row offset (units) | versus figure 15 | clearance |
|---|---|---|---|
| 1 | 8.5 | covers **57%** of the figure behind | −6.5 |
| **2 (current)** | **17.0** | clears the body by 2.0 units | +2.0 |
| 3 | 25.5 | clears by 10.5 | **+10.5** |
| 4 | 34.0 | clears by 19.0 | +19.0 |

So the *bodies* clear at a 2-tile row pitch (17 > 15) and the reason the floor still looks wrong
is the other two drawn artefacts:

1. **The marker.** `drawMarker` (`qubit-office.js:373`) stacks to `y − 20z` and then draws a `12z`
   box — i.e. **32 units above the feet**. At a 2-tile row pitch the marker reaches 17 + 32 = 49
   units into the row behind, over the back figure's *head* and most of its body. A 3-tile pitch
   brings that to 25.5 + 32 = 57.5 units — still crossing. **The fix is headroom, not pitch**
   (§7).
2. **The desk glyph is drawn after the figure that should occlude the row behind it**, and the
   depth sort is by `x + y`, which is *ambiguous for a `(x+1, y-1)` pair* — both sum to the same
   value. In a room-local grid with a uniform 2-tile column pitch, `(x, y+2)` and `(x+2, y)` also
   collide in the sort. **Staggering the rows by an odd offset removes every tie.**

Full occlusion of a *body* needs `8.5·p < 15` for the covering figure, i.e. only `p = 1`. At the
current `p = 2` no body is fully hidden; what is hidden is the **desk plate and the state band**
of the back row, which is exactly what the owner sees as "the desks overlap and occlude each
other". The honest fix is the 25.5-unit clearance of `p = 3` **plus** row stagger, and it costs
one tile of room depth per extra row (§5).

---

## 5. How many rooms per floor, and how many floors does 11 / 25 / 50 / 70 seats need?

Rooms per floor is fixed by `tower.py:98` at `ROOM_COLS × ROOM_ROWS = 3 × 2 = 6`.

### (a) At the current 6×4 / 6-desks-per-room scale

| seats | rooms needed | floors needed | seats/floor |
|---|---|---|---|
| 11 | 2 | **1** | 36 |
| 25 | 5 | **1** | 36 |
| 50 | 9 | **2** | 36 |
| 70 | 12 | **2** | 36 |

**The map is not too small for the org.** The full documented org (71 seats, `README.md`) needs
two floors of six rooms at today's capacity. The building is *bigger than the org needs*; it is
the desks inside it that are packed too tightly. This is the number that disproves the stated
diagnosis.

### (b) Cell size is the binding constraint, not floor count

The 6×4 room cannot be re-pitched (§1). Candidate rooms, with what they hold and what they cost:

| room | pitch | cols | rows | desks | room + 1-tile gap | floor grid |
|---|---|---|---|---|---|---|
| 6×4 | 2 | 3 | 2 | **6** | 7×5 | 21 × 10 |
| 6×4 | 4 | 2 | 1 | 2 | 7×5 | 21 × 10 |
| 8×6 | 2 | 4 | 3 | 12 | 9×7 | 27 × 14 |
| 8×6 | 4 | 2 | 2 | 4 | 9×7 | 27 × 14 |
| 9×6 | 2 | 5 | 3 | 15 | 10×7 | 30 × 14 |
| **9×6** | **4** | **3** | **2** | **6** | 10×7 | **30 × 14** |
| 10×7 | 2 | 5 | 4 | 20 | 11×8 | 33 × 16 |
| 12×7 | 2 | 6 | 4 | 24 | 13×8 | 39 × 16 |
| **12×7** | **(4,3)** | **3** | **3** | **9** | 13×8 | **39 × 16** |

Note the 6-desk coincidence: 6×4 at pitch 2 and 9×6 at pitch 4 both hold 6 desks. **Neither is
good** — the first because the pitch occludes, the second because it wastes a room's worth of
tiles on nothing.

---

## 6. What zoom does the renderer actually give each configuration?

`fitToBuilding` (`qubit-office.js:298`) fits `padX = w·0.94` horizontally and `padY = h·1.5`
vertically, `zoom = clamp(min(zx, zy), CHAR_FLOOR_ZOOM, MAX_ZOOM)`. A floor adds `z = 3`, and
`z` moves *only* `y` (`iso()` subtracts `z·DECK_Z`), so stacking floors 78 px each. At the
1200 px canvas the horizontal term stops binding once three or more floors are stacked:

| canvas | floors | room | extent | + stack | zx | zy | **zoom** | desk px | figure px |
|---|---|---|---|---|---|---|---|---|---|
| 1200×500 | 1 | 9×6 | 510 | 357 | 2.21 | 2.10 | **2.10** | 71.4 | 31.5 |
| 1200×500 | 3 | 9×6 | 510 | 513 | 2.21 | 1.46 | **1.46** | 49.7 | 21.9 |
| 1200×500 | 5 | 9×6 | 510 | 669 | 2.21 | 1.12 | **1.12** | 38.1 | 16.8 |
| **1200×500** | **1** | **12×7** | 663 | 450 | 1.70 | 1.66 | **1.66** | 56.6 | 25.0 |
| 1200×500 | 2 | 12×7 | 663 | 528 | 1.70 | 1.42 | **1.42** | 48.2 | 21.3 |
| **880×500** | **1** | **12×7** | 663 | 450 | 1.25 | 1.66 | **1.25** | 42.4 | 18.7 |
| 880×500 | 2 | 12×7 | 663 | 528 | 1.25 | 1.42 | **1.25** | 42.4 | 18.7 |
| 880×500 | 7 | 12×7 | 663 | 918 | 1.25 | 0.82 | **0.85** | 28.9 | 12.8 |
| 1200×500 | 7 | 12×7 | 663 | 918 | 1.70 | 0.82 | **0.85** | 28.9 | 12.8 |

Two facts fall out:

- **A 12×7 room at 1–2 floors never leaves the legible band**, and at the 880 px default canvas
  the fit lands at 1.25 regardless of floor count up to two — a 42 px desk and an 18.7 px figure,
  comfortably above the 12.8 px `CHAR_FLOOR_ZOOM` floor.
- **Seven floors is past the edge.** The height term drives zoom to 0.82, clipped by
  `CHAR_FLOOR_ZOOM` to 0.85, and the building then overflows. This is the real constraint on the
  room *height* in the recommendation: an org that needs seven floors cannot also afford a taller
  room, so the room must be sized for the floor count the org actually reaches — which is one,
  two at the documented 71 seats (§7).

---

## 7. RECOMMENDATION

**Change `tower.py`'s room constants to 12×7 and the desk stride to `(col 4, row 3)` with a
1-tile row stagger. Do not change `ROOM_COLS/ROOM_ROWS`, and do not enlarge the building.**

| parameter | current | recommended | why |
|---|---|---|---|
| `ROOM_W, ROOM_H` | 6, 4 | **12, 7** | 3 columns × 3 rows fits at a pitch that clears |
| column pitch | 2 | **4** | 68 units between bodies vs 9 units of body width |
| row pitch | 2 | **3** | 25.5 units of row offset vs a 15-unit figure — **+10.5 clearance** |
| row stagger | 0 | **1 tile, odd rows** | removes the `(x+1, y-1)` depth-sort tie (`x+y`), so no desk is ever drawn at the same sort key |
| desks/room | 6 | **9** | 3×3 grid; the 3 rows land at y = 1, 4, 7 |
| `ROOM_COLS × ROOM_ROWS` | 3×2 = 6 | **3×2 = 6 (unchanged)** | floor count is not the constraint |
| floor grid | 21 × 10 | **39 × 16** | `3·(12+1) × 2·(7+1)` |
| desks/floor | 36 | **54** | 6 rooms × 9 |
| `FLOOR_HEIGHT_UNITS` | 3 | **3 (unchanged)** | 78 px/floor; 2 floors → z ∈ {3, 6} → 156 px |
| `FACE_H` | 20 | **32, or inset the top desk row** | `drawMarker` stacks 20+12 = 32 units above the feet; at 20 the marker crosses a floor boundary by 12 |

Desk positions in the recommended room (`x = 1 + 4i` + stagger, `y = 1 + 3j`):

```
row j=0  y=1:  x = 1,  5,  9     (stagger +0)
row j=1  y=4:  x = 2,  6, 10     (stagger +1)   <- odd offset
row j=2  y=7:  x = 1,  5,  9     (stagger +0)
                                     = 9 desks
```

### Resulting floorplan per org size

| seats | rooms | floors | floor grid | deck z | deck stack on screen | fit zoom (880/1200 px) |
|---|---|---|---|---|---|---|
| **11** | 2 | **1** | 39 × 16 | 3 | 78 px | 1.25 / 1.66 |
| **25** | 3 | **1** | 39 × 16 | 3 | 78 px | 1.25 / 1.66 |
| **50** | 6 | **1** | 39 × 16 | 3 | 78 px | 1.25 / 1.66 |
| **70** | 8 | **2** | 39 × 16 | 6 | 156 px | 1.25 / 1.42 |

Every one of these sits inside the 1200 px canvas at zoom ≥ 1.25, giving a 42–57 px desk and an
**18.7–25.0 px figure** — better than the current setup reads at *any* zoom, and 1.5–2× the
`CHAR_FLOOR_ZOOM` floor. **11 seats now occupies a single floor and a single room pair**, which is
what the owner is looking at.

### The tradeoff I am choosing

> **Bigger cells, same building. I am buying legibility with tiles-per-room and paying for it in
> *panning inside a room*, not in floors.**

- A 12×7 room makes the floor grid 39 × 16 instead of 21 × 10 — 1.86× wider, 1.6× deeper. That is
  capacity thrown away: 54 desks/floor against an org of 6–8 rooms means the upper floors stay
  empty for a long time, and 39 tiles at zoom 1.25 is 1.63× an 880 px canvas, so the user must pan
  to see all six rooms on one floor.
- The alternative — keep 6×4, raise the pitch, add floors — is *worse*: §6 shows a 7-floor stack
  clips on `CHAR_FLOOR_ZOOM` and overflows, and §1 shows a re-pitched 6×4 holds 2 desks.
- **I choose the panning.** `clampPan` (`qubit-office.js:264`) already exists precisely so a
  larger extent stays reachable, and the pan margin is 30% of the deck. An office whose people are
  readable at a glance and needs a drag to see the far room is a working drawing of an office; a
  building that fits with no drag whose agents are 13 px fingerprints is not.

If panning is judged unacceptable, the honest fallback is **9×6 at pitch (3, 3) with a 1-tile
stagger: 30 × 14 grid, 2 columns × 3 rows = 6 desks/room, 36/floor** — a 21%-smaller floor print
than the recommendation for 33% fewer desks. Do **not** fall back to 9×6 at pitch 4: §5 shows it
holds 6 desks, i.e. it buys nothing over today's room.

---

## 8. The known defect: the back row hidden behind the front — the concrete fix

**Diagnosis.** Not a pitch problem alone. Three separate causes, each with its own arithmetic:

| # | cause | arithmetic | fix |
|---|---|---|---|
| 1 | figure taller than the row offset | `15 > 8.5` at pitch 2 → 6.5-unit overhang | row pitch **2 → 3** → offset 25.5, clearance **+10.5 units** (constant in zoom) |
| 2 | depth sort tie on diagonals | `(x, y+2)` and `(x+2, y)` both have `x+y` equal; a `(x+1, y-1)` pair differs by exactly `TILE_W = 34` with `Δy = 0` (the 2:1 iso trap) | **stagger alternating rows by 1 tile** → no two desks share a sort key |
| 3 | marker drawn over the row behind | `drawMarker` reaches `20 + 12 = 32` units above the feet; at pitch 3 that is `25.5 + 32 = 57.5` units into the row behind | raise `FACE_H` 20 → **32**, *or* inset the top desk row one tile |

**Proof the fix holds at every zoom.** The occlusion test is scale-free because both terms carry
`z`. For any two desks `A = (x₁, y₁)` and `B = (x₂, y₂)` with `B` drawn after `A`
(sort by `x + y`, then `y`):

```
|sx₂ − sx₁| ≥ CHAR_W   OR   sy₂ > sy₁        (screen space, pre-zoom)
in tiles:  y₂ ≠ y₁  ⇒  |y₂ − y₁| · 8.5  ≥ 15  ⇒  |y₂ − y₁| ≥ 2      ✓ (pitch 3)
           y₂ = y₁  ⇒  |x₂ − x₁| · 17 ≥ 9   ⇒  |x₂ − x₁| ≥ 1      ✓ (pitch 4)
```

With the odd stagger the desk set contains **no** `(x + k, y − k)` pair at all, so the `Δy = 0`
degenerate case in row 2 cannot arise, and the sort is total. Verified at the zooms the
recommended grid actually receives:

| zoom | row offset | figure height | verdict | clearance |
|---|---|---|---|---|
| 0.50 | 12.8 px | 7.5 px | CLEAR | +10.5 units |
| 0.70 | 17.8 px | 10.5 px | CLEAR | +10.5 units |
| 0.85 | 21.7 px | 12.8 px | CLEAR | +10.5 units |
| 1.00 | 25.5 px | 15.0 px | CLEAR | +10.5 units |

**Ordering fix (do this regardless of geometry).** `drawTower` currently paints all decks, all
desk plates, all deck labels, and *then* every character on top (`qubit-office.js:406-510`) —
so a far-row desk is painted over a near-row figure that should occlude it. The fix is one sorted
draw list per floor keyed by `sortKey = (x + y, y)` for desks and characters alike, painted back
to front, with the stagger above eliminating ties. This is the same technique the reference
renderers use; without it the pitch change alone still leaves desks drawn on top of the people in
front of them.

---

## 9. What to change, in code terms (not done here — this document is read-only analysis)

If the owner accepts the recommendation, the change is bounded to four constants and two
functions, none of which need new fields in the payload:

| file | change |
|---|---|
| `voicebridge/office/tower.py:99` | `ROOM_W, ROOM_H = 6, 4` → `12, 7` |
| `voicebridge/office/tower.py:100` | `SEAT_SLOTS_PER_ROOM = 6` → `9` |
| `voicebridge/office/tower.py:178,189` | strides `(n % 3) * 2` / `(n // 3) * 2` → `(n % 3) * 4` / `(n // 3) * 3`, plus `+ (1 if (n // 3) % 2 else 0)` row stagger |
| `voicebridge/office/tower.py:73-78` | `STATIONS` offsets are room-local and must move inside a 12×7 room (currently all ≤ (4, 3)) |
| `frontend/.../qubit-office.js:92` | `FACE_H = 20` → `32`, or inset the top desk row |
| `frontend/.../qubit-office.js:406-510` | one sorted back-to-front draw list per floor (desks + characters), key `(x + y, y)` |

`test_office_tower_plugin.py` asserts against the plugin's *text*, not these constants, so a
room-size change is invisible to it — but any test that pins seat coordinates or the grid dims
(`grid.cols = ROOM_COLS * (ROOM_W + 1)`) will need updating in the same commit, and saying so is
part of the change, not a follow-up.

---

## 10. The arithmetic, reproducible

The script below is the source of every figure in this document. It reads no repo state at
runtime — the constants are transcribed above with their line numbers — so it is safe to run
anywhere. Output was verified at exit code 0.

```python
TILE_W, TILE_H = 34.0, 17.0      # qubit-office.js:90 (2:1 iso)
DECK_Z         = 26.0            # qubit-office.js:91
CHAR_H, CHAR_W = 15.0, 9.0       # qubit-office.js:340 drawAgent
MARKER_TOP     = 20.0            # qubit-office.js:373
ROOM_W, ROOM_H = 6, 4            # tower.py:99
ROOM_COLS, ROOM_ROWS = 3, 2      # tower.py:98
FLOOR_H_UNITS  = 3               # tower.py:95
CHAR_FLOOR_ZOOM = 0.85           # qubit-office.js:97

# --- 1. capacity of a 6x4 room
slot_x = lambda n: 1 + (n % 3) * 2          # tower.py:178
slot_y = lambda n: 1 + (n // 3) * 2         # tower.py:189
# slots 0..5 land in x{1,3,5} x y{1,3}; slot 6+ lands at y=5 > ROOM_H -> dropped
# -> 3 cols x 2 rows = 6 desks/room; 6 rooms/floor -> 36 seats/floor
# 11 seats -> 2 rooms/f1 ; 25 -> 5/f1 ; 50 -> 9/f2 ; 70 -> 12/f2

# --- 2. desk cell vs figure, at zoom z (both scale by z => ratio is constant)
# tile cell = (TILE_W*z, TILE_H*z) ; figure = (9z, 15z) ; ratio 9/34 = 0.265 always
# => a desk cell is NEVER smaller than the figure. CHAR_FLOOR_ZOOM=0.85 is the
#    point a figure is 12.8px tall, i.e. the spot-it floor, not the desk.

# --- 3. row-over-row occlusion (the known defect). z cancels:
# figure at row y+1 extends 15*z up; row y sits 8.5*z lower on screen
# 15*z > 8.5*z  <=>  15 > 8.5  -> ALWAYS occluded, at every zoom.
# same-row 2-tile column pitch: delta = (34z, 17z) vs a 9z body -> no body overlap.

# --- 4. row pitch p -> offset 8.5*p units vs a 15-unit figure
for p in (1, 2, 3, 4):
    print(p, 8.5 * p, "CLEAR" if 8.5 * p >= 15 else "occludes by %.1f" % (15 - 8.5 * p))
# p=1 occludes by 6.5 ; p=2 clears by 2.0 ; p=3 clears by 10.5 ; p=4 by 19.0
# marker needs 20+12 = 32 units of headroom, so p=2's 17 is not enough either.

# --- 5. recommended room 12x7, stride (4, 3), odd row stagger
for j, y in [(0, 1), (1, 4), (2, 7)]:
    xs = [1 + 4 * i + (1 if j % 2 else 0) for i in range(3)]
    print("row", j, "y", y, "x", xs)     # +9 desks/room
# floor grid = 3*(12+1) x 2*(7+1) = 39 x 16 ; desks/floor = 6*9 = 54

# --- 6. fitToBuilding: padX = w*0.94, padY = h*1.5 ; extent for a 3x2 room block
def extent(rw, rh):
    pts = [(c * (rw + 1), r * (rh + 1)) for c in range(3) for r in range(2)]
    pts += [(x + rw, y + rh) for x, y in pts]
    xs = [(x - y) * (TILE_W / 2) for x, y in pts]
    ys = [(x + y) * (TILE_H / 2) for x, y in pts]
    return max(xs) - min(xs), max(ys) - min(ys)
# 12x7 -> 663 x 450 ; 1 floor -> stack 450 -> zoom min(1200*.94/663, 500*1.5/450) = 1.66
# 12x7 at 880px -> min(827/663, 750/450) = 1.25 ; 7 floors -> zy 0.82 -> clipped to 0.85
```

The working script (with all nine sections and their printed output) is kept at
`~/.local/state/qubit-voicebridge/runtime/cache/scratch/floorplan_arith.py`.

---

## 11. One-line answer

**The map is not too small — 71 seats fit in 12 rooms / 2 floors today, and 11 seats in 2 rooms.
The desks are 2 tiles apart, and a 15-unit figure at a 2-tile row pitch overhangs the row behind
by 6.5 units at every zoom, while a 2:1 iso projection makes `(x+1, y−1)` pairs tie in the
depth sort. Fix it with `ROOM_W, ROOM_H = 12, 7` and stride `(4, 3)` plus a 1-tile odd row
stagger: 9 desks/room, 54/floor, floor grid 39×16, a 25.5-unit row clearance against a 15-unit
figure, and 11 / 25 / 50 seats on one floor and 70 on two — all inside the 1200 px canvas at zoom
≥ 1.25. The tradeoff is panning inside a room; I take the panning.**
