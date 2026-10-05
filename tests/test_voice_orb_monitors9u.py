"""Orb monitor-aware placement: the orb must live on the PRIMARY monitor.

Owner hardware 2026-10-04 (``xrandr --query``): DP-2 primary
2580x1080+0+0 (VS Code lives here), HDMI-1 1920x1080+2580+0
(secondary, possibly off/unwatched). The orb validated persisted
x=4292 against the 4500-wide VIRTUAL desktop and parked on HDMI-1
while the owner works on DP-2 -- invisible in practice.

Contract (orb package only):
- parse ``xrandr --query`` (no new deps; fail-closed to current
  behavior when xrandr absent/foreign, e.g. Wayland),
- identify the PRIMARY output rect (else the focused window's
  output, else first connected),
- default/validate/clamp ALL against that rect (persisted pos on a
  non-primary output -> reset to primary default).
- Untouched: drag/click/IPC/focus/fullscreen-rule/ingame
  logic; 45px size; sticky/all-workspaces hints.

Hermetic: fake xrandr text/run (no display, no subprocess).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.orb import geometry as orb_geo
from voicebridge.orb import monitors as orb_mon

# This exact layout: DP-2 primary 2580x1080+0+0, HDMI-1 1920x1080+2580+0.
DUAL_XRANDR = """\
Screen 0: minimum 8 x 8, current 4500 x 1080, maximum 32767 x 32767
HDMI-0 disconnected (normal left inverted right x axis y axis)
DP-0 disconnected (normal left inverted right x axis y axis)
DP-1 disconnected (normal left inverted right x axis y axis)
HDMI-1 connected 1920x1080+2580+0 (normal left inverted right x axis y axis) 1280mm x 720mm
   1920x1080     60.00*+  59.94    50.00    29.97    25.00    24.00    23.98
DP-2 connected primary 2580x1080+0+0 (normal left inverted right x axis y axis) 797mm x 334mm
   2560x1080     60.00 +
   2580x1080     60.00*
DP-3 disconnected (normal left inverted right x axis y axis)
"""

SINGLE_XRANDR = """\
Screen 0: minimum 8 x 8, current 2580 x 1080, maximum 32767 x 32767
DP-2 connected primary 2580x1080+0+0 (normal left inverted right x axis y axis) 797mm x 334mm
   2580x1080     60.00*
"""

NO_PRIMARY_XRANDR = """\
Screen 0: minimum 8 x 8, current 4500 x 1080, maximum 32767 x 32767
HDMI-1 connected 1920x1080+2580+0 (normal left inverted right x axis y axis) 1280mm x 720mm
   1920x1080     60.00*+
DP-2 connected 2580x1080+0+0 (normal left inverted right x axis y axis) 797mm x 334mm
   2580x1080     60.00*
