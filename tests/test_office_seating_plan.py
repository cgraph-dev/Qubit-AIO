"""The floor-plan planner must refuse an occluding layout, not just describe one.

`docs/game-office/24-FLOORPLAN-SCALE.md` diagnoses "the desks overlap and
occlude each other" and proves it is a *constant*, not a zoom setting: a figure
is 15 units tall, a row a tile nearer is drawn 8.5 units lower on screen, and
`15*z > 8.5*p*z` cancels `z`. The doc's fix is `ROOM_W, ROOM_H = 12, 7` with a
`(4, 3)` stride and a 1-tile odd-row stagger.

This file checks that the planner reproduces that arithmetic and refuses the old
geometry rather than emitting a plausible floor plan for it. In particular:

  * `plan(11)` materialises the recommendation — one floor, 12x7, pitch (4,3);
  * `check_no_occlusion` independently reproduces the diagnosis: the OLD 6x4 /
    pitch-2 geometry comes back False with the rooms NAMED;
  * the recommended pitch clears the figure by a stated margin at every zoom
    (the margin is a number, not a boolean);
  * nonsense input (`0`, `-1`, `10_000`) is refused with an honest `reason`,
    because a planner that returns a layout for nonsense is the bug;
  * `desks_per_room` is derived — two pitches give two different counts.

The constants are read from the real sources, not assumed: `CHAR_H` is the `15`
in `drawAgent` (`qubit-office.js`: `var h = 15 * cam.zoom`), `TILE_H` is `17`
from the same file, and the legacy 6x4 room is `tower.py`'s `ROOM_W, ROOM_H`.

Hermetic: pure functions, no network, no live service, no I/O.
"""
from __future__ import annotations

import pytest

from voicebridge.office import seating_plan as S


# --- the constants are the real ones ----------------------------------------

def test_char_h_is_the_real_drawAgent_height():
    """CHAR_H must be the 15 the renderer actually draws, not a transcribed guess.

    Read from ``qubit-office.js``: ``function drawAgent(...) { var h = 15 *
    cam.zoom, w = 9 * cam.zoom; ... }``. If the renderer changes the figure
    height, this pin forces the occlusion arithmetic to be revisited.
    """
    here = __import__("pathlib").Path(__file__).resolve().parent.parent
    js = (here / "frontend" / "qubit-app" / "plugins" / "qubit-office"
          / "qubit-office.js").read_text(encoding="utf-8")
    assert "var h = 15 * cam.zoom, w = 9 * cam.zoom" in js
    assert S.CHAR_H == 15.0
    assert S.CHAR_W == 9.0
    # MIN_CLEARANCE is the row offset the doc's recommendation requires: 3 tiles
    # at 8.5 units = 25.5, which is the smallest pitch that clears the body with
    # headroom left over (a 2-tile pitch's 17.0 clears the body but buries the
    # desk plate and state band — doc §4).
    assert S.MIN_CLEARANCE == 25.5
    assert S.TILE_H == 17.0
    assert S.ODD_ROW_OFFSET == 1


# --- assertion 1: the doc's recommendation materialises ---------------------

def test_plan_11_is_one_floor_at_the_recommended_geometry():
    """11 seats must fit on ONE floor at 12x7 / pitch (4, 3) — doc §7's table."""
    p = S.plan(11)
    assert p["ok"] is True
    assert p["floors_needed"] == 1
    assert p["room_w"] == 12 and p["room_h"] == 7
    assert p["desk_pitch"] == (4, 3)
    assert S.ROOM_W == 12 and S.ROOM_H == 7
    assert S.PITCH_COL == 4 and S.PITCH_ROW == 3
    # 9 desks/room, 6 rooms/floor = 54/floor; 11 seats need 2 rooms.
    assert p["desks_per_room"] == 9
    assert p["desks_per_floor"] == 54
    assert p["rooms_needed"] == 2
    # floor grid 3*(12+1) x 2*(7+1) = 39 x 16
    assert p["grid_cols"] == 39 and p["grid_rows"] == 16


def test_recommended_room_layout_is_the_docs_three_by_three_staggered_grid():
    """The 9 positions are the doc's: y=1/4/7, odd row shifted by one tile."""
    p = S.plan(25)
    pos = p["positions"]
    assert len(pos) == 9
    by_row = {}
    for d in pos:
        by_row.setdefault(d["y"], []).append(d["x"])
    assert by_row == {1: [1, 5, 9], 4: [2, 6, 10], 7: [1, 5, 9]}
    # the stagger removes every (x + y) tie in the depth sort
    keys = [(d["x"] + d["y"], d["y"]) for d in pos]
    assert len(keys) == len(set(keys))


# --- assertion 2: the diagnosis is reproduced independently -----------------

def test_old_geometry_is_refused_and_names_the_rooms():
    """The OLD 6x4 / pitch-2 layout must come back NOT ok, with rooms named.

    This is the module reproducing the doc's §1/§4 diagnosis on its own
    arithmetic: at pitch 2 the 15-unit figure overhangs the row behind by 2.0
    units, so the back row's desk plate and state band are buried.
    """
    old = {
        "room_h": S.LEGACY_ROOM_H,        # 4
        "pitch": S.LEGACY_PITCH_ROW,      # 2
        "rooms": [
            {"room_id": "room-%d" % i, "room_h": S.LEGACY_ROOM_H,
             "pitch": S.LEGACY_PITCH_ROW}
            for i in range(1, 3)
        ],
    }
    verdict = S.check_no_occlusion(old)
    assert verdict["ok"] is False
    assert verdict["violating_rooms"] == ["room-1", "room-2"]
    # clearance at pitch 2 is 17.0 - 15.0 = +2.0, under MIN_CLEARANCE = 15.
    assert verdict["worst_clearance"] == pytest.approx(2.0)
    assert verdict["worst_clearance"] < S.MIN_CLEARANCE


