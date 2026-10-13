# 41 — Module Contract: who is the authority for each shared concept

**Status: ACCURATE** (this file). It records the authorities for the concepts
the office modules share, plus the concepts that have NO authority -- which is
a risk register, not an audit result. Its claims are checked by
`tests/test_office_cross_module.py`.

`voicebridge/office/` is 24 Python modules and
`frontend/qubit-app/plugins/qubit-office/` is five JS modules. Each has its own
test, and each test passes. That is not the same as the modules agreeing with
**each other**: a module can be internally consistent and still hold a number
the module next to it holds differently. The bug lives in the seam.

This document records, for every concept more than one module has an opinion
about, **which module is the AUTHORITY** — so a future change knows which one to
change — and the honest list of concepts that currently have **no single
authority**, which is where the next disagreement will come from.

**The test that enforces this:** `tests/test_office_cross_module.py`. Six
contracts; each either holds (asserted live) or is recorded as a finding
(`xfail` with both real numbers in the reason). Run it with:

```sh
.venv/bin/python -m pytest tests/test_office_cross_module.py -q
# prove the recorded findings really fail:
.venv/bin/python -m pytest tests/test_office_cross_module.py -q --runxfail
```

A disagreement found by that test is a **finding**, not a test to be made to
pass. Where the contract does not currently hold it is `xfail`-ed with the two
real numbers, so the gap is recorded instead of hidden.

---

## Part 1 — Concepts and their authorities

### 1.1 Desk capacity — "how many desks fit a room"

| Module | Role |
|---|---|
| **`seating_plan.py`** | **AUTHORITY.** `desks_per_room(room_w, room_h, pitch)` derives the count from the room and stride; `plan(headcount)` derives `rooms_needed` / `floors_needed`. |
| `floorplan_fit.py` | Consumer. `geometry_for(h)` **delegates every capacity field** to `seating_plan.plan(h)` — it does not re-derive them. This is the model to copy. |
| `tower.py` | Consumer. `floorplan()` reads `ROOM_COLS/ROOM_ROWS/ROOM_W/ROOM_H` as its own constants; `SEAT_SLOTS_PER_ROOM` must equal `seating_plan.desks_per_room(...)`. |
| `room_layout.py` | Consumer, with a twist — see §1.2. |

**Invariant.** `tower.SEAT_SLOTS_PER_ROOM == seating_plan.desks_per_room(12, 7, (4, 3)) == 9`.
Asserted by `test_tower_capacity_fields_agree_with_the_planners`.

### 1.2 Room *programme* — "what rooms the org needs"

| Module | Role |
|---|---|
| **`room_layout.py`** | **AUTHORITY.** `programme(headcount, dept_count)` emits a boardroom + one work room per manager + one review room, with `seats` per room and the split of seats into ceo/directors/managers/leaves (`staffing`). |

This is a **different question** from §1.1's "how many desks fit", and the two
answers are legitimately different numbers. The authority for "which rooms, what
kind, who sits in them" is `room_layout`; the authority for "how many desks a
room physically holds" is `seating_plan`. A change that needs one of them to
move must move the authority and reconcile the other.

**Currently they disagree about FLOORS** — see Finding A, below.

### 1.3 Room geometry and desk stride — `ROOM_W/ROOM_H`, `PITCH_COL/PITCH_ROW`, `ODD_ROW_OFFSET`

| Module | Role |
|---|---|
| **`seating_plan.py`** | **AUTHORITY.** States the recommended geometry and owns the clearance maths (`clearance`, `MIN_CLEARANCE`, `MIN_ROW_OFFSET`) that decides whether a stride is legal. |
| `floorplan_fit.py` | Consumer — but holds its **own copy** of the constants (§No-authority list, item N1). `assert_matches_tower()` is the checker that binds `tower.py` to the recommendation. |
| `tower.py` | Consumer. Carries the live constants the running server uses; must equal the recommendation. |

**Invariant.** `(tower.ROOM_W, tower.ROOM_H, tower.PITCH_COL, tower.PITCH_ROW,
tower.ODD_ROW_OFFSET) == seating_plan`'s. Asserted by
`test_tower_capacity_fields_agree_with_the_planners` and
`test_tower_desk_slots_equal_the_fit_layers_desk_positions`.

### 1.4 Desk *positions* — where a desk sits in a room

| Module | Role |
|---|---|
| **`seating_plan._desk_positions`** | **AUTHORITY** for the layout rule (stride + odd-row stagger + keep-if-in-room). |
| `floorplan_fit.desk_positions` | Consumer: the same layout translated to absolute coords (adds the room origin). |
| `tower.room_slot_x / room_slot_y / room_slot_offset` | The SERVER's placement of a desk for a seat index; must produce the same layout the planner recommends. |

