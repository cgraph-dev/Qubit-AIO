"""Agent B — the /qubit-chat reasoning selector must reach VOICE turns too.

The selector persists per session on the canonical store
(``QubitSessionStore.meta_patch({"reasoning": ...})``) and the text/stream path
already applies it (``chat_transport._chat_effective_reasoning``). The voice /
orchestrator worker turn (``voicebridge.agent.run_session``) is a SEPARATE
entry point; before this change it never read the session preference, so a
spoken turn silently ignored the reasoning mode the operator selected in
``/qubit-chat``. This pins the fix: a voice turn applies the persisted level,
and a missing/invalid preference degrades to the model default (never a brick).

Hermetic: the provider gateway is stubbed, so no model is contacted.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _FakeResult:
    def __init__(self, text: str = "stub voice reply") -> None:
        self.ok = True
        self.text = text
        self.model = "qubit-brain"
        self.provider = "local"
        self.identity = "local/qubit-brain"
        self.details: dict = {}
        self.usage = None


def _stub_gateway(monkeypatch):
    """Capture the kwargs run_session passes to the provider gateway."""
    capture: dict = {}

    def _run_role(prompt, cfg, **kwargs):
        capture.update(kwargs)
        return _FakeResult()

    import voicebridge.providers.gateway as _gw

    monkeypatch.setattr(_gw, "run_role", _run_role)
    return capture


def _no_plan(monkeypatch):
    import voicebridge.planner as _pl

    monkeypatch.setattr(_pl, "plan", lambda _t: None)


def test_voice_turn_applies_persisted_reasoning(tmp_path, monkeypatch):
    from voicebridge import agent as A
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    st.ensure("voice-reason", model="qubit-brain")
    patched = st.meta_patch("voice-reason", {"reasoning": "medium"})
    assert patched["state"] == "OK", patched

    capture = _stub_gateway(monkeypatch)
    _no_plan(monkeypatch)

    res = A.run_session(cfg, "what is the status?", session="voice-reason")
    assert res["state"] == "OK", res
    assert capture.get("reasoning") == "medium", capture


def test_voice_turn_without_preference_passes_none(tmp_path, monkeypatch):
    from voicebridge import agent as A
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    CT.store(cfg).ensure("voice-plain", model="qubit-brain")

    capture = _stub_gateway(monkeypatch)
    _no_plan(monkeypatch)

    res = A.run_session(cfg, "hello", session="voice-plain")
    assert res["state"] == "OK", res
    assert capture.get("reasoning") is None, capture


def test_invalid_persisted_reasoning_degrades_not_brick(
        tmp_path, monkeypatch):
    """A corrupt stored value must not brick a voice turn (advisory only)."""
    from voicebridge import agent as A
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    st.ensure("voice-corrupt", model="qubit-brain")
    st.meta_patch("voice-corrupt", {"reasoning": "definitely-not-a-level"})

    capture = _stub_gateway(monkeypatch)
    _no_plan(monkeypatch)

    res = A.run_session(cfg, "still works?", session="voice-corrupt")
    assert res["state"] == "OK", res
    assert capture.get("reasoning") is None, capture
