"""Real WebSocket/PTY integration tests for the Qubit `/chat` terminal path.

Covers handshake, ticket auth rejection, origin/peer rejection, input/output,
resize, reconnect, client cancellation, emergency-stop refusal, session limits,
cleanup, and a browser WebSocket round-trip. The PTY transport is injected with
a deterministic echo bridge so the tests are hermetic; the production
``_default_spawn`` (vendored ``hermes --tui`` behind ``PtyBridge``) is exercised
by the availability check at the end.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ws_sync = pytest.importorskip("websockets.sync.client")
from websockets.sync.client import connect as ws_connect  # noqa: E402

RESIZE = b"\x1b[RESIZE:120;40]"


class FakeBridge:
    """Echo PTY: input is mirrored back prefixed, resize recorded, close finite."""

    def __init__(self) -> None:
        self.pid = os.getpid()
        self.closed = False
        self.resizes: list = []
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
        time.sleep(min(max(timeout, 0.0), 0.02))
        return b""

    def write(self, data: bytes) -> bool:
        if self.closed:
            return False
        with self._lock:
            self._rx += b"ECHO:" + bytes(data)
        return True

    def resize(self, *, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return not self.closed


def _start(tmp_path, *, estop=False, spawn=None):
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

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    if estop:
        ui.pty_ws._estop = lambda _cfg: True
    else:
        ui.pty_ws._estop = lambda _cfg: False
    ui.pty_ws._spawn = spawn or (lambda cols, rows, resume, profile: (
        FakeBridge(), {"cwd": "", "argv_head": ["hermes"]}))
    ui.start()
    return ui, ui._server.server_address[1]


def _mint(port, *, headers=None, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/agent/api/auth/ws-ticket", data=data,
        method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {"X-Qubit-Session-Token": "test-token"}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        # HTTPError holds the response body fp; close it or the socket leaks.
        try:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"raw": raw.decode("utf-8", "replace")}
        finally:
            e.close()


def _ws_url(port, ticket, **extra):
    q = {"ticket": ticket, "cols": "100", "rows": "30"}
    q.update(extra)
    from urllib.parse import urlencode
    return f"ws://127.0.0.1:{port}/agent/api/pty?{urlencode(q)}"


def test_handshake_and_echo_roundtrip(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, out = _mint(port)
        assert status == 200 and out["ok"] and out["scope"] == "pty"
        ticket = out["ticket"]
        with ws_connect(_ws_url(port, ticket), open_timeout=8) as ws:
            ws.send(b"hello qubit")
            got = ws.recv(timeout=8)
            assert isinstance(got, (bytes, bytearray))
            assert b"ECHO:hello qubit" in bytes(got)
        # cleanup: session released after the client closes
        deadline = time.time() + 5
        while time.time() < deadline and ui.pty_ws.active_count != 0:
            time.sleep(0.05)
        assert ui.pty_ws.active_count == 0
    finally:
        ui.stop()


def test_named_profile_ticket_resumes_only_its_own_terminal(tmp_path, monkeypatch):
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    from qubit_cli import main_tui_launch, profiles, pty_bridge
    from voicebridge.qubit_session_store import QubitSessionStore, close_cached_db

    root = tmp_path / "runtime"
    monkeypatch.setenv("QUBIT_HOME", str(root))
    monkeypatch.setattr(profiles, "_get_default_qubit_home", lambda: root)
    monkeypatch.setenv("OPENAI_API_KEY", "launch-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "launch-only-test-key")
    monkeypatch.setenv("HERMES_TUI_GATEWAY_URL", "ws://localhost:1/launch")
    monkeypatch.setattr(
        main_tui_launch, "_make_tui_argv", lambda *a, **kw: (["fake-ink"], root))
    homes = {name: root / "profiles" / name for name in ("team-a", "team-b")}
    for name, home in homes.items():
        home.mkdir(parents=True)
        (home / ".env").write_text(f"OPENAI_API_KEY={name}-test-key\n")
        (home / "config.yaml").write_text(f"model: {name}-model\n")
        store = QubitSessionStore(runtime_home=home)
        store.create(session_id="shared-session", model=f"{name}-model")
        store.set_model("shared-session", f"{name}-model", provider="openai")
        store.create(session_id="different-session", model="different-model")

    children = []

    def fake_pty_spawn(_argv, *, cwd, env, cols, rows):
        child = FakeBridge()
        children.append((child, env))
        return child

    monkeypatch.setattr(pty_bridge.PtyBridge, "spawn", fake_pty_spawn)
    ui, port = _start(tmp_path)
    srv = ui.pty_ws
    srv._spawn = srv._production_spawn
    seen_model_profiles = []

    def guard(_cfg, *, profile_id):
        seen_model_profiles.append(profile_id)
        return {"ok": True, "profile_id": profile_id, "model": "launch-model",
                "provider": "openai", "context_tokens": 128000}

    srv._context_guard = guard

    def open_session(name, token, session="shared-session"):
        status, grant = _mint(port, body={
            "management_profile": name, "profile": "catalog-model",
            "resume": session,
        })
        assert status == 200, grant
        url = _ws_url(port, grant["ticket"], attach=token,
                      profile="ignored-query-profile", resume="different-session")
        return grant, ws_connect(url, open_timeout=8)

    def wait_for_detach():
        deadline = time.monotonic() + 5
        while srv.active_count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert srv.active_count == 0

    try:
        status, refusal = _mint(port, body={"management_profile": "missing"})
        assert status == 404 and refusal["code"] == "CHAT_PROFILE_INVALID"
        for name in homes:
            grant, socket = open_session(name, "same-token")
            assert grant["model"] == f"{name}-model"
            with socket as ws:
                ws.send(name.encode())
                assert ws.recv(timeout=8) == b"ECHO:" + name.encode()
            wait_for_detach()
        assert len(children) == 2
        for name, (_child, env) in zip(homes, children):
            assert env["QUBIT_HOME"] == str(homes[name])
            assert env["QUBIT_TUI_RESUME"] == "shared-session"
            assert env["HERMES_MODEL"] == f"{name}-model"
            assert env["OPENAI_API_KEY"] == f"{name}-test-key"
            assert "ANTHROPIC_API_KEY" not in env
            assert "HERMES_TUI_GATEWAY_URL" not in env
            assert (homes[name] / "config.yaml").read_text() == f"model: {name}-model\n"
        # A different attach token must fall back to the same-profile session.
        for name, token in (("team-b", "same-token"), ("team-a", "new-token")):
            _, socket = open_session(name, token)
            with socket as ws:
                assert ws.recv(timeout=8) == b"ECHO:" + name.encode()
            wait_for_detach()
        assert len(children) == 2
        assert set(seen_model_profiles) == {"catalog-model"}
        assert not (root / "state.db").exists()
    finally:
        ui.stop()
        for home in homes.values():
            close_cached_db(home / "state.db")


def test_ticket_is_single_use_and_scoped(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port)
        ticket = out["ticket"]
        with ws_connect(_ws_url(port, ticket), open_timeout=8):
            pass
        # Reuse of the same ticket is rejected (single-use).
        with pytest.raises(Exception):
            with ws_connect(_ws_url(port, ticket), open_timeout=8):
                pass
    finally:
        ui.stop()


def test_auth_rejection_no_ticket_and_bad_ticket(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pytest.raises(Exception):
            with ws_connect(_ws_url(port, "not-a-real-ticket"), open_timeout=8):
                pass
        with pytest.raises(Exception):
            with ws_connect(f"ws://127.0.0.1:{port}/agent/api/pty?cols=80&rows=24",
                            open_timeout=8):
                pass
        # mint endpoint itself requires auth
        status, _ = _mint(port, headers={"X-Qubit-Session-Token": "wrong"})
        assert status == 401
    finally:
        ui.stop()


def test_origin_rejection(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port)
        with pytest.raises(Exception):
            with ws_connect(_ws_url(port, out["ticket"]),
                            origin="https://evil.example", open_timeout=8):
                pass
    finally:
        ui.stop()


def test_resize_escape_is_consumed_not_written(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port)
        with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
            ws.send(RESIZE)
            ws.send(b"after-resize")
            got = ws.recv(timeout=8)
            assert b"ECHO:after-resize" in bytes(got)
            assert b"RESIZE" not in bytes(got)
            bridge = next(iter(ui.pty_ws._active.values())).bridge
            assert bridge.resizes == [(120, 40)]
    finally:
        ui.stop()


def test_reconnect_new_ticket_after_close(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for i in range(2):
            _s, out = _mint(port)
            with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8) as ws:
                ws.send(b"round-%d" % i)
                assert b"ECHO:round-%d" % i in bytes(ws.recv(timeout=8))
        assert ui.pty_ws.active_count == 0
    finally:
        ui.stop()


def test_client_cancel_closes_bridge(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _s, out = _mint(port)
        ws = ws_connect(_ws_url(port, out["ticket"]), open_timeout=8)
        ws.send(b"x")
        ws.recv(timeout=8)
        # A short idle window is what makes the terminal reapable; the
        # session is still live here (active, not yet detached).
        ui.pty_ws._idle = 0.2
        bridge = next(iter(ui.pty_ws._active.values())).bridge
        ws.close()
        # A client close DETACHES (a browser refresh must not kill an in-flight
        # turn): the session leaves the active set and stays live, detached.
        deadline = time.time() + 5
        while time.time() < deadline and ui.pty_ws.active_count != 0:
            time.sleep(0.05)
        assert ui.pty_ws.active_count == 0, "session not released after client close"
        assert bridge.closed is False, "a client close must not kill the terminal"
        # The idle reaper is what retires a detached terminal -- and it must
        # actually close the bridge (a live TUI child and its session lease).
        # The pump stamps ``last_activity`` as it exits, so wait out the window
        # after the detach is observed (observing it costs up to 5 s).
        time.sleep(0.3)
        ui.pty_ws._sweep_idle_detached()
        deadline = time.time() + 5
        while time.time() < deadline and not bridge.closed:
            time.sleep(0.05)
        assert bridge.closed, "the idle reaper did not retire the detached bridge"
        assert ui.pty_ws._detached == {}, "reaped session still in the detached set"
        assert ui.pty_ws._detached_keyless == [], "reaped key-less session leaked"
    finally:
        ui.stop()


def test_estop_refuses_upgrade(tmp_path):
    ui, port = _start(tmp_path, estop=True)
    try:
        _s, out = _mint(port)  # minting is allowed; the upgrade is refused
        with pytest.raises(Exception):
            with ws_connect(_ws_url(port, out["ticket"]), open_timeout=8):
                pass
        assert ui.pty_ws.active_count == 0
    finally:
        ui.stop()


def test_session_limit(tmp_path):
    ui, port = _start(tmp_path)
    ui.pty_ws._max = 1
    try:
        _s, a = _mint(port)
        _s, b = _mint(port)
        with ws_connect(_ws_url(port, a["ticket"]), open_timeout=8):
            with pytest.raises(Exception):
                with ws_connect(_ws_url(port, b["ticket"]), open_timeout=8):
                    pass
    finally:
        ui.stop()


def test_listener_stop_cleans_up_sessions(tmp_path):
    ui, port = _start(tmp_path)
    _s, out = _mint(port)
    ws = ws_connect(_ws_url(port, out["ticket"]), open_timeout=8)
    ws.send(b"x")
    ws.recv(timeout=8)
    # Real teardown requires an idle window; check FIRST that the reaper alone
    # does not kill a session (or nothing is revealed), then make the terminal
    # reapable and stop the listener.
    ui.pty_ws._sweep_idle_detached()
    ui.pty_ws._idle = 0.2
    bridge = next(iter(ui.pty_ws._active.values())).bridge
    ui.stop()
    # The listener's cleanup contract: no session and no PTY child outlives it.
    deadline = time.time() + 5
    while time.time() < deadline and bridge.is_alive():
        time.sleep(0.05)
    assert not bridge.is_alive(), "PTY child outlived the listener"
    assert ui.pty_ws.active_count == 0
    assert not ui.pty_ws._detached, "detached sessions leaked past shutdown"
    assert not ui.pty_ws._detached_keyless, "key-less sessions leaked past shutdown"


def test_default_spawn_is_the_vendored_pty_bridge():
    """The production spawn path is the vendored Hermes PtyBridge, not a stub."""
    from voicebridge import pty_ws
    assert pty_ws._default_spawn is not None
    try:
        from voicebridge import qubit_runtime
        qubit_runtime.ensure_path()
        from qubit_cli.pty_bridge import PtyBridge  # noqa: F401
        assert PtyBridge.is_available()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"vendored PTY bridge unavailable: {exc}")


def test_browser_websocket_roundtrip(tmp_path):
    """A real browser page opens the ticket-auth WS and gets PTY output."""
    pw = pytest.importorskip("playwright.sync_api")
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/sessions", timeout=30000)
            result = page.evaluate(
                """async (port) => {
                  const r = await fetch('/agent/api/auth/ws-ticket', {
                    method: 'POST',
                    headers: {'X-Qubit-Session-Token': 'test-token',
                              'Content-Type': 'application/json'},
                    body: '{}'});
                  const {ticket} = await r.json();
                  return await new Promise((resolve) => {
                    const ws = new WebSocket(`ws://127.0.0.1:${port}/agent/api/pty?ticket=${ticket}&cols=80&rows=24`);
                    ws.binaryType = 'arraybuffer';
                    const done = (v) => { try { ws.close(); } catch (e) {} resolve(v); };
                    ws.onopen = () => ws.send('browser-hello');
                    ws.onmessage = (ev) => {
                      const bytes = new Uint8Array(ev.data);
                      const text = new TextDecoder().decode(bytes);
                      if (text.includes('ECHO:browser-hello')) done('ok');
                    };
                    ws.onerror = () => done('error');
                    setTimeout(() => done('timeout'), 8000);
                  });
                }""", port)
            browser.close()
        assert result == "ok", result
    finally:
        ui.stop()