**Invariant.** `tower.room_slot_x(0, n) + tower.room_slot_offset(n)` and
`tower.room_slot_y(0, n)` for `n in range(SEAT_SLOTS_PER_ROOM)` equal
`floorplan_fit.desk_positions(0, 0, SEAT_SLOTS_PER_ROOM, geometry_for(11))`.
Asserted by `test_tower_desk_slots_equal_the_fit_layers_desk_positions`.

### 1.5 Span limit — the maximum direct reports one manager carries

| Module | Role |
|---|---|
| **`store.py`** (`MAX_SPAN = 7`) | **AUTHORITY.** `org.py` enforces `store.MAX_SPAN` in `_check_span`. |
| `room_layout.py` | Consumer — holds **its own copy** `MAX_SPAN = 7` and derives `MAX_ROOM_SEATS` from it (§No-authority item N2). |

**Invariant.** `room_layout.MAX_SPAN == store.MAX_SPAN == 7`, and `org.py`
carries no private literal. Asserted by
`test_span_limit_is_the_same_number_in_every_module`.

### 1.6 Legality of an arrangement — "is this a legal room / unit"

| Module | Role |
|---|---|
| **`room_layout.check`** | **AUTHORITY** for a *programme*: exactly one supervisor per work room, 1..MAX_SPAN leaves, review rooms seat nobody, a boardroom seats the executive chain, no shortfall. |
| **`org.hire` / `org._check_span`** | **AUTHORITY** for a *seat*: the CEO has no supervisor, every other seat has one, a supervisor may not exceed MAX_SPAN, a seat hired into a room belongs to that room's department. |
| `store.py` | The database: `CHECK` constraints and the enum sets (`SEAT_TYPES`, `KINDS`, `AGENT_STATUS`). |

These two are the same rule seen from two layers, and they must agree: a room
`room_layout.check` calls legal must be one `org.hire` will populate. Asserted
by `test_check_accepts_the_same_arrangement_hire_accepts` and
`test_check_rejects_the_same_arrangement_hire_rejects`.

### 1.7 Station map — named spots inside a room

| Module | Role |
|---|---|
| **`tower.py`** (`STATIONS`, `_EVENT_STATION`) | **AUTHORITY.** The server decides what a station *is* and which event walks to it. |
| `motion.js` (`STATIONS`, `STATION_EVENT`) | **Mirror, by contract.** Must name the same stations at the same offsets. |
| `motion.js` (`STATION_ROOMS`, `STATION_ANIMATION`) | Renderer-side additions with **no Python authority** (§No-authority item N4). |

**Invariant.** `motion.js`'s `STATIONS` and `STATION_EVENT` equal `tower.py`'s,
name for name, offset for offset. Asserted by
`test_motion_js_stations_match_tower_stations_literally` and
`test_motion_js_event_station_map_matches_tower_event_station`.

### 1.8 The state payload — what the renderer is sent

| Module | Role |
|---|---|
| **`tower.state()`** | **AUTHORITY** for the payload's shape. |
| `qubit-office.js` | Consumer. Every field it reads off a character must exist in the payload, or be a documented nullable. |

**Invariant.** Every character field the renderer reads (`position`, `slug`,
`seat_type`, `agent_id`, `animation`, `walking`, `stalled`, `facing`) is carried
by `tower.state()`; `position` always carries `{floor, room, room_id, slot, x,
y}`; `position.progress` is the documented nullable, present only while walking.
Asserted by `test_renderer_reads_only_fields_the_payload_carries`.

### 1.9 Kind taxonomy — what counts as spend

| Module | Role |
|---|---|
| **`store.KINDS`** | **AUTHORITY** for the full set of ledger kinds. |
| **`ledger.MAGNITUDE_KINDS` / `SIGNED_KINDS`** | **AUTHORITY** for the sign rule (magnitude vs signed). |
| `balance_sheet.py` | Reader: `MAGNITUDE_KINDS`/`SIGNED_KINDS` are read from `ledger`, and `COST_KINDS = MAGNITUDE_KINDS − {revenue} = ("inference","fees","capex")`. |
| **`ledger.sum_metered`** | **AUTHORITY** for the metered fold every cap reads (D16). |

**Invariant.** `balance_sheet.COST_KINDS == MAGNITUDE_KINDS − {revenue}` and
every module's cost-kind set equals it. Asserted by
`test_cost_kinds_equal_the_magnitude_kinds_minus_revenue` and
`test_envelope_admits_against_the_same_kinds_the_ledger_counts_as_cost`.

### 1.10 Unmetered rows — D16, recorded but excluded

