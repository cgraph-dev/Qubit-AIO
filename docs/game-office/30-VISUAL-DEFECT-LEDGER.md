# 30 — Visual defect ledger

> **Status 2026-10-13 — a record, not a plan. Nothing in this file fixes anything.**
>
> This is the single list of every visual defect the Qubit Office project has found, each with the
> evidence that found it, its **current true status**, and the command or test that would prove it
> fixed. It exists because the same failure recurred: a defect declared fixed without evidence.
> Three of them (D-CHARFLOOR, D-PROGRESS, D-SEATFLOOR-PITCH) were reported as fixes and were not.

## The rule this file enforces

A defect may be marked **FIXED** only when a command in the *Evidence* column, run against the
current worktree, produces the *Proof* result. A prose claim, a design document, and a merged module
are none of those things. Where the proof has not been run or does not exist, the status says so.

**A second rule learned the hard way here.** The plugin ships **five source modules**
(`iso-engine.js`, `sprites.js`, `camera.js`, `control-bar.js`, `motion.js`) that are individually
built and individually tested — and the live renderer `qubit-office.js` **calls none of them.**
Verified:

```
$ grep -n 'QubitIso\|QubitCamera\|QubitSprites\|QubitControlBar\|QubitOfficeMotion' \
      frontend/qubit-app/plugins/qubit-office/qubit-office.js
NONE-FOUND
```

So a defect can be **FIXED in a module and still OPEN on the page.** This ledger records the status
of the *page*, and names the module status separately where the two differ. A fix that lands in
`camera.js` but not in the renderer's own copy of the same function has fixed nothing the owner can
see.

## How defects were found (the four discovery modes)

| mode | what it is | how it is reproducible |
|---|---|---|
| **rendered page** | loading the real page in headless Chrome and counting pixels | `tests/test_office_render_smoke.py` (`-m dashboard`) |
| **source read** | reading `qubit-office.js` / `tower.py` and seeing the absence | `grep` / `read_file` at the cited line |
| **arithmetic** | computing what a geometry *must* do from the constants | the scripts in `24-FLOORPLAN-SCALE.md` §10 and below |
| **agent report** | a delegated agent's claim, which may be wrong in either direction | the claim is checked, never trusted |

## Worktree facts that bound every status below

* The whole Office tree is **untracked** — `git ls-files frontend/qubit-app/plugins/qubit-office/ docs/game-office/ voicebridge/office/tower.py` prints nothing, and `git log -- <those paths>` is empty. There is **no commit history** to consult; the record is the worktree.
* The live renderer is `qubit-office.js` (956 lines). It carries its **own** `iso()`, `clampPan()`,
  `extent()`, `fitToBuilding()`, `drawAgent()`, `drawShadow()`, `drawMarker()`, `drawMinimap()`,
  `drawPnl()` — duplicates of what the modules provide.
* The served bundle is `qubit-office.bundle.js`, rebuilt by `scripts/build_office_bundle.py`.
* Test ground truth, run at 2026-10-13:
  ```
  $ .venv/bin/python -m pytest tests/test_office_tower_visual.py \
        tests/test_office_tower_plugin.py tests/test_office_bundle.py -q
  2 failed, 74 passed
  $ .venv/bin/python -m pytest tests/test_office_iso_engine.py tests/test_office_camera.py \
        tests/test_office_motion.py tests/test_office_sprites.py tests/test_office_control_bar.py -q
  145 passed
  ```
  The 2 failures are the seat-colour collapse (D2), by design — see `tests/test_office_suite_health.py`
  ("2 *intentionally* red assertions … that document an unfixed `seatColour` collision").

---

## The ledger

Legend for **Status**: FIXED · OPEN · CLAIMED-FIXED-BUT-OPEN · NOT-A-DEFECT.
"Module status" is given where a module and the live renderer disagree.

---

### D1 — No depth sort: the renderer paints by loop nesting, not by screen depth

**What is wrong.** Characters and desks are drawn in fixed passes (all decks, then all characters),
so whether a figure covers a desk depends on object *kind*, not on where it stands — a figure behind
a desk paints over the desk in front of it.

