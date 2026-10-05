"""Echo guard for the rewrite transform (2026-10-03 voice-final lane).

Journal-proof 2026-10-03 23:49:00: "Redo this text for me, the quick brown
fox." routed TRANSFORM (spark4, 2.778s) and answered "the quick brown fox"
(19 chars) -- the input back verbatim, minus the period. The path executed
and produced nothing.

Contract pinned here (TDD: these fail until transform.py gains the guard):
* normalized echo (case/punct/whitespace-insensitive) is never returned
  silently as ok:true;
* on echo the engine retries ONCE with an explicit reword instruction;
* if the retry still echoes, it returns an HONEST typed no-change result;
* normal (differing) rewrites are byte-unchanged and cost exactly 1 call.
No test touches the live DISPLAY, clipboard, microphone, or model.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import parser
from voicebridge.voice_edit.transform import TransformEngine


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeResult:
    def __init__(self, text="", ok=True, code="", message="",
                 provider="ollama", model="qubit-spark4",
                 locality="local"):
        self.ok = ok
        self.text = text
        self.code = code
        self.message = message
        self.provider = provider
        self.model = model
        self.details = {"locality": locality}


def _cfg(**over):
    ve = {"enabled": True, "routing": "local",
          "confirm_before_replace": False}
    ve.update(over)
    return FakeCfg({"voice_edit": ve})


def test_echo_normalization_is_case_punct_whitespace_insensitive():
    from voicebridge.voice_edit.transform import is_echo_output
    assert is_echo_output("The quick brown fox.",
                          "the quick brown fox") is True
    assert is_echo_output("  Hello,  world! ", "hello world") is True
    assert is_echo_output("the quick brown fox.",
                          "a swift auburn fox") is False
    assert is_echo_output("do the thing", "do the other thing") is False
    assert is_echo_output("", "") is False


def test_pangram_echo_retries_once_then_returns_reword():
    calls = []

    def runner(cfg, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return FakeResult(text="the quick brown fox")
        return FakeResult(text="a swift auburn fox")

    cfg = _cfg()
    cmd = parser.parse("Redo this text for me.",
                       selected_text="the quick brown fox.")
    res = TransformEngine(runner=runner).transform(cfg, cmd)
    assert res.ok and res.text == "a swift auburn fox"
    assert len(calls) == 2  # exactly one retry, bounded
    retry_system = calls[1]["system"]
    assert "different words" in retry_system.lower()
    assert calls[1]["profile_id"] == calls[0]["profile_id"]


def test_pangram_double_echo_returns_honest_no_change_never_echo():
    calls = []

    def runner(cfg, **kw):
        calls.append(kw)
        return FakeResult(text="the quick brown fox")

    cfg = _cfg()
    cmd = parser.parse("Redo this text for me.",
                       selected_text="the quick brown fox.")
    res = TransformEngine(runner=runner).transform(cfg, cmd)
    assert res.ok is False
    assert res.code == "VOICE_EDIT_NO_CHANGE"
    assert res.text != "the quick brown fox."
    assert res.text != "the quick brown fox"
    assert "as-is" in res.detail
    assert len(calls) == 2  # retry bounded at 1: no loops


def test_normal_rewrite_is_unchanged_single_call():
    calls = []

    def runner(cfg, **kw):
        calls.append(kw)
        return FakeResult(text="a swift auburn fox")

    cfg = _cfg()
    cmd = parser.parse("Redo this text for me.",
                       selected_text="the quick brown fox.")
    res = TransformEngine(runner=runner).transform(cfg, cmd)
    assert res.ok and res.text == "a swift auburn fox"
    assert len(calls) == 1  # no retry when the output differs


def test_byte_identical_echo_is_also_caught():
    calls = []

    def runner(cfg, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return FakeResult(text="do the thing")
        return FakeResult(text="do the thing well")

    cfg = _cfg()
    cmd = parser.parse("make this clearer", selected_text="do the thing")
    res = TransformEngine(runner=runner).transform(cfg, cmd)
    assert res.ok and res.text == "do the thing well"
    assert len(calls) == 2