| Module | Role |
|---|---|
| **`ledger.py`** | **AUTHORITY.** `sum_metered` excludes `unmetered=1`; `sum_reported` includes it. |
| `store.py` views | The SQL folds, all gated on `unmetered=0`. |
| `caps.py` / `envelope.py` | Every cap must read the METERED fold, never the reported one. |
| `balance_sheet.py` / `receipts.py` | Report unmetered cents as a NAMED residual; never fold them in silently. |

**Invariant.** A ledger with one metered cost row and one unmetered row has
`sum_metered == <metered>` and `sum_reported == <metered + unmetered>`, and a
cap spends against the metered value. Asserted by
`test_metered_and_reported_folds_agree_about_what_unmetered_means`.

### 1.11 Grid — the floor grid the building occupies

| Module | Role |
|---|---|
| **`seating_plan.py`** | **AUTHORITY:** `grid_cols = ROOM_COLS * (ROOM_W + 1)`, `grid_rows = ROOM_ROWS * (ROOM_H + 1)`. |
| `tower.py` | Consumer: the payload's `grid` and its room origin arithmetic use the same formula. |
| `room_layout.py` | Consumer: `ROOMS_PER_FLOOR = 3 * 2 = 6`. |

**Invariant.** `tower.state()["grid"] == {cols: 39, rows: 16}` equals
`floorplan_fit.geometry_for(11)`'s `grid_cols`/`grid_rows`. Asserted by
`test_payload_grid_matches_the_geometry_the_planners_recommend`.

---

## Part 2 — Concepts with NO single authority (the real risk)

These are stated, verified, and named because they are where the next
disagreement will come from. Each is currently in agreement — but by
**coincidence of copies**, not by a link, so nothing fails when one copy moves.

### N1 — Room geometry constants exist in four modules

`ROOM_W, ROOM_H`, `PITCH_COL, PITCH_ROW`, `ODD_ROW_OFFSET` are typed in
`seating_plan.py`, `floorplan_fit.py`, **and** `tower.py` (and referenced by
`room_layout.py` in prose). They agree today by hand-copying.

**Risk.** `tower.py` was changed to the recommended geometry and
`floorplan_fit`'s checker did not follow — see Finding B. The fix is to have
`tower.py` import the numbers (or a single `geometry.py` own them), not to
re-type them in a fourth place.

### N2 — The span limit is stated twice

`store.MAX_SPAN = 7` (authority) and `room_layout.MAX_SPAN = 7` (a copy).
`room_layout.MAX_ROOM_SEATS = MAX_SPAN` is derived from the copy, not the
authority.

**Risk.** Raising the span limit in `store` (as D34 evolves) leaves
`room_layout` rejecting the larger rooms the org would then allow. The
cross-module test asserts they are equal, so the drift would be caught — but
the copy is still the wrong shape.

### N3 — The cost-kind tuple `('inference','fees','capex')` is a bare literal in eight modules

`balance_sheet` derives it (good). But the literal
`kind IN ('inference','fees','capex')` appears inline in the SQL of
`store.py` (many times), `envelope.py`, `audit.py`, `tower.py`, `ledger.py`,
`balance_sheet.py`, and `receipts.py`. None of them reads a shared constant.

**Risk.** Adding a new cost kind (say `storage`) in one place and not the others
makes the P&L, the cap admission and the balance sheet count different money —
exactly the failure D16 exists to prevent, arriving through the back door. The
authority is `ledger.MAGNITUDE_KINDS`; the copies should read it.

### N4 — `motion.js`'s `STATION_ROOMS` and `STATION_ANIMATION` have no Python counterpart

`STATION_ROOMS` maps a station to candidate room slugs, and `STATION_ANIMATION`
maps a station to an animation — neither is stated anywhere in Python. They are
renderer-side, and the module comment claims "nothing here is a name the backend
does not use". That claim is only partly checkable: the backend's room slugs are
arbitrary owner strings (e.g. `desks0`), so a name like `desks-2` matches
nothing a real org writes.

**Risk.** A station with no resolvable room is a walk the renderer cannot place.
The cross-module test asserts the table is not pure fiction (at least one named
slug is one the backend emits) and that the primary resolution path — by
`room_id` — works on a real payload; it cannot assert more without the backend
declaring the slug vocabulary, which it does not.

### N5 — The "work room" shape is asserted in two modules from different data

`room_layout` decides a work room is "one supervisor + 1..MAX_SPAN leaves" from
the **programme dict**; `tower.floorplan` builds work rooms from the **store**
(`office_room.kind`). Only `room_layout.check` enforces the supervisor count.
`tower.floorplan` will happily draw a room whose seats violate the rule, because
nothing calls `check` on a live floor plan.

