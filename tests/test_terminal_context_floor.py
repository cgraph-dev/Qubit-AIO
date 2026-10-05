"""`/chat` long-context floor (owner requirement).

The terminal surface must never silently truncate, so its resolved profile is
required to declare at least ``terminal.min_context_tokens`` (default 65536)
context tokens. A short-context default (Brain4 declares 4096) is a typed
configuration failure that lets the user select another provider/model, not a
silent fall back.
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


def test_default_brain4_below_floor_is_refused(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is False
    assert out["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
    assert out["context_tokens"] == 4096
    assert out["min_context_tokens"] == 65536


def test_context_floor_is_configurable(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, "terminal:\n  min_context_tokens: 4096\n")
    assert R.terminal_min_context_tokens(cfg) == 4096
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is True, out
    assert out["context_tokens"] == 4096


def test_profile_env_bridge_sets_model_and_provider(tmp_path, monkeypatch):
    """The selected profile is passed into the PTY child env."""
    from voicebridge.providers import resolver as R
    from voicebridge.pty_ws import terminal_profile_env

    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    guard = R.terminal_context_guard(cfg)
    assert guard["ok"] is True, guard
    env = terminal_profile_env(cfg, guard)
    assert env["HERMES_MODEL"] == "big-model"
    assert env["HERMES_INFERENCE_MODEL"] == "big-model"
    assert env["HERMES_TUI_PROVIDER"] == "opencode"
    assert env["HERMES_INFERENCE_PROVIDER"] == "opencode"
    # An env: reference is already present in the child env; never re-export.
    assert "OPENCODE_API_KEY" not in env


def test_profile_env_bridge_injects_file_secret(tmp_path, monkeypatch):
    """A file: reference is resolved into the provider's key env var."""
    from voicebridge.providers import resolver as R
    from voicebridge.pty_ws import terminal_profile_env

    key_file = tmp_path / "api.key"
    key_file.write_text("secret-key-123\n", encoding="utf-8")
    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(
        cfg, _api_profile(key_ref=f"file:{key_file}"), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    guard = R.terminal_context_guard(cfg)
    env = terminal_profile_env(cfg, guard)
    assert env["HERMES_MODEL"] == "big-model"
    assert "secret-key-123" in set(env.values())
    assert any(k.endswith("_API_KEY") for k in env)


def test_profile_env_bridge_empty_when_guard_fails(tmp_path, monkeypatch):
    from voicebridge.pty_ws import terminal_profile_env

    cfg = _cfg(tmp_path, monkeypatch)
    assert terminal_profile_env(cfg) == {}


def test_production_spawn_passes_profile_env(tmp_path, monkeypatch):
    from voicebridge import pty_ws
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    captured: dict = {}

    def fake(cols, rows, resume, profile, *, env_overrides=None,
             home_config=None, cfg=None, workspace_cwd=""):
        captured["env"] = env_overrides
        captured["home_config"] = home_config
        captured["cfg"] = cfg
        return object(), {}

    monkeypatch.setattr(pty_ws, "_default_spawn", fake)
    srv = pty_ws.PtyWsServer(cfg=cfg)
    srv._production_spawn(80, 24, "", "")
    assert captured["env"]["HERMES_MODEL"] == "big-model"
    assert captured["env"]["HERMES_TUI_PROVIDER"] == "opencode"
    assert captured["home_config"]["base_url"] == "https://api.example/v1"


def test_production_spawn_refuses_below_floor(tmp_path, monkeypatch):
    import pytest

    from voicebridge import pty_ws

    cfg = _cfg(tmp_path, monkeypatch)
    srv = pty_ws.PtyWsServer(cfg=cfg)
    with pytest.raises(pty_ws.TerminalContextError):
        srv._production_spawn(80, 24, "", "")


def test_bad_or_zero_floor_falls_back_to_default(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, "terminal:\n  min_context_tokens: 0\n")
    assert R.terminal_min_context_tokens(cfg) == R.TERMINAL_MIN_CONTEXT_TOKENS


def test_high_context_active_api_profile_satisfies_floor(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is True, out
    assert out["profile_id"] == "big-api"
    assert out["context_tokens"] == 131072


def test_terminal_profile_config_selects_profile(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               "terminal:\n  profile: big-api\n" + _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is True, out
    assert out["profile_id"] == "big-api"


def test_high_context_but_not_executable_is_typed(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    # Egress consent denied -> high context but not executable; must not be
    # reported as a truncation error, and must still refuse to open.
    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(
        cfg, _api_profile(egress_consent="deny"), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is False
    assert out["code"] == "TERMINAL_PROFILE_NOT_EXECUTABLE"


def test_resume_overlays_stored_session_model(tmp_path, monkeypatch):
    """A resumed TUI keeps its stored session model: the grant reports it.

    Regression for the MODEL card showing the current default (deepseek)
    while the resumed terminal ran its stored session model (muse-spark).
    """
    from voicebridge import pty_ws
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    monkeypatch.setattr(pty_ws, "_session_stored_model",
                        lambda sid, c=None: ("opencode", "old-model"))
    srv = pty_ws.PtyWsServer(cfg=cfg)
    fresh = srv._resolve_guard("")
    assert isinstance(fresh, dict)
    assert fresh["ok"] is True and fresh["model"] == "big-model", fresh
    resumed = srv._resolve_guard("", session_id="dashboard-abc123")
    assert isinstance(resumed, dict)
    assert resumed["model"] == "old-model", resumed
    assert resumed["provider"] == "opencode", resumed
    assert resumed.get("session_pinned") is True, resumed
    assert resumed.get("default_model") == "big-model", resumed
    assert resumed.get("resumed_session") == "dashboard-abc123", resumed


def test_resume_spawn_keeps_home_config_on_default(tmp_path, monkeypatch):
    """Resuming an old session must not flip the global default backwards.

    The child env names the session's pinned model, but the QUBIT_HOME
    config write tracks the current default fresh chats boot from.
    """
    from voicebridge import pty_ws
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    monkeypatch.setattr(pty_ws, "_session_stored_model",
                        lambda sid, c=None: ("opencode", "old-model"))
    captured: dict = {}

    def fake(cols, rows, resume, profile, *, env_overrides=None,
             home_config=None, cfg=None, workspace_cwd=""):
        captured["env"] = env_overrides
        captured["home_config"] = home_config
        return object(), {}

    monkeypatch.setattr(pty_ws, "_default_spawn", fake)
    srv = pty_ws.PtyWsServer(cfg=cfg)
    srv._production_spawn(80, 24, "dashboard-abc123", "")
    assert captured["env"]["HERMES_MODEL"] == "old-model"
    assert captured["home_config"]["model"] == "big-model"


def test_resolve_model_exposes_context_tokens(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    out = R.resolve_model(cfg)
    assert out["ok"] is True
    assert out["context_tokens"] == 4096


def test_pty_production_spawn_enforces_the_floor(tmp_path, monkeypatch):
    """A production PtyWsServer (no injected spawn) wires the guard."""
    from voicebridge.config import Config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = 0.0

    from voicebridge.webui import WebUI

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0,
               state_dir=tmp_path / "state")
    srv = ui.pty_ws
    assert srv._prod_spawn is True
    assert callable(srv._context_guard)
    guard = srv.context_preflight()
    assert guard is not None
    assert guard["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"


def test_ws_ticket_mint_surfaces_the_context_error(tmp_path, monkeypatch):
    """The frontend gets a typed 409 (not a silent terminal failure)."""
    import json
    import urllib.error
    import urllib.request

    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir()
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 "webui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = 0.0

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/agent/api/auth/ws-ticket",
            data=b"{}", method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Qubit-Session-Token", "test-token")
        try:
            urllib.request.urlopen(req, timeout=8)
            raise AssertionError("expected 409 context-floor refusal")
        except urllib.error.HTTPError as exc:
            assert exc.code == 409, exc.code
            body = json.loads(exc.read())
            assert body["ok"] is False
            assert body["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
            assert body["owner"] == "qubit-policy"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Audit + visible model (gaps: refusals not journaled, session_open/ws-ticket
# omit the model, ``?profile=`` ignored by the guard)
# ---------------------------------------------------------------------------
class _FakeBridge:
    """Minimal echo PTY so the REAL-spawn serve path stays hermetic."""

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


def _prod_cfg(tmp_path, monkeypatch):
    """Config with an executable high-context active profile + a web token."""
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch,
               _FRONTIER + "webui:\n  token: test-token\n")
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
    req.add_header("X-Qubit-Session-Token", "test-token")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_session_open_audit_names_model_and_profile(tmp_path, monkeypatch):
    """GAP 2/4: the resolved Qubit model is in session_open and ``?profile=``
    selects that model profile (never forwarded as a vendored profile dir)."""
    from voicebridge import pty_ws as P

    pytest.importorskip("websockets.sync.client")
    from websockets.sync.client import connect as ws_connect

    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    events: list = []
    monkeypatch.setattr(P, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    spawned: dict = {}

    def fake_spawn(cols, rows, resume, profile, *, env_overrides=None,
                   home_config=None, cfg=None, workspace_cwd=""):
        spawned["profile"] = profile
        return _FakeBridge(), {"cwd": "/tmp/qubit", "argv_head": ["qubit"]}

    monkeypatch.setattr(P, "_default_spawn", fake_spawn)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {"profile": "big-api"})
        assert status == 200, out
        assert out["model"] == "big-model"
        qs = urlencode({"ticket": out["ticket"], "cols": "80", "rows": "24",
                        "profile": "big-api"})
        with ws_connect(f"ws://127.0.0.1:{port}/agent/api/pty?{qs}",
                        open_timeout=8) as ws:
            ws.send(b"hi")
            ws.recv(timeout=8)
        opens = [f for ev, f in events if ev == "webui.pty.session_open"]
        assert opens, events
        rec = opens[-1]
        assert rec["profile_id"] == "big-api"
        assert rec["provider"] == "opencode"
        assert rec["model"] == "big-model"
        assert rec["context_tokens"] == 131072
        assert spawned["profile"] == ""
    finally:
        ui.stop()


def test_ws_ticket_response_carries_non_secret_model_fields(tmp_path,
                                                             monkeypatch):
    """GAP 3: the 200 body surfaces the active model/context (references only)."""
    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {"profile": "big-api"})
        assert status == 200, out
        assert out["ok"] is True
        assert out["profile_id"] == "big-api"
        assert out["provider"] == "opencode"
        assert out["model"] == "big-model"
        assert out["context_tokens"] == 131072
        assert out["min_context_tokens"] == 65536
        # References only: no credential value/ref or endpoint in the body.
        assert "key_ref" not in out
        assert "base_url" not in out
        assert "OPENCODE_API_KEY" not in json.dumps(out)
    finally:
        ui.stop()


def test_below_floor_refusal_is_journaled(tmp_path, monkeypatch):
    """GAP 1: the ws-ticket 409 writes a real, non-secret audit event."""
    from voicebridge import pty_ws as P

    cfg = _cfg(tmp_path, monkeypatch, "webui:\n  token: test-token\n")
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
        assert status == 409, out
        assert out["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
        refusals = [f for ev, f in events if ev == "webui.pty.context_refused"]
        assert refusals, events
        rec = refusals[-1]
        assert rec["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
        assert rec["profile_id"] == "qubit-brain4"
        assert rec["model"] == "qubit-brain4"
        assert rec["context_tokens"] == 4096
        assert rec["min_context_tokens"] == 65536
        assert rec["via"] == "ws-ticket"
        assert "key_ref" not in rec
        assert "base_url" not in rec
    finally:
        ui.stop()


def test_unknown_requested_profile_is_typed_refusal(tmp_path, monkeypatch):
    """GAP 4: an unknown Qubit profile id is refused, not silently substituted."""
    from voicebridge import pty_ws as P

    cfg = _prod_cfg(tmp_path, monkeypatch)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    events: list = []
    monkeypatch.setattr(P, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        status, out = _mint(port, {"profile": "not-a-qubit-profile"})
        assert status == 409, out
        assert out["ok"] is False
        assert out["code"] == "UNKNOWN_PROFILE"
        refusals = [f for ev, f in events if ev == "webui.pty.context_refused"]
        assert refusals, events
        rec = refusals[-1]
        assert rec["code"] == "UNKNOWN_PROFILE"
        assert rec["requested_profile"] == "not-a-qubit-profile"
        assert rec["via"] == "ws-ticket"
    finally:
        ui.stop()


def test_ws_serve_refusal_is_journaled(tmp_path, monkeypatch):
    """GAP 1: the pre-spawn refusal on the WS/PtyWs path is journaled too."""
    from voicebridge import pty_ws as P

    pytest.importorskip("websockets.sync.client")
    from websockets.sync.client import connect as ws_connect

    cfg = _cfg(tmp_path, monkeypatch, "webui:\n  token: test-token\n")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    events: list = []
    monkeypatch.setattr(P, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    ui = _server(cfg, state)
    ui.start()
    try:
        port = ui._server.server_address[1]
        ticket, _ttl = ui.pty_ws.tickets.mint(
            scope=P.SCOPE_PTY, session="s1", owner="tester", target="chat")
        qs = urlencode({"ticket": ticket, "cols": "80", "rows": "24"})
        with pytest.raises(Exception):
            with ws_connect(f"ws://127.0.0.1:{port}/agent/api/pty?{qs}",
                            open_timeout=8):
                pass
        refusals = [f for ev, f in events if ev == "webui.pty.context_refused"]
        assert refusals, events
        rec = refusals[-1]
        assert rec["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
        assert rec["model"] == "qubit-brain4"
        assert rec["via"] == "ws-pty"
        assert rec["session"] == "s1"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Real model-window cross-check (declared context_tokens is not trusted).
# ---------------------------------------------------------------------------
def _stub_window(monkeypatch, value, source):
    from voicebridge.providers import resolver as R

    monkeypatch.setattr(
        R, "_real_context_window",
        lambda eff, live=False: (value, source), raising=True)


def test_declared_overstatement_is_refused_when_real_window_smaller(
        tmp_path, monkeypatch):
    """A profile declaring 131072 for a 16384-window model must not open."""
    from voicebridge.providers import resolver as R

    _stub_window(monkeypatch, 16384, "cache")
    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is False, out
    assert out["code"] == "TERMINAL_CONTEXT_BELOW_MINIMUM"
    assert out["real_context_tokens"] == 16384
    assert out["declared_context_tokens"] == 131072
    assert out["context_verified"] is True


def test_unverifiable_window_passes_but_is_flagged(tmp_path, monkeypatch):
    """when_available: no real window -> declared accepted, flagged unverified."""
    from voicebridge.providers import resolver as R

    _stub_window(monkeypatch, None, "not-cached")
    cfg = _cfg(tmp_path, monkeypatch, _FRONTIER)
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is True, out
    assert out["context_verified"] is False
    assert out["context_window_source"] == "not-cached"
    assert out["context_verification"] == "when_available"


def test_required_mode_refuses_unverified_window(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    _stub_window(monkeypatch, None, "not-cached")
    cfg = _cfg(tmp_path, monkeypatch,
               _FRONTIER + "terminal:\n  context_window_verification: required\n")
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is False, out
    assert out["code"] == "TERMINAL_CONTEXT_UNVERIFIED"


def test_declared_mode_skips_cross_check(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R

    _stub_window(monkeypatch, 16384, "cache")
    cfg = _cfg(tmp_path, monkeypatch,
               _FRONTIER + "terminal:\n  context_window_verification: declared\n")
    R.upsert_model_profile(cfg, _api_profile(), confirm=True)
    R.set_active_profile(cfg, "big-api", confirm=True)
    out = R.terminal_context_guard(cfg)
    assert out["ok"] is True, out
    assert out["context_verification"] == "declared"
    assert "context_verified" not in out
