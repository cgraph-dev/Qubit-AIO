"""`/chat` terminal parity — context-window verification surfacing (9U).

These focused tests pin the fixes made on 2026-09-21 for the `/chat` long-context
path:

* The guard cross-checks a profile's *declared* ``context_tokens`` against the
  vendored resolver's real window (``_real_context_window``). A window that
  cannot be resolved in ``when_available`` mode is accepted but flagged
  ``context_verified=False``; in ``required`` mode it fails closed
  (``TERMINAL_CONTEXT_UNVERIFIED``). The flag must be *visible* — surfaced in the
  ws-ticket 200 body, the typed refusal body, and the ``session_open`` audit —
  not silently dropped.
* The ws-ticket 409/503 gate and the pre-spawn ``PtyWsServer.serve`` gate agree
  on the status mapping (unverified -> 503, otherwise 409).

No full matrix / Playwright sweep is run here (implementation-first cadence).
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _api_profile(pid="big-api", model="big-model", **over):
    from voicebridge.providers import resolver as R

    kwargs = dict(
        id=pid, kind=R.API, provider="opencode", model=model,
        base_url="https://api.example/v1", key_ref="env:OPENCODE_API_KEY",
        locality="external", egress_consent="allow", roles=["chat"],
        context_tokens=131072)
    kwargs.update(over)
    return R.ModelProfile(**kwargs)


_FRONTIER = ("intent:\n  frontier:\n    enabled: true\n"
             "    allowlist: ['opencode/big-model']\n")


def _stub_window(monkeypatch, value, source):
    from voicebridge.providers import resolver as R

    monkeypatch.setattr(
        R, "_real_context_window",
        lambda eff, live=False: (value, source), raising=True)


def _prod_cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               _FRONTIER + "webui:\n  token: test-token\n" + extra)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
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


# ---------------------------------------------------------------------------
# ws-ticket 200: the unverified flag is surfaced (not silently dropped)
# ---------------------------------------------------------------------------
def test_ws_ticket_surfaces_unverified_window(tmp_path, monkeypatch):
    _stub_window(monkeypatch, None, "not-cached")
    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {})
        assert status == 200, out
        assert out["ok"] is True
        assert out["context_verified"] is False
        assert out["context_verification"] == "when_available"
        assert out["context_window_source"] == "not-cached"
        assert out["context_tokens"] == 131072
        # References only: never a credential value/ref or endpoint.
        assert "key_ref" not in out
        assert "base_url" not in out
        assert "OPENCODE_API_KEY" not in json.dumps(out)
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# required mode fails closed; the ws-ticket gate mirrors the serve status (503)
# ---------------------------------------------------------------------------
def test_ws_ticket_required_unverified_is_503(tmp_path, monkeypatch):
    _stub_window(monkeypatch, None, "not-cached")
    cfg = _prod_cfg(
        tmp_path, monkeypatch,
        "terminal:\n  context_window_verification: required\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {})
        assert status == 503, out
        assert out["ok"] is False
        assert out["code"] == "TERMINAL_CONTEXT_UNVERIFIED"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# A declared window larger than the real window is a typed refusal whose body
# carries both the declared and real values
# ---------------------------------------------------------------------------
def test_ws_ticket_declared_overstatement_reports_real_window(tmp_path,
                                                             monkeypatch):
    _stub_window(monkeypatch, 16384, "cache")
    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {})
        assert status == 409, out
        assert out["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
        assert out["real_context_tokens"] == 16384
        assert out["declared_context_tokens"] == 131072
        assert out["context_verified"] is True
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# The refusal is journaled with the verification provenance (non-secret)
# ---------------------------------------------------------------------------
def test_unverified_refusal_journal_carries_provenance(tmp_path, monkeypatch):
    from voicebridge import pty_ws as P

    _stub_window(monkeypatch, None, "not-cached")
    cfg = _prod_cfg(
        tmp_path, monkeypatch,
        "terminal:\n  context_window_verification: required\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    events: list = []
    monkeypatch.setattr(P, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {})
        assert status == 503, out
        refusals = [f for ev, f in events if ev == "webui.pty.context_refused"]
        assert refusals, events
        rec = refusals[-1]
        assert rec["code"] == "TERMINAL_CONTEXT_UNVERIFIED"
        assert rec["context_verification"] == "required"
        assert rec["context_window_source"] == "not-cached"
        assert rec["context_verified"] is False
        assert "key_ref" not in rec
        assert "base_url" not in rec
        assert "OPENCODE_API_KEY" not in json.dumps(rec)
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# The vendored resolver's generic 256K fallback is NOT a real window: a live
# probe that only hits it must be treated as unverified, or `required` mode's
# strict fail-closed check is defeated by an unknown model.
# ---------------------------------------------------------------------------
def test_real_context_window_rejects_vendored_generic_fallback(monkeypatch):
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    qubit_runtime.ensure_path()
    from agent import model_metadata as mm

    class _Eff:
        model = "unknown-model-abc"
        base_url = ""
        provider = "unknown-provider"

    monkeypatch.setattr(mm, "get_cached_context_length",
                        lambda *a, **k: None, raising=True)
    monkeypatch.setattr(mm, "get_model_context_length",
                        lambda *a, **k: mm.DEFAULT_FALLBACK_CONTEXT,
                        raising=True)
    assert R._real_context_window(_Eff(), live=True) == (None, "fallback")


def test_unknown_model_live_probe_is_not_trusted():
    # Integration contract against the real vendored resolver: an unknown model
    # with no endpoint resolves to the generic fallback, which must be reported
    # as unverified rather than as a verified window.
    from voicebridge import qubit_runtime
    from voicebridge.providers import resolver as R

    qubit_runtime.ensure_path()

    class _Eff:
        model = "totally-unknown-model-xyz"
        base_url = ""
        provider = "unknown-provider-xyz"

    real, source = R._real_context_window(_Eff(), live=True)
    assert real is None
    assert source == "fallback"


def test_required_mode_refuses_generic_fallback(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    _stub_window(monkeypatch, None, "fallback")
    cfg = _prod_cfg(
        tmp_path, monkeypatch,
        "terminal:\n  context_window_verification: required\n")
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is False
    assert out["code"] == "TERMINAL_CONTEXT_UNVERIFIED"
    assert out["context_verified"] is False
    assert out["context_window_source"] == "fallback"


# ---------------------------------------------------------------------------
# The successful session_open audit records the verification state
# ---------------------------------------------------------------------------
class _FakeBridge:
    def __init__(self) -> None:
        self.pid = 4321
        self.closed = False
        self._rx = bytearray()
        self._lock = threading.Lock()

    def read(self, timeout: float = 0.2):
        if self.closed:
            return None
        with self._lock:
            if self._rx:
                out = bytes(self._rx)
                self._rx.clear()
                return out
        time.sleep(0.01)
        return b""

    def write(self, data: bytes) -> bool:
        if self.closed:
            return False
        with self._lock:
            self._rx += b"E:" + bytes(data)
        return True

    def resize(self, *, cols: int, rows: int) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return not self.closed


def test_session_open_audit_records_verified_window(tmp_path, monkeypatch):
    from voicebridge import pty_ws as P

    pytest.importorskip("websockets.sync.client")
    from websockets.sync.client import connect as ws_connect

    _stub_window(monkeypatch, 131072, "cache")
    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    events: list = []
    monkeypatch.setattr(P, "log_event",
                        lambda ev, **f: events.append((ev, f)))

    def fake_spawn(cols, rows, resume, profile, *, env_overrides=None,
                   home_config=None, cfg=None):
        return _FakeBridge(), {"cwd": "/tmp/qubit"}

    monkeypatch.setattr(P, "_default_spawn", fake_spawn)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {})
        assert status == 200, out
        qs = urlencode({"ticket": out["ticket"], "cols": "80", "rows": "24"})
        with ws_connect(f"ws://127.0.0.1:{port}/agent/api/pty?{qs}",
                        open_timeout=8) as ws:
            ws.send(b"hi")
            ws.recv(timeout=8)
        opens = [f for ev, f in events if ev == "webui.pty.session_open"]
        assert opens, events
        rec = opens[-1]
        assert rec["profile_id"] == "big-api"
        assert rec["context_verified"] is True
        assert rec["context_verification"] == "when_available"
        assert rec["context_window_source"] == "cache"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Task 2 inventory: upstream raw WS dialects stay typed-refused (426)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/console", "/api/ws", "/api/events", "/api/pub",
    "/api/audio/speak-stream", "/api/plugins/kanban/events",
    "/agent/api/console", "/agent/api/ws", "/agent/api/events",
])
def test_upstream_raw_ws_paths_are_typed_refused(tmp_path, monkeypatch, path):
    _assert_ws_upgrade(tmp_path, monkeypatch, path, want=426)


@pytest.mark.parametrize("path", ["/api/pty", "/agent/api/pty"])
def test_pty_socket_requires_a_ticket(tmp_path, monkeypatch, path):
    # `/api/pty` (and its `/agent` alias) is the one supported terminal socket:
    # an upgrade without a scoped ticket is a typed 401 TICKET_REQUIRED, never a
    # 426 refusal and never a bare connection error.
    _assert_ws_upgrade(tmp_path, monkeypatch, path, want=401)


def _assert_ws_upgrade(tmp_path, monkeypatch, path, *, want):
    import socket

    cfg = _cfg(tmp_path, monkeypatch, "webui:\n  token: test-token\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "X-Hermes-Session-Token: test-token\r\n"
            "\r\n"
        ).encode()
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
        assert status == want, (path, data[:200])
        body = data.split(b"\r\n\r\n", 1)
        assert len(body) == 2
        payload = json.loads(body[1])
        assert payload["ok"] is False
        assert payload["code"]
        assert payload["owner"] == "qubit-policy"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Task 2 inventory: ticket single-use, e-stop, resize/input/output/close,
# origin + loopback boundary. These exercise the real transport with an
# injected PTY bridge (no 64k provider needed).
# ---------------------------------------------------------------------------
class _RecBridge(_FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.resizes: list = []

    def resize(self, *, cols: int, rows: int) -> None:
        self.resizes.append((int(cols), int(rows)))


def _raw_ws_upgrade(port: int, path: str) -> bytes:
    import socket

    key = "dGhlIHNhbXBsZSBub25jZQ=="
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "X-Hermes-Session-Token: test-token\r\n"
        "\r\n"
    ).encode()
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
    return b"".join(chunks)


def test_ticket_is_single_use():
    from voicebridge.pty_ws import SCOPE_PTY, TicketStore

    store = TicketStore()
    token, _ttl = store.mint(scope=SCOPE_PTY, session="s", owner="o")
    assert store.consume(token, scope=SCOPE_PTY) is not None
    assert store.consume(token, scope=SCOPE_PTY) is None


def test_origin_and_loopback_boundary():
    from voicebridge.pty_ws import origin_allowed, peer_allowed

    class _H:
        def __init__(self, origin="", host="127.0.0.1:8890", peer="127.0.0.1"):
            self.headers = {"Origin": origin, "Host": host}
            self.client_address = (peer, 4321)

    assert origin_allowed(_H()) is True  # non-browser (no Origin)
    assert origin_allowed(_H(origin="http://127.0.0.1:8890")) is True
    assert origin_allowed(_H(origin="http://evil.example")) is False
    assert peer_allowed(_H()) is True
    assert peer_allowed(_H(peer="10.0.0.5")) is False


def _inject_bridge(ui, bridge, estop=None):
    ui.pty_ws._spawn = lambda *a, **k: (bridge, {"cwd": "/tmp/qubit"})
    if estop is not None:
        ui.pty_ws._estop = estop


def test_pty_estop_engaged_refuses_upgrade(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch, "webui:\n  token: test-token\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    _inject_bridge(ui, _RecBridge(), estop=lambda cfg: True)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {})
        assert status == 200, out
        data = _raw_ws_upgrade(port, f"/agent/api/pty?{urlencode({'ticket': out['ticket']})}")
        assert int(data.split(b" ", 2)[1]) == 423, data[:200]
        payload = json.loads(data.split(b"\r\n\r\n", 1)[1])
        assert payload["code"] == "ESTOP_ENGAGED"
    finally:
        ui.stop()


def test_pty_resize_input_output_and_close(tmp_path, monkeypatch):
    pytest.importorskip("websockets.sync.client")
    from websockets.sync.client import connect as ws_connect

    cfg = _cfg(tmp_path, monkeypatch, "webui:\n  token: test-token\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    bridge = _RecBridge()
    _inject_bridge(ui, bridge)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {})
        assert status == 200, out
        qs = urlencode({"ticket": out["ticket"], "cols": "80", "rows": "24"})
        with ws_connect(f"ws://127.0.0.1:{port}/agent/api/pty?{qs}",
                        open_timeout=8) as ws:
            ws.send(b"hello")
            assert ws.recv(timeout=8) == b"E:hello"
            ws.send(b"\x1b[RESIZE:120;40]")
            deadline = time.time() + 5
            while time.time() < deadline and bridge.resizes[-1:] != [(120, 40)]:
                time.sleep(0.02)
            assert bridge.resizes[-1] == (120, 40)
        deadline = time.time() + 5
        while time.time() < deadline and ui.pty_ws.active_count != 0:
            time.sleep(0.02)
        assert ui.pty_ws.active_count == 0
    finally:
        ui.stop()
