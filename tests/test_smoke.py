"""Packet 7B smoke coverage: startup, dashboard, task, cancellation, voice.

Hermetic: no live bridge, no Ollama, no audio hardware required. Dashboard and
voice paths are exercised through the WebUI with a fake orchestrator (same
pattern as tests/test_webui.py), cancellation through the typed RunCoordinator,
and voice through the piper WAV boundary when a voice is present (otherwise a
typed DEGRADED skip, never a silent pass).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Fake orchestrator (kept in this module so smoke is self-contained)
# ---------------------------------------------------------------------------
class _FakeTTs:
    name = "fake"

    def synthesize(self, text: str, out_path: Path) -> Path:
        Path(out_path).write_bytes(b"RIFFfakewav")
        return Path(out_path)


class _FakeTask:
    active = False
    run_id = None


class _FakeOrch:
    """Minimal orchestrator-shaped object for the webui handler."""

    def __init__(self) -> None:
        self.task = _FakeTask()
        self._route = "conversation"
        self._t0 = time.time()
        self._web_active = 0
        self.cfg = type("C", (), {
            "get": lambda self, *p, **kw: kw.get("default"),
            "section": lambda self, n: {},
            "model_dir": lambda self: Path("/tmp"),
            "state_dir": lambda self: Path("/tmp"),
        })()
        self._tts = _FakeTTs()
        self._last_web_summary = ""
        self._last_web_route = ""
        self._web_mode = False
        self._web_spoken = []

    def process_web_utterance(self, text: str) -> dict:
        return {"text": text, "route": "conversation",
                "summary": "smoke summary", "reply": "smoke summary",
                "confidence": 0.9}

    def _needs_answer(self) -> bool:
        return False


def _make_ui(tmp_path) -> tuple:
    from voicebridge.webui import WebUI
    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    ui.start()
    return ui, orch


# ---------------------------------------------------------------------------
# Startup smoke
# ---------------------------------------------------------------------------
@pytest.mark.smoke
def test_startup_webui_binds_loopback(tmp_path):
    ui, _ = _make_ui(tmp_path)
    try:
        assert ui.host == "127.0.0.1"
        assert ui._server is not None and ui._server.server_port > 0
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Dashboard smoke
# ---------------------------------------------------------------------------
@pytest.mark.smoke
def test_dashboard_status_and_activity(tmp_path):
    ui, _ = _make_ui(tmp_path)
    port = ui._server.server_port
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",
                                    timeout=5) as r:
            status = json.loads(r.read().decode())
        assert status["bridge"] == "up"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/activity?limit=5",
                                    timeout=5) as r:
            body = json.loads(r.read().decode())
        assert "items" in body
    finally:
        ui.stop()


@pytest.mark.smoke
def test_dashboard_root_serves_html(tmp_path):
    ui, _ = _make_ui(tmp_path)
    port = ui._server.server_port
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            html = r.read().decode()
        assert "<html" in html.lower() or "doctype" in html.lower()
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Task smoke (typed task through the webui path)
# ---------------------------------------------------------------------------
@pytest.mark.smoke
def test_task_path_submits_and_completes(tmp_path):
    ui, orch = _make_ui(tmp_path)
    try:
        rid = ui.submit_text("smoke task: check the flow")
        result = None
        for _ in range(50):
            r = ui.result(rid)
            if r["status"] == "done":
                result = r
                break
            time.sleep(0.05)
        assert result is not None
        assert result["text"].startswith("smoke task")
        assert result["route"] == "conversation"
        assert result["summary"]
        assert result["audio_b64"] is None or isinstance(result["audio_b64"], str)
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Cancellation smoke (typed event stream: one terminal event)
# ---------------------------------------------------------------------------
@pytest.mark.smoke
def test_cancellation_emits_single_terminal_event(tmp_path):
    ui, _ = _make_ui(tmp_path)
    try:
        assert ui.runs is not None, "typed event stream must be active"
        hub = ui._events_hub
        run_id = "smoke-run-1"
        sub = hub.subscribe(run_id=run_id)
        try:
            ui.typed_task_started("smoke", run_id, description="smoke")
            ui.typed_task_cancelled("smoke", run_id, reason="user")
            drained = sub.drain(timeout=1.0)
            cancelled = [e for e in drained
                         if str(e.get("type")) == "RUN_CANCELLED"]
            assert len(cancelled) == 1, \
                f"cancellation must settle once: {drained}"
            # second cancel is idempotent (restart-safe duplicate)
            ui.runs.cancel(run_id, reason="user")
            drained2 = sub.drain(timeout=0.3)
            cancelled2 = [e for e in drained2
                          if str(e.get("type")) == "RUN_CANCELLED"]
            assert len(cancelled2) == 0, \
                f"duplicate cancel must not emit a second terminal: {drained2}"
        finally:
            hub.unsubscribe(sub)
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# Voice smoke (piper WAV boundary; DEGRADED skip when voice absent)
# ---------------------------------------------------------------------------
def _voice_available() -> bool:
    try:
        from voicebridge.config import Config
        from voicebridge.tts.piper import PiperTTS
        cfg = Config.load()
        return PiperTTS(model_dir=cfg.model_dir(),
                        voice=cfg.get("tts", "voice", default="en_US-amy-medium")).available()
    except Exception:
        return False


@pytest.mark.smoke
@pytest.mark.live
@pytest.mark.skipif(not _voice_available(),
                    reason="DEGRADED: piper voice not installed; voice smoke not run")
def test_voice_piper_wav_boundary(tmp_path):
    """Real piper -> valid WAV at the playback boundary (no audio device)."""
    import wave
    from voicebridge.config import Config
    from voicebridge.tts.piper import PiperTTS
    cfg = Config.load()
    tts = PiperTTS(model_dir=cfg.model_dir(),
                   voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
    tts.load()
    out = tmp_path / "smoke-voice.wav"
    tts.synthesize("smoke voice check", out)
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1, "mono WAV required at playback boundary"
        assert wf.getsampwidth() == 2, "16-bit PCM WAV required"
        assert wf.getframerate() == tts.sample_rate
        assert wf.getnframes() > 0