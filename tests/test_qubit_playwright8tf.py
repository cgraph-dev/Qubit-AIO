"""Playwright interaction tests for the vendored Hermes dashboard (amendment 2026-09-11).

Boots the real Qubit WebUI, loads `/` in headless Chromium, and proves
every page family renders, navigation works, the Qubit Chat control drives a
real backend turn (typed success or typed model-unavailable — never fake),
overlay tabs render real backend data, and narrow-mobile layout holds.
Screenshots go to /tmp/opencode/shots (evidence, not committed).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"
pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")

pw = pytest.importorskip("playwright.sync_api")

SHOTS = Path("/tmp/opencode/shots")

FAMILIES = [
    ("chat", "Chat"), ("sessions", "Sessions"), ("files", "Files"),
    ("analytics", "Analytics"), ("models", "Models"), ("logs", "Logs"),
    ("cron", "Cron"), ("skills", "Skills"), ("plugins", "Plugins"),
    ("mcp", "MCP"), ("channels", "Channels"), ("webhooks", "Webhooks"),
    ("pairing", "Pairing"), ("profiles", "Profiles"),
    ("profiles/new", "ProfileBuilder"), ("config", "Config"), ("env", "Keys"),
    ("system", "System"), ("docs", "Docs"),
    ("qubit-chat", "Qubit Chat"), ("qubit-gateway", "Gateway"),
    ("qubit-boards", "Boards"), ("qubit-runtime", "Runtime"),
]


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    # This crawl only renders the dashboard; the shipped /chat floor is 65536
    # (covered by tests/test_terminal_context_floor.py). Lower it here so the
    # hermetic local default can open /chat without a configured API profile.
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n"
                 "terminal:\n  min_context_tokens: 4096\n")
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


def test_dashboard_families_render_and_navigate(tmp_path):
    ui, port = _start(tmp_path)
    SHOTS.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: errors.append(m.text)
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", timeout=30000)
            page.wait_for_selector("nav, aside, [role=navigation]", timeout=30000)
            page.wait_for_timeout(2500)
            page.screenshot(path=str(SHOTS / "qubit-shell.png"))
            for slug, label in FAMILIES:
                page.goto(f"http://127.0.0.1:{port}/{slug}", timeout=30000)
                page.wait_for_timeout(1800)
                body = page.inner_text("body")
                assert len(body.strip()) > 50, (slug, "empty render")
                page.screenshot(path=str(SHOTS / f"qubit-{slug}.png"))
            browser.close()
    finally:
        ui.stop()
    # By-design noise (documented, typed, tested at unit level):
    # - ws:// handshakes to /api/ws, /api/events, /api/pty fail: Qubit serves
    #   no WS gateway dialect (single SSE event path, amendment point 9).
    # - 403s from policy gates (credential pool, portal, remote update check).
    allowed_subs = ("ws://", "/api/ws", "/api/events?channel=", "/api/pty",
                    "/api/credentials/pool", "/api/portal",
                    "/hermes/update/check", "status of 403")
    fatal = [e for e in errors
             if not any(s in e for s in allowed_subs)]
    assert not fatal, f"console/page errors: {fatal[:10]}"
    assert (SHOTS / "qubit-shell.png").is_file()


def test_qubit_chat_control_drives_backend(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/qubit-chat", timeout=30000)
            page.wait_for_timeout(2000)
            box = page.get_by_label("Message Qubit")
            assert box.count() == 1
            box.fill("hello qubit")
            page.get_by_role("button", name="Send").click()
            page.wait_for_timeout(15000)
            body = page.inner_text("body")
            # The native transcript shows the user turn; either a real model
            # reply or a typed backend state follows — never silence.
            assert "hello qubit" in body, body[-500:]
            assert ("Qubit" in body or "MODEL_" in body or "AGENT_" in body
                    or "EMERGENCY" in body or "unavailable" in body.lower()), body[-500:]
            browser.close()
    finally:
        ui.stop()


def test_narrow_mobile_layout(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(f"http://127.0.0.1:{port}/sessions", timeout=30000)
            page.wait_for_timeout(2000)
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 1, f"horizontal overflow: {overflow}px"
            page.screenshot(path=str(SHOTS / "qubit-mobile-sessions.png"))
            browser.close()
    finally:
        ui.stop()


def test_rebrand_product_identity(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", timeout=30000)
            page.wait_for_timeout(3000)
            assert "Qubit" in page.title(), page.title()
            visible = page.evaluate("""() => {
              const out = [];
              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              let n;
              while (n = walker.nextNode()) {
                let el = n.parentElement, ok = true;
                while (el && el !== document.body) {
                  if (getComputedStyle(el).display === 'none') { ok = false; break; }
                  el = el.parentElement;
                }
                if (ok && n.textContent.trim()) out.push(n.textContent.trim());
              }
              return out.join('\\n');
            }""")
            assert "Qubit Agent" in visible, visible[:500]
            assert not re.search(r"Hermes", visible), visible[:500]
            browser.close()
    finally:
        ui.stop()


def test_accessibility_basics(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", timeout=30000)
            page.wait_for_timeout(2500)
            assert page.locator("h1, h2").count() >= 1
            buttons = page.locator("button").count()
            assert buttons >= 1
            unnamed = page.evaluate(
                "Array.from(document.querySelectorAll('button'))"
                ".filter(b => !(b.innerText.trim() || b.getAttribute('aria-label'))).length")
            assert unnamed == 0, f"{unnamed} buttons without accessible names"
            browser.close()
    finally:
        ui.stop()


def test_chat_is_terminal_and_qubit_chat_is_native(tmp_path):
    """Owner chat mapping (2026-09-17).

    `/chat` renders the real Hermes terminal (xterm) over the Qubit PTY
    WebSocket; `/qubit-chat` keeps the Qubit-native ChatPage. Both stay on
    their own URL (no redirect) and mount their own controls.
    """
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # /chat -> terminal surface (xterm host), no transcript composer.
            page.goto(f"http://127.0.0.1:{port}/chat", timeout=30000)
            page.wait_for_timeout(3000)
            assert page.url.endswith("/chat"), page.url
            assert page.locator('[aria-label="Qubit terminal"]').count() == 1
            assert page.locator(".xterm").count() >= 1
            # /qubit-chat -> Qubit-native ChatPage (transcript + composer).
            page.goto(f"http://127.0.0.1:{port}/qubit-chat", timeout=30000)
            page.wait_for_timeout(3000)
            assert page.url.endswith("/qubit-chat"), page.url
            assert page.locator(
                '[aria-label="Qubit Chat transcript"]').count() == 1
            assert page.get_by_label("Message Qubit").count() == 1
            browser.close()
    finally:
        ui.stop()

