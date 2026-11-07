"""Prove the agency plan actually hires into a real office store.

Builds a throwaway state dir, then executes the documented integration point in
order: floor -> department -> rooms -> seats, through the real ``org`` API so
every chain (C30), span (D34) and department-ownership (D35) rule is enforced by
the same code the live office uses. Then reads the org tree back.

Run: .venv/bin/python <this file>
"""
import sys
import tempfile
from pathlib import Path

# Put the worktree root FIRST: the venv's editable install points at the main
# repo, so without this the probe would hire into the wrong tree's modules.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from voicebridge.office import agency_org, org, store  # noqa: E402

assert Path(agency_org.__file__).resolve().parent == ROOT / "voicebridge" / "office", (
    f"imported agency_org from {agency_org.__file__}, not the worktree")


class Cfg:
    def __init__(self, root): self._root = root
    def state_dir(self): return self._root


def hire_plan(cfg, plan):
    """Execute `plan_building` against the store. Floor 0 is the executive floor."""
    # The building is 18 division floors plus the executive floor the boardroom
    # and review room sit on. A room needs a department and a department needs a
    # floor; the executive floor's department is created for the boardroom.
    # The executive floor is the top one (``floor_create`` allows 1..60; there is
    # no floor 0). The boardroom and review room sit there.
    exec_number = max(f["floor"] for f in plan["programme"]) 
    exec_floor = org.floor_create(cfg, exec_number, "Executive", role="executive",
                                  confirmed=True)
    exec_dept = org.department_create(
        cfg, "executive", name="Executive", floor_id=exec_floor["id"],
        division_kind="meta", template="agency",
        charter="the executive chain and the review room",
        kill_criteria="retires with the org", confirmed=True)
    floors = {f["number"]: f for f in plan["floors"]}
    dept_of = {"": exec_dept["id"]}
    for number, floor in floors.items():
        created = org.floor_create(cfg, number, floor["name"],
                                   role=floor["role"], confirmed=True)
        dept = org.department_create(
            cfg, floor["slug"], name=floor["name"], floor_id=created["id"],
            division_kind=agency_org.division_kind_for(floor["slug"]),
            template="agency", charter=f"{floor['name']} division as ported",
            kill_criteria="division retires when its personas are retired",
            confirmed=True)
        dept_of[floor["slug"]] = dept["id"]

    # rooms: group the programme by division ("" = the executive floor)
    room_ids = {}
    for room in plan["programme"]:
        division = room.get("division") or ""
        if division not in dept_of:
            continue
        rid = org.room_create(cfg, dept_of[division], room["slug_hint"],
                              kind=room["kind"], capacity=room["seats"],
                              confirmed=True)
        room_ids[(division, room["slug_hint"])] = rid["id"]

    # seats, supervisor-first so every supervisor already exists
    by_slug = {s["slug"]: s for s in plan["seats"]}
    order, done = [], set()

    def visit(seat):
        if seat["slug"] in done:
            return
        sup = seat["supervisor"]
        if sup is not None:
            visit(by_slug[sup])
        order.append(seat)
        done.add(seat["slug"])

    for seat in plan["seats"]:
        visit(seat)

    ids = {}
    for seat in order:
        rid = room_ids.get((seat["division"], seat.get("room") or ""))
        hired = org.hire(
            cfg, seat["slug"], name=seat["name"], seat_type=seat["seat_type"],
            department_id=dept_of.get(seat["division"]),
            room_id=rid,
            supervisor_id=ids.get(seat["supervisor"]) if seat["supervisor"] else None,
            portfolio=seat.get("portfolio", ""),
            confirmed=True)
        ids[seat["slug"]] = hired["id"]
    return ids


def main() -> int:
    plan = agency_org.plan_building()
    assert plan["ok"], plan["check"]
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Cfg(Path(tmp))
        ids = hire_plan(cfg, plan)
        tree = org.tree(cfg)
        print(f"seats hired: {len(ids)}   tree seats: {tree['seats']}")
        print(f"seat types: {tree['counts']}")
        print(f"roots: {tree['roots']}")
        print(f"over_span: {tree['over_span']}")
        floors = org.floors(cfg)
        print(f"floors: {floors['count']} (departments per floor: "
              f"{sorted({f['departments'] for f in floors['floors']})})")
        ok = (tree["seats"] == len(plan["seats"])
              and not tree["over_span"]
              and len(tree["roots"]) == 1)
        print("VERDICT:", "OK" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
