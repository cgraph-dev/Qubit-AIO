# 36 — The seat BINDING: seat ↔ room ↔ department ↔ supervisor

**Status: rule + checker.** The rule is stated here; the checker is
`tests/test_office_seat_binding.py`, which builds a real org through the real
functions (`org.hire` / `room_create` / `department_create`) and reads it back
through `tower.floorplan()` — the shape the renderer draws.

This file exists because two defects can sit in the store with nothing to catch
them:

1. **The two live `work` rooms hold LEAF seats only.** No manager has a desk in
   either, so nothing in the building ties `desk-a` to `lead-content` or
   `desk-b` to `lead-quality`. The org's chain of command (every leaf reports to
   a manager, C30/C31) is invisible in the floor plan.
2. **`office_agent.room_id` was settable only at HIRE time.** `org.hire` accepts
   `room_id` (`org.py:344`) but the CLI never exposed it, so a seat hired before
   its rooms existed could never be seated. `seat_move` (`org.py:288`) fills the
   hole this session. Nothing tested that the SEATING of a seat and the REVIEWER
   of its work agree.

`room_layout.check` (`room_layout.py:349`) validates a *programme* — a list of
rooms — against a headcount. It never reads the store, so it cannot notice a
leaf seated in room A while the manager reviewing its work sits in room B, or
has no desk at all.

---

## 1. The rule, stated plainly

For every live seat in the org:

| # | property | statement |
|---|---|---|
| B1 | **seat → room** | if `room_id` is set, the room exists |
| B2 | **seat → room's department** | if `room_id` is set, the seat's `department_id` is that room's `department_id` (or NULL — a seat may hold no department); it is **never** a *different* division |
| B3 | **leaf → seated supervisor** | every leaf's `supervisor_id` names a seat that is itself **SEATED** (`room_id` set and drawn in `tower.floorplan()`), not merely hired |
| B4 | **leaf → supervisor's room** | when a manager and its leaves share a room, the manager's desk is on a **distinct cell** from every leaf's |
| B5 | **manager's desk is in the room it runs** | a manager's own desk is in the room holding its leaves (`33-ROOM-LAYOUT.md` §1, D35 / `11-ORG-CHARTER.md`) |

B1/B2 are the seat↔room↔department triangle. B3/B4/B5 are the seat↔supervisor
half — the chain of command made visible in the floor plan.

---

## 2. Where each part is enforced

The "enforced by" column names a real line. `STORE` = the write path refuses an
illegal state and it cannot be inserted. `TEST` = no store code refuses it; the
checker in `tests/test_office_seat_binding.py` is the only thing that fails. `—`
= nothing enforces it yet.

| # | property | enforced by | kind | line |
|---|---|---|---|---|
| B1 | seat → room exists | `hire` refuses a missing room | STORE | `org.py:370–373` |
| B1 | seat → room exists (move path) | `seat_move` refuses a missing room | STORE | `org.py:316–319` |
| B2 | seat's room's department == seat's department | `hire` refuses a foreign-department room ("a P&L that does not match the floor") | STORE | `org.py:376–382` |
| B2 | a move takes the room's department as the seat's | `seat_move` `COALESCE(..., room.department_id)` | STORE | `org.py:320–321`, `327–330` |
| B3 | leaf's supervisor is SEATED | **nothing** | TEST | `test_a_leaf_supervisors_are_seated_not_merely_hired`; gap demo `test_the_seated_supervisor_rule_can_be_violated_by_a_real_path` |
| B4 | manager and leaves on distinct cells | nothing in the store; `desks-distinct` is a *room*-level geometry gate, and `tower.floorplan()` assigns distinct slots by construction | TEST | `test_manager_and_leaves_in_a_room_sit_on_distinct_desks` |
| B5 | manager's desk is in the room it runs | **nothing** | TEST (partial) | negative case in `test_leaf_seated_away_from_its_manager_is_caught_then_a_correct_one_passes` |

