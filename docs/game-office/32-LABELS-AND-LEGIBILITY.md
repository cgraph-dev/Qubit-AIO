# 32 — Labels and legibility: where the tower's text goes, measured

> **Status 2026-10-13 — measurement and a placement rule. No renderer code was changed to write
> this document.** Every number below is computed from the shipped constants and reproduced by a
> test; the reproduction command and its real output are in §8. Where a claim is a *measurement*
> it says so; where it is a *rule* it is arithmetic a fix must satisfy.

## 0. What the report said, and what is actually true

A vision read of the rendered page reported two things:

> *"`FLOOR 1 · executive`, `FLOOR 2 · operations` and `FLOOR 3 · intelligence` overlap each other
> where the decks stack. `review` (a room label) is buried under `FLOOR 3`. At the fitted zoom the
> agent figures may still be small."*

Measured, the report's first half is **wrong in its mechanism and right that something is wrong**:

| the report said | the measurement says |
|---|---|
| the three FLOOR labels **overlap each other** | they do **not**. They are exactly one deck pitch apart — **187.20 px** at the fitted zoom, against a **12.0 px** glyph. The gap is **175.20 px**, 15.6× the glyph. Two label boxes collide only if their baselines are under 12 px apart; a collision needs `zoom < 0.1538`, which the renderer's own legibility clamp (`0.85`) makes unreachable. |
| `review` is **buried under** `FLOOR 3` | a room label is drawn correctly *inside* its own room's plate (verified for all 6 rooms × 3 floors × 2 canvases, §5). What buries `review` is different: the **`FLOOR 1` label is drawn off the bottom of the canvas** (baseline y = 552.20 on a 500 px canvas), and the cropped deck loses its own room labels with it. |
| figures may be small | at the fitted zoom for the reported scene the figure is **36.0 × 21.6 px** — not small. The complaint is true only at the *legibility floor* (`0.85` → 12.75 × 7.65 px), which the fit only reaches for a building far bigger than the reported one. |

So the defect is **clipping at the viewport edge**, not text-on-text collision. It reads as
"overlap" because a clipped label's ascenders land on the plate stroke and the room slug of the
deck below, and because two decks' labels are the only repeated horizontal marks in the frame.

---

## 1. The arithmetic, and every constant's source

| constant | value | source |
|---|---|---|
| `TILE_W, TILE_H` | `34, 17` (2:1 iso) | `iso-engine.js:90` / `qubit-office.js:135` |
| `DECK_Z` | `26` px per `z` unit | `iso-engine.js:96` / `qubit-office.js:136` |
| `FLOOR_HEIGHT_UNITS` | `3` (so one floor is `z += 3`) | `voicebridge/office/tower.py:95,161` |
| FLOOR label font | `600 12px ui-sans-serif, system-ui, sans-serif` | `qubit-office.js:745` |
| FLOOR label anchor | `iso(maxX, maxY, z, cam)` then `+(4, 13)` | `qubit-office.js:743,747` |
| room label font | `10px ui-sans-serif, system-ui, sans-serif` | `qubit-office.js:694` |
| room label anchor | `iso(x0,y0).x + FACE_W*2`, `(iso(x0,y0).y + iso(x1,y1).y)/2 + 4` | `qubit-office.js:695–697` |
| `FACE_W` | `4` (so `FACE_W*2 = 8`) | `qubit-office.js:137` |
| `CHAR_FLOOR_ZOOM`, `MAX_ZOOM` | `0.85`, `2.4` | `qubit-office.js:139,142` |
| figure | `h = 15 * cam.zoom`, `w = 9 * cam.zoom` | `qubit-office.js:561` |
| canvas | `880×500` default, clamped to `[240, 1200]` wide | `qubit-office.js:1339,1070` |
| `extent()` / fit | `padX = w·0.94`, `padY = h·1.5`, clamp, then `max(z, 0.85)` | `qubit-office.js:457–468` |

### 1.1 The deck pitch, on screen

A floor's label is anchored at `iso(maxX, maxY, z, cam)` — the deck's **near corner** (its largest
`x + y`, which is the **bottom** vertex of the projected diamond, since screen y grows with
`x + y`). The projection is

```
sy = (x + y)·(TILE_H/2) − z·DECK_Z
screen_y = (sy − cam.cy)·zoom + cam.h/2
```

Two floors differ only in `z`, by `FLOOR_HEIGHT_UNITS = 3`, so the FLOOR-label anchors differ by
exactly

```
Δy = DECK_Z · FLOOR_HEIGHT_UNITS · zoom  =  26 · 3 · zoom  =  78·zoom   px
```

