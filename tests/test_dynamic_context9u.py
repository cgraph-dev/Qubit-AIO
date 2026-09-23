"""Objective D — dynamic Brain4 context selection and escalation.

Proves, with focused unit tests:

* a simple voice turn uses the fast context window;
* a long conversation compacts deterministically (older turns summarized);
* important instructions survive compaction;
* a difficult/too-large request escalates (or fails closed with a typed code);
* no API credential material is exposed in the decision/receipt;
* a session stays continuous across a model switch (same session id);
* the routing decision is audited.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _Cfg:
    """Minimal Config stand-in: dotted ``get`` over a nested dict."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _msg(role, content, **extra):
    return {"role": role, "content": content, **extra}


# ---------------------------------------------------------------------------
# Pure policy
# ---------------------------------------------------------------------------
def test_simple_voice_turn_uses_fast_window():
    from voicebridge import context_policy as C

    msgs = [_msg("user", "what time is it"),
            _msg("assistant", "It is noon.")]
    d = C.decide_context(_Cfg(), msgs, model_context_tokens=4096)
    assert d.tier == C.TIER_FAST
    assert d.window_tokens == 2048
    assert d.requires_escalation is False
    assert d.message_limit == 6


def test_long_conversation_compacts_and_keeps_instructions():
    from voicebridge import context_policy as C

    cfg = _Cfg({"agent": {"context": {"max_messages": 8,
                                      "default_messages": 4,
                                      "fast_messages": 2}}})
    msgs = [_msg("system", "always answer in French")]
    for i in range(40):
        msgs.append(_msg("user", f"turn {i} " + "x" * 200))
        msgs.append(_msg("assistant", f"reply {i} " + "y" * 200))
    d = C.decide_context(cfg, msgs, model_context_tokens=16384)
    assert d.summarized_messages > 0
    text, meta = C.build_context_prompt(msgs, d)
    # The active instruction is retained verbatim even though turns were summarized.
    assert "always answer in French" in text
    assert "[instructions]" in text
    assert "[summary-of-earlier-conversation]" in text
    assert meta["instructions_kept"] == 1
    assert meta["summarized_messages"] == d.summarized_messages


def test_difficult_request_escalates_or_fails_closed():
    from voicebridge import context_policy as C

    # A single enormous turn cannot fit the local 4096 window.
    huge = [_msg("user", "analyze this " + "z" * 200000)]
    d = C.decide_context(_Cfg(), huge, model_context_tokens=4096)
    assert d.requires_escalation is True
    assert d.est_prompt_tokens > 4096


def test_config_relation_is_normalized():
    from voicebridge import context_policy as C

    cfg = _Cfg({"agent": {"context": {"min_tokens": 9000,
                                      "default_tokens": 100,
                                      "max_tokens": 50}}})
    pol = C.context_config(cfg)
    assert pol["default_tokens"] >= pol["min_tokens"]
    assert pol["max_tokens"] >= pol["default_tokens"]


# ---------------------------------------------------------------------------
# Turn-level escalation / continuity / audit
# ---------------------------------------------------------------------------
class _FakeStore:
    def __init__(self):
        self._sessions = {}

    def ensure(self, sid, model=None):
        rec = self._sessions.setdefault(
            sid, {"id": sid, "model": model, "created_at": 1.0,
                  "updated_at": 1.0, "archived": False, "message_count": 0})
        return {"state": "OK", "session": dict(rec), "messages": []}

    def messages(self, sid, limit=200, offset=0):
        rec = self._sessions.setdefault(sid, {"id": sid, "message_count": 0})
        return {"state": "OK", "messages": list(rec.get("_m", []))}

    def append(self, sid, role, content, **kw):
        rec = self._sessions.setdefault(sid, {"id": sid, "message_count": 0})
        rec.setdefault("_m", []).append({"role": role, "content": content})
        return {"state": "OK", "message": {"role": role, "content": content}}

    def show(self, sid):
        rec = self._sessions.get(sid)
        if rec is None:
            return {"state": "MISSING"}
        return {"state": "OK", "messages": list(rec.get("_m", []))}


def _routed(model, kind, ctx, provider="ollama", pid=None):
    return {"ok": True, "model": model, "provider": provider,
            "profile_id": pid or model, "kind": kind, "route": "local",
            "locality": "local" if kind == "local" else "external",
            "executable": True, "context_tokens": ctx}


def test_huge_request_escalates_when_configured_and_keeps_session(monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _Cfg({"agent": {"context": {"escalation_model": "qubit-strong",
                                      "max_tokens": 1024,
                                      "default_tokens": 1024,
                                      "min_tokens": 512}}})
    fake = _FakeStore()
    monkeypatch.setattr(CT, "store", lambda _cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda _cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda _cfg: True)
    events = []
    monkeypatch.setattr(CT, "_audit", lambda op, **f: events.append((op, f)))
    seen = {}

    def _route(_cfg, requested=None, **kw):
        if requested == "qubit-strong":
            return _routed("strong-1", "api", 32768, provider="openrouter",
                           pid="qubit-strong")
        return _routed("qubit-brain4", "local", 4096)

    monkeypatch.setattr(CT, "resolve_route", _route)

    def _gen(_cfg, sid, clean, model, context, **kw):
        seen["sid"] = sid
        seen["model"] = model
        seen["decision"] = kw.get("decision")
        seen["context_meta"] = kw.get("context_meta")
        return {"ok": True, "reply": "done", "session_id": sid}

    monkeypatch.setattr(CT, "_generate", _gen)

    res = CT.turn(cfg, "analyze " + "z" * 200000, session="continuous-session")
    assert res["ok"] is True
    assert seen["sid"] == "continuous-session"
    assert seen["model"] == "strong-1"
    assert seen["decision"].requires_escalation is True
    assert any(op == "context_escalated" for op, _ in events)


def test_huge_request_fails_closed_without_escalation_target(monkeypatch):
    from voicebridge import chat_transport as CT

    cfg = _Cfg({"agent": {"context": {"escalation_model": "",
                                      "max_tokens": 1024,
                                      "default_tokens": 1024,
                                      "min_tokens": 512}}})
    fake = _FakeStore()
    monkeypatch.setattr(CT, "store", lambda _cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda _cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda _cfg: True)
    monkeypatch.setattr(CT, "resolve_route",
                        lambda *a, **k: _routed("qubit-brain4", "local", 4096))
    called = {"gen": False}

    def _gen(*a, **k):
        called["gen"] = True
        return {"ok": True, "reply": "nope"}

    monkeypatch.setattr(CT, "_generate", _gen)

    res = CT.turn(cfg, "analyze " + "z" * 200000, session="s")
    assert res["ok"] is False
    assert res["code"] == "CONTEXT_ESCALATION_UNAVAILABLE"
    assert called["gen"] is False
    # The decision is visible and carries no credential data.
    assert "context" in res
    assert "api_key" not in str(res["context"]).lower()


def test_decision_public_has_no_secret_fields():
    from voicebridge import context_policy as C

    d = C.decide_context(_Cfg(), [_msg("user", "hi")], model_context_tokens=4096)
    public = d.public()
    # The public view is an allowlist of context/routing fields only.
    assert set(public) == {
        "tier", "window_tokens", "message_limit", "est_prompt_tokens",
        "total_messages", "summarized_messages", "requires_escalation",
        "reasons"}
    blob = str(public).lower()
    for secret in ("api_key", "password", "authorization", "bearer"):
        assert secret not in blob
