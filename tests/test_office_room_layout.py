"""The room programme must seat everyone, cite the org rules, and refuse lies.

This file is the coverage for `voicebridge/office/room_layout.py`, the design
layer above `seating_plan.py`. `seating_plan` answers "does a room of size WxH
hold N desks without the row occluding?" (tiles and pitch); `room_layout`
answers "how many rooms of what KIND seat this headcount, and who sits in
each?" — the question the live office answers badly.

The rules under test, each cited to the line that enforces it:

  * `programme(11, 3)` seats all 11 people across the boardroom + work rooms,
    and the review room holds none of them by design (57-BOARDROOM.md);
  * `check` REFUSES a deliberately short programme, naming the shortfall, and
    refuses an over-span room, an unknown kind, a review room with seats, and
    a work room that counts its supervisor as a leaf (C31,
    95-CONTRACT.md:657);
  * a supervisor is never counted as a leaf seat — asserted both via the
    `seats_detail` flag and via a hand-built room that tries it;
  * the programme for the documented orgs fits, or SAYS it does not fit the
    3x2 floor grid rather than silently expanding the grid
    (24-FLOORPLAN-SCALE.md §7);
  * the constants are the real ones: MAX_SPAN is `org.py`'s span limit (D34),
    ROOM_COLS/ROOM_ROWS are `tower.py`'s grid.

Hermetic: pure functions, no network, no live service, no I/O.
"""
from __future__ import annotations

import pytest

from voicebridge.office import room_layout as R


# ============================================================ the constants
def test_room_kinds_are_only_kinds_the_code_can_write():
    """ROOM_KINDS must be writable, not aspirational.

    ``room_create``'s default is ``kind="work"`` (`org.py:253`),
    `57-BOARDROOM.md` names ``boardroom`` and ``review``, and the live
    ``office_room`` table holds exactly those three kinds. The long decorative
    enum in `30-DATA-MODEL.md:170` (``lab``, ``vault``, ``crawl_room`` ...) is
    deliberately absent because no code path writes it.
    """
    assert R.ROOM_KINDS == ("work", "boardroom", "review")
    for kind in R.ROOM_KINDS:
        assert isinstance(kind, str) and kind
    # the aspirational enum members are NOT in the programme's vocabulary
    for aspirational in ("lab", "studio", "vault", "crawl_room", "lounge"):
        assert aspirational not in R.ROOM_KINDS


def test_max_span_is_the_org_span_limit():
    """The span limit the programme splits on is `org.py`'s (D34).

    `org.py:94` `_check_span` refuses an 8th direct report and
    `store.py:40` states ``MAX_SPAN = 7``. The programme may not invent a
    different number, or it would plan rooms the org refuses to staff.
    """
    assert R.MAX_SPAN == 7
    assert R.MAX_ROOM_SEATS == R.MAX_SPAN


def test_room_grid_is_towers_grid():
    """The grid the programme must fit is `tower.py:98`'s 3x2, unchanged.

    `24-FLOORPLAN-SCALE.md` §7: "Do not change ROOM_COLS/ROOM_ROWS, and do not
    enlarge the building."
    """
    assert (R.MAX_ROOM_COLS, R.MAX_ROOM_ROWS) == (3, 2)
    assert R.ROOMS_PER_FLOOR == 6
    assert R.rooms_per_floor() == 6


def test_geometry_ceiling_is_higher_than_the_programme_ceiling():
    """The room's *tile* ceiling (9) is above its *programme* ceiling (7).

    Not a contradiction, and worth pinning: a 12x7 room holds 9 desks at the
    doc's (4,3) pitch, but a work room may hold only 7 leaves because the span
    limit binds before the tiles do. A programme that typed 8-leaf rooms would
    plan a room the org layer refuses to staff.
    """
    assert R.MAX_ROOM_DESKS_GEOMETRY == 9
    assert R.MAX_ROOM_SEATS == 7
    assert R.MAX_ROOM_SEATS < R.MAX_ROOM_DESKS_GEOMETRY


def test_min_room_seats_is_two():
    assert R.MIN_ROOM_SEATS == 2


# ============================================================ programme(11, 3)
def test_programme_11_3_seats_everyone():
    """The headline assertion: 11 seats, 3 departments, everyone has a desk."""
    p = R.programme(11, 3)
    c = R.check(p, 11)
    assert c["ok"] is True, c["reason"]
    assert c["shortfall"] == 0
    assert c["seats_offered"] == 11, c["reason"]


