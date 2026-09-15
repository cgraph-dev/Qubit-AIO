"""Agent 3 (wave 2) — live control verification (boards + achievements).

Drives the real browser controls on the built dashboard against the real
backend: Kanban claim + comment + snapshot through the shared ops broker with
inline Confirm, and Achievements two-step Reset through the confirm-gated
plugin route. Proves the wired buttons are not dead.
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
pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")

pw = pytest.importorskip("playwright.sync_api")


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


def _req(port, path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        method=method or ("POST" if body is not None else "GET"),
        headers={"Content-Type": "application/json",
                 "X-Hermes-Session-Token": "test-token"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def _settle(port, op, args):
    res = _req(port, "/agent/api/ops/run", {"id": op, "args": args})
    cid = res.get("confirmation_id")
    if cid:
        _req(port, "/agent/api/ops/confirm", {"confirmation_id": cid})


def _tasks(port, board):
    res = _req(port, f"/agent/api/ops/kanban?board={board}")
    return res["result"]["tasks"]


def test_boards_claim_and_comment_controls_work(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _settle(port, "kanban_boards_create", {"board": "ag3live", "name": "AG3"})
        _settle(port, "kanban_create", {"title": "Claim me", "board": "ag3live"})

        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/qubit-boards", timeout=30000)
            page.wait_for_selector('[aria-label="Active board"]', timeout=30000)
            page.get_by_label("Active board").fill("ag3live")
            page.wait_for_timeout(2500)

            page.get_by_role("button", name="Select card 1").click()
            actions = page.locator('[data-testid="card-actions"]')
            for label in ("Claim card", "Add comment", "Restore snapshot",
                          "Assign card", "Block card", "Archive card"):
                assert actions.get_by_role("button", name=label,
                                           exact=True).count() == 1, label

            # Claim through the UI (staged -> inline Confirm).
            actions.get_by_role("button", name="Claim card", exact=True).click()
            page.wait_for_timeout(600)
            page.get_by_role("region", name="Pending board confirmation") \
                .get_by_role("button", name="Confirm", exact=True).click()
            page.wait_for_timeout(1500)

            # Comment through the UI.
            actions.get_by_label("New comment body").fill("live agent3 comment")
            actions.get_by_role("button", name="Add comment", exact=True).click()
            page.wait_for_timeout(600)
            page.get_by_role("region", name="Pending board confirmation") \
                .get_by_role("button", name="Confirm", exact=True).click()
            page.wait_for_timeout(1500)
            assert "live agent3 comment" in page.inner_text("body")
            browser.close()

        task = _tasks(port, "ag3live")[0]
        assert task["status"] == "claimed", task
    finally:
        ui.stop()


def test_achievements_reset_control_is_confirm_gated(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/achievements", timeout=30000)
            page.wait_for_selector("text=Qubit Achievements", timeout=30000)
            page.wait_for_timeout(2000)

            reset = page.get_by_role("button", name="Reset", exact=True)
            assert reset.count() == 1, "Reset control missing"
            reset.click()
            page.wait_for_timeout(300)
            confirm = page.get_by_role("button", name="Confirm reset",
                                       exact=True)
            assert confirm.count() == 1, "Reset confirmation missing"
            with page.expect_response(
                    lambda r: "reset-state" in r.url):
                confirm.click()
            page.wait_for_timeout(1500)
            # The catalog is still served after reset (no dead page).
            assert page.get_by_text("Qubit Achievements").count() >= 1
            browser.close()

        payload = _req(port, "/agent/api/plugins/hermes-achievements/achievements")
        assert payload["unlocked_count"] == 0
    finally:
        ui.stop()
