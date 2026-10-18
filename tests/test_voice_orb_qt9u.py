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
    from voicebridge.orb import ember as _ember
    from voicebridge.orb import qt_app as qa
    # One shared frame budget across both backends (see ember.frame_budget_ms).
    assert qa.FRAME_MS == tk_app.FRAME_MS == _ember.FRAME_MS
    assert qa.POLL_MS == tk_app.POLL_MS == 500
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
        # Sample the REAL centre: the orb's size is configurable, so a
        # hardcoded pixel walked off the nucleus once the default size moved
        # off 154 and the probe landed on the (correctly faint) aura.
        cx, cy = (img.width() - 1) // 2, (img.height() - 1) // 2
        assert img.pixelColor(0, 0).alpha() == 0  # corner transparent
        assert img.pixelColor(cx, cy).alpha() > 200  # nucleus core solid
    finally:
        w.close()


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Qt")
def test_qt_orb_applies_options_and_test_state(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtWidgets", reason="Qt-less env")
    from PySide6.QtWidgets import QApplication
    from voicebridge.orb import geometry as G
    from voicebridge.orb import options as O
    from voicebridge.orb import qt_app as qa

    saved_save = O.save_options
    monkeypatch.setattr(O, "save_options",
                        lambda opts, path=None: saved_save(opts, tmp_path / "o.json"))
    saved_px = G.ORB_PX
    app = QApplication.instance() or QApplication([])
    opts = O.OrbOptions(size_px=200, opacity=0.8, speed=1.5, locked=True,
                        show_tray=False, colors={"listening": "#00FF00"})
    w = qa.QtVoiceOrb(poll=False, client_factory=lambda: None,
                      fullscreen_probe=lambda: False, options=opts,
                      enable_tray=False)
    try:
        assert G.ORB_PX == 200
        assert abs(w.windowOpacity() - 0.8) < 0.05
        assert w._options.locked is True and abs(w._speed - 1.5) < 1e-6
        assert w._color_transition._overrides == {"listening": (0, 255, 0)}
        w.apply_options(O.updated(w._options, size_px=9999, opacity=0.5))
        assert G.ORB_PX == O.SIZE_MAX
        assert abs(w.windowOpacity() - 0.5) < 0.05
        # test-state override wins over the feed, then expires
        w.set_test_state("speaking", 5.0)
        w._apply_poll_payload({"status": None, "events": []})
        assert w.orb_state == "speaking"
        w._test_until = 0.0
        w._apply_poll_payload({"status": None, "events": []})
        assert w.orb_state == "error"  # back to the live feed (offline)
    finally:
        w.close()
        G.ORB_PX = saved_px


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Qt")
def test_qt_orb_position_lock_blocks_drag(monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtWidgets", reason="Qt-less env")
    from PySide6.QtWidgets import QApplication
    from voicebridge.orb import geometry as G
    from voicebridge.orb import options as O
    from voicebridge.orb import qt_app as qa

    saved_save = O.save_options
    monkeypatch.setattr(O, "save_options",
                        lambda opts, path=None: saved_save(opts, tmp_path / "o.json"))
    saved_px = G.ORB_PX
    app = QApplication.instance() or QApplication([])
    w = qa.QtVoiceOrb(poll=False, client_factory=lambda: None,
                      fullscreen_probe=lambda: False,
                      options=O.OrbOptions(locked=True), enable_tray=False)
    try:
        w.move(100, 100)
        w._pos = G.OrbPosition(x=100, y=100)
        w._press = {"x": 0.0, "y": 0.0, "ox": 100, "oy": 100,
                    "zone": "main", "dragging": False}

        class _Ev:
            def globalPosition(self):
                class P:
                    def x(self_inner):
                        return 500.0

                    def y(self_inner):
                        return 500.0
                return P()

            def position(self):
                return self.globalPosition()

        w.mouseMoveEvent(_Ev())
        assert (w._pos.x, w._pos.y) == (100, 100)
        assert w._press["dragging"] is False
    finally:
        w.close()
        G.ORB_PX = saved_px


# --------------------------------------------------------------------------- #
# instant stop: the second press must cancel the live turn, never start one
# --------------------------------------------------------------------------- #
class _FakeToggleClient:
    """Records the orb's voice IPC without any HTTP or a live helper."""

    def __init__(self, helper_running=True, stop_speaking=False):
        self.calls = []
        self._helper_running = helper_running
        if stop_speaking:
            self.stop_speaking = lambda: self.calls.append(("stop",))

    def cancel_request(self, rid):
        self.calls.append(("cancel", rid))
        return {"ok": True}

    def trigger_helper_capture(self):
        if not self._helper_running:
            from voicebridge.orb.client import OrbError
            raise OrbError("ORB_HELPER_NOT_RUNNING", "no helper")
        self.calls.append(("trigger",))
        return {"ok": True}

    def begin_session(self):
        self.calls.append(("begin",))
        return {"ok": True, "request_id": "staged-1"}


class _OrbStub:
    """Attribute-only stand-in: ``QtVoiceOrb.toggle_voice`` needs no widget.

    A real ``QtVoiceOrb`` is a QWidget and cannot be instantiated without a
    display; calling the UNBOUND method on a plain object with the same
    attributes exercises the exact code path hermetically.
    """

    def __init__(self, client, **attrs):
        self._client = client
        self._client_factory = lambda: client
        self.__dict__.update(attrs)

    def _get_client(self):
        # Mirrors QtVoiceOrb._get_client (the method the real widget uses).
        if self._client is None:
            self._client = self._client_factory()
        return self._client


def _bare_qt_orb(client, *, orb_state, active_rid=None, active_ts=0.0,
                 pending_rid=None, pending_ts=0.0):
    pytest.importorskip("PySide6.QtWidgets", reason="Qt-less env")
    from voicebridge.orb import qt_app as qa

    if not hasattr(qa, "QtVoiceOrb"):  # Qt importable but disabled
        pytest.skip("QtVoiceOrb unavailable")
    return _OrbStub(
        client, orb_state=orb_state, _pending_rid=pending_rid,
        _pending_ts=pending_ts, _active_rid=active_rid, _active_ts=active_ts,
        notice_until=0.0)


def test_second_press_during_speaking_cancels_and_never_recaptures():
    import time

    from voicebridge.orb import qt_app as qa

    cli = _FakeToggleClient()
    orb = _bare_qt_orb(cli, orb_state="speaking", active_rid="live-rid",
                       active_ts=time.time() - 1.0)
    qa.QtVoiceOrb.toggle_voice(orb)
    assert cli.calls == [("cancel", "live-rid")], cli.calls
    # The live id is consumed; the next press starts fresh.
    assert orb._active_rid is None


def test_second_press_while_listening_finishes_the_capture():
    import time

    from voicebridge.orb import qt_app as qa

    cli = _FakeToggleClient()
    orb = _bare_qt_orb(cli, orb_state="listening", active_rid="live-rid",
                       active_ts=time.time() - 1.0)
    qa.QtVoiceOrb.toggle_voice(orb)
    assert cli.calls == [("trigger",)], cli.calls


def test_press_while_idle_triggers_capture():
    from voicebridge.orb import qt_app as qa

    cli = _FakeToggleClient()
    orb = _bare_qt_orb(cli, orb_state="idle")
    qa.QtVoiceOrb.toggle_voice(orb)
    assert cli.calls == [("trigger",)], cli.calls


def test_stage_only_fallback_still_stages_and_cancels():
    from voicebridge.orb import qt_app as qa

    cli = _FakeToggleClient(helper_running=False)
    orb = _bare_qt_orb(cli, orb_state="idle")
    qa.QtVoiceOrb.toggle_voice(orb)
    assert cli.calls == [("begin",)], cli.calls
    assert orb._pending_rid == "staged-1"
    # A second press with no live turn cancels the staged request.
    qa.QtVoiceOrb.toggle_voice(orb)
    assert cli.calls == [("begin",), ("cancel", "staged-1")], cli.calls


def test_qt_stop_speaking_preferred_over_cancel_request():
    import time

    from voicebridge.orb import qt_app as qa

    cli = _FakeToggleClient(stop_speaking=True)
    orb = _bare_qt_orb(cli, orb_state="speaking", active_rid="live-rid",
                       active_ts=time.time() - 1.0)
    qa.QtVoiceOrb.toggle_voice(orb)
    # One IPC call: stop_speaking halts the reply AND ends the turn.
    assert cli.calls == [("stop",)], cli.calls
    assert orb._active_rid is None