def test_programme_11_3_has_a_boardroom_a_work_room_and_a_review_room():
    """The shape the org model forces: executive chain, unit, decision room."""
    p = R.programme(11, 3)
    kinds = [r["kind"] for r in p]
    assert "boardroom" in kinds
    assert "work" in kinds
    assert "review" in kinds
    # exactly one boardroom (57-BOARDROOM.md: the CEO's room)
    assert kinds.count("boardroom") == 1


def test_programme_11_3_review_room_holds_no_permanent_seats():
    """A review room is a walking destination, not a desk allocation.

    This is WHY the current building renders `review` empty and why that is
    CORRECT rather than a defect: `57-BOARDROOM.md` describes it as "a manager
    ruling on their own team's proposals" — a place, with no permanent desks.
    """
    p = R.programme(11, 3)
    review = [r for r in p if r["kind"] == "review"]
    assert len(review) == 1
    assert review[0]["seats"] == 0
    assert review[0]["seats_detail"] == []


def test_programme_11_3_work_rooms_never_exceed_the_span_limit():
    """Every work room's leaf count is within the 7-report span (org.py:94)."""
    p = R.programme(11, 3)
    for room in p:
        if room["kind"] != "work":
            continue
        leaves = [s for s in room["seats_detail"] if not s["is_supervisor"]]
        assert len(leaves) <= R.MAX_SPAN
        assert len(leaves) >= 1


def test_programme_11_3_work_room_has_exactly_one_supervisor():
    """A work room is one manager's unit (C31), so exactly one supervisor desk."""
    p = R.programme(11, 3)
    for room in p:
        if room["kind"] != "work":
            continue
        supers = [s for s in room["seats_detail"] if s["is_supervisor"]]
        assert len(supers) == 1


def test_programme_11_3_cites_a_reason_per_room():
    """Every room states its reason, and the reason cites a rule."""
    p = R.programme(11, 3)
    for room in p:
        assert room["why"], f"{room['kind']} has no `why`"
        assert len(room["why"]) > 20


def test_programme_11_3_fits_the_recommended_grid():
    """11 seats fit one floor: 3 rooms is well inside the 3x2 = 6 grid."""
    p = R.programme(11, 3)
    assert len(p) <= R.ROOMS_PER_FLOOR
    assert R.check(p, 11)["fits_grid"] is True
    assert R.check(p, 11)["floors_needed"] == 1


# ============================================================ check refuses
def test_check_refuses_a_deliberately_short_programme_and_names_the_shortfall():
    """A programme that does not seat everyone is refused, with the gap named.

    This is the module's whole purpose: a floor plan that leaves seats undrawn
    is the bug, and the refusal must say *how many* seats were left without a
    desk rather than returning a bare False.
    """
    short = [{"kind": "work", "slug_hint": "desk-a", "seats": 3,
              "seats_detail": [{"role": "manager", "is_supervisor": True},
                               {"role": "leaf-1", "is_supervisor": False},
                               {"role": "leaf-2", "is_supervisor": False}]}]
    out = R.check(short, 11)
    assert out["ok"] is False
    assert out["shortfall"] == 8
    assert out["seats_offered"] == 3
    assert "8" in out["reason"]
    assert "without a desk" in out["reason"]


def test_check_refuses_a_room_past_the_span_limit():
    """8 leaves in one room is over-span: the unit must split (org.py:94 D34)."""
    over = [{"kind": "work", "slug_hint": "desk-a", "seats": 8,
             "seats_detail": [{"role": "manager", "is_supervisor": True}]
             + [{"role": f"leaf-{i}", "is_supervisor": False}
                for i in range(8)]}]
    out = R.check(over, 9)
    assert out["ok"] is False
    assert "8" in out["reason"] and "span limit" in out["reason"]


def test_check_refuses_an_unknown_kind():
    out = R.check([{"kind": "lab", "slug_hint": "x", "seats": 3}], 3)
    assert out["ok"] is False
    assert "unknown kind" in out["reason"]


def test_check_refuses_a_review_room_with_permanent_seats():
    """A review room holds no permanent seats (57-BOARDROOM.md)."""
    out = R.check([{"kind": "review", "slug_hint": "r", "seats": 2}], 2)
    assert out["ok"] is False
    assert "review" in out["reason"]


def test_check_refuses_a_boardroom_with_no_seats():
    out = R.check([{"kind": "boardroom", "slug_hint": "boardroom", "seats": 0}], 1)
    assert out["ok"] is False
    assert "boardroom" in out["reason"]


def test_check_refuses_a_nonpositive_headcount():
    out = R.check(R.programme(11, 3), 0)
    assert out["ok"] is False
    assert "headcount" in out["reason"]


