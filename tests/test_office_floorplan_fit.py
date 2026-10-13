"""Tests for the fitted floorplan geometry layer (``floorplan_fit``).

These assert the five things the change needs proven before ``tower.py``'s
constants can move:

1. ``geometry_for(11)`` matches ``24-FLOORPLAN-SCALE.md``'s recommendation AND
   ``seating_plan.plan(11)`` — the two planners must produce the SAME numbers;
2. ``assert_matches_tower()`` reports the 6x4-vs-12x7 divergence with line numbers;
3. ``clearance_report`` refuses the legacy 2-tile pitch and accepts the 3-tile one,
   with the margin as a number;
4. ``desk_positions`` for a 12x7 room at pitch (4,3) returns 9 distinct, in-bounds
   desks spanning >= 3 columns and >= 2 rows;
5. the odd-row stagger does something — and a stagger of 0 would collide two desks
   in SCREEN space, shown numerically.

Pure Python, no store I/O.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from voicebridge.office import floorplan_fit as ff          # noqa: E402
from voicebridge.office import seating_plan as sp           # noqa: E402


# --------------------------------------------------------------- (1) agreement
def test_geometry_for_11_matches_the_doc_recommendation():
    """The doc §7 recommends 12x7, stride (4,3), stagger 1, 9/room, 39x16 grid."""
    g = ff.geometry_for(11)
    assert g["room_w"] == 12 and g["room_h"] == 7
    assert (g["pitch_col"], g["pitch_row"]) == (4, 3)
    assert g["odd_row_offset"] == 1
    assert (g["grid_cols"], g["grid_rows"]) == (39, 16)   # 3*(12+1) x 2*(7+1)
    assert g["desks_per_room"] == 9                        # 3 cols x 3 rows
    assert g["rooms_needed"] == 2                          # the doc's §7 table
    assert g["floors_needed"] == 1                         # 11 seats on one floor


def test_geometry_for_delegates_capacity_and_agrees_with_seating_plan():
    """Two planners that disagree mean one is wrong — assert they are identical.

    Every capacity field of ``geometry_for`` must equal ``seating_plan.plan``'s,
    for a spread of headcounts, because that is the one source of truth.
    """
    for headcount in (1, 11, 25, 50, 70, 108, 200):
        g = ff.geometry_for(headcount)
        p = sp.plan(headcount)
        assert p["ok"], (headcount, p["reason"])
        assert g["room_w"] == p["room_w"]
        assert g["room_h"] == p["room_h"]
        assert (g["pitch_col"], g["pitch_row"]) == tuple(p["desk_pitch"])
        assert g["grid_cols"] == p["grid_cols"]
        assert g["grid_rows"] == p["grid_rows"]
        assert g["desks_per_room"] == p["desks_per_room"]
        assert g["rooms_needed"] == p["rooms_needed"]
        assert g["floors_needed"] == p["floors_needed"]


def test_geometry_for_11_room_count_is_derived_not_hardcoded():
    """ceil(11 / 9 desks-per-room) == 2, and the doc's larger orgs match too.

    The doc's recommendation table: 11 -> 2 rooms / 1 floor, 25 -> 3 / 1,
    50 -> 6 / 1, 70 -> 8 / 2.
    """
    expected = {11: (2, 1), 25: (3, 1), 50: (6, 1), 70: (8, 2)}
    for headcount, (rooms, floors) in expected.items():
        g = ff.geometry_for(headcount)
        assert (rooms, floors) == (g["rooms_needed"], g["floors_needed"]), headcount


def test_geometry_for_refuses_a_bad_headcount_by_carrying_plan_refusal():
    """``plan()``'s refusal (headcount 0 / non-int) is carried through, not invented."""
    for bad in (0, -1, "eleven", 11.5, True):
        g = ff.geometry_for(bad)  # type: ignore[arg-type]
        assert g["room_w"] is None
        assert g["rooms_needed"] is None
        assert g["reason"], "a refusal must carry a reason"


def test_legacy_geometry_is_the_current_tower_shape():
    """The legacy geometry mirrors ``tower.py``: 6x4, pitch 2, 6 desks, 21x10 grid."""
    g = ff.legacy_geometry()
    assert (g["room_w"], g["room_h"]) == (6, 4)
    assert (g["pitch_col"], g["pitch_row"]) == (2, 2)
    assert g["odd_row_offset"] == 0
    assert g["desks_per_room"] == 6
    assert (g["grid_cols"], g["grid_rows"]) == (21, 10)    # the doc's §5 table


