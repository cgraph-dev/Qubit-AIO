"""Wave D2 Task 7 — safety + honesty gates for /qubit-chat branches.

Plan: docs/superpowers/plans/2026-09-30-qubit-chat-voice-routing.md Task 7.
Destructive turns need confirmation, e-stop refuses before any model call,
no raw secret reaches a turn receipt, and a /qubit-chat build turn never
moves the /chat terminal pin (grounded in the real ``terminal.profile``
config value + the real providers/resolver decision, read-only).
Hermetic otherwise: fake store, stubbed worker/summarizer — no model,
no network, no side effects.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeStore:
    def __init__(self):
        self.rows = {}
        self.n = 0

    def ensure(self, sid, *, model=None, identity="dashboard:user"):
        sid = (sid or "").strip() or "dashboard-chat"
        self.rows.setdefault(sid, [])
        return {"state": "OK", "session": {"id": sid}, "messages": []}

    def append(self, sid, role, content, *, meta=None, model=None,
               timestamp=None):
        self.rows.setdefault(sid, []).append(
            {"role": role, "content": content, "meta": meta or {}})
        self.n += 1
        return {"state": "OK",
                "message": {"role": role, "content": content},
                "message_count": self.n}

    def messages(self, sid, *, limit=500, offset=0):
        rows = list(self.rows.get((sid or "").strip() or "dashboard-chat", []))
        return {"state": "OK", "session_id": sid, "messages": rows,
                "total": len(rows), "limit": limit, "offset": offset}

    def texts(self, sid):
        return " ".join(
            str(m.get("content", "")) for m in self.rows.get(sid, []))


def _hermetic(monkeypatch, fake):
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    return CT


def test_destructive_needs_confirmation(monkeypatch):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    # The one shared latch reads clear (fail-closed default would refuse
    # everything with a Config-less cfg); the destructive gate below must
    # still refuse on its own authority.
    monkeypatch.setattr("voicebridge.broker.estop_engaged", lambda cfg: False)

    def _boom(*a, **k):
        raise AssertionError("worker must never start for a destructive turn")

    monkeypatch.setattr("voicebridge.orchestrator._agent_run_session", _boom)
    out = CT.turn({}, "delete every file in my home directory",
                  session="safe-9u")
    assert out.get("ok") is False, out
    assert "CONFIRM" in str(out.get("code", "")), out
    # Nothing executed: no assistant "done" row, only the staged user row.
    assert "done" not in fake.texts("safe-9u").lower() or \
        "report" not in fake.texts("safe-9u").lower(), fake.texts("safe-9u")


def test_estop_refuses_before_model_call(monkeypatch):
    from voicebridge import chat_transport as CT
    import voicebridge.chat_transport as CTM

    def _boom_store(cfg):
        raise AssertionError("estop must refuse before any store access")

    monkeypatch.setattr(CTM, "store", _boom_store)
    monkeypatch.setattr(CTM, "_estop", lambda cfg: True)

    def _boom_model(*a, **k):
        raise AssertionError("estop must refuse before any model call")

    monkeypatch.setattr("voicebridge.agent.turn", _boom_model)
    out = CT.turn({}, "hello", session="safe-9u")
    assert out.get("code") == "EMERGENCY_STOP", out
    assert out.get("ok") is False, out


def test_no_raw_secret_in_receipt(monkeypatch):
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    secret = "sk-live-ABC123XYZ789qrs"
    # Pin the LLM verdict (temperature/load-dependent under a busy ollama;
    # ~1/15 falls back to a branch action): the gate under test is the
    # turn-path redaction, reached through the real decide_turn_action on
    # a clarify verdict.
    monkeypatch.setattr(
        "voicebridge.intent.classifier.classify_local_llm",
        lambda *a, **k: {"kind": "clarify", "confidence": 0.9,
                         "reason": "clarify:test", "task": ""})
    decided = CT.decide_turn_action(f"my api key is {secret}", cfg={})
    assert decided["action"] not in CT.BRANCH_ACTIONS, decided
    monkeypatch.setattr(
        CT, "decide_turn_action", lambda text, **kw: dict(decided))
    # The model echoes the secret back (worst case) — the turn path must
    # still redact it out of the reply AND the receipt.
    monkeypatch.setattr(
        "voicebridge.agent.turn",
        lambda *a, **k: {"ok": True,
                         "reply": f"noted, I will never repeat {secret} back",
                         "receipt": {"transport": "stub"}})
    out = CT.turn({}, f"my api key is {secret}", session="safe-9u")
    assert out.get("ok") is True, out
    assert secret not in str(out), str(out)[:500]
    assert "[REDACTED]" in str(out), str(out)[:500]


def test_chat_terminal_pin_untouched(monkeypatch):
    """A /qubit-chat build turn leaves the /chat terminal pin byte-identical.

    Grounded in the real pin source (``terminal.profile`` in the live
    config): value before == value after, and no timestamped
    terminal-profile backup appears (the writer backs up on every write).
    """
    import glob
    import os
    from voicebridge import chat_transport as CT
    from voicebridge.config import Config

    live = Config.load()
    cfg_path = str(getattr(live, "path", "") or "")
    before = live.get("terminal", "profile", default="")
    assert before, "live config must carry a terminal.profile pin for this test"
    bak_pattern = os.path.join(os.path.dirname(cfg_path),
                               "voice.yaml.terminal-profile.*.bak") \
        if cfg_path else None
    baks_before = set(glob.glob(bak_pattern)) if bak_pattern else set()

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    # Explicit agent delegation ("ask qubit to …") is a pure-regex
    # classifier path — deterministic agent_task with no LLM round-trip
    # (a bare "make me an app" reaches the temperature-dependent local
    # classifier and is load-flaky under a busy ollama).
    build_text = "ask qubit to make me an app that tracks expenses"
    decided = CT.decide_turn_action(build_text, cfg=live)
    assert decided["action"] == "agent_task", decided
    monkeypatch.setattr(
        CT, "decide_turn_action", lambda text, **kw: dict(decided))
    monkeypatch.setattr(
        "voicebridge.orchestrator.launch_task",
        lambda task, session=None, **kw: "worker-pin-9u")
    monkeypatch.setattr(
        "voicebridge.orchestrator.wait_task",
        lambda key, timeout_s=300, **kw: {"status": "done",
                                          "reply": "Pin probe build."})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "Pin probe build, summarized.")
    # Projection glue only — the pin assertion must not depend on (or
    # pollute) the live activity store.
    monkeypatch.setattr(CT, "record_activity", lambda *a, **k: {"state": "OK"})

    out = CT.turn(live, build_text, session="safe-pin-9u")
    assert out.get("ok") is True and out.get("route") == "task", out

    after = Config.load().get("terminal", "profile", default="")
    assert after == before, (before, after)
    if bak_pattern:
        assert set(glob.glob(bak_pattern)) == baks_before, \
            "terminal-profile writer ran during a /qubit-chat turn"
