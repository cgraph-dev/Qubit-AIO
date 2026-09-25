"""Agent 3 (role C) — dashboard page inventory: every inventoried page is
reachable through the real served SPA and backed by a real compat route.

Complements ``test_qubit_frontend_inventory8u.py`` by exercising the served
HTTP boundary: the built dashboard at ``/`` answers every baseline
route, every page's declared backing route resolves through the production
``voicebridge.qubit_compat`` dispatcher, and the page inventory never points
at a file or route that does not exist.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

MAP = REPO_ROOT / "docs/archive/2026-09-21" / "HERMES_FRONTEND_MAP.json"


def _doc():
    return json.loads(MAP.read_text(encoding="utf-8"))


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home" / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return Config.load(p)


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _get(port, path, token="test-token"):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="GET")
    req.add_header("X-Hermes-Session-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_page_inventory_files_and_backends_exist():
    doc = _doc()
    for row in doc["inventory"]["pages"]:
        assert (REPO_ROOT / row["qubit_file"]).is_file(), row["qubit_file"]
        backend = row["backend_route"]
        if backend and backend.startswith("/api"):
            assert row["status"] == "adapted", row
        if backend.startswith("/api"):
            assert any(e == backend or backend.startswith(e)
                       for e in doc["endpoints"]), backend


def test_every_inventoried_backend_route_dispatches(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for row in _doc()["inventory"]["pages"]:
        route = row["backend_route"]
        if not route.startswith("/api"):
            continue
        status, body = HC.dispatch(cfg, "GET", route, {}, {})
        assert status not in (404, 500), (row["qubit_route"], route, status)


def test_served_spa_answers_every_inventoried_page(tmp_path):
    doc = _doc()
    ui, port = _start(tmp_path)
    try:
        routes = list(doc["routes"]) + ["/achievements", "/kanban"]
        for route in routes:
            status, ctype, body = _get(port, route)
            assert status == 200, (route, status)
            assert "text/html" in ctype, (route, ctype)
            assert b'id="root"' in body, route
    finally:
        ui.stop()


def test_served_dashboard_title_is_qubit(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _ctype, body = _get(port, "/")
        assert status == 200
        assert b"<title>Qubit Agent" in body, body[:200]
    finally:
        ui.stop()


def test_served_fleet_and_plugin_bundles_are_qubit_branded(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _ctype, body = _get(port, "/plugins/fleet.json")
        assert status == 200
        fleet = {m["name"] for m in json.loads(body)}
        assert fleet == {"qubit-achievements", "kanban"}
        for name in sorted(fleet):
            status, ctype, body = _get(
                port, f"/plugins/{name}/dist/index.js")
            assert status == 200 and "javascript" in ctype, (name, status)
            # product-facing copy is Qubit; lowercase plugin ids/comments and
            # the X-Hermes-Session-Token wire header are compatibility tokens.
            assert b"Hermes Achievements" not in body, name
            assert b"Hermes Agent" not in body, name
            assert f'register("{name}"'.encode() in body, name
    finally:
        ui.stop()