"""


def _run_factory(text):
    def _run(argv, **kw):
        assert argv[:2] == ["xrandr", "--query"]

        class _R:
            stdout = text
        return _R()
    return _run


def _run_absent(argv, **kw):
    raise FileNotFoundError("no xrandr on PATH (Wayland?)")


# -- parse ---------------------------------------------------------------

def test_parse_dual_layout_finds_primary_and_secondary_rects():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    by_name = {o.name: o for o in outs}
    assert set(by_name) == {"HDMI-1", "DP-2"}
    primary = by_name["DP-2"]
    assert (primary.x, primary.y, primary.w, primary.h) == (0, 0, 2580, 1080)
    assert primary.primary is True
    secondary = by_name["HDMI-1"]
    assert (secondary.x, secondary.y, secondary.w, secondary.h) == (2580, 0, 1920, 1080)
    assert secondary.primary is False


def test_parse_ignores_disconnected_and_foreign_text():
    assert orb_mon.parse_xrandr("") == []
    assert orb_mon.parse_xrandr("can't open display\n") == []
    assert orb_mon.parse_xrandr("Screen 0: minimum 8 x 8\n") == []
    # Connected with no mode line carries no usable geometry.
    assert orb_mon.parse_xrandr("DP-2 connected (normal)\n") == []


def test_target_prefers_primary_over_first_connected():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None and target.name == "DP-2"


def test_target_without_primary_falls_back_to_first_connected():
    outs = orb_mon.parse_xrandr(NO_PRIMARY_XRANDR)
    assert all(o.primary is False for o in outs)
    target = orb_mon.pick_target(outs)
    # First connected in xrandr order (no primary flagged).
    assert target is not None and target.name == "HDMI-1"


def test_target_empty_is_none_fail_closed():
    assert orb_mon.pick_target([]) is None
    assert orb_mon.target_rect(run=_run_absent) is None


def test_query_never_raises_when_xrandr_absent():
    assert orb_mon.query_outputs(run=_run_absent) == []


# -- default / validate / clamp against the primary rect -----------------

def test_default_lands_on_primary_not_virtual_corner():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None
    pos = orb_geo.default_position_for_rect(target)
    # Bottom-right of DP-2 (0..2580), NOT of the 4500-wide virtual desktop.
    assert (pos.x, pos.y) == (2580 - 68 - orb_geo.MARGIN,
                              1080 - 68 - orb_geo.MARGIN)
    assert pos.x + orb_geo.ORB_PX <= 2580
    assert pos.collapsed is False


def test_persisted_hdmi_pos_resets_to_primary_default():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None
    # The exact failure: validated against the 4500-wide virtual desktop,
    # x=4292 looked onscreen -- but it is on HDMI-1, unwatched.
    stored = orb_geo.OrbPosition(x=4292, y=872, collapsed=False)
    resolved, reset, _reason = orb_geo.resolve_initial_position_for_rect(
        stored, target)
    assert reset is True
    assert resolved == orb_geo.default_position_for_rect(target)
    assert resolved.x + orb_geo.ORB_PX <= 2580


def test_primary_onscreen_pos_is_kept():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None
    stored = orb_geo.OrbPosition(x=2200, y=800, collapsed=False)
    resolved, reset, _reason = orb_geo.resolve_initial_position_for_rect(
        stored, target)
    assert reset is False
    assert (resolved.x, resolved.y) == (2200, 800)


def test_clamp_pins_to_primary_rect():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None
    clamped = orb_geo.clamp_to_rect(4292, 872, target)
    assert clamped.x + orb_geo.ORB_PX <= target.x + target.w
    assert clamped.x >= target.x and clamped.y >= target.y
    same = orb_geo.clamp_to_rect(200, 200, target)
    assert (same.x, same.y) == (200, 200)


def test_single_output_matches_legacy_behavior():
    outs = orb_mon.parse_xrandr(SINGLE_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None and target.name == "DP-2"
    assert orb_geo.default_position_for_rect(target) == orb_geo.default_position(
        target.w, target.h)
    stored = orb_geo.OrbPosition(x=4388, y=968, collapsed=True)
    rect_resolved, rect_reset, _ = orb_geo.resolve_initial_position_for_rect(
        stored, target)
    leg_resolved, leg_reset, _ = orb_geo.resolve_initial_position(
        stored, target.w, target.h)
    assert (rect_reset, rect_resolved) == (leg_reset, leg_resolved)


def test_dot_mode_rect_bounds_are_size_aware():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None
    # Owner 2026-10-04 (de-chromed orb): collapsed is migrated, never
    # honoured. x=2550 holds a 16px dot but NOT a 68px orb on DP-2
    # (2550+16=2566 fits; 2550+68=2618 overflows), so a dot-parked
    # position resets to the primary default.
    dot = orb_geo.OrbPosition(x=2550, y=100, collapsed=True)
    resolved, reset_dot, _ = orb_geo.resolve_initial_position_for_rect(dot, target)
    assert reset_dot is True
    assert resolved == orb_geo.default_position_for_rect(target)
    assert resolved.collapsed is False
    orb = orb_geo.OrbPosition(x=2550, y=100, collapsed=False)
    _, reset_orb, _ = orb_geo.resolve_initial_position_for_rect(orb, target)
    assert reset_orb is True


def test_persisted_collapsed_true_on_primary_migrates_to_expanded():
    # A dot-parked position that CAN hold the full 68px orb keeps its
    # x/y but resolves expanded — never as a dot.
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None
    stored = orb_geo.OrbPosition(x=200, y=200, collapsed=True)
    resolved, reset, _ = orb_geo.resolve_initial_position_for_rect(
        stored, target)
    assert reset is False
    assert (resolved.x, resolved.y) == (200, 200)
    assert resolved.collapsed is False


def test_rect_resolve_is_idempotent():
    outs = orb_mon.parse_xrandr(DUAL_XRANDR)
    target = orb_mon.pick_target(outs)
    assert target is not None
    stored = orb_geo.OrbPosition(x=4292, y=872, collapsed=False)
    first, _, _ = orb_geo.resolve_initial_position_for_rect(stored, target)
    second, reset2, _ = orb_geo.resolve_initial_position_for_rect(first, target)
    assert reset2 is False
    assert second == first