**The rule `hire` enforces** (`org.py:376–382`): when both `room_id` and
`department_id` are passed and they disagree, the insert is refused — *"a desk in
one division while counted in another is a P&L that does not match the floor."*
When only `room_id` is passed, the seat's department is taken from the room
(`org.py:374–375`), so the floor plan and the P&L cannot drift on that path.

**What `seat_move` adds** (`org.py:288–339`): it re-places a seat without
re-hiring it (`org.py:296–298`), refuses a room that does not exist
(`org.py:316–319`), and takes the room's department as the seat's when none is
given (`org.py:320–321`). But **`seat_move` does not check the supervisor's
room either** — moving a leaf across the building away from its manager is
accepted today.

---

## 3. What nothing enforces yet (the gaps)

Listed honestly. Each is a state the store will accept and the checker would
flag.

* **G1 — a leaf's supervisor may be unseated.** `hire` checks the supervisor
  *exists* (`org.py:405`) and is within span (`org.py:406`), never that it has a
  desk. `test_the_seated_supervisor_rule_can_be_violated_by_a_real_path` builds
  exactly this: a `worker` at a desk whose supervising manager has
  `room_id IS NULL`. The chain exists on paper and nowhere in the building.
* **G2 — a leaf may be seated in a room other than its manager's.** `hire` and
  `seat_move` both accept it. This is defect (1) at the seat level: the
  supervisor is seated, but in a different room. The negative case in
  `test_leaf_seated_away_from_its_manager_is_caught_then_a_correct_one_passes`
  builds and catches it; no store path refuses it.
* **G3 — a room may be "owned" by a manager with no desk in it.** Nothing links
  `office_room` to a manager seat at all. `room_create` (`org.py:253`) takes only
  `department_id`, `slug`, `kind`, `role_scope`, `capacity`; there is no
  `manager_id`. A room's manager is inferred from who is seated in it, and an
  empty room infers none — so the two live `work` rooms, holding leaves only,
  have no manager by this reading.
* **G4 — a manager's leaves may be split across rooms.** Nothing requires a
  manager's direct reports to share its room. Only the span limit
  (`org.py:94`, D34) bounds *how many*; nothing bounds *where*.
* **G5 — `department_id` may be set without a room.** Legal (a department's
  headcount is not its seating), but it means B2's "or NULL" branch carries
  weight — a seat with a department and no desk is a P&L with no desk, which is
  fine, and the checker treats it as fine.

The gap between B1/B2 (STORE) and B3/B4/B5 (nothing) is the whole reason a live
org can render a building whose floor plan says one thing and whose chain of
command says another. The fixes — a seating check in `hire`/`seat_move`, or a
`manager_id` on `office_room` — are out of scope for this file, which only
asserts and documents.

---

## 4. What the checker does NOT cover

* It reads one store built in a tmp XDG root; it does not diff against the LIVE
  `office.db`. Run it against a real store by pointing `XDG_STATE_HOME` at the
  live state dir.
* It does not check the CEO's seating. The CEO legitimately reports to the owner
  and may sit in the boardroom or nowhere; B3 explicitly skips `seat_type ==
  'ceo'`.
* It does not check a *director_portfolio*'s desk against its department
  (`org.py:227` guards the department→director edge, a different relation).
* It does not check terminated seats' seating; they are excluded, as in
  `tower.floorplan()` (`tower.py:138`).

---

## 5. Reproduce

```sh
.venv/bin/python -m pytest tests/test_office_seat_binding.py -v
```

The last test prints the live binding table —

```
seat           room               department   supervisor     sup_room
----------------------------------------------------------------------
ceo            -- not at a desk -- NULL         (owner)        n/a
lead-content   desk-a             content      ceo            -- not at a desk --
w1             desk-a             content      lead-content   desk-a
```

— which reads, in order: the CEO reports to the owner and holds no desk; the
manager `lead-content` sits in `desk-a`, is counted in `content`, and reports to
the unseated CEO; the leaf `w1` sits in `desk-a` beside its manager. B2 holds
(each seat's department matches its room); B3 holds for the one leaf whose
supervisor is seated (`w1` → `lead-content`).
