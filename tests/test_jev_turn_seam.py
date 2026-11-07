"""The Jev turn-classification seam: advisory, opt-in, fail-open.

The seam map (`voicebridge/jev/seams.py`) states the contract for this hook:
local heuristics stay authoritative and "a Jev verdict, when available,
re-ranks only ambiguous kinds". These tests pin exactly that, plus the three
safety properties a decision layer must never lose:

* off by default — a host with no ``jev.enabled`` never calls out;
* a refusal keeps the local answer (never a fabricated one);
* Jev can never widen its own authority to a confident kind.
"""
from __future__ import annotations

import pytest

from voicebridge.jev import advice


class _Cfg:
    """Minimal config object with the ``get(section, key, default=)`` shape."""

    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for seg in path:
            if not isinstance(node, dict):
                return default
            node = node.get(seg)
            if node is None:
                return default
        return node


def _enabled_cfg(**jev):
    return _Cfg({"jev": {"enabled": True, **jev}})


AMBIGUOUS = {"action": "clarify", "kind": "clarify", "task_text": "hmm",
             "params": {}, "reason": "uncertain"}
CONFIDENT = {"action": "registry", "kind": "conversation",
             "task_text": "open youtube", "params": {},
             "reason": "registry:open-youtube"}


class _Pick:
    """A stand-in for the jev client that answers with a chosen verdict."""

    def __init__(self, action="web_search", confidence=0.9, escalate=False,
                 ok=True, code=""):
        self.action, self.confidence = action, confidence
        self.escalate, self._ok, self._code = escalate, ok, code
        self.calls = 0

    def judge(self, state, questions, **kw):
        self.calls += 1
        if not self._ok:
            return {"ok": False, "code": self._code}
        return {"ok": True, "backend": "stub", "answers": [{
            "id": "turn-action", "kind": "pick", "answer": self.action,
            "confidence": self.confidence, "escalate": self.escalate}]}


# ── opt-in ────────────────────────────────────────────────────────────────

def test_off_by_default_never_calls_out():
    client = _Pick()
    out = advice.advise("hmm", AMBIGUOUS, _Cfg(), client=client)
    assert out["asked"] is False
    assert out["applied"] is False
    assert client.calls == 0, "Jev was called on a host that never opted in"


def test_an_unreadable_config_is_off_not_a_crash():
    out = advice.advise("hmm", AMBIGUOUS, None, client=_Pick())
    assert out["asked"] is False


# ── local stays authoritative ─────────────────────────────────────────────

def test_a_confident_local_decision_is_never_offered_to_jev():
    """registry/agent_task/route-word are decisions, not guesses."""
    client = _Pick()
    out = advice.advise("open youtube", CONFIDENT, _enabled_cfg(), client=client)
    assert out["asked"] is False
    assert client.calls == 0


def test_config_cannot_widen_the_rerank_set():
    """A config naming a confident kind cannot hand it to Jev."""
    cfg = _enabled_cfg(rerank_kinds=["registry", "agent_task", "clarify"])
    assert advice.rerank_kinds(cfg) == ("clarify",), (
        "config widened Jev's authority past the seam map's rule")


def test_the_ambiguous_kinds_are_candidates():
    assert advice.should_ask(AMBIGUOUS, _enabled_cfg()) is True


# ── fail-open, never fabricate ────────────────────────────────────────────

def test_a_typed_refusal_keeps_the_local_answer():
    out = advice.advise("hmm", AMBIGUOUS, _enabled_cfg(),
                        client=_Pick(ok=False, code="JEV_KEY_ABSENT"))
    assert out["asked"] is True
    assert out["applied"] is False
    assert out["reason"] == "JEV_KEY_ABSENT"


def test_low_confidence_is_discarded():
    out = advice.advise("hmm", AMBIGUOUS, _enabled_cfg(),
                        client=_Pick(confidence=advice.MIN_CONFIDENCE - 0.01))
    assert out["applied"] is False
    assert "low confidence" in out["reason"]


def test_escalation_is_respected():
    out = advice.advise("hmm", AMBIGUOUS, _enabled_cfg(),
                        client=_Pick(confidence=0.99, escalate=True))
    assert out["applied"] is False


def test_an_action_outside_the_allowed_set_is_refused():
    out = advice.advise("hmm", AMBIGUOUS, _enabled_cfg(),
                        client=_Pick(action="drop_all_tables", confidence=0.99))
    assert out["applied"] is False
    assert "not an allowed action" in out["reason"]


def test_an_exception_never_escapes():
    class Boom:
        def judge(self, *a, **k):
            raise RuntimeError("upstream exploded")

    out = advice.advise("hmm", AMBIGUOUS, _enabled_cfg(), client=Boom())
    assert out["applied"] is False
    assert "RuntimeError" in out["reason"]


# ── the verdict itself ────────────────────────────────────────────────────

def test_a_confident_verdict_is_reported_as_applied():
    out = advice.advise("hmm", AMBIGUOUS, _enabled_cfg(),
                        client=_Pick(action="web_search", confidence=0.9))
    assert out["asked"] is True
    assert out["applied"] is True
    assert out["action"] == "web_search"


def test_agreement_is_not_reported_as_a_change():
    out = advice.advise("hmm", AMBIGUOUS, _enabled_cfg(),
                        client=_Pick(action="clarify", confidence=0.9))
    assert out["applied"] is False
    assert "agrees" in out["reason"]