**Risk.** A room created directly in the store, bypassing the programme, is drawn
without ever being checked. The authority (`room_layout.check`) is not in the
write path for `tower`.

---

## Part 3 — Findings (contracts that currently do NOT hold)

Both were found by `tests/test_office_cross_module.py`, hold no owner waiver, and
are recorded as `xfail` with both values named so they cannot be forgotten.

### Finding A — `room_layout.programme` and `seating_plan.plan` describe different buildings

`room_layout` counts a boardroom + one work room per manager + a review room, so
its room count — and therefore floor count — exceeds the desk-based plan's for
the same headcount:

| headcount | `seating_plan` rooms / floors | `room_layout` rooms / floors |
|---|---|---|
| 1 | 1 / 1 | 2 / 1 |
| 11 | 2 / 1 | 3 / 1 |
| 25 | 3 / 1 | 5 / 1 |
| **50** | **6 / 1** | **8 / 2** |
| **71** | **8 / 2** | **11 / 2** |
| **200** | **23 / 4** | **27 / 5** |

`seating_plan` fits 50 seats in 6 rooms on ONE floor (54 desks/floor); the
programme needs 8 rooms = TWO floors (6 rooms/floor). One of the two is wrong
about the building a 50-seat org needs.

**Which is right is an owner question, not a test's** — the desk count can
legitimately fit more seats per floor than a programme of *one-manager-per-room*
rooms can house. The contract asserts only that the programme's floor count must
not EXCEED the desk plan's; it currently does, at h ∈ {50, 71, 200}.

**To resolve:** decide whether a floor may hold more seats than it has
one-manager rooms (then `seating_plan` is fine and the programme must allow
multi-manager rooms), or whether the room programme is the real constraint (then
`seating_plan`'s `rooms_needed`/`floors_needed` must be derived from
`room_layout`, making `room_layout` the authority). Either way, one of them
becomes the authority for "how many floors".

### Finding B — `floorplan_fit.assert_matches_tower()` reports a fixed defect as live

`assert_matches_tower()` parses `tower.py`'s text. For the desk stride it reads
the **return expression** inside `room_slot_x`/`room_slot_y` — which yields the
SYMBOL `PITCH_COL`, not its value — and then fills `current["pitch_col"]` etc.
from its **own** `LEGACY_PITCH_COL`/`LEGACY_PITCH_ROW`/`LEGACY_ODD_ROW_OFFSET`
constants instead of resolving the symbol it just parsed. It also reports
`SEAT_SLOTS_PER_ROOM` as `room_cols * room_rows = 6`.

So it reports, as a live divergence:

| param | reported `current` | `tower.py` ACTUALLY holds | reported `recommended` |
|---|---|---|---|
| `SEAT_SLOTS_PER_ROOM` | 6 | **9** | 9 |
| `pitch_col` | 2 | **4** | 4 |
| `pitch_row` | 2 | **3** | 3 |
| `odd_row_offset` | 0 | **1** | 1 |

`tower.py` was **fixed** to the recommended geometry, but the checker was not
updated to read it — so `same` is `False` on a `tower.py` that agrees. This is
precisely the "fix reported but never bound" failure the checker exists to
prevent, and it is worse than a missing check: a stale checker that says
"divergent" trains its readers to ignore it.

**To resolve:** resolve the parsed SYMBOL against `tower.py`'s own constant
definitions (as `_tower_constants_from_source()` in the test does), so the
checker compares `tower.py` to the recommendation rather than to a hardcoded
memory of the pre-fix state.

---

## Part 4 — The mutation proofs

Three assertions in `tests/test_office_cross_module.py` were proved capable of
failing by mutating a **scratch copy** of a module — never a repo file:

| Mutation | Scratch file | What it proves |
|---|---|---|
| `seating_plan.ROOM_COLS, ROOM_ROWS = 3, 2` → `3, 1` | copy of `seating_plan.py` | the capacity agreement assertion (`test_geometry_for_and_plan_agree_on_rooms_and_floors` / the floor-count xfail) catches a changed floor count |
| `motion.js` `workbench: {x: 1, y: 2}` → `{x: 2, y: 2}` | copy of `motion.js` | the station-offset comparison catches a one-tile drift |
| `ledger.MAGNITUDE_KINDS` loses `"capex"` | copy of `ledger.py` | the cost-kind contract catches a kind dropped from the fold |

Each mutation test asserts the mutation CHANGED the answer (so the proof is not
vacuous) before asserting the contract would catch it.

---

## Adding a concept

When two modules come to share a concept, add it here **at the same time** as
the test that binds it, and name one of them the authority. If neither can be
named, add it to Part 2 — a concept with two owners and no authority is a
finding waiting to happen, and Part 2 is where that gets written down.
