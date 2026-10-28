"""Hermetic E2E coverage for the Qubit voice surface (2026-09-21 workstream).

Owner: voice E2E workstream. This file adds tests only; it never edits product
code. Product defects found while writing it are reported (not fixed) with
``file:line`` and pinned as explicit ``xfail`` where the backend is reachable.

What is covered, with NO microphone, GPU, provider, credential, or network:

1. **Right-Ctrl** — the browser control and typed ``/qubit-chat`` input share
   one canonical route table; the browser *local* engine's transcript
   (``POST /api/audio/transcribe``) enters the same canonical turn/store; there
   is no right-ctrl shadow route. The DOM press/release/debounce link is
   browser-only and is an explicit ``xfail`` naming the exact missing link.
2. **``voice.input_engine``** — ``browser``/``local``/``auto`` normalization and
   default, the served config + schema, that it never changes the canonical
   operation, and the typed degraded states the UI surfaces. The browser
   engine-selection / fallback-reason / permission-failure *rendering* is
   browser-only and is an explicit ``xfail``.
3. **STT/TTS adapter switching** — default sherpa/parakeet + piper through the
   single ``voicebridge.providers.speech`` registry; config-driven provider and
   engine swaps; the moonshine fallback; and the canonical operation staying
   unchanged across role swaps.
4. **Cancellation / e-stop / recovery** — an in-flight stream turn is cancelled
   with no fabricated assistant reply, then recovered by ``retry_last`` without
   duplicating the user message; a voice "cancel" denies pending confirmations;
   the durable e-stop latch refuses a turn and clears on reset.

The model transport is stubbed so the tests are hermetic; the real adaptive
transport/registry/store are exercised. Live Playwright links live in
``tests/test_voice_e2e_phase8.py`` (``-m "ui or live"``).
"""
from __future__ import annotations

import base64
import json
import re
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# shared fixtures / helpers (mirror the existing voice test patterns)
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _force_legacy_model_path(monkeypatch):
    """Keep these tests on the stubbed legacy model path.

    The product now prefers the vendored tool-capable ``AIAgent`` for general
    turns (DECISIONS #26). That agent bypasses the ``local_llm`` stubs below,
    so without this seam a hermetic test would contact the real local model.
    Returning ``None`` from the agent seam is the documented fallback: the
    turn continues on the same ``chat_transport`` model path this file pins.
    """
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "_agent_chat", lambda *a, **k: None)


def _stub_local(monkeypatch, reply="stub chat reply"):
    """Stub the local Brain4 non-streaming call (no Ollama, no network)."""
    import voicebridge.local_llm as _llm

    _force_legacy_model_path(monkeypatch)
    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None, cancel=None, should_stop=None):
        calls.append({"text": text, "model": model})
        return reply

    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local", _fake)
    return _fake


def _stub_local_stream(monkeypatch, reply="stub stream reply"):
    """Stub the local Brain4 streaming call (no Ollama, no network)."""
    import voicebridge.local_llm as _llm

    _force_legacy_model_path(monkeypatch)
    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain",
              timeout_s=4.0, max_tokens=100, context="", on_sentence=None,
              should_stop=None, num_ctx=None, reasoning=None):
        calls.append({"text": text, "model": model, "context": context})
        if reply and on_sentence:
            on_sentence(reply)
        return reply

    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local_stream", _fake)
    return _fake


def _parse_sse(frames):
    """Parse ``h_chat_stream``/``stream_sse`` frames into event dicts."""
    events = []
    for frame in frames:
        name = None
        data = None
        for line in str(frame).splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        payload = json.loads(data) if data else {}
        payload.setdefault("event", name)
        events.append(payload)
    return events


def _dummy_orch(cfg):
    """A config-only orchestrator (no audio graph, no model load).

    ``_make_asr`` / ``_make_tts`` / ``_transcribe_with_fallback`` are pure
    config -> adapter constructors, so ``__new__`` without ``__init__`` is
    enough and never touches hardware.
    """
    from voicebridge.orchestrator import VoiceOrchestrator

    o = VoiceOrchestrator.__new__(VoiceOrchestrator)
    o.cfg = cfg
    o._fallback_asr = None
    return o