That is **the deck pitch in screen pixels**, and it is the *only* quantity that decides whether two
floor labels can collide. The glyph does **not** scale with zoom — `ctx.font` is a fixed `12px` —
so the glyph is a constant while the pitch is linear in zoom.

### 1.2 The collision condition — and why it is unreachable

Two 12 px glyph boxes overlap iff their baselines are **< 12 px** apart:

```
78·zoom  <  12        <=>        zoom  <  12/78  =  0.15385
```

The renderer clamps zoom at `CHAR_FLOOR_ZOOM = 0.85` **and applies that clamp last**
(`qubit-office.js:465–467`: `cam.zoom = max(z, CHAR_FLOOR_ZOOM)`), so no zoom below `0.85` can ever
reach the canvas. `0.85 > 0.15385`, therefore:

> **The three FLOOR labels cannot collide with each other at any zoom the renderer can produce.**
> The tightest reachable case is the clamp itself: pitch `78 · 0.85 = 66.3 px`, gap `66.3 − 12 =
> 54.3 px`.

This is the same shape of result as `24-FLOORPLAN-SCALE.md` §3's desk occlusion — a
zoom-independent quantity — but with the opposite sign (there the constant overhang was the defect;
here the constant glyph is *dominated* by a zoom-scaled pitch).

### 1.3 What the fitted zoom actually is for the reported scene

The reported scene is three floors of one 6×4 room (`executive` / `operations` / `intelligence`).
Its world extent is `170 × 241` units (one 6×4 diamond is `w = 6·34/… = 170`, and three decks stack
`3 · 26 = 78` apart each, giving the `241`). The fit then gives

```
zx = 880·0.94/170 = 4.866      zy = 500·1.5/241 = 3.112
zoom = min(2.4, max(0.85, min(zx, zy))) = 2.4        (the MAX_ZOOM ceiling)
```

At `zoom = 2.4` the pitch is `78 · 2.4 = 187.20 px` — the measured gap.

---

## 2. The measured layout of the reported scene (880×500, fitted zoom 2.4000)

```
FLOOR labels (baseline y, x, glyph box y-range):
  FLOOR 1 (executive)     y = 552.20  x = 484.80   box 541.20..553.20   OFF-CANVAS
  FLOOR 2 (operations)    y = 365.00  x = 484.80   box 354.00..366.00
  FLOOR 3 (intelligence)  y = 177.80  x = 484.80   box 166.80..178.80
  gap FLOOR 1 -> FLOOR 2 = 187.20 px  (one deck pitch)
  gap FLOOR 2 -> FLOOR 3 = 187.20 px  (one deck pitch)

room labels (baseline y, x, own plate y-range):
  room executive      floor 1  y = 441.20  plate 335.20..539.20  inside = True
  room operations     floor 2  y = 254.00  plate 148.00..352.00  inside = True
  room intelligence   floor 3  y =  66.80  plate −39.20..164.80  inside = True
```

Three facts fall out, each a number:

1. **The floor labels do not collide.** 552.20 / 365.00 / 177.80 are `187.20` apart; a 12 px glyph
   needs 12. There is no collision to fix here.
2. **`FLOOR 1`'s baseline is `552.20`, and the canvas is `500` px tall — 52.2 px below the last
   visible row.** The label is not merely clipped at its descender; the *entire glyph* is off the
   frame. That is the "the labels run into each other / something is buried" appearance.
3. **Every room label is inside its own plate**, `inside = True` for all three. The room-label
   placement is correct; the reported burial is the *floor* label of the deck below landing where
   the reader expects the *room* label to be — i.e. it is §2's fact 2 read as a room-label problem.

The same measurement on the 1200 px canvas gives identical `y` values (`zoom` is still clamped at
`2.4`); only the `x` values shift with the canvas centre. **The clipping is vertical and therefore
canvas-width-independent** — widening the canvas does not fix it.

### 2.1 Why the stack overflows: the camera is centred on the whole building

`fitToBuilding` (via `QubitCamera.fit`, a COVER fit) centres `cam.cx/cy` on the **whole
building's** extent and scales to fit it. For a stack, the extent's `h` is the *deck* height plus the
stack, so the top of the extent and the bottom of the extent are on-canvas, but the **canvas
aspect** (`880/500 = 1.76`) versus the extent's (`170/241 = 0.71`) means the WIDTH axis is the
binding constraint: `zx = 4.866 > zy = 3.112`, so the fit is width-limited and the height
overflows the viewport. The visible band is the middle `500 / (241 · 2.4) = 86%` of the building's
height, and the bottom 14% — which is where the lowest deck's near corner lives — is off-frame.

