# 34 — Is the walk actually visible? (C7's observable half)

C7 says a character moves **only** because an `office_event` row says so, never
from a client clock. `tower.walk_for` (`voicebridge/office/tower.py:263`) is the
honest half of that: it computes, for `now`, the single point on the desk→station
path that is *true* — `position.{x,y}` plus `progress ∈ [0,1)` while
`walking: true`. The renderer owns no path, no speed and no timer; it can only
draw the point it was handed.

What nobody had checked is whether that point can be **seen**. The bob is
`|sin(progress·π·4)| · 3 · zoom` world units against a 15-unit figure — a 20%
vertical displacement; `WALK_S` is 6 s at a real poll interval of `POLL_MS = 400`
ms, so a whole walk is ~15 polls; and the walk only starts when an event lands,
so a quiet org never walks at all. This document pays the claim with numbers, and
`tests/test_office_walk_visible.py` re-derives every one of them.

Test command (7 tests, all pass):

```sh
.venv/bin/python -m pytest tests/test_office_walk_visible.py -v -s
```

## The constants this rests on (read, not guessed)

| value | where | number |
|---|---|---|
| poll interval | `qubit-office.js` `POLL_MS` | **400 ms** |
| walk duration | `tower.py` `WALK_S` | **6.0 s** |
| bob amplitude | `qubit-office.js` `Math.abs(Math.sin(pr*Math.PI*4)) * 3 * zoom` | **3 units** |
| bob frequency | same expression, `pr·π·4` | 2 full bobs / walk |
| tile | `qubit-office.js` `TILE_W, TILE_H` | 34, 17 (2:1 iso) |
| floor gap | `qubit-office.js` `DECK_Z` | 26 |
| legibility floor | `camera.js` `CHAR_FLOOR_ZOOM` / `control-bar.js` `MIN_ZOOM` | **0.85** |
| loosest zoom bound | `qubit-office.js` `MIN_ZOOM` | 0.45 |
| fit ceiling | `camera.js` `MAX_ZOOM` | 2.4 |

The fit is `camera.js::fit` — a **cover** fit: `zoom = max(min(availW/ew,
availH/eh), minZoom)` with `margin = 0.08`, clamped to `MAX_ZOOM = 2.4` and then
floored at `CHAR_FLOOR_ZOOM = 0.85` (the renderer applies that floor twice, so it
is not negotiable). `ew`/`eh` are the projected extent of every room corner on
every floor (`qubit-office.js::extent` / `camera.js::worldExtent`).

## 1. Samples per walk at the real poll interval

```
samples per walk = WALK_S / (POLL_MS/1000) = 6.0 / 0.4 = 15
```

Fifteen polls land inside one walk. But `walk_for` rounds the interpolated point
to **integer grid cells** (`int(round(...))`), and the renderer draws that grid
cell projected — so the polls **dedupe**. For a `ledger_write` walk (desk → vault,
grid delta `(3,0)`) the real measurement is:

```
distinct positions the renderer sees = 4 of 15 polls
progress advances over               = 0.000 .. 0.933
```

The character **teleports across 4 cells in 6 seconds**, not a smooth glide. 15
polls, 4 distinct on-screen positions — the walk is a 3-step sprite hop, and the
eye sees the position change 3 times, not 15.

## 2. The bob in pixels at the fitted zoom

The fit produces the zoom from the org's real extent. Measured
(`tests/test_office_walk_visible.py::test_report_renderer_observable_walk` and
the reproduction script below), cover fit, viewport **1200×800**:

| org | extent (world) | fitted zoom | bob = 3·zoom |
|---|---|---|---|
| 1 floor, 1 room | 170 × 85 | **2.400** (ceiling) | **7.20 px** |
| 3 floors, 1 room/floor | 170 × 137 | **2.400** (ceiling) | **7.20 px** |
| 3 floors, full 3×2 grid | 493 × 298 | 2.045 | **6.13 px** |
| 1 floor, full 3×2 grid | 493 × 246 | 2.045 | **6.13 px** |

On a small 800×600 canvas the full-grid org drops to zoom 1.363 → **4.09 px**.
Stacking floors does *not* shrink the zoom much because the floors overlap in
x/y and only differ in `z` (`DECK_Z = 26`), so a tall tower is nearly as wide as
its widest floor.

