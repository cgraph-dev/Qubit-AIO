"""Tests for the voice dashboard (webui) + summarize-first (local-script)."""
from __future__ import annotations

import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.task_templates import TaskTemplates  # noqa: E402
from voicebridge.webui import pcm_to_wav  # noqa: E402


def _tmpl_yaml(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "templates.yaml"
    p.write_text(f"""templates:
  - name: search-web
    match:
      - "search the web for (.*)"
    kind: local-script
    ack: "Searching the web."
    summary_words: 20
    script: "echo RESULT: {{question}} is 42"
{extra}""")
    return p


def test_local_script_template_parses(tmp_path: Path) -> None:
    tt = TaskTemplates.load(path=_tmpl_tmpl_clean(tmp_path))
    m = tt.match("search the web for the answer")
    assert m is not None
    assert m.kind == "local-script"
    assert "{question}" in m.script
    assert m.summary_words == 20


def _tmpl_tmpl_clean(tmp_path: Path) -> Path:
    return _tmpl_yaml(tmp_path)


def test_pcm_to_wav_roundtrip() -> None:
    pcm = b"\x00\x00" * 16000  # 1s of silence @16k
    wav = pcm_to_wav(pcm)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    import struct
    rate = struct.unpack("<I", wav[24:28])[0]
    assert rate == 16000
    # data chunk carries the same PCM
    assert wav[-len(pcm):] == pcm


class _FakeTTs:
    name = "fake"

    def synthesize(self, text: str, out_path: Path) -> Path:
        Path(out_path).write_bytes(b"RIFFfakewav")
        return Path(out_path)


class _FakeOrch:
    """Minimal orchestrator-shaped object for the webui handler."""

    def __init__(self) -> None:
        self.task = type("T", (), {"active": False})()
        self._route = "gateway"
        self._t0 = time.time()
        self.cfg = type("C", (), {
            # speak_web_locally False -> bridge returns audio_b64 to the browser
            "get": lambda self, *p, **kw: False if p and p[-1] == "speak_web_locally" else kw.get("default"),
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
                "summary": "short summary", "reply": "short summary",
                "confidence": 0.9}


def test_webui_talk_returns_audio(tmp_path: Path) -> None:
    from voicebridge.webui import WebUI
    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    # stub ASR: return a fixed transcript
    class _FakeASR:
        def load(self):
            pass

        def transcribe(self, wav_path: Path):
            from voicebridge.asr.base import Transcript
            return Transcript(text="hello there", confidence=0.95)
    ui._asr_factory = lambda: _FakeASR()
    pcm = b"\x00\x00" * 16000
    # async: submit -> poll until done
    rid = ui.submit_talk(pcm)
    assert rid
    result = None
    for _ in range(50):
        r = ui.result(rid)
        if r["status"] == "done":
            result = r
            break
        time.sleep(0.05)
    assert result is not None
    assert result["text"] == "hello there"
    assert result["route"] == "conversation"
    assert result["summary"] == "short summary"
    assert result["audio_b64"] is not None  # TTS produced audio
    assert result["latency_s"] >= 0


def test_webui_rejects_empty_audio(tmp_path: Path) -> None:
    from voicebridge.webui import WebUI
    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    rid = ui.submit_talk(b"")
    assert rid
    result = None
    for _ in range(50):
        r = ui.result(rid)
        if r["status"] == "done":
            result = r
            break
        time.sleep(0.02)
    assert result is not None and result["audio_b64"] is None


def test_local_script_dispatch_runs_script(tmp_path: Path) -> None:
    """_dispatch_local_script runs the command and stores the summary."""
    import voicebridge.orchestrator as orch_mod  # noqa: F401  (import smoke)

    tt = TaskTemplates.load(path=_tmpl_yaml(tmp_path))
    tmpl = tt.match("search the web for the answer")
    assert tmpl is not None

    # drive through a minimal fake orchestrator subclass
    class _O:
        def __init__(self):
            self._last_web_text = "search the web for the answer"
            self._last_web_summary = ""
            self._last_web_route = ""
            self.cfg = type("C", (), {
                "get": lambda self, *p, **kw: kw.get("default"),
                "section": lambda self, n: {"max_words": 20},
            })()
            self._webui = None
            self.spoken = []

        def speak(self, text, **kw):
            self.spoken.append(text)

        def feed_push(self, item):
            pass

    o = _O()
    # monkeypatch the summarizer (the method imports it from .summarizer
    # internally, so patch the module it imports from)
    import voicebridge.summarizer as sum_mod
    orig = sum_mod.summarize
    sum_mod.summarize = lambda *a, **kw: "the answer is forty two"
    from voicebridge.task_templates import TemplateMatch
    tm = TemplateMatch(name="search-web", kind="local-script",
                       task="", ack="", script="echo RESULT: {question} is 42",
                       summary_words=20)
    # bind the real method
    import voicebridge.orchestrator as orch_mod2
    bound = orch_mod2.VoiceOrchestrator._dispatch_local_script.__get__(o, type(o))
    try:
        bound(tm)
    finally:
        sum_mod.summarize = orig
    assert o.spoken, "should have spoken a summary"
    assert "forty two" in o.spoken[0]
