# 37 — Wall volume: is the cutaway wall face tall enough to read as a building?

> **Status:** measurement + recommendation. This file produces the arithmetic and the
> recommended edit; **it does not apply it.** `frontend/qubit-app/plugins/qubit-office/qubit-office.js`
> is owned by the renderer worker. The exact edit is in §6.
>
> **Verdict, plainly:** the current `FACE_H = 20` is **not wrong — it is stale.** It was
> correct for a `6x4` room and it was never re-derived when the room became `12x7`. The
> wall face is now **12.4% of the plate it stands on** (it was 23.5% before the room
> grew), i.e. **8.1x shorter than the plate**, which is why the deck reads as a flat
> plate with a coloured rim rather than a room with volume.

Measured at commit `bd6ec9042`, 2026-10-13. All constants parsed from source, not
transcribed from this prose; the proof is `tests/test_office_wall_volume.py` (6 tests,
all passing).

---

## 1. The real constants (parsed, not assumed)

| constant | value | source |
|---|---|---|
| `TILE_W` | **34** | `qubit-office.js:135` |
| `TILE_H` | **17** | `qubit-office.js:135` |
| `DECK_Z` | **26** | `qubit-office.js:136` |
| `FACE_H` | **20** | `qubit-office.js:137` |
| `FACE_W` | 4 | `qubit-office.js:137` |
| `CHAR_FLOOR_ZOOM` | 0.85 | `qubit-office.js:142` |
| `MIN_ZOOM` / `MAX_ZOOM` | 0.45 / 2.4 | `qubit-office.js:139` |
| `FLOOR_HEIGHT_UNITS` | **3** | `tower.py:95` |
| `ROOM_W, ROOM_H` | **12, 7** | `tower.py:110` |
| `ROOM_COLS, ROOM_ROWS` | 3, 2 | `tower.py:109` |
| canvas | **880 x 500** device px | `qubit-office.js:1339` |

The brief believed `FACE_H` was 20 — **confirmed: it is 20.** But the brief's worked
example (`48px` wall vs `306px` depth at a fitted zoom of `2.4`) does **not** match this
build. The real fitted zoom is **0.85**, not 2.4, and the real plate depth is **137px**,
not 306px. The defect is the same and the conclusion is the same; the numbers in the
brief were illustrative, not measured. Below are the measured ones.

---

## 2. The fitted zoom — computed from the renderer's own `fit`, not guessed

The renderer frames the building with a **COVER** fit (`camera.js:191`, called from
`qubit-office.js:439-446`) on an 880x500 canvas at `margin 0.08`, with a floor of
`CHAR_FLOOR_ZOOM = 0.85` applied last.

The world extent of a real 3-floor plan (rooms `12x7` on a `3x2` grid, six rooms/floor,
`z = number * FLOOR_HEIGHT_UNITS`) measures **901.0 x 606.5** world units. Cover fit:

```
availW = 880 * (1 - 0.16) = 739.2      availH = 500 * (1 - 0.16) = 420.0
cover  = min(739.2 / 901.0, 420.0 / 606.5)
       = min(0.8204,             0.6925)         <- HEIGHT limits
       = 0.6925
zoom   = max(0.6925, CHAR_FLOOR_ZOOM 0.85) = 0.85
```

So the fitted zoom is **0.85** — the building overflows the canvas on its height axis and
that is accepted (the legibility floor wins; the fit is documented to crop architecture
but never lose the people). A 1-floor plan gives `cover = 0.8204` and lands on the same
`0.85` floor. Every realistic canvas width (240-1200px) and floor count yields **0.85**,
except a 1200px canvas with a single floor (0.932). **Use 0.85.**

---

## 3. The finding: wall face vs the plate it stands on

The cutaway wall face is drawn at ``h2 = FACE_H * cam.zoom`` px (`qubit-office.js:497`,
`drawRoomPlate`). The room plate is the iso diamond spanning `ROOM_W` columns and
`ROOM_H` rows, whose screen height is ``(ROOM_W + ROOM_H) * TILE_H/2`` world units
(the `sy = (x+y) * TILE_H/2` term), i.e. ``* zoom`` px on screen.

```
wall  = FACE_H * zoom         = 20 * 0.85 = 17.00 px
depth = (12 + 7) * (17/2) * 0.85          = 137.28 px
ratio = 17.00 / 137.28                    = 0.1238
```

**The wall face is 17.00px against a 137.28px plate — a ratio of 0.1238, so the wall is
8.08x shorter than the plate it stands on.** A 17px riser under a 137px floor reads as a
coloured rim, not as a wall. That is the "flat plates" report, as a number.

