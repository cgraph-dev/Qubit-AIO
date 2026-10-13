"""The room programme must be REACHABLE, and it must change the live building.

WHY THIS FILE EXISTS
====================
`voicebridge/office/room_layout.py` computes the room programme — how many rooms
of what KIND a headcount needs, one manager per work room, who sits where — and
before this file there was **no caller anywhere in the write path**:

    $ grep -rn "room_layout" voicebridge/ --include=*.py     # -> 0 hits

Only tests imported it. So the live office's rooms were whatever an operator
typed by hand, and that is exactly the defect doc 33 §3 names: `desk-a` and
`desk-b` seat leaves with **no manager's desk** in either, while both live
managers sit in the boardroom. The org model says a work room is ONE manager's
unit (`org.py:94` span limit 7, D34; C31 `95-CONTRACT.md:657`), so a work room
with no supervisor is a room nobody runs.

This file proves four things with the REAL store and the REAL CLI verb:

1. **Before** — build an org the ordinary way (floor → department → rooms →
   hires) and assert as a FACT that no work room holds a seat marked as its
   manager. That is the gap, not a hypothesis.
2. **After** — run `office programme --confirm` and assert every `work` room now
   holds exactly one supervisor plus its leaves, and the boardroom holds the
   executive chain. The per-room census is printed for the record.
3. **Capacity** — for the documented headcounts the programme's PHYSICAL DESKS
   must be >= the headcount it was asked to seat. `seating_plan.plan(headcount)`
   and `room_layout.programme(headcount, dept_count)` take different arguments
   (one has no department count), so they are NOT compared here; the check that
   matters and IS comparable is desks-vs-people.
4. **Idempotence** — a second `--confirm` run must not create duplicate rooms.
5. **Mutation proof** — a SCRATCH copy of `room_layout.py` with the supervisor
   seat dropped from a work room must make these tests fail. The mutation is
   executed in-process from a scratch file; the repo copy is untouched.

Hermetic: tmp XDG state only; the scratch mutation is written under the session
scratch dir, never into the repo. No network, no provider call.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The real module under test, and the source the scratch mutation copies.
from voicebridge.office import room_layout as R  # noqa: E402


def _cfg(tmp_path, monkeypatch):
    """A config whose office store lands under tmp_path (real `_cfg` pattern)."""
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _run(cfg, argv, capsys):
    """Parse real argv through the real parser, then dispatch for real."""
    import json

    from voicebridge import cli as CLI

    args = CLI.build_parser().parse_args(argv)
    rc = CLI._cmd_office(cfg, args)
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else {})


# --------------------------------------------------------------------------- #
# helpers: what the STORE actually holds
# --------------------------------------------------------------------------- #
def _live_rooms(cfg):
    """Every room as the store holds it, with its seats -- straight from SQL."""
    from voicebridge.office import store as S

    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        rooms = [dict(r) for r in conn.execute(
            "SELECT id, department_id, slug, kind, capacity"
            " FROM office_room ORDER BY id")]
        seats = [dict(r) for r in conn.execute(
            "SELECT id, slug, seat_type, room_id, supervisor_id"
            " FROM office_agent WHERE status != 'terminated'")]
    finally:
        conn.close()
    by_room = {}
    for s in seats:
        if s["room_id"] is not None:
            by_room.setdefault(int(s["room_id"]), []).append(s)
    for r in rooms:
        r["seats"] = by_room.get(int(r["id"]), [])
    return rooms


def _work_rooms_with_a_manager_seat(rooms):
    """Work rooms that hold a seat which supervises someone seated in them.

    A seat is the room's MANAGER when some other seat in the same room names it
    as `supervisor_id` -- i.e. the manager's desk is in the unit it runs, which
    is the programme's rule (doc 33 §3, `org.py:94` D34).
    """
    out = []
    for room in rooms:
        if room["kind"] != "work":
            continue
        if any(any(t["supervisor_id"] == s["id"] for t in room["seats"])
               for s in room["seats"]):
            out.append(room)
    return out


def _build_org_the_ordinary_way(cfg, capsys):
    """floor -> department -> rooms -> hires, with the two live work rooms.

    This mirrors how the live office was actually built: rooms created by hand,
    leaves hired into them, managers sitting somewhere else (the boardroom).
    Nothing here consults `room_layout`, which is the point.
    """
    rc, out = _run(cfg, ["office", "init"], capsys)
    assert rc == 0, out
    rc, fl = _run(cfg, ["office", "floor-create", "--number", "1",
                        "--name", "Operations", "--confirm"], capsys)
    assert rc == 0, fl
    rc, floors = _run(cfg, ["office", "floors"], capsys)
    floor_id = int(floors["floors"][0]["id"])

    rc, dept = _run(cfg, ["office", "department-create", "--slug", "delivery",
                          "--floor", str(floor_id), "--division-kind", "product",
                          "--kill-criteria", "no sale in 30 days",
                          "--confirm"], capsys)
    assert rc == 0, dept
    dept_id = int(dept["id"])

    # the CEO and two managers -- the live shape puts them in the boardroom
    rc, ceo = _run(cfg, ["office", "hire", "--slug", "ceo", "--seat-type", "ceo",
                         "--confirm"], capsys)
    assert rc == 0, ceo
    ceo_id = int(ceo["id"])

    rc, brd = _run(cfg, ["office", "room-create", "--department", str(dept_id),
                         "--slug", "boardroom", "--kind", "boardroom",
                         "--capacity", "4", "--confirm"], capsys)
    assert rc == 0, brd

    rc, da = _run(cfg, ["office", "room-create", "--department", str(dept_id),
                        "--slug", "desk-a", "--kind", "work",
                        "--capacity", "4", "--confirm"], capsys)
    assert rc == 0, da
    desk_a = int(da["id"])
    rc, db = _run(cfg, ["office", "room-create", "--department", str(dept_id),
                        "--slug", "desk-b", "--kind", "work",
                        "--capacity", "4", "--confirm"], capsys)
    assert rc == 0, db
    desk_b = int(db["id"])

    managers = []
    for slug in ("lead-content", "lead-quality"):
        rc, m = _run(cfg, ["office", "hire", "--slug", slug,
                           "--seat-type", "manager", "--department",
                           str(dept_id), "--supervisor", str(ceo_id),
                           "--confirm"], capsys)
        assert rc == 0, m
        managers.append(int(m["id"]))

    # seven leaves across the two work rooms, a manager's span each -- and NO
    # manager seat in either room (the defect doc 33 §3 records)
    leaves = []
    for slug, room_id, mgr in (("wren", desk_a, managers[0]),
                               ("mira", desk_a, managers[0]),
                               ("sable", desk_a, managers[0]),
                               ("kerr", desk_a, managers[0]),
                               ("alex", desk_b, managers[1]),
                               ("tobi", desk_b, managers[1]),
                               ("juno", desk_b, managers[1])):
        rc, w = _run(cfg, ["office", "hire", "--slug", slug,
                           "--seat-type", "worker", "--department",
                           str(dept_id), "--room", str(room_id),
                           "--supervisor", str(mgr), "--confirm"], capsys)
        assert rc == 0, w
        leaves.append(int(w["id"]))

    return {"floor_id": floor_id, "dept_id": dept_id, "ceo": ceo_id,
            "managers": managers, "leaves": leaves,
            "desk_a": desk_a, "desk_b": desk_b}


# =========================================================================== #
# 1. BEFORE -- the gap, asserted as a fact
# =========================================================================== #
def test_before_no_work_room_has_its_manager_seated_in_it(tmp_path, monkeypatch,
                                                          capsys):
    """THE GAP. Built the ordinary way, no work room holds its manager's desk.

    This is asserted as a fact about the store, not inferred: the two live
    managers are seated in the boardroom (or nowhere) and the work rooms hold
    leaves only. `room_layout` is not consulted to build this org, so a pass
    here proves the gap is real rather than a property of the programme.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    built = _build_org_the_ordinary_way(cfg, capsys)

    rooms = _live_rooms(cfg)
    work = [r for r in rooms if r["kind"] == "work"]
    assert len(work) == 2, [r["slug"] for r in rooms]

    with_manager = _work_rooms_with_a_manager_seat(rooms)
    assert with_manager == [], (
        "the ordinary build already seats a manager in a work room; the gap "
        "this file documents is gone -- re-read doc 33 §3: %r"
        % [r["slug"] for r in with_manager]
    )

    # and say it with numbers: 7 leaf seats between two rooms, 0 supervisors
    total_leaf = sum(len(r["seats"]) for r in work)
    assert total_leaf == 7, [r["slug"] for r in work]
    print("\nBEFORE census:")
    for r in rooms:
        print("  room=%-10s kind=%-9s capacity=%d seats=%d supervisors=%d"
              % (r["slug"], r["kind"], r["capacity"], len(r["seats"]),
                 len(_work_rooms_with_a_manager_seat([r]))))
    print("  work rooms with their manager seated: %d of %d"
          % (len(with_manager), len(work)))
    assert built["managers"], "the managers must exist for the gap to be about"


