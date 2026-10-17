"""Orb settings panel: pure spec/mapping + guarded Qt widget (TDD).

Hermetic by design: the module must import with no Qt and no display,
so every pure assertion runs anywhere. The one widget-level test is
guarded on Qt importability and only checks construction contract
(never opens a window without a display).
"""
from __future__ import annotations

import importlib
import os

import pytest

NO_DISPLAY = not os.environ.get("DISPLAY")


def test_module_imports_with_no_qt_or_display():
    # Import must never raise, and the pure layer must be present.
    from voicebridge.orb import panel
    for name in ("panel_rows", "apply_row_change", "opacity_to_slider",
                 "slider_to_opacity", "speed_to_slider", "slider_to_speed",
                 "state_hex"):
        assert callable(getattr(panel, name)), name


def test_panel_rows_covers_required_ids():
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    rows = {r["id"]: r for r in panel.panel_rows(O.OrbOptions())}
    required = {"size_px", "opacity", "speed", "locked", "click_through",
                "show_tray"}
    for state in ("idle", "listening", "thinking", "working", "speaking",
                  "error"):
        required.add("color:" + state)
        required.add("test:" + state)
    assert required.issubset(rows.keys())
    assert rows["size_px"]["kind"] == "slider"
    assert rows["locked"]["kind"] == "check"
    assert rows["color:idle"]["kind"] == "color"
    assert rows["test:idle"]["kind"] == "button"
    assert rows["test:idle"]["state"] == "idle"


def test_slider_ranges_match_options():
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    rows = {r["id"]: r for r in panel.panel_rows(O.OrbOptions())}
    assert (rows["size_px"]["min"], rows["size_px"]["max"]) == (
        O.SIZE_MIN, O.SIZE_MAX)
    # opacity/speed sliders use the panel's own integer coordinates.
    assert (rows["opacity"]["min"], rows["opacity"]["max"]) == (0, 100)
    assert (rows["speed"]["min"], rows["speed"]["max"]) == (40, 200)
    # Defaults land where the widget starts (speed 1.0 -> the 1.0x position).
    assert rows["opacity"]["value"] == 100
    assert panel.slider_to_speed(rows["speed"]["value"]) == pytest.approx(1.0, abs=0.02)


def test_color_rows_carry_ember_default_hex():
    from voicebridge.orb import ember
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    rows = {r["id"]: r for r in panel.panel_rows(O.OrbOptions())}
    for state, core in ember.STATE_CORES.items():
        expected = "#{:02X}{:02X}{:02X}".format(*core)
        assert rows["color:" + state]["hex"] == expected, state


def test_color_rows_honor_override():
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    o = O.OrbOptions(colors={"listening": "#12ab34"})
    rows = {r["id"]: r for r in panel.panel_rows(o)}
    assert rows["color:listening"]["hex"] == "#12AB34"


def test_opacity_slider_roundtrip():
    from voicebridge.orb import panel
    assert panel.slider_to_opacity(panel.opacity_to_slider(0.7)) == pytest.approx(
        0.7, abs=0.01)
    assert panel.opacity_to_slider(0.35) == 0
    assert panel.opacity_to_slider(1.0) == 100
    assert panel.slider_to_opacity(0) == pytest.approx(0.35)
    assert panel.slider_to_opacity(100) == pytest.approx(1.0)


def test_speed_slider_roundtrip():
    from voicebridge.orb import panel
    assert panel.slider_to_speed(panel.speed_to_slider(1.5)) == pytest.approx(
        1.5, abs=0.01)
    # The floor moved to 0.25x (owner 2026-10-17: slow motion must be
    # reachable and visibly slow, not a 4% nudge around the default).
    assert panel.speed_to_slider(panel.SPEED_MIN) == 40
    assert panel.speed_to_slider(2.0) == 200
    assert panel.slider_to_speed(40) == pytest.approx(0.25)
    assert panel.slider_to_speed(200) == pytest.approx(2.0)
    assert panel.slider_to_speed(panel.speed_to_slider(1.0)) == pytest.approx(
        1.0, abs=0.02)


def test_apply_row_change_clamps_and_maps():
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    base = O.OrbOptions()
    assert panel.apply_row_change(base, "size_px", 9999).size_px == O.SIZE_MAX
    assert panel.apply_row_change(
        base, "opacity", 250).opacity == pytest.approx(O.OPACITY_MAX)
    assert panel.apply_row_change(
        base, "speed", 999).speed == pytest.approx(O.SPEED_MAX)
    assert panel.apply_row_change(base, "locked", 1).locked is True
    assert panel.apply_row_change(base, "show_tray", 0).show_tray is False


def test_apply_row_change_color_valid_and_invalid():
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    base = O.OrbOptions()
    got = panel.apply_row_change(base, "color:idle", "#12ab34")
    assert got.colors.get("idle") == "#12AB34"
    # Invalid hex is ignored: unchanged.
    assert panel.apply_row_change(got, "color:idle", "not-a-color") == got
    # Unknown state is ignored.
    assert panel.apply_row_change(base, "color:bogus", "#FFFFFF") == base


def test_apply_row_change_unknown_id_unchanged():
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    base = O.OrbOptions(size_px=200)
    assert panel.apply_row_change(base, "nope", 5) == base
    # And it never raises on garbage.
    assert panel.apply_row_change(None, None, object()) == O.OrbOptions()


def test_settings_panel_symbol_is_guarded():
    from voicebridge.orb import panel
    # Either the class (Qt importable) or the None sentinel; import must
    # not fail either way.
    assert panel.SettingsPanel is None or isinstance(panel.SettingsPanel, type)


@pytest.mark.skipif(NO_DISPLAY, reason="needs a display (X11) for a widget")
def test_settings_panel_contract_live():
    from voicebridge.orb import options as O
    from voicebridge.orb import panel
    if panel.SettingsPanel is None:
        pytest.skip("PySide6 not importable")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = None
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("no Qt application available")
    changes = []
    opts = O.OrbOptions(size_px=200)
    w = panel.SettingsPanel(opts, on_change=changes.append,
                            on_test=lambda s: changes.append(s))
    assert w.current_options().size_px == 200
    w.set_options(O.OrbOptions(size_px=250, locked=True))
    back = w.current_options()
    assert back.size_px == 250 and back.locked is True
    del app