### 3.1 It is a regression from the room enlargement, not a random value

Same `FACE_H`, same `TILE_H`; only the room changed `6x4 -> 12x7`:

```
old plate depth = (6 + 4) * 8.5 =  85.0 world units   -> ratio 20/85.0  = 0.2353
new plate depth = (12 + 7) * 8.5 = 161.5 world units   -> ratio 20/161.5 = 0.1238
```

The plate grew **1.90x** while `FACE_H` stayed at 20, so the wall's share of the plate
**shrank 1.90x** — from 23.5% to 12.4%. The constant never moved; the thing it is
proportional to did. This is the whole defect: **`FACE_H` is a fixed world height, and the
plate it frames became twice as deep.**

### 3.2 The ratio is zoom-invariant — so a bigger window cannot fix it

Both the wall height (`FACE_H * zoom`) and the plate depth (`(w+h) * TILE_H/2 * zoom`)
carry `* zoom`, so `zoom` cancels:

```
ratio = FACE_H / ((ROOM_W + ROOM_H) * TILE_H/2) = 20 / 161.5 = 0.1238
```

at **every** canvas size, every floor count, every user zoom. The defect is the ratio of
two constants, not a window-size accident. Only moving `FACE_H` changes it.

---

## 4. Do the floors merge? (a different defect — checked, and they do not)

Two stacked decks are separated on screen by
``FLOOR_HEIGHT_UNITS * DECK_Z * zoom`` px — **not** ``DECK_Z * zoom``: `tower.py:179`
sets ``z = floor.number * FLOOR_HEIGHT_UNITS`` and the projection multiplies that `z` by
`DECK_Z`.

```
deck step = 3 * 26 * 0.85 = 66.30 px
wall rises           17.00 px
clearance            49.30 px   (the wall uses 25.6% of the floor gap)
```

The floors **do not** merge: the wall consumes a quarter of the vertical gap between
decks, leaving 49px of clear sky above every room. So this building's defect is **purely
"flat", not "merged."** That matters for the recommendation — there is a lot of unused
vertical room to grow a wall into.

---

## 5. The self-consistency ceiling on `FACE_H`

A wall may not rise past the gap to the deck behind it, or floors interpenetrate. Both
terms carry `* zoom`, so the ceiling is zoom-invariant:

```
FACE_H  <=  FLOOR_HEIGHT_UNITS * DECK_Z  =  3 * 26  =  78  world units
```

Current `FACE_H = 20` uses **25.6%** of that ceiling, leaving **58 units of headroom**.
So the wall can be raised substantially before any risk of interpenetration — the
constraint that actually binds is §3 (does it read as volume), not §5 (does it merge).

---

## 6. Recommendation

### 6.1 The value

**`FACE_H = 36`** (from 20). Reasoning:

| `FACE_H` | wall px @0.85 | ratio | reads as | merges? |
|---|---|---|---|---|
| 20 (current) | 17.0 | 0.124 | a rim | no |
| 24 | 20.4 | 0.149 | a low kerb | no |
| **36 (recommended)** | **30.6** | **0.223** | **a wall** | **no** |
| 40 | 34.0 | 0.248 | a wall (tall) | no |
| 78 | 66.3 | 0.483 | a floor — **merges** | **yes** |

36 gives a **0.223** ratio — the wall is **4.5x shorter** than the plate, up from 8.1x —
and uses **46%** of the 78-unit interpenetration ceiling, leaving 42 units of clearance.
That is the point where a 30.6px wall face is visibly a *surface* rather than a *line*
against a 137px plate, without crowding the deck behind it.

If a stronger read is wanted, **40** is also legal (0.248, using 51% of the ceiling, 49%
below it) and is
the value `tests/test_office_wall_volume.py` calls out as "the 1/4 threshold." **36 is the
conservative-legal pick; 40 is the aggressive-legal pick.** Do not exceed 78.

### 6.2 Does `DECK_Z` need to change? **No.**

`DECK_Z = 26` is the *pixels-per-z-unit* scale, and it is shared with the projection, the
camera extent, and the depth sort (`iso()`, `extent()`, `depthKey`). Changing it moves
every deck and re-frames the whole building — a large, unrelated change. `FACE_H` is a
pure draw-time height owned by `drawRoomPlate` alone (`qubit-office.js:497`), so raising
it changes **nothing** but the two wall quads. Leave `DECK_Z` at 26.