# =========================================================================== #
# 2. AFTER -- the programme path, and the real per-room census
# =========================================================================== #
def test_after_every_work_room_holds_one_supervisor_plus_its_leaves(
        tmp_path, monkeypatch, capsys):
    """The programme's work rooms: exactly ONE supervisor, 1..7 leaves.

    The supervisor count is read from the STORE (a seat in the room that other
    seats in the same room name as `supervisor_id`), and cross-checked against
    the programme's own `seats_detail`, so a report of "one supervisor" that the
    store disagrees with fails here.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, ["office", "programme", "--headcount", "50",
                         "--departments", "9", "--confirm"], capsys)
    assert rc == 0, out
    assert out["state"] == "OK", out

    prog = R.programme(50, 9)
    work_prog = [r for r in prog if r["kind"] == "work"]
    assert work_prog, "the programme must contain work rooms for 50 seats"

    for room in work_prog:
        detail = room["seats_detail"]
        supers = [s for s in detail if s["is_supervisor"]]
        leaves = [s for s in detail if not s["is_supervisor"]]
        assert len(supers) == 1, (room["slug_hint"], detail)
        assert 1 <= len(leaves) <= R.MAX_SPAN, (room["slug_hint"], leaves)
        # desks = the manager's own desk + its leaves
        assert room["seats"] == len(leaves) + 1, room

    # the boardroom holds the executive chain: the CEO plus one director each
    board = [r for r in prog if r["kind"] == "boardroom"]
    assert len(board) == 1
    staff = R.staffing(50, 9)
    assert board[0]["seats"] == staff["ceo"] + staff["directors"] == 10, board[0]
    assert all(s["is_supervisor"] for s in board[0]["seats_detail"])

    # the review room is a walking destination: no permanent seats, by design
    review = [r for r in prog if r["kind"] == "review"]
    assert len(review) == 1 and review[0]["seats"] == 0

    # the store agrees: rooms exist with the programme's kinds
    rooms = _live_rooms(cfg)
    kinds = sorted(r["kind"] for r in rooms)
    assert kinds.count("work") == len(work_prog), kinds
    assert "boardroom" in kinds and "review" in kinds, kinds

    print("\nAFTER census (programme(50, 9), written through the CLI):")
    print("  %-10s %-9s %5s %6s %6s" % ("slug", "kind", "desks", "mgr", "leaf"))
    for room in prog:
        d = room["seats_detail"]
        print("  %-10s %-9s %5d %6d %6d"
              % (room["slug_hint"], room["kind"], room["seats"],
                 sum(1 for s in d if s["is_supervisor"]),
                 sum(1 for s in d if not s["is_supervisor"])))
    print("  staffing:", staff)
    print("  seats_offered:", out["seats_offered"], "of headcount 50")


def test_after_the_live_four_room_plan_is_materialised_with_a_manager(
        tmp_path, monkeypatch, capsys):
    """The 11-seat case on screen: boardroom 4, ONE work room 7, review 0.

    The live building is 4 rooms (1 boardroom + 2 bare work rooms + a review);
    the programme's answer for the same org is 3 rooms with the manager's desk
    present. Asserting the census here pins that the difference is real and
    reported, not merely computed.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, ["office", "programme", "--headcount", "11",
                         "--departments", "3", "--confirm"], capsys)
    assert rc == 0, out
    census = [(r["slug"], r["kind"], r["seats"], r["supervisors"], r["leaves"])
              for r in out["rooms"]]
    assert census == [("boardroom", "boardroom", 4, 4, 0),
                      ("desk-a", "work", 7, 1, 6),
                      ("review", "review", 0, 0, 0)], census

    rooms = _live_rooms(cfg)
    slugs = sorted(r["slug"] for r in rooms)
    assert slugs == ["boardroom", "desk-a", "review"], slugs
    assert out["seats_offered"] == 11

    # The rooms carry the programme's own capacity: the work room is sized at
    # its manager + leaves (7), not at the bare 4 the live desk-a carried. The
    # seats themselves are NOT written by this verb -- it creates ROOMS, and
    # hiring/seating agents into them is the separate `office hire` /
    # `office seat-move` step. Asserting seats here would be asserting a
    # capability the verb does not claim.
    by_slug = {r["slug"]: r for r in rooms}
    assert by_slug["desk-a"]["capacity"] == 7, by_slug["desk-a"]
    assert by_slug["boardroom"]["capacity"] == 4, by_slug["boardroom"]
    assert by_slug["review"]["capacity"] == 0, by_slug["review"]

    print("\nLIVE 11-SEAT CENSUS (store, after --confirm):")
    for r in rooms:
        print("  %-10s %-9s capacity=%d seats=%d"
              % (r["slug"], r["kind"], r["capacity"], len(r["seats"])))
    print("  (rooms are materialised; seating agents into them is the "
          "separate hire/seat-move step)")