def _wav_data_url(payload: bytes = b"RIFF....WAVEfmt ") -> str:
    return "data:audio/wav;base64," + base64.b64encode(payload).decode()


# ===========================================================================
# 1. Right-Ctrl capture / press-release / debounce -> canonical backend
# ===========================================================================
def test_right_ctrl_shares_the_canonical_chat_route_table(tmp_path, monkeypatch):
    """The Right-Ctrl/voice path has no shadow backend: typed and voice turns
    are the same canonical ``chat_transport`` handlers, and the only voice
    server op is the canonical local-ASR route."""
    from voicebridge import qubit_compat as HC

    typed, _ = HC.match("POST", "/api/qubit/chat")
    stream, _ = HC.match("POST", "/api/qubit/chat/stream")
    assert typed["handler"] == "h_chat_turn"
    assert stream["handler"] == "h_chat_stream"
    assert stream["disposition"] == "stream"

    asr, _ = HC.match("POST", "/api/audio/transcribe")
    assert asr["handler"] == "h_audio_transcribe"
    tts, _ = HC.match("POST", "/api/audio/speak")
    assert tts["handler"] == "h_audio_speak"

    patterns = [r["pattern"] for r in HC.ROUTES]
    assert not any("right" in p.lower() for p in patterns), patterns
    assert not any("voice/control" in p.lower() for p in patterns), patterns


def test_right_ctrl_local_engine_transcript_enters_canonical_turn(
        tmp_path, monkeypatch):
    """The browser *local* engine capture path converges on the canonical turn.

    Chain proven hermetically: the canonical local ASR
    (``POST /api/audio/transcribe``) returns a transcript, and feeding that
    transcript into the SAME streamed route as typed input
    (``h_chat_stream`` -> ``chat_transport.stream_turn``) persists the
    conversation to the one canonical store with no second backend.
    """
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge.asr.base import Transcript

    cfg = _cfg(tmp_path, monkeypatch)

    class _ASR:
        name = "sherpa"

        def load(self):
            pass

        def transcribe(self, path):
            return Transcript(text="tell me a short story",
                              provider="sherpa",
                              language="en", confidence=0.97,
                              duration_s=0.4)

    monkeypatch.setattr(HC, "_asr_from_cfg", lambda c: _ASR())

    code, out = HC.dispatch(cfg, "POST", "/api/audio/transcribe", {},
                            {"data_url": _wav_data_url()})
    assert code == 200 and out["ok"] is True, out
    assert out["transcript"] == "tell me a short story"
    assert out["provider"] == "sherpa"

    _stub_local_stream(monkeypatch, "ok")
    events = _parse_sse(list(HC.h_chat_stream(
        cfg, {}, {"text": out["transcript"], "session": "voice-local"}, {})))
    done = [e for e in events if e["event"] == "done"][0]
    assert done["receipt"]["path"] == "voicebridge.chat_transport.stream_turn"

    shown = CT.store(cfg).show("voice-local")
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
    assert shown["messages"][0]["content"] == "tell me a short story"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Browser-only link, not operator-blocked. Right-Ctrl press/release and "
        "its debounce (keydown/keyup on code 'ControlRight', the event.repeat "
        "guard, and the rightCtrlHeldRef latch) live in "
        "frontend/qubit-web/src/pages/ChatPage.tsx:998-1018; proving the DOM "
        "key event reaches the canonical turn requires Playwright plus the "
        "built frontend/qubit-dist bundle plus a browser SpeechRecognition / "
        "getUserMedia stub, none reachable from a hermetic Python test. The "
        "live link is tests/test_voice_e2e_phase8.py::"
        "test_right_ctrl_drives_canonical_chat_stream under `-m 'ui or live'`. "
        "No credential is required; the missing link is browser automation."
    ),
)
def test_right_ctrl_press_release_debounce_reaches_canonical_turn():
    assert False, "the browser DOM key event is not reachable hermetically"


