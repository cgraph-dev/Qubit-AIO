"""Tests for the voice dashboard (webui) + summarize-first (local-script)."""
from __future__ import annotations

import array
import math
import struct
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.task_templates import TaskTemplates  # noqa: E402
from voicebridge.webui import (  # noqa: E402
    PCM_RATE, normalize_int16_bounded, parse_audio_rate, pcm_rms,
    pcm_to_wav, resample_pcm_int16,
)


def _tone(seconds: float = 1.0, rate: int = 16000, freq: float = 440.0,
          amp: float = 0.2) -> bytes:
    """A synthetic 'real speech' int16 mono PCM tone (RMS well above silence)."""
    a = array.array("h")
    n = int(rate * seconds)
    for i in range(n):
        v = int(32767 * amp * math.sin(2 * math.pi * freq * i / rate))
        a.append(max(-32768, min(32767, v)))
    return a.tobytes()


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
    # REAL speech-shaped audio (a tone), never silence: the new energy gate
    # rejects near-silence before ASR.
    pcm = _tone(1.0)
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


def test_webui_rejects_silence_without_false_asr(tmp_path: Path) -> None:
    """Pure silence must produce a typed NO_SPEECH result, never a
    hallucinated ASR text such as 'thank you'."""
    from voicebridge.webui import WebUI
    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    called = []

    class _BombASR:
        def load(self):
            pass

        def transcribe(self, wav_path: Path):
            called.append(True)   # must never be reached
            raise AssertionError("ASR must not run on silence")

    ui._asr_factory = lambda: _BombASR()
    pcm = b"\x00\x00" * PCM_RATE   # 1s of pure digital silence
    rid = ui.submit_talk(pcm)
    result = None
    for _ in range(50):
        r = ui.result(rid)
        if r["status"] == "done":
            result = r
            break
        time.sleep(0.02)
    assert result is not None
    assert result["reason"] == "NO_SPEECH"
    assert result["text"] == ""
    assert result["audio_b64"] is None
    assert "no speech" in result["summary"].lower()
    assert not called, "ASR must not be invoked on silence"


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
    assert result["reason"] in ("SHORT", "NO_SPEECH")


def test_webui_short_audio_typed(tmp_path: Path) -> None:
    from voicebridge.webui import WebUI
    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    called = []

    class _BombASR:
        def load(self):
            pass

        def transcribe(self, wav_path: Path):
            called.append(True)
            raise AssertionError("ASR must not run on a short clip")

    ui._asr_factory = lambda: _BombASR()
    res = ui.handle_talk(_tone(0.03))   # ~30ms < 100ms floor
    assert res["reason"] == "SHORT"
    assert res["audio_b64"] is None
    assert not called


def test_webui_resamples_48k_to_16k_before_asr(tmp_path: Path) -> None:
    """Chrome often delivers 48 kHz; the backend must resample to the 16 kHz
    ASR contract instead of misreading the samples."""
    from voicebridge.webui import WebUI
    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)

    class _CaptureASR:
        def __init__(self):
            self.rate = None
            self.data_bytes = None

        def load(self):
            pass

        def transcribe(self, wav_path: Path):
            raw = Path(wav_path).read_bytes()
            self.rate = struct.unpack("<I", raw[24:28])[0]
            self.data_bytes = struct.unpack("<I", raw[40:44])[0]
            from voicebridge.asr.base import Transcript
            return Transcript(text="hello there", confidence=0.95)

    asr = _CaptureASR()
    ui._asr_factory = lambda: asr
    pcm48 = _tone(1.0, rate=48000)   # 1s @48k
    res = ui.handle_talk(pcm48, rate=48000)
    assert res["text"] == "hello there"
    assert asr.rate == PCM_RATE == 16000
    # 1s of 48k -> 1s of 16k int16 = 32000 bytes
    assert asr.data_bytes == PCM_RATE * 2


def test_parse_audio_rate_bounds():
    assert parse_audio_rate("48000") == 48000
    assert parse_audio_rate("44100") == 44100
    assert parse_audio_rate("16000") == 16000
    assert parse_audio_rate("abc") == PCM_RATE
    assert parse_audio_rate("0") == PCM_RATE
    assert parse_audio_rate(96000) == PCM_RATE
    assert parse_audio_rate(None) == PCM_RATE


def test_pcm_energy_helpers():
    assert pcm_rms(b"\x00\x00" * 100) == 0.0
    assert pcm_rms(b"") == 0.0
    tone = _tone(0.2, amp=0.05)
    assert pcm_rms(tone) > 0.01
    # resample 48k->16k halves the sample count 3:1
    r48 = resample_pcm_int16(tone, 48000)
    assert len(r48) == (len(tone) // 2 // 3) * 2
    # resample identity for the same rate
    assert resample_pcm_int16(tone, 16000) == tone


def test_normalize_bounded_never_amplifies_silence():
    silence = b"\x00\x00" * 100
    assert normalize_int16_bounded(silence) == silence
    quiet = _tone(0.3, amp=0.005)
    norm = normalize_int16_bounded(quiet)
    assert norm != quiet           # genuine speech is boosted
    assert pcm_rms(norm) > pcm_rms(quiet)
    # the boost is bounded (never amplifies near-silence into a false hit)
    assert pcm_rms(norm) < 1.0


def _frontend_index_html() -> str:
    return (Path(__file__).resolve().parents[1] / "frontend" / "index.html") \
        .read_text(errors="replace")


RATE_AWARE_NEEDLES = (
    "ctx.sampleRate",              # 1. store the REAL AudioContext rate
    "X-Audio-Rate",                # 2. send it as the rate header
    "sendAudio(out.buffer, sampleRate)",  # 3. pass the rate through
    "vbApi('/api/talk'",           # 4. keep the authenticated helper
)


def test_canonical_frontend_index_html_is_rate_aware():
    """The canonical dashboard (frontend/index.html, not the embedded page)
    must carry the same rate-aware contract the backend implements."""
    html = _frontend_index_html()
    for needle in RATE_AWARE_NEEDLES:
        assert needle in html, f"frontend/index.html missing {needle!r}"
    assert "sampleRate: 16000" in html      # still asks for 16 kHz
    assert "releaseMic" in html             # mic + AudioContext always released
    assert "getUserMedia" in html


def test_served_dashboard_http_content_is_rate_aware(tmp_path: Path) -> None:
    """Verify the SERVED / page (over HTTP, not only the source file) contains
    the rate-aware audio contract."""
    import urllib.request
    from voicebridge.webui import WebUI
    orch = _FakeOrch()
    ui = WebUI(orch, host="127.0.0.1", port=0, state_dir=tmp_path)
    ui.start()
    try:
        port = ui._server.server_address[1]
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=10).read().decode()
    finally:
        ui.stop()
    assert "<!doctype html" in html.lower()
    for needle in RATE_AWARE_NEEDLES:
        assert needle in html, f"served / page missing {needle!r}"


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
