# 33 — The room programme: how many rooms, of what kind, seat what headcount

**Status: design verdict + module.** The programme is computed in
`voicebridge/office/room_layout.py` and proved in
`tests/test_office_room_layout.py`. This document states the programme for
11 / 25 / 50 / 71 seats with the arithmetic, shows how the *current* four rooms
differ and why they leave `review` empty, and records the tradeoff chosen
(fewer big rooms vs more small rooms) with a recommendation.

`24-FLOORPLAN-SCALE.md` fixed the **geometry** layer — room size and desk pitch.
This file is the layer **above** it: given a headcount and a department count,
how many rooms of what kind should the building contain, and who sits in each?
Geometry says a 12×7 room holds 9 desks at pitch (4,3); it does not say whether
the office wants one 9-desk room or three 3-desk rooms. That is this document.

---

## 1. The org rules the programme is built from (each cited)

Every claim below is enforced by a line of code, not by prose — a programme
justified by adjectives is the failure mode this whole exercise exists to avoid.

| rule | statement | enforced at |
|---|---|---|
| one supervisor per seat | every seat except the CEO names a superior; the chain terminates at the CEO | `voicebridge/office/org.py:399` (refusal), `org.py:80` `_check_chain_terminates`, C30 (`docs/game-office/95-CONTRACT.md:633`) |
| chain is acyclic | a seat cannot be its own ancestor | `voicebridge/office/org.py:57` `_walk_up` |
| span limit 7 | a supervisor carries at most 7 direct reports; past that the unit splits | `voicebridge/office/org.py:94` `_check_span`; `store.py:40` `MAX_SPAN = 7`; D34 (`10-DECISIONS.md`) |
| one CEO | exactly one seat of type `ceo` in the org | `voicebridge/office/org.py:390`; D2 |
| one owning director per department | a department's `director_id` must be a `director_portfolio` | `voicebridge/office/org.py:227`; D35 |
| boardroom | owner-facing decisions: "CEO + the proposing manager + (remote) the owner" | `docs/game-office/57-BOARDROOM.md:18` |
| review room | "internal decisions: a manager ruling on their own team's proposals, no owner needed"; a department "may have any number" | `docs/game-office/57-BOARDROOM.md:19–21` |
| `work` is the default kind | an ordinary team room | `voicebridge/office/org.py:253` `room_create(..., kind="work")` |
| a level cannot rate itself | the reviewer is exactly one level above the reviewed seat | `docs/game-office/95-CONTRACT.md:657` (C31, section starts at :653) |

From these, three facts the programme is **derived from** rather than choosing:

1. **"leaf" is a graph property, not a label.** A seat is a leaf when no live
   seat names it as `supervisor_id` (`org.py:399` forces that field for every
   non-CEO seat). A supervisor is a seat that some other seat reports to.
2. **A unit past 7 leaves is two rooms, not one big room** — `org.py:94`. So the
   number of *work rooms* is the org's own manager count, not a design taste.
3. **The executive chain sits in the boardroom, not in a team room** —
   `57-BOARDROOM.md:18`. So a work room holds *one manager plus its leaves*; the
   manager's own desk is in the room it runs, and it is never counted as a leaf.

---

## 2. The programme for 11 / 25 / 50 / 71 seats

`room_layout.staffing(headcount, dept_count)` solves the split **exactly**:
the largest leaf count `L` for which

```
1 (CEO) + directors + ceil(L / 7) + L  ==  headcount
```

where `directors = dept_count` when `headcount >= 1 + 2·dept_count` (the
directorate is affordable) and `0` otherwise — small orgs run their managers
straight off the CEO, which is what the live DB does (all three live departments
have `director_id` NULL). Solving rather than estimating is not fussiness: an
estimate that forgets the manager desks produced a 71-seat building seating 68
people. The test `test_staffing_is_exact` pins `1 + directors + managers + leaves
== headcount` for every documented size.

### 11 seats / 3 departments — the case on screen

| room | kind | desks | who |
|---|---|---|---|
| `boardroom` | boardroom | 4 | CEO + 3 portfolio directors |
| `desk-a` | work | 7 | 1 manager + 6 leaves |
| `review` | review | 0 | nobody permanent — a walking destination |

