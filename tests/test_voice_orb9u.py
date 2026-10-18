"""Tests for the OS-level voice orb (``voicebridge.orb``).

Hermetic and headless-safe: no Tk construction, no network, no service
contact. Anything needing a display is skipped cleanly (``NO_DISPLAY``).
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

from voicebridge.orb import client as orb_client
from voicebridge.orb import geometry as orb_geo
from voicebridge.orb import persist as orb_persist
from voicebridge.orb import state as orb_state

NO_DISPLAY = not os.environ.get("DISPLAY")


# -- state mapping -----------------------------------------------------------

def _ev(name: str, age_s: float, now: float):
    return {"ts": now - age_s, "event": name}


def test_unknown_maps_to_idle():
    assert orb_state.resolve_orb_state({"active_requests": 0}, [], time.time()) == "idle"
    assert orb_state.resolve_orb_state({}, [{"ts": time.time(), "event": "???"}], time.time()) == "idle"


def test_malformed_inputs_never_raise_and_yield_idle():
    now = time.time()
    assert orb_state.resolve_orb_state(None, [], now) == "idle"
    assert orb_state.resolve_orb_state("nope", "nope", now) == "idle"
    assert orb_state.resolve_orb_state({}, [{"event": "stt.started"}], now) == "idle"
    assert orb_state.resolve_orb_state({}, [{"ts": "yesterday", "event": "stt.started"}], now) == "idle"
    assert orb_state.resolve_orb_state({}, None, now) == "idle"


def test_listening_beats_everything():
    now = time.time()
    status = {"active_requests": 3}
    events = [_ev("tts.speak", 1.0, now), _ev("stt.started", 1.0, now)]
    assert orb_state.resolve_orb_state(status, events, now) == "listening"


def test_speaking_beats_thinking():
    # Owner contract 2026-10-04: GREEN = TALKING whenever talking — a
    # fresh stt.final inside its window used to mask a playing reply.
    now = time.time()
    status = {"active_requests": 2}
    events = [_ev("tts.speak", 1.0, now)]
    assert orb_state.resolve_orb_state(status, events, now) == "speaking"
    events = [_ev("voice_edit.helper.speak", 1.0, now)]
    assert orb_state.resolve_orb_state(status, events, now) == "speaking"


def test_thinking_without_speaking():
    now = time.time()
    status = {"active_requests": 2}
    assert orb_state.resolve_orb_state(status, [], now) == "thinking"


def test_speaking_from_tts_events():
    now = time.time()
    assert orb_state.resolve_orb_state({}, [_ev("tts.speak", 2.0, now)], now) == "speaking"
    assert orb_state.resolve_orb_state({}, [_ev("voice_edit.helper.speak", 2.0, now)], now) == "speaking"
    assert orb_state.resolve_orb_state({}, [_ev("tts.synthesize", 2.0, now)], now) == "speaking"


def test_stale_events_do_not_stick():
    now = time.time()
    assert orb_state.resolve_orb_state({}, [_ev("stt.started", 60.0, now)], now) == "idle"
    assert orb_state.resolve_orb_state({}, [_ev("tts.speak", 60.0, now)], now) == "idle"
    assert orb_state.resolve_orb_state({"active_requests": 0}, [_ev("stt.final", 120.0, now)], now) == "idle"


def test_thinking_from_recent_transcript_completion():
    now = time.time()
    assert orb_state.resolve_orb_state({}, [_ev("stt.final", 3.0, now)], now) == "thinking"
    assert orb_state.resolve_orb_state({}, [_ev("stt.completed", 3.0, now)], now) == "thinking"


def test_capture_signal_counts_as_listening_not_command_accept():
    # Only true capture signals light listening. The command-accept row fires
    # AFTER the owner stops talking, and a 6 s listening window on it masked
    # the thinking color for the whole turn (live 2026-10-05).
    now = time.time()
    assert orb_state.resolve_orb_state(
        {}, [_ev("voice_edit.wake.capturing", 2.0, now)], now) == "listening"
    assert orb_state.resolve_orb_state(
        {}, [_ev("voice_edit.wake.command", 2.0, now)], now) == "idle"


def test_state_names_are_the_os_orb_vocabulary():
    # Owner palette 2026-10-06: the OS orb is the only orb; "working" is a
    # first-class action (amber) alongside idle/listening/thinking/speaking.
    assert set(orb_state.ORB_STATES) == {
        "idle", "listening", "thinking", "working", "speaking"}


def test_working_state_on_tool_exec_events():
    now = time.time()
    # A live tool/action run (start newer than finish) reads "working".
    assert orb_state.resolve_orb_state(
        {"active_requests": 1},
        [_ev("broker.exec.start", 1.0, now)], now) == "working"
    # Finished -> no longer working (falls back to thinking while in flight).
    assert orb_state.resolve_orb_state(
        {"active_requests": 1},
        [_ev("broker.exec.start", 3.0, now),
         _ev("broker.exec.finish", 1.0, now)], now) == "thinking"
    # No tool events -> plain thinking.
    assert orb_state.resolve_orb_state(
        {"active_requests": 1}, [_ev("stt.final", 1.0, now)], now) == "thinking"
    # Working outranks thinking but not speaking.
    speak_win = [_ev("broker.exec.start", 1.0, now),
                 _ev("voice_edit.helper.speak", 0.5, now)]
    assert orb_state.resolve_orb_state(
        {"active_requests": 1}, speak_win, now) == "speaking"


def test_capturing_suppressed_while_speak_recency_active():
    # Own-TTS echo re-arms listening: wake re-fires capturing while the room
    # is voiced, so a capturing edge older than a fresh speak marker must not
    # count as listening (live 2026-10-05).
    now = time.time()
    echo = [_ev("voice_edit.wake.capturing", 2.0, now),
            _ev("voice_edit.helper.speak", 1.0, now)]
    assert orb_state.resolve_orb_state({"active_requests": 0}, echo, now) == "speaking"
    # A stale speak marker (outside the backstop) suppresses nothing.
    stale_speak = [_ev("voice_edit.wake.capturing", 1.0, now),
                   _ev("voice_edit.helper.speak", 30.0, now)]
    assert orb_state.resolve_orb_state({"active_requests": 0}, stale_speak, now) == "listening"
    # Non-capturing capture signals still read live even mid-reply.
    live_capture = [_ev("stt.started", 1.0, now),
                    _ev("voice_edit.helper.speak", 0.5, now)]
    assert orb_state.resolve_orb_state({"active_requests": 0}, live_capture, now) == "listening"


def test_thinking_ends_when_turn_ends():
    # active==0: only a SHORT idle window honors a stale transcript marker
    # (live 2026-10-05: thinking->idle flipped exactly 12 s after stt.final).
    now = time.time()
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [_ev("stt.final", 3.0, now)], now) == "thinking"
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [_ev("stt.final", 6.0, now)], now) == "idle"
    assert orb_state.resolve_orb_state(
        {"active_requests": 0}, [_ev("stt.completed", 6.0, now)], now) == "idle"
    # active>0: the full ceiling still honors it (turn genuinely in flight).
    assert orb_state.resolve_orb_state(
        {"active_requests": 1}, [_ev("stt.final", 6.0, now)], now) == "thinking"
    # Malformed status stays fail-closed (never raises, never thinks).
    assert orb_state.resolve_orb_state(
        "nope", [_ev("stt.final", 6.0, now)], now) == "idle"


def test_recency_backstops_are_short():
    assert orb_state.LISTEN_WINDOW_S == 3.0
    assert orb_state.SPEAK_WINDOW_S == 4.0
    assert orb_state.THINK_WINDOW_S == 12.0
    assert orb_state.THINK_IDLE_WINDOW_S == 4.0
    now = time.time()
    assert orb_state.resolve_orb_state({}, [_ev("stt.started", 4.0, now)], now) == "idle"
    assert orb_state.resolve_orb_state({}, [_ev("tts.speak", 5.0, now)], now) == "idle"
    assert orb_state.resolve_orb_state({}, [_ev("stt.started", 2.0, now)], now) == "listening"
    assert orb_state.resolve_orb_state({}, [_ev("tts.speak", 2.0, now)], now) == "speaking"


# -- toggle planning ----------------------------------------------------------

def test_toggle_idle_plans_begin():
    action, rid = orb_state.plan_toggle(None, 0.0, time.time())
    assert (action, rid) == ("begin", None)


def test_toggle_pending_plans_cancel():
    now = time.time()
    assert orb_state.plan_toggle("abc123", now - 5.0, now) == ("cancel", "abc123")


def test_toggle_expired_pending_plans_begin():
    now = time.time()
    action, rid = orb_state.plan_toggle("abc123", now - 600.0, now)
    assert (action, rid) == ("begin", None)


def test_toggle_malformed_pending_plans_begin():
    assert orb_state.plan_toggle("", time.time(), time.time())[0] == "begin"
    assert orb_state.plan_toggle(None, "soon", time.time())[0] == "begin"


# -- geometry ------------------------------------------------------------------

def test_click_drag_threshold_boundary():
    assert orb_geo.is_drag(6, 0) is False  # exactly at budget: still a click
    assert orb_geo.is_drag(0, 6) is False
    assert orb_geo.is_drag(4, 4) is False  # hypot ~5.66
    assert orb_geo.is_drag(7, 0) is True
    assert orb_geo.is_drag(5, 5) is True  # hypot ~7.07


def test_default_position_is_bottom_right_inside_screen():
    pos = orb_geo.default_position(1920, 1080)
    assert pos.x + orb_geo.ORB_PX <= 1920 - orb_geo.MARGIN + 1
    assert pos.y + orb_geo.ORB_PX <= 1080 - orb_geo.MARGIN + 1
    assert pos.x > 1920 / 2 and pos.y > 1080 / 2


def test_orb_default_size_is_big_waveform():
    # Owner 2026-10-06: enlarged to 168px so the equalizer is legible;
    # the collapsed dot stays 16px (compat constant only — the collapsed
    # state is unreachable live).
    assert orb_geo.ORB_PX == 168
    assert orb_geo.DOT_PX == 16
    assert orb_geo.window_size_px(False) == 168
    assert orb_geo.window_size_px(True) == 16
    pos = orb_geo.default_position(4500, 1080)
    assert (pos.x, pos.y) == (4500 - orb_geo.ORB_PX - orb_geo.MARGIN,
                              1080 - orb_geo.ORB_PX - orb_geo.MARGIN)
    assert pos.collapsed is False


def test_clamp_keeps_orb_reachable():
    clamped = orb_geo.clamp_position(-5000, 9000, 1920, 1080)
    # Full-window bounds for the 68px orb (not the legacy 32px sliver).
    assert 0 <= clamped.x <= 1920 - orb_geo.ORB_PX
    assert 0 <= clamped.y <= 1080 - orb_geo.ORB_PX
    same = orb_geo.clamp_position(100, 100, 1920, 1080)
    assert (same.x, same.y) == (100, 100)


def test_clamp_tiny_viewport_never_negative():
    clamped = orb_geo.clamp_position(50, 50, 10, 10)
    assert clamped.x >= 0 and clamped.y >= 0


# -- persist --------------------------------------------------------------------

def test_persist_round_trip(tmp_path):
    path = tmp_path / "orb.json"
    pos = orb_geo.OrbPosition(x=321, y=654, collapsed=True)
    orb_persist.save_position(str(path), pos)
    loaded = orb_persist.load_position(str(path))
    assert loaded == pos
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"x", "y", "collapsed"}  # no secrets, no extras


def test_persist_corrupt_and_missing_yield_none(tmp_path):
    missing = tmp_path / "absent.json"
    assert orb_persist.load_position(str(missing)) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert orb_persist.load_position(str(bad)) is None
    wrong_shape = tmp_path / "shape.json"
    wrong_shape.write_text('{"x": "far", "token": "sk-secret"}', encoding="utf-8")
    assert orb_persist.load_position(str(wrong_shape)) is None


def test_persist_save_never_raises(tmp_path):
    orb_persist.save_position(str(tmp_path / "no" / "dir" / "orb.json"),
                              orb_geo.OrbPosition(x=1, y=2))


def test_state_file_lives_outside_repo():
    assert "qubit-voicebridge" not in str(orb_persist.default_path()) or \
        str(orb_persist.default_path()).startswith(str(orb_persist.state_dir()))
    assert str(orb_persist.default_path()).endswith("voice_orb.json")
    assert "/home/trick/Projects/Repos/" not in str(orb_persist.default_path())


# -- client ----------------------------------------------------------------------

def _getter(mapping):
    def get(*keys, default=""):
        node = mapping
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node
    return get


def test_service_base_explicit_url_wins():
    get = _getter({"voice_edit": {"service_url": "http://127.0.0.1:9999/"}})
    assert orb_client.service_base(get) == "http://127.0.0.1:9999"


def test_service_base_from_webui_host_port():
    get = _getter({"webui": {"host": "127.0.0.1", "port": 8890}})
    assert orb_client.service_base(get) == "http://127.0.0.1:8890"


def test_service_base_wildcard_host_becomes_loopback():
    get = _getter({"webui": {"host": "0.0.0.0", "port": 8890}})
    assert orb_client.service_base(get) == "http://127.0.0.1:8890"


def test_service_base_defaults_match_dashboard():
    assert orb_client.service_base(_getter({})) == "http://127.0.0.1:8890"


def test_client_sends_both_session_headers_and_agent_prefix():
    seen = {}

    def opener(req, timeout=10):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["method"] = req.get_method()

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true}'
        return Resp()

    cli = orb_client.OrbClient("http://127.0.0.1:8890", "tok123", opener=opener)
    assert cli.get_status() == {"ok": True}
    assert seen["url"] == "http://127.0.0.1:8890/agent/api/voice-edit/status"
    assert seen["method"] == "GET"
    # urllib normalises header capitalisation on the wire; compare folded.
    lowered = {k.lower(): v for k, v in seen["headers"].items()}
    assert lowered.get("x-api-token") == "tok123"
    assert lowered.get("x-qubit-session-token") == "tok123"


def test_client_begin_and_cancel_paths():
    calls = []

    def opener(req, timeout=10):
        calls.append((req.get_method(), req.full_url))
        body = b'{"ok": true, "request_id": "r1"}' if req.data else b'{"ok": true}'

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return body
        return Resp()

    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t", opener=opener)
    out = cli.begin_session()
    assert out["request_id"] == "r1"
    cli.cancel_request("r1")
    methods_urls = [(m, u.split("/agent")[1]) for m, u in calls]
    assert ("POST", "/api/voice-edit/session") in methods_urls
    assert ("POST", "/api/voice-edit/r1/cancel") in methods_urls


def test_client_errors_are_typed_never_raw():
    import urllib.error

    def opener(req, timeout=10):
        raise urllib.error.URLError("connection refused")
    cli = orb_client.OrbClient("http://127.0.0.1:9", "", opener=opener)
    with pytest.raises(orb_client.OrbError) as exc:
        cli.get_status()
    assert exc.value.code == "ORB_SERVICE_UNREACHABLE"


def test_client_never_embeds_token_in_error():
    import urllib.error

    def opener(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "tok-SUPERSECRET", opener=opener)
    with pytest.raises(orb_client.OrbError) as exc:
        cli.get_status()
    assert "SUPERSECRET" not in str(exc.value)


# -- offscreen-reset (lane: orb parks offscreen, x=4388 on 2580-wide) ------

def test_offscreen_persisted_x_resets_to_default():
    screen_w, screen_h = 2580, 1080
    stored = orb_geo.OrbPosition(x=4388, y=968, collapsed=True)
    resolved, reset, _reason = orb_geo.resolve_initial_position(stored, screen_w, screen_h)
    assert reset is True
    assert resolved == orb_geo.default_position(screen_w, screen_h)


def test_negative_coords_reset_to_default():
    screen_w, screen_h = 2580, 1080
    stored = orb_geo.OrbPosition(x=-40, y=100, collapsed=False)
    resolved, reset, _reason = orb_geo.resolve_initial_position(stored, screen_w, screen_h)
    assert reset is True
    assert resolved == orb_geo.default_position(screen_w, screen_h)


def test_y_overflow_resets_to_default():
    screen_w, screen_h = 2580, 1080
    stored = orb_geo.OrbPosition(x=100, y=2000, collapsed=False)
    resolved, reset, _reason = orb_geo.resolve_initial_position(stored, screen_w, screen_h)
    assert reset is True
    assert resolved == orb_geo.default_position(screen_w, screen_h)


def test_onscreen_position_is_kept():
    screen_w, screen_h = 2580, 1080
    stored = orb_geo.OrbPosition(x=200, y=200, collapsed=False)
    resolved, reset, _reason = orb_geo.resolve_initial_position(stored, screen_w, screen_h)
    assert reset is False
    assert (resolved.x, resolved.y) == (200, 200)
    assert resolved.collapsed is False


def test_dot_mode_bounds_are_size_aware():
    # Owner 2026-10-04 (de-chromed orb): the collapsed state is
    # unreachable, so resolve migrates collapsed=True to always-expanded
    # instead of honouring the dot. x=2550 holds a 16px dot but NOT a
    # 68px orb on a 2580-wide screen (2550+16=2566 fits; 2550+68=2618
    # overflows) — so the dot-parked position now resets to the default
    # rather than trapping the window as a dot.
    screen_w, screen_h = 2580, 1080
    dot = orb_geo.OrbPosition(x=2550, y=100, collapsed=True)
    resolved_dot, reset_dot, _r1 = orb_geo.resolve_initial_position(dot, screen_w, screen_h)
    assert reset_dot is True
    assert resolved_dot == orb_geo.default_position(screen_w, screen_h)
    assert resolved_dot.collapsed is False
    orb = orb_geo.OrbPosition(x=2550, y=100, collapsed=False)
    resolved_orb, reset_orb, _r2 = orb_geo.resolve_initial_position(orb, screen_w, screen_h)
    assert reset_orb is True
    assert resolved_orb == orb_geo.default_position(screen_w, screen_h)


def test_persisted_collapsed_true_migrates_to_expanded():
    # A dot-parked position that CAN hold the full 68px orb keeps its
    # x/y but resolves expanded — never as a dot.
    screen_w, screen_h = 2580, 1080
    stored = orb_geo.OrbPosition(x=200, y=200, collapsed=True)
    resolved, reset, _reason = orb_geo.resolve_initial_position(
        stored, screen_w, screen_h)
    assert reset is False
    assert (resolved.x, resolved.y) == (200, 200)
    assert resolved.collapsed is False


def test_old_sliver_park_now_resets():
    # The old clamp kept a 32px sliver on-screen (x = w-32); that park is
    # mostly offscreen and must now reset to the default.
    screen_w, screen_h = 2580, 1080
    stored = orb_geo.OrbPosition(x=screen_w - 32, y=100, collapsed=False)
    resolved, reset, _reason = orb_geo.resolve_initial_position(stored, screen_w, screen_h)
    assert reset is True
    assert resolved == orb_geo.default_position(screen_w, screen_h)


def test_resolve_is_idempotent():
    screen_w, screen_h = 2580, 1080
    stored = orb_geo.OrbPosition(x=4388, y=968, collapsed=True)
    first, _, _ = orb_geo.resolve_initial_position(stored, screen_w, screen_h)
    second, reset2, _ = orb_geo.resolve_initial_position(first, screen_w, screen_h)
    assert reset2 is False
    assert second == first


def test_clamp_keeps_full_window_onscreen_live_dims():
    # Clamp uses the passed (live) screen dims: a 68px orb never extends
    # past the edge, in either mode.
    clamped = orb_geo.clamp_position(4388, 968, 2580, 1080)
    assert clamped.x + orb_geo.ORB_PX <= 2580
    assert clamped.y + orb_geo.ORB_PX <= 1080
    clamped_dot = orb_geo.clamp_position(4388, 968, 2580, 1080, collapsed=True)
    assert clamped_dot.x + orb_geo.DOT_PX <= 2580
    assert clamped_dot.y + orb_geo.DOT_PX <= 1080


# -- focus discipline (static property, no display needed) -----------------------

def test_orb_window_never_takes_focus():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "voicebridge" / "orb" / "app.py").read_text()
    for banned in ("focus_force", "focus_set", "grab_set", "grab_current"):
        assert banned not in src, f"orb must never call {banned} (focus discipline)"
    assert "takefocus" in src  # widgets explicitly opt out of focus


def test_orb_package_import_needs_no_display():
    import voicebridge.orb.app as app_mod  # noqa: F401  (import only, no Tk())
    import voicebridge.orb.__main__ as main_mod  # noqa: F401
    assert callable(main_mod.main)


# -- ember render wiring (owner 2026-10-04: app draws ember, not teal) -----

class _FakeOrbCanvas:
    """Canvas-like recorder for the app draw helper (no Tk, no window)."""

    def __init__(self) -> None:
        self.ovals = []
        self.lines = []
        self.texts = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append((x0, y0, x1, y1, kw))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((pts, kw))
        return len(self.lines)

    def create_text(self, x, y, **kw):
        self.texts.append((x, y, kw))
        return len(self.texts)

    def delete(self, *args):
        pass

    def configure(self, **kw):
        pass


def test_app_draws_ember_bands_for_every_state(monkeypatch):
    # The app defaults to the Qubit-mark renderer (owner 2026-10-17); these
    # ember-band assertions pin the opt-in ember look explicitly.
    monkeypatch.setenv("QUBIT_ORB_STYLE", "ember")
    from voicebridge.orb import app as orb_app
    for st in ("idle", "listening", "thinking", "speaking"):
        fc = _FakeOrbCanvas()
        stats = orb_app.draw_orb_frame(fc, 68, st, 4.2)
        assert stats["lines"] == 5, st  # ember's 5 wave bands
        # Point counts shrink with window size (fewer steps across 69px);
        # what matters is every band draws a real polyline (>=10 pts each).
        assert stats["points"] >= 50, st
        assert stats["ovals"] >= 1 + 3, st  # core + min rings


def test_app_unknown_state_falls_back_to_idle_frame(monkeypatch):
    monkeypatch.setenv("QUBIT_ORB_STYLE", "ember")
    from voicebridge.orb import app as orb_app
    bogus = orb_app.draw_orb_frame(_FakeOrbCanvas(), 68, "warp-drive", 4.2)
    idle = orb_app.draw_orb_frame(_FakeOrbCanvas(), 68, "idle", 4.2)
    assert bogus["lines"] == idle["lines"] == 5
    assert bogus["points"] == idle["points"]


def test_app_speaking_brighter_than_idle(monkeypatch):
    monkeypatch.setenv("QUBIT_ORB_STYLE", "ember")
    from voicebridge.orb import app as orb_app
    idle = orb_app.draw_orb_frame(_FakeOrbCanvas(), 68, "idle", 4.2)
    speak = orb_app.draw_orb_frame(_FakeOrbCanvas(), 68, "speaking", 4.2)

    def _lum(fills):
        tot = sum(sum(int(f[i:i + 2], 16) for i in (1, 3, 5)) for f in fills)
        return tot / max(1, len(fills))
    assert _lum(speak["fills"]) > _lum(idle["fills"])


def test_app_reduced_motion_frame_is_static():
    from voicebridge.orb import app as orb_app
    c1, c2 = _FakeOrbCanvas(), _FakeOrbCanvas()
    orb_app.draw_orb_frame(c1, 123, "speaking", 5.0, reduced_motion=True)
    orb_app.draw_orb_frame(c2, 123, "speaking", 50.0, reduced_motion=True)
    assert [p for line in c1.lines for p in line[0]] == \
        [p for line in c2.lines for p in line[0]]


def test_app_collapsed_draws_dot_only_and_pauses_ember():
    # Pause-when-collapsed: no wave bands animate under the dot.
    from voicebridge.orb import app as orb_app
    fc = _FakeOrbCanvas()
    stats = orb_app.draw_orb_frame(fc, 16, "speaking", 4.2, collapsed=True)
    assert stats["lines"] == 0
    assert stats["ovals"] >= 1  # the dot itself
    assert stats["collapsed"] is True


def test_app_draws_no_chrome_but_keeps_indicators():
    # Owner 2026-10-04: the x/- buttons were removed outright — the
    # frame must draw NO chrome ovals/texts at the old button rects
    # (top-left 8,6-28,26 and top-right size-28,6-size-8,26), while the
    # offline/notice indicator dots still render.
    from voicebridge.orb import app as orb_app
    size = 68
    fc = _FakeOrbCanvas()
    orb_app.draw_orb_frame(fc, size, "idle", 1.0)
    ovals = [(x0, y0, x1, y1) for x0, y0, x1, y1, _kw in fc.ovals]
    # Chrome buttons were top-edge-anchored strips (old ×/− rects sat at
    # y0≈6 with ~20px height in the top corners). Centered glow/core
    # layers legitimately cover corners at small sizes and are not chrome:
    # only a button-height strip touching the top edge in a corner counts.
    def _is_chrome_strip(x0, y0, x1, y1):
        h = y1 - y0
        return (12 <= h <= 28 and y0 <= 8
                and (x0 <= 10 or x1 >= size - 10))
    assert not any(_is_chrome_strip(*o) for o in ovals)
    # Binary "1"/"0" fragments and math-symbol/equation glyphs are legitimate
    # text now; the old close/collapse "x"/"-" chrome buttons must be gone.
    assert all(isinstance(kw.get("text"), str) and kw.get("text")
               and kw.get("text") not in ("x", "-", "X")
               for (_x, _y, kw) in fc.texts)
    off = _FakeOrbCanvas()
    orb_app.draw_orb_frame(off, size, "idle", 1.0, offline=True)
    assert len(off.ovals) > len(fc.ovals)  # offline dot still drawn
    note = _FakeOrbCanvas()
    orb_app.draw_orb_frame(note, size, "idle", 1.0, notice=True)
    assert len(note.ovals) > len(fc.ovals)  # notice dot still drawn


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Tk()")
def test_tk_initial_geometry_applies_persisted_position():
    # The loaded/default position must reach the window: without this the
    # orb opens at the WM default (top-left) no matter what is persisted.
    # Owner 2026-10-04: placement is monitor-aware -- a PRIMARY-onscreen
    # position applies verbatim; a non-primary one (x=4292 lives on
    # HDMI-1) resets to the primary default.
    from voicebridge.orb import app as orb_app
    from voicebridge.orb import geometry as g
    from voicebridge.orb import persist as p
    want = g.OrbPosition(x=2200, y=800, collapsed=False)
    real_load = p.load_position
    p.load_position = lambda path=None: want  # noqa: E731 - test seam
    try:
        orb = orb_app.VoiceOrb(poll=False)
        try:
            size = g.DOT_PX if orb._pos.collapsed else g.ORB_PX
            assert (orb._pos.x, orb._pos.y) == (want.x, want.y)
            # Withdrawn windows report 1x1 until mapped, so prove the
            # position suffix plus the canvas at the new size (live
            # mapping size is proven separately via getwindowgeometry).
            assert orb.root.geometry().endswith(f"+{want.x}+{want.y}")
            assert int(orb.canvas.cget("width")) == size == orb_geo.ORB_PX
            assert int(orb.canvas.cget("height")) == size == orb_geo.ORB_PX
        finally:
            orb.close()
    finally:
        p.load_position = real_load


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Tk()")
def test_tk_non_primary_persisted_position_resets_to_primary():
    # x=4292 validates against the 4500-wide virtual desktop but lives
    # on HDMI-1 (secondary): the orb must reset to the primary default,
    # never park unwatched. Runs against the live xrandr layout.
    from voicebridge.orb import app as orb_app
    from voicebridge.orb import geometry as g
    from voicebridge.orb import monitors as m
    from voicebridge.orb import persist as p
    rect = m.target_rect()
    if rect is None:  # no xrandr (headless/Wayland): nothing to prove live
        pytest.skip("no xrandr target on this host")
    off = g.OrbPosition(x=4292, y=872, collapsed=False)
    real_load = p.load_position
    p.load_position = lambda path=None: off  # noqa: E731 - test seam
    try:
        orb = orb_app.VoiceOrb(poll=False)
        try:
            default = g.default_position_for_rect(rect)
            assert (orb._pos.x, orb._pos.y) == (default.x, default.y)
            assert orb._pos.x + g.ORB_PX <= rect.x + rect.w
        finally:
            orb.close()
    finally:
        p.load_position = real_load
        p.load_position = real_load


@pytest.mark.skipif(NO_DISPLAY, reason="needs an X11 display for Tk()")
def test_tk_construction_smoke():
    from voicebridge.orb.app import VoiceOrb
    orb = VoiceOrb(poll=False)
    try:
        assert orb.state == "idle"
        assert orb.root.winfo_exists()
    finally:
        orb.close()