# ===========================================================================
# 2. voice.input_engine browser | local | auto (selection, fallback, denial)
# ===========================================================================
def test_input_engine_values_and_default():
    from voicebridge.config import VOICE_INPUT_ENGINES, normalize_input_engine

    assert VOICE_INPUT_ENGINES == ("browser", "local", "terminal", "auto")
    for value in VOICE_INPUT_ENGINES:
        assert normalize_input_engine(value) == value
    # Unknown/missing/wrong-case fails closed to the documented default.
    for bad in (None, "", "BROWSER", "Local", "remote", "speech", 3, [], {}):
        assert normalize_input_engine(bad) == "auto", bad


def test_config_default_and_served_surface_expose_voice_input_engine(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    assert cfg.get("voice", "input_engine") == "auto"

    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200, live
    assert live["voice"]["input_engine"] == "auto", live.get("voice")

    code, schema = HC.dispatch(cfg, "GET", "/api/config/schema", {}, {})
    assert code == 200, schema
    field = schema["fields"]["voice.input_engine"]
    assert field["default"] == "auto"
    assert field["type"] == "string"


def test_input_engine_never_changes_the_canonical_operation(
        tmp_path, monkeypatch):
    """The setting affects speech input only — never the model/session/tools/
    policy/audit/e-stop path. The canonical turn receipt is identical for all
    three configured engines."""
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    receipts = {}
    for engine in ("browser", "local", "terminal", "auto"):
        cfg = _cfg(tmp_path / engine, monkeypatch,
                   f"voice:\n  input_engine: {engine}\n")
        assert cfg.get("voice", "input_engine") == engine
        _stub_local_stream(monkeypatch, f"{engine} reply")
        events = _parse_sse(list(HC.h_chat_stream(
            cfg, {}, {"text": "hello", "session": f"e-{engine}"}, {})))
        done = [e for e in events if e["event"] == "done"][0]
        receipts[engine] = done["receipt"]
        assert CT.store(cfg).show(f"e-{engine}")["messages"][-1]["content"] == \
            f"{engine} reply"

    assert {r["path"] for r in receipts.values()} == {
        "voicebridge.chat_transport.stream_turn"}
    assert {r["model"] for r in receipts.values()} == {"qubit-brain"}
    assert all(r["runtime"] == "qubit-native" for r in receipts.values())


def test_local_engine_asr_unavailable_is_typed_not_fabricated(
        tmp_path, monkeypatch):
    """The degraded local-STT state the UI surfaces is a typed gate, never a
    fabricated transcript."""
    from voicebridge import qubit_compat as HC
    from voicebridge.asr.base import ASRUnavailable

    cfg = _cfg(tmp_path, monkeypatch)

    class _Missing:
        def load(self):
            raise ASRUnavailable("parakeet model not installed")

    monkeypatch.setattr(HC, "_asr_from_cfg", lambda c: _Missing())
    code, out = HC.dispatch(cfg, "POST", "/api/audio/transcribe", {},
                            {"data_url": _wav_data_url()})
    assert code == 403 and out["ok"] is False, out
    assert out["code"] == "AUDIO_ASR_UNAVAILABLE"
    assert "transcript" not in out


def test_local_engine_tts_unavailable_is_typed_not_fabricated(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.tts.base import TTSUnavailable

    cfg = _cfg(tmp_path, monkeypatch)

    class _Missing:
        def load(self):
            raise TTSUnavailable("piper voice not installed")

    monkeypatch.setattr(HC, "_tts_from_cfg", lambda c: _Missing())
    code, out = HC.dispatch(cfg, "POST", "/api/audio/speak", {},
                            {"text": "hello"})
    assert code == 403 and out["ok"] is False, out
    assert out["code"] == "AUDIO_TTS_UNAVAILABLE"
    assert "data_url" not in out


def test_voice_config_served_read_has_no_credential_and_relay_mode(
        tmp_path, monkeypatch):
    """The STT/TTS profile read the UI consumes is host-side relay only and
    never exports a provider key or path."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/audio/voice-config", {}, {})
    assert code == 200 and out["ok"] is True, out
    assert out["schema"] == "qubit-runtime-audio/1"
    assert out["mode"] == "relay"
    assert out["stt"]["mode"] == "relay" and out["tts"]["mode"] == "relay"
    blob = json.dumps(out)
    for leaked in ("key_ref", "api_key", "OPENAI_API_KEY", "sk-"):
        assert leaked not in blob, leaked


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Browser-only link, not operator-blocked. The engine selector, the "
        "`network:`/`privacy:` consequence, the `voice-fallback` reason, and "
        "the `voice-status` mic/Right-Ctrl state render from "
        "frontend/qubit-web/src/pages/ChatPage.tsx (normalizeVoiceEngine:262, "
        "startVoice:876-895, effectiveEngine/privacy:1020-1059, "
        "voice-status:1141-1173). Proving them needs Playwright plus the built "
        "dist plus a SpeechRecognition stub. Live links: "
        "tests/test_voice_e2e_phase8.py::"
        "test_ui_engine_selection_reflects_normalized_config, "
        "test_auto_prefers_browser_speech_recognition, "
        "test_auto_falls_back_to_local_with_visible_reason."
    ),
)
def test_browser_engine_selection_and_fallback_reason_render_hermetically():
    assert False, "the rendered voice-status DOM is not reachable hermetically"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Browser-only link, not operator-blocked. A denied microphone renders "
        "the typed `[VOICE_DENIED]` alert from the SpeechRecognition onerror "
        "branch (frontend/qubit-web/src/pages/ChatPage.tsx:930-956) and the "
        "local getUserMedia catch (ChatPage.tsx:978-982); proving it needs "
        "Playwright + the built dist + a getUserMedia reject stub. Live link: "
        "tests/test_voice_e2e_phase8.py::"
        "test_microphone_permission_failure_renders_typed_error."
    ),
)
def test_microphone_permission_failure_renders_typed_alert_hermetically():
    assert False, "the browser permission failure is not reachable hermetically"


# ===========================================================================
# 3. STT / TTS adapter switching through the single speech registry
# ===========================================================================
def test_speech_registry_defaults_match_orchestrator_seams(
        tmp_path, monkeypatch):
    """The shipped defaults are the same in the canonical registry and in the
    orchestrator ASR/TTS seams: sherpa/parakeet STT, piper TTS."""
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _dummy_orch(cfg)

    stt = R.resolve_speech_in(cfg)
    assert stt.profile_id == S.DEFAULT_SPEECH_IDS[S.SPEECH_IN]
    assert stt.provider == "sherpa" and stt.settings["engine"] == "parakeet"
    asr = orch._make_asr()
    assert type(asr).__name__ == "SherpaASR" and asr.engine == "parakeet"

    out = R.resolve_speech_out(cfg)
    assert out.profile_id == S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT]
    assert out.provider == "piper" and out.voice == "en_US-amy-medium"
    tts = orch._make_tts()
    assert type(tts).__name__ == "PiperTTS" and tts.voice == "en_US-amy-medium"


def test_stt_provider_switch_config_drives_registry_and_seam(
        tmp_path, monkeypatch):
    """Switching to whisper moves both the registry and the seam; switching the
    sherpa engine to moonshine likewise."""
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg_w = _cfg(tmp_path / "whisper", monkeypatch,
                 "asr:\n  provider: whisper\n  model: small\n")
    assert R.resolve_speech_in(cfg_w).provider == "whisper"
    asr_w = _dummy_orch(cfg_w)._make_asr()
    assert type(asr_w).__name__ == "FasterWhisperASR"

    cfg_m = _cfg(tmp_path / "moonshine", monkeypatch,
                 "asr:\n  provider: sherpa\n  engine: moonshine\n")
    eff = R.resolve_speech_in(cfg_m)
    assert eff.provider == "sherpa" and eff.settings["engine"] == "moonshine"
    asr_m = _dummy_orch(cfg_m)._make_asr()
    assert type(asr_m).__name__ == "SherpaASR" and asr_m.engine == "moonshine"


def test_stt_fallback_engine_moonshine_on_empty_primary(
        tmp_path, monkeypatch):
    """When the primary engine returns empty text on a real capture, the
    configured fallback (default moonshine) is used exactly once."""
    from voicebridge.asr.base import Transcript
    import voicebridge.asr.sherpa as sherpa

    cfg = _cfg(tmp_path, monkeypatch,
               "asr:\n  engine: parakeet\n  fallback_engine: moonshine\n")
    orch = _dummy_orch(cfg)

    class _Primary:
        engine = "parakeet"

        def transcribe(self, path):
            return Transcript(text="   ", provider="sherpa")

    orch._asr = _Primary()

    class _FakeFallback:
        def __init__(self, **kw):
            self.engine = str(kw.get("engine"))

        def load(self):
            pass

        def transcribe(self, path):
            return Transcript(text="moonshine transcript", provider="sherpa",
                              confidence=1.0, duration_s=0.3)

    monkeypatch.setattr(sherpa, "SherpaASR", _FakeFallback)
    tr = orch._transcribe_with_fallback("/tmp/qubit-absent-capture.wav")
    assert tr.text == "moonshine transcript" and tr.empty is False
    assert orch._fallback_asr.engine == "moonshine"


def test_tts_provider_switch_default_and_optional_providers(
        tmp_path, monkeypatch):
    """piper is the default; kokoro/edge are selected when configured through
    the same seams the registry drives."""
    from voicebridge.providers import resolver as R

    cfg_p = _cfg(tmp_path / "piper", monkeypatch)
    assert type(_dummy_orch(cfg_p)._make_tts()).__name__ == "PiperTTS"
    assert R.resolve_speech_out(cfg_p).provider == "piper"

    cfg_k = _cfg(tmp_path / "kokoro", monkeypatch,
                 "tts:\n  provider: kokoro\n")
    assert R.resolve_speech_out(cfg_k).provider == "kokoro"
    assert type(_dummy_orch(cfg_k)._make_tts()).__name__ == "KokoroTTS"

    cfg_e = _cfg(tmp_path / "edge", monkeypatch, "tts:\n  provider: edge\n")
    assert R.resolve_speech_out(cfg_e).provider == "edge"
    assert type(_dummy_orch(cfg_e)._make_tts()).__name__ == "EdgeTTS"


def test_canonical_operation_unchanged_across_speech_role_swaps(
        tmp_path, monkeypatch):
    """Swapping STT/TTS adapters must not change the canonical turn: same
    transport path, same local model, same one session store, same e-stop gate.
    """
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    # role set A: shipped defaults
    cfg_a = _cfg(tmp_path / "a", monkeypatch)
    _stub_local_stream(monkeypatch, "a reply")
    ev_a = _parse_sse(list(HC.h_chat_stream(
        cfg_a, {}, {"text": "hi", "session": "swap-a"}, {})))
    done_a = [e for e in ev_a if e["event"] == "done"][0]
    # Snapshot the store while cfg_a's env is still active.
    shown_a = CT.store(cfg_a).show("swap-a")

    # role set B: whisper STT + kokoro TTS
    cfg_b = _cfg(tmp_path / "b", monkeypatch,
                 "asr:\n  provider: whisper\n  model: small\n"
                 "tts:\n  provider: kokoro\n")
    orch_a = _dummy_orch(cfg_a)
    orch_b = _dummy_orch(cfg_b)
    assert type(orch_a._make_asr()).__name__ != type(orch_b._make_asr()).__name__
    assert type(orch_a._make_tts()).__name__ != type(orch_b._make_tts()).__name__

    _stub_local_stream(monkeypatch, "b reply")
    ev_b = _parse_sse(list(HC.h_chat_stream(
        cfg_b, {}, {"text": "hi", "session": "swap-b"}, {})))
    done_b = [e for e in ev_b if e["event"] == "done"][0]
    shown_b = CT.store(cfg_b).show("swap-b")

    assert done_a["receipt"]["path"] == done_b["receipt"]["path"]
    assert done_a["receipt"]["model"] == done_b["receipt"]["model"]
    assert shown_a["messages"][-1]["content"] == "a reply"
    assert shown_b["messages"][-1]["content"] == "b reply"
    assert shown_a["state"] == "OK" and shown_b["state"] == "OK"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "PRODUCT GAP (reported, not operator-blocked): a session/explicit "
        "speech-profiled selection resolves in the registry but does not reach "
        "the orchestrator's ASR/TTS seams. voicebridge/orchestrator.py:82 "
        "(`_speech_override`) calls `resolve_speech(cfg, kind)` with no "
        "`session`/`profile_id`, and _make_asr/_make_tts "
        "(orchestrator.py:531,567) pass neither, so only the config-derived "
        "default can ever win. `voicebridge/providers/speech.py:571-593` "
        "(_pick_speech) is the only place a session selection is read. Until "
        "the seam is passed the active session, 'speech roles are replaceable "
        "through the single registry' holds for defaults only."
    ),
)
def test_session_selected_speech_profile_reaches_orchestrator_seam(
        tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    R.upsert_speech_profile(cfg, S.SpeechProfile(
        id="session-tts", kind=S.SPEECH_OUT, provider="piper",
        voice="en_US-lessac-medium", locality="local"), confirm=True)
    S.set_session_speech_profile(cfg, "s1", S.SPEECH_OUT, "session-tts",
                                 confirm=True)
    # The registry honors the session selection ...
    assert R.resolve_speech_out(cfg, session="s1").voice == "en_US-lessac-medium"
    # ... but the seam still builds the config default (this is the gap; the
    # expectation below is the desired behavior and is expected to fail).
    assert _dummy_orch(cfg)._make_tts().voice == "en_US-lessac-medium"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "PRODUCT BUG (reported, not operator-blocked): with `asr.provider: "
        "whisper`, the speech-in record's `model` is filled from "
        "`asr.engine` (default parakeet) — voicebridge/providers/speech.py:202,"
        "218 (`model=asr_engine`) — and _speech_override returns it, so "
        "voicebridge/orchestrator.py:552-554 builds "
        "FasterWhisperASR(model='parakeet') instead of the configured "
        "`asr.model` (e.g. 'small'). The whisper engine would then try to load "
        "a non-existent model. The registry/seam switch (provider) is correct; "
        "only the model name is wrong."
    ),
)
def test_whisper_configured_model_reaches_the_seam(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch,
               "asr:\n  provider: whisper\n  model: small\n")
    asr = _dummy_orch(cfg)._make_asr()
    assert asr.model_name == "small"


@pytest.mark.xfail(
    strict=False,
    reason=(
        "PRODUCT GAP (reported, not operator-blocked): the compat audio ops "
        "build their adapters from raw config, bypassing the canonical speech "
        "registry/override. voicebridge/qubit_compat.py:3910 (`_asr_from_cfg`) "
        "and :3940 (`_tts_from_cfg`) read `cfg.section('asr')`/"
        "`cfg.get('tts', ...)` only, unlike orchestrator.py:531/567 which call "
        "`_speech_override`. A registry-level speech profile therefore does not "
        "affect `POST /api/audio/transcribe` / `/api/audio/speak`."
    ),
)
def test_compat_audio_ops_honor_the_speech_registry_override(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    R.upsert_speech_profile(cfg, S.SpeechProfile(
        id="compat-tts", kind=S.SPEECH_OUT, provider="kokoro",
        voice="en_US-amy-medium", locality="local"), confirm=True)
    S.set_session_speech_profile(cfg, "s1", S.SPEECH_OUT, "compat-tts",
                                 confirm=True)
    assert R.resolve_speech_out(cfg, session="s1").provider == "kokoro"
    assert type(HC._tts_from_cfg(cfg)).__name__ == "KokoroTTS"


# ===========================================================================
# 4. Cancellation / e-stop / recovery on an in-flight voice turn
# ===========================================================================
def test_stream_cancel_marks_cancelled_and_recovery_does_not_duplicate(
        tmp_path, monkeypatch):
    """An in-flight streamed voice turn is cancelled with no fabricated
    assistant reply, and ``retry_last`` recovers it without duplicating the
    user message."""
    from voicebridge import chat_transport as CT
    import voicebridge.local_llm as _llm

    _force_legacy_model_path(monkeypatch)
    cfg = _cfg(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def _blocking(text, conversation_prompt="", model="qubit-brain",
                  timeout_s=4.0, max_tokens=100, context="", on_sentence=None,
                  should_stop=None, num_ctx=None, reasoning=None):
        started.set()
        release.wait(5)
        if on_sentence:
            on_sentence("late reply")
        return "late reply"

    monkeypatch.setattr(_llm, "chat_local_stream", _blocking)
    events = []

    def _run():
        events.extend(CT.stream_turn(cfg, "long voice task", session="vcxl"))

    t = threading.Thread(target=_run)
    t.start()
    assert started.wait(5), "model call never started"

    out = CT.cancel("vcxl")
    assert out["ok"] is True and out["cancelled"] is True, out
    release.set()
    t.join(5)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "start" and "cancelled" in kinds, kinds
    assert "done" not in kinds, kinds
    # Cancellation persisted NO assistant reply (recovery precondition).
    before = CT.store(cfg).show("vcxl")["messages"]
    assert [m["role"] for m in before] == ["user"]

    # Recovery: retry re-runs the unanswered turn without duplicating the user.
    _stub_local(monkeypatch, "recovered reply")
    retried = CT.retry_last(cfg, "vcxl")
    assert retried["ok"] is True and retried["reply"] == "recovered reply"
    after = CT.store(cfg).show("vcxl")["messages"]
    assert [m["role"] for m in after] == ["user", "assistant"]
    assert [m["content"] for m in after] == ["long voice task",
                                             "recovered reply"]


def test_voice_cancel_utterance_denies_pending_confirmation(
        tmp_path, monkeypatch):
    """A spoken "cancel" clears the orchestrator's pending confirmation and
    denies any pending voice-turn confirmation through the shared journal."""
    from types import SimpleNamespace

    from voicebridge import orchestrator as OR
    from voicebridge.intent.state import ConfirmationState

    cfg = _cfg(tmp_path, monkeypatch)
    orch = SimpleNamespace(
        cfg=cfg,
        spoken=[],
        confirmations=ConfirmationState(),
        task=SimpleNamespace(active=False),
        playback=SimpleNamespace(stop=lambda: None),
    )
    orch.speak = orch.spoken.append

    orch.confirmations.request("launch the task", {"kind": "task"}, "pending")
    assert orch.confirmations.pending is True

    OR.VoiceOrchestrator._handle_cancel_utterance(orch)

    assert orch.confirmations.pending is False
    assert "Cancelled." in orch.spoken


def test_estop_latch_refuses_voice_turn_and_recovers_after_reset(
        tmp_path, monkeypatch):
    """The durable emergency stop refuses an in-flight voice turn (no model
    call), and a reset restores a working canonical turn."""
    from voicebridge import chat_transport as CT
    from voicebridge import power as PW

    cfg = _cfg(tmp_path, monkeypatch)
    stub = _stub_local(monkeypatch, "after reset")
    ctl = PW.power_control(cfg)
    try:
        res = ctl.emergency_stop()
        assert res["state"] == "OK", res

        code, refusal, _model = CT.stream_preflight(cfg, "hello", None,
                                                    session="es")
        assert code == 403 and refusal["code"] == "EMERGENCY_STOP", refusal
        denied = CT.turn(cfg, "hello", session="es")
        assert denied["ok"] is False and denied["code"] == "EMERGENCY_STOP"
        # Fail closed before the model: nothing was invoked.
        assert stub.calls == []
    finally:
        ctl.reset()

    from voicebridge.broker import estop_engaged
    assert estop_engaged(cfg) is False
    ok = CT.turn(cfg, "hello", session="es")
    assert ok["ok"] is True and ok["reply"] == "after reset"
    assert stub.calls and "hello" in stub.calls[0]["text"]