**Found by.** source read (doc `26-OCCLUSION-AND-DEPTH.md` §1) + source read of current code.

**Evidence.**
```
$ grep -n '\.sort(\|depthKey\|paintOrder\|sortByDepth\|zY' \
      frontend/qubit-app/plugins/qubit-office/qubit-office.js
NO-SORT-IN-RENDERER
$ awk 'NR>2600' frontend/qubit-app/plugins/qubit-office/qubit-office.bundle.js \
      | grep -n '\.sort(\|depthKey\|paintOrder\|zY'
NO-SORT-IN-BUNDLE-RENDERER-SECTION
```
Pass order is hard-wired at `qubit-office.js:406` (`shown.slice().reverse().forEach`) and
`qubit-office.js:480` (`(v.characters || []).forEach`) — the comment at `:479` still reads
*"the characters, on top of every deck"*. `26-OCCLUSION-AND-DEPTH.md` §4 specifies the fix
(`paintOrder()` / `depthKey()`), and `iso-engine.js:260-282` **implements** a tested `depthKey`/
`sortByDepth`. The renderer calls neither.

**Current status: OPEN.** Module status: the sorting primitive exists and is tested
(`test_office_iso_engine.py`, 20 tests) but is **not wired into the live renderer.**

**What proves it fixed.** `drawTower` builds one `items` list (decks + desks + characters) and paints
it in increasing depth key; a test that draws a desk at `(1,1)` and a character at `(0,0)` and
asserts the character's body fill occurs **before** the desk fill. Today
`test_characters_are_painted_after_every_deck` asserts the *opposite* (see D12).

---

### D2 — `seatColour` collapse: different slugs render as the same colour

**What is wrong.** Two distinct worker slugs get an identical colour, so a room of people draws as a
blob instead of as distinguishable figures. The hue is `Math.abs(hash) % PALETTE.length` over a
10-entry palette, then `tint(base, TYPE_HUE[seatType], 0.55)` drags same-type slugs onto one hue,
where they collide.

**Found by.** source read (the test names the defect) + rendered-page-adjacent probe (node run of the
real function).

**Evidence.** Two tests fail on the current worktree:
```
FAILED tests/test_office_tower_visual.py::test_two_different_slugs_get_two_different_colours
FAILED tests/test_office_tower_visual.py::test_a_room_of_distinct_slugs_gets_mostly_distinct_colours
E  AssertionError: 12 distinct slugs produced only 6 distinct colours:
   ['#4fda9d', '#54a4ec', '#54ec74', '#69d1d9', '#b28ae5', '#d654f6']  (assert 6 >= 10)
```
Live probe of the shipped function:
```
$ node -e '…seatColour("inbox-sweeper","worker") = #54ec74
           seatColour("ledger-auditor","worker") = #54ec74'
```
Source: `qubit-office.js:170-178`.

**Current status: OPEN.** Explicitly documented as unfixed in `tests/test_office_suite_health.py`
("the 2 *intentionally* red assertions").

**What proves it fixed.** `test_two_different_slugs_get_two_different_colours` and
`test_a_room_of_distinct_slugs_gets_mostly_distinct_colours` both pass (≥10 distinct colours for the
12-slug room).

---

### D3 — The walk bob was keyed on `position.progress`, which `state()` computed and then dropped

**What is wrong.** The renderer's walk bob is driven only by `position.progress` (C7). `walk_for()`
computed `progress` on its mid-walk branch, but `state()` copied only `x`/`y` into `position`, so
`progress` never reached the payload — the bob drew nothing for hours while every text test passed.