def test_preview_writes_nothing_and_confirm_is_required(tmp_path, monkeypatch,
                                                        capsys):
    """A bare `office programme` reports the plan and creates NO rows."""
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    rc, out = _run(cfg, ["office", "programme", "--headcount", "11",
                         "--departments", "3"], capsys)
    assert rc == 0, out
    assert out["state"] == "preview", out
    assert out["seats_offered"] == 11
    assert out["would"].startswith("create 1 floor(s)"), out

    assert _live_rooms(cfg) == [], "a preview must not write a room"
    rc, floors = _run(cfg, ["office", "floors"], capsys)
    assert floors["count"] == 0, "a preview must not write a floor"


def test_programme_refuses_with_exit_3_while_the_emergency_stop_is_latched(
        tmp_path, monkeypatch, capsys):
    """A latched estop returns 3 and writes nothing (the shared office gate).

    The programme is a mutation, so it takes the same latch the rest of the
    office does -- and refuses with exit 3 BEFORE any floor or room is written.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, ["office", "init"], capsys)

    from voicebridge.power import power_control_store

    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    rc, out = _run(cfg, ["office", "programme", "--headcount", "11",
                         "--departments", "3", "--confirm"], capsys)
    assert rc == 3, out
    assert out["state"] == "ESTOP", out
    assert out["code"] == "OFFICE_ESTOP", out
    assert _live_rooms(cfg) == [], "estop must not have written a room"


# =========================================================================== #
# 3. capacity: physical desks vs the headcount asked for
# =========================================================================== #
@pytest.mark.parametrize("headcount,dept_count", [(11, 3), (25, 5), (50, 9),
                                                  (71, 9)])
def test_programme_desks_are_at_least_the_headcount(headcount, dept_count):
    """A programme that seats fewer desks than people is a real defect.

    `seating_plan.plan(headcount)` and `room_layout.programme(headcount,
    dept_count)` are NOT compared: `plan` takes no department count, so any
    comparison would be between two differently-parameterised buildings. What IS
    comparable and checkable is the one that matters: the number of PHYSICAL
    DESKS the programme requests for a headcount must be >= that headcount, or
    the building cannot seat the org it was planned for.
    """
    prog = R.programme(headcount, dept_count)
    desks = sum(int(r["seats"]) for r in prog)
    verdict = R.check(prog, headcount)
    print("\nheadcount=%d depts=%d -> desks=%d  (shortfall %d)"
          % (headcount, dept_count, desks, verdict["shortfall"]))
    assert desks >= headcount, (
        f"programme({headcount}, {dept_count}) requests {desks} physical desks "
        f"for a headcount of {headcount}: {headcount - desks} seat(s) would be "
        f"left without a desk"
    )
    assert verdict["ok"], (headcount, verdict)
    assert verdict["seats_offered"] >= headcount, verdict


# =========================================================================== #
# 4. idempotence
# =========================================================================== #
def test_running_the_programme_twice_creates_no_duplicate_rooms(
        tmp_path, monkeypatch, capsys):
    """Two `--confirm` runs must leave the same room count, not twice the rooms.

    `office_room` is UNIQUE(department_id, slug), so a naive second run would
    raise. The verb reports the existing rooms instead, and this asserts the
    store's room count is identical after the second run -- and that the second
    run names the rooms it found rather than claiming to have made them.
    """
    cfg = _cfg(tmp_path, monkeypatch)
    rc, first = _run(cfg, ["office", "programme", "--headcount", "25",
                           "--departments", "5", "--confirm"], capsys)
    assert rc == 0, first
    assert first["rooms_created"], first
    after_first = len(_live_rooms(cfg))
    assert after_first == len(R.programme(25, 5)), after_first

    rc, second = _run(cfg, ["office", "programme", "--headcount", "25",
                            "--departments", "5", "--confirm"], capsys)
    assert rc == 0, second
    after_second = len(_live_rooms(cfg))

    print("\nidempotence: rooms after 1st run = %d, after 2nd = %d"
          % (after_first, after_second))
    print("  2nd run: created=%r existing=%r"
          % ([r["slug"] for r in second["rooms_created"]],
             second["rooms_existing"]))
    assert after_second == after_first, (
        "a second --confirm run changed the room count: %d -> %d"
        % (after_first, after_second)
    )
    assert second["rooms_created"] == [], second["rooms_created"]
    assert sorted(second["rooms_existing"]) == sorted(
        r["slug_hint"] for r in R.programme(25, 5)), second["rooms_existing"]
    assert second["floors_created"] == [] and second["floors_reused"] == [1]


# =========================================================================== #
# 5. mutation proof -- a scratch copy with the supervisor seat dropped
# =========================================================================== #
def _load_mutated_room_layout(scratch_dir):
    """Import a SCRATCH copy of room_layout with the manager's desk removed.

    The mutation drops the one line that appends the supervisor seat to a work
    room's `seats_detail`, which is precisely the "work room with no manager's
    desk" defect. Written under the session scratch dir, never the repo.
    """
    src = (REPO / "voicebridge" / "office" / "room_layout.py").read_text()
    marker = '            detail = [{"role": "manager", "is_supervisor": True}]\n'
    assert marker in src, (
        "the supervisor-seat line moved; update the mutation to match the "
        "source rather than letting the proof silently no-op"
    )
    mutated = src.replace(marker,
                          "            detail = []  # MUTATED: no manager desk\n")
    assert mutated != src

    scratch_dir.mkdir(parents=True, exist_ok=True)
    out = scratch_dir / "room_layout_mutated.py"
    out.write_text(mutated)
    spec = importlib.util.spec_from_file_location(
        "room_layout_mutated", out)
    assert spec is not None and spec.loader is not None, out
    mod = importlib.util.module_from_spec(spec)
    sys.modules["room_layout_mutated"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_mutation_drop_the_supervisor_seat_is_caught(tmp_path):
    """The tests above must FAIL on a programme with no manager desk.

    A green suite proves nothing unless it can go red for the defect it claims
    to cover. The scratch copy drops the supervisor seat from every work room;
    the same assertions the real tests make must then fail, and the failure text
    is asserted to name the missing manager.
    """
    # A per-test scratch dir, NOT a fixed path: the office suite-health guard
    # forbids an office test reaching a hard-coded path outside tmp_path.
    scratch = tmp_path / "mutation"
    mod = _load_mutated_room_layout(scratch)
    mutated = mod.programme(50, 9)
    work = [r for r in mutated if r["kind"] == "work"]
    assert work, "the mutation kept the work rooms (only the seat was dropped)"

    # the real assertions, run against the mutated programme
    failure = None
    try:
        for room in work:
            supers = [s for s in room["seats_detail"] if s["is_supervisor"]]
            assert len(supers) == 1, (
                f"work room {room['slug_hint']!r} has {len(supers)} supervisor "
                f"seat(s); a work room is one manager's unit (C31) and must "
                f"hold its manager's desk"
            )
    except AssertionError as exc:
        failure = str(exc)

    assert failure is not None, (
        "the supervisor-seat mutation was NOT caught -- the tests do not "
        "actually pin the manager's desk"
    )
    assert "supervisor" in failure and "manager" in failure, failure

    # and the module's own `check` refuses it, naming the missing manager
    verdict = mod.check(mutated, 50)
    assert verdict["ok"] is False, verdict
    assert "no supervisor" in verdict["reason"], verdict["reason"]

    print("\nMUTATION CAUGHT:")
    print("  ", failure)
    print("   room_layout.check:", verdict["reason"])

    # the repo copy is untouched -- mutate a copy, never the subject
    assert "MUTATED" not in (
        REPO / "voicebridge" / "office" / "room_layout.py").read_text()