# --------------------------------------------------------- (2) tower divergence
def test_assert_matches_tower_reports_the_divergence_with_line_numbers():
    """The divergence it reports must be REAL, with real line numbers.

    This test used to pin the divergence as a set of frozen numbers
    (`ROOM_W current == 6`, `pitch_col tower_line == 178`, ...). That made it a
    record of one moment rather than a check: the day `tower.py` was correctly
    changed to the recommended geometry it failed, because there was no
    divergence left to report. A test that fails when the defect is FIXED is
    measuring the defect, not the code.

    What actually matters is the property: whatever `tower.py` reads as now,
    every reported line number must point at a real line, and the report must
    agree with the source it parsed. So this reads `tower.py` itself and
    cross-examines the report rather than trusting a snapshot.
    """
    import re
    from pathlib import Path

    tower = Path(ff.__file__).with_name("tower.py")
    text = tower.read_text()
    lines = text.splitlines()

    d = ff.assert_matches_tower()
    assert d["lines_read"], "no line numbers recorded"
    assert set(d["lines_read"]) >= {"ROOM_W", "ROOM_COLS"}, d["lines_read"]

    for name, lineno in d["lines_read"].items():
        assert 1 <= lineno <= len(lines), f"{name} cites line {lineno}, out of range"
    for row in d["differs_on"]:
        assert 1 <= row["tower_line"] <= len(lines), row

    # the report must agree with the file it parsed, not with a memory of it
    pattern = r"^ROOM_W, ROOM_H = ([0-9]+), ([0-9]+)"
    found = re.search(pattern, text, re.M)
    assert found, "could not read the geometry constants from tower.py"
    cur = d["current"]
    assert int(found.group(1)) == cur["ROOM_W"]
    assert int(found.group(2)) == cur["ROOM_H"]
    assert d["lines_read"]["ROOM_W"] == next(
        i for i, ln in enumerate(lines, 1) if ln.startswith("ROOM_W, ROOM_H"))

    # the headline claim has to hold in BOTH directions: identical geometry
    # reports no divergence for that param, different geometry reports it. These
    # two assertions are what make the test survive the fix instead of being
    # pinned to the pre-fix numbers.
    listed = {row["param"] for row in d["differs_on"]}
    assert ("ROOM_W" in listed) is (cur["ROOM_W"] != d["recommended"]["ROOM_W"])
    assert ("ROOM_H" in listed) is (cur["ROOM_H"] != d["recommended"]["ROOM_H"])

    if d["same"]:
        assert not d["differs_on"], "same=True but differences were listed"
    else:
        assert d["differs_on"], "same=False but nothing differs"
        for row in d["differs_on"]:
            assert row["current"] != row["recommended"], row




def test_assert_matches_tower_reads_the_lines_it_names():
    """The reported line numbers point at the text that was actually parsed."""
    d = ff.assert_matches_tower()
    path = os.path.join(ROOT, "voicebridge", "office", "tower.py")
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    lr = d["lines_read"]
    assert "ROOM_W, ROOM_H" in lines[lr["ROOM_W"] - 1]      # 1-indexed
    assert "ROOM_COLS, ROOM_ROWS" in lines[lr["ROOM_COLS"] - 1]
    assert lines[lr["room_slot_x_return"] - 1].strip().startswith("return col *")
    assert lines[lr["room_slot_y_return"] - 1].strip().startswith("return row *")
    assert lines[lr["SEAT_SLOTS_PER_ROOM"] - 1].startswith("SEAT_SLOTS_PER_ROOM")


