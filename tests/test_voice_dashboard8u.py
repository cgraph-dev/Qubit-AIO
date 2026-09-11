"""Dashboard voice control for Qubit Chat (Hermes-first architecture).

The qubit-chat overlay owns the dashboard voice surface: a Voice button
drives browser SpeechRecognition and sends the transcript through the SAME
POST /api/qubit/chat backend turn as typed input, CLI text, and voice PTT
(voicebridge.agent.turn). Voice executes backend turns, never describes
them; every failure mode is a typed state (VOICE_UNSUPPORTED / VOICE_DENIED
/ VOICE_NO_SPEECH / VOICE_TIMEOUT), never silence or a hang.
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


def test_voice_unsupported_is_typed(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.add_init_script(
                "delete window.SpeechRecognition; "
                "delete window.webkitSpeechRecognition;")
            page.goto(f"http://127.0.0.1:{port}/hermes/qubit-chat",
                      timeout=30000)
            page.wait_for_timeout(2000)
            assert page.get_by_role("button", name="Voice input").count() == 1
            page.get_by_role("button", name="Voice input").click()
            page.wait_for_timeout(800)
            assert "VOICE_UNSUPPORTED" in page.inner_text("body")
            browser.close()
    finally:
        ui.stop()


def test_voice_listening_can_stop_without_hang(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/hermes/qubit-chat",
                      timeout=30000)
            page.wait_for_timeout(2000)
            page.get_by_role("button", name="Voice input").click()
            page.wait_for_timeout(800)
            # Either unsupported (no SR) or listening (SR present) — both
            # typed, and listening must offer a working stop.
            if page.get_by_role("button", name="Stop voice input").count():
                page.get_by_role("button", name="Stop voice input").click()
                page.wait_for_timeout(800)
                assert page.get_by_role("button",
                                        name="Voice input").count() == 1
            else:
                assert "VOICE_UNSUPPORTED" in page.inner_text("body")
            browser.close()
    finally:
        ui.stop()


def test_voice_transcript_executes_backend_turn(tmp_path):
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.add_init_script("""
              class FakeSR {
                constructor(){ this.interimResults=false; }
                start(){
                  const self=this;
                  setTimeout(()=>{
                    if(self.onresult) self.onresult({resultIndex:0, results:[
                      {isFinal:true, 0:{transcript:'hello qubit'}}]});
                    setTimeout(()=>{ if(self.onend) self.onend(); },50);
                  },100);
                }
                stop(){}
                abort(){}
              }
              window.SpeechRecognition = FakeSR;
            """)
            page.goto(f"http://127.0.0.1:{port}/hermes/qubit-chat",
                      timeout=30000)
            page.wait_for_timeout(2000)
            page.get_by_role("button", name="Voice input").click()
            page.wait_for_timeout(12000)
            body = page.inner_text("body")
            assert "You:" in body and "hello qubit" in body, body[-600:]
            assert ("Qubit:" in body or "MODEL_" in body or "AGENT_" in body
                    or "EMERGENCY" in body or "unavailable" in body.lower()), \
                body[-500:]
            browser.close()
    finally:
        ui.stop()
