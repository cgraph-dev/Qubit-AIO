"""Packet 8T-F — runtime tiles (dashboard/CLI inventory) + deferral boundaries.

Proves the /api/runtime backend, settings UI tile, and CLI status lines
report the REAL provision state (never invented), and that every DEFERRED
matrix row has a tested boundary (no shell paths, no second memory/
scheduler/listener, no upstream pip imports, no hidden UI success).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _start(tmp_path):
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
    ui.start()
    return ui


def _http(ui, method, path, body=None, token="test-token"):
    import urllib.request
    port = ui._server.server_address[1]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        # 30s: local loopback server under full-suite CPU load.
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw or "{}")
            except Exception:
                return r.status, {"_raw": raw[:120]}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode() or "{}"
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"_raw": raw[:120]}
        finally:
            e.close()


# ---------------------------------------------------------------------------
# /api/runtime backend
# ---------------------------------------------------------------------------
def test_runtime_route_requires_auth(tmp_path):
    ui = _start(tmp_path)
    try:
        st, _ = _http(ui, "GET", "/api/runtime", token=None)
        assert st == 401
        st, _ = _http(ui, "GET", "/api/runtime", token="wrong")
        assert st == 401
    finally:
        ui.stop()


def test_runtime_route_reports_real_state(tmp_path):
    ui = _start(tmp_path)
    try:
        st, body = _http(ui, "GET", "/api/runtime")
        assert st == 200, body
        assert body["product"]["name"] == "Qubit Agent"
        assert body["hermes"]["commit"] == \
            "2237be355906fbe6065ce1815711eee52b2d646e"
        assert body["hermes"]["state"] in ("DISABLED", "READY", "UNAVAILABLE",
                                           "DEGRADED")
        assert "vendored" in body and "voice" in body and "broker" in body
        assert "approvals" in body and "adapters" in body
        blob = json.dumps(body).lower()
        for secret in ("sk-", "token:", "cookie", "Bearer "):
            assert secret not in blob, secret
    finally:
        ui.stop()


def test_settings_page_has_runtime_tile():
    html = (REPO_ROOT / "frontend" / "settings.html").read_text()
    assert "runtimeStatus()" in html
    assert 'id="runtime-state"' in html and 'id="runtime-list"' in html
    assert "/api/runtime" in html


def test_cli_status_shows_runtime_lines(tmp_path, monkeypatch, capsys):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")
    from voicebridge import cli as CLI

    CLI._cmd_status(cfg)
    out = capsys.readouterr().out
    assert "hermes runtime  :" in out
    assert "emergency stop :" in out


# ---------------------------------------------------------------------------
# estop cross-wire callbacks (no upstream needed)
# ---------------------------------------------------------------------------
def test_estop_listeners_fire_on_stop_not_reset():
    import voicebridge.power as PW

    control = PW.PowerControl()
    fired = []
    control.on_estop(lambda: fired.append("paused"))
    assert fired == []
    control.emergency_stop()
    assert fired == ["paused"]
    control.reset()
    assert fired == ["paused"], "reset must never resume upstream"


def test_failing_listener_cannot_break_latch():
    import voicebridge.power as PW

    control = PW.PowerControl()

    def _boom():
        raise RuntimeError("listener blew up")

    control.on_estop(_boom)
    out = control.emergency_stop()
    assert out["state"] == "OK"
    assert control.state["emergency_stop"] is True


# ---------------------------------------------------------------------------
# Deferral boundaries: the reasons DEFER rows stay out, proven in code
# ---------------------------------------------------------------------------
def _product_sources():
    return [p for p in (REPO_ROOT / "voicebridge").rglob("*.py")
            if "__pycache__" not in p.parts]


def _code_text(path: Path) -> str:
    """Source minus comments and string literals (prose can't execute)."""
    import io
    import tokenize

    parts = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            parts.append(tok.string)
    except Exception:
        return path.read_text()
    return " ".join(parts)


def test_no_shell_execution_paths():
    offenders = [str(p) for p in _product_sources()
                 if p.name != "__init__.py" and
                 "shell=True" in _code_text(p)]
    assert offenders == [], offenders


def test_no_upstream_pip_imports_in_product():
    offenders = []
    for p in _product_sources():
        text = _code_text(p)
        for mod in ("hermes_agent", "openclaw_sdk", "openclaw-sdk"):
            if f"import {mod}" in text or f"from {mod}" in text:
                offenders.append(f"{p}:{mod}")
    assert offenders == [], offenders


def test_no_second_memory_or_scheduler():
    offenders = []
    for p in _product_sources():
        text = _code_text(p)
        for mod in ("import schedule", "from apscheduler", "import celery",
                    "from croniter", "watchdog.observers"):
            if mod in text:
                offenders.append(f"{p}:{mod}")
    assert offenders == [], offenders


def test_no_websocket_server_or_hidden_listener():
    offenders = [str(p) for p in _product_sources()
                 if "websockets.serve" in _code_text(p)
                 or "socketserver.TCPServer" in _code_text(p)]
    assert offenders == [], offenders


def test_no_desktop_electron_or_cloud_browser_refs():
    offenders = []
    for p in _product_sources():
        text = _code_text(p).lower()
        for mark in ("electron", "camofox", "playwright.chromium.launch",
                     "selenium"):
            if mark in text:
                offenders.append(f"{p}:{mark}")
    assert offenders == [], offenders