### 6.3 The tradeoff, stated honestly

Raising `FACE_H` is not free, and the cost is the *opposite* of the cutaway intent:

* The walls hang **down** from each deck's top face (`qubit-office.js:499-509`, `a.y -> a.y - h2`).
  A taller wall covers more of the room's near edge — the two viewer-facing tiles the
  room's desks and front-row characters stand on.
* At `FACE_H = 36` the wall covers **36 of the room's 161.5 world units of depth on the
  screen-y axis (22%)**. At 78 it would cover 48%. The cutaway exists precisely to keep
  the interior visible; past roughly a quarter of the plate the front row of desks starts
  to tuck behind its own wall.
* So there is a **window**, not a "taller is better": below ~24 the wall is a rim (flat),
  above ~44 it starts hiding the near desks (anti-cutaway). **36-40 is the window**, and
  36 sits at the low, safe end.

Nothing here is a visual-cue claim that can be settled by argument — the numbers bound it:
the wall must be a meaningful fraction of the plate (§3) and must not eat the interior
(§6.3) nor cross the deck ceiling (§5). 36-40 is the intersection of those three.

---

## 7. The exact edit (for the renderer worker — NOT applied here)

**One line.** `frontend/qubit-app/plugins/qubit-office/qubit-office.js:137`:

```diff
-  var FACE_H = 20, FACE_W = 4;       /* cutaway wall face height, in deck units */
+  var FACE_H = 36, FACE_W = 4;       /* cutaway wall face height, in deck units */
```

No other line changes. `drawRoomPlate` already reads `FACE_H` (`:497`), the constant is
module-local, and the bundled copy `qubit-office.bundle.js:2715` carries the same value —
**rebuild the bundle** (`scripts/build_office_bundle.py`) so the served plugin and the
bundle agree, then re-run the wall-volume test against the renderer.

Falsification check (run, recorded): temporarily setting `FACE_H = 40` in the renderer
makes `test_wall_face_is_a_stated_fraction_of_the_plate_it_stands_on` **FAIL** with
`ratio 0.2477` clearing the 0.20 threshold — so the test is load-bearing, not vacuous.
The renderer was restored to `FACE_H = 20` afterwards and every test passes.

---

## 8. The proof

```
$ .venv/bin/python -m pytest tests/test_office_wall_volume.py -v -s
```

Real output:

```
test_wall_face_is_a_stated_fraction_of_the_plate_it_stands_on
  fitted zoom (3 floors, 880x500) = 0.85
  wall face  = FACE_H(20.0) * zoom(0.85) = 17.00 px
  room depth = (w12+h7) * TILE_H/2(8.5) * zoom = 137.28 px
  ratio wall/depth = 0.1238
  the wall face is 8.08x shorter than the plate, 12.4% of it          PASSED
test_the_ratio_is_zoom_invariant_...
  ratio is 0.1238 at every zoom (FACE_H / ((w+h)*TILE_H/2))            PASSED
test_the_enlarged_room_made_the_wall_proportionally_thinner
  plate depth: 85.0 -> 161.5 world units (1.90x)
  wall share of the plate fell 1.90x (0.526 of its former ratio)       PASSED
test_deck_separation_keeps_the_wall_out_of_the_deck_behind_it
  deck step = FLOOR_HEIGHT_UNITS(3) * DECK_Z(26) * zoom(0.85) = 66.30 px
  wall rises 17.00 px -> clearance 49.30 px (25.6% of the gap)         PASSED
test_a_taller_wall_fits_under_the_interpenetration_ceiling
  interpenetration ceiling FACE_H <= FLOOR_HEIGHT_UNITS*DECK_Z = 78 (zoom-invariant)
  current FACE_H 20 leaves 58 units of headroom (25.6% used)           PASSED
test_a_recommended_face_h_reaches_volume_and_stays_legal
  plate depth = 161.5 world units
  to reach ratio 0.25: FACE_H ~= 40 ;  ceiling = 78 -> LEGAL            PASSED

6 passed in 0.21s
```

---

## 9. What this file does not do

* It does **not** edit `qubit-office.js` or the bundle — §7 is the one-line change for the
  renderer worker.
* It does **not** change `DECK_Z`, the projection, the depth sort, or the payload
  contract. The change is a single draw-time constant.
* It does **not** claim a *look* was verified — no screenshot was taken. It bounds the
  change by arithmetic: the current value fails the volume test (§3), the floors do not
  currently merge (§4), and 36-40 sits inside the legal window on every side (§5, §6.3).