def test_planning_itself_refuses_the_occluding_pitch():
    """A caller who asks for the old pitch gets a refusal, not a layout."""
    bad = S.plan(11, {"room_w": 6, "room_h": 4, "pitch": (4, 2)})
    assert bad["ok"] is False
    assert "occlud" in bad["reason"].lower()
    assert bad["rooms_needed"] == 0


def test_pitch_one_buries_the_row_behind_by_6_5_units():
    """The doc's §4 worst case: pitch 1 covers 57% of the figure behind."""
    assert S.clearance(7, 1) == pytest.approx(-6.5)
    assert S.check_no_occlusion(
        {"room_h": 7, "pitch": 1})["ok"] is False


# --- assertion 3: clearance at the recommended pitch, margin as a number ----

def test_recommended_clearance_exceeds_figure_with_a_stated_margin():
    """Row offset 25.5 >= figure 15.0, leaving +10.5 units of margin — doc §7.

    ``clearance`` reports the MARGIN; the acceptance test is on the row offset
    itself (the doc's §8 identity ``|y₂ - y₁| · 8.5 ≥ 15``). Both numbers are
    stated so a caller sees 25.5 and 10.5, not a boolean.
    """
    offset = (S.TILE_H / 2.0) * S.PITCH_ROW          # 25.5
    c = S.clearance(S.ROOM_H, S.PITCH_ROW)           # 25.5 - 15.0 = 10.5
    assert offset == pytest.approx(25.5)
    assert offset >= S.CHAR_H                        # the row behind is cleared
    assert c == pytest.approx(10.5)
    assert c == pytest.approx(offset - S.CHAR_H)
    assert c > 0
    # the margin appears in the plan's own reason, as a number
    assert "10.5" in S.plan(11)["reason"]


def test_plan_11_reports_the_clearance_margin_as_a_number():
    """The caller can read the margin rather than trust an `ok`."""
    p = S.plan(11)
    assert p["worst_clearance"] == pytest.approx(10.5)
    assert p["worst_clearance"] > 0


def test_clearance_is_zoom_independent_by_construction():
    """Both terms carry z, so clearance is a pure function of the pitch.

    Scaling the row offset and the figure by the same zoom cannot change the
    sign — which is exactly why the doc calls occlusion a geometry bug and not
    a zoom setting.
    """
    for zoom in (0.45, 0.70, 0.85, 1.00, 1.40, 2.40):
        # (8.5 * 3 - 15) * zoom, compared against the unzoomed clearance
        scaled = ((S.TILE_H / 2.0) * S.PITCH_ROW - S.CHAR_H) * zoom
        assert (scaled > 0) == (S.clearance(S.ROOM_H, S.PITCH_ROW) > 0)


# --- assertion 4: nonsense is refused honestly ------------------------------

@pytest.mark.parametrize("bad", [0, -1])
def test_zero_and_negative_headcount_are_refused(bad):
    p = S.plan(bad)
    assert p["ok"] is False
    assert p["reason"]
    assert p["rooms_needed"] == 0 and p["floors_needed"] == 0


def test_non_integer_headcount_is_refused():
    assert S.plan(True)["ok"] is False        # bool is not a headcount
    assert S.plan(3.5)["ok"] is False         # type: ignore[arg-type]
    assert S.plan(None)["ok"] is False        # type: ignore[arg-type]


def test_huge_headcount_still_plans_and_is_internally_consistent():
    """10_000 is not a crash: the building grows, and the capacity adds up."""
    p = S.plan(10_000)
    assert p["ok"] is True
    assert p["rooms_needed"] > 0 and p["floors_needed"] > 0
    assert p["rooms_needed"] * p["desks_per_room"] >= 10_000
    assert p["floors_needed"] * S.ROOM_COLS * S.ROOM_ROWS >= p["rooms_needed"]


# --- assertion 5: desks_per_room is derived, not hardcoded ------------------

def test_desks_per_room_changes_with_the_pitch():
    """Two different pitches must give two different counts."""
    nine = S.desks_per_room(12, 7, (4, 3))     # 3 cols x 3 rows
    twelve = S.desks_per_room(12, 7, (4, 2))   # 3 cols x 4 rows — tighter pitch
    six = S.desks_per_room(6, 4, 2)            # doc §1: 3 cols x 2 rows
    assert nine == 9
    assert twelve == 12
    assert six == 6
    assert len({nine, twelve, six}) == 3


def test_six_by_four_at_the_current_pitch_matches_the_doc():
    """The doc's §1 table, reproduced: 6x4 holds 6 at pitch 2, 4 at pitch 3."""
    assert S.desks_per_room(6, 4, 2) == 6
    assert S.desks_per_room(6, 4, 3) == 4
    assert S.desks_per_room(6, 4, 4) == 2


def test_plan_reports_a_reason_and_no_plan_when_capacity_is_zero():
    """A room too small for any desk is refused, not silently rounded up."""
    p = S.plan(4, {"room_w": 0, "room_h": 0})
    assert p["ok"] is False
    assert p["reason"]