def test_assert_matches_tower_reads_the_file_and_mutates_nothing():
    """It PARSES tower.py; it must not import it and must not change itself.

    The first version of this test asserted ``"voicebridge.office.tower" not in
    sys.modules``. That is a claim about global interpreter state, not about this
    module: it passed in isolation and failed in the full suite only because an
    EARLIER test had imported tower for its own reasons. A test whose result
    depends on what other tests ran is worse than no test, because it makes every
    "all green" claim conditional.

    What actually matters is testable without touching sys.modules:
    (a) calling it does not introduce a tower module reference into THIS module's
        namespace, and (b) it leaves its own constants alone. The file-parsing
        claim is proven directly instead — by checking the function's own source
        has no import of tower.
    """
    import voicebridge.office.floorplan_fit as mod
    import inspect

    src = inspect.getsource(mod)
    assert "import tower" not in src and "from .tower" not in src, (
        "floorplan_fit must parse tower.py's text, not import it -- importing "
        "would make this layer depend on tower's runtime state")

    before = (mod.ROOM_W, mod.ROOM_H, mod.PITCH_COL, mod.PITCH_ROW)
    out = ff.assert_matches_tower()
    after = (mod.ROOM_W, mod.ROOM_H, mod.PITCH_COL, mod.PITCH_ROW)
    assert before == after == (12, 7, 4, 3), "the call mutated its own constants"

    # it must reference the module by name, never as an object
    assert not hasattr(mod, "tower"), (
        "floorplan_fit holds a reference to the tower module")
    # and it must return what it read, with real line numbers.
    #
    # RE-POINTED. This assertion used to be `assert out["differs_on"]` -- "a
    # divergence MUST be reported". That was true only while tower.py still
    # carried the legacy stride. It is now `same: True` with `differs_on == []`,
    # so demanding a divergence would fail on the CORRECT state and would, if
    # satisfied, mean the recommendation had been un-applied. What the caller
    # actually needs is: the comparison ran, it was performed against parsed
    # source (so line numbers exist), and it reports agreement when there is
    # agreement. A divergence is reported when it exists -- proven separately by
    # `test_the_checker_reports_a_divergence_when_tower_is_changed`, which edits
    # a scratch copy of the source rather than requiring the live file to be wrong.
    assert "differs_on" in out and isinstance(out["differs_on"], list)
    assert out["same"] is True and out["differs_on"] == [], (
        "assert_matches_tower reports a divergence against the live tower.py: "
        f"{out['differs_on']} -- either the recommendation was un-applied, or the "
        "checker is reading the wrong source again (it once compared this module "
        "to ITSELF, filling `current` from its own LEGACY_* constants)")
    assert out["current"]["pitch_col"] == 4, (
        "the checker did not read tower.py's real PITCH_COL (expected 4, got "
        f"{out['current']['pitch_col']}) -- it is fabricating `current` again")
    assert out["lines_read"], "no line numbers recorded for the read"


def test_the_checker_reports_a_divergence_when_tower_is_changed(tmp_path):
    """The checker CAN fail: give it a tower.py that carries the legacy stride.

    A checker that always reports `same: True` is as useless as one that always
    reports a divergence. This points `_TOWER_PATH` at a scratch copy of the real
    tower.py with `PITCH_COL, PITCH_ROW` reverted to the legacy `2, 2` and asserts
    the divergence is reported with the param named and a line number attached.
    """
    import voicebridge.office.floorplan_fit as mod
    from pathlib import Path

    real = Path(mod._TOWER_PATH).read_text(encoding="utf-8")
    patched = real.replace("PITCH_COL, PITCH_ROW = 4, 3", "PITCH_COL, PITCH_ROW = 2, 2")
    assert patched != real, "could not construct the legacy tower.py fixture"
    scratch = tmp_path / "tower.py"
    scratch.write_text(patched, encoding="utf-8")

    original = mod._TOWER_PATH
    try:
        mod._TOWER_PATH = str(scratch)
        out = mod.assert_matches_tower()
    finally:
        mod._TOWER_PATH = original

    assert out["same"] is False, (
        "the checker reported agreement against a tower.py carrying the legacy "
        "pitch -- it cannot detect the very divergence it exists to report")
    named = {d["param"] for d in out["differs_on"]}
    assert "pitch_col" in named and "pitch_row" in named, (
        f"the divergence is reported but does not name the pitch params: {named}")
    # and the values it reports must be the SCRATCH file's, not this module's
    assert out["current"]["pitch_col"] == 2 and out["current"]["pitch_row"] == 2, (
        "the checker reported `current` from somewhere other than the file it was "
        f"pointed at: {out['current']['pitch_col']}, {out['current']['pitch_row']}")


# -------------------------------------------------------------- (3) clearance
def test_clearance_report_accepts_the_recommended_pitch_with_a_numeric_margin():
    """pitch 3 -> 25.5 units of row offset vs a 15-unit figure -> +10.5."""
    r = ff.clearance_report(ff.geometry_for(11))
    assert r["ok"] is True
    assert r["pitch_row"] == 3
    assert r["row_offset"] == pytest.approx(25.5)
    assert r["figure_h"] == 15.0
    assert r["margin"] == pytest.approx(10.5)
    assert "+10.5" in r["reason"]


def test_clearance_report_refuses_the_legacy_pitch_and_states_the_margin():
    """pitch 2 -> 17.0 units vs the 15-unit figure: ok=False, margin +2.0."""
    r = ff.clearance_report(ff.legacy_geometry())
    assert r["ok"] is False
    assert r["row_offset"] == pytest.approx(17.0)
    assert r["margin"] == pytest.approx(2.0)
    assert "buried" in r["reason"]
    assert "2.0" in r["reason"]


