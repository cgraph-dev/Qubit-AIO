"""Wave D2 Task 6 — one transcript for voice + typing.

Plan: docs/superpowers/plans/2026-09-30-qubit-chat-voice-routing.md Task 6.
Adapted to the real store names: there is no ``CT.history`` /
``ORC.append_voice_exchange`` pair with those exact names in the tree — the
requirement is one store, both directions visible. The canonical store is
``chat_transport.store(cfg)`` (QubitSessionStore over the one state.db);
``orchestrator.append_voice_exchange`` is the voice-side entry onto that same
store. Hermetic: the store, e-stop, agent turn, launch/wait, and summarizer
are doubles — no model, no worker, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeStore:
    """Minimal chat_transport store double (ensure/append/messages)."""

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


def test_voice_and_typed_share_one_transcript(monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    # Typed side: real decision, stubbed model. "hello from typing" must
    # stay on the model path (never a branch), so the transcript write
    # under test is the shared turn path.
    decided = CT.decide_turn_action("hello from typing", cfg={})
    assert decided["action"] not in CT.BRANCH_ACTIONS, decided
    monkeypatch.setattr(
        CT, "decide_turn_action", lambda text, **kw: dict(decided))
    monkeypatch.setattr(
        "voicebridge.agent.turn",
        lambda *a, **k: {"ok": True, "reply": "typed reply ok",
                         "receipt": {"transport": "stub"}})
    out = CT.turn({}, "hello from typing", session="shared-9u")
    assert out.get("ok") is True, out
    # Voice side: the module entry onto the SAME store factory.
    res = ORC.append_voice_exchange("shared-9u", "hello from voice",
                                    "hi back", cfg={})
    assert res.get("state") == "OK", res
    assert res.get("session_id") == "shared-9u", res
    rows = CT.store({}).messages("shared-9u").get("messages", [])
    texts = " ".join(str(r.get("content", "")) for r in rows)
    assert "hello from typing" in texts, texts
    assert "typed reply ok" in texts, texts
    assert "hello from voice" in texts, texts
    assert "hi back" in texts, texts
    roles = [r.get("role") for r in rows]
    assert roles.count("user") == 2 and roles.count("assistant") == 2, roles


def test_branch_turns_emit_uniform_receipt_shape(monkeypatch):
    """Task 6 Files line 1: every branch success outcome carries the same
    receipt shape ``{route, task, sources, model}`` — the typed turn, the
    voice turn, and the stream variant all read one lineage. ``task`` is
    redacted (free-form user text must never leak a raw secret)."""
    from types import SimpleNamespace
    from voicebridge import chat_transport as CT

    fake = _FakeStore()
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    # Free-first is the default web order, so the shared web branch reaches
    # ``_ddg_web_fallback`` -- not the paid ``answer_web_query`` layer. Stub
    # the layer that actually runs, so the test never touches the network.
    monkeypatch.setattr(
        CT, "_ddg_web_fallback",
        lambda text: {"ok": True, "answer": "Aion 2 Q4.",
                      "sources": ["https://example/aion2"]})
    monkeypatch.setattr(
        "voicebridge.summarizer.summarize",
        lambda reply, cfg=None: "Built it, summarized.")

    secret_task = "use perplexity to search the sk-live-ABC123XYZ789qrs breach"
    web = CT._turn_web({}, fake, "shape-9u", secret_task)
    assert web.get("ok") is True, web

    from voicebridge.local import actions as LA
    monkeypatch.setattr(
        LA, "match",
        lambda text: SimpleNamespace(name="open_app",
                                     params={"app": "chromium"}))
    monkeypatch.setattr(
        "voicebridge.safety.destructive_screen.screen_destructive",
        lambda text: SimpleNamespace(matched=False))
    monkeypatch.setattr(
        "voicebridge.broker.build_host_broker",
        lambda **kw: SimpleNamespace(
            execute=lambda cap, args, **k: SimpleNamespace(
                ok=True, denied="", output="Opened chromium.", error="")))
    local = CT._turn_local({}, fake, "shape-9u", "open chromium")
    assert local.get("ok") is True, local

    from voicebridge import voice_commands as VC
    monkeypatch.setattr(
        VC, "voice_say",
        lambda cfg, text, **kw: {"state": "ok", "speech": "Opened.",
                                 "route": "browser.open",
                                 "entry": "browser.open"})
    registry = CT._turn_registry({}, fake, "shape-9u",
                                 "open youtube in the browser")
    assert registry.get("ok") is True, registry

    task = CT._finish_task({}, fake, "shape-9u", "worker-9u-9",
                           {"status": "done", "reply": "Built."})
    assert task.get("ok") is True, task

    for out, route in ((web, "web"), (local, "local"),
                       (registry, "registry"), (task, "task")):
        receipt = out.get("receipt")
        assert isinstance(receipt, dict), (route, out)
        for key in ("route", "task", "sources", "model"):
            assert key in receipt, (route, receipt)
        assert receipt["route"] == route, receipt
        assert isinstance(receipt["sources"], list), receipt
    assert web["receipt"]["sources"] == ["https://example/aion2"], web
    assert task["receipt"]["task"] == "worker-9u-9", task
    assert "sk-live-ABC123XYZ789qrs" not in str(web["receipt"]), web


def test_voice_exchange_resolves_the_same_store_factory():
    """Pin the one-store wiring: the voice append must go through
    ``chat_transport.store`` (the same factory ``turn()`` uses), never a
    second store module."""
    import inspect
    from voicebridge import orchestrator as ORC

    assert callable(getattr(ORC, "append_voice_exchange", None))
    src = inspect.getsource(ORC.append_voice_exchange)
    assert "chat_transport" in src and "store(" in src, src
    # The conversation path must use it too (one store call, both callers).
    remember_src = inspect.getsource(
        ORC.VoiceOrchestrator._remember_voice_exchange)
    assert "append_voice_exchange" in remember_src, remember_src


def test_voice_exchange_is_best_effort_when_store_down(monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    def _boom(cfg):
        raise RuntimeError("no store here")

    monkeypatch.setattr(CT, "store", _boom)
    res = ORC.append_voice_exchange("shared-9u", "hi", "hello", cfg={})
    assert res.get("state") == "FAILED", res
    assert res.get("code") == "VOICE_APPEND_FAILED", res


def test_voice_task_result_lands_in_shared_store(monkeypatch):
    """Voice task results append to the shared chat store (Task 6 Step 3):
    the spoken summary must also be readable from /qubit-chat, and speak()
    must stay the single TTS exit (exactly one call)."""
    import threading
    from types import SimpleNamespace
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as ORC

    fake = _FakeStore()
    _hermetic(monkeypatch, fake)
    monkeypatch.setattr("voicebridge.orchestrator.summarize",
                        lambda reply, cfg=None: "Spoken summary.")
    spoken = []

    orch = ORC.VoiceOrchestrator.__new__(ORC.VoiceOrchestrator)

    class _Cfg:
        def get(self, *path, default=None):
            if path == ("reports", "verify"):
                return False
            return default

        def section(self, name):
            return {}

    orch.cfg = _Cfg()
    orch.shared = SimpleNamespace(
        append_agent_report=lambda *a, **k: None)
    orch.feed_push = lambda *a, **k: None
    orch._notify_typed_task = lambda *a, **k: None
    orch._persist_full_report = lambda *a, **k: None
    orch._web_lock = threading.Lock()
    orch._web_active = 0
    orch._web_mode = False
    orch._last_web_summary = ""
    orch.task_queue = SimpleNamespace(size=0)
    orch.speak = lambda text, *a, **k: (spoken.append(text), True)[1]

    orch._handle_agent_result("worker-9u-1", "Built the widget. Two files.",
                              "ok", 12.5, task_text="make a test widget")
    assert spoken == ["Spoken summary."], spoken
    texts = fake.texts("dashboard-chat")
    assert "make a test widget" in texts, texts
    assert "Built the widget. Two files." in texts, texts