`staffing(11,3) = {ceo: 1, directors: 3, managers: 1, leaves: 6}` → **3 rooms**,
one floor, inside the 3×2 grid. Seats offered: 4 + 7 + 0 = **11**, shortfall 0.

### 25 seats / 5 departments

`{ceo: 1, directors: 5, managers: 3, leaves: 16}` → **5 rooms, 1 floor**:

| room | kind | desks |
|---|---|---|
| `boardroom` | boardroom | 6 (CEO + 5 directors) |
| `desk-a` | work | 7 (1 mgr + 6) |
| `desk-b` | work | 6 (1 mgr + 5) |
| `desk-c` | work | 6 (1 mgr + 5) |
| `review` | review | 0 |

Offered 6+7+6+6 = **25**, shortfall 0, `fits_grid` true.

### 50 seats / 9 departments

`{ceo: 1, directors: 9, managers: 5, leaves: 35}` → **7 rooms, 2 floors**:

| room | kind | desks |
|---|---|---|
| `boardroom` | boardroom | 10 (CEO + 9 directors) |
| `desk-a…desk-e` | work ×5 | 8 each (1 mgr + 7) |
| `review` | review | 0 |

Offered 10 + 40 = **50**, shortfall 0. **7 rooms > 6**, so
`fits_grid = false`, `floors_needed = ceil(7/6) = 2`.

### 71 seats / 9 departments — the documented full corp

`{ceo: 1, directors: 9, managers: 8, leaves: 53}` → **10 rooms, 2 floors**:

| room | kind | desks |
|---|---|---|
| `boardroom` | boardroom | 10 |
| `desk-a…desk-e` | work ×5 | 8 each (1 mgr + 7 leaves) |
| `desk-f…desk-h` | work ×3 | 7 each (1 mgr + 6 leaves) |
| `review` | review | 0 |

Offered 10 + (5·8 + 3·7) = 10 + 61 = **71**, shortfall 0.
`floors_needed = ceil(10/6) = 2`.

### The floors arithmetic, stated

`floors = ceil(rooms / 6)`, because `tower.py:98` fixes `ROOM_COLS × ROOM_ROWS =
3 × 2 = 6` rooms per floor and `24-FLOORPLAN-SCALE.md` §7 is explicit: *"Do not
change `ROOM_COLS/ROOM_ROWS`, and do not enlarge the building."*

| seats | depts | CEU/dir/mgr/leaf | rooms | rooms/floor | floors |
|---|---|---|---|---|---|
| 11 | 3 | 1/3/1/6 | 3 | 6 | **1** |
| 25 | 5 | 1/5/3/16 | 5 | 6 | **1** |
| 50 | 9 | 1/9/5/35 | 7 | 6 | **2** |
| 71 | 9 | 1/9/8/53 | 10 | 6 | **2** |

**This is the correction to the stated problem.** The owner's page shows one
floor with four rooms, and the brief said "the map is too small for the org".
The arithmetic says the opposite again: **11 seats needs one floor and three
rooms** — 6 room slots and one floor are *unused*. The building is bigger than
the org; it is the *programme inside it* that is wrong (§3), and, separately, the
desk pitch that occludes (§8 of doc 24).

---

## 3. How the CURRENT four rooms differ, and why `review` is empty

The live store (`<state>/office/office.db`) holds exactly:

| live room | kind (as written) | capacity | seated |
|---|---|---|---|
| `boardroom` | `exec` | 4 | 3 — CEO + `lead-content` + `lead-quality` (both managers) |
| `desk-a` | `work` | 4 | 4 leaves (`wren`, `mira`, `sable`, `kerr`) |
| `desk-b` | `work` | 4 | 3 leaves (`alex`, `tobi`, `juno`) |
| `review` | `review` | 4 | **0** |

and three live departments (`leadership`/meta, `delivery`/product,
`assurance`/intelligence), all with `director_id` NULL, with the two managers
reporting to the CEO.

### Three differences from the programme, in order of importance

1. **`review` is empty — and that is CORRECT, not a defect.**
   `57-BOARDROOM.md:19` defines a review room as *"a manager ruling on their own
   team's proposals"* — a place agents **walk to** for a meeting
   (`tower.py:63` maps the `meeting` event to the `meeting` animation). It has no
   permanent desks. `room_layout` types it at **`seats = 0`** for exactly this
   reason, and `check` *refuses* a review room that claims permanent seats.
   **Rendering it empty is the room working.** The defect the owner sees —
   "`review` empty, desks crowded" — is not two symptoms of one cause; the empty
   room is correct and the crowded desks are the fault.

