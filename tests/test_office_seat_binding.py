"""The seat BINDING: a seat, its room, its department and its supervisor agree.

The floor plan and the chain of command are two views of one org, and nothing
currently checks that the LIVE store holds them consistently. `room_layout.check`
(`voicebridge/office/room_layout.py:349`) validates a *programme* — a list of
rooms — against a headcount, but it never sees the store: it cannot notice that a
leaf is seated in `desk-a` while the manager that reviews its work has no desk at
all, or sits in `desk-b`.

This file asserts the binding on a REAL org built through the real functions
(`org.floor_create` / `department_create` / `room_create` / `hire`) and read back
through `tower.floorplan()` — the exact shape the renderer draws. It is a
*checker*, not a fix: it neither edits the store nor the modules, and where the
current model cannot express the rule it says so and asserts the weaker true
property instead of inventing one.

The four parts of the binding, and where each is enforced — code (STORE) or this
test (TEST) or nothing yet (GAP):

    | property                                        | enforced by        |
    |-------------------------------------------------|--------------------|
    | a seat's room's department == seat's department  | STORE  org.py:376  |
    | a seat's room exists when room_id is set         | STORE  org.py:370  |
    | every leaf's supervisor is SEATED, not just hired| TEST-ONLY (GAP)    |
    | a manager and its leaves share one room          | TEST-ONLY (GAP)    |
    | manager and leaves on DISTINCT desks             | TEST-ONLY (GAP)    |

Full enforcement map with line numbers: `docs/game-office/36-SEAT-BINDING.md`.

Hermetic: tmp XDG_* only; no network, no provider call, no live service.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg):
    """A two-department building with a manager and leaves, through the REAL API.

    Two work rooms in two departments (`desk-a`/content, `desk-b`/quality), one
    CEO, one manager (`lead-content`) seated in `desk-a`, two leaves under it —
    one in `desk-a` (its own room) and one in `desk-b` (a DIFFERENT room, the
    negative-case shape). Returns ids keyed by name.
    """
    from voicebridge.office import org as O

    floor = O.floor_create(cfg, 1, name="F1", role="operations",
                           confirmed=True)["id"]
    content = O.department_create(
        cfg, "content", floor_id=floor, division_kind="product",
        kill_criteria="closes if it ships nothing for 30 days",
        confirmed=True)["id"]
    quality = O.department_create(
        cfg, "quality", floor_id=floor, division_kind="service",
        kill_criteria="closes if it finds nothing for 30 days",
        confirmed=True)["id"]
    desk_a = O.room_create(cfg, content, "desk-a", confirmed=True)["id"]
    desk_b = O.room_create(cfg, quality, "desk-b", confirmed=True)["id"]

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    lead = O.hire(cfg, "lead-content", seat_type="manager", department_id=content,
                  room_id=desk_a, supervisor_id=ceo, confirmed=True)["id"]
    worker = O.hire(cfg, "w1", seat_type="worker", department_id=content,
                    room_id=desk_a, supervisor_id=lead, confirmed=True)["id"]
    return {
        "floor": floor, "content": content, "quality": quality,
        "desk_a": desk_a, "desk_b": desk_b,
        "ceo": ceo, "lead": lead, "worker": worker,
    }


def _seats(cfg):
    """The live org keyed by id, terminated included (a checker sees all)."""
    from voicebridge.office import survival as S

    return {s["id"]: s for s in
            S.roster(cfg, include_terminated=True)["seats"]}


def _floorplan_seats(cfg):
    """Every SEATED agent, from the renderer's own `tower.floorplan()`.

    Returns `{agent_id: room}` where `room` is the floor-plan room dict (its
    `slug`, `department_id`, and the seat's `slot` inside it). This is the
    building as drawn — the ground truth for "is this seat at a desk?".
    """
    from voicebridge.office import tower as T

    out = {}
    for floor in T.floorplan(cfg)["floors"]:
        for room in floor["rooms"]:
            for slot in room["seats"]:
                out[int(slot["agent_id"])] = {"room": room, "slot": slot}
    return out


def _supervises(seats, supervisor_id):
    """Slugs whose `supervisor_id` is this seat — the seats it reviews."""
    return sorted(s["slug"] for s in seats.values()
                  if s["supervisor_id"] == supervisor_id
                  and s["status"] != "terminated")


# =================================================== 1. seat room / department
def test_every_seated_agents_room_department_is_its_own_or_null(tmp_path,
                                                                monkeypatch):
    """A seat's room belongs to the seat's department (or the seat has none).

    This is the rule `hire` enforces at `org.py:376` ("a desk in one division
    while counted in another is a P&L that does not match the floor"). The
    point of asserting it on the REAL store is that the guard only runs on the
    paths that call it — it proves nothing about seats already written, or
    written by a path that skips it. Here it is checked against the floor plan
    the renderer draws, for every seat in the building.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    seats = _seats(cfg)
    plan = _floorplan_seats(cfg)

    assert plan, "the fixture puts seats at desks; an empty plan is a broken test"
    for agent_id, entry in plan.items():
        room = entry["room"]
        seat = seats[agent_id]
        # The room's department is what the seat is counted in, or the seat
        # holds no department at all (NULL is legal — ownership is not implied
        # by seating alone). Anything else is a P&L that disagrees with the map.
        assert seat["department_id"] in (room["department_id"], None), (
            f"{seat['slug']} sits in room {room['slug']!r} "
            f"(department #{room['department_id']}) but is counted in "
            f"department #{seat['department_id']}")
        # And the department the seat names must be the one hire wrote — the
        # fixture seats both leaves in their own manager's room, so both hold
        # a department and it equals the room's.
        if seat["slug"] in ("lead-content", "w1"):
            assert seat["department_id"] == room["department_id"]

    # the two leaves' rooms are the rooms the fixture asked for
    assert plan[ids["lead"]]["room"]["slug"] == "desk-a"
    assert plan[ids["worker"]]["room"]["slug"] == "desk-a"


def test_seat_room_department_rule_is_not_vacuous(tmp_path, monkeypatch):
    """The rule above has teeth: the STORE refuses a room from another dept.

    Without this, part 1 could pass because nothing ever puts a seat in a foreign
    room — i.e. it might be vacuous. `hire` must raise when a seat is sent to a
    room that belongs to a department other than the one named.
    """
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    b = _org(cfg)
    ceo = b["ceo"]
    # w1's room (desk-a, content) but claiming the quality department: refused.
    with pytest.raises(OfficeError) as exc:
        O.hire(cfg, "mismatch", seat_type="worker", department_id=b["quality"],
               room_id=b["desk_a"], supervisor_id=b["lead"], confirmed=True)
    assert exc.value.code == "OFFICE_BAD_INPUT"
    assert "P&L that does not match the floor" in exc.value.detail
    assert ceo  # the fixture's CEO was needed to build the rooms; named, not unused


# ============================================= 2. leaf's supervisor is SEATED
def test_a_leaf_supervisors_are_seated_not_merely_hired(tmp_path, monkeypatch):
    """Every leaf's supervisor has a desk (room_id set) — the weak true form.

    The STRONG rule — the supervisor's desk is in the leaf's room — the store
    neither expresses nor enforces (see `36-SEAT-BINDING.md` §Gaps), so this
    asserts the weaker property the model CAN hold: a leaf whose supervisor
    exists but has no `room_id` is a chain that exists on paper and nowhere in
    the building. The fixture is built correctly (both leaves under the seated
    `lead-content`), so this must hold.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    _org(cfg)
    seats = _seats(cfg)
    plan = _floorplan_seats(cfg)
    live = {i: s for i, s in seats.items() if s["status"] != "terminated"}

    for agent_id, seat in live.items():
        if seat["seat_type"] == "ceo":
            continue
        leaves = _supervises(live, agent_id)
        if not leaves:
            continue  # this seat supervises nobody
        for leaf_slug in leaves:
            leaf = next(s for s in live.values() if s["slug"] == leaf_slug)
            # the supervisor (this seat) is seated at a desk in the floor plan
            assert agent_id in plan, (
                f"{seat['slug']} supervises {leaf_slug!r} but holds no desk; "
                f"the chain exists on paper and nowhere in the building")
            assert seat["room_id"] is not None
            assert leaf["room_id"] is not None


def test_the_seated_supervisor_rule_can_be_violated_by_a_real_path(
        tmp_path, monkeypatch):
    """The GAP, demonstrated: a seated leaf may have an UNSET supervisor.

    `hire` validates that the supervisor EXISTS (`org.py:405`) and that it is
    within span (`org.py:406`), but never that it is SEATED. So a leaf can be
    drawn at a desk while the manager that reviews its work is absent from the
    floor plan — the exact defect the binding rule forbids, and the store does
    not refuse it. This test documents that hole rather than pretending the rule
    already holds: it builds the offending org and asserts the checker's own
    predicate (`supervisor seated?`) is False, proving part 2 is not vacuous.
    """
    from voicebridge.office import org as O

    cfg = _cfg(tmp_path, monkeypatch)
    b = _org(cfg)
    # A manager with NO room, supervising a leaf that IS at a desk.
    unseated = O.hire(cfg, "lead-nomad", seat_type="manager",
                      department_id=b["content"], room_id=None,
                      supervisor_id=b["ceo"], confirmed=True)["id"]
    leaf = O.hire(cfg, "w-nomad", seat_type="worker",
                  department_id=b["content"], room_id=b["desk_a"],
                  supervisor_id=unseated, confirmed=True)["id"]

    seats = _seats(cfg)
    plan = _floorplan_seats(cfg)

    # the leaf is drawn at a desk ...
    assert leaf in plan
    # ... while its supervisor is NOT, and the store accepted that — the gap.
    assert seats[unseated]["room_id"] is None
    assert unseated not in plan
    # the binding predicate part 2 checks would fail here — so part 2 is real
    with pytest.raises(AssertionError):
        assert unseated in plan, "supervisor is unseated"


# ============================================ 3. manager and leaves: one room
def test_manager_and_leaves_in_a_room_sit_on_distinct_desks(tmp_path,
                                                            monkeypatch):
    """When a manager shares a room with its leaves, each gets a distinct cell.

    The `desks-distinct` gate is a *room*-level property (no two seats on one
    cell). This is the narrower relationship: within one room holding a manager
    and that manager's own leaves, the manager's desk is not the desk of any of
    its reports. `tower.floorplan()` assigns a `slot` per seat in seat order, so
    equal slots inside one room means two agents drawn on each other.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    b = _org(cfg)
    seats = _seats(cfg)
    plan = _floorplan_seats(cfg)

    # the fixture: manager `lead-content` and its leaf `w1` share `desk-a`
    assert plan[b["lead"]]["room"]["slug"] == plan[b["worker"]]["room"]["slug"]
    assert plan[b["lead"]]["room"]["slug"] == "desk-a"

    # distinct cells within the shared room
    assert plan[b["lead"]]["slot"]["slot"] != plan[b["worker"]]["slot"]["slot"]

    # and generally: every room's seats occupy distinct slots
    from voicebridge.office import tower as T

    for floor in T.floorplan(cfg)["floors"]:
        for room in floor["rooms"]:
            slots = [s["slot"] for s in room["seats"]]
            assert len(slots) == len(set(slots)), (
                f"room {room['slug']!r} has two seats in one cell: {slots}")

    # the manager is NOT one of its own leaves — the C31 relationship assert
    live = {i: s for i, s in seats.items() if s["status"] != "terminated"}
    assert "w1" in _supervises(live, b["lead"])
    assert "lead-content" not in _supervises(live, b["lead"])


# ================================================= 4. the NEGATIVE case, made
def test_leaf_seated_away_from_its_manager_is_caught_then_a_correct_one_passes(
        tmp_path, monkeypatch):
    """The live defect: a leaf in a room whose manager is elsewhere.

    Build the WRONG arrangement (a leaf seated in `desk-b`, its manager seated in
    `desk-a`), run the binding checker, and assert it flags it. Then move the
    leaf back to its manager's room and assert the checker passes. If the
    negative case did not fail, the positive case would prove nothing.
    """
    from voicebridge.office import org as O

    cfg = _cfg(tmp_path, monkeypatch)
    b = _org(cfg)

    # WRONG: a leaf under `lead-content` but seated in the OTHER room (desk-b).
    stray = O.hire(cfg, "w-stray", seat_type="worker",
                   department_id=b["quality"], room_id=b["desk_b"],
                   supervisor_id=b["lead"], confirmed=True)["id"]

    def _violations(cfg):
        """Return the cross-room (leaf, supervisor) pairs — the binding check.

        A violation is a SEATED leaf whose SEATED supervisor is in a different
        room. It mirrors the rule docs/game-office/36-SEAT-BINDING.md states and
        is the thing no store code currently enforces.
        """
        seats = _seats(cfg)
        plan = _floorplan_seats(cfg)
        bad = []
        for agent_id, seat in seats.items():
            sup = seat["supervisor_id"]
            if sup is None or seat["status"] == "terminated":
                continue
            if agent_id in plan and sup in plan:
                if plan[agent_id]["room"]["id"] != plan[sup]["room"]["id"]:
                    bad.append((seat["slug"], seats[sup]["slug"],
                                plan[agent_id]["room"]["slug"],
                                plan[sup]["room"]["slug"]))
        return bad

    wrong = _violations(cfg)
    assert wrong, "the checker must flag a leaf seated away from its manager"
    assert any(pair[0] == "w-stray" for pair in wrong), wrong
    # `lead-content` sits in desk-a while its leaf w-stray sits in desk-b
    assert ("w-stray", "lead-content", "desk-b", "desk-a") in wrong

    # CORRECT: move the stray leaf into its manager's room. `seat_move` takes
    # the room's department as the seat's when one is not given (org.py:320),
    # so the binding's department half is repaired in the same move.
    O.seat_move(cfg, stray, room_id=b["desk_a"], confirmed=True)
    assert _violations(cfg) == [], "the corrected arrangement must pass"

    # and the moved leaf is now drawn in desk-a beside its manager
    plan = _floorplan_seats(cfg)
    assert plan[stray]["room"]["slug"] == "desk-a"
    assert plan[stray]["room"]["id"] == plan[b["lead"]]["room"]["id"]


# ======================================================= 5. the binding table
def test_binding_table_reports_the_live_wiring(tmp_path, monkeypatch, capsys):
    """Print the live binding as a table a reader can scan.

    seat | room | department | supervisor | supervisor's room. This is the
    deliverable that lets a reader see the wiring at a glance instead of
    rebuilding it from three tables.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    b = _org(cfg)
    seats = _seats(cfg)
    plan = _floorplan_seats(cfg)

    rows = []
    for agent_id, seat in sorted(seats.items()):
        if seat["status"] == "terminated":
            continue
        room = plan.get(agent_id, {}).get("room")
        sup_id = seat["supervisor_id"]
        sup = seats.get(sup_id) if sup_id is not None else None
        sup_room = plan.get(sup_id, {}).get("room") if sup_id is not None else None
        rows.append({
            "seat": seat["slug"],
            "room": room["slug"] if room else "-- not at a desk --",
            "department": seat["department_slug"] or "NULL",
            "supervisor": sup["slug"] if sup else "(owner)",
            "supervisor_room": (sup_room["slug"] if sup_room else
                                ("-- not at a desk --" if sup else "n/a")),
        })

    header = f"{'seat':<14} {'room':<18} {'department':<12} {'supervisor':<14} {'sup_room':<18}"
    print()
    print("SEAT BINDING — live wiring")
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['seat']:<14} {r['room']:<18} {r['department']:<12} "
              f"{r['supervisor']:<14} {r['supervisor_room']:<18}")

    # the table covers every live seat and names the fixture's key seats
    assert len(rows) == len([s for s in seats.values()
                             if s["status"] != "terminated"])
    by_slug = {r["seat"]: r for r in rows}
    assert by_slug["lead-content"]["room"] == "desk-a"
    assert by_slug["lead-content"]["supervisor_room"] == "-- not at a desk --"
    assert by_slug["w1"]["supervisor"] == "lead-content"
    assert by_slug["w1"]["supervisor_room"] == "desk-a"
    out = capsys.readouterr().out
    assert "SEAT BINDING" in out
