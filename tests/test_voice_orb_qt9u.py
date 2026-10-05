"""Qt orb view layer (voicebridge/orb/qt_app.py + qt_compat.py).

Hermetic by default (no display, no Qt needed); the two live-widget
tests skip cleanly without a display or Qt, mirroring
``tests/test_voice_orb9u.py`` (``NO_DISPLAY``).
"""
from __future__ import annotations

import os

import pytest

NO_DISPLAY = not os.environ.get("DISPLAY")


def test_qt_compat_import_needs_no_display():
    import voicebridge.orb.qt_compat as qc
    assert callable(qc.ensure_xcb_cursor)
    assert callable(qc.qt_importable)
    assert callable(qc.qt_usable_on_x11)


def test_ensure_xcb_cursor_returns_bool_never_raises():
    from voicebridge.orb import qt_compat as qc
    out = qc.ensure_xcb_cursor(extra_paths=["/nonexistent/libxcb-cursor.so.0"])
    assert isinstance(out, bool)


def test_qt_app_import_needs_no_display_or_qt():
    import voicebridge.orb.qt_app as qa
    for name in ("backend_wanted", "zone_at", "band_pen", "core_stops",
                 "ring_pen"):
        assert callable(getattr(qa, name)), name


def test_backend_wanted_parses_env(monkeypatch):
    from voicebridge.orb import qt_app as qa
    monkeypatch.setenv("QUBIT_ORB_BACKEND", "qt")
    assert qa.backend_wanted() == "qt"
    monkeypatch.setenv("QUBIT_ORB_BACKEND", "TK")
    assert qa.backend_wanted() == "tk"
    monkeypatch.delenv("QUBIT_ORB_BACKEND", raising=False)
    assert qa.backend_wanted() == ""


def test_zone_at_has_no_chrome():
    # Owner 2026-10-04: the x/- buttons were removed outright — every
    # hit is main, including the old chrome corners, at any size.
    from voicebridge.orb import qt_app as qa
    for size in (68, 192, 16):
        assert qa.zone_at(size - 18, 16, size, False) == "main"
        assert qa.zone_at(18, 16, size, False) == "main"
        assert qa.zone_at(96, 96, size, False) == "main"
        assert qa.zone_at(0, 0, size, False) == "main"
        assert qa.zone_at(size - 18, 16, size, True) == "main"
    assert qa.zone_at("bad", None, 154, False) == "main"


def test_zone_at_matches_tk_rule():
    """Both views report no chrome zones (Tk needs a display; compare source)."""
    from pathlib import Path
    for name in ("app.py", "qt_app.py"):
        src = (Path(__file__).resolve().parent.parent
               / "voicebridge" / "orb" / name).read_text()
        assert '(x - (size - 18)) ** 2 + (y - 16) ** 2 <= 100' not in src
        assert '(x - 18) ** 2 + (y - 16) ** 2 <= 100' not in src
        assert '"close"' not in src and '"collapse"' not in src


def test_cadence_matches_tk_view():
    from voicebridge.orb import app as tk_app
    from voicebridge.orb import qt_app as qa
    assert qa.FRAME_MS == tk_app.FRAME_MS == 33
    assert qa.POLL_MS == tk_app.POLL_MS == 1000
    assert qa.NOTICE_S == tk_app.NOTICE_S


def test_band_pen_state_mapping():
    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as qa
    for bi in range(len(E.BAND_SPECS)):
        rgb_spk, a_spk, w_spk = qa.band_pen(bi, "speaking")
        rgb_idle, a_idle, _ = qa.band_pen(bi, "idle")
        # Single source: Qt bands read ember.band_colors per state
        # (owner 2026-10-04: speaking green, idle ember-orange).
        assert rgb_spk == tuple(E.band_colors("speaking")[bi % 5])
        assert rgb_idle == tuple(E.band_colors("idle")[bi % 5])
        assert a_spk > a_idle > 0.0
        assert 0.0 < a_spk <= 1.0
        assert w_spk == (2 if bi >= 3 else 1)
    # unknown state falls back to idle (fail-safe, same as ember)
    assert qa.band_pen(0, "nope") == qa.band_pen(0, "idle")


