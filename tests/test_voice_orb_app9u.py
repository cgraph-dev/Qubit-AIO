"""Orb app layer: geometry size seam + QtVoiceOrb app surface (pure checks).

The Qt-widget integration (options applied to a real window, test-state
override, lock) lives in ``test_voice_orb_qt9u.py`` so it shares that
file's single long-lived QApplication (a lone Qt test in a fresh process
aborts on teardown — a pre-existing PySide6 lifetime quirk).
"""
from __future__ import annotations


def test_set_orb_size_clamps_and_returns():
    from voicebridge.orb import geometry as G
    saved = G.ORB_PX
    try:
        assert G.set_orb_size(200) == 200 and G.ORB_PX == 200
        assert G.set_orb_size(9999) == 400
        assert G.set_orb_size(1) == 48
        assert G.set_orb_size("bad") == G.ORB_PX  # unchanged, no raise
    finally:
        G.ORB_PX = saved


def test_qtvoiceorb_exposes_app_surface():
    # No widget constructed: just prove the app-layer API exists on the
    # class (guarded when Qt is unavailable).
    from voicebridge.orb import qt_app as qa
    cls = getattr(qa, "QtVoiceOrb", None)
    if cls is None:
        return
    for name in ("apply_options", "set_test_state", "open_settings",
                 "_maybe_start_tray", "_apply_window_opacity"):
        assert hasattr(cls, name), name
