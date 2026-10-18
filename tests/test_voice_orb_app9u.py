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


# --------------------------------------------------------------------------- #
# Tk instant stop: a second press cancels the live turn (never re-captures)
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


class _TkOrbStub:
    """Attribute-only stand-in: ``VoiceOrb.toggle_voice`` needs no widget.

    A real ``VoiceOrb`` is a Tk root and cannot be built without a display;
    calling the UNBOUND method on a plain object with the same attributes
    exercises the exact code path hermetically.
    """

    def __init__(self, client, **attrs):
        self._client = client
        self._client_factory = lambda: client
        self.__dict__.update(attrs)

    def _get_client(self):
        # Mirrors VoiceOrb._get_client (the method the real app uses).
        if self._client is None:
            self._client = self._client_factory()
        return self._client


def _bare_tk_orb(client, *, state, active_rid=None, active_ts=0.0,
                 pending_rid=None, pending_ts=0.0):
    return _TkOrbStub(
        client, state=state, _pending_rid=pending_rid, _pending_ts=pending_ts,
        _active_rid=active_rid, _active_ts=active_ts, notice_until=0.0)


def test_tk_second_press_during_speaking_cancels_and_never_recaptures():
    import time

    from voicebridge.orb import app as orb_app

    cli = _FakeToggleClient()
    orb = _bare_tk_orb(cli, state="speaking", active_rid="live-rid",
                       active_ts=time.time() - 1.0)
    orb_app.VoiceOrb.toggle_voice(orb)
    assert cli.calls == [("cancel", "live-rid")], cli.calls
    # The live id is consumed; the next press starts fresh.
    assert orb._active_rid is None


def test_tk_second_press_while_listening_finishes_the_capture():
    import time

    from voicebridge.orb import app as orb_app

    cli = _FakeToggleClient()
    orb = _bare_tk_orb(cli, state="listening", active_rid="live-rid",
                       active_ts=time.time() - 1.0)
    orb_app.VoiceOrb.toggle_voice(orb)
    assert cli.calls == [("trigger",)], cli.calls


def test_tk_press_while_idle_triggers_capture():
    from voicebridge.orb import app as orb_app

    cli = _FakeToggleClient()
    orb = _bare_tk_orb(cli, state="idle")
    orb_app.VoiceOrb.toggle_voice(orb)
    assert cli.calls == [("trigger",)], cli.calls


def test_tk_stage_only_fallback_still_stages_and_cancels():
    from voicebridge.orb import app as orb_app

    cli = _FakeToggleClient(helper_running=False)
    orb = _bare_tk_orb(cli, state="idle")
    orb_app.VoiceOrb.toggle_voice(orb)
    assert cli.calls == [("begin",)], cli.calls
    assert orb._pending_rid == "staged-1"
    # A second press with no live turn cancels the staged request.
    orb_app.VoiceOrb.toggle_voice(orb)
    assert cli.calls == [("begin",), ("cancel", "staged-1")], cli.calls


def test_tk_stop_speaking_preferred_over_cancel_request():
    import time

    from voicebridge.orb import app as orb_app

    cli = _FakeToggleClient(stop_speaking=True)
    orb = _bare_tk_orb(cli, state="speaking", active_rid="live-rid",
                       active_ts=time.time() - 1.0)
    orb_app.VoiceOrb.toggle_voice(orb)
    # One IPC call: stop_speaking halts the reply AND ends the turn.
    assert cli.calls == [("stop",)], cli.calls


def test_tk_staged_cancel_falls_back_when_stop_speaking_raises():
    import time

    from voicebridge.orb import app as orb_app
    from voicebridge.orb.client import OrbError

    cli = _FakeToggleClient()

    def _no_helper():
        raise OrbError("ORB_HELPER_NOT_RUNNING", "no helper")

    cli.stop_speaking = _no_helper
    # Stage-only fallback (helper down): a fresh staged request, no live turn.
    orb = _bare_tk_orb(cli, state="idle", pending_rid="staged-1",
                       pending_ts=time.time() - 1.0)
    orb_app.VoiceOrb.toggle_voice(orb)
    # stop_speaking is present but the helper is down, so the staged request
    # must still be cancelled rather than left stuck.
    assert cli.calls == [("cancel", "staged-1")], cli.calls
    assert orb._pending_rid is None


def test_tk_poll_payload_learns_the_live_request_id():
    import time

    from voicebridge.orb import app as orb_app

    orb = object.__new__(orb_app.VoiceOrb)
    orb._client = object()
    orb._display = None
    orb.state = "idle"
    orb.offline = False
    orb.ingame_override = False
    now = time.time()
    orb_app.VoiceOrb._apply_poll_payload(orb, {
        "status": {"active_requests": 0},
        "events": [{"event": "voice_edit.helper.transcript",
                    "ts": now - 1.0, "request_id": "learned-rid"}],
        "settings": None})
    assert orb._active_rid == "learned-rid"
    assert orb._active_ts == now - 1.0