def test_core_stops_shape():
    from voicebridge.orb import qt_app as qa
    stops = qa.core_stops("speaking")
    assert [s for s, _ in stops] == [0.0, 0.55, 1.0]
    assert stops[-1][1][3] == 0  # transparent edge
    assert stops[0][1][3] >= stops[1][1][3] > 0  # highlight hottest
    idle = qa.core_stops("idle")
    assert idle[1][1][3] < stops[1][1][3]  # idle dimmer than speaking


def test_ring_pen_delegates_to_ember():
    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as qa
    for ph in (0.0, 0.25, 0.5, 0.9):
        alpha, width = qa.ring_pen(ph, 0, "thinking")
        params = E.resolve_params("thinking")
        expect = min(1.0, 0.25 + E.ring_alpha(ph, params["brightness"]) * 2)
        assert alpha == pytest.approx(expect)
        assert width == 2
    assert qa.ring_pen(0.5, 3, "thinking")[1] == 1


def test_qt_view_never_takes_focus_source_scan():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "voicebridge" / "orb" / "qt_app.py").read_text()
    for banned in ("activateWindow", "setFocus(", "grabMouse",
                   "grabKeyboard", "focus_force", "focus_set"):
        assert banned not in src, f"qt orb must never call {banned}"
    assert "WindowDoesNotAcceptFocus" in src
    assert "WA_ShowWithoutActivating" in src
    assert "NoFocus" in src


def test_resolve_backend_env_tk(monkeypatch):
    from voicebridge.orb import __main__ as m
    monkeypatch.setenv("QUBIT_ORB_BACKEND", "tk")
    assert m._resolve_backend() == "tk"


def test_resolve_backend_follows_probe(monkeypatch):
    from voicebridge.orb import __main__ as m
    from voicebridge.orb import qt_compat as qc
    monkeypatch.delenv("QUBIT_ORB_BACKEND", raising=False)
    monkeypatch.setattr(qc, "qt_usable_on_x11", lambda: True)
    assert m._resolve_backend() == "qt"
    monkeypatch.setattr(qc, "qt_usable_on_x11", lambda: False)
    assert m._resolve_backend() == "tk"


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Qt")
def test_qt_widget_translucent_no_focus_flags():
    pytest.importorskip("PySide6.QtWidgets", reason="Qt-less env")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from voicebridge.orb import qt_app as qa
    app = QApplication.instance() or QApplication([])
    w = qa.QtVoiceOrb(poll=False,
                      client_factory=lambda: None,
                      fullscreen_probe=lambda: False)
    try:
        assert w.testAttribute(Qt.WA_TranslucentBackground)
        assert bool(w.windowFlags() & Qt.FramelessWindowHint)
        assert bool(w.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        assert w.focusPolicy() == Qt.NoFocus
    finally:
        w.close()


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Qt")
def test_qt_quit_after_exits():
    """Regression: request_quit must end the event loop (close() only hides)."""
    import time
    pytest.importorskip("PySide6.QtWidgets", reason="Qt-less env")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from voicebridge.orb import qt_app as qa
    app = QApplication.instance() or QApplication([])
    w = qa.QtVoiceOrb(poll=False,
                      client_factory=lambda: None,
                      fullscreen_probe=lambda: False)
    try:
        w.move(0, 0)
        w.show()
        QTimer.singleShot(300, w.request_quit)
        QTimer.singleShot(5000, app.quit)  # backstop, never the exit path
        t0 = time.monotonic()
        app.exec()
        assert time.monotonic() - t0 < 4.5
    finally:
        w.close()


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Qt")
def test_qt_widget_paints_frame():
    pytest.importorskip("PySide6.QtWidgets", reason="Qt-less env")
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from voicebridge.orb import qt_app as qa
    app = QApplication.instance() or QApplication([])
    w = qa.QtVoiceOrb(poll=False,
                      client_factory=lambda: None,
                      fullscreen_probe=lambda: False)
    try:
        w.move(0, 0)
        w.orb_state = "speaking"
        w.show()
        app.processEvents()
        w.repaint()
        app.processEvents()
        assert w.frames >= 1
        pix = w.grab()
        assert not pix.isNull()
        img = pix.toImage().convertToFormat(QImage.Format_RGBA8888)
        assert img.pixelColor(0, 0).alpha() == 0  # corner transparent
        assert img.pixelColor(77, 77).alpha() > 200  # core solid (154px orb)
    finally:
        w.close()
