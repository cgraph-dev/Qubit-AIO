"""Confirmation-loop parity for the Hermes dashboard (Hermes-first architecture).

Staged dashboard mutations must never look executed: they answer 409 +
NEEDS_CONFIRMATION (never 2xx — the vendored UI treats any 2xx as success).
The loop completes through GET /api/ops/approvals + POST /api/ops/confirm
+ POST /api/ops/cancel on the SHARED journal (same records CLI
`qubit approvals`, voice yes/no, and the classic dashboard settle):
one permission/confirmation path, settle-once, audited.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "hermes-dist"
needs_dist = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")


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
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None, token="test-token"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Hermes-Session-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@needs_dist
def test_staged_mutation_is_409_never_2xx(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, out = _http(port, "POST", "/agent/api/ops/boards-create",
                             {"board": "main", "name": "Main"})
        assert status == 409, (status, out)
        assert out.get("state") == "NEEDS_CONFIRMATION", out
        assert out.get("confirmation_id"), out
        assert out.get("ok") is False, out
    finally:
        ui.stop()


@needs_dist
def test_confirm_loop_executes_on_shared_journal(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _, staged = _http(port, "POST", "/agent/api/ops/boards-create",
                           {"board": "main", "name": "Main"})
        cid = staged["confirmation_id"]
        status, listed = _http(port, "GET", "/agent/api/ops/approvals")
        assert status == 200
        assert listed["count"] == 1
        assert listed["approvals"][0]["id"] == cid
        assert listed["approvals"][0]["capability"] == \
            "voice.kanban_boards_create"
        status, done = _http(port, "POST", "/agent/api/ops/confirm",
                              {"confirmation_id": cid})
        assert status == 200, (status, done)
        assert done.get("ok") is True and done.get("state") == "executed", \
            done
        _, listed2 = _http(port, "GET", "/agent/api/ops/approvals")
        assert listed2["count"] == 0
        # Backend effect is real on the canonical store (same module as CLI).
        status, boards = _http(port, "GET", "/agent/api/ops/boards")
        assert status == 200
        result = boards.get("result", boards)
        assert any(b.get("board") == "main"
                   for b in result.get("boards", [])), result
        # Settle-once: confirming again cannot re-execute.
        status, again = _http(port, "POST", "/agent/api/ops/confirm",
                               {"confirmation_id": cid})
        assert status == 400, (status, again)
    finally:
        ui.stop()


@needs_dist
def test_cancel_settles_without_executing(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _, staged = _http(port, "POST", "/agent/api/ops/boards-create",
                           {"board": "ghost", "name": "Ghost"})
        cid = staged["confirmation_id"]
        status, out = _http(port, "POST", "/agent/api/ops/cancel",
                             {"confirmation_id": cid})
        assert status == 200, (status, out)
        _, listed = _http(port, "GET", "/agent/api/ops/approvals")
        assert listed["count"] == 0
        _, boards = _http(port, "GET", "/agent/api/ops/boards")
        result = boards.get("result", boards)
        assert all(b.get("board") != "ghost"
                   for b in result.get("boards", [])), result
    finally:
        ui.stop()


@needs_dist
def test_confirm_unknown_id_is_typed(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, out = _http(port, "POST", "/agent/api/ops/confirm",
                             {"confirmation_id": "nope"})
        assert status == 400, (status, out)
        assert out.get("ok") is False
        status, out = _http(port, "POST", "/agent/api/ops/confirm", {})
        assert status == 400, (status, out)
        assert out.get("code") == "MALFORMED", out
    finally:
        ui.stop()


@needs_dist
def test_model_set_retry_settles_staged_approval(tmp_path):
    """Upstream retry shape (re-POST with confirm flag) settles the staged
    journal approval instead of staging a second one: pending never grows,
    the staged record is consumed settle-once."""
    ui, port = _start(tmp_path)
    try:
        status, staged = _http(port, "POST", "/agent/api/model/set",
                                {"model": "qubit-brain4", "scope": "main"})
        assert status == 409, (status, staged)
        assert staged.get("confirm_required") is True, staged
        _, before = _http(port, "GET", "/agent/api/ops/approvals")
        assert before["count"] == 1
        status, out = _http(port, "POST", "/agent/api/model/set",
                             {"model": "qubit-brain4", "scope": "main",
                              "confirm_expensive_model": True})
        # Either outcome proves the retry settled (not re-staged): the tmp
        # state has no such brain profile, so the backend honestly refuses.
        assert status in (200, 400), (status, out)
        _, after = _http(port, "GET", "/agent/api/ops/approvals")
        assert after["count"] == 0, after
    finally:
        ui.stop()


@needs_dist
def test_banner_settles_pending_confirmation(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    ui, port = _start(tmp_path)
    try:
        _, staged = _http(port, "POST", "/agent/api/ops/boards-create",
                           {"board": "main", "name": "Main"})
        assert staged.get("confirmation_id")
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/qubit-boards",
                      timeout=30000)
            page.wait_for_timeout(2500)
            banner = page.get_by_role("region",
                                      name="Pending confirmations")
            assert banner.count() == 1
            assert "kanban_boards_create" in banner.inner_text()
            page.get_by_role(
                "button",
                name="Confirm voice.kanban_boards_create").click()
            page.wait_for_timeout(2500)
            assert banner.count() == 0
            browser.close()
        _, boards = _http(port, "GET", "/agent/api/ops/boards")
        result = boards.get("result", boards)
        assert any(b.get("board") == "main"
                   for b in result.get("boards", [])), result
    finally:
        ui.stop()


@needs_dist
def test_approvals_require_auth(tmp_path):
    ui, port = _start(tmp_path)
    try:
        import urllib.request as _up
        req = _up.Request("http://127.0.0.1:%d/agent/api/ops/approvals"
                          % port, method="GET")
        try:
            with _up.urlopen(req, timeout=10) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status == 401, status
    finally:
        ui.stop()