That is exactly the trade `24-FLOORPLAN-SCALE.md` §7 chose deliberately ("the building may overflow;
the people may not disappear"). The *labels* were not part of that trade, and they are the casualty.

**How many floors before the lowest label clips** (same 880×500 canvas, one 6×4 room per floor,
`FLOOR 1`'s label baseline from the real `fitToBuilding` + `extent`):

| floors | world extent `h` | fitted zoom | deck pitch px | FLOOR 1 baseline | verdict |
|---|---|---|---|---|---|
| 1 | 85.0 | 2.4000 | 187.20 | 365.00 | on-canvas |
| 2 | 163.0 | 2.4000 | 187.20 | 458.60 | on-canvas (42 px of air left) |
| **3** | **241.0** | **2.4000** | **187.20** | **552.20** | **CLIPPED — 52.2 px below the frame** |
| 7 | 553.0 | 1.3562 | 105.79 | 638.00 | **CLIPPED — 138 px below the frame** |

> **The lowest deck's FLOOR label goes off-canvas at exactly THREE floors** — which is the reported
> scene. One and two floors fit (the two-floor case has 42 px of air under its label); the third
> floor's `z` pushes the near corner past `cam.h`. From there the overflow grows: at seven floors the
> fit is height-limited (`zoom = 1.3562`, not the `2.4` ceiling) and the lowest label is 138 px out.

This is the finding that reframes the defect: **it is not "the decks are spaced too close together"
(the pitch is 187.20 px against a 12 px glyph) and it is not "the labels overlap" (they are 187.20 px
apart). It is "the deck label is anchored at the BOTTOM vertex of its diamond, so the moment a third
deck enters the stack the lowest one's label is anchored below the viewport floor."**

---

## 3. The minimum gap required, stated as the rule a fix must satisfy

The rule is not a pixel number; it is an inequality with a **placement** term:

```
    (placement separation between two floor labels, in px)
        ≥  glyph height (12 px)  +  a legibility margin
```

At the fitted zoom the placement separation is `78·zoom` and is already 15.6× the glyph, so **the
inequality is satisfied and the labels still read as a collision because one of them is off-canvas.**
The inequality that is actually violated is the *containment* one:

```
    0  ≤  label_baseline_y  ≤  cam.h          (and, better, ≤ cam.h − 4)
```

Measured: `552.20 > 500` for FLOOR 1. The fix must therefore be a placement rule that keeps labels
inside the viewport, **not** a pitch rule. §6 gives it.

---

## 4. Does the figure legibility complaint hold? (the arithmetic)

`drawAgent` draws `h = 15 * cam.zoom`, `w = 9 * cam.zoom` (`qubit-office.js:561`), so:

| zoom | figure | where that zoom comes from |
|---|---|---|
| **2.400** (`MAX_ZOOM`) | **36.0 × 21.6 px** | **the fitted zoom of the reported 3-floor scene** |
| 1.678 | 25.2 × 15.1 px | 21-tile floor grid (6 rooms wide), width-limited |
| 1.500 | 22.5 × 13.5 px | a wide single floor |
| 1.067 | 16.0 × 9.6 px | the "comfortable" threshold below |
| **0.850** (`CHAR_FLOOR_ZOOM`) | **12.75 × 7.65 px** | **the lowest the fit will ever go** |

- **At the fitted zoom the figures are not small.** 36 × 21.6 px is a readable figure with a
  readable state band. The complaint does not hold for the reported scene.
- **The complaint is true at the clamp.** At `0.85` the figure is 12.75 px tall × **7.65 px wide** —
  tall enough to spot a person, **too narrow to read a state band or a facing**. The `24-FLOORPLAN-SCALE.md`
  §2 note already says this: `CHAR_FLOOR_ZOOM` is a *spot-a-person* floor, not a *read-the-state* floor.
- **The zoom that would be needed**, if 12 px of glyph (the size the FLOOR label uses) is taken as
  the readable floor: `zoom ≥ 12/15 = 0.800`. For a comfortable 16 px figure: `zoom ≥ 16/15 = 1.0667`.
  Both are **at or below the fitted zoom** for every real scene in the table above except the
  `CHAR_FLOOR_ZOOM`-clamped ones, so "make the figure bigger" is not the fix — the fit already does.

**The honest conclusion:** the figure complaint is not a rendering defect at the fitted zoom, and a
user seeing it is either (a) at a very large floorplan where the clamp binds, in which case the
remedy is to *crop/pan* (which the camera already supports, `PAN_MARGIN = 0.30`) rather than to zoom
further out, or (b) misreading a 36 px figure whose *state band* is only `max(1, 1.6·zoom) = 3.8 px`
tall — the band, not the body, is the legibility limit. That is a separate finding and is recorded
as such, not folded into the label fix.

---

## 5. What is verified about the room labels (the other half of the report)

The room slug is drawn at `iso(x0,y0).x + FACE_W·2` and `(iso(x0,y0).y + iso(x1,y1).y)/2 + 4`
(`qubit-office.js:696–697`). Since `iso(x0,y0)` is the plate's top vertex and `iso(x1,y1)` its
bottom, the `y` anchor is the **mid-point of the plate in screen space**, which is inside the plate
by construction for any room size. Swept over the real floor grid (3 columns × 2 rows × 3 floors =
18 rooms, on both canvases = 36 placements) **every room label is inside its own plate** — the
containment test is green, with the numbers printed.

So the reported "`review` buried under `FLOOR 3`" is:

- **not** a room label leaving its room (measured false), and
- **is** the `FLOOR 1` label's off-canvas baseline landing over the lowest deck's room slug band,
  plus that deck's own bottom half being clipped at the frame.

One limitation is real and separate: the room slug is **horizontal text on an isometric deck**, so at
high zoom or in a narrow room it can overrun the plate's *horizontal* extent (`D11` in
`30-VISUAL-DEFECT-LEDGER.md` already records this). It is not the reported defect and is not fixed by
the rule below.

---

## 6. RECOMMENDED PLACEMENT RULE — every floor's label on its own deck edge, staggered by floor

The rule has to satisfy two conditions simultaneously:

```
    (a) CONTAINMENT   the label's glyph box is fully inside the viewport
    (b) SEPARATION    two floor labels' glyph boxes never overlap
```

The current rule satisfies (b) and violates (a). The rule below satisfies both, and does it without
touching the camera, the projection, the payload or the contract.

### 6.1 The rule, stated once

> **Anchor each floor's label on its deck's own near-edge, INSET from the near corner by the floor
> number, and clamp the label's baseline into the viewport.**

Concretely, in `drawTower`'s label item (`qubit-office.js:738–749`):

1. **Anchor on the deck edge, not the corner.** Replace `iso(maxX, maxY, z, cam)` with the deck's
   near edge midpoint — `iso((minX+maxX)/2, maxY, z, cam)` — so the label sits on the *front edge*
   of the plate rather than spilling past its bottom vertex.
2. **Stagger by floor number.** Add `+ fl.number * FLOOR_STRIDE` to the baseline, where
   `FLOOR_STRIDE` is a constant chosen so the stagger is smaller than a deck pitch but larger than
   the glyph:
   `FLOOR_STRIDE = 14 px` (12 px glyph + 2 px of air). Over `n` floors the stagger spans
   `(n−1)·14` px while the pitch is `78·zoom ≥ 66.3 px`, so **the stagger can never push one floor's
   label into the next floor's label** — it absorbs into the gap.
3. **Clamp into the viewport, last.** After computing the baseline, clamp:
   `baseline_y = min(max(baseline_y, FLOOR_GLYPH + 2), cam.h - 4)`. This is the condition the current
   code violates; it is one `Math.min/Math.max` and it cannot change a label that is already inside.

### 6.2 Why stagger + clamp and not "drop labels below a threshold zoom"

The alternative — **hide the FLOOR labels below some zoom and draw them in the HUD instead** — was
considered and rejected, on the arithmetic:

| option | satisfies (a)? | satisfies (b)? | cost |
|---|---|---|---|
| **stagger + clamp** (§6.1) | yes, by construction | yes: stagger `≤ (n−1)·14` < pitch `≥ 66.3` | 3 lines in `drawTower`; every floor still named on its deck |
| hide below a zoom threshold | yes, trivially (not drawn) | yes (not drawn) | the tower stops naming its own floors at exactly the zooms where the reader most needs the name; the HUD floor picker already lists `F1 · <role>`, so the information survives, but the *drawing* loses its labels |
| move the label to the HUD only, always | yes | yes | same as above, unconditionally — worst of both |

**The tradeoff, stated:** the stagger+clamp keeps the label attached to its floor (which is the whole
point of a deck label, and the fix `D11` was written to protect) at the cost of up to `(n−1)·14` px
of horizontal drift between floors. For the reported 3-floor scene that is 28 px on a 187.20 px pitch
— visually a diagonal staircase of labels down the tower, which reads as *depth*, not as error. The
HUD fallback loses that cue and gains nothing the clamp does not already give.

**Do not** "fix" this by reducing `DECK_Z` or raising `FLOOR_HEIGHT_UNITS`: §1.2 shows the pitch is
already 15.6× the glyph, and shrinking it makes the *decks* collide (a 26 px `DECK_Z` at zoom 2.4 is
62.4 px of deck separation, against a plate half-height of `17·2.4/2 = 20.4 px` and a 20 px
`FACE_H` cutaway — the floors are already tight; there is no slack to give).

### 6.3 The one-line version

> **The floor labels are correctly spaced and wrongly anchored: at `zoom 2.4` they are `187.20 px`
> apart against a `12 px` glyph (a `175.20 px` gap, so no collision), but `FLOOR 1`'s baseline is
> `552.20` on a `500 px` canvas — `52.2 px` off the frame. Inset the anchor to the deck edge,
> stagger each floor's label by `fl.number · 14 px`, and clamp the baseline into `[14, cam.h − 4]`.
> The figure is `36 × 21.6 px` at the fitted zoom (`15 · 2.4`), which is legible; the legibility
> floor `0.85` gives `12.75 × 7.65 px`, and a readable figure needs `zoom ≥ 0.8` — already met.**

---

## 7. What is NOT claimed here

- This document does **not** claim the fix is applied. The renderer is owned elsewhere; §6 is the
  rule for whoever lands it.
- It does **not** claim `review` is a room-label bug. §5 measures that room labels are inside their
  plates; the burial is the floor label's off-canvas baseline.
- It does **not** claim the labels collide. §1.2 proves they cannot, and §2 measures the gap.
- It does **not** re-open `D11`. The `D11` fix (anchor on the deck's own extent corner instead of a
  grid-row count) is what makes the labels *per-deck*; this document finds that the same anchor is
  the *bottom* vertex and therefore the one that falls off the frame. `D11`'s "limitation, still
  true" note about horizontal overrun on a narrow canvas is untouched and remains open.

---

## 8. The arithmetic, reproducible

Everything above is computed by one test file, which passes on the current worktree:

```bash
.venv/bin/python -m pytest tests/test_office_label_layout.py -q
```

Real output on the current worktree (2026-10-13):

```
13 passed in 0.19s
```

and with `-s`, the report the reader needs (880×500, the reported 3-floor scene):

```
canvas 880x500  fitted zoom 2.4000  extent {'cx': 17.0, 'cy': -113.5, 'w': 170.0, 'h': 241.0}
deck pitch on screen = DECK_Z * FLOOR_HEIGHT_UNITS * zoom = 26.0 * 3 * 2.4000 = 187.20 px
floor glyph = 12.0 px  ->  gap between two floors' glyphs = 175.20 px
figure = 15.0 * zoom = 36.00 px tall
FLOOR labels (baseline y, x, box y-range):
  FLOOR 1 (executive)      y = 552.20  x = 484.80  box 541.20..553.20   OFF-CANVAS
  FLOOR 2 (operations)     y = 365.00  x = 484.80  box 354.00..366.00
  FLOOR 3 (intelligence)   y = 177.80  x = 484.80  box 166.80..178.80
  gap FLOOR 1 -> FLOOR 2 = -187.20 px (baseline to baseline)
  gap FLOOR 2 -> FLOOR 3 = -187.20 px (baseline to baseline)
room labels (baseline y, x, own plate y-range):
  room executive     floor 1 y = 441.20 x = 407.20 plate 335.20..539.20  inside=True
  room operations    floor 2 y = 254.00 x = 407.20 plate 148.00..352.00  inside=True
  room intelligence  floor 3 y =  66.80 x = 407.20 plate −39.20..164.80  inside=True
[figure legibility] fitted zoom 2.4000 -> figure 36.00 x 21.60 px;
  clamp 0.85 -> figure 12.75 x 7.65 px; readable(12px) zoom=0.8000; comfortable(16px) zoom=1.0667
```

The test's two assertions of the *defect* (`test_the_lowest_floor_label_is_drawn_off_the_bottom_of_the_canvas`,
`test_the_top_most_deck_label_is_the_only_one_with_room_above_it`) are written to **fail** when the
§6.1 rule lands, so the fix must be re-measured rather than asserted. The collision assertion
(`test_floor_labels_cannot_collide_at_any_reachable_zoom`) is a sweep of the whole reachable zoom
range and pins the `0.1538` threshold against `CHAR_FLOOR_ZOOM`.
