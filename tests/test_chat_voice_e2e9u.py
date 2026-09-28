"""Focused hermetic E2E wiring for the two chat surfaces + voice input.

Covers, without a live provider, network, or microphone:

1. ``POST /api/qubit/chat`` (non-streaming) -> ``h_chat_turn`` ->
   ``chat_transport.turn`` persists a user+assistant turn in the canonical
   ``QubitSessionStore``; the session id is continuous across two turns.
2. ``POST /api/qubit/chat/stream`` -> ``h_chat_stream`` ->
   ``chat_transport.stream_sse`` yields typed ``start``/``delta``/``done``
   events; the ``done`` receipt carries the model and the dynamic-context
   routing fields; a model that returns nothing produces a typed ``error``
   event and no fabricated assistant turn.
3. ``/chat`` terminal over the one ``/agent/api/pty`` socket: the ws-ticket
   route mints a scoped ticket, the PTY upgrade is origin/auth gated, and the
   upstream raw WS dialects stay typed-refused (426).
4. Right-Ctrl: the browser control has no shadow backend -- the backend the
   browser voice path targets is the same canonical ``chat_transport`` route
   table as the typed path. The actual DOM key event is not hermetically
   reachable (browser + built dist); that link is an explicit xfail here and a
   live Playwright check in ``tests/test_voice_e2e_phase8.py``.
5. ``voice.input_engine`` normalization: ``browser``/``local``/``auto`` map to
   themselves and an unknown/missing value fails closed to ``auto``.

Everything is additive; no product code is modified. When a real provider or
the ``>=65,536-token`` ``/chat`` credential would be required, the test says so
instead of asserting a fabricated success.
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# shared fixtures / helpers
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _stub_local(monkeypatch, reply="stub chat reply"):
    """Stub the local Brain4 non-streaming call (no Ollama, no network)."""
    import voicebridge.local_llm as _llm

    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain4",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None):
        calls.append({"text": text, "model": model,
                      "conversation_prompt": conversation_prompt})
        return reply

    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local", _fake)
    return _fake


def _stub_local_stream(monkeypatch, reply="stub stream reply"):
    """Stub the local Brain4 streaming call (no Ollama, no network)."""
    import voicebridge.local_llm as _llm

    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain4",
              timeout_s=4.0, max_tokens=100, context="", on_sentence=None,
              should_stop=None, num_ctx=None, reasoning=None):
        calls.append({"text": text, "model": model, "context": context})
        if reply and on_sentence:
            on_sentence(reply)
        return reply

    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local_stream", _fake)
    return _fake


def _parse_sse(frames):
    """Parse ``h_chat_stream``/``stream_sse`` frames into event dicts."""
    events = []
    for frame in frames:
        name = None
        data = None
        for line in str(frame).splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        payload = json.loads(data) if data else {}
        payload.setdefault("event", name)
        events.append(payload)
    return events


# ===========================================================================
# 1. POST /api/qubit/chat (non-streaming) — persisted turn + session identity
# ===========================================================================
def test_non_streaming_route_persists_turn_and_continuous_session(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    stub = _stub_local(monkeypatch, "four")

    code1, out1 = HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                              {"text": "what is 2+2", "session": "e2e9u"})
    code2, out2 = HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                              {"text": "and 3+3", "session": "e2e9u"})
    assert code1 == 200 and out1["ok"] is True, (code1, out1)
    assert code2 == 200 and out2["ok"] is True, (code2, out2)

    # One continuous session id across both turns (no fork, no new id).
    assert out1["session_id"] == out2["session_id"] == "e2e9u"
    assert out1["receipt"]["model"] == "qubit-brain4"

    # Both turns land in the canonical store: user then assistant, twice.
    shown = CT.store(cfg).show("e2e9u")
    assert shown["state"] == "OK", shown
    roles = [m["role"] for m in shown["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"], roles
    assert [m["content"] for m in shown["messages"]
            if m["role"] == "assistant"] == ["four", "four"]
    assert shown["session"]["message_count"] == 4

    # The real model backend was invoked with the typed text (not a mock result).
    assert stub.calls and "what is 2+2" in stub.calls[0]["text"]


# ===========================================================================
# 2. POST /api/qubit/chat/stream — typed events + done receipt + no fabrication
# ===========================================================================
def test_stream_route_typed_events_and_done_receipt(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _stub_local_stream(monkeypatch, "Hello. World.")

    # A plain JSON dispatch of the stream route is a typed refusal, never a
    # fabricated envelope -- the SSE front door owns the socket.
    code, refused = HC.dispatch(cfg, "POST", "/api/qubit/chat/stream", {},
                                {"text": "hi", "session": "s9u"})
    assert code == 400 and refused["code"] == "STREAM_REQUIRES_SSE", refused

    frames = list(HC.h_chat_stream(cfg, {}, {"text": "greet me",
                                             "session": "s9u"}, {}))
    events = _parse_sse(frames)
    kinds = [e["event"] for e in events]

    assert kinds[0] == "start", kinds
    assert "delta" in kinds and "done" in kinds, kinds
    # Every delta is a real typed delta with text (nothing synthesized).
    assert all(isinstance(e.get("text"), str) and e["text"]
               for e in events if e["event"] == "delta")

    done = [e for e in events if e["event"] == "done"][0]
    assert done["ok"] is True and done["reply"] == "Hello. World."
    assert done["receipt"]["model"] == "qubit-brain4"
    assert done["receipt"]["path"] == "voicebridge.chat_transport.stream_turn"
    assert done["receipt"]["streaming"] is True

    # Dynamic-context routing fields on the receipt.
    ctx = done["receipt"]["context"]
    assert ctx["tier"] in ("fast", "expanded", "escalated"), ctx
    assert isinstance(ctx["window_tokens"], int) and ctx["window_tokens"] > 0
    assert isinstance(ctx["message_limit"], int) and ctx["message_limit"] >= 1
    routing = ctx["routing"]
    assert routing["tier"] == ctx["tier"]
    assert isinstance(routing["window_tokens"], int)
    assert "requires_escalation" in routing and "reasons" in routing

    # The streamed reply is the one persisted to the canonical store.
    shown = CT.store(cfg).show("s9u")
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
    assert shown["messages"][-1]["content"] == "Hello. World."


def test_stream_route_empty_model_is_typed_error_not_fabricated(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _stub_local_stream(monkeypatch, "")

    events = _parse_sse(list(HC.h_chat_stream(
        cfg, {}, {"text": "hi", "session": "s9u-empty"}, {})))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start", kinds
    assert "done" not in kinds, kinds
    errors = [e for e in events if e["event"] == "error"]
    assert errors and errors[0]["code"] == "MODEL_UNAVAILABLE", events

    # No assistant turn was fabricated.
    shown = CT.store(cfg).show("s9u-empty")
    assert [m["role"] for m in shown["messages"]] == ["user"]


# ===========================================================================
# 3. /chat terminal — ticket mint, origin/auth gate, refused raw WS dialects
# ===========================================================================
_FRONTIER = ("intent:\n  frontier:\n    enabled: true\n"
             "    allowlist: ['opencode/big-model']\n")


def _prod_cfg(tmp_path, monkeypatch):
    """Config with a >=65,536-context API profile so the /chat guard passes.

    A live *credential* is never needed: the window is stubbed below.
    """
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               _FRONTIER + "webui:\n  token: test-token\n")
    R.upsert_model_profile(cfg, R.ModelProfile(
        id="big-api", kind=R.API, provider="opencode", model="big-model",
        base_url="https://api.example/v1", key_ref="env:OPENCODE_API_KEY",
        locality="external", egress_consent="allow", roles=["chat"],
        context_tokens=131072), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    monkeypatch.setattr(
        R, "_real_context_window",
        lambda eff, live=False: (131072, "cache"), raising=True)
    return cfg


def _server(cfg, state):
    from voicebridge.webui import WebUI

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = 0.0

    return WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)


def _mint(port, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/agent/api/auth/ws-ticket",
        data=json.dumps(body or {}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Hermes-Session-Token", "test-token")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _raw_upgrade(port, path, *, origin=None):
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    headers = [
        f"GET {path} HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        "X-Hermes-Session-Token: test-token",
    ]
    if origin is not None:
        headers.append(f"Origin: {origin}")
    req = ("\r\n".join(headers) + "\r\n\r\n").encode()
    with socket.create_connection(("127.0.0.1", port), timeout=8) as s:
        s.sendall(req)
        s.settimeout(8)
        chunks = []
        while True:
            try:
                part = s.recv(65536)
            except socket.timeout:
                break
            if not part:
                break
            chunks.append(part)
    data = b"".join(chunks)
    status = int(data.split(b" ", 2)[1])
    body = data.split(b"\r\n\r\n", 1)
    payload = json.loads(body[1]) if len(body) == 2 and body[1] else {}
    return status, payload


def test_ws_ticket_mints_and_pty_upgrade_is_gated(tmp_path, monkeypatch):
    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]

        # The authenticated HTTP route mints a short-lived scoped ticket.
        status, out = _mint(port, {})
        assert status == 200 and out["ok"] is True, out
        assert out["ticket"] and out["scope"] == "pty"
        assert int(out["expires_in"]) > 0
        # A ticket is a reference, never a credential leak.
        assert "key_ref" not in out and "OPENCODE_API_KEY" not in json.dumps(out)

        # The PTY socket requires that ticket: a bare upgrade is a typed 401.
        status, payload = _raw_upgrade(port, "/agent/api/pty")
        assert status == 401, (status, payload)
        assert payload["ok"] is False and payload["code"] == "TICKET_REQUIRED"
        assert payload["owner"] == "qubit-policy"

        # Origin is gated too: a foreign Origin is refused before the handshake.
        ticket = _mint(port, {})[1]["ticket"]
        status, payload = _raw_upgrade(
            port, f"/agent/api/pty?ticket={ticket}",
            origin="http://evil.example")
        assert status == 403, (status, payload)
        assert payload["code"] == "ORIGIN_REFUSED"
    finally:
        ui.stop()


@pytest.mark.parametrize("path", [
    "/api/console", "/api/ws", "/api/events", "/api/pub",
    "/api/audio/speak-stream", "/api/plugins/kanban/events",
    "/agent/api/console", "/agent/api/ws",
])
def test_upstream_raw_ws_dialects_stay_typed_refused(tmp_path, monkeypatch,
                                                     path):
    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, payload = _raw_upgrade(port, path)
        assert status == 426, (path, status, payload)
        assert payload["ok"] is False and payload["code"]
        assert payload["owner"] == "qubit-policy"
    finally:
        ui.stop()


def test_owned_pty_alias_requires_a_ticket_not_a_426(tmp_path, monkeypatch):
    """`/api/pty` is the one supported terminal socket alias (with `/agent`):
    it is typed 401 TICKET_REQUIRED, never a 426 refusal or a bare 404."""
    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, payload = _raw_upgrade(port, "/api/pty")
        assert status == 401, (status, payload)
        assert payload["code"] == "TICKET_REQUIRED"
        assert payload["owner"] == "qubit-policy"
    finally:
        ui.stop()


# ===========================================================================
# 4. Right-Ctrl — shared canonical backend, no shadow path
# ===========================================================================
def test_right_ctrl_targets_the_shared_chat_transport_backend(
        tmp_path, monkeypatch):
    """The browser Right-Ctrl/voice path and typed input share one backend.

    The browser control is client-side (keydown 'ControlRight' -> speech input
    -> the same ``send()``); it must resolve to the same canonical
    ``chat_transport`` route table as typed ``/qubit-chat`` turns, with no
    right-ctrl-specific server route that could become a shadow backend.
    """
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    # Route identity: typed and streamed turns are siblings in one authority.
    typed_route, _ = HC.match("POST", "/api/qubit/chat")
    stream_route, _ = HC.match("POST", "/api/qubit/chat/stream")
    assert typed_route["handler"] == "h_chat_turn"
    assert stream_route["handler"] == "h_chat_stream"
    assert stream_route["disposition"] == "stream"

    # The browser voice path's handler delegates to the canonical transport.
    # Scope the spy to this block so the test's env isolation is untouched.
    seen = {}

    def _spy(cfg_, text, **kw):
        seen["text"] = text
        seen["session"] = kw.get("session")
        return iter(())

    with monkeypatch.context() as m:
        m.setattr(CT, "stream_sse", _spy)
        list(HC.h_chat_stream(cfg, {}, {"text": "voice turn",
                                        "session": "rc"}, {}))
    assert seen == {"text": "voice turn", "session": "rc"}, seen

    # No separate right-ctrl/voice server route exists to diverge from.
    patterns = [r["pattern"] for r in HC.ROUTES]
    assert not any("right" in p.lower() for p in patterns), patterns
    assert not any("voice/control" in p.lower() for p in patterns), patterns

    # Both turns persist into the one canonical store.
    _stub_local_stream(monkeypatch, "voice reply")
    list(HC.h_chat_stream(cfg, {}, {"text": "voice turn", "session": "rc"},
                          {}))
    _stub_local(monkeypatch, "typed reply")
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                            {"text": "typed turn", "session": "typed"})
    assert code == 200 and out["ok"] is True, (code, out)

    st = CT.store(cfg)
    assert st.show("rc")["messages"][-1]["content"] == "voice reply"
    assert st.show("typed")["messages"][-1]["content"] == "typed reply"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Right-Ctrl has no server-side entry point to invoke hermetically: it "
        "is a browser trigger (frontend/qubit-web/src/pages/ChatPage.tsx:998-"
        "1018, a window keydown/keyup on code 'ControlRight' that starts/stops "
        "the selected speech-input engine; the final transcript then enters "
        "the same send() -> POST /api/qubit/chat/stream path as typed input). "
        "Proving the DOM key event reaches the canonical turn requires "
        "Playwright plus the built frontend/qubit-dist bundle and a browser "
        "SpeechRecognition stub. That link is covered non-hermetically by "
        "tests/test_voice_e2e_phase8.py::"
        "test_right_ctrl_drives_canonical_chat_stream under `-m 'ui or live'`. "
        "No operator credential is required; the missing link is browser "
        "automation, not a provider."
    ),
)
def test_right_ctrl_has_a_hermetic_server_side_entry_point():
    from voicebridge import qubit_compat as HC

    # A hermetic, backend-only right-ctrl proof would need a server route that
    # stands in for the browser key event; none exists (by design, so the
    # control cannot become a shadow backend). This assertion is expected to
    # fail until/unless such a route is deliberately added.
    route, _ = HC.match("POST", "/api/qubit/right-ctrl")
    assert route is not None


# ===========================================================================
# 5. voice.input_engine normalization
# ===========================================================================
def test_voice_input_engine_normalization():
    from voicebridge.config import VOICE_INPUT_ENGINES, normalize_input_engine

    assert VOICE_INPUT_ENGINES == ("browser", "local", "auto")
    for value in VOICE_INPUT_ENGINES:
        assert normalize_input_engine(value) == value
    # Unknown/missing/wrong-case values fail closed to the auto engine.
    for bad in (None, "", "BROWSER", "Local", "remote", "speech", 3, [], {}):
        assert normalize_input_engine(bad) == "auto", bad


def test_voice_input_engine_default_is_auto(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert cfg.get("voice", "input_engine") == "auto"
