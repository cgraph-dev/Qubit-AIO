"""W2 — chat transport served through the production compat dispatcher.

Every request below goes through ``voicebridge.hermes_compat.dispatch`` — the
exact function ``voicebridge.webui`` calls for every ``/agent/api/*`` (and
``/api/*``) dashboard request — so the proof exercises the real caller, not a
handler in isolation. The model transport is stubbed; the live Brain4 proof is
in ``tests/test_qbpv2_hermes_brain4.py``.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _stub(monkeypatch, reply="route reply"):
    import voicebridge.local_llm as _llm

    def _fake(text, conversation_prompt="", model="qubit-brain4",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None):
        return reply

    monkeypatch.setattr(_llm, "chat_local", _fake)
    monkeypatch.setattr(
        _llm, "chat_local_stream",
        lambda text, conversation_prompt="", model="qubit-brain4", timeout_s=4.0,
        max_tokens=100, context="", on_sentence=None, should_stop=None,
        num_ctx=None: (on_sentence and on_sentence(reply), reply)[1])


def test_every_chat_route_is_registered_and_reaches_a_handler():
    from voicebridge import hermes_compat as HC

    expected = {
        ("POST", "/api/qubit/chat"): "h_chat_turn",
        ("GET", "/api/qubit/chat/sessions"): "h_chat_sessions_list",
        ("POST", "/api/qubit/chat/sessions"): "h_chat_session_create",
        ("POST", "/api/qubit/chat/sessions/import"): "h_chat_session_import",
        ("GET", "/api/qubit/chat/sessions/chat-1"): "h_chat_session_show",
        ("GET", "/api/qubit/chat/sessions/chat-1/messages"):
            "h_chat_session_messages",
        ("GET", "/api/qubit/chat/sessions/chat-1/export"): "h_chat_session_export",
        ("PATCH", "/api/qubit/chat/sessions/chat-1"): "h_chat_session_patch",
        ("DELETE", "/api/qubit/chat/sessions/chat-1"): "h_chat_session_delete",
        ("POST", "/api/qubit/chat/cancel"): "h_chat_cancel",
        ("POST", "/api/qubit/chat/retry"): "h_chat_retry",
        ("POST", "/api/qubit/chat/pty"): "h_chat_pty",
    }
    for (method, path), handler in expected.items():
        route, params = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["handler"] == handler, (method, path, route["handler"])
        assert handler in HC.HANDLERS, handler
    # Session share routes must not be shadowed by the chat transport rows.
    share, _ = HC.match("GET", "/api/sessions/shares")
    assert share is not None and share["handler"] == "h_session_shares_list"


def test_turn_via_dispatch_persists_and_reloads(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _stub(monkeypatch, "the route answer")

    code, out = HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                            {"text": "hello route", "session": "route-chat"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["reply"] == "the route answer"
    assert out["receipt"]["model"] == "qubit-brain4"

    listed = HC.dispatch(cfg, "GET", "/api/qubit/chat/sessions", {}, {})
    assert listed[0] == 200 and listed[1]["ok"] is True
    ids = [s["id"] for s in listed[1]["sessions"]]
    assert "route-chat" in ids

    shown = HC.dispatch(cfg, "GET", "/api/qubit/chat/sessions/route-chat", {}, {})
    assert shown[0] == 200
    assert [m["role"] for m in shown[1]["messages"]] == ["user", "assistant"]

    msgs = HC.dispatch(cfg, "GET",
                       "/api/qubit/chat/sessions/route-chat/messages", {}, {})
    assert msgs[0] == 200
    assert msgs[1]["pagination"]["returned"] == 2
    assert msgs[1]["messages"][-1]["content"] == "the route answer"


def test_session_crud_via_dispatch(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _stub(monkeypatch)

    created = HC.dispatch(cfg, "POST", "/api/qubit/chat/sessions", {},
                          {"name": "Built"})
    assert created[0] == 200 and created[1]["ok"] is True
    sid = created[1]["session"]["id"]

    dup = HC.dispatch(cfg, "POST", "/api/qubit/chat/sessions", {}, {"id": sid})
    assert dup[0] == 409 and dup[1]["code"] == "SESSION_EXISTS"

    patched = HC.dispatch(cfg, "PATCH", f"/api/qubit/chat/sessions/{sid}", {},
                          {"name": "Renamed"})
    assert patched[0] == 200
    assert patched[1]["session"]["name"] == "Renamed"

    denied = HC.dispatch(cfg, "DELETE", f"/api/qubit/chat/sessions/{sid}", {}, {})
    assert denied[0] == 409 and denied[1]["code"] == "CONFIRM_REQUIRED"

    deleted = HC.dispatch(cfg, "DELETE", f"/api/qubit/chat/sessions/{sid}", {},
                          {"confirm": True})
    assert deleted[0] == 200 and deleted[1]["ok"] is True

    missing = HC.dispatch(cfg, "GET", f"/api/qubit/chat/sessions/{sid}", {}, {})
    assert missing[0] == 404 and missing[1]["code"] == "SESSION_NOT_FOUND"


def test_export_import_via_dispatch(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _stub(monkeypatch, "exportable")
    HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                {"text": "persist me", "session": "exp"})

    env_code, env = HC.dispatch(cfg, "GET",
                                "/api/qubit/chat/sessions/exp/export", {}, {})
    assert env_code == 200 and env["ok"] is True
    assert env["schema"] == "qubit-chat-session/1"
    assert len(env["messages"]) == 2

    imp_code, imp = HC.dispatch(cfg, "POST", "/api/qubit/chat/sessions/import",
                                {}, {"session": {"id": "copy", "name": "Copy"},
                                     "messages": env["messages"]})
    assert imp_code == 200 and imp["ok"] is True, (imp_code, imp)
    assert imp["imported"] == 2
    copy = HC.dispatch(cfg, "GET", "/api/qubit/chat/sessions/copy", {}, {})
    assert copy[0] == 200 and len(copy[1]["messages"]) == 2


def test_cancel_and_pty_via_dispatch(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _stub(monkeypatch, "pty reply")

    no_turn = HC.dispatch(cfg, "POST", "/api/qubit/chat/cancel", {},
                          {"session": "none"})
    assert no_turn[0] == 404 and no_turn[1]["code"] == "NO_ACTIVE_TURN"

    frame = HC.dispatch(cfg, "POST", "/api/qubit/chat/pty", {},
                        {"session": "pty-route",
                         "input": base64.b64encode(b"hi\r").decode()})
    assert frame[0] == 200 and frame[1]["ok"] is True
    assert b"pty reply" in base64.b64decode(frame[1]["output"])

    # reload proves the pty turn was persisted by the dispatcher
    shown = HC.dispatch(cfg, "GET", "/api/qubit/chat/sessions/pty-route", {}, {})
    assert shown[0] == 200
    assert shown[1]["messages"][-1]["content"] == "pty reply"


def test_retry_via_dispatch(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    import voicebridge.local_llm as _llm

    cfg = _cfg(tmp_path, monkeypatch)
    mode = {"fail": True}

    def _fake(*a, **k):
        if mode["fail"]:
            raise RuntimeError("temporary model outage")
        return "retry succeeded"

    monkeypatch.setattr(_llm, "chat_local", _fake)
    first = HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                        {"text": "try me", "session": "retry-route"})
    assert first[0] == 502 and first[1]["code"] == "MODEL_ERROR", first

    mode["fail"] = False
    retried = HC.dispatch(cfg, "POST", "/api/qubit/chat/retry", {},
                          {"session": "retry-route"})
    assert retried[0] == 200 and retried[1]["reply"] == "retry succeeded"

    again = HC.dispatch(cfg, "POST", "/api/qubit/chat/retry", {},
                        {"session": "retry-route"})
    assert again[0] == 409 and again[1]["code"] == "NOTHING_TO_RETRY"


# ---------------------------------------------------------------------------
# Live HTTP: the actual WebUI listener serving the chat transport routes.
# ---------------------------------------------------------------------------
_DIST = REPO_ROOT / "frontend" / "hermes-dist"
_needs_dist = pytest.mark.skipif(
    not (_DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")


def _start_webui(tmp_path):
    import time as _time
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self) -> None:
            self.cfg = cfg
            self._t0 = _time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None):
    import json as _json
    import urllib.error
    import urllib.request

    data = _json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    req.add_header("X-Hermes-Session-Token", "test-token")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, _json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, _json.loads(e.read() or b"{}")


@_needs_dist
def test_chat_transport_over_real_webui_http(tmp_path, monkeypatch):
    """The real listener's `/agent/api/qubit/chat*` path reaches the transport
    and persists the session (production caller, not an in-process shortcut)."""
    import base64

    _stub(monkeypatch, "live route reply")
    ui, port = _start_webui(tmp_path)
    try:
        status, out = _http(port, "POST", "/agent/api/qubit/chat",
                            {"text": "hello over http", "session": "http-chat"})
        assert status == 200 and out["ok"] is True, (status, out)
        assert out["reply"] == "live route reply"

        status, listing = _http(port, "GET", "/agent/api/qubit/chat/sessions")
        assert status == 200
        assert any(s["id"] == "http-chat" for s in listing["sessions"])

        status, shown = _http(
            port, "GET", "/agent/api/qubit/chat/sessions/http-chat")
        assert status == 200
        assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]

        status, frame = _http(
            port, "POST", "/agent/api/qubit/chat/pty",
            {"session": "http-pty",
             "input": base64.b64encode(b"ping\r").decode()})
        assert status == 200 and frame["ok"] is True
        assert b"live route reply" in base64.b64decode(frame["output"])
    finally:
        ui.stop()
