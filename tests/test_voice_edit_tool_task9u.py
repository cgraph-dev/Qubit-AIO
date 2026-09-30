"""Tests for the deterministic + confirmed task dispatch (tool_task)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.voice_edit.tool_task as TT


class _Spec:
    def __init__(self, needs):
        self.needs_confirmation = needs


class _Binding:
    def __init__(self, needs):
        self.spec = _Spec(needs)


class _Res:
    denied = ""
    ok = True
    error = ""
    output = "ok"


class FakeBroker:
    def __init__(self, needs=frozenset()):
        self._bindings = {n: _Binding(n in needs)
                          for n in ("local.volume_up", "local.empty_trash",
                                    "web.search", "web.fetch")}
        self.calls = []
        self.issued = []

    def issue_confirmation(self, capability, args, *, issued_by, ttl_s=120):
        self.issued.append((capability, dict(args), issued_by))
        return types.SimpleNamespace(id="conf-1")

    def execute(self, name, args, *, source, session=None, **kw):
        self.calls.append((name, source, kw.get("confirmation_id")))
        return _Res()


@pytest.fixture(autouse=True)
def _no_persist(monkeypatch):
    monkeypatch.setattr(TT, "_persist", lambda *a, **k: None)


def test_unconfirmed_uses_model_source(monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr(TT, "_broker", lambda cfg: broker)
    TT.execute_tool(object(), "local.volume_up", {}, confirmed=False)
    assert broker.calls == [("local.volume_up", "model", None)]
    assert broker.issued == []


def test_confirmed_mutating_mints_user_confirmation(monkeypatch):
    broker = FakeBroker(needs={"local.empty_trash"})
    monkeypatch.setattr(TT, "_broker", lambda cfg: broker)
    TT.execute_tool(object(), "local.empty_trash", {"force": True},
                    confirmed=True)
    assert broker.issued == [("local.empty_trash", {"force": True}, "user")]
    assert broker.calls == [("local.empty_trash", "user", "conf-1")]


def test_confirmed_reversible_needs_no_confirmation(monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr(TT, "_broker", lambda cfg: broker)
    TT.execute_tool(object(), "local.volume_up", {}, confirmed=True)
    assert broker.issued == []
    assert broker.calls == [("local.volume_up", "user", None)]


def test_unknown_tool_raises():
    # A local.* name that is not a real action fails closed before the broker.
    with pytest.raises(ValueError):
        TT.execute_tool(object(), "shell.rm", {})
    # A web.* name that is not a registered capability is denied by the broker.
    with pytest.raises(PermissionError):
        TT.execute_tool(object(), "web.evil", {})


def test_denied_broker_result_raises(monkeypatch):
    class Denied(_Res):
        denied = "CONFIRMATION_SOURCE_FORBIDDEN"
        ok = False

    class B(FakeBroker):
        def execute(self, name, args, *, source, session=None, **kw):
            return Denied()

    monkeypatch.setattr(TT, "_broker", lambda cfg: B())
    with pytest.raises(PermissionError):
        TT.execute_tool(object(), "local.volume_up", {})


def test_propose_tool_web_search_and_fetch():
    assert TT.propose_tool(None, "search the web for qubit news") == {
        "tool": "web.search", "args": {"query": "qubit news"}}
    assert TT.propose_tool(None, "google quantum computing") == {
        "tool": "web.search", "args": {"query": "quantum computing"}}
    assert TT.propose_tool(None, "fetch the url https://example.com/a") == {
        "tool": "web.fetch", "args": {"url": "https://example.com/a"}}
    assert TT.propose_tool(None, "https://example.com") == {
        "tool": "web.fetch", "args": {"url": "https://example.com"}}
    # not an http(s) url -> not a fetch
    assert TT.propose_tool(None, "open the url ftp://example.com") is None


def test_run_tool_task_web_search_through_broker(monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr(TT, "_broker", lambda cfg: broker)
    out = TT.run_tool_task(object(), "search the web for qubit news")
    assert broker.calls and broker.calls[0][0] == "web.search"
    assert out["tool_used"] == "web.search"
    # fake broker output "ok" is not JSON -> treated as a plain OK reply
    assert out["state"] == "OK"


def test_run_tool_task_passes_confirmed_down(monkeypatch):
    seen = {}

    def fake_execute(cfg, tool, args, *, session="", confirmed=False):
        seen["tool"] = tool
        seen["confirmed"] = confirmed
        return "done"

    monkeypatch.setattr(TT, "execute_tool", fake_execute)
    out = TT.run_tool_task(object(), "raise the volume", confirmed=True)
    assert seen.get("confirmed") is True
    assert out["state"] == "OK"