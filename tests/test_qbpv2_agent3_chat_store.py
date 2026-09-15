"""Agent 3 (repair) — server-backed Chat session sidebar + transcript.

Boots the real Qubit WebUI on the built dashboard with a seeded Qubit
chat-session store (the same ``voicebridge.chat_transport`` module the
production turn/SSE/CLI/voice use), then drives ``/chat`` headless:
- the sidebar lists ``GET /api/qubit/chat/sessions`` (the chat store), never
  the receipts ``GET /api/sessions`` store;
- selecting a row loads its persisted transcript from
  ``GET /api/qubit/chat/sessions/<id>/messages`` and renders it;
- a full page reload restores both from the server (no localStorage needed).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "hermes-dist"
pytestmark = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")

pw = pytest.importorskip("playwright.sync_api")

SEED_ID = "agent3-seed"
SEED_USER = "seeded user question"
SEED_REPLY = "seeded assistant answer"


def _start(tmp_path, seed=True):
    from voicebridge import chat_transport as CT
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)
    if seed:
        st = CT.store(cfg)
        st.create("Agent3 Seed", session_id=SEED_ID)
        st.append(SEED_ID, "user", SEED_USER)
        st.append(SEED_ID, "assistant", SEED_REPLY)

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


def test_chat_sidebar_uses_the_chat_store_and_loads_the_transcript(tmp_path):
    ui, port = _start(tmp_path)
    api_calls: list[str] = []
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            def _record(request):
                parsed = urlparse(request.url)
                if parsed.path.startswith("/agent/api/"):
                    api_calls.append(parsed.path + (
                        f"?{parsed.query}" if parsed.query else ""))

            page.on("request", _record)
            page.goto(f"http://127.0.0.1:{port}/chat?resume={SEED_ID}",
                      timeout=30000)
            page.wait_for_timeout(3000)
            body = page.inner_text("body")

            # Sidebar lists the seeded chat-store session.
            assert "Agent3 Seed" in body, body[-600:]
            # Transcript loaded from the server store.
            assert SEED_USER in body, body[-600:]
            assert SEED_REPLY in body, body[-600:]
            browser.close()
    finally:
        ui.stop()

    assert any(u.startswith("/agent/api/qubit/chat/sessions?") for u in api_calls), \
        api_calls
    assert any(f"/agent/api/qubit/chat/sessions/{SEED_ID}/messages" in u
               for u in api_calls), api_calls
    # The receipts store must not drive the Chat surface.
    assert not any(u.startswith("/agent/api/sessions?") for u in api_calls), \
        [u for u in api_calls if u.startswith("/agent/api/sessions")]


def test_chat_transcript_survives_a_full_reload(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/chat?resume={SEED_ID}",
                      timeout=30000)
            page.wait_for_selector("text=seeded assistant answer", timeout=30000)
            page.reload(timeout=30000)
            page.wait_for_selector("text=seeded assistant answer", timeout=30000)
            body = page.inner_text("body")
            assert SEED_USER in body
            assert SEED_REPLY in body
            browser.close()
    finally:
        ui.stop()