2. **The two `work` rooms hold leaves only — no manager's desk.** The live
   `desk-a`/`desk-b` seat 7 leaves between them, and neither records a desk for
   the manager who runs it. The building therefore cannot show *which manager
   owns which desks*: `lead-content` and `lead-quality` sit in the boardroom and
   their teams sit in two rooms with no rule binding them. The programme puts
   **one manager desk in each work room** — `desk-a` = 1 manager + 6 leaves — so
   the unit is visible in the floor plan. `check` enforces this: a work room with
   two supervisors, or with none, is refused (`95-CONTRACT.md:657`, C31).

3. **The rooms are over-filled relative to their own stated capacity.**
   `desk-a` carries `capacity = 4` in the store and seats 4; `desk-b` carries
   `capacity = 4` and seats 3. Seven leaves in three rooms with a 4-seat
   capacity constant each is a plan that fits people into the rooms it has rather
   than sizing rooms to the units the org actually runs. At the programme's
   geometry (doc 24 §7: 12×7, nine desks) a 1-manager + 6-leaf room is 7 desks —
   comfortably inside one room, so the crowding is a *programme* error, not a
   tile shortage.

### Is the current layout wrong?

**Yes, in two ways, and the fix is not more rooms.**

* **Wrong: the work rooms have no manager boundary.** Nothing in the building
  ties `desk-a` to `lead-content` and `desk-b` to `lead-quality`. The org model
  (`org.py:94`, `95-CONTRACT.md:657`) says a unit is measured and reviewed as one
  manager's set of leaves; a building that mixes them into undistinguished rooms
  hides the structure the office exists to make legible.
* **Not wrong: `review` being empty.** It is a transient meeting room by
  definition. The current programme should keep it — deleting it because it draws
  empty would remove the one room the internal-decision function has.

The current programme (1 boardroom + 2 bare work rooms + 1 review) is a
*4-room* answer to a *3-room* problem. The programme here is 3 rooms: a 4-seat
boardroom, **one** manager's 7-seat work room, and the review room. If the org
prefers two managers each running their own unit, the programme is
`programme(13, 3)`-shaped (7 leaves, 2 managers) — the point is that the **room
count follows the org's manager count**, which the current layout does not
express.

---

## 4. The tradeoff: fewer big rooms vs more small ones

The programme could type a work room at any leaf count from 1 to 7 (the span
limit) and still be legal. That is a real choice with two poles:

| | **fewer, bigger rooms** | **more, smaller rooms** |
|---|---|---|
| a work room holds | up to 7 leaves (span max) | 2–3 leaves |
| rooms for 25 seats | 3 work rooms | 6–8 work rooms |
| floors for 25 seats | 1 | 1–2 |
| reads as | one team per room, desks dense | one squad per room, desks sparse |
| cost | a manager's span is fully used; a departure empties a room slower | more managers (each room needs one); more room-check cost at the store layer |
| occlusion risk | a 7-leaf room at pitch 2 occludes (doc 24 §3) — needs the (4,3) fix | pitch 2 is fine because rows are packed less tightly… but the *cell* is still 15 vs 8.5 |

**I choose fewer, bigger rooms, up to the span limit** — i.e. each work room is
one manager's full unit, ≤ 7 leaves. Three reasons, in order:

1. **The span limit already caps a room at 7 leaves** (`org.py:94`, D34). Going
   *smaller* than the span limit does not buy legibility in the org model — it
   buys more managers, and managers are seats that do not produce. The org's own
   design says "one manager, up to seven reports"; a room of 2 leaves is a
   deliberate under-use of a manager the org is already paying for.
2. **Room count is bounded by the floors, not by taste.** At 11 and 25 seats the
   programme is 3 and 5 rooms and fits one 6-room floor either way; at 50/71 it
   is 7/10 rooms and needs two floors. Small rooms only *hurt* here: a 2-leaf
   room policy would double the work-room count and push 25 seats onto a second
   floor, spending a floor to achieve nothing the org model asks for.
