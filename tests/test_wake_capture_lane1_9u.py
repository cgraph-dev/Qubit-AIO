"""Lane-1 capture-path tests: cap-truncation observability + invariants.

Hermetic: no X, mic, socket, or model. The wake loop's 15 s segment cap cuts
the TAIL of a long utterance; the route word ("type this ...") sits at the
head, so routing must survive cap-length audio — and the journal must say
the segment was capped so future tail-loss is measurable, not silent.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.voice_edit.wake as wake_mod
from voicebridge.voice_edit.wake import WakeMonitor


class FakeCfg:
    def __init__(self, wake=None):
        self._d = {"voice_edit": {"beep": False,
                                   "wake": wake or {"enabled": True}}}

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


class TraceRecorder:
    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr(wake_mod, "_trace", self._rec)

    def _rec(self, stage, **fields):
        self.calls.append((stage, fields))

    def fields(self, stage):
        return [f for s, f in self.calls if s == stage]


def _monitor(replies, monkeypatch):
    commands = []
    it = iter(replies)
    m = WakeMonitor(FakeCfg(), on_command=commands.append,
                    is_busy=lambda: False,
                    transcribe=lambda pcm: next(it, ""))
    return m, commands


def test_capped_segment_is_traced_and_still_routes(monkeypatch):
    """A cap-length segment must flag capped=True AND route the head command."""
    rec = TraceRecorder(monkeypatch)
    # 484000 B ~= 15.1 s: past the default 15.0 s cap (15*16000*2 = 480000).
    m, commands = _monitor(
        ["hey qubit, type this for me: the route word survives the cut"],
        monkeypatch)
    m._handle_segment(b"\x00" * 484000)
    assert commands == ["type this for me: the route word survives the cut"]
    segs = rec.fields("segment")
    assert len(segs) == 1 and segs[0]["capped"] is True


def test_slow_speech_not_capped_under_new_cap(monkeypatch):
    """The 2026-10-04 owner case: 7.8 s of slow speech was cut mid-word
    by the old 8 s cap; under the 15 s cap it is NOT capped and routes."""
    rec = TraceRecorder(monkeypatch)
    m, commands = _monitor(
        ["hey qubit, type this for me: quarterly results for Q3 driving"],
        monkeypatch)
    m._handle_segment(b"\x00" * 249600)  # the exact journal byte count
    assert commands == ["type this for me: quarterly results for Q3 driving"]
    segs = rec.fields("segment")
    assert len(segs) == 1 and segs[0]["capped"] is False


def test_short_segment_is_not_capped(monkeypatch):
    rec = TraceRecorder(monkeypatch)
    m, commands = _monitor(
        ["hey qubit, what time is it in bucharest"], monkeypatch)
    m._handle_segment(b"\x00" * 9600)
    assert commands == ["what time is it in bucharest"]
    segs = rec.fields("segment")
    assert len(segs) == 1 and segs[0]["capped"] is False


def test_route_word_survives_exact_cap_boundary(monkeypatch):
    """>15 s synthetic utterance cut at exactly 15.0 s: head routes whole."""
    rec = TraceRecorder(monkeypatch)
    long_cmd = ("hey qubit, type this for me: one two three four five six "
                "seven eight nine ten eleven twelve")
    m, commands = _monitor([long_cmd], monkeypatch)
    m._handle_segment(b"\x00" * (15 * 16000 * 2))
    assert commands == [long_cmd[len("hey qubit, "):]]
    segs = rec.fields("segment")
    assert len(segs) == 1 and segs[0]["capped"] is True


def test_capture_path_never_synthesizes_keys():
    """No-key-synthesis invariant: capture modules must not touch keys."""
    roots = [Path("voicebridge/voice_edit/wake.py"),
             Path("voicebridge/voice_edit/kws.py"),
             Path("voicebridge/voice_edit/stt_client.py"),
             Path("voicebridge/voice_edit/asr_accurate.py"),
             Path("voicebridge/stt_ws.py")]
    roots += sorted(Path("voicebridge/audio").glob("*.py"))
    forbidden = ("pynput", "xdotool", "ydotool", "wtype", "xte ",
                 "clipboard", "xclip", "xsel", "wl-copy", "key press",
                 "KeyCode", "Controller()", "pyautogui", "uinput")
    for path in roots:
        src = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token.lower() not in src, f"{path}: {token}"