**Found by.** source read + live-populated-store probe (doc `28-HUD-PANELS.md` N11: "`walk_for`
returned `progress: 0.25` while `characters[0].position` had no `progress` key").

**Evidence.** The field is now carried through:
```
voicebridge/office/tower.py:612  if walk.get("walking") and walk.get("progress") is not None:
voicebridge/office/tower.py:613      position["progress"] = walk["progress"]
```
`walk_for` still produces it (`tower.py:304-305`, `"progress": round(t, 3)`), and
`test_office_tower_visual.py::test_the_walk_bob_only_exists_where_the_payload_reports_a_walk`
exercises the renderer's consumer side and passes.

**Current status: FIXED.** Evidence: `tower.py:612-613` + the passing walk-bob test.

**What proves it fixed (and keeps it fixed).** The *drop site* is `state()` (tower.py:612-613), and
the existing backend test covers only the *producer*: `test_office_tower_walk.py:112
test_mid_walk_reports_progress_between_zero_and_one` asserts `walk_for(...)["progress"]` is in (0,1)
— it calls `walk_for` directly (line 114), not `state()`. So the very regression this defect was
(nothing asserting that `state()` carries the field through) is **still unguarded**: a future edit
that removes `tower.py:612-613` passes the whole suite. The proof to add is a payload-level test that
calls `state()` for a live mid-walk character and asserts `position.progress` is present, and that a
stopped character's `position` has no `progress` (the "carry only while walking" guard at `:612`).

---

### D4 — `iso()` is duplicated with no shared source of truth

**What is wrong.** The projection is defined twice: `qubit-office.js:254` `iso()` and
`iso-engine.js:146` `gridToScreen()` (plus the same constants `TILE_W=34, TILE_H=17, DECK_Z=26` in
both). The live renderer uses its own copy, so a fix to the module cannot change the page — and the
two can drift (they already do on the camera/extent behaviour, see D6).

**Found by.** source read + `grep` for the module globals in the renderer (the cross-check in the
intro box).

**Evidence.**
```
$ grep -n 'function iso' frontend/qubit-app/plugins/qubit-office/qubit-office.js       → 254
$ grep -n 'function gridToScreen' frontend/qubit-app/plugins/qubit-office/iso-engine.js → 146
$ grep -n 'QubitIso' frontend/qubit-app/plugins/qubit-office/qubit-office.js           → NONE
```

**Current status: OPEN.** The renderer's `iso()` is the one that runs; `QubitIso.gridToScreen` is
dead as far as the page is concerned.

**What proves it fixed.** `qubit-office.js` contains no local `iso()`/`extent()`/`drawAgent()` and
instead calls `window.QubitIso.*` / `window.QubitCamera.*`; a test asserts the renderer references
each module global it depends on (today that assertion would fail for every one of the five).

---

### D5 — `CHAR_FLOOR_ZOOM = 0.85` was added and reported as a fix; it does not bind for any real org

**What is wrong (as reported).** The legibility zoom floor was added so characters never shrink to
specks; it was reported as the fix for unreadable figures.

**What the arithmetic shows.** For the org sizes that exist, the fit lands at `MAX_ZOOM = 2.4`,
never near 0.85 — because a 6×4 / 12×7 room block is small relative to the canvas, so the raw fit is
2.3–3.0. `CHAR_FLOOR_ZOOM` can only raise the zoom when the raw fit is *below* 0.85, which requires
either a canvas narrower than ~520 px with ≤2 rooms, or the 7-floor overflow case that this org
never reaches.

**Found by.** arithmetic (the fit formula) + source read.

**Evidence.** Reproduced from the real constants (`tower.py:98-99`, `ROOM_W=6, ROOM_H=4`):
```
rooms | 880x500 raw fit | 1200x500 raw fit | binds CHAR_FLOOR_ZOOM?
  1   |      2.400      |      2.400       | False
  2   |      2.400      |      2.400       | False
  3   |      2.400      |      2.400       | False
  6   |      2.317      |      2.400       | False
```
("raw fit" = `min(MAX_ZOOM, min(canvas.w*0.94/e.w, canvas.h*1.5/e.h))`, exactly
`fitToBuilding` at `qubit-office.js:308-316`.) The floor binds only for canvas widths and room
counts the product does not use. The value is also **duplicated and inconsistent**:
`qubit-office.js:97` = 0.85, `camera.js:53` = 0.85, `control-bar.js:81` sets `MIN_ZOOM = 0.85`
(whereas the renderer's `MIN_ZOOM` at `:94` is 0.45) — three copies, one of which disagrees.

**Current status: CLAIMED-FIXED-BUT-OPEN.** It was reported as a fix; it is inert for every real
org, and it is dead code with three divergent definitions.

**How that was detected.** By computing the fit from the actual constants rather than trusting the
comment at `:95-96`. The comment claims the floor is what keeps figures ≥ 13 px; the arithmetic
shows the fit is at 2.4, i.e. 36 px figures, so the floor never engaged. A fix whose branch is never
taken has no observable effect and cannot be called a fix.

**What proves it fixed.** Either (a) remove the constant and state that the fit is the only control
(this is `camera.js`'s "cover fit" direction), or (b) a test that constructs a canvas/floor payload
where the raw fit is genuinely below 0.85 and asserts the final zoom is clamped to exactly 0.85, plus
a single-source-of-truth assertion that `MIN_ZOOM`/`CHAR_FLOOR_ZOOM` are defined once.

---

### D6 — `extent()` seeds its bounds at 0 and ignores `z`

**What is wrong.** `qubit-office.js:276-293` (`extent`) seeds `minX=maxX=minY=maxY=0` and folds every
room corner with `Math.max`/`Math.min`, so whenever the true edge does not cross 0 the box is dragged
to the origin — the "mostly-empty canvas with the building jammed in a corner" defect. It also ignores
`z`, so the vertical stack of floors is not measured. `camera.js:110-141` (`worldExtent`) **fixes
both** (±Infinity seed, corner projection, null rejection) — and is never called.

**Found by.** source read + arithmetic (a room at `(0,12)` projects to x = −204, which the 0-seed
cannot represent) + the fix is quoted in `camera.js`'s own comment block (`:99-103`).

**Evidence.**
```
qubit-office.js:277  var minX = 0, maxX = 0, minY = 0, maxY = 0, seen = false;
camera.js:115        var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
$ grep -n 'QubitCamera' frontend/qubit-app/plugins/qubit-office/qubit-office.js  → NONE
```
`test_fit_to_building_centres_the_camera_on_the_actual_extent` passes against the *current* 0-seed
`extent` only because its test rooms sit at `(20,20)`/`(24,22)`, where the max corner happens to be
the true edge.

**Current status: OPEN.** Module status: fixed in `camera.js::worldExtent` and tested
(`test_office_camera.py`, 22 tests) but **not wired into the renderer.**

**What proves it fixed.** A test that fits a floor whose rooms are all in negative-projection
quadrants (e.g. a room spanning `(0, 12)`) and asserts `cam.cx/cy` equal the true extent centre, not
a value pulled toward 0. Or: the renderer calls `QubitCamera.worldExtent`.

---

### D7 — The desk pitch occludes the row behind (the "map is too small" misdiagnosis)

**What is wrong.** At the current 2-tile row pitch a 15-unit figure overhangs the row behind by 6.5
units, and the marker stack reaches 32 units above the feet — so the back row's desk plate and state
band are hidden. The room stays `6×4`; the stride stays `(2,2)`.

**Found by.** arithmetic (`24-FLOORPLAN-SCALE.md` §3–§4).

**Evidence.**
```
voicebridge/office/tower.py:99   ROOM_W, ROOM_H = 6, 4
voicebridge/office/tower.py:100  SEAT_SLOTS_PER_ROOM = 6
voicebridge/office/tower.py:178  return col * (ROOM_W + 1) + 1 + (n % 3) * 2
voicebridge/office/tower.py:189  return row * (ROOM_H + 1) + 1 + (n // 3) * 2
```
Overhang is scale-free (`15 > 8.5`), so no zoom fixes it: `24-FLOORPLAN-SCALE.md` §3 gives a constant
6.5-unit overhang at pitch 2. `24-FLOORPLAN-SCALE.md` §7 recommends `12×7`, stride `(4,3)`, odd-row
stagger — **not applied.**

The *stated* diagnosis ("the map is too small for the org") is **false**: 11 seats need 2 rooms
(11/6 → ceil 2), and the 71-seat documented org needs 12 rooms = 2 floors — the building is bigger
than the org. That part is a **NOT-A-DEFECT** (wrong diagnosis); the occlusion itself is real.

**Current status: OPEN** (constraint unchanged; the recommended fix is unlanded) —
and the *reason given* was **CLAIMED-FIXED-BUT-OPEN**: the brief's "map is too small" was asserted as
the cause and then disproved by arithmetic (11 seats → 2 rooms), so it was never the cause.

**What proves it fixed.** `ROOM_W, ROOM_H = 12, 7` and strides `(4,3)` + 1-tile odd-row stagger in
`tower.py`, plus a test asserting the row offset (25.5 units) exceeds the figure height (15), and
that every `(x+1, y−1)` diagonal is absent from the desk set (no depth-sort tie).

---

### D8 — Flat-plate geometry: only two cutaway faces are drawn

**What is wrong.** A deck is drawn as a plate plus two wall faces; a desk is an 8×5 `fillRect`. The
"solid objects" that make a block read as a box (top + left + right) are not drawn, so desks and
partitions read as coloured quads.

**Found by.** source read (`29-REPO-AUDIT.md` §5 row 5) + source read of current code.

**Evidence.**
```
qubit-office.js:426  /* two cutaway wall faces only, so the interior stays visible. */
qubit-office.js:450  (room.seats || []).forEach(function (slot) { … ctx.fillRect(p.x - 4*z, …) })
```
`iso-engine.js:320-343` `drawBlock()` implements the three-face cuboid; `test_office_iso_engine.py`
asserts `drawBlock` emits three faces and `drawTile` one. The renderer does not call it.

**Current status: OPEN.** Module status: fixed in `iso-engine.js::drawBlock`, tested, not wired in.

**What proves it fixed.** The renderer draws desks/decks via `QubitIso.drawBlock`, and a pixel test
counts three fills (top + two walls) per desk with distinct tone factors.

---

### D9 — Minimap vestigiality: origin pinned to the corner, no offset

**What is wrong.** `drawMinimap` scales by `sx = w/cols`, `sy = h/rows` with **no offset**, and
derives from `grid.cols/rows` (the world extent) rather than the actual room box. A world that does
not sit at `(0,0)` therefore lands off-rect; the whole floor collapses into the top-left of the map.

**Found by.** source read (`camera.js:30-39` names the defect) + source read of current code.

**Evidence.**
```
qubit-office.js:530  var sx = w / cols, sy = h / rows;      // no offset
qubit-office.js:536  ctx.fillRect(Number(room.x) * sx, Number(room.y) * sy, …)
```
`camera.js:274-293` `minimapTransform` implements the world-box → rect-box form with an explicit
offset and an inverse; `test_office_camera.py` covers it. Not called.

**Current status: OPEN.** Module status: fixed in `camera.js::minimapTransform`, tested, not wired in.

**What proves it fixed.** `drawMinimap` uses `QubitCamera.minimapTransform`, and a test fuzzes
`toMini(toWorld(p)) == p` over a grid plus a case where rooms are offset from the origin and asserts
the drawn rects are centred, not corner-pinned.

---

### D10 — The `$0.01.4286` money bug

**What is wrong.** A sub-cent per-day rate (0.14286 c/day) was formatted through the integer-cents
formatter, producing two decimal points (`$0.00.4286/day`).

**Found by.** source read + probe (documented by the test's own docstring).

**Evidence.** Live probe of the current function:
```
$ node … money(0.14286)   = $0.00
$ node … moneyRate(0.14286) = $0.00
$ node … money(142.86)    = $1.43
```
`money()` now rounds to whole cents (`qubit-office.js:200-213`), and `moneyRate()` is a separate
sub-cent path (`:217-229`). Both guarded by
`test_money_always_renders_one_decimal_point` and `test_money_rate_renders_one_decimal_point`.

**Current status: FIXED.** Evidence: the probe above + 8 passing parametrised money assertions.

**What proves it fixed.** The money tests pass and `money(0.14286)` has exactly one `.`.

---

### D11 — The floor label floated away from its floor

**What is wrong (reported).** The deck label was positioned from the grid's row count / an offset
outside the extent, so it drew in empty space or off-canvas.

**Found by.** source read + the fix's own comment.

**Evidence.** The label is now drawn at the deck's own near corner inside the footprint:
```
qubit-office.js:472  var lp = iso(maxX, maxY, z, cam);
qubit-office.js:475-476  ctx.fillText("FLOOR " + fl.number + " · " + fl.role, lp.x + 4, lp.y + 13)
```
with the comment at `:463-466` recording the two earlier wrong placements. A pixel test asserts the
`FLOOR n · role` labels are emitted (`test_floors_are_painted_in_reversed_payload_order`).

**Current status: FIXED** for the float itself (the label is anchored to the deck's own extent corner).
**Limitation, still true:** the label is horizontal text on an isometric deck, so at
`MAX_ZOOM`/narrow canvases it can overrun the plate edge — not the reported defect, but not proven
clean either.

**What proves it fixed.** A pixel test that asserts the label's `(x, y)` lies inside the projected
deck polygon for a range of room origins (today only the *text content and order* are asserted, not
the position).

---

### D12 — The two visual tests that encoded the old draw order

**What is wrong (as an issue).** `test_floors_are_painted_in_reversed_payload_order` and
`test_characters_are_painted_after_every_deck` assert the *current* (wrong) paint discipline — array
order reversed, characters always after every deck.

**Is this a defect?** **NOT-A-DEFECT as tests; a STALE GUARD against the intended fix.** They were
written to pin behaviour that *was* the design (`26-OCCLUSION-AND-DEPTH.md` §1 documents the
"characters on top of every deck" comment as a stated intent). They are correct characterisations of
today's code.

**Evidence.** `test_office_tower_visual.py:357-401` asserts the payload's last floor draws first and
that z does *not* drive it; `:404-437` asserts the first character body comes after the last deck
plate.

**Current status: NOT-A-DEFECT** (they test what the code does today).
**Consequence, recorded honestly:** when D1 is fixed, **both tests must be rewritten**, not deleted —
`test_characters_are_painted_after_every_deck` asserts the exact property D1 removes. A fix that
leaves these two green has not fixed D1; a fix that deletes them has removed the regression guard.

**What proves the D1 fix correct.** These two tests inverted to assert depth order
(a near figure paints over a far desk; a far figure paints under a near desk), with the same
pixel-level method.

---

### D13 — The probe-stripper bug that deleted the strings it was checking

**What is wrong.** The comment-stripper in `test_office_bundle.py` first dropped quotes **and their
contents**, so the string literals the write-verb assertions look for (`"POST"`, `"PUT"`, …) were
erased before the assertion ran — the assertions passed partly by deleting their own evidence.

**Found by.** source read (the fix carries its own note).

**Evidence.**
```
tests/test_office_bundle.py:67-88  — the stripper now KEEPS a string, quotes and all, and skips it as a unit.
tests/test_office_bundle.py:70-72  — the recorded cause: "The first version … dropped the quotes AND
                                     the contents, which silently deleted the very string literals the
                                     write-verb assertions look for."
```
`test_no_bundled_module_owns_a_timer_or_a_write_verb` now checks `f'"{verb}"' in code` against a
stripper that preserves strings.

**Current status: FIXED.** Evidence: the stripper at `:67-88` preserves strings; the write-verb
assertions at `:244-246` now have real evidence to check.

**What proves it fixed.** A negative control: inject a `"POST"` into a copy of a module and assert
the stripper-preserving check fails. (The builder-refusal test at `:209-233` is the nearest thing and
is a good model.)

---

### D14 — A subagent reported `completed` having changed nothing

**What is wrong.** A delegated agent returned a success/"completed" status without any change to the
worktree — a completion claim with no artifact behind it.

**Found by.** agent report, checked against the worktree.

**Evidence.** The verification method, not a single number: `git status` / `git diff --stat` over the
files the agent claimed to touch shows no diff. The pattern is recorded here because it is the same
failure as D5 and D7's misdiagnosis: **a claim is not artifact.** (No commit exists to cite — the
Office tree is untracked, so a subagent's "changed X" must be checked by reading X, not by trusting
the status string.)

**Current status: CLAIMED-FIXED-BUT-OPEN** — the "fix" has no artifact; the underlying defect it was
sent to fix is unchanged.

**How that was detected.** By reading the files the agent said it edited and finding them byte-identical
to their prior state — the same method this ledger uses everywhere: run the command, read the file,
do not trust the summary.

**What proves it fixed.** Any future "completed" claim is accompanied by a diff or a passing test
that fails without the change. A subagent's status string is not evidence.

---

## Count by status

Each defect is counted **once**, under its dominant status (D7 is counted OPEN; its false "map is too
small" cause is named inside its entry and in the CLAIMED list below, not counted twice):

| Status | Count | Defects |
|---|---|---|
| **FIXED** | **4** | D3 (progress carried by `state()`), D10 (money), D11 (floor-label float), D13 (probe-stripper) |
| **OPEN** | **7** | D1 (depth sort), D2 (seatColour), D4 (duplicate `iso()`), D6 (`extent()` zero-seed/z), D7 (desk-pitch occlusion), D8 (flat-plate), D9 (minimap) |
| **CLAIMED-FIXED-BUT-OPEN** | **3** | D5 (`CHAR_FLOOR_ZOOM`), D7-cause ("map too small"), D14 (subagent "completed") |
| **NOT-A-DEFECT** | **1** | D12 (the two old-order tests are correct characterisations of today's code) |
| **TOTAL distinct defects** | **15** | |

Observed defect density: of 15 recorded defects, **7 are still open on the live page**, and **5 of
those 7 (D1, D4, D6, D8, D9) are already "fixed" inside a module the renderer does not call.** The
module/page gap is the single largest source of false "fixed" status in this project.

## The three defects that were CLAIMED-FIXED-BUT-OPEN

1. **D5 — `CHAR_FLOOR_ZOOM = 0.85`.** Added and reported as the legibility fix. Arithmetic on the
   real constants shows the fit lands at 2.4 for every real org, so the clamp branch is never taken:
   the constant is inert. **Detected by** computing `fitToBuilding`'s raw fit from the actual room
   constants instead of trusting the comment that claims the floor is doing work.

2. **D7 (its cause) — "the map is too small for the org."** Asserted as the diagnosis and acted on;
   disproved by arithmetic — 11 seats → 2 rooms, the 71-seat org → 12 rooms → 2 floors, i.e. the
   building is larger than the org. **Detected by** the capacity table in `24-FLOORPLAN-SCALE.md` §5.
   The real cause (2-tile pitch vs a 15-unit figure) is a different defect and is still **OPEN**.

3. **D14 — a subagent reported `completed` having changed nothing.** **Detected by** reading the files
   it claimed to edit and finding them unchanged; its status string was the only artifact and it was
   false.

*(D3 belongs to the same family — the walk bob was reported as implemented but drew nothing because
`progress` was dropped — but it has since been genuinely fixed at `tower.py:612-613`, so it is
recorded as FIXED, not CLAIMED-FIXED-BUT-OPEN.)*

## Standing rule for this ledger

1. **FIXED requires a command.** A module that is "fixed" but not called by the renderer is
   **OPEN on the page** (D1, D4, D6, D8, D9 are all in this state).
2. **A branch that is never taken is not a fix** (D5).
3. **A diagnosis disproved by arithmetic is not a defect — but acting on it is** (D7).
4. **A completion claim is not an artifact** (D14); the artifact is a diff or a test that fails
   without the change.
5. **A test that encodes the old behaviour must be inverted, not deleted, when the behaviour changes**
   (D12).