**The bob is visible at every realistic org/viewport — 4.1 to 7.2 px** vertical,
2 full cycles per walk. It only reaches the 0.85 floor (bob = **2.55 px**) when
the building is so large the cover fit would need to zoom *below* the legibility
floor — and the fit refuses to go there (`zoom = max(fit.zoom, 0.85)`), cropping
architecture instead. At the loosest bound any renderer constant permits
(`MIN_ZOOM = 0.45`) the bob is **1.35 px** — above 1 px, but marginal.

Two honest caveats:

- **Frame 1 shows no bob.** At age 0 the character is already `walking: true`
  with `progress = 0.0`, and `|sin(0)| = 0`. The first of 15 frames bobs 0 px.
- **The horizontal travel dwarfs the bob.** The vault walk moves the figure
  **136.8 px** on screen at fit zoom (48.5 px at the 0.85 floor); the bob is a
  7.2 px ripple *on top of* a large position change. Even if the bob were
  invisible, the walk itself is a large, obvious translation.

## 3. Does a quiet org ever walk?

**No, and that is the C7-correct answer.** A walk requires a **fresh
`office_event` row of a station-mapped kind** (`tool_call`, `skill_used`,
`ledger_write`, `mcp_call`, `note_written`, `memory_written`, or `escalation`)
within `ACTIVE_WINDOW_S`. With no rows:

- `tower.walk_for` returns the desk with `walking: false`, `at: "desk"`, and no
  `progress` — forever, at any clock.
- `tests/test_office_walk_visible.py::test_no_event_means_nobody_walks...`
  advances the clock a full day and asserts every position is byte-identical.

So on a quiet org the walk is **unobservable because it does not happen** — not
a rendering fault, the contract working. The visibility question only has an
answer when events flow.

## Verdict

**The walk is visible.** The bob clears 4 px at every realistic fitted zoom, the
walk translates the figure by 40–137 px, and the motion is provably data — it
starts on an event and stops at `ts + WALK_S` with `progress` removed. C7's
visible claim holds.

The residual weaknesses, all real but not disqualifying:

1. **4 distinct positions from 15 polls.** Integer-cell rounding makes the walk a
   3-step hop. A smooth path would need sub-cell interpolation server-side (which
   `motion.js` exists to do, but `walk_for` ships only the raw cell).
2. **The bob is sub-pixel at `MIN_ZOOM = 0.45`?** No — 1.35 px, above 1 px, but
   only just. The test asserts `> 1 px` at both 0.85 and 0.45 and fails loudly if
   anyone shrinks the bob.
3. **Frame 1 is a 0 px bob.** Harmless, but it means a walk's first drawn frame
   is indistinguishable from standing still.

## Recommendation

**Accept the design; do not change the constants.** The walk is already visible —
raising the bob or lengthening `WALK_S` would spend motion budget to fix a
problem the numbers say does not exist, and `WALK_S = 6 s` at `POLL_MS = 400 ms`
is deliberately 15× the poll so the walk is *sampled*, not aliased.

Cost of accepting: the residual "4 positions, 3-step hop" remains. If that reads
as stutter to a viewer, the right fix is **not** the bob — it is to have
`walk_for` ship the un-rounded interpolated point (drop `int(round(...))` on the
mid-walk branch, keep the rounded endpoint), which makes the renderer see up to
15 distinct positions for the same 15 polls. That is a one-line change at
`tower.py:302-303` and is strictly more faithful to "the point that is true at
`now`" — but it touches the renderer's position contract, so it is a separate,
deliberate change, not part of this verification.

Second-order option, only if a future reviewer wants the bob to read at the
legibility floor: raise `BOB_UNITS` from 3 to 4 (bob at 0.85 floor goes 2.55 →
3.40 px). Cheap, but it is decoration tuning; the translation already carries the
walk.

## Reproducing the numbers

```sh
# the assertion tests + the printed report
.venv/bin/python -m pytest tests/test_office_walk_visible.py -v -s

# what the report prints (real output):
#   samples per walk        = 15.0
#   distinct positions seen = 4 of 15 polls
#   fitted zoom (camera.fit)= 2.400x   (3-floor, 1-room-per-floor org @1200x800)
#   bob pixels @fit zoom    = 7.20 px
#   bob pixels @floor 0.85  = 2.55 px
#   bob pixels @MIN_ZOOM .45= 1.35 px
#   whole-walk travel @fit  = 136.8 px
```
