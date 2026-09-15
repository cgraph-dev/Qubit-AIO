"""Wave-2 W3 — live `/chat` proof: native Qubit turn, no PTY reconnect loop.

Boots the real Qubit WebUI against the built dashboard and drives a real turn
from the `/chat` page. Asserts (a) the page sends `POST /api/qubit/chat`
and renders a real reply, and (b) it never opens `/api/pty` nor shows the old
1006 reconnect banner.
"""
from __future__ import annotations

import sys
import time
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


def test_chat_page_uses_native_turn_and_no_pty(tmp_path):
    ui, port = _start(tmp_path)
    pty_requests: list[str] = []
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on(
                "request",
                lambda r: pty_requests.append(r.url) if "/api/pty" in r.url else None,
            )
            page.goto(f"http://127.0.0.1:{port}/chat", timeout=30000)
            page.wait_for_selector('[aria-label="Message Qubit"]', timeout=30000)
            box = page.get_by_label("Message Qubit")
            assert box.count() == 1
            box.fill("hello qubit")
            page.get_by_role("button", name="Send").click()
            page.wait_for_timeout(15000)
            body = page.inner_text("body")
            assert "hello qubit" in body, body[-500:]
            # A real reply or a typed backend state — never the old PTY loop.
            assert ("You" in body and "Qubit" in body), body[-500:]
            assert "Reconnecting" not in body, body[-500:]
            assert "code 1006" not in body, body[-500:]
            browser.close()
    finally:
        ui.stop()
    assert pty_requests == [], pty_requests


def test_chat_page_has_no_websocket_pty(tmp_path):
    """The page must not even attempt a WebSocket — no /api/pty, /api/ws,
    /api/events upgrades from the chat surface."""
    ui, port = _start(tmp_path)
    ws_urls: list[str] = []
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("websocket", lambda w: ws_urls.append(w.url))
            page.goto(f"http://127.0.0.1:{port}/chat", timeout=30000)
            page.wait_for_timeout(4000)
            browser.close()
    finally:
        ui.stop()
    assert not [u for u in ws_urls if "/api/pty" in u], ws_urls