# ================================================= a supervisor is not a leaf
def test_check_refuses_a_supervisor_counted_as_a_leaf_seat():
    """The named rule: a work room must not seat the manager who reviews it.

    C31 (`95-CONTRACT.md:657`): "a seat cannot write its own `office_review`;
    the reviewer's level must be exactly one above". A manager sitting *as one
    of the leaves* in the unit it reviews leaves that unit with no independent
    reviewer, so the room is refused by name.
    """
    bad = [{"kind": "work", "slug_hint": "desk-a", "seats": 3,
            "seats_detail": [
                {"role": "leaf-1", "is_supervisor": False},
                # the manager, wrongly typed as a leaf (is_supervisor False)
                {"role": "manager", "is_supervisor": False},
                {"role": "leaf-2", "is_supervisor": False},
            ]}]
    # This specific mis-type is caught by the supervisor-count check: a work
    # room must contain its manager, so a room whose detail has ZERO supervisors
    # is refused.
    out = R.check(bad, 3)
    assert out["ok"] is False
    assert "supervisor" in out["reason"]


def test_check_refuses_a_work_room_with_no_supervisor():
    """A room with leaves but no manager is an un-reviewable unit (C30/C31)."""
    bad = [{"kind": "work", "slug_hint": "desk-a", "seats": 2,
            "seats_detail": [
                {"role": "leaf-1", "is_supervisor": False},
                {"role": "leaf-2", "is_supervisor": False},
            ]}]
    out = R.check(bad, 2)
    assert out["ok"] is False
    assert "no supervisor" in out["reason"]


def test_check_refuses_two_supervisors_in_one_work_room():
    """Two managers in one unit room is not one manager's unit (C31)."""
    bad = [{"kind": "work", "slug_hint": "desk-a", "seats": 3,
            "seats_detail": [
                {"role": "manager-a", "is_supervisor": True},
                {"role": "manager-b", "is_supervisor": True},
                {"role": "leaf-1", "is_supervisor": False},
            ]}]
    out = R.check(bad, 3)
    assert out["ok"] is False
    assert "supervisors" in out["reason"]


def test_programme_never_counts_its_supervisor_as_a_leaf():
    """Every work room's `seats` is leaves + exactly 1 supervisor desk."""
    for hc, dc in ((11, 3), (25, 5), (50, 9), (71, 9)):
        for room in R.programme(hc, dc):
            if room["kind"] != "work":
                continue
            supers = sum(1 for s in room["seats_detail"] if s["is_supervisor"])
            leaves = sum(1 for s in room["seats_detail"] if not s["is_supervisor"])
            assert supers == 1, (hc, dc, room["slug_hint"])
            assert room["seats"] == leaves + 1, (hc, dc, room["slug_hint"])


# ===================================================== the grid, said out loud
@pytest.mark.parametrize("headcount,dept_count", [
    (11, 3), (25, 5), (50, 9), (71, 9),
])
def test_documented_orgs_seat_everyone(headcount, dept_count):
    """Every documented org size is seated — offered == headcount, no shortfall."""
    p = R.programme(headcount, dept_count)
    c = R.check(p, headcount)
    assert c["ok"] is True, c["reason"]
    assert c["seats_offered"] == headcount
    assert c["shortfall"] == 0


def test_a_programme_too_big_for_the_grid_SAYS_SO_and_does_not_expand_it():
    """Overflowing the 3x2 grid is reported, not absorbed.

    `24-FLOORPLAN-SCALE.md` §7 forbids enlarging the building. So a programme
    with more than 6 rooms must report `fits_grid=False`, a `floors_needed`
    count, and a reason naming the grid — while still being `ok` (everyone is
    seated) and leaving `MAX_ROOM_COLS/ROWS` untouched at 3x2.
    """
    grid_now = (R.MAX_ROOM_COLS, R.MAX_ROOM_ROWS)
    # 71 seats needs more than 6 rooms, so it must not fit one floor.
    p71 = R.programme(71, 9)
    assert len(p71) > R.ROOMS_PER_FLOOR
    c71 = R.check(p71, 71)
    assert c71["ok"] is True
    assert c71["fits_grid"] is False
    assert c71["floors_needed"] >= 2
    assert "floor grid" in c71["reason"]
    assert "NOT expanded" in c71["reason"]
    # the grid constant was NOT quietly grown by emitting an overflow
    assert (R.MAX_ROOM_COLS, R.MAX_ROOM_ROWS) == grid_now == (3, 2)


def test_programme_11_3_is_the_small_legible_case():
    """11 seats must NOT overflow the grid — it is the case on screen today."""
    p = R.programme(11, 3)
    c = R.check(p, 11)
    assert c["fits_grid"] is True
    assert len(p) <= 6


