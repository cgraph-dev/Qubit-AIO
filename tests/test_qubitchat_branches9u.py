"""Wave C Tasks 2-4 — branch wiring inside chat_transport.turn.

The Wave B suites pin the backends (answer_web_query, LA.match/execute,
VC.resolve); these tests pin that ``turn()`` actually routes into the
branches and persists the receipts. Hermetic: fake store, no e-stop, no
model, no broker, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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

    def texts(self, sid):
        return " ".join(
            str(m.get("content", "")) for m in self.rows.get(sid, []))


def _hermetic(monkeypatch, fake):
    from voicebridge import chat_transport as CT

    monkeypatch.setattr(CT, "store", lambda cfg: fake)
    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    return CT


def test_web_branch_executes_with_sources(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    CT._web_cache_clear()  # Slice 2: identical-query cache is process-wide
    d = CT.decide_turn_action(
        "use perplexity api to search when Aion 2 will release", cfg={})
    assert d["action"] == "web_search", d
    monkeypatch.setattr(
        "voicebridge.orchestrator.answer_web_query",
        lambda cfg, text, **kw: {"ok": True,
                                 "answer": "Aion 2 releases Q4 2026.",
                                 "sources": ["https://example/aion2"],
                                 "code": "OK"})
    # Reconciliation 2026-10-02 (free-first lane): the turn now tries the
    # no-key DDG layer first, so this hermetic test pins DDG to a miss —
    # the paid mock above then executes, with no network touched.
    monkeypatch.setattr(CT, "_ddg_web_fallback", lambda query: None)
    out = CT.turn({}, "use perplexity api to search when Aion 2 will release",
                  session="test-branch-web-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "web", out
    assert "Aion 2" in (out.get("text") or ""), out
    assert out.get("sources") == ["https://example/aion2"], out
    assert "Sources:" in fake.texts("test-branch-web-9u"), \
        fake.texts("test-branch-web-9u")


def test_web_branch_failure_is_typed(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    CT._web_cache_clear()  # Slice 2: the success test above caches this q
    monkeypatch.setattr(
        "voicebridge.orchestrator.answer_web_query",
        lambda cfg, text, **kw: {"ok": False, "answer": "",
                                 "sources": [], "code": "WEB_UNAVAILABLE"})
    # Both providers down: the no-key DDG fallback must also miss for the
    # typed failure contract to hold (fallback-fires is pinned in
    # Both providers down: the no-key DDG fallback must also miss for the
    # typed failure contract to hold (fallback-fires is pinned in
    # test_qubitchat_web_search9u.py; this file stays hermetic/no-network).
    monkeypatch.setattr(CT, "_ddg_web_fallback", lambda query: None)
    out = CT.turn({}, "use perplexity api to search when Aion 2 will release",
                  session="test-branch-webfail-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "WEB_UNAVAILABLE", out


def test_local_branch_uses_broker_not_chatter(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    d = CT.decide_turn_action("open chromium", cfg={})
    assert d["action"] == "local_action", d

    calls = []

    class _FakeBroker:
        def execute(self, capability, args, *, source, session,
                    utterance=""):
            calls.append({"capability": capability, "args": args,
                          "source": source, "session": session})
            return SimpleNamespace(ok=True, output="Opened chromium.",
                                   error="", denied="")

    monkeypatch.setattr("voicebridge.broker.build_host_broker",
                        lambda cfg=None: _FakeBroker())
    out = CT.turn({}, "open chromium", session="test-branch-local-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "local", out
    assert "chromium" in (out.get("text") or "").lower(), out
    assert calls and calls[0]["capability"] == "local.open_app", calls
    assert calls[0]["source"] == "user", calls


def test_local_destructive_needs_confirmation(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    monkeypatch.setattr(
        CT, "decide_turn_action",
        lambda text, **kw: {"action": "local_action", "kind": "local",
                            "task_text": text, "params": {},
                            "reason": "test"})

    def _boom(*a, **k):
        raise AssertionError("broker must not execute a destructive turn")

    monkeypatch.setattr("voicebridge.broker.build_host_broker", _boom)
    out = CT.turn({}, "delete every file in my home directory",
                  session="test-branch-destruct-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "CONFIRM_REQUIRED", out


def test_registry_branch_executes_browser(monkeypatch):
    # Reconciled 2026-10-03: "open youtube in the browser" no longer reaches
    # the registry browser_open entry. The §70 local open_website action owns
    # spoken site shortcuts (youtube -> https://www.youtube.com, http(s)-only
    # launch guard), so decide_turn_action deterministically yields a
    # local_action and the turn executes through the broker — not a blind
    # expectation edit: the params below are the real §70 shortcut mapping.
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    d = CT.decide_turn_action("open youtube in the browser", cfg={})
    assert d["action"] == "local_action", d
    assert d["params"]["action"] == "open_website", d
    assert d["params"]["action_params"]["url"] == "https://www.youtube.com", d

    calls = []

    class _FakeBroker:
        def execute(self, capability, args, *, source, session,
                    utterance=""):
            calls.append({"capability": capability, "args": args,
                          "source": source, "session": session})
            return SimpleNamespace(ok=True, output="Opened https://www.youtube.com.",
                                   error="", denied="")

    monkeypatch.setattr("voicebridge.broker.build_host_broker",
                        lambda cfg=None: _FakeBroker())
    out = CT.turn({}, "open youtube in the browser",
                  session="test-branch-reg-9u")
    assert out.get("ok") is True, out
    assert out.get("route") == "local", out
    assert "youtube" in (out.get("text") or "").lower(), out
    assert calls and calls[0]["capability"] == "local.open_website", calls


def test_registry_confirm_tier_is_typed(monkeypatch):
    fake = _FakeStore()
    CT = _hermetic(monkeypatch, fake)
    monkeypatch.setattr(
        CT, "decide_turn_action",
        lambda text, **kw: {"action": "registry", "kind": "registry",
                            "task_text": text, "params": {},
                            "reason": "test", "entry": "browser_close_tab"})
    monkeypatch.setattr(
        "voicebridge.voice_commands.voice_say",
        lambda cfg, text, **kw: {"state": "needs-confirm",
                                 "speech": "Close the tab?",
                                 "confirmation_id": "conf-9u-1"})
    out = CT.turn({}, "close that tab", session="test-branch-regconf-9u")
    assert out.get("ok") is False, out
    assert out.get("code") == "CONFIRM_REQUIRED", out
    assert out.get("confirmation_id") == "conf-9u-1", out