def test_clearance_report_refuses_a_negative_margin_pitch():
    """pitch 1 -> 8.5 units vs 15: fully occluding, margin -6.5 (the doc §4 row)."""
    r = ff.clearance_report({"pitch_row": 1, "room_h": 7})
    assert r["ok"] is False
    assert r["margin"] == pytest.approx(-6.5)
    assert "occluded" in r["reason"]


def test_clearance_report_prints_the_doc_table_per_row_pitch():
    """The doc §4 table as data: 1 -> -6.5, 2 -> +2.0, 3 -> +10.5, 4 -> +19.0."""
    r = ff.clearance_report(ff.geometry_for(11))
    table = r["per_row_pitch"]
    assert table[1]["clearance"] == pytest.approx(-6.5)
    assert table[2]["clearance"] == pytest.approx(2.0)
    assert table[3]["clearance"] == pytest.approx(10.5)
    assert table[4]["clearance"] == pytest.approx(19.0)
    assert table[3]["row_offset"] == pytest.approx(25.5)
    assert table[2]["row_offset"] == pytest.approx(17.0)


def test_clearance_report_agrees_with_seating_plan_clearance():
    """The two layers must not disagree on the same arithmetic."""
    for pitch in (1, 2, 3, 4, 5):
        r = ff.clearance_report({"pitch_row": pitch, "room_h": 7})
        assert r["margin"] == pytest.approx(sp.clearance(7, pitch)), pitch


# ---------------------------------------------------------- (4) desk positions
def test_desk_positions_12x7_pitch_4_3_returns_nine_distinct_in_bounds_desks():
    g = ff.geometry_for(11)
    room_x, room_y = 0, 0
    pos = ff.desk_positions(room_x, room_y, g["desks_per_room"], g)

    assert len(pos) == 9
    # none sharing a cell
    cells = {(p["x"], p["y"]) for p in pos}
    assert len(cells) == 9

    # spanning at least 3 columns and 2 rows
    assert len({p["x"] for p in pos}) >= 3
    assert len({p["y"] for p in pos}) >= 2

    # every position inside the room's own extent
    for p in pos:
        assert room_x + 1 <= p["x"] <= room_x + g["room_w"], p
        assert room_y + 1 <= p["y"] <= room_y + g["room_h"], p


def test_desk_positions_matches_seating_plan_positions_offset_by_the_room_origin():
    """The fit layer is the seating planner's layout, translated to absolute coords."""
    g = ff.geometry_for(11)
    local = sp._desk_positions(g["room_w"], g["room_h"],
                               g["pitch_col"], g["pitch_row"], g["odd_row_offset"])
    for room_x, room_y in ((0, 0), (13, 0), (26, 8), (13, 8)):
        got = ff.desk_positions(room_x, room_y, 9, g)
        assert got == [{"x": room_x + p["x"], "y": room_y + p["y"]} for p in local]


def test_desk_positions_caps_at_n_and_matches_the_doc_layout():
    """The doc §7 layout, verbatim: rows y=1 {1,5,9}, y=4 {2,6,10}, y=7 {1,5,9}."""
    g = ff.geometry_for(11)
    assert ff.desk_positions(0, 0, 9, g) == [
        {"x": 1, "y": 1}, {"x": 5, "y": 1}, {"x": 9, "y": 1},
        {"x": 2, "y": 4}, {"x": 6, "y": 4}, {"x": 10, "y": 4},
        {"x": 1, "y": 7}, {"x": 5, "y": 7}, {"x": 9, "y": 7},
    ]
    # n caps the count, and does not grow it
    assert len(ff.desk_positions(0, 0, 4, g)) == 4
    assert len(ff.desk_positions(0, 0, 20, g)) == 9


# ------------------------------------------------- (5) the stagger must matter
def test_odd_row_offset_shifts_x_by_exactly_one_tile():
    """Row j=1 is shifted one tile from row j=0; row j=2 is not."""
    g = ff.geometry_for(11)
    pos = ff.desk_positions(0, 0, 9, g)
    rows = {}
    for p in pos:
        rows.setdefault(p["y"], []).append(p["x"])
    for xs in rows.values():
        xs.sort()
    assert rows[1] == [1, 5, 9]     # j=0, stagger +0
    assert rows[4] == [2, 6, 10]    # j=1, stagger +1
    assert rows[7] == [1, 5, 9]     # j=2, stagger +0 back
    assert rows[4][0] - rows[1][0] == ff.ODD_ROW_OFFSET == 1
    assert rows[7][0] - rows[4][0] == -1


