"""Lane 4 (2026-10-03) — spoken legacy-brand substitution (9u).

Spoken voice IS a product surface (DECISIONS #7): the brain stitches legacy
self-reference tokens from memory/skill prose, so the helper's speak path
must substitute them before TTS. Preview/History rows keep verbatim for
audit honesty (asserted here). Pure-function tests + one _speak wiring test.
No mic, socket, model, or service.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_journal_sentence_is_cleaned():
    from voicebridge.desktop_helper import speak_brand_text
    out = speak_brand_text("I'll use Hermes agent instead of OpenClaw.")
    assert "Hermes" not in out and "OpenClaw" not in out, out
    assert out == "I'll use Qubit instead of Qubit."


def test_bare_hermes_and_openclaw_cleaned():
    from voicebridge.desktop_helper import speak_brand_text
    assert speak_brand_text("Hermes CLI is ready.") == "Qubit CLI is ready."
    assert speak_brand_text("Ask OpenClaw for that.") == "Ask Qubit for that."


def test_nous_variants_cleaned():
    from voicebridge.desktop_helper import speak_brand_text
    assert "Nous" not in speak_brand_text("Built by NousResearch.")
    assert "Nous" not in speak_brand_text("Built by Nous Research.")


def test_case_insensitive_forms_cleaned():
    from voicebridge.desktop_helper import speak_brand_text
    out = speak_brand_text("the hermes agent and openclaw bridge")
    assert "ermes" not in out.lower().replace("qubit", ""), out
    assert "openclaw" not in out.lower(), out


def test_clean_sentences_byte_identical():
    from voicebridge.desktop_helper import speak_brand_text
    for s in (
        "On it — working on that in chat.",
        "Timer set for one minute. Say yes to run it, or no to cancel.",
        "Bucharest: 14 degrees, clear sky.",
        "https://github.com/cgraph-dev/qubit-voicebridge",
    ):
        assert speak_brand_text(s) == s, s


def test_speak_sends_clean_but_history_keeps_verbatim(monkeypatch):
    """_speak() TTS payload is substituted; History row keeps verbatim."""
    from voicebridge import desktop_helper as DH

    spoken_payloads: list = []
    history_lines: list = []

    class FakeCfg:
        def __init__(self, data):
            self._data = data

        def get(self, *path, default=None):
            node = self._data
            for p in path:
                if isinstance(node, dict) and p in node:
                    node = node[p]
                else:
                    return default
            return node

    class FakeClient:
        def speak(self, text):
            spoken_payloads.append(text)
            return b"RIFF" + b"\x00" * 44

    class FakeOverlay:
        def set_status(self, state, detail=""):
            pass

        def show_preview(self, payload):
            pass

        def show_error(self, code, detail):
            pass

        def append_history(self, line):
            history_lines.append(line)

    class FakeDesktop:
        def session(self):
            return {"session_type": "x11"}

    cfg = FakeCfg({"voice_edit": {"enabled": True, "speak_answers": True}})
    helper = DH.DesktopHelper(cfg, client=FakeClient(),
                              overlay=FakeOverlay(),
                              desktop=FakeDesktop(),
                              mic_factory=lambda: None)
    # Fake playback: no audio, no process (mirrors test_voice_edit_bargein9u).
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/paplay")

    class FakeProc:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: FakeProc())
    helper._history("answer", "q", "I'll use Hermes agent instead of OpenClaw.")
    helper._speak("I'll use Hermes agent instead of OpenClaw.")
    helper._speaking = False  # playback thread may still hold the flag
    assert spoken_payloads, "expected a TTS call"
    assert "Hermes" not in spoken_payloads[0]
    assert "OpenClaw" not in spoken_payloads[0]
    assert any("Hermes agent instead of OpenClaw" in line
               for line in history_lines), history_lines


def test_panel_preview_text_untouched_by_substitution():
    """Apply/Copy path never routes through speak_brand_text (verbatim)."""
    from voicebridge.desktop_helper import speak_brand_text
    import inspect
    from voicebridge import desktop_helper as DH
    src = inspect.getsource(DH.DesktopHelper.on_overlay_action)
    assert "speak_brand_text" not in src
    assert speak_brand_text("") == ""