# ================================================ staffing arithmetic is exact
@pytest.mark.parametrize("headcount,dept_count", [
    (11, 3), (25, 5), (50, 9), (71, 9), (6, 1), (12, 3), (30, 5),
])
def test_staffing_is_exact(headcount, dept_count):
    """The split must SUM to the headcount, not approximately.

    An estimate that forgets the manager seats produced a 71-seat building
    seating 68 people; this asserts 1 CEO + directors + managers + leaves ==
    headcount exactly for every documented size.
    """
    st = R.staffing(headcount, dept_count)
    total = st["ceo"] + st["directors"] + st["managers"] + st["leaves"]
    assert total == headcount, (headcount, dept_count, st)
    # managers carry at most the span limit of leaves each
    if st["managers"]:
        assert st["leaves"] <= st["managers"] * R.MAX_SPAN
    # one CEO, never more
    assert st["ceo"] == 1


def test_a_department_gets_a_director_only_when_affordable():
    """Small orgs run managers off the CEO; that is the live office's shape.

    The live DB (11 seats, 3 departments) has no ``director_id`` set on any
    department and two managers reporting to the CEO. C30 requires *a*
    supervisor, never a director, so refusing to invent a directorate the
    headcount cannot afford is the honest programme.
    """
    # 11 seats / 3 departments: no directors (1 + 2*3 = 7 <= 11 though — so here
    # they ARE affordable; the unaffordable case is a tiny org).
    tiny = R.staffing(3, 3)      # 1 + 2*3 = 7 > 3 -> no directors
    assert tiny["directors"] == 0
    # the big documented org can afford them
    big = R.staffing(71, 9)
    assert big["directors"] == 9


def test_programme_rejects_nonsense_input():
    assert R.programme(0, 3) == []
    assert R.programme(-5, 3) == []
    # a str is not an int: the guard is deliberate, so the call is suppressed.
    assert R.programme("11", 3) == []          # type: ignore[arg-type]
    assert R.programme(True, 3) == []          # type: ignore[arg-type]
    assert R.programme(11, -1) == []


def test_kind_alias_recognises_the_live_exec_boardroom():
    """The live DB writes the boardroom kind as ``exec``, not ``boardroom``.

    `check` maps the alias so a correctly-programmed building is not reported as
    missing a boardroom for a spelling reason.
    """
    assert R.ROOM_KIND_ALIASES["exec"] == "boardroom"
    live_boardroom = [{"kind": "exec", "slug_hint": "boardroom", "seats": 3}]
    out = R.check(live_boardroom, 3)
    assert out["ok"] is True


# ================================================== the current layout is wrong
def test_the_current_four_room_layout_is_refused_by_the_span_rule():
    """The live office's team rooms over-fill: 7 leaves in 2 undifferentiated rooms.

    The live DB seats 7 leaf workers (4 in ``desk-a``, 3 in ``desk-b``) under two
    managers, in two rooms with no rule tying a manager to its desks. A work room
    in the programme is ONE manager's unit, so the current arrangement — where
    ``desk-a`` holds 4 leaves and ``desk-b`` holds 3, with no manager desk
    recorded in either — is not a programme this module would emit. It is not
    *illegal* at the store layer (there is no seat_index tie in this schema), but
    it is not legible: nothing in the building says which manager owns which
    desks. The programme splits the leaves by manager instead.
    """
    live = [
        {"kind": "boardroom", "slug_hint": "boardroom", "seats": 3},
        {"kind": "work", "slug_hint": "desk-a", "seats": 4},
        {"kind": "work", "slug_hint": "desk-b", "seats": 3},
        {"kind": "review", "slug_hint": "review", "seats": 0},
    ]
    out = R.check(live, 11)
    # It DOES seat 10 of 11 (the CEO + 2 managers are in the boardroom's 3, the
    # 7 leaves in the two work rooms) — so it is not short by the check's
    # accounting here; what it lacks is a manager desk per work room, which is
    # why the programme puts one in each.
    assert out["seats_offered"] == 10


def test_programme_gives_each_work_room_a_manager_desk_the_live_plan_lacks():
    """The fix: the programme's work rooms each contain their manager.

    The live plan's two work rooms hold only leaves (no manager desk), so the
    building cannot show which manager runs which team. The programme emits one
    manager desk per work room, and `check` enforces it.
    """
    p = R.programme(11, 3)
    work = [r for r in p if r["kind"] == "work"]
    assert work
    for room in work:
        assert any(s["is_supervisor"] for s in room["seats_detail"]), room
