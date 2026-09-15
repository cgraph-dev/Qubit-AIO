"""Wave-2 W3 — live Qubit Boards lifecycle in the browser.

Seeds a board and card through the real WebUI broker, then drives the
`/qubit-boards` page: select the card, archive it (confirmed), switch
the visibility filter to Archived, restore it (confirmed), and confirm the
card is active again after a reload. Proves the archive/restore workflow and
that state survives reload.
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


def _post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "X-Hermes-Session-Token": "test-token"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def _settle(port, op, args):
    res = _post(port, "/agent/api/ops/run", {"id": op, "args": args})
    cid = res.get("confirmation_id")
    if cid:
        _post(port, "/agent/api/ops/confirm", {"confirmation_id": cid})


def test_boards_page_archive_and_restore(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _settle(port, "kanban_boards_create", {"board": "w3live", "name": "W3 Live"})
        _settle(port, "kanban_create", {"title": "Live card", "board": "w3live"})

        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto("http://127.0.0.1:%d/qubit-boards" % port,
                      timeout=30000)
            page.wait_for_selector('[aria-label="Active board"]', timeout=30000)
            page.get_by_label("Active board").fill("w3live")
            page.wait_for_timeout(2500)

            # Select the seeded card and assert the full action set renders.
            page.get_by_role("button", name="Select card 1").click()
            actions = page.locator('[data-testid="card-actions"]')
            for label in ("Assign card", "Block card", "Unblock card",
                          "Archive card", "Restore card", "Save card title"):
                assert actions.get_by_role("button", name=label, exact=True).count() == 1, label

            # Archive (staged -> confirm).
            actions.get_by_role("button", name="Archive card", exact=True).click()
            page.wait_for_timeout(600)
            page.get_by_role("region", name="Pending board confirmation") \
                .get_by_role("button", name="Confirm", exact=True).click()
            page.wait_for_timeout(1500)

            # Switch to the archived view and restore the card.
            page.get_by_label("Card visibility filter").select_option("archived")
            page.wait_for_timeout(1500)
            page.get_by_role("button", name="Select card 1").click()
            page.locator('[data-testid="card-actions"]').get_by_role(
                "button", name="Restore card", exact=True).click()
            page.wait_for_timeout(600)
            page.get_by_role("region", name="Pending board confirmation") \
                .get_by_role("button", name="Confirm", exact=True).click()
            page.wait_for_timeout(1500)

            # Back to active view: the card survives a reload.
            page.get_by_label("Card visibility filter").select_option("")
            page.reload(timeout=30000)
            page.wait_for_selector('[aria-label="Active board"]', timeout=30000)
            page.wait_for_timeout(2500)
            assert "Live card" in page.inner_text("body")
            browser.close()
    finally:
        ui.stop()
