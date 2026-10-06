"""Wake command -> RouteTarget wiring (OpenClaw ``voicewake.routing`` port).

Covers the two seams added for routing a spoken wake command to a configured
target session/agent:

* ``voicebridge.voice_edit.wake.WakeMonitor`` — the optional
  ``on_command_route`` callback (``_emit`` dispatches to it; the plain
  single-arg ``on_command`` contract is preserved and any routing failure
  falls back to it);
* ``voicebridge.desktop_helper`` — ``on_wake_command_routed`` (records the
  target, delegates to the existing plain path) and the session seam
  (``_route_session_key`` -> ``HelperClient.begin(session_id=...)``).

Hermetic: no X, no mic, no socket. The routing store is redirected to a
``tmp_path`` file via ``wake_routing.routing_path``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.desktop_helper import (DesktopHelper, HelperClient,
                                        _route_session_key,
                                        _route_target_fields)
from voicebridge.voice_edit import wake_routing as WR
from voicebridge.voice_edit.wake import WakeMonitor


class FakeCfg:
    def __init__(self, wake=None):
        self._d = {"voice_edit": {"beep": False,
                                   "wake": wake or {"enabled": True}}}

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def _monitor(*, on_command, on_command_route=None):
    return WakeMonitor(FakeCfg(), on_command=on_command,
                       on_command_route=on_command_route,
                       is_busy=lambda: False,
                       transcribe=lambda pcm: "")


def _write_routing(tmp_path, doc):
    p = tmp_path / WR.ROUTING_FILENAME
    if isinstance(doc, str):
        p.write_text(doc, encoding="utf-8")
    else:
        import json
        p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# WakeMonitor._emit dispatch
# ---------------------------------------------------------------------------
def test_emit_uses_routed_callback_when_set(monkeypatch):
    monkeypatch.setattr(WR, "routing_path",
                        lambda: Path("/nonexistent/wake_routing.json"))
    seen_routed = []
    seen_plain = []
    m = _monitor(on_command=seen_plain.append,
                 on_command_route=lambda c, t: seen_routed.append((c, t)))
    m._emit("open youtube")
    assert seen_routed == [("open youtube", WR.RouteTarget.current())]
    assert seen_plain == []


def test_emit_uses_plain_callback_when_no_route(monkeypatch):
    monkeypatch.setattr(WR, "routing_path",
                        lambda: Path("/nonexistent/wake_routing.json"))
    seen_plain = []
    m = _monitor(on_command=seen_plain.append)
    m._emit("open youtube")
    assert seen_plain == ["open youtube"]


def test_emit_blank_command_emits_nothing(monkeypatch):
    monkeypatch.setattr(WR, "routing_path",
                        lambda: Path("/nonexistent/wake_routing.json"))
    routed, plain = [], []
    m = _monitor(on_command=plain.append,
                 on_command_route=lambda c, t: routed.append((c, t)))
    m._emit("   ")
    assert routed == [] and plain == []


# ---------------------------------------------------------------------------
# Malformed store / routing failure never raises, still emits
# ---------------------------------------------------------------------------
def test_malformed_store_still_emits_current(monkeypatch, tmp_path):
    bad = _write_routing(tmp_path, "{not valid json")
    monkeypatch.setattr(WR, "routing_path", lambda: bad)
    routed = []
    m = _monitor(on_command=lambda c: None,
                 on_command_route=lambda c, t: routed.append((c, t)))
    m._emit("open youtube")  # must not raise
    assert routed == [("open youtube", WR.RouteTarget.current())]


def test_resolve_failure_falls_back_to_plain(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(WR, "load_config", boom)
    plain, routed = [], []
    m = _monitor(on_command=plain.append,
                 on_command_route=lambda c, t: routed.append((c, t)))
    m._emit("open youtube")  # must not raise
    assert plain == ["open youtube"]
    assert routed == []


def test_routed_sink_raising_falls_back_to_plain(monkeypatch):
    monkeypatch.setattr(WR, "routing_path",
                        lambda: Path("/nonexistent/wake_routing.json"))

    def bad_route(command, target):
        raise RuntimeError("sink exploded")

    plain = []
    m = _monitor(on_command=plain.append, on_command_route=bad_route)
    m._emit("open youtube")  # must not raise
    assert plain == ["open youtube"]


# ---------------------------------------------------------------------------
# resolve_target integration through _emit
# ---------------------------------------------------------------------------
def test_emit_resolves_configured_trigger(monkeypatch, tmp_path):
    store = _write_routing(tmp_path, {
        "defaultTarget": {"mode": "current"},
        "routes": [
            {"trigger": "open youtube",
             "target": {"sessionKey": "agent:robot:main"}},
        ],
    })
    monkeypatch.setattr(WR, "routing_path", lambda: store)
    routed = []
    m = _monitor(on_command=lambda c: None,
                 on_command_route=lambda c, t: routed.append((c, t)))
    m._emit("open youtube")
    assert len(routed) == 1
    cmd, target = routed[0]
    assert cmd == "open youtube"
    assert target == WR.RouteTarget.for_session("agent:robot:main")
    assert target.kind == "session"


def test_emit_unmatched_trigger_is_current(monkeypatch, tmp_path):
    store = _write_routing(tmp_path, {
        "defaultTarget": {"mode": "current"},
        "routes": [
            {"trigger": "robot", "target": {"sessionKey": "agent:robot:main"}},
        ],
    })
    monkeypatch.setattr(WR, "routing_path", lambda: store)
    routed = []
    m = _monitor(on_command=lambda c: None,
                 on_command_route=lambda c, t: routed.append((c, t)))
    m._emit("open youtube")
    assert routed == [("open youtube", WR.RouteTarget.current())]


# ---------------------------------------------------------------------------
# desktop_helper routed handler + session seam
# ---------------------------------------------------------------------------
class _HelperStub:
    """Minimal stand-in: exercises on_wake_command_routed without a real
    helper (no client/UI/mic)."""

    def __init__(self):
        self._wake_route_target = None
        self.calls = []

    def on_wake_command(self, command):
        self.calls.append(command)

    on_wake_command_routed = DesktopHelper.on_wake_command_routed


def test_helper_routed_handler_current_delegates_plain():
    h = _HelperStub()
    h.on_wake_command_routed("open youtube", {"mode": "current"})
    assert h.calls == ["open youtube"]
    assert h._wake_route_target == {"mode": "current"}


def test_helper_routed_handler_session_records_and_delegates():
    h = _HelperStub()
    h.on_wake_command_routed("open youtube", {"sessionKey": "agent:robot:main"})
    assert h.calls == ["open youtube"]
    assert h._wake_route_target == {"sessionKey": "agent:robot:main"}


def test_helper_routed_handler_junk_target_never_raises():
    h = _HelperStub()
    h.on_wake_command_routed("open youtube", object())
    assert h.calls == ["open youtube"]


def test_route_target_fields_and_session_key():
    assert _route_target_fields({"mode": "current"}) == ("current", "", "")
    assert _route_target_fields({"agentId": "robot"}) == ("agent", "robot", "")
    assert _route_target_fields({"sessionKey": "agent:r:main"}) == \
        ("session", "", "agent:r:main")
    # agentId has no safe session derivation on this surface -> current path
    assert _route_session_key({"mode": "current"}) == ""
    assert _route_session_key({"agentId": "robot"}) == ""
    assert _route_session_key({"sessionKey": "agent:r:main"}) == "agent:r:main"
    assert _route_session_key(None) == ""
    assert _route_session_key(object()) == ""


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_helper_client_begin_carries_session_only_when_set():
    captured = []

    def opener(req, timeout=None):
        captured.append(req)
        return _FakeResp(b'{"ok": true, "request_id": "r1"}')

    client = HelperClient("http://127.0.0.1:8890", opener=opener)
    assert client.begin().get("request_id") == "r1"
    assert captured[-1].data == b"{}"  # no session -> unchanged empty payload

    assert client.begin(session_id="agent:robot:main").get("request_id") == "r1"
    assert b'"session_id": "agent:robot:main"' in captured[-1].data