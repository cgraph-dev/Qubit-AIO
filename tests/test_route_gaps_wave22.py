"""Wave 22: close the two genuine v0.21.5 route gaps.

The parity audit recorded two reference routes as openly MISSING:

* ``GET /api/chat/workspaces`` -- the v0.21.5 chat-workspace picker backend.
  The vendored router reads a per-profile ``projects.db`` + repo scanner; Qubit
  has one canonical project registry, so the real port is a read-only
  projection of :mod:`voicebridge.projects` (the same store the CLI ``qubit
  project``, the dashboard Projects page, and the voice ``chat_workspaces``
  entry use). ``repos`` is the honest empty list and ``scan_enabled`` is false
  because Qubit runs no git-root scanner -- never a fabricated discovered-repo
  list.

* ``WEBSOCKET /api/display/ws`` -- the raw RFB Bot Desktop viewer. The
  vendored bridge (``qubit_runtime/qubit_cli/web_routers/display.py``) splices
  a profile's Xvnc socket and requires a single-use ``display.observe`` ticket
  plus a live bot-desktop lease. This listener runs neither the tui_gateway
  dialect that mints the ticket nor an Xvnc host, so the honest answer is a
  typed HTTP 426 ``WS_DESKTOP_CLIENT_ONLY`` refusal on the ONE WS gate
  (``webui.py``) -- never a fabricated byte stream.
"""
from __future__ import annotations

import http.client
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ----------------------------------------------------------- chat workspaces

def test_chat_workspaces_route_is_registered_real():
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("GET", "/api/chat/workspaces")
    assert route is not None, "GET /api/chat/workspaces is not routed"
    assert route["disposition"] == "implemented", route
    assert route["handler"] == "h_chat_workspaces", route
    assert callable(HC.HANDLERS.get("h_chat_workspaces")), "handler unregistered"


def test_chat_workspaces_reads_the_real_project_registry(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import projects as PJ

    cfg = _cfg(tmp_path, monkeypatch)
    folder = tmp_path / "work" / "alpha"
    folder.mkdir(parents=True)
    created = PJ.create(cfg, "Site", [str(folder)], confirmed=True, use=True)
    assert created["state"] == "OK", created

    code, out = HC.dispatch(cfg, "GET", "/api/chat/workspaces")
    assert code == 200, out
    assert out["ok"] is True, out
    # The picker wire shape the vendored SPA consumes.
    assert out["home"] == str(Path.home()), out
    assert out["repos"] == [], out          # no scanner -> honest empty list
    assert out["scan_enabled"] is False, out
    assert out["default_cwd"] == str(folder), out
    assert len(out["projects"]) == 1, out
    proj = out["projects"][0]
    assert proj["slug"] == "site" and proj["name"] == "Site", proj
    assert proj["primary_path"] == str(folder), proj
    assert proj["archived"] is False, proj
    assert proj["folders"] == [{"path": str(folder), "label": None,
                                "is_primary": True}], proj


def test_chat_workspaces_empty_store_is_honest(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/chat/workspaces")
    assert code == 200, out
    assert out["projects"] == [], out
    assert out["repos"] == [], out
    assert out["default_cwd"] == str(Path.home()), out
    assert out["scan_enabled"] is False, out
    # scan=1 is accepted and a no-op (Qubit runs no repo scanner).
    code2, out2 = HC.dispatch(cfg, "GET", "/api/chat/workspaces",
                              query={"scan": "1"})
    assert code2 == 200 and out2["scan_enabled"] is False, out2


# -------------------------------------------------------------- display ws

def test_display_ws_route_is_a_declared_typed_gate():
    from voicebridge import qubit_compat as HC
    from voicebridge.webui import QUBIT_WS_GATE_CODES, qubit_ws_gate_code

    route, _ = HC.match("WEBSOCKET", "/api/display/ws")
    assert route is not None, "WEBSOCKET /api/display/ws is not declared"
    assert route["disposition"] == "gated", route
    gate = route["gate"]
    assert gate["code"] == "WS_DESKTOP_CLIENT_ONLY", gate
    assert gate["counterpart"], gate
    # The live WS authority enforces the same code on both the public prefix
    # and the internal compat path.
    assert QUBIT_WS_GATE_CODES["/api/display/ws"] == "WS_DESKTOP_CLIENT_ONLY"
    assert qubit_ws_gate_code("/api/display/ws") == "WS_DESKTOP_CLIENT_ONLY"
    assert qubit_ws_gate_code("/agent/api/display/ws") == \
        "WS_DESKTOP_CLIENT_ONLY"


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n"
                 "  jsonl: true\n"
                 f"  jsonl_path: '{state}'\n"
                 "webui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1], cfg


def _get(port, path, token="test-token"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={
        "X-Hermes-Session-Token": token,
        "Origin": f"http://127.0.0.1:{port}",
    })
    resp = conn.getresponse()
    status, ctype, body = resp.status, resp.getheader("Content-Type", ""), resp.read()
    conn.close()
    return status, ctype, body


def _upgrade(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
    }
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    status, ctype, body = resp.status, resp.getheader("Content-Type", ""), resp.read()
    conn.close()
    return status, ctype, body


def test_display_ws_upgrade_is_a_typed_desktop_only_gate(tmp_path):
    ui, port, _cfg = _start(tmp_path)
    try:
        for path in ("/api/display/ws", "/agent/api/display/ws"):
            status, ctype, body = _upgrade(port, path)
            assert status == 426, (path, status, body[:200])
            assert "application/json" in ctype, (path, ctype)
            out = json.loads(body)
            assert out["code"] == "WS_DESKTOP_CLIENT_ONLY", (path, out)
            assert out["state"] == "UNAVAILABLE", (path, out)
            assert out["ok"] is False and out["ws"] == "single-sse", (path, out)
            assert out["detail"] and out["qubit_counterpart"], (path, out)
            assert out["owner"] == "qubit-policy" and out["rollback"], (path, out)
            # Never a fabricated / accepted stream.
            assert status != 101, (path, status)
    finally:
        ui.stop()


def test_chat_workspaces_live_read_uses_the_qubit_registry(tmp_path):
    """The served picker reads the ONE canonical project store, not the fork.

    The vendored proxy would serve a per-profile ``projects.db`` + a
    session-derived repo list; the Qubit read-authority list keeps this GET on
    ``h_chat_workspaces`` so a workspace created through the CLI/voice (which
    write ``voicebridge.projects``) actually appears on /chat.
    """
    from voicebridge import projects as PJ

    ui, port, cfg = _start(tmp_path)
    try:
        folder = tmp_path / "work" / "beta"
        folder.mkdir(parents=True)
        created = PJ.create(cfg, "Beta", [str(folder)], confirmed=True, use=True)
        assert created["state"] == "OK", created

        status, ctype, body = _get(port, "/agent/api/chat/workspaces")
        assert status == 200, (status, body[:300])
        assert "application/json" in ctype, ctype
        out = json.loads(body)
        # The Qubit handler's shape: the created project is present and the
        # no-scanner contract is honest (the vendored proxy cannot see this
        # project and would report scan_enabled=true).
        assert out["scan_enabled"] is False, out
        assert out["repos"] == [], out
        assert out["default_cwd"] == str(folder), out
        assert [p["slug"] for p in out["projects"]] == ["beta"], out
        assert out["projects"][0]["primary_path"] == str(folder), out
    finally:
        ui.stop()
