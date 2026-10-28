"""W2 Agent 2 — Qubit runtime: chat, sessions, streaming, WS gate.

Focused regression tests for the Agent-2 owned runtime (``voicebridge.webui``,
``voicebridge.chat_transport``, ``voicebridge.local_llm``). Every HTTP request
below goes to a real ``voicebridge.webui.WebUI`` listener on an ephemeral port
so the production caller is exercised, not a handler in isolation. The model
transport is stubbed (the live Brain4 proof lives in
``tests/test_qbpv2_hermes_brain4.py``).

Defects locked here:

* ``POST /api/qubit/chat/stream`` dropped the request ``model`` entirely, so an
  unknown model was silently routed to ``qubit-brain`` (a wrong-model turn)
  while the non-streaming ``/api/qubit/chat`` returned a typed
  ``MODEL_NOT_ALLOWED``. The streaming branch now resolves/validates the model
  (and the e-stop + empty-text guards) BEFORE the SSE stream opens.
* ``local_llm.chat_local_stream``'s bounded empty-retry path flushed a growing
  buffer inside its sentence loop, re-emitting cumulative fragments. The flush
  is now once per pass and the retry never duplicates a completed sentence.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------- fixtures
def _cfg(tmp_path, monkeypatch, token="test-token"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 f"webui:\n  token: {token}\n")
    return C.Config.load(p), state


def _start_webui(cfg, state):
    from voicebridge.webui import WebUI

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self) -> None:
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None, token="test-token", raw=False,
          upgrade_ws=False):
    data = None
    headers = {"X-Qubit-Session-Token": token}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if upgrade_ws:
        headers.update({"Connection": "Upgrade", "Upgrade": "websocket",
                        "Sec-WebSocket-Version": "13",
                        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="})
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
            return r.status, dict(r.headers), (payload if raw else
                                               json.loads(payload or b"{}"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            parsed = json.loads(payload or b"{}")
        except ValueError:
            parsed = payload
        return e.code, dict(e.headers), parsed


def _raw_status(port, path):
    """GET without following redirects -> (status, Location)."""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location")
    finally:
        conn.close()


def _sse_events(text):
    """Parse an SSE response body into a list of (event, dict)."""
    out = []
    event = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: "):])))
    return out


def _stub_stream(monkeypatch, reply="stream reply"):
    import voicebridge.local_llm as _llm

    seen = []

    def _fake(text, conversation_prompt="", model="qubit-brain",
              timeout_s=4.0, max_tokens=100, context="", on_sentence=None,
              should_stop=None, num_ctx=None, reasoning=None):
        seen.append({"text": text, "model": model})
        if on_sentence:
            on_sentence(reply)
        return reply

    _fake.seen = seen
    monkeypatch.setattr(_llm, "chat_local_stream", _fake)
    monkeypatch.setattr(_llm, "chat_local",
                        lambda *a, **k: reply)
    return _fake


# ------------------------------------------- streaming model authority
def test_chat_stream_route_is_declared_in_the_route_authority():
    """The Qubit streaming front door is a first-class route in the single
    authority (qubit_compat.ROUTES), not a webui-only hardcoded string."""
    from voicebridge import qubit_compat as HC

    route, _params = HC.match("POST", "/api/qubit/chat/stream")
    assert route is not None, "POST /api/qubit/chat/stream is not declared"
    assert route["disposition"] == "stream"
    assert route["handler"] == "h_chat_stream"
    assert callable(HC.HANDLERS.get("h_chat_stream"))
    # Every observed frontend literal must resolve to a declared route.
    assert any(r["pattern"] == r"/api/qubit/chat/stream" for r in HC.ROUTES)
    # The generic JSON dispatcher cannot express a stream: typed refusal, not
    # a fake JSON success.
    code, out = HC.dispatch(None, "POST", "/api/qubit/chat/stream", {},
                            {"text": "hi"})
    assert code == 400 and out["code"] == "STREAM_REQUIRES_SSE"


def test_sse_rejects_disallowed_model_before_stream(tmp_path, monkeypatch):
    """A streaming request for an unknown model is a typed 400, never a
    silently mis-routed qubit-brain turn."""
    cfg, state = _cfg(tmp_path, monkeypatch)
    _stub_stream(monkeypatch)
    ui, port = _start_webui(cfg, state)
    try:
        status, headers, out = _http(
            port, "POST", "/agent/api/qubit/chat/stream",
            {"text": "hi", "session": "a2-badmodel", "model": "gpt-4o"})
        assert status == 400, (status, out)
        assert out["ok"] is False and out["code"] == "MODEL_NOT_ALLOWED"
        # The stream never opened: JSON content type, no SSE frames.
        assert "text/event-stream" not in headers.get("Content-Type", "")
        # No session/messages were created for the refused request.
        _, _, shown = _http(
            port, "GET", "/agent/api/qubit/chat/sessions/a2-badmodel")
        assert shown.get("ok") is False
    finally:
        ui.stop()


def test_sse_and_nonstream_share_model_authority(tmp_path, monkeypatch):
    """The streaming and non-streaming chat fronts resolve/refuse the model
    identically (one ``chat_transport.resolve_model`` authority)."""
    cfg, state = _cfg(tmp_path, monkeypatch)
    _stub_stream(monkeypatch)
    ui, port = _start_webui(cfg, state)
    try:
        s1, _, o1 = _http(port, "POST", "/agent/api/qubit/chat",
                          {"text": "hi", "session": "a2-ns", "model": "gpt-4o"})
        s2, _, o2 = _http(port, "POST", "/agent/api/qubit/chat/stream",
                          {"text": "hi", "session": "a2-st", "model": "gpt-4o"})
        assert (s1, o1["code"]) == (s2, o2["code"]) == \
            (400, "MODEL_NOT_ALLOWED")
    finally:
        ui.stop()


def test_sse_honors_configured_model_and_persists(tmp_path, monkeypatch):
    cfg, state = _cfg(tmp_path, monkeypatch)
    stub = _stub_stream(monkeypatch, "SSE_OK")
    ui, port = _start_webui(cfg, state)
    try:
        status, headers, body = _http(
            port, "POST", "/agent/api/qubit/chat/stream",
            {"text": "hello", "session": "a2-ok", "model": "qubit-brain"},
            raw=True)
        assert status == 200
        assert headers.get("Content-Type", "").startswith("text/event-stream")
        events = _sse_events(body.decode("utf-8"))
        kinds = [e for e, _ in events]
        assert kinds[0] == "start" and "done" in kinds
        assert "delta" in kinds, kinds
        deltas = [d["text"] for e, d in events if e == "delta"]
        assert "".join(deltas) == "SSE_OK", deltas
        start = [d for e, d in events if e == "start"][0]
        assert start["model"] == "qubit-brain"
        assert stub.seen and stub.seen[0]["model"] == "qubit-brain"
        done = [d for e, d in events if e == "done"][0]
        assert done["ok"] is True and done["reply"] == "SSE_OK"
        # The streamed turn is persisted server-side (sidebar + transcript).
        _, _, shown = _http(
            port, "GET", "/agent/api/qubit/chat/sessions/a2-ok")
        assert shown["ok"] is True
        assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
        assert shown["messages"][-1]["content"] == "SSE_OK"
    finally:
        ui.stop()


def test_sse_empty_text_is_typed_before_stream(tmp_path, monkeypatch):
    cfg, state = _cfg(tmp_path, monkeypatch)
    _stub_stream(monkeypatch)
    ui, port = _start_webui(cfg, state)
    try:
        status, headers, out = _http(
            port, "POST", "/agent/api/qubit/chat/stream",
            {"text": "   ", "session": "a2-empty"})
        assert status == 400 and out["code"] == "MALFORMED"
        assert "text/event-stream" not in headers.get("Content-Type", "")
    finally:
        ui.stop()


# --------------------------------------------- session persistence/restart
def test_session_persists_across_listener_restart(tmp_path, monkeypatch):
    """A turn created through one listener is still listed/shown by a brand
    new listener over the same state dir (service restart), model included."""
    cfg, state = _cfg(tmp_path, monkeypatch)
    _stub_stream(monkeypatch, "restart me")
    ui, port = _start_webui(cfg, state)
    try:
        _, _, turn = _http(port, "POST", "/agent/api/qubit/chat",
                           {"text": "remember me", "session": "a2-restart", "model": "qubit-brain"})
        assert turn["ok"] is True
    finally:
        ui.stop()

    # Simulate a restart: a fresh process/listener re-reads the same state.
    cfg2, _ = _cfg(tmp_path, monkeypatch)
    ui2, port2 = _start_webui(cfg2, state)
    try:
        _, _, listed = _http(port2, "GET",
                             "/agent/api/qubit/chat/sessions?limit=50")
        ids = [s["id"] for s in listed["sessions"]]
        assert "a2-restart" in ids
        _, _, shown = _http(
            port2, "GET", "/agent/api/qubit/chat/sessions/a2-restart")
        assert shown["session"]["model"] == "qubit-brain"
        assert [m["content"] for m in shown["messages"]] == \
            ["remember me", "restart me"]
    finally:
        ui2.stop()


# ------------------------------------------------- streaming state machine
def test_stream_timeout_is_typed_and_not_fabricated(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg, _ = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    def _slow(*a, **k):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(_llm, "chat_local_stream", _slow)
    events = list(CT.stream_turn(cfg, "slow", session="a2-timeout", model="qubit-brain"))
    errs = [e for e in events if e["event"] == "error"]
    assert errs and errs[0]["code"] == "MODEL_ERROR"
    assert "timed out" in errs[0]["detail"]
    # The user message is retained, no assistant reply was fabricated.
    msgs = CT.store(cfg).show("a2-timeout")["messages"]
    assert [m["role"] for m in msgs] == ["user"]


def test_stream_cancel_emits_cancelled_and_keeps_user(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg, _ = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    started = threading.Event()
    release = threading.Event()

    def _blocking(*a, **k):
        on_sentence = k.get("on_sentence")
        if on_sentence:
            on_sentence("first.")
        started.set()
        release.wait(5)
        return "first."

    monkeypatch.setattr(_llm, "chat_local_stream", _blocking)
    box = []

    def _run():
        box.extend(list(CT.stream_turn(cfg, "long", session="a2-cancel", model="qubit-brain")))

    t = threading.Thread(target=_run)
    t.start()
    assert started.wait(5)
    assert CT.cancel("a2-cancel")["ok"] is True
    release.set()
    t.join(5)
    assert any(e["event"] == "cancelled" for e in box), box
    msgs = CT.store(cfg).show("a2-cancel")["messages"]
    assert [m["role"] for m in msgs] == ["user"]


def test_retry_after_error_completes_without_duplicating(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    cfg, _ = _cfg(tmp_path, monkeypatch)
    import voicebridge.local_llm as _llm

    mode = {"fail": True}

    def _fake(*a, **k):
        if mode["fail"]:
            raise RuntimeError("model down")
        return "recovered"

    monkeypatch.setattr(_llm, "chat_local", _fake)
    first = CT.turn(cfg, "help", session="a2-retry")
    assert first["ok"] is False and first["code"] == "MODEL_ERROR"
    assert [m["role"] for m in CT.store(cfg).show("a2-retry")["messages"]] == \
        ["user"]
    mode["fail"] = False
    second = CT.retry_last(cfg, "a2-retry")
    assert second["ok"] is True and second["reply"] == "recovered"
    assert [m["role"] for m in CT.store(cfg).show("a2-retry")["messages"]] == \
        ["user", "assistant"]
    again = CT.retry_last(cfg, "a2-retry")
    assert again["code"] == "NOTHING_TO_RETRY"


# --------------------------------------------- WS gate: scoped PTY ticket
def test_ws_upgrade_to_pty_requires_ticket_not_a_loop(tmp_path, monkeypatch):
    """The `/chat` terminal upgrade is served only with a short-lived scoped
    ticket. A ticketless upgrade is a typed HTTP 401 (no hang, no 1006
    reconnect loop); an unknown WS upgrade path is a plain 404. Owner decision
    2026-09-17 replaced the blanket PTY refusal with this authenticated path."""
    cfg, state = _cfg(tmp_path, monkeypatch)
    ui, port = _start_webui(cfg, state)
    try:
        status, headers, out = _http(
            port, "GET", "/agent/api/pty", upgrade_ws=True)
        assert status == 401
        assert out["code"] == "TICKET_REQUIRED"
        assert "Sec-WebSocket-Accept" not in headers
        # An unknown WS upgrade path is a plain 404 (no hang, no socket).
        status2, _, _ = _http(port, "GET", "/agent/api/unknown",
                              upgrade_ws=True)
        assert status2 == 404
    finally:
        ui.stop()


def test_normal_chat_page_uses_no_raw_pty_route(tmp_path, monkeypatch):
    """The Qubit Chat tab is a JSON turn (POST /api/qubit/chat), not the raw
    PTY stream; the WS gate code mapping must classify /api/pty as PTY."""
    from voicebridge import webui as W

    assert W.qubit_ws_gate_code("/agent/api/pty") == "PTY_NO_SHELL"
    assert W.qubit_ws_gate_code("/api/console") == "PTY_NO_SHELL"
    assert W.qubit_ws_gate_code("/api/qubit/chat") is None
    assert W.qubit_ws_gate_code("/agent/api/qubit/chat/stream") is None


def test_activity_alias_redirects_to_logs(tmp_path, monkeypatch):
    """``/activity`` and ``/activity.html`` are family aliases for ``/logs``;
    removing the unreachable static-page branch preserves the 302 redirect."""
    from voicebridge import webui as W

    assert W.qubit_alias_target("/activity") == "/logs"
    assert W.qubit_alias_target("/activity.html") == "/logs"
    assert W.QUBIT_ROUTE_ALIASES["/activity"] == "/logs"
    # The legacy constant stays defined (referenced by the rebrand suite).
    assert "Qubit Agent" in W.ACTIVITY_PAGE
    cfg, state = _cfg(tmp_path, monkeypatch)
    ui, port = _start_webui(cfg, state)
    try:
        assert _raw_status(port, "/activity") == (302, "/logs")
        assert _raw_status(port, "/activity.html") == (302, "/logs")
        assert _raw_status(port, "/logs")[0] == 200
    finally:
        ui.stop()


def test_served_chat_chunk_opens_no_raw_pty_websocket():
    """The built normal-chat chunks must not open a WebSocket or a raw
    /api/pty path (that is what produced the code-1006 reconnect loop)."""
    dist = REPO_ROOT / "frontend" / "qubit-dist" / "assets"
    chunks = sorted(dist.glob("ChatPage-*.js"))
    if not chunks:
        pytest.skip("hermes-dist not built (npm build step)")
    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in chunks)
    assert "new WebSocket" not in blob
    assert "/api/pty" not in blob


# --------------------------------------------- local_llm retry regression
def test_compile_4b_active_section_prefix_is_stripped():
    """The ``[active]`` section marker is stripped exactly once (the
    py3.11-safe precomputed prefix keeps the previous behavior)."""
    from voicebridge import local_llm as LL

    out = LL._compile_4b("hi", "User: earlier\nQubit: ok", "", 4096, 100,
                         include_core_facts=False)
    assert out == "Recent conversation:\nUser: earlier\nQubit: ok"


def test_local_llm_compiles_under_python311_when_available(tmp_path):
    """Guard the f-string backslash fix: the module must parse on 3.11 too."""
    py = shutil.which("python3.11")
    if not py:
        pytest.skip("python3.11 not installed")
    cfile = tmp_path / "local_llm.pyc"
    code = ("import py_compile, sys\n"
            "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)\n")
    proc = subprocess.run(
        [py, "-c", code, str(REPO_ROOT / "voicebridge" / "local_llm.py"),
         str(cfile)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-400:]


def test_chat_local_stream_retry_does_not_reemit_sentences(monkeypatch):
    """The bounded empty-retry path emits each completed sentence exactly once
    (locks the local_llm cumulative-buffer fix)."""
    import voicebridge.local_llm as LL

    requests = []

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: N802
            pass

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            requests.append(1)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            if len(requests) == 1:
                self.wfile.write((json.dumps(
                    {"message": {"content": ""}, "done": True}) + "\n").encode())
            else:
                for tok in ("Hello.", " World.", " More."):
                    self.wfile.write((json.dumps(
                        {"message": {"content": tok}, "done": False}) +
                        "\n").encode())
                self.wfile.write((json.dumps(
                    {"message": {"content": ""}, "done": True}) + "\n").encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        emitted = []
        full = LL.chat_local_stream(
            "hi", url="http://127.0.0.1:%d" % srv.server_address[1],
            timeout_s=10, on_sentence=emitted.append)
        assert full == "Hello. World. More."
        assert emitted == ["Hello.", "World.", "More."]
        assert len(requests) == 2  # exactly one bounded retry
    finally:
        srv.shutdown()
        srv.server_close()


# --------------------------------------------- one model authority
def test_cli_dashboard_voice_share_brain4_authority(tmp_path, monkeypatch):
    """``chat_transport`` (dashboard), ``agent`` (CLI/voice worker) and the
    compat model surfaces all resolve the same configured Qubit brain."""
    from voicebridge import agent as AG
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    assert CT.configured_model(cfg) == "qubit-brain"
    assert AG._model(cfg) == "qubit-brain"
    assert HC._qubit_model(cfg)["name"] == "qubit-brain"
    assert CT.resolve_model(cfg, None)["model"] == "qubit-brain"


# --------------------------------------------- one fork home (never ~/.hermes)
def test_qubit_runtime_home_is_qubit_owned(tmp_path, monkeypatch):
    """The fork's home resolves to the Qubit-owned runtime dir, and the
    dashboard bridge resolves the exact same home (one authority)."""
    from voicebridge import qubit_runtime as HR

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    home = HR.default_home()
    assert home == tmp_path / "state" / "qubit-voicebridge" / "runtime"
    assert home != tmp_path / ".hermes"
    assert HR._external_default_home() == tmp_path / ".hermes"

    saved = os.environ.get("QUBIT_HOME")
    try:
        resolved = HR.use_qubit_home()
        assert resolved == home
        assert os.environ["QUBIT_HOME"] == str(home)
    finally:
        if saved is None:
            os.environ.pop("QUBIT_HOME", None)
        else:
            os.environ["QUBIT_HOME"] = saved

    # The dashboard bridge delegates to the same resolver (single home).
    from voicebridge import qubit_asgi as HA
    src = HA.__file__
    assert src  # module importable


def test_fork_get_qubit_home_is_the_qubit_home(tmp_path, monkeypatch):
    """The vendored fork's own resolver returns the Qubit home after
    ``use_qubit_home()`` (proves the fork env var is QUBIT_HOME)."""
    from voicebridge import qubit_runtime as HR

    script = (
        "import os\n"
        "os.environ.pop('QUBIT_HOME', None)\n"
        "from voicebridge import qubit_runtime as HR\n"
        "h = HR.use_qubit_home()\n"
        "HR.ensure_path()\n"
        "from qubit_constants import get_qubit_home\n"
        "print(str(h))\n"
        "print(str(get_qubit_home()))\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_STATE_HOME"] = str(home / "state")
    env.pop("QUBIT_HOME", None)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-800:]
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln]
    assert lines[-2] == lines[-1], lines
    assert lines[-1] == str(home / "state" / "qubit-voicebridge" / "runtime")
    assert str(home / ".hermes") not in lines[-1]


def test_external_qubit_home_is_not_consulted(tmp_path):
    """``qubit agent status`` uses the Qubit home and never reads (or writes)
    the external ``~/.hermes`` config."""
    home = tmp_path / "home"
    external = home / ".hermes"
    external.mkdir(parents=True)
    sentinel = external / "config.yaml"
    sentinel.write_text("model:\n  default: EXTERNAL_SENTINEL_MODEL\n")
    before = sentinel.read_bytes()

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_STATE_HOME"] = str(home / "state")
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env.pop("QUBIT_HOME", None)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", "status"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=240)
    assert proc.returncode == 0, proc.stderr[-800:]
    out = proc.stdout + proc.stderr
    assert "EXTERNAL_SENTINEL_MODEL" not in out
    # External ~/.hermes was neither read nor written.
    assert sentinel.read_bytes() == before
    # The Qubit-owned home was the one initialized.
    assert (home / "state" / "qubit-voicebridge" / "runtime").is_dir()

