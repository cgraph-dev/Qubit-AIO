"""The local editorial system prompt must not be silently dropped.

The local chat adapter ignores ``system_prompt`` (it takes a conversation, not
a system role). Transform and classifier calls therefore use the raw
``/api/generate`` adapter, which forwards the system prompt. This regression
made every local model behave like a chatbot ("Do you want it shorter?") instead
of an editor, and wasted the strict "output only" instructions.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.brain import BrainRouter
from voicebridge.voice_edit.parser import parse
from voicebridge.voice_edit.transform import TransformEngine


class FakeCfg:
    def __init__(self):
        self._data = {"voice_edit": {
            "routing": "local", "allow_api": False, "api_profile": "",
            "local_profile": "p", "detail_model": "d", "timeout_s": 5.0,
            "max_input_chars": 10000}}

    def get(self, *path, default=None):
        node = self._data
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node


def test_transform_uses_generate_and_forwards_the_system_prompt(monkeypatch):
    seen = {}

    def fake_run(prompt, cfg, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True, text="A clearer sentence.",
                               model="m", provider="local", code="",
                               message="", details={"locality": "local"})

    monkeypatch.setattr("voicebridge.providers.gateway.run_role", fake_run)
    text = "The quick brown fox jumps over the lazy dog. Make this clearer."
    cmd = parse("rewrite this clearly", selected_text=text)
    result = TransformEngine().transform(FakeCfg(), cmd, selected_text=text)
    assert result.ok is True
    assert seen["local_mode"] == "generate"
    assert "Output ONLY the transformed text" in seen["system_prompt"]


def test_brain_classifier_uses_generate_and_forwards_the_system_prompt(monkeypatch):
    seen = {}

    def fake_run(prompt, cfg, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True, text='{"action":"rewrite","style":""}',
                               model="m", provider="local")

    monkeypatch.setattr("voicebridge.providers.gateway.run_role", fake_run)
    router = BrainRouter()
    assert router.classify(FakeCfg(), "make it nicer", has_selection=True) == {
        "action": "rewrite", "style": ""}
    assert seen["local_mode"] == "generate"
    assert "routing classifier" in seen["system_prompt"]


def test_local_generate_sends_think_false(monkeypatch):
    """Qwen3.5 thinks by default; the editor must request no-think."""
    import json
    from voicebridge.desktop_helper import build_cfg
    from voicebridge.providers import gateway

    seen = {}

    def fake_post(url, payload, timeout, **kwargs):
        seen.update(payload)
        return json.dumps({"response": "OK"}).encode()

    monkeypatch.setattr("voicebridge.local_llm.post_json", fake_post)
    res = gateway._local_generate("hi", build_cfg(), 16, system_prompt="sys")
    assert res.ok is True
    assert seen["think"] is False
    assert seen["system"] == "sys"


def test_summary_prompt_bans_markdown_and_lists():
    from voicebridge.voice_edit.prompts import render_prompt
    p = render_prompt(parse("summarize this", selected_text="Some text."),
                      "Some text.")
    assert "one short paragraph" in p["system"]
    assert "Do not use markdown" in p["system"]
