# 42 — The room programme, made reachable: the CLI verb and the before/after

**Status: ACCURATE — shipped and proved by `tests/test_office_room_layout_applied.py`.**
`33-ROOM-LAYOUT.md` designed the room programme and `room_layout.py` computes it,
but until this document the programme had **no caller anywhere in the write
path**. `grep -rn "room_layout" voicebridge/ --include=*.py` returned **zero
hits** — only tests imported it. So the live office's rooms were whatever an
operator typed by hand, and a work room had no manager's desk. This document
records the verb that closes that gap, the numbers it produces against a real
store, and — honestly — what is still **not** wired.

---

## 1. The gap, stated as a fact

The programme existed and was proved in isolation (`tests/test_office_room_layout.py`,
40 tests). Nothing in `voicebridge/` called it. Measured, before this change:

```
$ grep -rn "room_layout" voicebridge/ --include=*.py
(no output)
```

The consequence is the one doc 33 §3 named: an org built the ordinary way
(floor → department → room → hire, no programme consulted) produces work rooms
with **no manager's desk**. From `tests/test_office_room_layout_applied.py`,
`test_before_no_work_room_has_its_manager_seated_in_it`, the store census after a
hand-built 11-seat org:

```
BEFORE census:
  room=boardroom  kind=boardroom capacity=4 seats=0 supervisors=0
  room=desk-a     kind=work      capacity=4 seats=4 supervisors=0
  room=desk-b     kind=work      capacity=4 seats=3 supervisors=0
  work rooms with their manager seated: 0 of 2
```

Two work rooms, seven leaf seats, **zero** managers seated in the unit they run.
The two live managers sit in the boardroom. The org model says a work room is one
manager's unit (`org.py:94`, span limit 7, D34; C31 `95-CONTRACT.md:657`), so
these rooms are units nobody runs.

---

## 2. The CLI verb

```
qubit office programme --headcount N --departments D [--confirm]
```

Registered in `voicebridge/cli.py` alongside the other `office` subcommands. It:

1. calls `room_layout.programme(headcount, dept_count)` and
   `room_layout.check(programme, headcount)`;
2. **previews by default** — a bare invocation prints the whole programme, per
   room, with the `why` from the module and writes nothing (its own
   `preview_default` path; every other mutating verb still refuses without
   `--confirm`);
3. on `--confirm`, creates the floors/departments/rooms it needs through the
   **existing** `org.floor_create` / `org.department_create` / `org.room_create`
   calls. No new store path, no new kwargs;
4. is **idempotent-ish**: a room slug already present under the programme's
   department is *reported* in `rooms_existing`, never re-created (the store's
   `UNIQUE(department_id, slug)` would otherwise raise);
5. exits **0** on success, **2** on a typed refusal (bad input, a programme its
   own `check` rejects), **3** when the emergency stop is latched — the same
   estop gate every other mutating verb uses.

Preview, run for real against a throwaway store:

```
$ qubit office programme --headcount 11 --departments 3
{
  "state": "preview",
  "headcount": 11,
  "departments": 3,
  "staffing": {"ceo": 1, "directors": 3, "managers": 1, "leaves": 6, "seats": 11},
  "rooms": [
    {"slug": "boardroom", "kind": "boardroom", "seats": 4, "supervisors": 4, "leaves": 0, "why": "the executive chain: the CEO (exactly one seat, org.py:390 D2) plus 3 portfolio director(s) ..."},
    {"slug": "desk-a",    "kind": "work",      "seats": 7, "supervisors": 1, "leaves": 6, "why": "one manager's unit: 6 leaf seat(s) of department #1 plus the manager's own desk ..."},
    {"slug": "review",    "kind": "review",    "seats": 0, "supervisors": 0, "leaves": 0, "why": "internal decisions — a manager ruling on its own team's proposals ... Holds NO permanent seats by design"}
  ],
  "seats_offered": 11,
  "floors_needed": 1,
  "fits_grid": true,
  "would": "create 1 floor(s), 3 department(s) and 3 room(s)",
  "next": "re-run with --confirm to write these rooms"
}
```

`--confirm` writes them; a second `--confirm` reports them as existing:

```
$ qubit office programme --headcount 11 --departments 3 --confirm   # run 1
  floors_created [1]  depts_created [programme-11-1, programme-11-2, programme-11-3]
  rooms_created   [boardroom, desk-a, review]        rooms_existing []
$ qubit office programme --headcount 11 --departments 3 --confirm   # run 2
  floors_created []   floors_reused [1]  depts_existing [programme-11-1..3]
  rooms_created   []                 rooms_existing [boardroom, desk-a, review]
```

---

## 3. The per-room census, with real numbers

`test_after_every_work_room_holds_one_supervisor_plus_its_leaves` writes
`programme(50, 9)` into a real store and prints the census:

```
AFTER census (programme(50, 9), written through the CLI):
  slug       kind      desks    mgr   leaf
  boardroom  boardroom    10     10      0
  desk-a     work          8      1      7
  desk-b     work          8      1      7
  desk-c     work          8      1      7
  desk-d     work          8      1      7
  desk-e     work          8      1      7
  review     review        0      0      0
  staffing: {'ceo': 1, 'directors': 9, 'managers': 5, 'leaves': 35, 'seats': 50}
  seats_offered: 50 of headcount 50
```

Every `work` room holds **exactly one** `is_supervisor` seat plus 1..7 leaves;
the boardroom holds the executive chain (CEO + 9 directors, all supervisors); the
review room holds none by design. For the 11-seat case on screen the programme is
`boardroom 4 · desk-a 7 · review 0` — **3 rooms**, one floor, versus the live
building's 4 rooms with two bare work rooms.

### Desks vs headcount

`seating_plan.plan(headcount)` takes **no** department count, so it is not
comparable to `programme(headcount, dept_count)` and is not compared here. The
comparable, checkable claim is that the programme's **physical desks** cover the
people it was asked to seat:

| headcount | departments | physical desks | shortfall |
|---|---|---|---|
| 11 | 3 | 11 | 0 |
| 25 | 5 | 25 | 0 |
| 50 | 9 | 50 | 0 |
| 71 | 9 | 71 | 0 |

At all four sizes **desks >= headcount** (in fact equal), so no seat is left
without a desk. A programme that seated fewer desks than people would be reported
by this test with both numbers.

---

## 4. Idempotence

```
idempotence: rooms after 1st run = 5, after 2nd = 5
  2nd run: created=[] existing=['boardroom', 'desk-a', 'desk-b', 'desk-c', 'review']
```

Two `--confirm` runs leave the same room count (5 for `programme(25, 5)`); the
second creates nothing, reuses floor 1, and names every room it found. No
duplicate rooms.

---

## 5. The mutation proof

A green suite proves nothing unless it can go red for the defect it claims to
cover. The test loads a **scratch copy** of `room_layout.py` with the one line
that appends the manager's desk to a work room removed — the "work room with no
manager" defect, injected — and runs the same assertions against it:

```
MUTATION CAUGHT:
   work room 'desk-a' has 0 supervisor seat(s); a work room is one manager's
   unit (C31) and must hold its manager's desk
   room_layout.check: room #1 ('desk-a') has no supervisor; a work room is one
   manager's unit (C31), so it must contain its manager's desk
```

The scratch copy lives under the session scratch dir; the repo module is asserted
unmodified (`"MUTATED" not in ...room_layout.py`).

---

## 6. What is still NOT wired — stated plainly

* **`tower.state()` / `tower.floorplan()` do not read `room_layout`.** They build
  the drawing from the **store** (`office_room.kind`, `office_agent.room_id`).
  The programme now *reaches the store* through this verb, so rooms created via
  `--confirm` **do** appear in `office floorplan` (verified: `floor 1 operations
  [('boardroom','boardroom'), ('desk-a','work'), ('review','review')]`) — but the
  *programme* is still not consulted when the tower renders. A room created
  directly in the store, bypassing this verb, is drawn without `room_layout.check`
  ever seeing it (this is N5 in `41-MODULE-CONTRACT.md`, unchanged).
* **The verb creates ROOMS, not SEATS.** It writes floors, departments and rooms
  at the programme's capacities; it does **not** hire agents or seat them. Putting
  the manager and its leaves into those rooms is the existing, separate
  `office hire` / `office seat-move` step. So after `programme --confirm`, the
  work room exists with `capacity = manager + leaves` but is **empty** — the
  manager's desk is sized, not filled.
* **`room_layout.check` is still not in `org.room_create`'s path.** `org.py:253`
  accepts any `kind` and any capacity; the store's `office_room.kind` has no
  `CHECK`. Wiring `check` into the write path is a change to `org.py`, which this
  task was scoped out of (doc 33 §6 says the same).
* **`seating_plan.plan` and `room_layout.programme` still describe different
  buildings** for a given headcount (`41-MODULE-CONTRACT.md` Finding A, an
  `xfail`). Reconciling them — deciding which is authority, or making `programme`
  consume `plan`'s geometry — is the parent's call and is not attempted here.
* **`staffing`/`programme`'s chosen director rule** (`headcount >= 1 + 2·D`) is
  still a choice, not an org law (doc 33 §6); the verb inherits it.

---

## 7. Running the proof

```sh
.venv/bin/python -m pytest tests/test_office_room_layout_applied.py -q
```

Expected: **10 passed**. They cover the before/after census, seats-per-work-room,
the desks-vs-headcount table, idempotence, the preview-writes-nothing rule, and
the mutation proof. Hermetic: tmp XDG state only; the mutation is a scratch copy.