3. **The occlusion problem is a pitch problem, not a room-size problem.**
   `24-FLOORPLAN-SCALE.md` §3 proves the row overhang is `15 > 8.5` — a
   *constant* that is unaffected by how many leaves a room holds. Packing fewer
   desks per room does not fix occlusion; changing the pitch to (4,3) does. So
   "more smaller rooms" solves a problem this layer does not have.

### Recommendation

1. **Adopt `room_layout.programme()` as the room programme.** 11 seats → 3
   rooms (boardroom 4, work 7, review 0), one floor.
2. **Give every work room its manager's desk.** This is the single change that
   makes the org's structure visible in the building, and `check` enforces it.
3. **Keep the review room even though it draws empty.** Empty is the room
   working (`57-BOARDROOM.md`); the defect is the crowded, unbounded work rooms.
4. **Fix the geometry separately** with doc 24 §7 (12×7, pitch (4,3), odd-row
   stagger) — a different layer, a different change, and not one this programme
   can substitute for.

---

## 5. What the module refuses, and why each refusal exists

`room_layout.check(programme, headcount)` returns `{ok, seats_offered,
shortfall, floors_needed, fits_grid, reason}` and refuses, by name:

| refused | reason it must be refused |
|---|---|
| a programme that leaves seats undrawn (`shortfall > 0`) | the headcount walks; a plan that cannot seat everyone is the bug the module exists to catch |
| a work room with **no** supervisor | leaves with no manager are an un-reviewable unit (C30/C31) |
| a work room with **two** supervisors | a work room is ONE unit (C31, `95-CONTRACT.md:657`) |
| a supervisor counted **as one of the leaves** | the manager cannot be a leaf in the unit it reviews (C31) — asserted twice in the tests |
| a work room past **7 leaves** | the manager would be over-span (`org.py:94`, D34) |
| a work room below **2 desks** | a 1-seat room cannot hold a manager and a report |
| a `review` room with permanent seats | a review room is a walking destination (`57-BOARDROOM.md`) |
| a `boardroom` with no seats | the executive chain must sit somewhere |
| an unknown `kind` | only `work` / `boardroom` / `review` are writable by the code |
| a room count over the **3×2 grid** | reported as `fits_grid=false` + `floors_needed` — **never** silently absorbed by growing the grid (doc 24 §7) |

Note the deliberate split in `ok` vs `fits_grid`: a programme that seats
everyone but needs a second floor is `ok=true, fits_grid=false` with the overflow
named in `reason`. Conflating the two would make `ok` a lie in one direction
(claiming an unseatable org is fine) or the other (claiming nobody can sit down
because a floor is full).

---

## 6. What this document does NOT claim

* It does **not** claim the geometry is fixed. `24-FLOORPLAN-SCALE.md`'s
  recommendation (12×7, pitch (4,3)) is a change to `tower.py` and is **not
  applied**; D7 and D8 in `30-VISUAL-DEFECT-LEDGER.md` remain **OPEN**. This
  programme is satisfied by *any* geometry that holds 1 manager + ≤7 leaves in a
  room — which the current 6×4 at 6 desks does not (a 7-leaf work room needs 8
  desks, and 6×4 holds 6). So **the programme requires the doc-24 geometry to be
  buildable**: the two changes are complementary, not alternatives.
* It does **not** claim the store enforces the programme. The `office_room.kind`
  column has no `CHECK` in `store.py:97` and `room_create` (`org.py:253`) accepts
  any `kind` string. `room_layout` is the design layer; wiring `check` into
  `room_create` would be a **separate change** to `org.py`, which this task was
  scoped out of. Until then the programme is advisory to the operator, not
  enforced by the store.
* It does **not** claim directors are mandatory on small orgs. `_can_afford_directors`
  (`headcount >= 1 + 2·dept_count`) is a **chosen** rule, matching the live
  office's shape; D35 requires a director to own a department that *has* one, not
  that every org of 8 seats invent a directorate. If the owner wants a director
  seat at every department regardless of size, this rule is the one to change.

---

## 7. Running the proof

```sh
.venv/bin/python -m pytest tests/test_office_room_layout.py -q
```

Expected: 40 tests, all passing. The tests read the constants from the module
(which reads them from `org.py` / `tower.py`) rather than transcribing them, and
cover: `programme(11,3)` seats everyone and fits the grid; `check` refuses a
short programme **and names the shortfall (8)**; a supervisor is never counted as
a leaf; an over-grid programme says so and does **not** grow the grid.
