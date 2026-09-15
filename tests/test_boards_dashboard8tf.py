"""Functional Boards tab (Hermes-first architecture, OpenClaw addition).

The qubit-boards overlay is a real command surface, not a status tile:
create board, add card, move card, complete card — every mutation runs the
shared kanban backend (same module as CLI/voice) through the shared
confirmation journal (409 staged + inline Confirm/Cancel, same records as
the shell banner, CLI approvals, voice yes/no).
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


def _stage_confirm(port, method, path, body):
    status, out = _http(port, method, path, body)
    assert status == 409, (path, status, out)
    assert out.get("state") == "NEEDS_CONFIRMATION", out
    status, done = _http(port, "POST", "/agent/api/ops/confirm",
                          {"confirmation_id": out["confirmation_id"]})
    assert status == 200 and done.get("ok"), (path, status, done)
    return done


@needs_dist
def test_board_card_lifecycle_over_http(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _stage_confirm(port, "POST", "/agent/api/ops/boards-create",
                       {"board": "main", "name": "Main"})
        done = _stage_confirm(port, "POST", "/agent/api/ops/cards-create",
                              {"title": "buy milk", "board": "main"})
        tid = ((done.get("receipt") or {}).get("result") or {}).get(
            "task", {}).get("id")
        assert tid, done
        _stage_confirm(port, "POST", "/agent/api/ops/cards-move",
                       {"task_id": tid, "status": "claimed"})
        _stage_confirm(port, "POST", "/agent/api/ops/cards-complete",
                       {"task_id": tid})
        status, out = _http(port, "GET", "/agent/api/ops/kanban?board=main")
        assert status == 200
        tasks = (out.get("result") or {}).get("tasks", [])
        assert [(t["id"], t["status"]) for t in tasks] == [(tid, "done")], \
            tasks
    finally:
        ui.stop()


@needs_dist
def test_boards_tab_drives_backend(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/qubit-boards",
                      timeout=30000)
            page.wait_for_timeout(2500)
            # Create board through the tab, confirm inline.
            page.get_by_label("New board slug").fill("main")
            page.get_by_label("New board name").fill("Main")
            page.get_by_role("button", name="Create board").click()
            page.wait_for_timeout(1200)
            region = page.get_by_role("region",
                                      name="Pending board confirmation")
            assert region.count() == 1
            region.get_by_role("button", name="Confirm").click()
            page.wait_for_timeout(2000)
            assert region.count() == 0
            # Add card through the tab, confirm inline.
            page.get_by_label("New card title").fill("buy milk")
            page.get_by_role("button", name="Add card").click()
            page.wait_for_timeout(1200)
            assert region.count() == 1
            region.get_by_role("button", name="Confirm").click()
            page.wait_for_timeout(2000)
            body = page.inner_text("body")
            assert "buy milk" in body, body[-500:]
            # Move the card across columns through the tab.
            page.get_by_label("Move card 1 to status").select_option(
                "claimed")
            page.wait_for_timeout(1200)
            assert region.count() == 1
            region.get_by_role("button", name="Confirm").click()
            page.wait_for_timeout(2000)
            # Complete the card through the tab.
            page.get_by_role("button", name="Complete card 1").click()
            page.wait_for_timeout(1200)
            assert region.count() == 1
            region.get_by_role("button", name="Confirm").click()
            page.wait_for_timeout(2000)
            assert not errors, errors[:3]
            browser.close()
        status, out = _http(port, "GET", "/agent/api/ops/kanban?board=main")
        assert status == 200
        tasks = (out.get("result") or {}).get("tasks", [])
        assert [(t["id"], t["status"]) for t in tasks] == [(1, "done")], \
            tasks
    finally:
        ui.stop()