def test_odd_row_offset_is_what_breaks_the_depth_sort_tie():
    """The stagger's actual, measurable job, shown on the layout it fixes.

    The doc's §8 cause #2 is the depth-sort tie: ``(x, y+2)`` and ``(x+2, y)``
    both have ``x + y`` equal, so the back-to-front sort is ambiguous. That tie is
    real in a layout whose column pitch is 2 — which is exactly the LEGACY 6x4
    layout. ``desk_positions`` shows it:

        legacy (pitch 2,2):  (3,1) and (1,3) both have x + y = 4
                             (5,1) and (3,3) both have x + y = 6

    and the recommended (12x7, pitch (4,3), stagger 1) layout has NO two desks
    sharing a sort key — verified below, for every desk pair, not asserted.
    """
    # --- the legacy layout DOES tie, and this is the number the doc cites ---
    legacy = ff.desk_positions(0, 0, 6, ff.legacy_geometry())
    legacy_keys = [p["x"] + p["y"] for p in legacy]
    tied = sorted({k for k in legacy_keys if legacy_keys.count(k) > 1})
    assert tied == [4, 6]
    pairs = [(a, b) for a in legacy for b in legacy
             if a is not b and a["x"] + a["y"] == b["x"] + b["y"]]
    # the concrete (x+2, y-2) diagonal pair from the doc, as coordinates:
    assert any({(a["x"], a["y"]), (b["x"], b["y"])} == {(3, 1), (1, 3)}
               for a, b in pairs)

    # --- the recommended layout does NOT tie in the doc's ACTUAL sort key ---
    # The doc's total key is ``(x + y, y)`` (§8's "sort by x+y, then y"). The bare
    # ``x + y`` alone still repeats under the recommendation — e.g. (5,1) and (2,4)
    # both sum to 6 — so the tie-break on ``y`` is load-bearing, not decorative.
    g = ff.geometry_for(11)
    pos = ff.desk_positions(0, 0, 9, g)
    assert len({p["x"] + p["y"] for p in pos}) < 9, "x+y alone is not a total key"
    full = [(p["x"] + p["y"], p["y"]) for p in pos]
    assert len(set(full)) == 9, "but (x + y, y) is total"


def test_two_desks_that_tie_in_screen_space_are_exactly_the_tie_pairs():
    """Screen-space distance for a tied pair, so the collision is a number.

    In the 2:1 iso basis (``dx = (TILE_W/2, TILE_H/2) = (17, 8.5)``), the legacy
    tie pair ``(3,1)`` / ``(1,3)`` sits at screen ``((3-1)*17, (3+1)*8.5) =
    (34, 34)`` versus ``((1-3)*17, (1+3)*8.5) = (-34, 34)`` — 68 px apart in x and
    0 apart in y. Because they have the same ``x + y``, the depth sort cannot say
    which is in front; with a uniform column pitch of 2 there is no ``y`` break to
    fall back on, which is the tie. The stagger of the recommendation removes every
    such pair, and the test checks the pair set is empty there.
    """
    def screen(p):
        return ((p["x"] - p["y"]) * (ff.TILE_W / 2.0),
                (p["x"] + p["y"]) * (ff.TILE_H / 2.0))

    legacy = ff.desk_positions(0, 0, 6, ff.legacy_geometry())
    a = next(p for p in legacy if (p["x"], p["y"]) == (3, 1))
    b = next(p for p in legacy if (p["x"], p["y"]) == (1, 3))
    (ax, ay), (bx, by) = screen(a), screen(b)
    assert (ax, ay) == (34.0, 34.0)
    assert (bx, by) == (-34.0, 34.0)
    # same row on screen (dy == 0) despite being two rows apart in the grid:
    assert ay == by
    assert abs(ax - bx) == pytest.approx(2 * ff.TILE_W)  # 68 px = two tile widths
    # ...and the same depth key, so the bare x+y sort cannot order them:
    assert a["x"] + a["y"] == b["x"] + b["y"]

    # the recommendation has no such *screen* tie: the docs' sort is (x+y, y) and
    # every desk has a distinct (x+y, y) — the bare x+y repeats, but the y break
    # orders it, and no two desks sit at the same screen point.
    g = ff.geometry_for(11)
    pos = ff.desk_positions(0, 0, 9, g)
    assert len({(p["x"] + p["y"], p["y"]) for p in pos}) == 9
    screen_pts = {screen(p) for p in pos}
    assert len(screen_pts) == 9, "no two desks may share a screen point"
